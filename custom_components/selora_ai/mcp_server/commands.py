"""MCP service-call tools, and the dispatch + state read-back chat shares."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .entities import _format_state_value

_LOGGER = logging.getLogger(__name__)


# ── Tool: selora_validate_action ───────────────────────────────────────────────


def _safe_command_entity_allowlist(hass: HomeAssistant) -> set[str]:
    """Entity IDs that the safe-command tools are allowed to operate on.

    Shared by ``_tool_validate_action`` (pre-flight check) and
    ``_tool_execute_command`` (actual dispatch) so a validation success
    can never disagree with the subsequent execution. Mirrors the JSON
    command path: starts from the filtered ``_collect_entity_states``
    snapshot (unavailable / disabled / non-actionable entities are
    excluded) and adds scene-domain states that aren't tracked by the
    collector but are in the safe-command service allowlist.
    """
    from .. import _collect_entity_states

    allowed = {e["entity_id"] for e in _collect_entity_states(hass) if e.get("entity_id")}
    for state in hass.states.async_all("scene"):
        if state.state not in ("unavailable", "unknown"):
            allowed.add(state.entity_id)
    return allowed


async def _tool_validate_action(
    hass: HomeAssistant,
    arguments: dict[str, Any],
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Validate a service call against the safe-command policy without executing it."""
    from ..llm_client import validate_command_action

    service = str(arguments.get("service", "")).strip()
    raw_entity = arguments.get("entity_id")
    if isinstance(raw_entity, str):
        target_ids: str | list[str] = raw_entity.strip()
    elif isinstance(raw_entity, list):
        target_ids = [str(e).strip() for e in raw_entity if str(e).strip()]
    else:
        target_ids = ""

    data = arguments.get("data")
    if data is not None and not isinstance(data, dict):
        return {
            "valid": False,
            "errors": ["data must be an object"],
            "service": service,
            "domain": None,
            "allowed_data_keys": [],
        }

    # Use the same allowlist as _tool_execute_command so this pre-flight
    # check can't approve an entity the dispatcher would later reject
    # (unavailable, disabled, non-actionable, off-domain). ``hass`` +
    # ``session_id`` are also passed so REVIEW services covered by a
    # standing Session/Always grant come back ``valid=True`` instead of
    # ``requires_approval`` — without them the tool path would keep
    # surfacing approval cards even after the user clicked Allow.
    return validate_command_action(
        service,
        target_ids,
        data,
        known_entity_ids=_safe_command_entity_allowlist(hass),
        hass=hass,
        session_id=session_id,
    )


# ── Tool: selora_execute_command ───────────────────────────────────────────────


# Upper bound on how long execute_command waits for a slow device to reflect
# a command before reading back its state. Only hit by genuinely slow devices
# doing a real transition; no-op commands and fast/optimistic writes never wait
# this long.
_STATE_SETTLE_TIMEOUT = 5.0

# Maps a service to the state its target ends up in, so an idempotent no-op
# (the target is already there) can skip the settle wait — those commonly emit
# no state event at all and would otherwise stall on the timeout. Only confident
# mappings live here: a domain whose turn_on doesn't resolve to the literal
# "on" state (climate → an HVAC mode, media_player → varies) is omitted so we
# fall back to waiting. ``*.turn_off`` → "off" holds across every domain and is
# handled separately in ``_requested_terminal_state``.
_SERVICE_TERMINAL_STATE: dict[str, str] = {
    "light.turn_on": "on",
    "switch.turn_on": "on",
    "fan.turn_on": "on",
    "input_boolean.turn_on": "on",
    "humidifier.turn_on": "on",
    "siren.turn_on": "on",
    "remote.turn_on": "on",
    "lock.lock": "locked",
    "lock.unlock": "unlocked",
    "cover.open_cover": "open",
    "cover.close_cover": "closed",
    "media_player.media_play": "playing",
    "media_player.media_pause": "paused",
}


def _requested_terminal_state(service: str) -> str | None:
    """Return the state ``service`` drives its target to, or None if unknown."""
    if service.endswith(".turn_off"):
        return "off"
    return _SERVICE_TERMINAL_STATE.get(service)


def _targets_awaiting_transition(
    hass: HomeAssistant, expected: str | None, target_ids: list[str]
) -> list[str]:
    """Return the targets a real state change is expected for.

    Targets already in ``expected`` (idempotent no-ops — turning on an already-on
    light, locking a locked door) are dropped: they often complete without
    emitting any state event even though ``blocking=True`` confirmed success, so
    waiting for one would just stall a command that already succeeded. Callers
    arm the settle wait only for the returned subset, so a mixed batch (one light
    already on, one off) still returns as soon as the entity that actually
    transitions reports in. When the service has no known terminal state
    (``expected is None``), every target is returned (wait for the first event).
    """
    if expected is None:
        return list(target_ids)
    return [
        eid for eid in target_ids if (st := hass.states.get(eid)) is None or st.state != expected
    ]


def _arm_state_settle(
    hass: HomeAssistant,
    entity_ids: list[str],
    expected: str | None,
) -> tuple[asyncio.Event, Callable[[], None]]:
    """Arm event listeners so a subsequent service call's outcome can be awaited.

    ``hass.services.async_call(blocking=True)`` waits for the service handler
    to return, but *not* for the resulting state-change event to propagate.
    Push integrations that write state optimistically inside ``async_turn_on``
    are current when the call returns, but coordinator/poll-backed lights update
    a beat later — reading ``hass.states.get()`` immediately then reports the
    stale pre-command state (the "commands executed but lights still report off"
    bug). Waiting for the state event fixes that.

    Must be called *before* the service call so synchronous writes are caught
    too: callback listeners run inline when the state event fires, so an
    optimistic integration that writes state during a blocking call has already
    resolved by the time the call returns.

    When ``expected`` is known (the terminal state the service drives toward), a
    target settles only once it actually reaches that state — not on the first
    event. This matters for services with transitional states: ``cover.open_cover``
    emits ``opening`` before ``open`` and a lock emits ``locking`` before
    ``locked``; settling on the first touch would report the transitional value.
    When ``expected`` is None (service with no known terminal state), the first
    event of either kind settles the target — the best signal available.

    Both real transitions (``state_changed``) and no-op writes (``state_reported``)
    are observed. Only a command that touches the entity in neither way (rare: a
    poll-backed device that writes nothing until its next scheduled refresh), or
    one that never reaches ``expected`` within the timeout, falls through to the
    caller's timeout.

    Returns an ``asyncio.Event`` that is set once every target has settled (or
    immediately if there are no targets) and an unsubscribe callback the caller
    must invoke when done.
    """
    from homeassistant.core import Event, callback  # noqa: PLC0415
    from homeassistant.helpers.event import (  # noqa: PLC0415
        async_track_state_change_event,
        async_track_state_report_event,
    )

    pending: set[str] = set(entity_ids)
    settled = asyncio.Event()
    if not pending:
        settled.set()
        return settled, lambda: None

    @callback
    def _on_touch(event: Event) -> None:
        eid = event.data.get("entity_id")
        if eid not in pending:
            return
        if expected is not None:
            new_state = event.data.get("new_state")
            # Wait for the requested terminal state; ignore transitional ones.
            if new_state is None or new_state.state != expected:
                return
        pending.discard(eid)
        if not pending:
            settled.set()

    unsubs = [
        async_track_state_change_event(hass, entity_ids, _on_touch),
        async_track_state_report_event(hass, entity_ids, _on_touch),
    ]

    def _unsub() -> None:
        for unsub in unsubs:
            unsub()

    return settled, _unsub


async def _tool_execute_command(
    hass: HomeAssistant,
    arguments: dict[str, Any],
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Validate and execute a service call within the safe-command allowlist.

    Replaces the legacy ``{"intent":"command","calls":[...]}`` JSON path for
    tool-capable providers. Runs through ``validate_command_action`` first;
    only forwards to ``hass.services.async_call`` if the call passes the
    same policy that gates the JSON path — including the same actionable /
    active / domain filters that ``_collect_entity_states`` applies, so a
    disabled or non-actionable entity that exists in ``hass.states`` is
    rejected here just as the JSON path would reject it.
    """
    from ..llm_client import validate_command_action

    service = str(arguments.get("service", "")).strip()
    raw_entity = arguments.get("entity_id")
    if isinstance(raw_entity, str):
        target_ids: list[str] = [raw_entity.strip()] if raw_entity.strip() else []
    elif isinstance(raw_entity, list):
        target_ids = [str(e).strip() for e in raw_entity if str(e).strip()]
    elif raw_entity is None:
        # No-target services (notify.mobile_app_*, script.foo,
        # shell_command.bar) are legitimate REVIEW calls — the validator
        # decides whether an entity is required for this service. Don't
        # short-circuit here, otherwise an approved no-target service
        # would fail with "entity_id must be a string or list of strings"
        # instead of executing.
        target_ids = []
    else:
        return {"valid": False, "errors": ["entity_id must be a string or list of strings"]}

    data_raw = arguments.get("data")
    if data_raw is not None and not isinstance(data_raw, dict):
        return {"valid": False, "errors": ["data must be an object"]}
    data: dict[str, Any] = data_raw or {}

    # Shared with _tool_validate_action so pre-flight checks and actual
    # execution agree on which entities are permitted. ``hass`` +
    # ``session_id`` let the validator consult the approval store so
    # REVIEW services with a standing Session/Always grant execute
    # directly here instead of re-prompting the user every turn.
    validation = validate_command_action(
        service,
        target_ids,
        data,
        known_entity_ids=_safe_command_entity_allowlist(hass),
        hass=hass,
        session_id=session_id,
    )
    if not validation["valid"]:
        return validation

    return await _call_service_and_settle(hass, service, target_ids, data)


async def _call_service_and_settle(
    hass: HomeAssistant,
    service: str,
    target_ids: list[str],
    data: dict[str, Any],
    *,
    want_response: bool = False,
    settle: bool = True,
) -> dict[str, Any]:
    """Run an already-validated service call and read back its targets' states.

    Shared by chat's policy-gated path and MCP's any-service path, so both
    wait for the state to settle the same way. ``want_response`` asks for
    the service's response data (to-do items, calendar events, forecasts),
    which Home Assistant refuses to return unless asked. ``settle=False``
    skips the wait, for a read that changes no state.
    """
    domain, service_name = service.split(".", 1)
    # No-target REVIEW services (notify.mobile_app_*, script.foo, …)
    # must NOT carry an empty ``entity_id`` field — HA rejects it as
    # an invalid target. Only include the entity_id key when the call
    # actually targets entities.
    service_data: dict[str, Any] = dict(data)
    if target_ids:
        service_data["entity_id"] = target_ids

    # Only wait for a state event on targets a real transition is expected for.
    # Targets already satisfying the requested action (idempotent no-op, e.g.
    # turning on an already-on light) are dropped — the current state is already
    # correct and they often emit no event, so waiting would just stall on the
    # timeout. Arming only the transitioning subset also keeps a mixed batch
    # (one light already on, one off) prompt.
    expected = _requested_terminal_state(service)
    settle_ids = _targets_awaiting_transition(hass, expected, target_ids) if settle else []
    # Arm the settle watch BEFORE the call so synchronous state writes (and
    # idempotent no-op state_reported events) are caught, not just delayed ones.
    # When the terminal state is known, the watch waits for it specifically so a
    # transitional state (cover "opening", lock "locking") isn't reported.
    settled, unsub_settle = (
        _arm_state_settle(hass, settle_ids, expected) if settle_ids else (None, None)
    )
    try:
        try:
            response = await hass.services.async_call(
                domain,
                service_name,
                service_data,
                blocking=True,
                return_response=want_response,
            )
        except Exception as exc:  # noqa: BLE001 — surface failure to the LLM
            _LOGGER.error("execute_command failed for %s: %s", service, exc)
            return {
                "executed": False,
                "service": service,
                "entity_ids": target_ids,
                "error": str(exc),
            }

        # blocking=True awaits the service handler, not the state event. Wait
        # for each target to reflect the command (a real change or a no-op
        # report) before reading back, otherwise post_states would report the
        # stale pre-command state. Bounded so a silent poll-backed device can't
        # stall the response indefinitely.
        if settled is not None:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(settled.wait(), _STATE_SETTLE_TIMEOUT)
    finally:
        if unsub_settle is not None:
            unsub_settle()

    # Capture post-execution state so the LLM can confirm to the user.
    post_states: list[dict[str, Any]] = []
    for eid in target_ids:
        state = hass.states.get(eid)
        if state is not None:
            post_states.append({"entity_id": eid, "state": _format_state_value(state.state)})

    result: dict[str, Any] = {
        "executed": True,
        "service": service,
        "entity_ids": target_ids,
        "states": post_states,
    }
    if want_response and response is not None:
        result["response"] = response
    return result


async def _tool_mcp_validate_action(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """MCP's pre-flight check: the same verdict ``_tool_mcp_execute_command`` acts on."""
    from ..mcp_service_call import check_service_call  # noqa: PLC0415

    return dict(
        check_service_call(
            hass,
            str(arguments.get("service", "")),
            arguments.get("entity_id"),
            arguments.get("data"),
        )
    )


async def _tool_mcp_execute_command(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Any service over MCP, gated by risk — see ``mcp_service_call``.

    Chat's ``_tool_execute_command`` keeps its allowlist and approval cards;
    MCP has no card, so a risky call is confirmed by calling again.
    """
    from ..mcp_service_call import async_execute_service_call  # noqa: PLC0415
    from ..tool_executor import _opt_bool  # noqa: PLC0415

    return await async_execute_service_call(
        hass,
        str(arguments.get("service", "")),
        arguments.get("entity_id"),
        arguments.get("data"),
        confirmed=_opt_bool(arguments.get("confirmed")) is True,
    )
