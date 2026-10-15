"""Matching purchases to the catalog and finding products to add."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.shopping_list_catalog.const import DOMAIN
from custom_components.shopping_list_catalog.matching import (
    add_to_catalog,
    canonical_purchases,
    catalog_index,
    find_candidates,
    match_key,
    split_quantity,
)

from .test_history import LIST, SENSOR, _settle, _setup_catalog, _setup_shopping_list

CATALOG = {
    "Fruits": [
        {"title": "Grapes", "subtitle": "Green"},
        {"title": "Grapes", "subtitle": "Purple"},
        {"title": "Strawberries"},
        {"title": "Pear"},
    ],
    "Vegetables": [{"title": "Cucumber"}, {"title": "Tomatoes"}],
    "Meat": [{"title": "Chicken", "types": ["Breast", {"name": "Thighs"}]}],
    "Dairy": [{"title": "Cheese", "list_prefix": "Dairy"}],
}


def bought(name: str, days_ago: int, quantity: int = 1) -> dict:
    when = dt_util.now() - timedelta(days=days_ago)
    return {"time": when.isoformat(), "name": name, "quantity": quantity, "list": LIST, "uid": f"{name}{days_ago}"}


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        ("Milk", ("Milk", 1)),
        ("Milk (2)", ("Milk", 2)),
        ("Milk (4 bottles)", ("Milk", 4)),
        ("Milk 2", ("Milk", 2)),
        ("Chicken - Legs 2", ("Chicken - Legs", 2)),
        ("Chuck roast 2 lbs", ("Chuck roast", 2)),
        ("Tomato sauce  in can 3", ("Tomato sauce in can", 3)),
        ("Vitamin B12", ("Vitamin B12", 1)),
        ("2", ("2", 1)),
        ("Milk 2 percent", ("Milk 2 percent", 1)),
    ],
)
def test_split_quantity(summary, expected) -> None:
    assert split_quantity(summary) == expected


def test_match_key_ignores_case_spacing_and_plurals() -> None:
    assert match_key("Pears") == match_key("pear")
    assert match_key("Tomatoes") == match_key("Tomato")
    assert match_key("Strawberries") == match_key("strawberry")
    assert match_key("Snap  Peas") == match_key("snap pea")
    assert match_key("Grapes - Green") != match_key("Grapes Green")


def test_purchases_are_matched_to_catalog_names() -> None:
    names, _products = catalog_index(CATALOG)
    purchases = [
        bought("Pears", 1),
        bought("Milk 2", 1),
        bought("grapes - green", 1),
        bought("Grapes", 1),
        bought("Chicken - Breast 3", 1),
        bought("Cheese", 1),
        bought("Kale", 1, 2),
    ]
    result = canonical_purchases(purchases, names)
    assert [(item["name"], item["quantity"]) for item in result] == [
        ("Pear", 1),
        ("Milk", 2),
        ("Grapes - Green", 1),
        ("Grapes", 1),
        ("Chicken - Breast", 3),
        ("Dairy - Cheese", 1),
        ("Kale", 2),
    ]


def test_candidates_need_repeat_purchases_and_suggest_a_place() -> None:
    purchases = [
        bought("Bamba", 20),
        bought("Bamba", 10),
        bought("Bamba", 3),
        bought("Mini cucumbers", 9),
        bought("Mini cucumbers 2", 2, 2),
        bought("Chicken wings", 8),
        bought("Chicken wings", 1),
        bought("Grapes - Red", 7),
        bought("Grapes - Red", 1),
        bought("Strawberry jam", 6),
        bought("Strawberry jam", 2),
        bought("Pears", 5),
        bought("Pears", 4),
        bought("Saffron", 30),
        bought("Kale", 5),
        bought("Kale", 5),
    ]
    found = {item["name"]: item for item in find_candidates(purchases, CATALOG, set())}
    assert list(found) == ["Bamba", "Chicken wings", "Grapes - Red", "Mini cucumbers", "Strawberry jam"]
    assert found["Bamba"] == {
        "key": "bamba",
        "name": "Bamba",
        "times": 3,
        "last": (dt_util.now() - timedelta(days=3)).date().isoformat(),
        "quantity": 1,
        "variant_of": None,
    }
    assert found["Mini cucumbers"]["variant_of"] == {"category": "Vegetables", "title": "Cucumber", "variant": "Mini"}
    assert found["Chicken wings"]["variant_of"] == {"category": "Meat", "title": "Chicken", "variant": "Wings"}
    assert found["Grapes - Red"]["variant_of"] == {"category": "Fruits", "title": "Grapes", "variant": "Red"}
    assert found["Strawberry jam"]["variant_of"] is None

    assert "Bamba" not in {item["name"] for item in find_candidates(purchases, CATALOG, {"bamba"})}
    with_bamba = {**CATALOG, "Snacks": [{"title": "Bamba"}]}
    assert "Bamba" not in {item["name"] for item in find_candidates(purchases, with_bamba, set())}


async def test_candidates_and_dismissals_over_websocket(
    hass: HomeAssistant,
    config_dir: Path,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
) -> None:
    await _setup_shopping_list(hass)
    entry = await _setup_catalog(hass, config_dir, [LIST])
    history = entry.runtime_data.history
    history.purchases.extend([bought("Bamba", 9), bought("Bamba", 2), bought("Bananas 2", 14), bought("Banana", 7)])
    history._notify()
    await _settle(hass)
    assert hass.states.get(SENSOR).attributes["suggestions"]["Banana"]["quantity"] == 2

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/candidates", "entity_id": SENSOR})
    response = await client.receive_json()
    assert [item["name"] for item in response["result"]["candidates"]] == ["Bamba"]

    reader = await hass_ws_client(hass, hass_read_only_access_token)
    await reader.send_json_auto_id({"type": f"{DOMAIN}/dismiss", "entity_id": SENSOR, "key": "bamba"})
    assert (await reader.receive_json())["error"]["code"] == "unauthorized"

    await client.send_json_auto_id({"type": f"{DOMAIN}/dismiss", "entity_id": SENSOR, "key": "bamba"})
    assert (await client.receive_json())["success"]
    await client.send_json_auto_id({"type": f"{DOMAIN}/candidates", "entity_id": SENSOR})
    assert (await client.receive_json())["result"]["candidates"] == []

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.history.dismissed == ["bamba"]


async def test_catalog_edits_update_suggestions(hass: HomeAssistant, config_dir: Path) -> None:
    await _setup_shopping_list(hass)
    entry = await _setup_catalog(hass, config_dir, [LIST])
    history = entry.runtime_data.history
    history.purchases.extend([bought("Kiwis", 14), bought("Kiwis", 7)])
    history._notify()
    await _settle(hass)
    assert "Kiwis" in hass.states.get(SENSOR).attributes["suggestions"]

    store = entry.runtime_data.catalog
    await store.async_save({**store.data, "Fruits": [*store.data["Fruits"], {"title": "Kiwi"}]}, store.revision)
    await _settle(hass)
    suggestions = hass.states.get(SENSOR).attributes["suggestions"]
    assert "Kiwi" in suggestions
    assert "Kiwis" not in suggestions


def test_candidates_are_added_as_products_or_variants() -> None:
    def candidate(name, place=None):
        return {"key": match_key(name), "name": name, "variant_of": place}

    added = add_to_catalog(CATALOG, candidate("Bamba"), "Dairy")
    assert added["Dairy"][-1] == {"title": "Bamba"}
    assert CATALOG["Dairy"] == [{"title": "Cheese", "list_prefix": "Dairy"}]

    grapes = add_to_catalog(
        CATALOG, candidate("Grapes - Red", {"category": "Fruits", "title": "Grapes", "variant": "Red"}), None
    )
    assert [entry.get("subtitle") for entry in grapes["Fruits"][:3]] == ["Green", "Purple", "Red"]

    wings = add_to_catalog(
        CATALOG, candidate("Chicken wings", {"category": "Meat", "title": "chicken", "variant": "Wings"}), None
    )
    assert wings["Meat"] == [{"title": "Chicken", "types": ["Breast", {"name": "Thighs"}, "Wings"]}]

    mini = add_to_catalog(
        CATALOG, candidate("Mini cucumbers", {"category": "Vegetables", "title": "Cucumber", "variant": "Mini"}), None
    )
    assert mini["Vegetables"][0] == {"title": "Cucumber", "types": ["Mini"]}

    with pytest.raises(ValueError, match='no "Snacks" category'):
        add_to_catalog(CATALOG, candidate("Bamba"), "Snacks")
    with pytest.raises(ValueError, match="can't be added as a variant"):
        add_to_catalog(CATALOG, candidate("Bamba"), None)
    with pytest.raises(ValueError, match="no longer in the catalog"):
        add_to_catalog(
            CATALOG, candidate("Kiwi gold", {"category": "Fruits", "title": "Kiwi", "variant": "Gold"}), None
        )


async def test_missing_products_on_the_sensor_can_be_added_from_the_card(
    hass: HomeAssistant,
    config_dir: Path,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
) -> None:
    await _setup_shopping_list(hass)
    entry = await _setup_catalog(hass, config_dir, [LIST])
    history = entry.runtime_data.history
    history.purchases.extend(
        [
            bought("Bamba", 9),
            bought("Bamba", 2),
            bought("Apple - Fuji", 9),
            bought("Apple - Fuji 2", 3),
            bought("Kale", 8),
            bought("Kale", 1),
        ]
    )
    history._notify()
    await _settle(hass)
    missing = hass.states.get(SENSOR).attributes["missing_products"]
    assert [item["name"] for item in missing] == ["Apple - Fuji", "Bamba", "Kale"]
    assert missing[0]["variant_of"] == {"category": "Fruits", "title": "Apple", "variant": "Fuji"}

    reader = await hass_ws_client(hass, hass_read_only_access_token)
    await reader.send_json_auto_id(
        {"type": f"{DOMAIN}/add_candidate", "entity_id": SENSOR, "key": "bamba", "category": "Fruits"}
    )
    assert (await reader.receive_json())["error"]["code"] == "unauthorized"

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/add_candidate", "entity_id": SENSOR, "key": "bamba", "category": "Fruits"}
    )
    assert (await client.receive_json())["success"]
    await client.send_json_auto_id({"type": f"{DOMAIN}/add_candidate", "entity_id": SENSOR, "key": missing[0]["key"]})
    assert (await client.receive_json())["success"]
    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/add_candidate", "entity_id": SENSOR, "key": "bamba", "category": "Fruits"}
    )
    assert (await client.receive_json())["error"]["code"] == "not_found"
    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/add_candidate", "entity_id": SENSOR, "key": "kale", "category": "Nope"}
    )
    assert 'no "Nope" category' in (await client.receive_json())["error"]["message"]
    await _settle(hass)

    state = hass.states.get(SENSOR)
    fruits = state.attributes["catalog"]["Fruits"]
    assert fruits[0]["types"][-1] == "Fuji"
    assert fruits[-1] == {"title": "Bamba"}
    assert [item["name"] for item in state.attributes["missing_products"]] == ["Kale"]

    await client.send_json_auto_id({"type": f"{DOMAIN}/dismiss", "entity_id": SENSOR, "key": "kale"})
    assert (await client.receive_json())["success"]
    await _settle(hass)
    assert hass.states.get(SENSOR).attributes["missing_products"] == []

    await reader.send_json_auto_id({"type": f"{DOMAIN}/restore", "entity_id": SENSOR, "key": "kale"})
    assert (await reader.receive_json())["error"]["code"] == "unauthorized"
    await client.send_json_auto_id({"type": f"{DOMAIN}/restore", "entity_id": SENSOR, "key": "kale"})
    assert (await client.receive_json())["success"]
    await _settle(hass)
    assert [item["name"] for item in hass.states.get(SENSOR).attributes["missing_products"]] == ["Kale"]
    assert entry.runtime_data.history.dismissed == []
