"""LLM token-usage tracking and emission.

A ``UsageTracker`` owns:
- A ContextVar buffer of usage events the provider reports during a call.
- A flush path that fans events out to the dispatcher signal (sensors),
  the HA event bus (Logbook), an in-memory ring buffer (panel breakdown),
  and the persistent ``usage_store`` (long-term analytics).
- A ``scope()`` context manager that callers wrap around each LLM call to
  isolate its events and tag the call kind for the provider.

Pricing overrides are stored here too so cost estimation stays close to
the buffer that emits the events.
"""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.dispatcher import async_dispatcher_send

from ..const import (
    DOMAIN,
    EVENT_LLM_USAGE,
    LLM_PROVIDER_SELORA_CLOUD,
    SIGNAL_LLM_USAGE,
    estimate_llm_cost_usd,
)
from ..types import LLMUsageEvent, LLMUsageInfo

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..providers.base import LLMProvider

_LOGGER = logging.getLogger(__name__)

# How many recent usage events to keep in the in-memory ring buffer that
# powers the panel's "Where tokens go" breakdown. Chosen so even a chatty
# user has at least a day of context, while bounding memory.
LLM_USAGE_BUFFER_SIZE = 500


class UsageTracker:
    """Buffers provider-reported usage events and emits them on flush."""

    def __init__(
        self,
        hass: HomeAssistant,
        provider: LLMProvider,
        pricing_overrides: dict[str, dict[str, tuple[float, float] | list[float]]] | None = None,
    ) -> None:
        self._hass = hass
        self._provider = provider
        self._pricing_overrides = pricing_overrides or {}
        self.pending: ContextVar[list[tuple[str, str, LLMUsageInfo]]] = ContextVar(
            f"selora_pending_usage_{id(self)}"
        )
        self._provider.set_usage_callback(self._on_provider_usage)
        # Shared in-memory ring buffer of recent enriched events, used by
        # the panel's "Where tokens go" breakdown. Keyed in hass.data so
        # multiple LLMClient instances (e.g. main + device manager) share
        # one history.
        hass.data.setdefault(DOMAIN, {}).setdefault(
            "llm_usage_events",
            deque(maxlen=LLM_USAGE_BUFFER_SIZE),
        )

    def set_pricing_overrides(
        self,
        overrides: dict[str, dict[str, tuple[float, float] | list[float]]] | None,
    ) -> None:
        """Replace the in-memory pricing overrides used by the cost estimator."""
        self._pricing_overrides = overrides or {}

    def _on_provider_usage(
        self,
        provider_type: str,
        model: str,
        usage: LLMUsageInfo,
    ) -> None:
        """Buffer usage from the provider; ``flush`` emits it later."""
        buf = self.pending.get(None)
        if buf is not None:
            buf.append((provider_type, model, usage))

    def flush(self, kind: str, *, intent: str | None = None) -> None:
        """Emit all pending usage events with the given kind/intent.

        Called by each public method at the point where we know what the
        call was for. Fires the dispatcher signal (sensors), the HA event
        (Logbook), and appends to the ring buffer (panel breakdown).
        """
        buf = self.pending.get(None)
        if not buf:
            return
        pending = list(buf)
        buf.clear()
        buffer: deque[LLMUsageEvent] = self._hass.data[DOMAIN]["llm_usage_events"]
        for provider_type, model, usage in pending:
            # Selora AI Cloud usage is metered by Selora Connect (the SaaS
            # backend bills the user directly). Skip recording locally so we
            # don't double-count it in sensors, the ring buffer, or the
            # persistent usage store.
            if provider_type == LLM_PROVIDER_SELORA_CLOUD:
                _LOGGER.debug("Skipping local usage record for Selora Cloud (tracked in Connect)")
                continue
            input_tokens = int(usage.get("input_tokens", 0))
            output_tokens = int(usage.get("output_tokens", 0))
            cost_usd = estimate_llm_cost_usd(
                provider_type,
                model,
                input_tokens,
                output_tokens,
                overrides=self._pricing_overrides,
            )
            event: LLMUsageEvent = {
                "timestamp": datetime.now(UTC).isoformat(),
                "kind": kind,
                "provider": provider_type,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost_usd,
            }
            if intent:
                event["intent"] = intent
            if "cache_creation_input_tokens" in usage:
                event["cache_creation_input_tokens"] = usage["cache_creation_input_tokens"]
            if "cache_read_input_tokens" in usage:
                event["cache_read_input_tokens"] = usage["cache_read_input_tokens"]
            buffer.append(event)
            async_dispatcher_send(self._hass, SIGNAL_LLM_USAGE, event)
            self._hass.bus.async_fire(EVENT_LLM_USAGE, event)
            self._record_in_store(event)

    def drop(self) -> None:
        """Discard pending usage (e.g. when a call errored before completion)."""
        buf = self.pending.get(None)
        if buf is not None:
            buf.clear()

    def _record_in_store(self, event: LLMUsageEvent) -> None:
        """Schedule a persistent record of one event without blocking flush.

        The store write is fire-and-forget — failures are logged but never
        propagate, since telemetry must not break the user-facing call path.
        """
        from ..usage_store import get_usage_store  # noqa: PLC0415

        store = get_usage_store(self._hass)

        async def _record() -> None:
            try:
                await store.record(event)
            except Exception:  # noqa: BLE001 — telemetry must not raise
                _LOGGER.exception("Failed to persist LLM usage event")

        self._hass.async_create_task(_record())

    @contextmanager
    def scope(self, kind: str | None = None) -> Any:
        """Create an isolated usage buffer for the current call.

        ``kind`` mirrors the value passed later to ``flush`` and is
        forwarded to the provider via ``set_call_kind`` so backends that
        route to specialist models (e.g. SeloraLocal LoRAs) can pick the
        right one for this call's purpose.
        """
        token: Token[list[tuple[str, str, LLMUsageInfo]]] = self.pending.set([])
        self._provider.set_call_kind(kind)
        try:
            yield
        finally:
            self.pending.reset(token)
            self._provider.set_call_kind(None)
