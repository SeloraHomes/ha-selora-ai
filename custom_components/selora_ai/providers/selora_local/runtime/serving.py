"""Selora AI Local — serving + LoRA-activation concern (HTTP request path)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import re
import time
from typing import Any

import aiohttp

from ....const import (
    CONTEXT_WINDOW_PROBE_TTL_S,
    HEALTH_CHECK_TIMEOUT,
    SELORA_LOCAL_BACKEND_OLLAMA,
    SELORA_LOCAL_DEFAULT_INTENT,
    SELORA_LOCAL_KIND_TO_INTENT,
    SELORA_LOCAL_LORA_FILENAME_KEYWORDS,
)
from ...base import _positive_int

_LOGGER = logging.getLogger(__name__)

# Selora AI Local — specialist intents we pre-warm at startup.
_SELORA_LOCAL_PREWARM_KINDS: tuple[str, ...] = (
    "chat_command",
    "chat_automation",
    "chat_answer",
    "chat_clarification",
    "chat_utilities",
)

# Selora AI Local — retry schedule for GET /lora-adapters when the hub
# is not serving yet (still booting, transient network blip). Without a
# retry at all the prewarm task's first call would lock in "no LoRA
# routing" for the whole HA session because the hub wasn't ready.
#
# The schedule escalates rather than using one flat delay, because one
# number was doing two incompatible jobs: about right for a hub that is
# genuinely down, and far too long for the ordinary case, which is Home
# Assistant finishing its restart a few seconds before the hub finishes
# loading its model. There, a flat 30s turns a few seconds of
# unreadiness into half a minute in which every user request fails
# outright. Starting short and escalating to the same ceiling costs the
# common case one slightly slow request and still settles the down-hub
# case at one probe every 30s.
_SELORA_LOCAL_DISCOVERY_BACKOFF_MIN_S = 0.5
_SELORA_LOCAL_DISCOVERY_BACKOFF_MAX_S = 30.0
# How long a user's request may wait for a retry instead of failing.
# Past this the hub is not "seconds from ready" and failing fast is the
# honest answer.
_SELORA_LOCAL_DISCOVERY_WAIT_S = 2.0
# Slept on top of the remaining window before re-probing, so the probe
# is never refused for waking a hair early: asyncio may fire a timer
# within one clock resolution of its deadline, and the re-probe is
# gated on that same deadline having passed.
_SELORA_LOCAL_DISCOVERY_WAKE_MARGIN_S = 0.01
# Per-probe HTTP timeout for the two discovery GETs. Deliberately far
# below HEALTH_CHECK_TIMEOUT: they read metadata the hub already holds
# in memory, so a healthy hub answers in milliseconds and a hub that is
# not listening yet refuses the connection outright. The only case the
# timeout covers is a socket that accepts and then goes quiet, and a
# request waiting out the boot race must not be stuck behind that for a
# quarter of a minute.
_SELORA_LOCAL_DISCOVERY_PROBE_TIMEOUT_S = 5.0


def _log_at(delay: float, escalated: Callable[..., None]) -> Callable[..., None]:
    """Pick a log level for a discovery retry from how long it armed for.

    A short step is the restart race and settles by itself; a long one means
    the hub is actually unwell and the line is worth someone's attention.
    """
    return _LOGGER.debug if delay < _SELORA_LOCAL_DISCOVERY_WAIT_S else escalated


class _SeloraLocalActivationError(ConnectionError):
    """Raised when /lora-adapters refuses to activate the target slot."""


# Trailing UNCLOSED ``{domain.partial_slug`` placeholder — the answer specialist's reply was clipped by max_tokens mid-placeholder ("The kitchen plug is {switch.kitchen_appliance_pl").
_SELORA_LOCAL_TRUNC_PLACEHOLDER_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\.([a-z0-9_]*)$")


class _ServingMixin:
    """Serving + LoRA-activation concern for SeloraLocalProvider."""

    # LoRA-slot discovery + activation

    def _schedule_discovery_retry(self) -> float:
        """Arm the next discovery attempt, escalate the delay, return it."""
        delay = self._discovery_backoff_s
        self._discovery_retry_after = time.monotonic() + delay
        self._discovery_armed_delay = delay
        self._discovery_backoff_s = min(delay * 2, _SELORA_LOCAL_DISCOVERY_BACKOFF_MAX_S)
        return delay

    async def _settle_discovery(self) -> None:
        """Give a hub that is seconds from ready one bounded chance.

        Startup race: Home Assistant came back before the hub did. While
        the schedule is still on one of its short steps the hub is
        plausibly seconds from ready, so wait that window out once and
        re-probe rather than failing the user's first request — a
        slightly slow answer beats an error they have to retry by hand.

        The test is the armed STEP, not the time left in the window.
        Those differ: a request landing in the last second of a
        fully-escalated 30s window has a short time remaining, but the
        hub has by then failed every probe for half a minute. Waiting
        there buys nothing and only delays the error. Past the cap we
        fail fast at any point in the window.

        Callers must run this BEFORE taking ``_request_lock``. The wait
        settles discovery and touches no LoRA slot, so it does not need
        that lock — and holding it across the sleep would make
        concurrent requests queue up and pay the window one after
        another instead of all sharing the one window they are actually
        waiting on. ``_slot_lock`` inside ``_ensure_lora_discovery``
        still collapses the wake-up into a single probe.
        """
        await self._ensure_lora_discovery()
        if self._lora_slots is not None:
            return
        if self._prewarming.get():
            # Pre-warm is fire-and-forget at setup: nobody is waiting on
            # its answer, so there is nothing for a wait to rescue. What
            # it would do is spend the schedule's short steps — the ones
            # a user's first request needs — before anyone has typed.
            return
        remaining = self._discovery_retry_after - time.monotonic()
        if (
            self._discovery_armed_delay <= _SELORA_LOCAL_DISCOVERY_WAIT_S
            and 0.0 < remaining <= _SELORA_LOCAL_DISCOVERY_WAIT_S
        ):
            # Sleeping past the deadline is what lets the re-probe go
            # through: _ensure_lora_discovery gates on that same
            # deadline, so nothing has to reach in and clear it.
            await asyncio.sleep(remaining + _SELORA_LOCAL_DISCOVERY_WAKE_MARGIN_S)
            await self._ensure_lora_discovery()

    async def _ensure_lora_discovery(self) -> None:
        """GET /v1/models + GET /lora-adapters discovery, cached after success.

        Populates ``self._base_model_id``, ``self._n_slots``, and
        ``self._lora_slots`` (intent → slot id). Successful discovery is
        cached for the lifetime of the provider. A transient failure
        (hub still booting, network blip) leaves ``_lora_slots`` unset
        and arms a short backoff so the next request retries — without
        this, a single startup-race failure would disable LoRA routing
        until HA restarts. Inside the backoff window the call is a
        no-op so the hub isn't hammered while it's down.
        """
        if self._lora_slots is not None:
            return
        if time.monotonic() < self._discovery_retry_after:
            return
        async with self._slot_lock:
            if self._lora_slots is not None:
                return
            if time.monotonic() < self._discovery_retry_after:
                return
            session = self._get_session()
            timeout = aiohttp.ClientTimeout(total=_SELORA_LOCAL_DISCOVERY_PROBE_TIMEOUT_S)
            # Discover the loaded base model id. Used as the OpenAI
            # ``model`` field for inspectability and as a telemetry tag.
            # The same body carries ``meta.n_ctx``, so _apply_models_payload
            # records the served context window here too — free, and it
            # means the window is known from first use rather than only
            # after the panel triggers a capability refresh.
            try:
                async with session.get(
                    f"{self._host}/v1/models",
                    headers=self._get_headers(),
                    timeout=timeout,
                ) as resp:
                    if resp.status == 200:
                        self._apply_models_payload(await resp.json())
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                _LOGGER.debug("Selora Local /v1/models probe failed: %s", exc)
            # Discover loaded LoRAs. Treat any non-200 status or
            # transport error as transient — arm the backoff and leave
            # ``_lora_slots`` unset so the next call retries.
            try:
                async with session.get(
                    f"{self._host}/lora-adapters",
                    headers=self._get_headers(),
                    timeout=timeout,
                ) as resp:
                    if resp.status == 404:
                        # No such endpoint: this build serves a single
                        # model and has no adapters to route between.
                        # That is an answer, not a hub that is still
                        # coming up — escalating against it would leave
                        # ``_lora_slots`` unset and every request
                        # raising for the life of the process. Record
                        # "no LoRAs" and let the turns run against the
                        # base model, which activation already handles.
                        self._lora_slots = {}
                        self._n_slots = 0
                        _LOGGER.info(
                            "Selora Local hub has no /lora-adapters endpoint — "
                            "serving the base model without specialist routing"
                        )
                        return
                    if resp.status != 200:
                        delay = self._schedule_discovery_retry()
                        # The short steps are the ordinary restart race,
                        # which settles on its own within a second or
                        # two. Logging each of them puts seven lines in
                        # everyone's log for a non-event; only once the
                        # schedule has escalated is something actually
                        # wrong with the hub.
                        _log_at(delay, _LOGGER.info)(
                            "Selora Local /lora-adapters returned %s — will retry in %.1fs",
                            resp.status,
                            delay,
                        )
                        return
                    slots = await resp.json()
            except (aiohttp.ClientError, TimeoutError) as exc:
                delay = self._schedule_discovery_retry()
                _log_at(delay, _LOGGER.warning)(
                    "Selora Local LoRA discovery failed: %s — will retry in %.1fs",
                    exc,
                    delay,
                )
                return
            mapping: dict[str, int] = {}
            for slot in slots or []:
                path = slot.get("path", "") or ""
                name = path.rsplit("/", 1)[-1].lower()
                slot_id = slot.get("id")
                if slot_id is None:
                    continue
                for keyword in SELORA_LOCAL_LORA_FILENAME_KEYWORDS:
                    if keyword in name and keyword not in mapping:
                        mapping[keyword] = int(slot_id)
                        break
            self._lora_slots = mapping
            self._n_slots = len(slots or [])
            self._discovery_backoff_s = _SELORA_LOCAL_DISCOVERY_BACKOFF_MIN_S
            _LOGGER.info(
                "Selora Local discovered base=%s, %d LoRA slots: %s",
                self._base_model_id or "?",
                self._n_slots,
                mapping or "(no recognized intents)",
            )

    async def _activate_lora_for_kind(self, kind: str | None) -> None:
        """POST /lora-adapters so the upcoming chat completion routes to the right specialist."""
        if self._backend == SELORA_LOCAL_BACKEND_OLLAMA:
            return
        await self._ensure_lora_discovery()
        if self._lora_slots is None:
            # Discovery failed (transient hub unavailability, in backoff).
            raise _SeloraLocalActivationError(
                "LoRA discovery has not completed — the hub may still be booting"
            )
        if self._n_slots == 0:
            # Discovery succeeded but the hub has no LoRAs loaded (single-model backend).
            return
        intent = SELORA_LOCAL_KIND_TO_INTENT.get(kind or "", SELORA_LOCAL_DEFAULT_INTENT)
        target = self._lora_slots.get(intent)
        if target is None:
            # Hub has slots but none match the requested intent — the specialist isn't loaded.
            if self._active_slot is not None:
                await self._deactivate_all_loras(intent)
            return
        if target == self._active_slot:
            return
        body = [{"id": i, "scale": 1.0 if i == target else 0.0} for i in range(self._n_slots)]
        try:
            session = self._get_session()
            async with session.post(
                f"{self._host}/lora-adapters",
                headers=self._get_headers(),
                json=body,
                timeout=aiohttp.ClientTimeout(total=HEALTH_CHECK_TIMEOUT),
            ) as resp:
                if resp.status == 200:
                    self._active_slot = target
                    return
                _LOGGER.warning(
                    "Selora Local POST /lora-adapters returned %s for slot %d (%s)",
                    resp.status,
                    target,
                    intent,
                )
                self._active_slot = None
                raise _SeloraLocalActivationError(
                    f"LoRA activation failed: /lora-adapters returned HTTP {resp.status}"
                )
        except (aiohttp.ClientError, TimeoutError) as exc:
            _LOGGER.warning(
                "Selora Local LoRA activation for slot %d (%s) failed: %s",
                target,
                intent,
                exc,
            )
            self._active_slot = None
            raise _SeloraLocalActivationError(f"LoRA activation failed: {exc}") from exc

    async def _deactivate_all_loras(self, intent: str) -> None:
        """POST /lora-adapters with every slot scaled to 0.0 so the base model serves the next request."""
        body = [{"id": i, "scale": 0.0} for i in range(self._n_slots)]
        try:
            session = self._get_session()
            async with session.post(
                f"{self._host}/lora-adapters",
                headers=self._get_headers(),
                json=body,
                timeout=aiohttp.ClientTimeout(total=HEALTH_CHECK_TIMEOUT),
            ) as resp:
                if resp.status == 200:
                    self._active_slot = None
                    _LOGGER.debug(
                        "Selora Local deactivated all LoRAs (intent %r has no matching slot)",
                        intent,
                    )
                    return
                _LOGGER.warning(
                    "Selora Local POST /lora-adapters (deactivate) returned %s for intent %r",
                    resp.status,
                    intent,
                )
                self._active_slot = None
                raise _SeloraLocalActivationError(
                    f"LoRA deactivation failed: /lora-adapters returned HTTP {resp.status}"
                )
        except (aiohttp.ClientError, TimeoutError) as exc:
            _LOGGER.warning(
                "Selora Local LoRA deactivation for intent %r failed: %s",
                intent,
                exc,
            )
            self._active_slot = None
            raise _SeloraLocalActivationError(f"LoRA deactivation failed: {exc}") from exc

    # Override the request methods to slip in slot activation.

    async def send_request(  # type: ignore[override]
        self,
        system: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 1024,
        log_errors: bool = True,
        timeout: float | None = None,
    ) -> tuple[str | None, str | None]:
        await self._ensure_specialist_prompts_loaded()
        # Outside the request lock deliberately — see _settle_discovery.
        await self._settle_discovery()
        # Hold the request lock from activation through completion so an overlapping call can't swap the LoRA mid-flight.
        async with self._request_lock:
            try:
                await self._activate_lora_for_kind(self._call_kind.get())
            except _SeloraLocalActivationError as exc:
                # Don't fall through to the chat completion — the hub is still on whatever slot the previous call activated, so the prompt would be answered by the wrong LoRA.
                return None, str(exc)
            return await super().send_request(
                system,
                messages,
                max_tokens=max_tokens,
                log_errors=log_errors,
                timeout=timeout,
            )

    async def raw_request(  # type: ignore[override]
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        await self._ensure_specialist_prompts_loaded()
        # Outside the request lock deliberately — see _settle_discovery.
        await self._settle_discovery()
        async with self._request_lock:
            # SeloraLocalActivationError is a ConnectionError, which the tool-calling loop in LLMClient already handles — so we let it propagate rather than fabricating a dict result.
            await self._activate_lora_for_kind(self._call_kind.get())
            return await super().raw_request(system, messages, tools=tools)

    # Pre-warm

    async def prewarm(self, entities: list[Any] | None = None) -> None:
        """Send one tiny request per chat specialist so the hub's prefix
        cache fills and each LoRA loads. Without this, the first real
        user request per specialist pays a ~16s cold prefill on Vega 8.

        ``entities`` should be the real HA entity list (from
        ``_collect_entity_states``). Pre-warming with the actual entity
        list is what makes the cache HIT on the user's first chat —
        priming with no entities builds a different prefix and forces
        a re-prefill anyway. Mirrors what model-tester's
        ``_prewarm_llamacpp_specialists`` does (sends the full
        training-format body with synthetic ENTITIES).

        Safe to call multiple times — discovery is cached. Failures are
        swallowed (logged) so a hub hiccup at HA startup never blocks
        async_setup_entry.

        Pre-warm deliberately spends nothing from the discovery retry
        schedule beyond its own first probe: its requests don't wait out
        a window, and it stops at the first specialist that can't be
        reached. A hub that hasn't answered discovery will answer none of
        the remaining specialists either, and every extra attempt would
        escalate the backoff that the user's first real request is about
        to depend on.
        """
        token = self._prewarming.set(True)
        try:
            await self._run_prewarm(entities)
        finally:
            self._prewarming.reset(token)

    async def _run_prewarm(self, entities: list[Any] | None) -> None:
        """The pre-warm loop itself. See prewarm() for what it is for."""
        await self._ensure_lora_discovery()
        ok = 0
        for kind in _SELORA_LOCAL_PREWARM_KINDS:
            self.set_call_kind(kind)
            # Same chat context the first real user request will use —
            # this makes build_payload generate the EXACT same prefix
            # (system + USER REQUEST + EXISTING AUTOMATIONS + AVAILABLE
            # ENTITIES blocks) as the real call, so llama-server's
            # cache_prompt actually hits.
            self.set_chat_context(
                user_message="warmup",
                entities=entities or [],
                existing_automations=[],
                history=[],
            )
            try:
                _, err = await self.send_request(
                    "",
                    [{"role": "user", "content": "warmup"}],
                    max_tokens=1,
                    log_errors=False,
                    timeout=120.0,
                )
                if err is None:
                    ok += 1
                else:
                    _LOGGER.debug("Selora Local pre-warm for %s: %s", kind, err)
            except (aiohttp.ClientError, TimeoutError, ConnectionError) as exc:
                _LOGGER.debug("Selora Local pre-warm for %s failed: %s", kind, exc)
            finally:
                self.set_call_kind(None)
            if self._lora_slots is None:
                # The hub hasn't answered discovery. The remaining
                # specialists would each re-probe a hub we already know
                # isn't serving and escalate the backoff for it. Stop —
                # the user's first request retries from a short step.
                _LOGGER.debug(
                    "Selora Local pre-warm stopped at %s: the hub is not serving yet",
                    kind,
                )
                break
        _LOGGER.info(
            "Selora Local pre-warm complete: %d/%d specialists primed (%d entities in prefix)",
            ok,
            len(_SELORA_LOCAL_PREWARM_KINDS),
            len(entities or []),
        )

    def _resolve_state_placeholder(self, entity_id: str) -> str:
        """Look up the live state of ``entity_id`` for the answer specialist's ``{entity_id}`` template substitution."""
        # Calendar entities never expose their event list as live state (synthetic_home / stock HA only serve events via calendar.get_events), so ``states.get("calendar.personal").state`` is just "on"/"off".
        if entity_id.startswith("calendar."):
            resolved = self._resolve_calendar_placeholder(entity_id)
            if resolved is not None:
                return resolved
        state = self._hass.states.get(entity_id)
        if state is None:
            return entity_id
        attrs = state.attributes or {}
        fname = attrs.get("friendly_name", entity_id)
        return f"{fname}: {state.state}"

    def _resolve_truncated_placeholder(self, text: str) -> str | None:
        """Salvage an answer reply clipped mid-placeholder."""
        if not text or self._hass is None:
            return None
        m = _SELORA_LOCAL_TRUNC_PLACEHOLDER_RE.search(text)
        if m is None:
            return None
        domain = m.group(1)
        partial_slug = m.group(2)
        partial_id = f"{domain}.{partial_slug}"
        # Resolve the partial entity ref.
        resolved_state: Any = None
        exact = self._hass.states.get(partial_id) if partial_slug else None
        if exact is not None:
            resolved_state = exact
        else:
            matches = [
                s for s in self._hass.states.async_all() if s.entity_id.startswith(partial_id)
            ]
            if len(matches) == 1:
                resolved_state = matches[0]
        if resolved_state is None:
            return None
        live = (resolved_state.state or "").lower()
        if not live or live in ("unknown", "unavailable"):
            return None
        prefix = text[: m.start()].rstrip()
        if not prefix:
            return None
        marker = f"\n[[entities:{resolved_state.entity_id}]]"
        return f"{prefix} {live}.{marker}"

    # Health check

    async def health_check(self) -> bool:
        """Check the host is reachable."""
        endpoint = "/api/tags" if self._backend == SELORA_LOCAL_BACKEND_OLLAMA else "/health"
        try:
            session = self._get_session()
            async with session.get(
                f"{self._host}{endpoint}",
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=HEALTH_CHECK_TIMEOUT),
            ) as resp:
                return resp.status == 200
        except (aiohttp.ClientError, TimeoutError) as exc:
            _LOGGER.debug("Selora Local health check failed: %s", exc)
            return False

    def _apply_models_payload(self, data: object) -> None:
        """Read the base model id and served context window out of a
        ``GET /v1/models`` body.

        Shared by ``async_refresh_capabilities`` and the LoRA discovery
        pass so both fill the same cache from one response shape — LoRA
        discovery already fetches this endpoint, so it costs it nothing
        to record the window while the body is in hand. Missing or
        malformed fields are skipped, never written as None: the hub is
        the only source for these and a partial body must not clobber an
        earlier good reading.

        The envelope is shape-checked before anything is read out of it,
        not just the fields inside. ``host`` is user-configured, so a
        proxy or an unrelated server on that port can answer 200 with a
        JSON array or scalar, and ``.get`` on one of those raises
        ``AttributeError`` — which neither caller catches. That would
        break the non-raising contract of ``async_refresh_capabilities``
        (reached from the ``get_config`` websocket handler) and would
        surface on the completion path through the LoRA discovery pass.
        """
        if not isinstance(data, dict):
            _LOGGER.debug(
                "Selora Local /v1/models answered with a %s, not an object — ignoring",
                type(data).__name__,
            )
            return
        raw_entries = data.get("data")
        entries = (
            [entry for entry in raw_entries if isinstance(entry, dict)]
            if isinstance(raw_entries, list)
            else []
        )
        for entry in entries:
            if model_id := entry.get("id"):
                self._base_model_id = str(model_id)
                break
        for entry in entries:
            meta = entry.get("meta")
            if not isinstance(meta, dict):
                continue
            if n_ctx := _positive_int(meta.get("n_ctx")):
                self._context_window = n_ctx
                break

    async def async_refresh_capabilities(self) -> None:
        """Ask the hub what context window llama-server is serving.

        ``GET /v1/models`` reports the live server config in
        ``data[0].meta.n_ctx`` — authoritative, because the operator
        fixes it with ``-c`` at launch and it cannot be changed
        per-request. ``meta.n_ctx_train`` sits next to it and is the
        model's trained maximum; we deliberately read the served one.

        TTL-cached, and the timestamp is stamped before the request so a
        hub that is down doesn't get re-probed on every panel load. Any
        failure (hub off, older build with no ``meta`` block, malformed
        body) leaves the cached value untouched rather than erasing a
        good reading. Never raises.
        """
        now = time.monotonic()
        if (
            self._context_probe_at is not None
            and now - self._context_probe_at < CONTEXT_WINDOW_PROBE_TTL_S
        ):
            return
        self._context_probe_at = now
        try:
            session = self._get_session()
            async with session.get(
                f"{self._host}/v1/models",
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=HEALTH_CHECK_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    _LOGGER.debug("Selora Local context-window probe returned HTTP %s", resp.status)
                    return
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            _LOGGER.debug("Selora Local context-window probe failed: %s", exc)
            return
        self._apply_models_payload(data)
