"""Catalog validation and file storage."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant

from custom_components.shopping_list_catalog.catalog import (
    CatalogConflictError,
    CatalogError,
    CatalogPathError,
    CatalogStore,
    resolve_catalog_path,
    validate_catalog,
)

from .conftest import CATALOG, write_json


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "categories with product arrays"),
        ({" ": []}, "must have a name"),
        ({"Fruit": {}}, 'Category "Fruit" must be an array'),
        ({"Fruit": ["Apple"]}, 'Product 1 in "Fruit" needs a title'),
        ({"Fruit": [{"title": " "}]}, "needs a title"),
        ({"Fruit": [{"title": "Apple", "image": 3}]}, 'Product "Apple" has an invalid image'),
        ({"Fruit": [{"title": "Apple", "types": {"a": 1}}]}, "invalid variants"),
    ],
)
def test_validate_rejects_what_the_card_rejects(data, message) -> None:
    with pytest.raises(CatalogError, match=message):
        validate_catalog(data)


@pytest.mark.parametrize(
    "data",
    [
        {},
        CATALOG,
        {"Fruit": [{"title": "Apple", "types": "Gala, Fuji", "subtitle": None, "quantity_max": 5}]},
    ],
)
def test_validate_accepts_what_the_card_accepts(data) -> None:
    validate_catalog(data)


@pytest.mark.parametrize("value", ["../outside.json", "/etc/catalog.json", "catalog.txt", "."])
def test_paths_must_be_json_inside_config(tmp_path: Path, value: str) -> None:
    with pytest.raises(CatalogPathError):
        resolve_catalog_path(str(tmp_path), value)


def test_absolute_path_inside_config_is_allowed(tmp_path: Path) -> None:
    assert resolve_catalog_path(str(tmp_path), str(tmp_path / "a" / "b.json")) == (tmp_path / "a" / "b.json").resolve()


async def test_save_writes_atomically_keeps_backup_and_unknown_fields(hass: HomeAssistant, tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    write_json(path, CATALOG)
    original = path.read_text()
    store = CatalogStore(hass, path)
    await store.async_refresh(force=True)
    calls = []
    store.async_add_listener(lambda: calls.append(1))

    updated = {**store.data, "Bakery": [{"title": "Bread"}]}
    revision = await store.async_save(updated, store.revision)

    assert json.loads(path.read_text()) == updated
    assert path.read_text().endswith("\n")
    assert (tmp_path / "catalog.json.bak").read_text() == original
    assert store.data["Fruits"][0]["custom"] == 1
    assert store.data["Fruits"][0]["types"][0] == {"name": "Gala", "image": "/local/gala.png"}
    assert revision == store.revision
    assert calls == [1]
    assert not [name for name in os.listdir(tmp_path) if name.endswith(".tmp")]


async def test_save_rejects_stale_revision(hass: HomeAssistant, tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    write_json(path, CATALOG)
    store = CatalogStore(hass, path)
    await store.async_refresh(force=True)
    first = store.revision
    await store.async_save({"A": []}, first)

    with pytest.raises(CatalogConflictError):
        await store.async_save({"B": []}, first)
    assert json.loads(path.read_text()) == {"A": []}


async def test_save_detects_manual_edit(hass: HomeAssistant, tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    write_json(path, CATALOG)
    store = CatalogStore(hass, path)
    await store.async_refresh(force=True)
    loaded = store.revision
    write_json(path, {"Edited": [{"title": "By hand"}]})

    with pytest.raises(CatalogConflictError):
        await store.async_save({"A": []}, loaded)
    assert store.data == {"Edited": [{"title": "By hand"}]}


async def test_save_rejects_invalid_catalog_without_writing(hass: HomeAssistant, tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    write_json(path, CATALOG)
    store = CatalogStore(hass, path)
    await store.async_refresh(force=True)

    with pytest.raises(CatalogError, match="needs a title"):
        await store.async_save({"A": [{}]}, store.revision)
    assert json.loads(path.read_text()) == CATALOG
    assert not (tmp_path / "catalog.json.bak").exists()


async def test_invalid_or_missing_file_sets_error_and_blocks_save(hass: HomeAssistant, tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    path.write_text("{ not json")
    store = CatalogStore(hass, path)
    await store.async_refresh(force=True)
    assert store.data is None
    assert "not valid JSON" in store.error
    with pytest.raises(CatalogError, match="Fix the catalog file"):
        await store.async_save({}, None)
    assert path.read_text() == "{ not json"

    path.unlink()
    await store.async_refresh()
    assert store.error == "The catalog file was not found."

    write_json(path, CATALOG)
    await store.async_refresh()
    assert store.error is None
    assert store.data == CATALOG


async def test_oversized_file_is_rejected(hass: HomeAssistant, tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    write_json(path, {"A": [{"title": "x" * 1_000_001}]})
    store = CatalogStore(hass, path)
    await store.async_refresh(force=True)
    assert "larger than 1 MB" in store.error
