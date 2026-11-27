"""Calculs de marée à partir des données brutes de l'API (sans dépendance HA)."""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

HIGH: Literal["high"] = "high"
LOW: Literal["low"] = "low"

# Fenêtre et résolution des données exposées à la carte.
CARD_BEFORE = timedelta(hours=24)
CARD_AFTER = timedelta(hours=48)
CARD_EXTREMA_AFTER = timedelta(days=8)
CARD_STEP = timedelta(minutes=10)


def _interpolate(
    start: datetime | None, step: timedelta, values: list[float], when: datetime
) -> float | None:
    if start is None or len(values) < 2:
        return None
    offset = (when - start) / step
    if not 0 <= offset <= len(values) - 1:
        return None
    idx = int(offset)
    if idx >= len(values) - 1:
        return values[-1]
    return values[idx] + (values[idx + 1] - values[idx]) * (offset - idx)


def find_extrema(
    start: datetime,
    step: timedelta,
    values: list[float],
    min_swing: float = 0.5,
    flat_tolerance: float = 0.005,
) -> list[Extremum]:
    """Repère pleines et basses eaux sur une courbe au pas régulier.

    Un extremum n'est retenu que si le niveau s'en écarte ensuite d'au moins
    `min_swing` mètres (évite les faux extrema). Quand le niveau forme un palier
    (cas de la Rance, où l'usine maintient le niveau), l'heure retenue est le
    milieu du palier, dont le début et la fin sont conservés.
    """
    found: list[tuple[str, int]] = []
    state: str | None = None  # "max" : on cherche un maximum ; "min" : un minimum
    i_max = i_min = 0
    for i, v in enumerate(values):
        if state != "min" and v > values[i_max]:
            i_max = i
        if state != "max" and v < values[i_min]:
            i_min = i
        if state != "min" and values[i_max] - v >= min_swing:
            found.append((HIGH, i_max))
            state, i_min = "min", i
        elif state != "max" and v - values[i_min] >= min_swing:
            found.append((LOW, i_min))
            state, i_max = "max", i

    result: list[Extremum] = []
    last = len(values) - 1
    for kind, idx in found:
        ref = values[idx]
        lo = hi = idx
        while lo > 0 and abs(values[lo - 1] - ref) <= flat_tolerance:
            lo -= 1
        while hi < last and abs(values[hi + 1] - ref) <= flat_tolerance:
            hi += 1
        if lo == 0 or hi == last:
            continue  # extremum tronqué par le début ou la fin des données
        t_lo, t_hi = start + step * lo, start + step * hi
        result.append(
            Extremum(
                kind=kind,
                time=t_lo + (t_hi - t_lo) / 2,
                height=max(values[lo : hi + 1]) if kind == HIGH else min(values[lo : hi + 1]),
                start=t_lo if hi > lo else None,
                end=t_hi if hi > lo else None,
            )
        )
    return result


@dataclass(frozen=True, slots=True)
class Extremum:
    """Une pleine mer (high) ou une basse mer (low)."""

    kind: Literal["high", "low"]
    time: datetime
    height: float
    coefficient: int | None = None
    # Palier (niveau maintenu par l'usine de la Rance) : début et fin.
    start: datetime | None = None
    end: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        """Forme sérialisable, utilisée par la carte."""
        data: dict[str, Any] = {
            "type": self.kind,
            "time": self.time.isoformat(),
            "height": round(self.height, 2),
        }
        if self.coefficient is not None:
            data["coef"] = self.coefficient
        if self.start is not None and self.end is not None:
            data["start"] = self.start.isoformat()
            data["end"] = self.end.isoformat()
        return data


@dataclass(frozen=True, slots=True)
class SunTimes:
    """Lever et coucher du soleil d'une journée (UTC)."""

    day: str
    sunrise: datetime | None
    sunset: datetime | None

    def as_dict(self) -> dict[str, Any]:
        """Forme sérialisable, utilisée par la carte."""
        return {
            "date": self.day,
            "sunrise": self.sunrise.isoformat() if self.sunrise else None,
            "sunset": self.sunset.isoformat() if self.sunset else None,
        }


@dataclass(slots=True)
class TideData:
    """Données d'un site, récupérées lors d'un rafraîchissement."""

    site_id: str
    site_name: str
    extrema: list[Extremum]
    curve_start: datetime | None = None
    curve_step: timedelta = timedelta(minutes=10)
    curve: list[float] = field(default_factory=list)
    sun: list[SunTimes] = field(default_factory=list)
    attribution: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    source: str = "api_maree"
    # Source EDF (Rance) : niveau de la mer au même pas que `curve`, et écluse.
    sea_curve: list[float] = field(default_factory=list)
    locks: list[Any] = field(default_factory=list)

    # --- Recherche d'extrema ------------------------------------------------

    def previous_extremum(self, now: datetime) -> Extremum | None:
        """Dernier extremum passé (ou en cours)."""
        idx = bisect_right([e.time for e in self.extrema], now)
        return self.extrema[idx - 1] if idx > 0 else None

    def next_extremum(self, now: datetime, kind: str | None = None) -> Extremum | None:
        """Prochain extremum strictement futur, éventuellement filtré par type."""
        for ext in self.extrema:
            if ext.time > now and (kind is None or ext.kind == kind):
                return ext
        return None

    def is_rising(self, now: datetime) -> bool | None:
        """Vrai si la marée monte."""
        nxt = self.next_extremum(now)
        if nxt is not None:
            return nxt.kind == HIGH
        prev = self.previous_extremum(now)
        return None if prev is None else prev.kind == LOW

    # --- Hauteur ------------------------------------------------------------

    def height_at(self, when: datetime) -> float | None:
        """Hauteur d'eau estimée à un instant donné.

        Interpolation linéaire sur la courbe fine (pas de 10 min) si elle
        couvre l'instant, sinon interpolation sinusoïdale entre les extrema
        encadrants (approximation classique de la courbe de marée).
        """
        fine = _interpolate(self.curve_start, self.curve_step, self.curve, when)
        if fine is not None:
            return fine

        prev = self.previous_extremum(when)
        nxt = self.next_extremum(when)
        if prev is None or nxt is None:
            return None
        span = (nxt.time - prev.time).total_seconds()
        if span <= 0:
            return prev.height
        ratio = (when - prev.time).total_seconds() / span
        return prev.height + (nxt.height - prev.height) * (1 - math.cos(math.pi * ratio)) / 2

    def level_percent(self, now: datetime) -> float | None:
        """Position de la hauteur actuelle entre basse mer (0 %) et pleine mer (100 %)."""
        prev = self.previous_extremum(now)
        nxt = self.next_extremum(now)
        height = self.height_at(now)
        if prev is None or nxt is None or height is None:
            return None
        low, high = sorted((prev.height, nxt.height))
        if high - low < 1e-6:
            return None
        return max(0.0, min(100.0, (height - low) / (high - low) * 100))

    # --- Coefficients -------------------------------------------------------

    def coefficients_on(self, day_start: datetime, day_end: datetime) -> list[int]:
        """Coefficients des pleines mers comprises dans l'intervalle."""
        return [
            e.coefficient
            for e in self.extrema
            if e.kind == HIGH and e.coefficient is not None and day_start <= e.time < day_end
        ]

    # --- Export pour la carte ----------------------------------------------

    def sea_height_at(self, when: datetime) -> float | None:
        """Niveau de la mer (source EDF) par interpolation linéaire."""
        return _interpolate(self.curve_start, self.curve_step, self.sea_curve, when)

    def lock_at(self, when: datetime) -> Any | None:
        """Période d'écluse en cours."""
        return next((p for p in self.locks if p.start <= when < p.end), None)

    def lock_windows(self, passable: frozenset[str]) -> list[tuple[datetime, datetime]]:
        """Fenêtres de passage possibles (statuts consécutifs « passables » fusionnés)."""
        windows: list[tuple[datetime, datetime]] = []
        for period in self.locks:
            if period.status not in passable:
                continue
            if windows and windows[-1][1] == period.start:
                windows[-1] = (windows[-1][0], period.end)
            else:
                windows.append((period.start, period.end))
        return windows

    def card_attributes(self, now: datetime | None = None) -> dict[str, Any]:
        """Données compactes exposées en attributs pour la carte Lovelace.

        La courbe est limitée à [maintenant - 24 h, maintenant + 48 h] et
        ramenée à un pas de 10 min au plus fin, pour garder des attributs légers.
        """
        now = now or datetime.now(UTC)
        start, step, curve, sea = self.curve_start, self.curve_step, self.curve, self.sea_curve
        if start is not None and curve:
            factor = max(1, int(CARD_STEP / step))
            first = max(0, int((now - CARD_BEFORE - start) / step))
            last = min(len(curve) - 1, int((now + CARD_AFTER - start) / step) + 1)
            start = start + step * first
            curve = curve[first : last + 1 : factor]
            sea = sea[first : last + 1 : factor] if sea else []
            step = step * factor
        lo, hi = now - CARD_BEFORE, now + CARD_EXTREMA_AFTER
        attrs: dict[str, Any] = {
            "site_id": self.site_id,
            "site_name": self.site_name,
            "source": self.source,
            "extrema": [e.as_dict() for e in self.extrema if lo <= e.time <= hi],
            "curve_start": start.isoformat() if start else None,
            "curve_step": int(step.total_seconds() // 60),
            "curve": [round(h, 2) for h in curve],
            "sun": [s.as_dict() for s in self.sun],
            "data_attribution": self.attribution,
            "fetched_at": self.fetched_at.isoformat(),
        }
        if sea:
            attrs["sea_curve"] = [round(h, 2) for h in sea]
        if self.locks:
            attrs["locks"] = [p.as_dict() for p in self.locks if p.end >= lo and p.start <= hi]
        return attrs


# --- Analyse des réponses JSON (tz=UTC) ------------------------------------


def _utc(day: str, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=UTC)


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_extrema(payload: dict[str, Any]) -> list[Extremum]:
    """Convertit la réponse de /tide-extrema en liste triée d'extrema."""
    result: list[Extremum] = []
    for day in payload.get("data", []):
        for item in day.get("extrema", []):
            kind = HIGH if item.get("type") == "PM" else LOW
            coef = item.get("coef")
            result.append(
                Extremum(
                    kind=kind,
                    time=_utc(day["date"], item["time"]),
                    height=float(item["height"]),
                    coefficient=int(coef) if coef is not None else None,
                )
            )
    result.sort(key=lambda e: e.time)
    return result


def parse_curve(payload: dict[str, Any]) -> tuple[datetime | None, timedelta, list[float]]:
    """Convertit la réponse de /water-levels en (début, pas, hauteurs)."""
    points = payload.get("data", [])
    step = timedelta(minutes=int(payload.get("step_minutes", 10)))
    if not points:
        return None, step, []
    return _parse_iso(points[0]["time"]), step, [float(p["height"]) for p in points]


def parse_sun(payload: dict[str, Any]) -> list[SunTimes]:
    """Convertit la réponse de /sun-times."""
    return [
        SunTimes(
            day=item["date"],
            sunrise=_utc(item["date"], item["sunrise"]) if item.get("sunrise") else None,
            sunset=_utc(item["date"], item["sunset"]) if item.get("sunset") else None,
        )
        for item in payload.get("data", [])
    ]


def parse_attribution(payload: dict[str, Any]) -> str | None:
    """Texte d'attribution (HTML) exigé par la licence CC BY 4.0."""
    source = payload.get("source") or {}
    return source.get("attribution")
