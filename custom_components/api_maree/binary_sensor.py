"""Capteur binaire « vives eaux »."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CONF_SPRING_TIDE_THRESHOLD, DEFAULT_SPRING_TIDE_THRESHOLD, SOURCE_EDF_RANCE
from .coordinator import ApiMareeConfigEntry, TideCoordinator
from .edf import LOCK_PASSABLE
from .entity import ApiMareeEntity
from .tide import HIGH


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ApiMareeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Ajoute les capteurs binaires d'un site."""
    coordinator = entry.runtime_data
    if coordinator.source == SOURCE_EDF_RANCE:
        async_add_entities([LockPassableBinarySensor(coordinator)])
        return
    threshold = entry.options.get(CONF_SPRING_TIDE_THRESHOLD, DEFAULT_SPRING_TIDE_THRESHOLD)
    async_add_entities([SpringTideBinarySensor(coordinator, int(threshold))])


class SpringTideBinarySensor(ApiMareeEntity, BinarySensorEntity):
    """Actif quand le coefficient de la prochaine pleine mer atteint le seuil."""

    def __init__(self, coordinator: TideCoordinator, threshold: int) -> None:
        super().__init__(coordinator, "spring_tide")
        self._threshold = threshold

    @property
    def is_on(self) -> bool | None:
        """Vives eaux ?"""
        ext = self.coordinator.data.next_extremum(dt_util.utcnow(), HIGH)
        if ext is None or ext.coefficient is None:
            return None
        return ext.coefficient >= self._threshold

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Seuil utilisé."""
        return {"threshold": self._threshold}


class LockPassableBinarySensor(ApiMareeEntity, BinarySensorEntity):
    """Actif quand le passage par l'écluse de la Rance est possible (sas ou à la demande)."""

    def __init__(self, coordinator: TideCoordinator) -> None:
        super().__init__(coordinator, "lock_open")

    @property
    def is_on(self) -> bool | None:
        """Écluse praticable ?"""
        period = self.coordinator.data.lock_at(dt_util.utcnow())
        if period is None:
            return None
        return period.status in LOCK_PASSABLE
