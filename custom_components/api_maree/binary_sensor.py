"""Capteur binaire « vives eaux »."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CONF_SPRING_TIDE_THRESHOLD, DEFAULT_SPRING_TIDE_THRESHOLD
from .coordinator import ApiMareeConfigEntry, ApiMareeCoordinator
from .entity import ApiMareeEntity
from .tide import HIGH


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ApiMareeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Ajoute le capteur binaire d'un port."""
    threshold = entry.options.get(CONF_SPRING_TIDE_THRESHOLD, DEFAULT_SPRING_TIDE_THRESHOLD)
    async_add_entities([SpringTideBinarySensor(entry.runtime_data, int(threshold))])


class SpringTideBinarySensor(ApiMareeEntity, BinarySensorEntity):
    """Actif quand le coefficient de la prochaine pleine mer atteint le seuil."""

    def __init__(self, coordinator: ApiMareeCoordinator, threshold: int) -> None:
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
