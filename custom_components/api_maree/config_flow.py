"""Formulaire de configuration de l'intégration API Marée."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util

from .api import ApiMareeAuthError, ApiMareeClient, ApiMareeError, Site
from .const import (
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    CONF_SOURCE,
    CONF_SPRING_TIDE_THRESHOLD,
    DEFAULT_SPRING_TIDE_THRESHOLD,
    DEMO_API_KEY,
    DOMAIN,
    SOURCE_API_MAREE,
    SOURCE_EDF_RANCE,
)
from .edf import EDF_SITE_ID, EDF_SITE_NAME, EDF_TZ, PAGE_URL, EdfError, EdfRanceClient, monday_of

_LOGGER = logging.getLogger(__name__)

REGISTER_URL = "https://api-maree.fr/register"


def _normalize_key(value: str | None) -> str:
    value = (value or "").strip()
    return "" if value == DEMO_API_KEY else value


class ApiMareeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ajout d'un port : clé API facultative, puis choix du site."""

    VERSION = 1

    def __init__(self) -> None:
        self._api_key: str = ""
        self._sites: list[Site] = []

    async def _async_validate(self, api_key: str) -> tuple[list[Site], str | None]:
        """Vérifie la clé et renvoie les sites accessibles (ou un code d'erreur)."""
        client = ApiMareeClient(async_get_clientsession(self.hass), api_key)
        try:
            sites = await client.async_get_sites()
            if not sites:
                return [], "no_sites"
            if client.is_demo:
                return [s for s in sites if s.demo], None
            # La clé personnelle est testée sur un site hors démo.
            probe = next((s for s in sites if not s.demo), sites[0])
            today = dt_util.utcnow().date()
            await client.async_get_tide_extrema(probe.site_id, today, today)
        except ApiMareeAuthError:
            return [], "invalid_auth"
        except ApiMareeError:
            _LOGGER.exception("Impossible de joindre api-maree.fr")
            return [], "cannot_connect"
        return sites, None

    def _existing_key(self) -> str:
        for entry in self._async_current_entries(include_ignore=False):
            if key := entry.data.get(CONF_API_KEY):
                return key
        return ""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choix de la source de données."""
        return self.async_show_menu(
            step_id="user", menu_options=[SOURCE_API_MAREE, SOURCE_EDF_RANCE]
        )

    async def async_step_edf_rance(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Estuaire de la Rance (EDF) : aucune clé, un seul site."""
        await self.async_set_unique_id(EDF_SITE_ID)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            client = EdfRanceClient(async_get_clientsession(self.hass))
            today = dt_util.now(EDF_TZ).date()
            try:
                await client.async_get_levels([monday_of(today)])
            except EdfError:
                _LOGGER.exception("Impossible de lire les prévisions EDF de la Rance")
                errors["base"] = "edf_cannot_connect"
            else:
                return self.async_create_entry(
                    title=EDF_SITE_NAME,
                    data={
                        CONF_SOURCE: SOURCE_EDF_RANCE,
                        CONF_SITE_ID: EDF_SITE_ID,
                        CONF_SITE_NAME: EDF_SITE_NAME,
                        CONF_LATITUDE: 48.5806,
                        CONF_LONGITUDE: -1.9811,
                    },
                )
        return self.async_show_form(
            step_id="edf_rance",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"page_url": PAGE_URL},
        )

    async def async_step_api_maree(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """api-maree.fr, étape 1 : clé API (laisser vide pour l'accès sans compte)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._api_key = _normalize_key(user_input.get(CONF_API_KEY))
            sites, error = await self._async_validate(self._api_key)
            if error:
                errors["base"] = error
            else:
                self._sites = sorted(sites, key=lambda s: s.name.lower())
                return await self.async_step_site()

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_API_KEY,
                    description={"suggested_value": self._existing_key()},
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
            }
        )
        return self.async_show_form(
            step_id="api_maree",
            data_schema=schema,
            errors=errors,
            description_placeholders={"register_url": REGISTER_URL},
        )

    async def async_step_site(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Étape 2 : choix du port."""
        configured = {
            entry.unique_id for entry in self._async_current_entries(include_ignore=False)
        }
        available = [s for s in self._sites if s.site_id not in configured]
        if not available:
            return self.async_abort(reason="no_more_sites")

        if user_input is not None:
            site = next(s for s in available if s.site_id == user_input[CONF_SITE_ID])
            await self.async_set_unique_id(site.site_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=site.name,
                data={
                    CONF_SOURCE: SOURCE_API_MAREE,
                    CONF_API_KEY: self._api_key,
                    CONF_SITE_ID: site.site_id,
                    CONF_SITE_NAME: site.name,
                    CONF_LATITUDE: site.latitude,
                    CONF_LONGITUDE: site.longitude,
                },
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_SITE_ID): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=s.site_id, label=s.name) for s in available
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                        sort=False,
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="site",
            data_schema=schema,
            description_placeholders={
                "count": str(len(available)),
                "mode": "démo" if not self._api_key else "compte personnel",
            },
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Clé refusée : demande d'une nouvelle clé."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Saisie de la nouvelle clé."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            api_key = _normalize_key(user_input.get(CONF_API_KEY))
            sites, error = await self._async_validate(api_key)
            if error:
                errors["base"] = error
            elif entry.unique_id not in {s.site_id for s in sites}:
                errors["base"] = "site_not_allowed"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_KEY: api_key}
                )

        schema = vol.Schema(
            {
                vol.Optional(CONF_API_KEY): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                )
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={"site": entry.title},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ApiMareeOptionsFlow:
        """Options."""
        return ApiMareeOptionsFlow()

    @classmethod
    @callback
    def async_supports_options_flow(cls, config_entry: ConfigEntry) -> bool:
        """Le seuil de vives eaux n'a de sens que pour api-maree.fr."""
        return config_entry.data.get(CONF_SOURCE, SOURCE_API_MAREE) == SOURCE_API_MAREE


class ApiMareeOptionsFlow(OptionsFlowWithReload):
    """Réglage du seuil de vives eaux."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Formulaire des options."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SPRING_TIDE_THRESHOLD: int(user_input[CONF_SPRING_TIDE_THRESHOLD])}
            )
        current = self.config_entry.options.get(
            CONF_SPRING_TIDE_THRESHOLD, DEFAULT_SPRING_TIDE_THRESHOLD
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_SPRING_TIDE_THRESHOLD, default=current): NumberSelector(
                    NumberSelectorConfig(min=20, max=120, step=1, mode=NumberSelectorMode.SLIDER)
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
