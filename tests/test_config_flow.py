"""Config flow."""

from __future__ import annotations

import json
from pathlib import Path

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.shopping_list_catalog.const import DOMAIN, STARTER_CATALOG

from .conftest import CATALOG, write_json


async def _start(hass: HomeAssistant, name: str, source: str):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"name": name, "source": source})


async def test_starter_catalog(hass: HomeAssistant, config_dir: Path) -> None:
    result = await _start(hass, "Trader Joe's", "starter")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Trader Joe's"
    assert result["data"] == {"path": "shopping_catalogs/trader_joes.json"}
    assert json.loads((config_dir / "shopping_catalogs" / "trader_joes.json").read_text()) == STARTER_CATALOG
    await hass.async_block_till_done()
    assert hass.states.get("sensor.shopping_catalog_trader_joes").state == "5"


async def test_empty_catalog(hass: HomeAssistant, config_dir: Path) -> None:
    result = await _start(hass, "Costco", "empty")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get("sensor.shopping_catalog_costco").state == "0"


async def test_copy_from_another_catalog(hass: HomeAssistant, loaded_entry: MockConfigEntry, config_dir: Path) -> None:
    result = await _start(hass, "Ralphs", f"copy:{loaded_entry.entry_id}")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert json.loads((config_dir / "shopping_catalogs" / "ralphs.json").read_text()) == CATALOG


async def test_new_catalog_does_not_overwrite_a_file(hass: HomeAssistant, config_dir: Path) -> None:
    write_json(config_dir / "shopping_catalogs" / "costco.json", {"Mine": []})
    result = await _start(hass, "Costco", "starter")
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "file_exists"}
    assert result["description_placeholders"]["path"] == "shopping_catalogs/costco.json"
    assert json.loads((config_dir / "shopping_catalogs" / "costco.json").read_text()) == {"Mine": []}


async def test_name_needs_letters(hass: HomeAssistant, config_dir: Path) -> None:
    result = await _start(hass, "!!!", "starter")
    assert result["errors"] == {"name": "invalid_name"}


async def test_existing_file(hass: HomeAssistant, config_dir: Path) -> None:
    write_json(config_dir / "shopping_items.json", CATALOG)
    original = (config_dir / "shopping_items.json").read_text()
    result = await _start(hass, "Groceries", "existing")
    assert result["step_id"] == "existing"

    for path, error in (
        ("../outside.json", "invalid_path"),
        ("missing.json", "file_not_found"),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"path": path})
        assert result["errors"] == {"path": error}

    (config_dir / "broken.json").write_text('{"A": [{}]}')
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"path": "broken.json"})
    assert result["errors"] == {"path": "invalid_catalog"}
    assert "needs a title" in result["description_placeholders"]["error"]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"path": "shopping_items.json"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"path": "shopping_items.json"}
    await hass.async_block_till_done()
    assert (config_dir / "shopping_items.json").read_text() == original
    assert hass.states.get("sensor.shopping_catalog_groceries").attributes["catalog"] == CATALOG


async def test_same_file_cannot_be_added_twice(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    result = await _start(hass, "Other name", "existing")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"path": "shopping_catalogs/trader_joes.json"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
