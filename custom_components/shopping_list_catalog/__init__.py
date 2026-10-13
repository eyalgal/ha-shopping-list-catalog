"""Shopping List Catalog: editable product catalogs for Shopping List Card."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .catalog import CatalogStore, resolve_catalog_path
from .const import CONF_PATH, DOMAIN, SCAN_INTERVAL
from .websocket import async_register_commands

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type CatalogConfigEntry = ConfigEntry[CatalogStore]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the WebSocket commands shared by all catalogs."""
    async_register_commands(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CatalogConfigEntry) -> bool:
    """Load one catalog file and expose it as a sensor."""
    store = CatalogStore(hass, resolve_catalog_path(hass.config.config_dir, entry.data[CONF_PATH]))
    await store.async_refresh(force=True)
    entry.runtime_data = store

    async def _poll(_now) -> None:
        await store.async_refresh()

    entry.async_on_unload(async_track_time_interval(hass, _poll, SCAN_INTERVAL))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CatalogConfigEntry) -> bool:
    """Unload a catalog. The file is kept."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
