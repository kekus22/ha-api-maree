"""Source EDF : niveaux de l'estuaire de la Rance et horaires d'éclusage.

Données publiées par EDF pour l'usine marémotrice de la Rance
(https://www.edf.fr/usine-maremotrice-rance/marees-en-rance) :

- niveaux prévisionnels de la mer et de l'estuaire (marégraphe de Saint-Suliac),
  un fichier JSON par semaine (lundi → dimanche), un point toutes les 5 minutes ;
- statut horaire de l'écluse du barrage, un document par jour.

Ces adresses ne sont pas une API documentée : elles sont lues par la page web
d'EDF et peuvent évoluer sans préavis.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

EDF_TZ = ZoneInfo("Europe/Paris")
LEVELS_URL = (
    "https://www.edf.fr/sites/groupe/files/dpi-rance/previsions/json/NiveauxRance_{monday}.json"
)
LOCK_URL = "https://www.edf.fr/rance/niveau/eclusage/{timestamp}"
PAGE_URL = "https://www.edf.fr/usine-maremotrice-rance/marees-en-rance"

EDF_SITE_ID = "edf_rance"
EDF_SITE_NAME = "Rance (Saint-Suliac)"
EDF_ATTRIBUTION = (
    "Prévisions EDF, usine marémotrice de la Rance (marégraphe de Saint-Suliac, "
    "zéro des cartes, ± 30 cm hors surcote/décote). Source : "
    f'<a href="{PAGE_URL}">edf.fr</a>.'
)

# Statuts de l'écluse (classes CSS de la page EDF → valeurs de l'intégration).
LOCK_SAS = "sas"
LOCK_ON_DEMAND = "on_demand"
LOCK_UNCERTAIN = "uncertain"
LOCK_NO_SAS = "no_sas"
LOCK_UNKNOWN = "unknown"
LOCK_STATUSES = [LOCK_SAS, LOCK_ON_DEMAND, LOCK_UNCERTAIN, LOCK_NO_SAS, LOCK_UNKNOWN]
LOCK_PASSABLE = frozenset({LOCK_SAS, LOCK_ON_DEMAND})
_LOCK_CLASSES = {
    "saswithbridge": LOCK_SAS,
    "sas": LOCK_SAS,
    "sasondemand": LOCK_ON_DEMAND,
    "uncertainsas": LOCK_UNCERTAIN,
    "nosas": LOCK_NO_SAS,
}

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
_TBODY_RE = re.compile(r"<tbody>(.*?)</tbody>", re.S | re.I)
_TD_CLASS_RE = re.compile(r'<td[^>]*class="([^"]*)"', re.I)


class EdfError(Exception):
    """Erreur d'accès aux données EDF."""


class EdfNotFoundError(EdfError):
    """Fichier non (encore) publié."""


@dataclass(frozen=True, slots=True)
class LockPeriod:
    """Période homogène de statut de l'écluse."""

    start: datetime
    end: datetime
    status: str

    def as_dict(self) -> dict[str, Any]:
        """Forme sérialisable, utilisée par la carte."""
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "status": self.status}


@dataclass(slots=True)
class EdfLevels:
    """Séries de niveaux au pas régulier (UTC)."""

    start: datetime
    step: timedelta
    estuary: list[float]
    sea: list[float]
    updated: datetime | None


def monday_of(day: date) -> date:
    """Lundi de la semaine contenant `day`."""
    return day - timedelta(days=day.weekday())


def local_midnight(day: date) -> datetime:
    """Minuit (heure de Paris) du jour donné, en datetime aware."""
    return datetime(day.year, day.month, day.day, tzinfo=EDF_TZ)


def parse_levels(payloads: list[dict[str, Any]]) -> EdfLevels:
    """Fusionne un ou plusieurs fichiers hebdomadaires en séries régulières.

    Format EDF : {"1": {"<epoch>": {"h": epoch, "nm": mer, "ne": estuaire,
    "dm": "", "dme": epoch_maj}, ...}, "2": {...}, ...}
    """
    points: dict[int, tuple[float, float]] = {}
    updated = 0
    for payload in payloads:
        for day in payload.values():
            if not isinstance(day, dict):
                continue
            for item in day.values():
                try:
                    ts = int(item["h"])
                    points[ts] = (float(item["ne"]), float(item["nm"]))
                except (KeyError, TypeError, ValueError):
                    continue
                with contextlib.suppress(TypeError, ValueError):
                    updated = max(updated, int(item.get("dme") or 0))
    if len(points) < 2:
        raise EdfError("Aucun niveau dans les données EDF")

    times = sorted(points)
    step_s = min(b - a for a, b in itertools.pairwise(times)) or 300
    start, end = times[0], times[-1]
    estuary: list[float] = []
    sea: list[float] = []
    last = points[start]
    for ts in range(start, end + 1, step_s):
        # Un trou éventuel est comblé par la dernière valeur connue.
        last = points.get(ts, last)
        estuary.append(last[0])
        sea.append(last[1])
    return EdfLevels(
        start=datetime.fromtimestamp(start, UTC),
        step=timedelta(seconds=step_s),
        estuary=estuary,
        sea=sea,
        updated=datetime.fromtimestamp(updated, UTC) if updated else None,
    )


def parse_lock_day(day: date, payload: dict[str, Any]) -> list[str]:
    """Statuts horaires (24 valeurs, 00h → 23h) d'une réponse /eclusage."""
    if not payload.get("status"):
        raise EdfNotFoundError(f"Pas d'horaires d'éclusage pour le {day}")
    content = str(payload.get("content") or "")
    body = _TBODY_RE.search(content)
    classes = _TD_CLASS_RE.findall(body.group(1) if body else content)
    statuses = [_LOCK_CLASSES.get(c.strip().lower(), LOCK_UNKNOWN) for c in classes]
    if len(statuses) != 24:
        raise EdfError(f"Horaires d'éclusage illisibles pour le {day} ({len(statuses)} cases)")
    return statuses


def build_lock_periods(days: dict[date, list[str]]) -> list[LockPeriod]:
    """Fusionne les statuts horaires en périodes continues (UTC).

    Les colonnes EDF sont des heures locales (00h à 23h) : aux changements
    d'heure, une heure inexistante est ignorée et une heure doublée est étirée.
    """
    periods: list[LockPeriod] = []
    for day in sorted(days):
        for hour, status in enumerate(days[day]):
            start = local_midnight(day).replace(hour=hour).astimezone(UTC)
            end = (
                local_midnight(day + timedelta(days=1))
                if hour == 23
                else local_midnight(day).replace(hour=hour + 1)
            ).astimezone(UTC)
            if end <= start:
                continue
            if periods and periods[-1].status == status and periods[-1].end >= start:
                periods[-1] = LockPeriod(periods[-1].start, max(end, periods[-1].end), status)
            else:
                periods.append(LockPeriod(start, end, status))
    return periods


class EdfRanceClient:
    """Lecture des fichiers publiés par EDF."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def _get_json(self, url: str) -> dict[str, Any]:
        try:
            async with self._session.get(
                url, timeout=REQUEST_TIMEOUT, headers={"Accept": "application/json"}
            ) as resp:
                if resp.status == 404:
                    raise EdfNotFoundError(url)
                if resp.status >= 400:
                    raise EdfError(f"HTTP {resp.status} sur {url}")
                text = await resp.text(encoding="utf-8-sig")
        except (TimeoutError, aiohttp.ClientError) as err:
            raise EdfError(f"{url} : {err}") from err
        try:
            payload = json.loads(text)
        except ValueError as err:
            # EDF renvoie une page HTML « maintenance » quand l'accès est filtré.
            raise EdfError(f"Réponse non JSON sur {url}") from err
        if not isinstance(payload, dict):
            raise EdfError(f"Réponse inattendue sur {url}")
        return payload

    async def async_get_week(self, monday: date) -> dict[str, Any]:
        """Niveaux d'une semaine (lundi → dimanche)."""
        return await self._get_json(LEVELS_URL.format(monday=monday.strftime("%Y%m%d")))

    async def async_get_lock_day(self, day: date) -> list[str]:
        """Statuts horaires de l'écluse pour un jour."""
        ts = int(local_midnight(day).timestamp())
        return parse_lock_day(day, await self._get_json(LOCK_URL.format(timestamp=ts)))

    async def async_get_levels(self, weeks: list[date]) -> EdfLevels:
        """Niveaux sur plusieurs semaines ; les semaines non publiées sont ignorées."""
        results = await asyncio.gather(
            *(self.async_get_week(m) for m in weeks), return_exceptions=True
        )
        payloads = [r for r in results if isinstance(r, dict)]
        errors = [r for r in results if isinstance(r, BaseException)]
        if not payloads:
            raise errors[0] if errors else EdfError("Aucune donnée EDF")
        for err in errors:
            if not isinstance(err, EdfNotFoundError):
                raise err
        return parse_levels(payloads)

    async def async_get_locks(self, days: list[date]) -> dict[date, list[str]]:
        """Statuts de l'écluse ; les jours non publiés sont ignorés."""
        results = await asyncio.gather(
            *(self.async_get_lock_day(d) for d in days), return_exceptions=True
        )
        locks: dict[date, list[str]] = {}
        for day, res in zip(days, results, strict=True):
            if isinstance(res, list):
                locks[day] = res
            elif not isinstance(res, EdfNotFoundError):
                raise res
        return locks
