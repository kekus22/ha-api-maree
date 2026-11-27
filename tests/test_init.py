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

from .conftest import NOW, load_fixture


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
    assert 300 < len(level.attributes["curve"]) <= 360
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


@pytest.mark.freeze_time("2026-11-27T09:00:00+00:00")  # vendredi 27/11, 10:00 à Paris
async def test_edf_entities(
    hass: HomeAssistant, mock_edf: AiohttpClientMocker, edf_entry: MockConfigEntry
) -> None:
    """Estuaire de la Rance : niveaux, écluse et données de la carte."""
    await hass.config.async_set_time_zone("Europe/Paris")
    await _setup(hass, edf_entry)
    assert edf_entry.state is ConfigEntryState.LOADED
    states = {s.entity_id for s in hass.states.async_all()}
    prefix = "sensor.maree_rance_saint_suliac_"
    # Pas de coefficient ni de vives eaux pour la Rance.
    assert f"{prefix}tide_coefficient" not in states
    assert "binary_sensor.maree_rance_saint_suliac_spring_tide" not in states

    level = hass.states.get(f"{prefix}water_level")
    assert float(level.state) == pytest.approx(12.17, abs=0.05)  # niveau EDF à 10:00
    attrs = level.attributes
    assert attrs["source"] == "edf_rance"
    assert attrs["curve_step"] == 10
    assert len(attrs["sea_curve"]) == len(attrs["curve"])
    assert attrs["locks"][0]["status"] in ("sas", "uncertain", "no_sas")
    assert "EDF" in attrs["data_attribution"]

    assert float(hass.states.get(f"{prefix}sea_level").state) == pytest.approx(11.03, abs=0.05)
    assert hass.states.get(f"{prefix}tide").state == "rising"
    high = hass.states.get(f"{prefix}next_high_tide")
    assert high.attributes["height"] == 12.28
    assert high.attributes["plateau_start"] == "2026-11-27T09:25:00+00:00"  # 10:25 Paris

    # Écluse le 27/11 : sas de 05h à 14h, pas de sas de 14h à 18h, sas ensuite.
    assert hass.states.get(f"{prefix}lock").state == "sas"
    assert hass.states.get(f"{prefix}lock").attributes["next_status"] == "no_sas"
    assert hass.states.get(f"{prefix}lock_opening_end").state == "2026-11-27T13:00:00+00:00"
    assert hass.states.get(f"{prefix}next_lock_opening").state == "2026-11-27T17:00:00+00:00"
    assert hass.states.get("binary_sensor.maree_rance_saint_suliac_lock_passable").state == "on"

    # Aucune option (seuil de vives eaux) pour cette source.
    assert edf_entry.supports_options is False


@pytest.mark.freeze_time("2026-11-27T09:00:00+00:00")
async def test_edf_locks_optional(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, edf_entry: MockConfigEntry
) -> None:
    """Écluse indisponible : les niveaux restent disponibles."""
    from custom_components.api_maree.edf import LEVELS_URL

    aioclient_mock.get(
        LEVELS_URL.format(monday="20261123"), json=load_fixture("edf/NiveauxRance_20261123.json")
    )
    for monday in ("20261116", "20261130", "20261207", "20261214"):
        aioclient_mock.get(LEVELS_URL.format(monday=monday), status=404)
    from datetime import date

    from custom_components.api_maree.edf import LOCK_URL, local_midnight

    for offset in range(-2, 10):
        day = date(2026, 11, 27) + timedelta(days=offset)
        url = LOCK_URL.format(timestamp=int(local_midnight(day).timestamp()))
        aioclient_mock.get(url, status=500)
    await _setup(hass, edf_entry)
    assert edf_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.maree_rance_saint_suliac_water_level").state != "unavailable"
    assert hass.states.get("sensor.maree_rance_saint_suliac_lock").state == "unknown"
