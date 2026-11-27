"""Entité de base de l'intégration API Marée."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ApiMareeCoordinator


class ApiMareeEntity(CoordinatorEntity[ApiMareeCoordinator]):
    """Entité rattachée à un port."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ApiMareeCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.site_id}_{key}"
        self._attr_translation_key = key
        self._attr_attribution = "Données de marée : api-maree.fr (CC BY 4.0)"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.site_id)},
            name=f"Marée {coordinator.site_name}",
            manufacturer="api-maree.fr",
            model="Prévisions de marée",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://api-maree.fr/",
        )
