"""Sensor, polling, and WebSocket commands."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.shopping_list_catalog.const import SCAN_INTERVAL

from .conftest import CATALOG, write_json

ENTITY = "sensor.shopping_catalog_trader_joes"


async def test_sensor_exposes_catalog(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    state = hass.states.get(ENTITY)
    assert state.state == "3"
    assert state.attributes["catalog"] == CATALOG
    assert state.attributes["friendly_name"] == "Trader Joe's catalog"
    assert state.attributes["unit_of_measurement"] == "products"


async def test_unrecorded_catalog_attribute(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    entity = hass.data["entity_components"]["sensor"].get_entity(ENTITY)
    assert "catalog" in entity._unrecorded_attributes


async def test_manual_edits_are_picked_up_by_polling(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, config_dir: Path
) -> None:
    path = config_dir / "shopping_catalogs" / "trader_joes.json"
    write_json(path, {"Bakery": [{"title": "Bread"}]})
    async_fire_time_changed(hass, dt_util.utcnow() + SCAN_INTERVAL + timedelta(seconds=1))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(ENTITY).state == "1"

    path.write_text("broken")
    async_fire_time_changed(hass, dt_util.utcnow() + 2 * SCAN_INTERVAL + timedelta(seconds=2))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(ENTITY).state == STATE_UNAVAILABLE


async def test_unload_keeps_file(hass: HomeAssistant, loaded_entry: MockConfigEntry, config_dir: Path) -> None:
    assert await hass.config_entries.async_remove(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.NOT_LOADED
    assert (config_dir / "shopping_catalogs" / "trader_joes.json").exists()


async def test_ws_get_and_save(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, config_dir: Path, hass_ws_client: WebSocketGenerator
) -> None:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "shopping_list_catalog/get", "entity_id": ENTITY})
    got = await client.receive_json()
    assert got["success"]
    result = got["result"]
    assert result["title"] == "Trader Joe's"
    assert result["path"] == "shopping_catalogs/trader_joes.json"
    assert result["catalog"] == CATALOG
    assert result["error"] is None

    updated = {**CATALOG, "Bakery": [{"title": "Bread"}]}
    await client.send_json_auto_id(
        {"type": "shopping_list_catalog/save", "entity_id": ENTITY, "catalog": updated, "revision": result["revision"]}
    )
    saved = await client.receive_json()
    assert saved["success"]
    assert saved["result"]["revision"] != result["revision"]
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "4"
    assert json.loads((config_dir / "shopping_catalogs" / "trader_joes.json").read_text()) == updated

    await client.send_json_auto_id(
        {"type": "shopping_list_catalog/save", "entity_id": ENTITY, "catalog": CATALOG, "revision": result["revision"]}
    )
    conflict = await client.receive_json()
    assert not conflict["success"]
    assert conflict["error"]["code"] == "conflict"

    await client.send_json_auto_id(
        {
            "type": "shopping_list_catalog/save",
            "entity_id": ENTITY,
            "catalog": {"A": [{}]},
            "revision": saved["result"]["revision"],
        }
    )
    invalid = await client.receive_json()
    assert invalid["error"]["code"] == "invalid_format"
    assert "needs a title" in invalid["error"]["message"]


async def test_ws_save_requires_admin(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
) -> None:
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id({"type": "shopping_list_catalog/get", "entity_id": ENTITY})
    revision = (await client.receive_json())["result"]["revision"]
    await client.send_json_auto_id(
        {"type": "shopping_list_catalog/save", "entity_id": ENTITY, "catalog": {}, "revision": revision}
    )
    response = await client.receive_json()
    assert response["error"]["code"] == "unauthorized"


async def test_ws_rejects_other_entities(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, hass_ws_client: WebSocketGenerator
) -> None:
    hass.states.async_set("sensor.other", "1", {"catalog": {}})
    client = await hass_ws_client(hass)
    for entity_id in ("sensor.other", "sensor.missing"):
        await client.send_json_auto_id({"type": "shopping_list_catalog/get", "entity_id": entity_id})
        assert (await client.receive_json())["error"]["code"] == "not_found"
