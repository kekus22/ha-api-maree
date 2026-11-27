"""Coordinateur de mise à jour des données de marée."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    ApiMareeAuthError,
    ApiMareeClient,
    ApiMareeError,
    ApiMareeRateLimitError,
)
from .const import (
    CONF_SITE_ID,
    CONF_SITE_NAME,
    CURVE_HOURS_AFTER,
    CURVE_HOURS_BEFORE,
    CURVE_STEP_MINUTES,
    DOMAIN,
    EXTREMA_DAYS_AFTER,
    EXTREMA_DAYS_BEFORE,
    RECOMPUTE_INTERVAL,
    UPDATE_INTERVAL,
)
from .tide import TideData, parse_attribution, parse_curve, parse_extrema, parse_sun

_LOGGER = logging.getLogger(__name__)

type ApiMareeConfigEntry = ConfigEntry[ApiMareeCoordinator]


class ApiMareeCoordinator(DataUpdateCoordinator[TideData]):
    """Récupère les données d'un port toutes les 6 h.

    Entre deux appels, les entités sont recalculées chaque minute à partir des
    données en cache (hauteur actuelle, prochaine marée…), sans requête API.
    """

    config_entry: ApiMareeConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ApiMareeConfigEntry, client: ApiMareeClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.data[CONF_SITE_ID]}",
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.site_id: str = entry.data[CONF_SITE_ID]
        self.site_name: str = entry.data.get(CONF_SITE_NAME, self.site_id)
        self._unsub_tick: CALLBACK_TYPE | None = None

    async def _async_update_data(self) -> TideData:
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        today = now.date()
        curve_start = now.replace(minute=0) - timedelta(hours=CURVE_HOURS_BEFORE)
        curve_end = curve_start + timedelta(hours=CURVE_HOURS_BEFORE + CURVE_HOURS_AFTER)
        try:
            extrema_raw, curve_raw, sun_raw = await asyncio.gather(
                self.client.async_get_tide_extrema(
                    self.site_id,
                    today - timedelta(days=EXTREMA_DAYS_BEFORE),
                    today + timedelta(days=EXTREMA_DAYS_AFTER),
                ),
                self.client.async_get_water_levels(
                    self.site_id, curve_start, curve_end, CURVE_STEP_MINUTES
                ),
                self.client.async_get_sun_times(
                    self.site_id,
                    today - timedelta(days=EXTREMA_DAYS_BEFORE),
                    today + timedelta(days=EXTREMA_DAYS_AFTER),
                ),
            )
        except ApiMareeAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except ApiMareeRateLimitError as err:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="rate_limited") from err
        except ApiMareeError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        start, step, heights = parse_curve(curve_raw)
        return TideData(
            site_id=self.site_id,
            site_name=extrema_raw.get("site_name") or self.site_name,
            extrema=parse_extrema(extrema_raw),
            curve_start=start,
            curve_step=step,
            curve=heights,
            sun=parse_sun(sun_raw),
            attribution=parse_attribution(extrema_raw),
            fetched_at=now,
        )

    @callback
    def async_start_ticker(self) -> None:
        """Rafraîchit l'état des entités chaque minute, à partir du cache."""

        @callback
        def _tick(_: datetime) -> None:
            if self.data is not None:
                self.async_update_listeners()

        self._unsub_tick = async_track_time_interval(self.hass, _tick, RECOMPUTE_INTERVAL)

    @callback
    def async_stop_ticker(self) -> None:
        """Arrête le recalcul périodique."""
        if self._unsub_tick:
            self._unsub_tick()
            self._unsub_tick = None
