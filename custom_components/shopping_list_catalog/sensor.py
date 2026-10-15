"""Catalog sensor read by Shopping List Card."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import slugify

from . import CatalogConfigEntry
from .catalog import CatalogStore, count_products
from .const import ATTR_CATALOG, ATTR_MISSING, ATTR_PURCHASES, ATTR_SUGGESTIONS


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
    """Add the catalog and suggestions sensors."""
    async_add_entities([CatalogSensor(entry), SuggestionsSensor(entry)])


class CatalogSensor(SensorEntity):
    """Product count, with the whole catalog in the catalog attribute."""

    _attr_should_poll = False
    _attr_icon = "mdi:cart-variant"
    _attr_native_unit_of_measurement = "products"
    # Large catalogs exceed the recorder's attribute size limit.
    _unrecorded_attributes = frozenset({ATTR_CATALOG, ATTR_SUGGESTIONS, ATTR_MISSING})

    def __init__(self, entry: CatalogConfigEntry) -> None:
        self._store: CatalogStore = entry.runtime_data.catalog
        self._history = entry.runtime_data.history
        self._attr_unique_id = entry.entry_id
        self._attr_name = f"{entry.title} catalog"
        self.entity_id = f"sensor.{catalog_object_id(entry.title)}"

    async def async_added_to_hass(self) -> None:
        """Follow catalog and purchase history changes."""
        self.async_on_remove(self._store.async_add_listener(self.async_write_ha_state))
        self.async_on_remove(self._history.async_add_listener(self.async_write_ha_state))

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
        """The catalog map, read by the card's catalog_attribute, plus purchase count and suggestions."""
        if self._store.data is None:
            return None
        return {
            ATTR_CATALOG: self._store.data,
            ATTR_PURCHASES: len(self._history.purchases),
            ATTR_SUGGESTIONS: self._history.suggestions,
            ATTR_MISSING: self._history.missing,
        }


class SuggestionsSensor(SensorEntity):
    """How many products are due, with the details in the items attribute."""

    _attr_should_poll = False
    _attr_icon = "mdi:cart-heart"
    _attr_native_unit_of_measurement = "products"
    _unrecorded_attributes = frozenset({"items"})

    def __init__(self, entry: CatalogConfigEntry) -> None:
        self._history = entry.runtime_data.history
        self._attr_unique_id = f"{entry.entry_id}_suggestions"
        self._attr_name = f"{entry.title} suggestions"
        self.entity_id = f"sensor.{catalog_object_id(entry.title)}_suggested"

    async def async_added_to_hass(self) -> None:
        """Follow suggestion changes."""
        self.async_on_remove(self._history.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> int:
        """Number of suggested products."""
        return len(self._history.suggestions)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Suggested products, most overdue first."""
        return {"items": [{"name": name, **info} for name, info in self._history.suggestions.items()]}
