"""Suggestions from purchase history."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.shopping_list_catalog.predict import suggest

from .test_history import LIST, _settle, _setup_catalog, _setup_shopping_list

TODAY = date(2026, 10, 12)


def bought(name: str, days_ago: int, quantity: int = 1) -> dict:
    when = datetime.combine(TODAY - timedelta(days=days_ago), datetime.min.time().replace(hour=10))
    return {
        "time": dt_util.as_utc(when.replace(tzinfo=dt_util.get_default_time_zone())).isoformat(),
        "name": name,
        "quantity": quantity,
        "list": LIST,
        "uid": f"{name}{days_ago}",
    }


def test_weekly_products_are_suggested_with_their_usual_quantity() -> None:
    history = [bought("Milk", 21, 2), bought("Milk", 14, 2), bought("Milk", 7, 1)]
    assert suggest(history, TODAY) == {"Milk": {"last": "2026-10-05", "since": 7, "every": 7, "quantity": 2}}


def test_not_suggested_right_after_buying_or_after_a_single_purchase() -> None:
    history = [bought("Eggs", 9), bought("Eggs", 2), bought("Saffron", 30)]
    assert suggest(history, TODAY) == {}


def test_slow_products_wait_for_most_of_their_interval() -> None:
    history = [bought("Rice", 60), bought("Rice", 30)]
    assert suggest(history, TODAY + timedelta(days=-7)) == {}
    assert "Rice" in suggest(history, TODAY)


def test_recent_regulars_are_suggested_before_their_interval_passes() -> None:
    history = [bought("Grapes", 40), bought("Grapes", 13), bought("Grapes", 5)]
    assert suggest(history, TODAY)["Grapes"]["since"] == 5


def test_names_group_case_insensitively_and_most_overdue_comes_first() -> None:
    history = [
        bought("bananas", 20),
        bought("Bananas", 10),
        bought("Pears", 14),
        bought("Pears", 7),
        bought("Pears", 7),
    ]
    result = suggest(history, TODAY)
    assert list(result) == ["Bananas", "Pears"]
    assert result["Bananas"]["since"] == 10


async def test_sensors_expose_suggestions(hass: HomeAssistant, config_dir: Path) -> None:
    await _setup_shopping_list(hass)
    entry = await _setup_catalog(hass, config_dir, [LIST])
    history = entry.runtime_data.history
    history.purchases.extend(
        [
            {**bought("Milk", 14), "time": (dt_util.utcnow() - timedelta(days=14)).isoformat()},
            {**bought("Milk", 7), "time": (dt_util.utcnow() - timedelta(days=7)).isoformat()},
        ]
    )
    history._notify()
    await _settle(hass)

    catalog = hass.states.get("sensor.shopping_catalog_supermarket")
    assert catalog.attributes["suggestions"]["Milk"]["every"] == 7
    suggested = hass.states.get("sensor.shopping_catalog_supermarket_suggested")
    assert suggested.state == "1"
    assert suggested.attributes["items"][0]["name"] == "Milk"
    assert suggested.attributes["friendly_name"] == "Supermarket suggestions"
