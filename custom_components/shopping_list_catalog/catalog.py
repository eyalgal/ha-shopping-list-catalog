"""Catalog file storage and validation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.file import write_utf8_file_atomic

from .const import MAX_CATALOG_BYTES

# Mirrors readCatalog() in the card so both sides accept the same files.
STRING_FIELDS = (
    "subtitle",
    "image",
    "image_base",
    "list_prefix",
    "on_icon",
    "off_icon",
    "on_color",
    "off_color",
)

type FileKey = tuple[int, int]


class CatalogError(HomeAssistantError):
    """The catalog content is invalid."""


class CatalogConflictError(HomeAssistantError):
    """The catalog changed since the client loaded it."""


class CatalogPathError(HomeAssistantError):
    """The catalog path is not allowed."""


def validate_catalog(data: Any) -> None:
    """Raise CatalogError unless data is a catalog the card can read."""
    if not isinstance(data, dict):
        raise CatalogError("The catalog must contain categories with product arrays.")
    for category, products in data.items():
        if not isinstance(category, str) or not category.strip():
            raise CatalogError("Catalog categories must have a name.")
        if not isinstance(products, list):
            raise CatalogError(f'Category "{category}" must be an array.')
        for index, product in enumerate(products, 1):
            title = product.get("title") if isinstance(product, dict) else None
            if not isinstance(title, str) or not title.strip():
                raise CatalogError(f'Product {index} in "{category}" needs a title.')
            for field in STRING_FIELDS:
                if product.get(field) is not None and not isinstance(product[field], str):
                    raise CatalogError(f'Product "{title}" has an invalid {field}.')
            types = product.get("types")
            if types is not None and not isinstance(types, (str, list)):
                raise CatalogError(f'Product "{title}" has invalid variants.')


def count_products(data: dict[str, Any]) -> int:
    """Return the number of product entries."""
    return sum(len(products) for products in data.values() if isinstance(products, list))


def encode_catalog(data: dict[str, Any]) -> str:
    """Serialize a catalog the way it is stored on disk."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def resolve_catalog_path(config_dir: str, value: str) -> Path:
    """Resolve a user path, which must be a .json file inside the config directory."""
    root = Path(config_dir).resolve()
    path = Path(value.strip())
    path = (path if path.is_absolute() else root / path).resolve()
    if path.suffix.lower() != ".json" or not path.is_relative_to(root) or path == root:
        raise CatalogPathError(value)
    return path


def relative_catalog_path(config_dir: str, path: Path) -> str:
    """Return the path stored in the config entry."""
    return path.relative_to(Path(config_dir).resolve()).as_posix()


def read_catalog_file(path: Path) -> tuple[FileKey, dict[str, Any], str]:
    """Read and validate a catalog file. Runs in the executor."""
    with path.open("rb") as file:
        stat = os.fstat(file.fileno())
        raw = file.read(MAX_CATALOG_BYTES + 1)
    if len(raw) > MAX_CATALOG_BYTES:
        raise CatalogError("The catalog file is larger than 1 MB.")
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise CatalogError(f"The catalog file is not valid JSON: {err}") from None
    validate_catalog(data)
    return (stat.st_mtime_ns, stat.st_size), data, _revision(raw)


def write_catalog_file(path: Path, text: str, *, backup: bool = True) -> FileKey:
    """Write atomically, keeping the previous version as <file>.bak. Runs in the executor."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        shutil.copy2(path, path.with_name(f"{path.name}.bak"))
    write_utf8_file_atomic(str(path), text, private=False)
    stat = path.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _file_key(path: Path) -> FileKey:
    stat = path.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _revision(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:16]


class CatalogStore:
    """Owns one catalog file and notifies listeners when it changes."""

    def __init__(self, hass: HomeAssistant, path: Path) -> None:
        self.hass = hass
        self.path = path
        self.data: dict[str, Any] | None = None
        self.revision: str | None = None
        self.error: str | None = None
        self._file_key: FileKey | None = None
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call listener after every change; returns a remover."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    async def async_refresh(self, *, force: bool = False) -> None:
        """Reload the file if it changed on disk."""
        async with self._lock:
            await self._async_refresh(force)

    async def async_save(self, data: Any, base_revision: str | None) -> str:
        """Replace the catalog unless it changed since base_revision was read."""
        async with self._lock:
            await self._async_refresh(False)
            if self.error is not None:
                raise CatalogError(f"Fix the catalog file before saving: {self.error}")
            if base_revision != self.revision:
                raise CatalogConflictError("The catalog changed since it was loaded.")
            validate_catalog(data)
            text = encode_catalog(data)
            raw = text.encode()
            if len(raw) > MAX_CATALOG_BYTES:
                raise CatalogError("The catalog is larger than 1 MB.")
            self._file_key = await self.hass.async_add_executor_job(write_catalog_file, self.path, text)
            self.data = json.loads(raw)
            self.revision = _revision(raw)
        self._notify()
        return self.revision

    async def _async_refresh(self, force: bool) -> None:
        try:
            key = await self.hass.async_add_executor_job(_file_key, self.path)
        except OSError:
            key = None
        if not force and key is not None and key == self._file_key:
            return
        try:
            key, data, revision = await self.hass.async_add_executor_job(read_catalog_file, self.path)
        except FileNotFoundError:
            self._set_error(None, "The catalog file was not found.")
        except OSError as err:
            self._set_error(None, f"The catalog file could not be read: {err.strerror or err}")
        except CatalogError as err:
            self._set_error(key, str(err))
        else:
            changed = revision != self.revision or self.error is not None
            self.data, self.revision, self.error, self._file_key = data, revision, None, key
            if changed:
                self._notify()

    def _set_error(self, key: FileKey | None, message: str) -> None:
        changed = message != self.error
        self.data, self.revision, self.error, self._file_key = None, None, message, key
        if changed:
            self._notify()

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()
