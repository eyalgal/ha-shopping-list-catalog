"""Catalog sensor read by Shopping List Card."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import slugify

from . import CatalogConfigEntry
from .catalog import CatalogStore, count_products
from .const import ATTR_CATALOG


def catalog_object_id(title: str) -> str:
    """Return the object ID for a catalog title, e.g. Trader Joe's -> shopping_catalog_trader_joes."""
    cleaned = title.replace("'", "").replace("\u2019", "")
    if not any(char.isalnum() for char in cleaned):
        return "shopping_catalog"
    return f"shopping_catalog_{slugify(cleaned)}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CatalogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the catalog sensor."""
    async_add_entities([CatalogSensor(entry)])


class CatalogSensor(SensorEntity):
    """Product count, with the whole catalog in the catalog attribute."""

    _attr_should_poll = False
    _attr_icon = "mdi:cart-variant"
    _attr_native_unit_of_measurement = "products"
    # Large catalogs exceed the recorder's attribute size limit.
    _unrecorded_attributes = frozenset({ATTR_CATALOG})

    def __init__(self, entry: CatalogConfigEntry) -> None:
        self._store: CatalogStore = entry.runtime_data
        self._attr_unique_id = entry.entry_id
        self._attr_name = f"{entry.title} catalog"
        self.entity_id = f"sensor.{catalog_object_id(entry.title)}"

    async def async_added_to_hass(self) -> None:
        """Follow catalog changes."""
        self.async_on_remove(self._store.async_add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        """Unavailable while the file is missing or invalid."""
        return self._store.data is not None

    @property
    def native_value(self) -> int | None:
        """Number of product entries."""
        return count_products(self._store.data) if self._store.data is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The catalog map, read by the card's catalog_attribute."""
        return {ATTR_CATALOG: self._store.data} if self._store.data is not None else None
