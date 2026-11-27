"""Tests du formulaire de configuration."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.api_maree.const import (
    BASE_URL,
    CONF_SITE_ID,
    CONF_SPRING_TIDE_THRESHOLD,
    DOMAIN,
)

from .conftest import load_fixture


def _site_options(result) -> list[str]:
    selector = result["data_schema"].schema[CONF_SITE_ID]
    return [opt["value"] for opt in selector.config["options"]]


async def test_demo_flow(hass: HomeAssistant, mock_api: AiohttpClientMocker) -> None:
    """Sans clé : seuls les ports de démo sont proposés."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "site"
    assert _site_options(result) == ["brest", "saint-malo"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SITE_ID: "brest"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Brest"
    assert result["data"][CONF_API_KEY] == ""
    assert result["result"].unique_id == "brest"


async def test_personal_key_flow(hass: HomeAssistant, mock_api: AiohttpClientMocker) -> None:
    """Avec une clé valide : tous les ports sont proposés."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: " abc123 "}
    )
    assert _site_options(result) == ["aber-wrac-h", "brest", "saint-malo", "wissant"]
    # La clé est vérifiée sur un port hors démo.
    probe = [c for c in mock_api.mock_calls if c[1].path == "/tide-extrema"]
    assert probe and probe[0][1].query["site"] == "aber-wrac-h"
    assert probe[0][3]["Authorization"] == "Bearer abc123"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SITE_ID: "wissant"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_KEY] == "abc123"


async def test_invalid_key(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Clé refusée : erreur affichée."""
    aioclient_mock.get(f"{BASE_URL}/sites", json=load_fixture("sites.json"))
    aioclient_mock.get(f"{BASE_URL}/tide-extrema", status=401, json={"error": "invalid_api_key"})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "bad"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_cannot_connect(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Service indisponible."""
    aioclient_mock.get(f"{BASE_URL}/sites", status=500)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_configured_sites_are_hidden(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Un port déjà configuré n'est plus proposé."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert _site_options(result) == ["saint-malo"]


async def test_reauth(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Remplacement de la clé."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "newkey"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_API_KEY] == "newkey"


async def test_options(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Seuil de vives eaux."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SPRING_TIDE_THRESHOLD: 95}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_SPRING_TIDE_THRESHOLD: 95}
