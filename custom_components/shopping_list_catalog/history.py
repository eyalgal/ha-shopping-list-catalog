"""Purchase history learned from shopping to-do lists."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable
from datetime import timedelta
from functools import partial
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .predict import suggest

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_DELAY = 10
SUGGESTION_REFRESH = timedelta(hours=1)
# Unchecking an item within this many seconds treats the check-off as a mistake.
UNDO_WINDOW = 86400
SHOPPING_LIST_EVENT = "shopping_list_updated"

_QUANTITY = re.compile(r"^(?P<name>.*?)(?: \((?P<qty>\d+)(?: [^()]+)?\))?$")

type Snapshot = dict[str, list[str]]


def parse_summary(summary: str) -> tuple[str, int]:
    """Split "Milk (2)" or "Milk (2 bottles)" into its name and quantity."""
    match = _QUANTITY.match(summary.strip())
    return match["name"].strip(), int(match["qty"]) if match["qty"] else 1


def purchases_from_shopping_list_events(
    rows: Iterable[tuple[float, str | None]], entity_id: str
) -> list[dict[str, Any]]:
    """Rebuild check-offs from recorded shopping_list_updated events."""
    purchases: dict[str, dict[str, Any]] = {}
    for ts, raw in rows:
        try:
            data = json.loads(raw or "{}")
        except ValueError:
            continue
        item = data.get("item") or {}
        uid, summary = item.get("id"), item.get("name")
        if data.get("action") != "update" or not uid or not isinstance(summary, str):
            continue
        if item.get("complete") and uid not in purchases:
            purchases[uid] = _purchase(dt_util.utc_from_timestamp(ts), entity_id, uid, summary)
        elif not item.get("complete") and uid in purchases:
            bought = dt_util.parse_datetime(purchases[uid]["time"])
            if bought and ts - bought.timestamp() <= UNDO_WINDOW:
                del purchases[uid]
    return list(purchases.values())


def _purchase(when, entity_id: str, uid: str, summary: str) -> dict[str, Any]:
    name, quantity = parse_summary(summary)
    return {
        "time": when.isoformat(),
        "list": entity_id,
        "uid": uid,
        "name": name,
        "quantity": quantity,
    }


def _load_shopping_list_events(hass: HomeAssistant) -> list[tuple[float, str | None]]:
    """Read recorded shopping list events. Runs in the recorder's executor."""
    from homeassistant.components.recorder.db_schema import EventData, Events, EventTypes  # noqa: PLC0415
    from homeassistant.helpers.recorder import session_scope  # noqa: PLC0415

    with session_scope(hass=hass, read_only=True) as session:
        return [
            (row.time_fired_ts, row.shared_data)
            for row in session.query(Events.time_fired_ts, EventData.shared_data)
            .join(EventTypes, Events.event_type_id == EventTypes.event_type_id)
            .outerjoin(EventData, Events.data_id == EventData.data_id)
            .filter(EventTypes.event_type == SHOPPING_LIST_EVENT)
            .order_by(Events.time_fired_ts)
        ]


class PurchaseHistory:
    """Records check-offs on the watched to-do lists, kept until the files are deleted."""

    def __init__(self, hass: HomeAssistant, entry_id: str, lists: list[str]) -> None:
        self.hass = hass
        self.lists = lists
        self.purchases: list[dict[str, Any]] = []
        self.suggestions: dict[str, dict[str, Any]] = {}
        self._snapshots: dict[str, Snapshot] = {}
        self._backfilled: list[str] = []
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.history.{entry_id}")
        self._debouncers: dict[str, Debouncer] = {}
        self._listeners: list[Callable[[], None]] = []

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call listener when purchases change; returns a remover."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    async def async_load(self) -> None:
        """Load the saved history."""
        data = await self._store.async_load() or {}
        self.purchases = data.get("purchases", [])
        self._snapshots = data.get("snapshots", {})
        self._backfilled = data.get("backfilled", [])
        self._recompute()

    async def async_backfill(self) -> None:
        """Import check-offs still in the recorder, once per Shopping List list."""
        if "recorder" not in self.hass.config.components:
            return
        registry = er.async_get(self.hass)
        pending = [
            entity_id
            for entity_id in self.lists
            if entity_id not in self._backfilled
            and (entity := registry.async_get(entity_id)) is not None
            and entity.platform == "shopping_list"
        ]
        if not pending:
            return
        from homeassistant.helpers.recorder import get_instance  # noqa: PLC0415

        try:
            rows = await get_instance(self.hass).async_add_executor_job(_load_shopping_list_events, self.hass)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Could not read shopping list history from the recorder", exc_info=True)
            return
        known = {(purchase["list"], purchase["uid"]) for purchase in self.purchases}
        for entity_id in pending:
            imported = [
                purchase
                for purchase in purchases_from_shopping_list_events(rows, entity_id)
                if (entity_id, purchase["uid"]) not in known
            ]
            self.purchases.extend(imported)
            self._backfilled.append(entity_id)
            _LOGGER.info("Imported %d past purchases from %s", len(imported), entity_id)
        self.purchases.sort(key=lambda purchase: purchase["time"])
        self._save()
        self._notify()

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        """Watch the lists and keep suggestions current; returns a function that stops."""
        unsub_refresh = async_track_time_interval(self.hass, self._async_hourly, SUGGESTION_REFRESH)
        if not self.lists:
            return unsub_refresh
        for entity_id in self.lists:
            self._debouncers[entity_id] = Debouncer(
                self.hass,
                _LOGGER,
                cooldown=2,
                immediate=False,
                function=partial(self._async_refresh, entity_id),
            )
        unsub_state = async_track_state_change_event(self.hass, self.lists, self._async_state_changed)
        unsub_started = async_at_started(self.hass, self._async_refresh_all)

        @callback
        def stop() -> None:
            unsub_refresh()
            unsub_state()
            unsub_started()
            for debouncer in self._debouncers.values():
                debouncer.async_shutdown()
            self._debouncers.clear()

        return stop

    async def async_flush(self) -> None:
        """Write pending changes now."""
        await self._store.async_save(self._data())

    async def _async_refresh_all(self, _hass: HomeAssistant) -> None:
        for entity_id in self.lists:
            await self._async_refresh(entity_id)

    @callback
    def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        if debouncer := self._debouncers.get(event.data["entity_id"]):
            self.hass.async_create_task(debouncer.async_call())

    async def _async_refresh(self, entity_id: str) -> None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        try:
            response = await self.hass.services.async_call(
                "todo",
                "get_items",
                {"entity_id": entity_id, "status": ["needs_action", "completed"]},
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as err:
            _LOGGER.debug("Could not read %s: %s", entity_id, err)
            return
        items = (response or {}).get(entity_id, {}).get("items", [])
        current: Snapshot = {
            item["uid"]: [item["summary"], item["status"]] for item in items if item.get("uid") and item.get("summary")
        }
        previous = self._snapshots.get(entity_id)
        changed = False
        if previous is not None:
            now = dt_util.utcnow()
            for uid, (summary, status) in current.items():
                before = previous.get(uid)
                if before is None or before[1] == status:
                    continue
                if status == "completed":
                    self.purchases.append(_purchase(now, entity_id, uid, summary))
                    changed = True
                elif status == "needs_action":
                    changed = self._remove_recent(entity_id, uid, now) or changed
        if current != previous:
            self._snapshots[entity_id] = current
            self._save()
        if changed:
            self._notify()

    def _remove_recent(self, entity_id: str, uid: str, now) -> bool:
        for index in range(len(self.purchases) - 1, -1, -1):
            purchase = self.purchases[index]
            if purchase["list"] == entity_id and purchase["uid"] == uid:
                bought = dt_util.parse_datetime(purchase["time"])
                if bought and (now - bought).total_seconds() <= UNDO_WINDOW:
                    del self.purchases[index]
                    return True
                return False
        return False

    def _data(self) -> dict[str, Any]:
        return {"purchases": self.purchases, "snapshots": self._snapshots, "backfilled": self._backfilled}

    def _save(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY)

    def _recompute(self) -> bool:
        suggestions = suggest(self.purchases, dt_util.now().date())
        changed = suggestions != self.suggestions
        self.suggestions = suggestions
        return changed

    @callback
    def _async_hourly(self, _now) -> None:
        if self._recompute():
            for listener in list(self._listeners):
                listener()

    def _notify(self) -> None:
        self._recompute()
        for listener in list(self._listeners):
            listener()
