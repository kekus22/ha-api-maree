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
