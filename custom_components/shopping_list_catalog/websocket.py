"""WebSocket commands used by the card's catalog editor."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .catalog import CatalogConflictError, CatalogError, CatalogStore
from .const import CONF_PATH, DOMAIN
from .matching import add_to_catalog


@callback
def async_register_commands(hass: HomeAssistant) -> None:
    """Register the catalog commands."""
    websocket_api.async_register_command(hass, ws_get)
    websocket_api.async_register_command(hass, ws_save)
    websocket_api.async_register_command(hass, ws_history)
    websocket_api.async_register_command(hass, ws_candidates)
    websocket_api.async_register_command(hass, ws_dismiss)
    websocket_api.async_register_command(hass, ws_restore)
    websocket_api.async_register_command(hass, ws_add_candidate)


def _find(hass: HomeAssistant, entity_id: str) -> tuple[str, Any, str] | None:
    entity = er.async_get(hass).async_get(entity_id)
    if entity is None or entity.platform != DOMAIN or entity.config_entry_id is None:
        return None
    entry = hass.config_entries.async_get_entry(entity.config_entry_id)
    if entry is None or entry.state is not ConfigEntryState.LOADED:
        return None
    return entry.title, entry.runtime_data, entry.data[CONF_PATH]


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/get", vol.Required("entity_id"): cv.entity_id})
@websocket_api.async_response
async def ws_get(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Return a catalog and the revision a save must be based on."""
    found = _find(hass, msg["entity_id"])
    if found is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Catalog not found.")
        return
    title, runtime, path = found
    store: CatalogStore = runtime.catalog
    await store.async_refresh()
    connection.send_result(
        msg["id"],
        {
            "title": title,
            "path": path,
            "catalog": store.data,
            "revision": store.revision,
            "error": store.error,
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/save",
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("catalog"): dict,
        vol.Required("revision"): vol.Any(str, None),
    }
)
@websocket_api.async_response
async def ws_save(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Replace a catalog unless someone else saved it first."""
    found = _find(hass, msg["entity_id"])
    if found is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Catalog not found.")
        return
    store: CatalogStore = found[1].catalog
    try:
        revision = await store.async_save(msg["catalog"], msg["revision"])
    except CatalogConflictError as err:
        connection.send_error(msg["id"], "conflict", str(err))
    except CatalogError as err:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, str(err))
    except OSError as err:
        connection.send_error(
            msg["id"], websocket_api.ERR_HOME_ASSISTANT_ERROR, f"The catalog could not be saved: {err.strerror or err}"
        )
    else:
        connection.send_result(msg["id"], {"revision": revision})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/history", vol.Required("entity_id"): cv.entity_id})
@callback
def ws_history(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Return the purchases logged from a catalog's to-do lists."""
    found = _find(hass, msg["entity_id"])
    if found is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Catalog not found.")
        return
    history = found[1].history
    connection.send_result(msg["id"], {"lists": history.lists, "purchases": history.purchases})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/candidates", vol.Required("entity_id"): cv.entity_id}
)
@callback
def ws_candidates(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Return products bought repeatedly that the catalog does not have yet."""
    found = _find(hass, msg["entity_id"])
    if found is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Catalog not found.")
        return
    connection.send_result(msg["id"], {"candidates": found[1].history.candidates()})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/dismiss", vol.Required("entity_id"): cv.entity_id, vol.Required("key"): str}
)
@callback
def ws_dismiss(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Stop offering a product for the catalog."""
    found = _find(hass, msg["entity_id"])
    if found is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Catalog not found.")
        return
    found[1].history.async_dismiss(msg["key"])
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/restore", vol.Required("entity_id"): cv.entity_id, vol.Required("key"): str}
)
@callback
def ws_restore(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Offer a dismissed product for the catalog again."""
    found = _find(hass, msg["entity_id"])
    if found is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Catalog not found.")
        return
    found[1].history.async_restore(msg["key"])
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/add_candidate",
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("key"): str,
        vol.Optional("category"): str,
    }
)
@websocket_api.async_response
async def ws_add_candidate(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Add a missing product to the catalog: to category, or as its suggested variant without one."""
    found = _find(hass, msg["entity_id"])
    if found is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Catalog not found.")
        return
    runtime = found[1]
    store: CatalogStore = runtime.catalog
    await store.async_refresh()
    candidate = next((item for item in runtime.history.candidates() if item["key"] == msg["key"]), None)
    if candidate is None or store.data is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "That product is no longer missing.")
        return
    try:
        data = add_to_catalog(store.data, candidate, msg.get("category"))
        revision = await store.async_save(data, store.revision)
    except ValueError as err:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, str(err))
    except CatalogConflictError as err:
        connection.send_error(msg["id"], "conflict", str(err))
    except CatalogError as err:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, str(err))
    except OSError as err:
        connection.send_error(
            msg["id"], websocket_api.ERR_HOME_ASSISTANT_ERROR, f"The catalog could not be saved: {err.strerror or err}"
        )
    else:
        connection.send_result(msg["id"], {"revision": revision})
