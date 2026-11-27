"""Fixtures communes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from homeassistant.const import CONF_API_KEY
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.api_maree.const import (
    BASE_URL,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    DOMAIN,
)

FIXTURES = Path(__file__).parent / "fixtures"
# Instant de référence des fixtures (Brest) : marée descendante,
# pleine mer à 05:24 UTC (7,305 m), basse mer à 11:55 UTC (1,299 m).
NOW = "2026-11-27T09:00:00+00:00"


def load_fixture(name: str) -> dict[str, Any]:
    """Charge une réponse JSON enregistrée."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Active les intégrations personnalisées."""


@pytest.fixture
def mock_api(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Simule les endpoints d'api-maree.fr."""
    aioclient_mock.get(f"{BASE_URL}/sites", json=load_fixture("sites.json"))
    aioclient_mock.get(f"{BASE_URL}/tide-extrema", json=load_fixture("tide_extrema.json"))
    aioclient_mock.get(f"{BASE_URL}/water-levels", json=load_fixture("water_levels.json"))
    aioclient_mock.get(f"{BASE_URL}/sun-times", json=load_fixture("sun_times.json"))
    return aioclient_mock


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Entrée de configuration pour Brest, en mode démo."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Brest",
        unique_id="brest",
        data={
            CONF_API_KEY: "",
            CONF_SITE_ID: "brest",
            CONF_SITE_NAME: "Brest",
            CONF_LATITUDE: 48.355071,
            CONF_LONGITUDE: -4.553849,
        },
    )


@pytest.fixture
def edf_entry() -> MockConfigEntry:
    """Entrée de configuration pour l'estuaire de la Rance (EDF)."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Rance (Saint-Suliac)",
        unique_id="edf_rance",
        data={
            "source": "edf_rance",
            CONF_SITE_ID: "edf_rance",
            CONF_SITE_NAME: "Rance (Saint-Suliac)",
            CONF_LATITUDE: 48.5806,
            CONF_LONGITUDE: -1.9811,
        },
    )


@pytest.fixture
def mock_edf(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Simule les fichiers EDF : une semaine publiée, l'écluse du 26/11 au 04/12."""
    from datetime import date, timedelta

    from custom_components.api_maree.edf import LEVELS_URL, LOCK_URL, local_midnight

    aioclient_mock.get(
        LEVELS_URL.format(monday="20261123"), json=load_fixture("edf/NiveauxRance_20261123.json")
    )
    for monday in ("20261116", "20261130", "20261207", "20261214"):
        aioclient_mock.get(LEVELS_URL.format(monday=monday), status=404)
    lock = load_fixture("edf/eclusage_1795734000.json")
    for offset in range(-2, 10):
        day = date(2026, 11, 27) + timedelta(days=offset)
        url = LOCK_URL.format(timestamp=int(local_midnight(day).timestamp()))
        aioclient_mock.get(url, json=lock)
    return aioclient_mock
