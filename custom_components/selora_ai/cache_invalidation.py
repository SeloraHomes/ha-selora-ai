"""Keep Selora's learned caches in sync with the entity and device registries.

When a device, entity, or whole integration is removed from Home Assistant,
stale references linger in two places:

- the :class:`PatternStore` (state history, detected patterns, and pending
  suggestions), and
- the in-memory suggestion caches in ``hass.data`` (``proactive_suggestions``
  and ``latest_suggestions``).

Left alone, Selora keeps detecting patterns on — and proposing (or generating,
via chat) automations that target — devices that no longer exist. HA fires
``EVENT_ENTITY_REGISTRY_UPDATED`` (action ``remove``) for every entity that
goes away, including the cascade when a device or config entry is deleted. We
also listen to ``EVENT_DEVICE_REGISTRY_UPDATED`` so a suggestion that targets a
device by ``device_id`` alone (no ``entity_id``) is caught too. Removals are
debounced so a bulk teardown collapses into one store write.

The Settings "Clear learned data" action reuses :func:`async_clear_all_caches`
as an explicit break-glass reset.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN, SIGNAL_PROACTIVE_SUGGESTIONS, STALE_CACHE_PURGE_DELAY
from .pattern_store import _suggestion_reference_ids

if TYPE_CHECKING:
    from homeassistant.core import Event

    from .pattern_store import PatternStore

_LOGGER = logging.getLogger(__name__)


def _prune_memory_suggestions(hass: HomeAssistant, reference_ids: set[str]) -> int:
    """Drop in-memory suggestions referencing any removed *reference_ids*.

    ``reference_ids`` mixes removed entity_ids and device_ids. Covers both
    cache shapes: pattern-based ``proactive_suggestions`` (payload under
    ``automation_data``) and collector ``latest_suggestions`` (the item is
    itself the automation dict). Matches by entity_id **and** device_id so
    device-only automations are pruned too. Returns the number removed.
    """
    domain_data = hass.data.get(DOMAIN)
    if not domain_data:
        return 0

    removed = 0
    for cache_key in ("proactive_suggestions", "latest_suggestions"):
        cache = domain_data.get(cache_key)
        if not cache:
            continue
        kept = [s for s in cache if not reference_ids.intersection(_suggestion_reference_ids(s))]
        removed += len(cache) - len(kept)
        domain_data[cache_key] = kept
    return removed


async def async_invalidate_references(
    hass: HomeAssistant,
    pattern_store: PatternStore | None,
    reference_ids: set[str],
) -> dict[str, int]:
    """Purge removed *reference_ids* (entity + device ids) from every cache.

    ``pattern_store`` is ``None`` when pattern detection is disabled — there is
    no persisted learned data, but the collector can still populate the
    in-memory suggestion caches, so those are always pruned.
    """
    if pattern_store is not None:
        counts = await pattern_store.purge_stale_references(reference_ids)
    else:
        counts = {"history": 0, "patterns": 0, "suggestions": 0}
    counts["memory_suggestions"] = _prune_memory_suggestions(hass, reference_ids)
    if any(counts.values()):
        async_dispatcher_send(hass, SIGNAL_PROACTIVE_SUGGESTIONS)
    return counts


async def async_clear_all_caches(
    hass: HomeAssistant,
    pattern_store: PatternStore | None,
) -> dict[str, int]:
    """Break-glass reset — wipe learned data and the in-memory suggestions.

    ``pattern_store`` is ``None`` when pattern detection is disabled: there is
    no persisted learned data to wipe, but the collector can still populate the
    in-memory ``latest_suggestions``, so we always clear those.
    """
    if pattern_store is not None:
        counts = await pattern_store.clear_learned_data()
    else:
        counts = {"history": 0, "patterns": 0, "suggestions": 0}
    domain_data = hass.data.get(DOMAIN, {})
    counts["memory_suggestions"] = len(domain_data.get("proactive_suggestions", [])) + len(
        domain_data.get("latest_suggestions", [])
    )
    domain_data["proactive_suggestions"] = []
    domain_data["latest_suggestions"] = []
    async_dispatcher_send(hass, SIGNAL_PROACTIVE_SUGGESTIONS)
    return counts


class StaleCacheInvalidator:
    """Listens for registry removals and purges stale cache references.

    Tracks both entity-registry and device-registry removals: a device-only
    automation carries a ``device_id`` and no ``entity_id``, so deleting the
    device would otherwise leave its suggestion behind. Removals are
    accumulated and flushed after ``STALE_CACHE_PURGE_DELAY`` so a bulk
    teardown (removing an integration) results in a single store write.
    """

    def __init__(self, hass: HomeAssistant, pattern_store: PatternStore | None) -> None:
        self._hass = hass
        self._pattern_store = pattern_store
        self._pending: set[str] = set()
        self._unsubs: list[CALLBACK_TYPE] = []
        self._cancel_flush: CALLBACK_TYPE | None = None

    @callback
    def async_start(self) -> None:
        """Begin listening for entity- and device-registry removals."""
        self._unsubs.append(
            self._hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._handle_entity_registry_update
            )
        )
        self._unsubs.append(
            self._hass.bus.async_listen(
                dr.EVENT_DEVICE_REGISTRY_UPDATED, self._handle_device_registry_update
            )
        )

    @callback
    def async_stop(self) -> None:
        """Stop listening and cancel any pending flush."""
        while self._unsubs:
            self._unsubs.pop()()
        if self._cancel_flush is not None:
            self._cancel_flush()
            self._cancel_flush = None
        self._pending.clear()

    @callback
    def _handle_entity_registry_update(self, event: Event) -> None:
        action = event.data.get("action")
        stale_id: str | None = None
        if action == "remove":
            stale_id = event.data.get("entity_id")
        elif action == "update":
            # A rename leaves the old entity_id dangling in stored payloads.
            old_id = event.data.get("old_entity_id")
            if old_id and old_id != event.data.get("entity_id"):
                stale_id = old_id
        self._queue(stale_id)

    @callback
    def _handle_device_registry_update(self, event: Event) -> None:
        # Device removals cascade into entity removals, but a device-only
        # automation references the device_id directly, so queue that too.
        if event.data.get("action") == "remove":
            self._queue(event.data.get("device_id"))

    @callback
    def _queue(self, stale_id: str | None) -> None:
        if not stale_id:
            return
        self._pending.add(stale_id)
        if self._cancel_flush is None:
            self._cancel_flush = async_call_later(
                self._hass, STALE_CACHE_PURGE_DELAY, self._async_flush
            )

    async def _async_flush(self, _now: Any = None) -> None:
        self._cancel_flush = None
        reference_ids = self._pending
        self._pending = set()
        if not reference_ids:
            return
        try:
            await async_invalidate_references(self._hass, self._pattern_store, reference_ids)
        except Exception:  # noqa: BLE001 — cache cleanup must never break HA
            _LOGGER.exception("Failed to purge stale cache for removed references")
