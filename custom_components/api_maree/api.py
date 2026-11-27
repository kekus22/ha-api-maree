"""Client asynchrone minimal pour l'API https://api-maree.fr."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import aiohttp

from .const import BASE_URL, DEMO_API_KEY

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


class ApiMareeError(Exception):
    """Erreur générique de l'API."""


class ApiMareeConnectionError(ApiMareeError):
    """Le service est injoignable."""


class ApiMareeAuthError(ApiMareeError):
    """Clé API invalide, ou site non accessible avec cette clé."""


class ApiMareeRateLimitError(ApiMareeError):
    """Quota horaire dépassé."""


@dataclass(frozen=True, slots=True)
class Site:
    """Un site (port) proposé par l'API."""

    site_id: str
    name: str
    latitude: float | None
    longitude: float | None
    demo: bool


class ApiMareeClient:
    """Accès aux endpoints utilisés par l'intégration.

    Toutes les requêtes sont faites en UTC (`tz=UTC`) pour éviter toute
    ambiguïté au changement d'heure ; la conversion en heure locale est faite
    par Home Assistant et par la carte.
    """

    def __init__(self, session: aiohttp.ClientSession, api_key: str | None) -> None:
        self._session = session
        self._api_key = api_key or DEMO_API_KEY

    @property
    def is_demo(self) -> bool:
        """Vrai si le client utilise la clé de démonstration."""
        return self._api_key == DEMO_API_KEY

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}
        try:
            async with self._session.get(
                f"{BASE_URL}{path}", params=params, headers=headers, timeout=REQUEST_TIMEOUT
            ) as resp:
                try:
                    payload = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    payload = {}
                if resp.status == 401:
                    raise ApiMareeAuthError(payload.get("error", "invalid_api_key"))
                if resp.status == 429:
                    raise ApiMareeRateLimitError(payload.get("error", "rate_limited"))
                if resp.status >= 400:
                    raise ApiMareeError(
                        f"HTTP {resp.status}: {payload.get('error', 'unknown_error')}"
                    )
                if not isinstance(payload, dict):
                    raise ApiMareeError("Réponse inattendue de l'API")
                return payload
        except (TimeoutError, aiohttp.ClientError) as err:
            raise ApiMareeConnectionError(str(err)) from err

    async def async_get_sites(self) -> list[Site]:
        """Liste des sites disponibles (endpoint public)."""
        payload = await self._get("/sites")
        return [
            Site(
                site_id=item["site_id"],
                name=item.get("site_name") or item["site_id"],
                latitude=item.get("latitude"),
                longitude=item.get("longitude"),
                demo=bool(item.get("demo", False)),
            )
            for item in payload.get("sites", [])
            if item.get("site_id")
        ]

    async def async_get_tide_extrema(self, site: str, start: date, end: date) -> dict[str, Any]:
        """Pleines et basses mers (bornes de jours incluses)."""
        return await self._get(
            "/tide-extrema",
            {"site": site, "from": start.isoformat(), "to": end.isoformat(), "tz": "UTC"},
        )

    async def async_get_water_levels(
        self, site: str, start: datetime, end: datetime, step: int
    ) -> dict[str, Any]:
        """Série de hauteurs d'eau (datetimes UTC naïfs ou aware, convertis en UTC)."""
        return await self._get(
            "/water-levels",
            {
                "site": site,
                "from": _fmt_utc(start),
                "to": _fmt_utc(end),
                "step": step,
                "tz": "UTC",
            },
        )

    async def async_get_sun_times(self, site: str, start: date, end: date) -> dict[str, Any]:
        """Lever et coucher du soleil (bornes de jours incluses)."""
        return await self._get(
            "/sun-times",
            {"site": site, "from": start.isoformat(), "to": end.isoformat(), "tz": "UTC"},
        )


def _fmt_utc(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M")
