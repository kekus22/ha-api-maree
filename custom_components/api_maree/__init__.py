"""Intégration Home Assistant pour api-maree.fr."""

from __future__ import annotations

from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ApiMareeClient
from .const import CONF_SOURCE, SOURCE_EDF_RANCE
from .coordinator import (
    ApiMareeConfigEntry,
    ApiMareeCoordinator,
    EdfRanceCoordinator,
    TideCoordinator,
)
from .edf import EdfRanceClient

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ApiMareeConfigEntry) -> bool:
    """Configure un port à partir d'une entrée de configuration."""
    session = async_get_clientsession(hass)
    coordinator: TideCoordinator
    if entry.data.get(CONF_SOURCE) == SOURCE_EDF_RANCE:
        coordinator = EdfRanceCoordinator(hass, entry, EdfRanceClient(session))
    else:
        coordinator = ApiMareeCoordinator(
            hass, entry, ApiMareeClient(session, entry.data.get(CONF_API_KEY))
        )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    coordinator.async_start_ticker()
    entry.async_on_unload(coordinator.async_stop_ticker)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ApiMareeConfigEntry) -> bool:
    """Décharge une entrée."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
