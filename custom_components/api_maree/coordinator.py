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
    EDF_LOCK_DAYS,
    EDF_UPDATE_INTERVAL,
    EDF_WEEKS_AHEAD,
    EXTREMA_DAYS_AFTER,
    EXTREMA_DAYS_BEFORE,
    RECOMPUTE_INTERVAL,
    SOURCE_EDF_RANCE,
    UPDATE_INTERVAL,
)
from .edf import (
    EDF_ATTRIBUTION,
    EDF_SITE_NAME,
    EDF_TZ,
    EdfError,
    EdfRanceClient,
    build_lock_periods,
    monday_of,
)
from .tide import (
    TideData,
    find_extrema,
    parse_attribution,
    parse_curve,
    parse_extrema,
    parse_sun,
)

_LOGGER = logging.getLogger(__name__)

type ApiMareeConfigEntry = ConfigEntry[TideCoordinator]


class TideCoordinator(DataUpdateCoordinator[TideData]):
    """Base commune : données d'un site, recalculées chaque minute.

    Entre deux appels, les entités sont recalculées à partir des données en
    cache (hauteur actuelle, prochaine marée…), sans requête réseau.
    """

    config_entry: ApiMareeConfigEntry
    source: str

    def __init__(
        self, hass: HomeAssistant, entry: ApiMareeConfigEntry, interval: timedelta
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.data[CONF_SITE_ID]}",
            update_interval=interval,
        )
        self.site_id: str = entry.data[CONF_SITE_ID]
        self.site_name: str = entry.data.get(CONF_SITE_NAME, self.site_id)
        self._unsub_tick: CALLBACK_TYPE | None = None

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


class ApiMareeCoordinator(TideCoordinator):
    """Port api-maree.fr : 3 requêtes toutes les 6 h."""

    source = "api_maree"

    def __init__(
        self, hass: HomeAssistant, entry: ApiMareeConfigEntry, client: ApiMareeClient
    ) -> None:
        super().__init__(hass, entry, UPDATE_INTERVAL)
        self.client = client

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


class EdfRanceCoordinator(TideCoordinator):
    """Estuaire de la Rance (EDF) : niveaux sur 3 semaines et écluse sur 8 jours."""

    source = SOURCE_EDF_RANCE

    def __init__(
        self, hass: HomeAssistant, entry: ApiMareeConfigEntry, client: EdfRanceClient
    ) -> None:
        super().__init__(hass, entry, EDF_UPDATE_INTERVAL)
        self.client = client
        self.site_name = entry.data.get(CONF_SITE_NAME, EDF_SITE_NAME)

    async def _async_update_data(self) -> TideData:
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        today = now.astimezone(EDF_TZ).date()
        first_week = monday_of(today - timedelta(days=1))
        weeks = sorted(
            {first_week + timedelta(weeks=i) for i in range(EDF_WEEKS_AHEAD + 1)}
            | {monday_of(today)}
        )
        try:
            levels = await self.client.async_get_levels(weeks)
        except EdfError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        # L'écluse est facultative : en cas d'erreur, on garde les horaires précédents.
        days = [today + timedelta(days=i) for i in range(-1, EDF_LOCK_DAYS - 1)]
        try:
            locks = build_lock_periods(await self.client.async_get_locks(days))
        except EdfError as err:
            _LOGGER.warning("Horaires d'éclusage EDF indisponibles : %s", err)
            locks = self.data.locks if self.data else []

        return TideData(
            site_id=self.site_id,
            site_name=self.site_name,
            extrema=find_extrema(levels.start, levels.step, levels.estuary),
            curve_start=levels.start,
            curve_step=levels.step,
            curve=levels.estuary,
            sea_curve=levels.sea,
            locks=locks,
            attribution=EDF_ATTRIBUTION,
            fetched_at=now,
            source=SOURCE_EDF_RANCE,
        )
