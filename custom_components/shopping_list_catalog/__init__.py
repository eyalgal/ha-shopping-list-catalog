"""Shopping List Catalog: editable product catalogs for Shopping List Card."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .catalog import CatalogStore, resolve_catalog_path
from .const import CONF_PATH, CONF_TODO_LISTS, DOMAIN, SCAN_INTERVAL
from .history import PurchaseHistory
from .websocket import async_register_commands

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class CatalogRuntime:
    """A loaded catalog and the purchase history of its lists."""

    catalog: CatalogStore
    history: PurchaseHistory


type CatalogConfigEntry = ConfigEntry[CatalogRuntime]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the WebSocket commands shared by all catalogs."""
    async_register_commands(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CatalogConfigEntry) -> bool:
    """Load one catalog file, expose it as a sensor, and learn from its lists."""
    store = CatalogStore(hass, resolve_catalog_path(hass.config.config_dir, entry.data[CONF_PATH]))
    await store.async_refresh(force=True)
    history = PurchaseHistory(hass, entry.entry_id, list(entry.options.get(CONF_TODO_LISTS, [])), store)
    await history.async_load()
    await history.async_backfill()
    entry.runtime_data = CatalogRuntime(store, history)

    async def _poll(_now) -> None:
        await store.async_refresh()

    entry.async_on_unload(async_track_time_interval(hass, _poll, SCAN_INTERVAL))
    entry.async_on_unload(history.async_start())
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: CatalogConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: CatalogConfigEntry) -> bool:
    """Unload a catalog. The catalog file and purchase history are kept."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.history.async_flush()
    return unloaded
