"""Capteurs de marée."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import ApiMareeConfigEntry, ApiMareeCoordinator
from .entity import ApiMareeEntity
from .tide import HIGH, LOW, TideData

TIDE_STATE_OPTIONS = ["rising", "falling"]


@dataclass(frozen=True, kw_only=True)
class ApiMareeSensorDescription(SensorEntityDescription):
    """Description d'un capteur de marée."""

    value_fn: Callable[[TideData, datetime], Any]
    attrs_fn: Callable[[TideData, datetime], dict[str, Any]] | None = None


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def _next_attr(kind: str) -> Callable[[TideData, datetime], dict[str, Any]]:
    def _fn(data: TideData, now: datetime) -> dict[str, Any]:
        ext = data.next_extremum(now, kind)
        if ext is None:
            return {}
        attrs: dict[str, Any] = {"height": round(ext.height, 2)}
        if ext.coefficient is not None:
            attrs["coefficient"] = ext.coefficient
        return attrs

    return _fn


def _next_time(kind: str) -> Callable[[TideData, datetime], datetime | None]:
    def _fn(data: TideData, now: datetime) -> datetime | None:
        ext = data.next_extremum(now, kind)
        return ext.time if ext else None

    return _fn


def _next_height(kind: str) -> Callable[[TideData, datetime], float | None]:
    def _fn(data: TideData, now: datetime) -> float | None:
        ext = data.next_extremum(now, kind)
        return round(ext.height, 2) if ext else None

    return _fn


def _tide_state(data: TideData, now: datetime) -> str | None:
    rising = data.is_rising(now)
    if rising is None:
        return None
    return "rising" if rising else "falling"


def _coefficient(data: TideData, now: datetime) -> int | None:
    ext = data.next_extremum(now, HIGH)
    return ext.coefficient if ext else None


def _local_day_bounds(now: datetime) -> tuple[datetime, datetime]:
    local = dt_util.as_local(now)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def _coefficient_attrs(data: TideData, now: datetime) -> dict[str, Any]:
    start, end = _local_day_bounds(now)
    today = data.coefficients_on(start, end)
    tomorrow = data.coefficients_on(end, end + timedelta(days=1))
    return {"today": today, "tomorrow": tomorrow}


def _height_attrs(data: TideData, now: datetime) -> dict[str, Any]:
    attrs = data.card_attributes()
    prev = data.previous_extremum(now)
    nxt = data.next_extremum(now)
    attrs["previous_tide"] = prev.as_dict() if prev else None
    attrs["next_tide"] = nxt.as_dict() if nxt else None
    return attrs


SENSORS: tuple[ApiMareeSensorDescription, ...] = (
    ApiMareeSensorDescription(
        key="water_level",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d, now: _round(d.height_at(now), 2),
        attrs_fn=_height_attrs,
    ),
    ApiMareeSensorDescription(
        key="tide_state",
        device_class=SensorDeviceClass.ENUM,
        options=TIDE_STATE_OPTIONS,
        value_fn=_tide_state,
    ),
    ApiMareeSensorDescription(
        key="tide_level",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d, now: _round(d.level_percent(now), 1),
    ),
    ApiMareeSensorDescription(
        key="next_high_tide",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_next_time(HIGH),
        attrs_fn=_next_attr(HIGH),
    ),
    ApiMareeSensorDescription(
        key="next_low_tide",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_next_time(LOW),
        attrs_fn=_next_attr(LOW),
    ),
    ApiMareeSensorDescription(
        key="next_high_tide_height",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        suggested_display_precision=2,
        value_fn=_next_height(HIGH),
        entity_registry_enabled_default=False,
    ),
    ApiMareeSensorDescription(
        key="next_low_tide_height",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        suggested_display_precision=2,
        value_fn=_next_height(LOW),
        entity_registry_enabled_default=False,
    ),
    ApiMareeSensorDescription(
        key="coefficient",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_coefficient,
        attrs_fn=_coefficient_attrs,
    ),
)

# Attributs volumineux ou purement techniques : exclus de l'historique (recorder).
_UNRECORDED = frozenset(
    {
        "extrema",
        "curve",
        "curve_start",
        "curve_step",
        "sun",
        "data_attribution",
        "fetched_at",
        "previous_tide",
        "next_tide",
        "site_id",
        "site_name",
    }
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ApiMareeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Ajoute les capteurs d'un port."""
    coordinator = entry.runtime_data
    async_add_entities(ApiMareeSensor(coordinator, desc) for desc in SENSORS)


class ApiMareeSensor(ApiMareeEntity, SensorEntity):
    """Capteur calculé à partir des données en cache."""

    entity_description: ApiMareeSensorDescription
    _unrecorded_attributes = _UNRECORDED

    def __init__(
        self, coordinator: ApiMareeCoordinator, description: ApiMareeSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Valeur courante."""
        return self.entity_description.value_fn(self.coordinator.data, dt_util.utcnow())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Attributs supplémentaires."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data, dt_util.utcnow())
