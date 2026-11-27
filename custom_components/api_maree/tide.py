"""Calculs de marée à partir des données brutes de l'API (sans dépendance HA)."""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

HIGH: Literal["high"] = "high"
LOW: Literal["low"] = "low"


@dataclass(frozen=True, slots=True)
class Extremum:
    """Une pleine mer (high) ou une basse mer (low)."""

    kind: Literal["high", "low"]
    time: datetime
    height: float
    coefficient: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """Forme sérialisable, utilisée par la carte."""
        data: dict[str, Any] = {
            "type": self.kind,
            "time": self.time.isoformat(),
            "height": round(self.height, 2),
        }
        if self.coefficient is not None:
            data["coef"] = self.coefficient
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
        if self.curve_start is not None and len(self.curve) >= 2:
            offset = (when - self.curve_start) / self.curve_step
            if 0 <= offset <= len(self.curve) - 1:
                idx = int(offset)
                frac = offset - idx
                if idx >= len(self.curve) - 1:
                    return self.curve[-1]
                return self.curve[idx] + (self.curve[idx + 1] - self.curve[idx]) * frac

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

    def card_attributes(self) -> dict[str, Any]:
        """Données compactes exposées en attributs pour la carte Lovelace."""
        return {
            "site_id": self.site_id,
            "site_name": self.site_name,
            "extrema": [e.as_dict() for e in self.extrema],
            "curve_start": self.curve_start.isoformat() if self.curve_start else None,
            "curve_step": int(self.curve_step.total_seconds() // 60),
            "curve": [round(h, 2) for h in self.curve],
            "sun": [s.as_dict() for s in self.sun],
            "data_attribution": self.attribution,
            "fetched_at": self.fetched_at.isoformat(),
        }


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
