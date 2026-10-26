"""Config flow: one entry per catalog file."""

from __future__ import annotations

import copy
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .catalog import (
    CatalogError,
    CatalogPathError,
    encode_catalog,
    read_catalog_file,
    relative_catalog_path,
    resolve_catalog_path,
    write_catalog_file,
)
from .const import (
    CATALOG_DIR,
    CONF_PATH,
    CONF_SOURCE,
    CONF_TODO_LISTS,
    DEFAULT_EXISTING_PATH,
    DOMAIN,
    SOURCE_COPY_PREFIX,
    SOURCE_EMPTY,
    SOURCE_EXISTING,
    SOURCE_STARTER,
    STARTER_CATALOG,
)
from .sensor import catalog_object_id

CONF_NAME = "name"


def _validate_existing_file(config_dir: str, value: str) -> str:
    """Check that value is a readable catalog and return its stored path. Runs in the executor."""
    path = resolve_catalog_path(config_dir, value)
    read_catalog_file(path)
    return relative_catalog_path(config_dir, path)


class CatalogConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create a catalog from a starter, an empty file, an existing file, or another catalog."""

    VERSION = 1

    def __init__(self) -> None:
        self._name = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CatalogOptionsFlow:
        """Choose the to-do lists this catalog learns purchases from."""
        return CatalogOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Name the catalog and choose where its products come from."""
        errors: dict[str, str] = {}
        placeholders = {"path": ""}
        if user_input is not None:
            self._name = user_input[CONF_NAME].strip()
            source = user_input[CONF_SOURCE]
            if catalog_object_id(self._name) == "shopping_catalog":
                errors[CONF_NAME] = "invalid_name"
            elif source == SOURCE_EXISTING:
                return await self.async_step_existing()
            else:
                data = self._initial_data(source)
                if data is None:
                    errors[CONF_SOURCE] = "copy_unavailable"
                else:
                    relative = f"{CATALOG_DIR}/{catalog_object_id(self._name).removeprefix('shopping_catalog_')}.json"
                    await self.async_set_unique_id(relative)
                    self._abort_if_unique_id_configured()
                    path = await self.hass.async_add_executor_job(
                        resolve_catalog_path, self.hass.config.config_dir, relative
                    )
                    if await self.hass.async_add_executor_job(path.exists):
                        errors["base"] = "file_exists"
                        placeholders["path"] = relative
                    else:
                        await self.hass.async_add_executor_job(write_catalog_file, path, encode_catalog(data))
                        return self.async_create_entry(title=self._name, data={CONF_PATH: relative})

        options = [
            SelectOptionDict(value=SOURCE_STARTER, label="Starter products"),
            SelectOptionDict(value=SOURCE_EMPTY, label="Empty catalog"),
            SelectOptionDict(value=SOURCE_EXISTING, label="Existing JSON file"),
            *(
                SelectOptionDict(value=f"{SOURCE_COPY_PREFIX}{entry.entry_id}", label=f"Copy of {entry.title}")
                for entry in self._async_current_entries(include_ignore=False)
                if entry.state is ConfigEntryState.LOADED and entry.runtime_data.catalog.data is not None
            ),
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=self._name): TextSelector(),
                vol.Required(CONF_SOURCE, default=SOURCE_STARTER): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                ),
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors, description_placeholders=placeholders
        )

    async def async_step_existing(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Use a JSON file that already exists in the config directory."""
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            try:
                relative = await self.hass.async_add_executor_job(
                    _validate_existing_file, self.hass.config.config_dir, user_input[CONF_PATH]
                )
            except CatalogPathError:
                errors[CONF_PATH] = "invalid_path"
            except FileNotFoundError:
                errors[CONF_PATH] = "file_not_found"
            except OSError:
                errors[CONF_PATH] = "cannot_read"
            except CatalogError as err:
                errors[CONF_PATH] = "invalid_catalog"
                placeholders["error"] = str(err)
            else:
                await self.async_set_unique_id(relative)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=self._name, data={CONF_PATH: relative})

        schema = vol.Schema(
            {vol.Required(CONF_PATH, default=(user_input or {}).get(CONF_PATH, DEFAULT_EXISTING_PATH)): TextSelector()}
        )
        return self.async_show_form(
            step_id="existing", data_schema=schema, errors=errors, description_placeholders=placeholders
        )

    def _initial_data(self, source: str) -> dict[str, Any] | None:
        if source == SOURCE_STARTER:
            return copy.deepcopy(STARTER_CATALOG)
        if source == SOURCE_EMPTY:
            return {}
        if source.startswith(SOURCE_COPY_PREFIX):
            entry = self.hass.config_entries.async_get_entry(source.removeprefix(SOURCE_COPY_PREFIX))
            if (
                entry
                and entry.domain == DOMAIN
                and entry.state is ConfigEntryState.LOADED
                and entry.runtime_data.catalog.data is not None
            ):
                return copy.deepcopy(entry.runtime_data.catalog.data)
        return None


class CatalogOptionsFlow(OptionsFlow):
    """Pick the shopping to-do lists whose check-offs are logged as purchases."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the list picker."""
        if user_input is not None:
            return self.async_create_entry(data={CONF_TODO_LISTS: user_input.get(CONF_TODO_LISTS, [])})
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_TODO_LISTS, default=self.config_entry.options.get(CONF_TODO_LISTS, [])
                ): EntitySelector(EntitySelectorConfig(domain="todo", multiple=True)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
