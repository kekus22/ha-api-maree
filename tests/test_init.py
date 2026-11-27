"""Tests de l'installation et des entités."""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.api_maree.const import BASE_URL, CONF_SPRING_TIDE_THRESHOLD

from .conftest import NOW


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.freeze_time(NOW)
async def test_entities(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Valeurs des capteurs à 09:00 UTC à Brest."""
    await hass.config.async_set_time_zone("Europe/Paris")
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    # 3 requêtes par rafraîchissement.
    assert mock_api.call_count == 3

    # Les identifiants suivent la langue du système (anglais pendant les tests).
    level = hass.states.get("sensor.maree_brest_water_level")
    assert 1.3 < float(level.state) < 7.3
    assert level.attributes["unit_of_measurement"] == "m"
    assert len(level.attributes["curve"]) == 360
    assert level.attributes["next_tide"]["type"] == "low"
    assert "api-maree.fr" in level.attributes["data_attribution"]

    assert hass.states.get("sensor.maree_brest_tide").state == "falling"
    assert 0 < float(hass.states.get("sensor.maree_brest_tide_level").state) < 100
    high = hass.states.get("sensor.maree_brest_next_high_tide")
    assert high.state == "2026-11-27T17:53:00+00:00"
    assert high.attributes["coefficient"] == 90
    low = hass.states.get("sensor.maree_brest_next_low_tide")
    assert low.state == "2026-11-27T11:55:00+00:00"
    assert low.attributes["height"] == 1.3
    coef = hass.states.get("sensor.maree_brest_tide_coefficient")
    assert coef.state == "90"
    assert coef.attributes["today"] == [93, 90]
    assert hass.states.get("binary_sensor.maree_brest_spring_tide").state == "on"


@pytest.mark.freeze_time(NOW)
async def test_threshold_option(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Le seuil de vives eaux est pris en compte."""
    entry = MockConfigEntry(
        domain=config_entry.domain,
        title=config_entry.title,
        unique_id=config_entry.unique_id,
        data=config_entry.data,
        options={CONF_SPRING_TIDE_THRESHOLD: 95},
    )
    await _setup(hass, entry)
    spring = next(s for s in hass.states.async_all() if s.entity_id.startswith("binary_sensor."))
    assert spring.state == "off"
    assert spring.attributes["threshold"] == 95


@pytest.mark.freeze_time(NOW)
async def test_values_refresh_without_api_calls(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Le recalcul chaque minute n'appelle pas l'API."""
    await _setup(hass, config_entry)
    calls = mock_api.call_count
    tide = hass.states.get("sensor.maree_brest_tide")
    assert tide.state == "falling"

    freezer.tick(timedelta(hours=4))  # après la basse mer de 11:55
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(tide.entity_id).state == "rising"
    assert mock_api.call_count == calls

    freezer.tick(timedelta(hours=2, minutes=1))  # rafraîchissement 6 h
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert mock_api.call_count == calls + 3


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Clé refusée au démarrage : demande de ré-authentification."""
    for path in ("/tide-extrema", "/water-levels", "/sun-times"):
        aioclient_mock.get(f"{BASE_URL}{path}", status=401, json={"error": "invalid_api_key"})
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert flows and flows[0]["context"]["source"] == "reauth"


async def test_unavailable_api_retries(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Service indisponible : nouvel essai plus tard."""
    for path in ("/tide-extrema", "/water-levels", "/sun-times"):
        aioclient_mock.get(f"{BASE_URL}{path}", status=503)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Déchargement propre."""
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED
