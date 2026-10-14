"""Purchase history learned from to-do lists."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.shopping_list_catalog.const import CONF_PATH, CONF_TODO_LISTS, DOMAIN
from custom_components.shopping_list_catalog.history import parse_summary, purchases_from_shopping_list_events

from .conftest import CATALOG, write_json

LIST = "todo.shopping_list"
SENSOR = "sensor.shopping_catalog_supermarket"


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        ("Milk", ("Milk", 1)),
        ("Milk (2)", ("Milk", 2)),
        ("Milk (4 bottles)", ("Milk", 4)),
        (" Eggs (12) ", ("Eggs", 12)),
    ],
)
def test_parse_summary(summary, expected) -> None:
    assert parse_summary(summary) == expected


def test_recorded_events_become_purchases() -> None:
    def event(ts, action, uid, name, complete):
        return ts, json.dumps({"action": action, "item": {"id": uid, "name": name, "complete": complete}})

    rows = [
        event(1000, "add", "a", "Milk (2)", False),
        event(2000, "update", "a", "Milk (2)", True),
        event(2100, "update", "a", "Milk (2)", True),
        event(3000, "update", "b", "Eggs", True),
        event(3500, "update", "b", "Eggs", False),
        event(4000, "update", "c", "Bread", True),
        event(4000 + 2 * 86400, "update", "c", "Bread", False),
        event(5000, "remove", "d", "Apple", False),
        (6000, "not json"),
        (6100, json.dumps({"action": "reorder"})),
    ]
    purchases = purchases_from_shopping_list_events(rows, LIST)
    assert [(p["uid"], p["name"], p["quantity"], p["list"]) for p in purchases] == [
        ("a", "Milk", 2, LIST),
        ("c", "Bread", 1, LIST),
    ]
    assert purchases[0]["time"] == dt_util.utc_from_timestamp(2000).isoformat()


async def _setup_shopping_list(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain="shopping_list")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _todo(hass: HomeAssistant, service: str, **data) -> None:
    await hass.services.async_call("todo", service, {"entity_id": LIST, **data}, blocking=True)
    await _settle(hass)


async def _settle(hass: HomeAssistant) -> None:
    await hass.async_block_till_done(wait_background_tasks=True)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
    await hass.async_block_till_done(wait_background_tasks=True)


async def _setup_catalog(hass: HomeAssistant, config_dir: Path, lists: list[str]) -> MockConfigEntry:
    write_json(config_dir / "shopping_catalogs" / "supermarket.json", CATALOG)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Supermarket",
        data={CONF_PATH: "shopping_catalogs/supermarket.json"},
        options={CONF_TODO_LISTS: lists},
        unique_id="shopping_catalogs/supermarket.json",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await _settle(hass)
    return entry


async def test_check_offs_are_logged_and_unchecking_undoes_them(
    hass: HomeAssistant, config_dir: Path, hass_ws_client: WebSocketGenerator
) -> None:
    await _setup_shopping_list(hass)
    for name in ("Milk (2)", "Eggs", "Apple"):
        await _todo(hass, "add_item", item=name)
    entry = await _setup_catalog(hass, config_dir, [LIST])
    history = entry.runtime_data.history
    assert history.purchases == []

    await _todo(hass, "update_item", item="Milk (2)", status="completed")
    await _todo(hass, "update_item", item="Eggs", status="completed")
    await _todo(hass, "update_item", item="Eggs", status="needs_action")
    await _todo(hass, "remove_item", item="Apple")

    assert [(p["name"], p["quantity"], p["list"]) for p in history.purchases] == [("Milk", 2, LIST)]
    assert hass.states.get(SENSOR).attributes["purchases_logged"] == 1

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/history", "entity_id": SENSOR})
    result = (await client.receive_json())["result"]
    assert result["lists"] == [LIST]
    assert [p["name"] for p in result["purchases"]] == ["Milk"]


async def test_check_offs_while_unloaded_are_caught_up(hass: HomeAssistant, config_dir: Path) -> None:
    await _setup_shopping_list(hass)
    await _todo(hass, "add_item", item="Bread")
    entry = await _setup_catalog(hass, config_dir, [LIST])

    assert await hass.config_entries.async_unload(entry.entry_id)
    await _todo(hass, "update_item", item="Bread", status="completed")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await _settle(hass)

    assert [p["name"] for p in entry.runtime_data.history.purchases] == ["Bread"]


async def test_catalogs_without_lists_log_nothing(hass: HomeAssistant, config_dir: Path) -> None:
    await _setup_shopping_list(hass)
    await _todo(hass, "add_item", item="Milk")
    entry = await _setup_catalog(hass, config_dir, [])
    await _todo(hass, "update_item", item="Milk", status="completed")
    assert entry.runtime_data.history.purchases == []
    assert hass.states.get(SENSOR).attributes["purchases_logged"] == 0


async def test_options_flow_sets_lists_and_reloads(hass: HomeAssistant, config_dir: Path) -> None:
    await _setup_shopping_list(hass)
    entry = await _setup_catalog(hass, config_dir, [])
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_TODO_LISTS: [LIST]})
    await _settle(hass)
    assert entry.options == {CONF_TODO_LISTS: [LIST]}
    assert entry.runtime_data.history.lists == [LIST]

    await _todo(hass, "add_item", item="Milk")
    await _todo(hass, "update_item", item="Milk", status="completed")
    assert [p["name"] for p in entry.runtime_data.history.purchases] == ["Milk"]
