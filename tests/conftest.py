"""Shared fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.shopping_list_catalog.const import CONF_PATH, DOMAIN

CATALOG = {
    "Fruits": [
        {"title": "Apple", "types": [{"name": "Gala", "image": "/local/gala.png"}, "Granny Smith"], "custom": 1},
        {"title": "Banana"},
    ],
    "Dairy": [{"title": "Milk", "subtitle": "Lactose-free"}],
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow loading the integration under test."""


@pytest.fixture
def config_dir(hass: HomeAssistant, tmp_path: Path) -> Path:
    """Use an isolated configuration directory."""
    hass.config.config_dir = str(tmp_path)
    return tmp_path


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
async def loaded_entry(hass: HomeAssistant, config_dir: Path) -> MockConfigEntry:
    """A loaded Trader Joe's catalog backed by shopping_catalogs/trader_joes.json."""
    write_json(config_dir / "shopping_catalogs" / "trader_joes.json", CATALOG)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Trader Joe's",
        data={CONF_PATH: "shopping_catalogs/trader_joes.json"},
        unique_id="shopping_catalogs/trader_joes.json",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
