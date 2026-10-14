"""Importing past check-offs from the recorder."""

from __future__ import annotations

from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from .test_history import LIST, _settle, _setup_catalog, _setup_shopping_list, _todo


@pytest.fixture(autouse=True)
def mock_recorder_before_hass(async_test_recorder) -> None:
    """Set up the recorder database before Home Assistant starts."""


async def test_recorder_history_is_imported_once(recorder_mock, hass: HomeAssistant, config_dir: Path) -> None:
    await _setup_shopping_list(hass)
    for name in ("Milk (2)", "Eggs", "Bread"):
        await _todo(hass, "add_item", item=name)
    await _todo(hass, "update_item", item="Milk (2)", status="completed")
    await _todo(hass, "update_item", item="Eggs", status="completed")
    await _todo(hass, "update_item", item="Eggs", status="needs_action")
    await async_wait_recording_done(hass)

    entry = await _setup_catalog(hass, config_dir, [LIST])
    assert [(p["name"], p["quantity"]) for p in entry.runtime_data.history.purchases] == [("Milk", 2)]

    await _todo(hass, "update_item", item="Bread", status="completed")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await _settle(hass)
    assert [p["name"] for p in entry.runtime_data.history.purchases] == ["Milk", "Bread"]
