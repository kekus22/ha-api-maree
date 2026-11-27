"""Entité de base de l'intégration API Marée."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SOURCE_EDF_RANCE
from .coordinator import TideCoordinator
from .edf import PAGE_URL


class ApiMareeEntity(CoordinatorEntity[TideCoordinator]):
    """Entité rattachée à un port."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TideCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.site_id}_{key}"
        self._attr_translation_key = key
        edf = coordinator.source == SOURCE_EDF_RANCE
        self._attr_attribution = (
            "Prévisions : EDF, usine marémotrice de la Rance"
            if edf
            else "Données de marée : api-maree.fr (CC BY 4.0)"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.site_id)},
            name=f"Marée {coordinator.site_name}",
            manufacturer="EDF" if edf else "api-maree.fr",
            model="Niveaux et éclusage de la Rance" if edf else "Prévisions de marée",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=PAGE_URL if edf else "https://api-maree.fr/",
        )
