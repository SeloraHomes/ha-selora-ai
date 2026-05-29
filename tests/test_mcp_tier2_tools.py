"""Tests for Tier 2/3 tools: execute_command, activate_scene, search_entities,
get_entity_history, eval_template, and the large_context_only gating."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

from custom_components.selora_ai.mcp_server import (
    _tool_eval_template,
    _tool_execute_command,
    _tool_get_entity_history,
    _tool_search_entities,
)
from custom_components.selora_ai.tool_registry import CHAT_TOOLS


@pytest.fixture
async def setup_world(hass: HomeAssistant):
    """A small home: lights in two areas, a switch, and a scene entity."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)

    entry = MockConfigEntry(domain="test", entry_id="mock_entry_tier2")
    entry.add_to_hass(hass)

    kitchen = area_reg.async_create("Kitchen")
    bedroom = area_reg.async_create("Master Bedroom")

    # Kitchen island light (target for search query "kitchen island")
    ent_reg.async_get_or_create(
        "light",
        "test",
        "kitchen_island_uid",
        suggested_object_id="kitchen_island",
    )
    ent_reg.async_update_entity("light.kitchen_island", area_id=kitchen.id)
    hass.states.async_set(
        "light.kitchen_island",
        "off",
        {"friendly_name": "Kitchen Island Light"},
    )

    # Bedroom lamp (alias match target)
    ent_reg.async_get_or_create(
        "light",
        "test",
        "bedroom_lamp_uid",
        suggested_object_id="bedroom_lamp",
    )
    ent_reg.async_update_entity(
        "light.bedroom_lamp",
        area_id=bedroom.id,
        aliases={"reading lamp"},
    )
    hass.states.async_set(
        "light.bedroom_lamp",
        "on",
        {"friendly_name": "Bedroom Lamp"},
    )

    # A switch (different domain)
    ent_reg.async_get_or_create(
        "switch",
        "test",
        "coffee_uid",
        suggested_object_id="coffee_maker",
    )
    hass.states.async_set("switch.coffee_maker", "off", {"friendly_name": "Coffee Maker"})

    # Scene
    hass.states.async_set("scene.movie_night", "scening")

    return {"kitchen_id": kitchen.id, "bedroom_id": bedroom.id}


# ── execute_command ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_command_runs_service(
    hass: HomeAssistant, setup_world
) -> None:
    """execute_command invokes hass.services and returns post-state."""
    calls: list[dict] = []

    async def _capture(call):
        calls.append(
            {
                "domain": call.domain,
                "service": call.service,
                "data": dict(call.data),
            }
        )
        # Simulate the side effect
        hass.states.async_set("light.kitchen_island", "on")

    hass.services.async_register("light", "turn_on", _capture)

    result = await _tool_execute_command(
        hass,
        {
            "service": "light.turn_on",
            "entity_id": "light.kitchen_island",
            "data": {"brightness_pct": 60},
        },
    )

    assert result["executed"] is True
    assert result["service"] == "light.turn_on"
    assert result["entity_ids"] == ["light.kitchen_island"]
    assert result["states"][0]["state"] == "on"

    assert len(calls) == 1
    assert calls[0]["domain"] == "light"
    assert calls[0]["service"] == "turn_on"
    assert calls[0]["data"]["entity_id"] == ["light.kitchen_island"]
    assert calls[0]["data"]["brightness_pct"] == 60


@pytest.mark.asyncio
async def test_execute_command_rejects_invalid_call(
    hass: HomeAssistant, setup_world
) -> None:
    """Unknown verb is rejected before any service is called."""
    result = await _tool_execute_command(
        hass,
        {"service": "light.explode", "entity_id": "light.kitchen_island"},
    )
    assert result["valid"] is False
    assert any("not a valid light service" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_execute_command_rejects_unknown_entity(
    hass: HomeAssistant, setup_world
) -> None:
    """Unknown entity_id is rejected (caught by validate_command_action)."""
    result = await _tool_execute_command(
        hass,
        {"service": "light.turn_on", "entity_id": "light.ghost"},
    )
    assert result["valid"] is False
    assert any("not known to Home Assistant" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_execute_command_rejects_blocked_service(
    hass: HomeAssistant, setup_world
) -> None:
    """BLOCKED-bucket services (``python_script.exec``, ``homeassistant.restart``)
    can never be invoked through the chat-driven execute_command path."""
    result = await _tool_execute_command(
        hass,
        {"service": "python_script.exec", "entity_id": "python_script.foo"},
    )
    assert result["valid"] is False
    assert any("allowlist" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_execute_command_requires_approval_for_review_service(
    hass: HomeAssistant, setup_world
) -> None:
    """REVIEW-bucket services (lock.unlock) come back with
    ``requires_approval=True`` so the LLM can route the call to the
    chat approval card instead of telling the user the command failed."""
    result = await _tool_execute_command(
        hass,
        {"service": "lock.unlock", "entity_id": "lock.front"},
    )
    assert result["valid"] is False
    assert result.get("requires_approval") is True
    assert result.get("risk_level") == "high"


@pytest.mark.asyncio
async def test_execute_command_review_shape_rejected_before_approval(
    hass: HomeAssistant, setup_world
) -> None:
    """SECURITY regression: an LLM-supplied REVIEW call with a malformed
    payload (unsupported data key here) must be rejected at validate
    time — NOT held for approval. Otherwise the user could click Allow
    and the unvalidated payload would execute via async_call, bypassing
    the same data-key whitelist that gates the JSON command path.
    """
    result = await _tool_execute_command(
        hass,
        {
            "service": "lock.unlock",
            "entity_id": "lock.front",
            "data": {"bogus_param": 42},
        },
    )
    assert result["valid"] is False
    # Crucially: requires_approval is NOT set — the shape is bad, so
    # no amount of clicking Allow could ever make this safe to run.
    assert result.get("requires_approval") is not True
    assert any("bogus_param" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_resolve_approval_does_not_grant_on_invalid_call(
    hass: HomeAssistant,
) -> None:
    """SECURITY regression: the resolver used to persist Session/Always
    grants for every REVIEW service in the proposal BEFORE revalidating
    the calls. A model-supplied or stale ``command_approval`` payload
    with a malformed entry (e.g. ``lock.unlock`` without an entity_id,
    which now fails ``requires_target``) could therefore record a
    permanent ``lock.unlock`` grant even though nothing actually ran.
    The next ``lock.unlock`` request would then bypass approval.

    Click Always on a card whose only call fails validation, and assert
    the persistent grant store stays empty.
    """
    import unittest.mock as _mock
    import uuid as _uuid

    from custom_components.selora_ai import (
        ConversationStore,
        _resolve_approval,
    )
    from custom_components.selora_ai.approval_store import ApprovalStore
    from custom_components.selora_ai.const import DOMAIN

    store = ConversationStore(hass)
    approval_store = ApprovalStore(hass)
    await approval_store.async_load()
    hass.data.setdefault(DOMAIN, {})["_approval_store"] = approval_store

    proposal_id = str(_uuid.uuid4())
    session_id = "sess-resolver"
    await store.append_message(
        session_id,
        "assistant",
        "needs approval",
        intent="command_approval",
        command_approval={
            "proposal_id": proposal_id,
            "risk_level": "high",
            "risk_reasons": ["…"],
            # Malformed: lock.unlock with no entity_id. This now fails
            # ``_validate_review_call`` (requires_target=True for lock.*),
            # so no service should ever fire AND no grant should land.
            "calls": [{"service": "lock.unlock", "target": {}, "data": {}}],
        },
        approval_status="pending",
    )

    connection = _mock.MagicMock()
    connection.user = _mock.MagicMock(id="user-1")

    await _resolve_approval(
        hass,
        connection,
        {"id": 1, "session_id": session_id, "proposal_id": proposal_id, "scope": "always"},
        store,
        approval_store,
        session_id,
        proposal_id,
        "always",
    )

    # Persistent grant store stays empty — the call never validated,
    # so granting it would be unsafe.
    grants = await approval_store.async_list_grants()
    assert grants == [], grants
    # And no service was executed either.
    assert approval_store.is_approved("lock.unlock") is False


@pytest.mark.asyncio
async def test_resolve_delayed_approval_reports_dropped_calls(
    hass: HomeAssistant,
) -> None:
    """P2: an approved delayed proposal with a mix of valid and invalid
    calls used to schedule only the valid ones while telling the user
    everything was scheduled and returning ``errors: []``. The dropped
    calls (e.g. an entity removed between proposal and Allow) must be
    surfaced, like the immediate-execution path does.
    """
    import unittest.mock as _mock
    import uuid as _uuid

    from custom_components.selora_ai import (
        ConversationStore,
        _resolve_approval,
    )
    from custom_components.selora_ai.approval_store import ApprovalStore
    from custom_components.selora_ai.const import DOMAIN

    hass.states.async_set("light.kitchen", "off", {})

    store = ConversationStore(hass)
    approval_store = ApprovalStore(hass)
    await approval_store.async_load()
    hass.data.setdefault(DOMAIN, {})["_approval_store"] = approval_store
    # Pre-seed the scheduler so the handler doesn't build a real
    # automation — we only care that dropped calls are reported.
    tracker = _mock.MagicMock()
    tracker.schedule_at_time = _mock.AsyncMock(
        return_value=_mock.MagicMock(schedule_id="sched-1")
    )
    hass.data[DOMAIN]["_scheduled_tasks"] = tracker

    proposal_id = str(_uuid.uuid4())
    session_id = "sess-delayed-drop"
    await store.append_message(
        session_id,
        "assistant",
        "needs approval",
        intent="command_approval",
        command_approval={
            "proposal_id": proposal_id,
            "risk_level": "high",
            "risk_reasons": ["", "…"],
            "original_intent": "delayed_command",
            "scheduled_time": "2099-01-01T10:00:00",
            "calls": [
                # Valid SAFE call — schedules fine.
                {"service": "light.turn_on", "target": {"entity_id": ["light.kitchen"]}},
                # Invalid: lock.unlock with no entity_id fails requires_target.
                {"service": "lock.unlock", "target": {}, "data": {}},
            ],
        },
        approval_status="pending",
    )

    connection = _mock.MagicMock()
    connection.user = _mock.MagicMock(id="user-1")

    await _resolve_approval(
        hass,
        connection,
        {"id": 1, "session_id": session_id, "proposal_id": proposal_id, "scope": "once"},
        store,
        approval_store,
        session_id,
        proposal_id,
        "once",
    )

    # Only the valid call was scheduled.
    tracker.schedule_at_time.assert_awaited_once()
    scheduled_calls = tracker.schedule_at_time.await_args.args[1]
    assert [c["service"] for c in scheduled_calls] == ["light.turn_on"]
    # The REVIEW call was dropped, leaving only a SAFE call — the risk gate
    # must NOT be bypassed for a bundle that no longer requires approval.
    assert tracker.schedule_at_time.await_args.kwargs.get("approved") is False

    # The dropped lock.unlock is surfaced in both the WS payload and the
    # persisted assistant message — not silently discarded.
    payload = connection.send_result.call_args.args[1]
    assert any("lock.unlock" in e for e in payload["errors"])
    persisted = payload["result_message"]
    assert "Errors:" in persisted["content"]
    assert "lock.unlock" in persisted["content"]


async def test_resolve_delayed_approval_bypasses_gate_for_review_call(
    hass: HomeAssistant,
) -> None:
    """A surviving REVIEW call (the user explicitly approved it) must keep
    the persisted scheduled automation enabled past the risk gate —
    ``approved=True`` — otherwise the one-shot is written disabled and
    silently never fires.
    """
    import unittest.mock as _mock
    import uuid as _uuid

    from custom_components.selora_ai import (
        ConversationStore,
        _resolve_approval,
    )
    from custom_components.selora_ai.approval_store import ApprovalStore
    from custom_components.selora_ai.const import DOMAIN

    hass.states.async_set("lock.front_door", "locked", {})

    store = ConversationStore(hass)
    approval_store = ApprovalStore(hass)
    await approval_store.async_load()
    hass.data.setdefault(DOMAIN, {})["_approval_store"] = approval_store
    tracker = _mock.MagicMock()
    tracker.schedule_at_time = _mock.AsyncMock(
        return_value=_mock.MagicMock(schedule_id="sched-2")
    )
    hass.data[DOMAIN]["_scheduled_tasks"] = tracker

    proposal_id = str(_uuid.uuid4())
    session_id = "sess-delayed-review"
    await store.append_message(
        session_id,
        "assistant",
        "needs approval",
        intent="command_approval",
        command_approval={
            "proposal_id": proposal_id,
            "risk_level": "high",
            "risk_reasons": ["…"],
            "original_intent": "delayed_command",
            "scheduled_time": "2099-01-01T10:00:00",
            "calls": [
                {"service": "lock.unlock", "target": {"entity_id": ["lock.front_door"]}},
            ],
        },
        approval_status="pending",
    )

    connection = _mock.MagicMock()
    connection.user = _mock.MagicMock(id="user-1")

    await _resolve_approval(
        hass,
        connection,
        {"id": 1, "session_id": session_id, "proposal_id": proposal_id, "scope": "once"},
        store,
        approval_store,
        session_id,
        proposal_id,
        "once",
    )

    tracker.schedule_at_time.assert_awaited_once()
    scheduled_calls = tracker.schedule_at_time.await_args.args[1]
    assert [c["service"] for c in scheduled_calls] == ["lock.unlock"]
    assert tracker.schedule_at_time.await_args.kwargs.get("approved") is True


def test_normalize_explicit_approval_attaches_quick_actions() -> None:
    """Regression: when the LLM directly emits ``intent: "command_approval"``
    without quick_actions, the chat persists a card the user can't
    resolve — no Allow/Deny buttons. Normalising the payload at parse
    time mints a stable proposal_id (if missing) and attaches the
    four sentinel actions tied to that id."""
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    llm_result = {
        "intent": "command_approval",
        "response": "needs approval",
        "command_approval": {
            "risk_level": "high",
            "risk_reasons": ["Releases a physical lock"],
            "calls": [
                {
                    "service": "lock.unlock",
                    "target": {"entity_id": ["lock.front"]},
                    "data": {},
                }
            ],
            # NB: no proposal_id, no quick_actions on the result.
        },
    }
    normalised = synthesize_approval_from_tool_log(llm_result, tool_log=None)
    proposal_id = normalised["command_approval"]["proposal_id"]
    assert isinstance(proposal_id, str) and proposal_id
    actions = normalised["quick_actions"]
    assert len(actions) == 4
    scopes = {a["value"].split(":")[1] for a in actions}
    assert scopes == {"once", "session", "always", "deny"}
    # Every sentinel must reference the SAME proposal_id so clicks
    # route to the persisted card.
    for action in actions:
        assert action["value"].endswith(f":{proposal_id}")


def test_normalize_explicit_approval_downgrades_malformed() -> None:
    """A ``command_approval`` payload with no ``calls`` list is
    nonsense — leaving it intact would persist an unresolvable card
    forever. Downgrade to ``intent: "answer"`` so the user gets a
    plain reply instead.
    """
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    bad = {
        "intent": "command_approval",
        "response": "i think you need approval but i forgot the calls",
        "command_approval": {"risk_level": "high"},  # missing calls list
    }
    result = synthesize_approval_from_tool_log(bad, tool_log=None)
    assert result["intent"] == "answer"
    assert "command_approval" not in result
    assert "quick_actions" not in result


def test_normalize_explicit_approval_derives_risk_when_missing() -> None:
    """P2: a model that emits command_approval with no risk_level must not
    default to LOW. The badge is derived server-side from the calls
    (lock.unlock → HIGH) so the user's safety decision isn't understated."""
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    llm_result = {
        "intent": "command_approval",
        "response": "needs approval",
        "command_approval": {
            "calls": [
                {"service": "lock.unlock", "target": {"entity_id": ["lock.front"]}}
            ],
        },
    }
    result = synthesize_approval_from_tool_log(llm_result, tool_log=None)
    assert result["command_approval"]["risk_level"] == "high"
    # Reason filled server-side since the model supplied none.
    assert result["command_approval"]["risk_reasons"]


def test_normalize_explicit_approval_overrides_understated_risk() -> None:
    """P2: a model can't talk its way to a lower badge. Server reclassifies
    alarm_disarm as HIGH even when the payload claims LOW."""
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    llm_result = {
        "intent": "command_approval",
        "response": "no big deal",
        "command_approval": {
            "risk_level": "low",
            "risk_reasons": ["totally fine"],
            "calls": [
                {
                    "service": "alarm_control_panel.alarm_disarm",
                    "target": {"entity_id": ["alarm_control_panel.home"]},
                }
            ],
        },
    }
    result = synthesize_approval_from_tool_log(llm_result, tool_log=None)
    assert result["command_approval"]["risk_level"] == "high"


def test_normalize_explicit_approval_reclassifies_shell_command_high() -> None:
    """P2: shell_command.* is HIGH regardless of the model's claim."""
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    llm_result = {
        "intent": "command_approval",
        "response": "just a script",
        "command_approval": {
            "risk_level": "low",
            "calls": [{"service": "shell_command.run_backup"}],
        },
    }
    result = synthesize_approval_from_tool_log(llm_result, tool_log=None)
    assert result["command_approval"]["risk_level"] == "high"


def test_normalize_explicit_approval_mixed_calls_take_max_risk() -> None:
    """P2: a SAFE call bundled with a HIGH call yields a HIGH badge."""
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    llm_result = {
        "intent": "command_approval",
        "response": "needs approval",
        "command_approval": {
            "risk_level": "low",
            "calls": [
                {"service": "light.turn_on", "target": {"entity_id": ["light.kitchen"]}},
                {"service": "lock.unlock", "target": {"entity_id": ["lock.front"]}},
            ],
        },
    }
    result = synthesize_approval_from_tool_log(llm_result, tool_log=None)
    assert result["command_approval"]["risk_level"] == "high"


def test_normalize_explicit_approval_reasons_align_by_call_index() -> None:
    """P3: the approval card renders ``risk_reasons`` strictly per call
    index. A mixed SAFE+REVIEW proposal with no reasons must yield a
    reasons list parallel to ``calls`` — empty slot for the SAFE call,
    the real reason on the REVIEW call. A compacted list would tag the
    SAFE row with the lock's reason and leave the lock unexplained.
    """
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    llm_result = {
        "intent": "command_approval",
        "response": "needs approval",
        "command_approval": {
            "risk_level": "low",
            "calls": [
                {"service": "light.turn_on", "target": {"entity_id": ["light.kitchen"]}},
                {"service": "lock.unlock", "target": {"entity_id": ["lock.front"]}},
            ],
        },
    }
    result = synthesize_approval_from_tool_log(llm_result, tool_log=None)
    reasons = result["command_approval"]["risk_reasons"]
    assert len(reasons) == 2
    assert reasons[0] == ""  # SAFE light — no reason
    assert "lock" in reasons[1].lower() or "physical" in reasons[1].lower()


def test_pending_approval_calls_handles_validate_action_too() -> None:
    """Regression: an LLM that calls ``validate_action`` first
    (sees ``requires_approval=True``) and then hedges in prose
    ("would you like me to go ahead?") used to leave the user
    without a card — the synthesizer only watched
    ``execute_command``. The card must surface either way,
    otherwise the user has to say "yes" to a question the
    integration was already going to answer with an approval
    prompt.

    Also asserts the dedup: a ``validate_action`` followed by
    ``execute_command`` for the same call produces ONE card.
    """
    from custom_components.selora_ai.llm_client.command_policy import (
        _pending_approval_calls_from_log,
    )

    tool_log = [
        {
            "tool": "validate_action",
            "arguments": {"service": "lock.unlock", "entity_id": "lock.front"},
            "result": {
                "valid": False,
                "requires_approval": True,
                "service": "lock.unlock",
                "risk_level": "high",
                "approval_reason": "Releases a physical lock — physical access risk.",
            },
        },
        {
            "tool": "execute_command",
            "arguments": {"service": "lock.unlock", "entity_id": "lock.front"},
            "result": {
                "valid": False,
                "requires_approval": True,
                "service": "lock.unlock",
                "risk_level": "high",
                "approval_reason": "Releases a physical lock — physical access risk.",
            },
        },
    ]
    pending = _pending_approval_calls_from_log(tool_log)
    assert len(pending) == 1, pending
    assert pending[0]["service"] == "lock.unlock"
    assert pending[0]["target"] == {"entity_id": ["lock.front"]}


def test_pending_approval_dedup_keys_on_data_payload() -> None:
    """P2: two ``notify.mobile_app_*`` calls share an empty entity set
    but carry different ``data`` messages. Keying dedup on service +
    entity set alone collapsed them into one card, so only the first
    notification was shown and executed after approval. The data
    payload must be part of the dedup key.
    """
    from custom_components.selora_ai.llm_client.command_policy import (
        _pending_approval_calls_from_log,
    )

    tool_log = [
        {
            "tool": "execute_command",
            "arguments": {
                "service": "notify.mobile_app_phone",
                "data": {"message": "first"},
            },
            "result": {
                "requires_approval": True,
                "service": "notify.mobile_app_phone",
                "risk_level": "low",
            },
        },
        {
            "tool": "execute_command",
            "arguments": {
                "service": "notify.mobile_app_phone",
                "data": {"message": "second"},
            },
            "result": {
                "requires_approval": True,
                "service": "notify.mobile_app_phone",
                "risk_level": "low",
            },
        },
    ]
    pending = _pending_approval_calls_from_log(tool_log)
    assert len(pending) == 2, pending
    messages = {p["data"]["message"] for p in pending}
    assert messages == {"first", "second"}


@pytest.mark.asyncio
async def test_normalize_explicit_approval_escalates_entity_aware_cover(
    hass: HomeAssistant,
) -> None:
    """P2: an explicit ``command_approval`` for ``cover.open_cover`` on a
    garage cover is SAFE-bucket per ``_classify_call``, so risk derivation
    used to leave it LOW with no physical-access reason — while the resolver
    still executes the high-risk action. Server-side derivation must
    escalate the badge to HIGH via the device_class.
    """
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    hass.states.async_set(
        "cover.garage", "closed", {"device_class": "garage"}
    )
    llm_result = {
        "intent": "command_approval",
        "response": "needs approval",
        "command_approval": {
            "risk_level": "low",
            "calls": [
                {
                    "service": "cover.open_cover",
                    "target": {"entity_id": ["cover.garage"]},
                },
            ],
        },
    }
    result = synthesize_approval_from_tool_log(llm_result, tool_log=None, hass=hass)
    proposal = result["command_approval"]
    assert proposal["risk_level"] == "high"
    reasons = " ".join(proposal.get("risk_reasons", []))
    assert "physical access" in reasons.lower()


@pytest.mark.asyncio
async def test_approval_store_per_entity_grant(hass: HomeAssistant) -> None:
    """Per-entity grants must NOT cover other entities of the same service,
    and the wildcard grant still covers every entity. Without this, the
    granularity the user just asked for would silently broaden — granting
    "unlock the front door" would unlock every door in the house.
    """
    from custom_components.selora_ai.approval_store import ApprovalStore

    store = ApprovalStore(hass)
    await store.async_load()

    # Per-entity grant: only the front door is approved.
    await store.async_grant_always(
        "lock.unlock",
        risk_level="high",
        granted_by_user_id="u",
        entity_id="lock.front_door",
    )
    assert store.is_approved("lock.unlock", entity_id="lock.front_door")
    assert not store.is_approved("lock.unlock", entity_id="lock.back_door")
    # Service wildcard query (no entity) must NOT see the per-entity grant
    # — otherwise apply_command_policy would treat the front-door grant
    # as a service-wide allow for any future unlock target.
    assert not store.is_approved("lock.unlock")

    # Now grant the wildcard. It covers EVERY entity, including
    # the previously unapproved back door.
    await store.async_grant_always(
        "lock.unlock",
        risk_level="high",
        granted_by_user_id="u",
    )
    assert store.is_approved("lock.unlock", entity_id="lock.back_door")
    assert store.is_approved("lock.unlock")

    # Revoking the per-entity grant must leave the wildcard intact.
    revoked = await store.async_revoke("lock.unlock:lock.front_door")
    assert revoked
    assert store.is_approved("lock.unlock", entity_id="lock.front_door")
    assert store.is_approved("lock.unlock")


def test_trust_gate_ignores_entity_markers_in_prose(hass) -> None:
    """SECURITY/UX regression: the unbacked-entity veto treated raw
    entity_ids inside ``[[entity:lock.front_door]]`` markers as
    matchable tokens. Users with ANY entity whose object_id contains
    "lock" / "entity" (any automation/script/sensor named accordingly
    — very common) tripped the veto on every successful unlock,
    flunking the trust gate and showing the bogus "no entity matched"
    stomp even though the tool actually fired.

    Markers are stripped before veto matching so only the
    user-visible prose contributes tokens.
    """
    from custom_components.selora_ai.llm_client.parsers import parse_streamed_response

    prose = (
        "Unlocking the Front Door.\n\n"
        "[[entity:lock.front_doorUnlocked Front Door.\n\n"
        "[[entities:lock.front_door]]"
    )
    tool_log = [
        {
            "tool": "execute_command",
            "arguments": {
                "service": "lock.unlock",
                "entity_id": "lock.front_door",
            },
            "result": {
                "executed": True,
                "service": "lock.unlock",
                "entity_ids": ["lock.front_door"],
                "states": [],
            },
        }
    ]
    # Snapshot has entities whose object_id contains "lock" — exactly
    # the case that used to trip the veto via marker bleed.
    entities = [
        {"entity_id": "lock.front_door", "state": "unlocked", "attributes": {}},
        {
            "entity_id": "automation.lock_doors_at_night",
            "state": "on",
            "attributes": {},
        },
        {
            "entity_id": "script.unlock_all_for_party",
            "state": "off",
            "attributes": {},
        },
    ]
    out = parse_streamed_response(prose, hass, entities=entities, tool_log=tool_log)
    assert out["intent"] == "answer"
    assert out.get("validation_error") != "no_matching_entity_for_command"
    assert "no entity clearly matched" not in out["response"]


def test_attempted_call_suppresses_unbacked_stomp(hass) -> None:
    """Regression: when the LLM emits action-confirmation prose AND a
    matching ``execute_command`` was attempted but the tool result has
    neither ``executed:True`` (action didn't fire) NOR
    ``requires_approval:True`` (synth path doesn't trigger) — e.g. a
    weird streaming partial result — the strict unbacked-action stomp
    used to fire with "no entity matched" even though the entity
    clearly did match (the tool referenced it).

    The attempted-call gate now sets ``suppressed_duplicate_command``
    when the prose verbs+entities line up with the tool arguments,
    bypassing the stomp.
    """
    from custom_components.selora_ai.llm_client.parsers import parse_streamed_response

    # User's actual buggy case: malformed markers, tool ran but result
    # shape doesn't include executed:True (e.g. older response shape
    # or partial streaming).
    prose = (
        "Unlocking the front door.\n\n"
        "[[entity:lock.front_door|Front Door"
        "Front door is now unlocked.\n\n"
        "[[entity:lock.front_door|Front Door]]"
    )
    tool_log = [
        {
            "tool": "execute_command",
            "arguments": {
                "service": "lock.unlock",
                "entity_id": "lock.front_door",
            },
            # Neither executed:True nor requires_approval:True
            "result": {"service": "lock.unlock"},
        }
    ]
    entities = [
        {"entity_id": "lock.front_door", "state": "unlocked", "attributes": {}},
    ]
    out = parse_streamed_response(prose, hass, entities=entities, tool_log=tool_log)
    assert out["intent"] == "answer"
    # Stomp MUST NOT have replaced the prose. The LLM's text (however
    # malformed) preserves what actually happened better than the
    # generic "no entity matched" message.
    assert "no entity clearly matched" not in out["response"]
    assert out.get("validation_error") != "no_matching_entity_for_command"


def test_apply_command_policy_carries_delay_into_approval() -> None:
    """SAFETY regression: a ``delayed_command`` containing a REVIEW
    call ("unlock the door in 10 minutes") used to convert into a
    plain ``command_approval`` and drop the delay metadata. The
    resolver then executed the calls immediately — turning "later"
    into "now" the moment the user tapped Allow.

    The proposal must carry ``original_intent``, ``delay_seconds``,
    and any ``scheduled_time`` so the resolver can route through
    the scheduler instead of ``hass.services.async_call``.
    """
    from custom_components.selora_ai.llm_client.command_policy import (
        apply_command_policy,
    )

    parsed = {
        "intent": "delayed_command",
        "response": "Unlocking the door in 10 minutes.",
        "delay_seconds": 600,
        "calls": [
            {
                "service": "lock.unlock",
                "target": {"entity_id": "lock.front"},
                "data": {},
            }
        ],
    }
    entities = [{"entity_id": "lock.front", "state": "locked", "attributes": {}}]
    result = apply_command_policy(parsed, entities)
    assert result["intent"] == "command_approval"
    proposal = result["command_approval"]
    assert proposal["original_intent"] == "delayed_command"
    assert proposal["delay_seconds"] == 600
    # And the per-call risk reason still aligns with the REVIEW call.
    assert any("physical lock" in r.lower() for r in proposal["risk_reasons"])


def test_apply_command_policy_preserves_call_order_in_proposal() -> None:
    """Regression: a mixed SAFE + REVIEW turn ("turn off the kitchen
    light, then unlock the door") used to bundle ALL pending REVIEW
    calls ahead of validated SAFE ones in the approval proposal.
    After the user approved, the resolver iterated ``approval.calls``
    in proposal order — so the door unlocked first and the light
    turned off afterward, inverting the user's stated order.
    """
    from custom_components.selora_ai.llm_client.command_policy import (
        apply_command_policy,
    )

    parsed = {
        "intent": "command",
        "response": "Two things.",
        "calls": [
            # ORDER MATTERS: light off FIRST, lock unlock SECOND.
            {
                "service": "light.turn_off",
                "target": {"entity_id": "light.kitchen"},
                "data": {},
            },
            {
                "service": "lock.unlock",
                "target": {"entity_id": "lock.front"},
                "data": {},
            },
        ],
    }
    entities = [
        {"entity_id": "light.kitchen", "state": "on", "attributes": {}},
        {"entity_id": "lock.front", "state": "locked", "attributes": {}},
    ]
    result = apply_command_policy(parsed, entities)
    assert result["intent"] == "command_approval"
    proposal_calls = result["command_approval"]["calls"]
    assert [c["service"] for c in proposal_calls] == [
        "light.turn_off",
        "lock.unlock",
    ], "Proposal must run light.turn_off BEFORE lock.unlock to honor the user's stated order"

    # And risk_reasons aligned 1:1 — empty for the SAFE light entry,
    # non-empty for the REVIEW lock entry.
    reasons = result["command_approval"]["risk_reasons"]
    assert len(reasons) == 2
    assert reasons[0] == ""  # light.turn_off carries no risk reason
    assert "physical lock" in reasons[1].lower()


def test_call_required_approval_distinguishes_safe_from_review() -> None:
    """SECURITY regression: when an approval bundles SAFE + REVIEW
    calls, only the REVIEW ones should land in the persistent grants
    store. Otherwise approving "unlock the door and open the blinds"
    with Always would persist a grant for ``cover.open_cover`` — and
    that grant would later let a garage-door open run without
    prompting, because the device_class elevation check honors
    standing grants before classifying.

    The helper must return True for static REVIEW services AND for
    SAFE services elevated by device_class; False for pure SAFE.
    """
    import unittest.mock as _mock

    from custom_components.selora_ai.llm_client.command_policy import (
        call_required_approval,
    )

    # No hass needed for non-elevated cases.
    assert (
        call_required_approval(
            None,
            {
                "service": "lock.unlock",
                "target": {"entity_id": ["lock.front"]},
                "data": {},
            },
        )
        is True
    )
    # Pure SAFE: harmless light switch, no elevation.
    assert (
        call_required_approval(
            None,
            {
                "service": "light.turn_on",
                "target": {"entity_id": ["light.kitchen"]},
                "data": {},
            },
        )
        is False
    )
    # Pure SAFE cover on a blind — no hass = no elevation check.
    assert (
        call_required_approval(
            None,
            {
                "service": "cover.open_cover",
                "target": {"entity_id": ["cover.blinds"]},
                "data": {},
            },
        )
        is False
    )
    # Elevated SAFE: cover.open_cover on a garage door. Needs hass to
    # resolve device_class.
    fake_hass = _mock.MagicMock()
    fake_state = _mock.MagicMock()
    fake_state.attributes = {"device_class": "garage"}
    fake_hass.states.get.return_value = fake_state
    assert (
        call_required_approval(
            fake_hass,
            {
                "service": "cover.open_cover",
                "target": {"entity_id": ["cover.garage_door"]},
                "data": {},
            },
        )
        is True
    )


def test_validate_review_call_requires_target_for_entity_services() -> None:
    """SECURITY regression: a REVIEW call for an entity-scoped service
    (lock.unlock, alarm_*, vacuum.*, water_heater.*) without an
    explicit entity_id used to validate as an empty target. HA falls
    back to applying entity services to EVERY entity in the domain
    when no target is supplied — so a single Allow click could unlock
    every door / disarm every alarm at once. Reject the proposal
    upfront unless the service is one of the legitimately targetless
    ones (notify.*, script.*, shell_command.*).
    """
    from custom_components.selora_ai.llm_client.command_policy import (
        _REVIEW_SERVICE_POLICIES,
        _validate_review_call,
    )

    # Entity-scoped: must reject missing target.
    entry = _REVIEW_SERVICE_POLICIES["lock"]["unlock"]
    bad, err = _validate_review_call(
        {"service": "lock.unlock", "target": {}, "data": {}}, entry
    )
    assert bad is None
    assert "requires an explicit entity_id" in err

    # Targetless: notify.* must continue to validate without entity_id.
    notify_entry = _REVIEW_SERVICE_POLICIES["notify"]["*"]
    ok, err = _validate_review_call(
        {
            "service": "notify.mobile_app_pixel",
            "target": {},
            "data": {"message": "ping"},
        },
        notify_entry,
    )
    assert err is None
    assert ok["service"] == "notify.mobile_app_pixel"


def test_pending_approval_calls_includes_elevated_cover() -> None:
    """Regression: when a tool-capable model calls execute_command on
    cover.open_cover targeting a garage door, validate_command_action
    returns requires_approval=True with the elevated risk metadata —
    but ``_classify_call("cover.open_cover")`` still returns "safe".
    The synthesizer must use the tool result's risk_level when the
    static REVIEW table doesn't cover the service, otherwise the
    approval card never appears and the user is stuck with a narrated
    "requires approval" message.
    """
    from custom_components.selora_ai.llm_client.command_policy import (
        _pending_approval_calls_from_log,
    )

    tool_log = [
        {
            "tool": "execute_command",
            "arguments": {
                "service": "cover.open_cover",
                "entity_id": "cover.garage_door",
            },
            "result": {
                "valid": False,
                "requires_approval": True,
                "service": "cover.open_cover",
                "risk_level": "high",
                "approval_reason": "Opens a garage door — physical access risk.",
            },
        }
    ]
    pending = _pending_approval_calls_from_log(tool_log)
    assert len(pending) == 1, pending
    entry = pending[0]
    assert entry["service"] == "cover.open_cover"
    assert entry["target"] == {"entity_id": ["cover.garage_door"]}
    assert entry["_risk_level"] == "high"
    assert "garage" in entry["_reason"]


def test_validate_safe_call_rejects_unsupported_data_key() -> None:
    """SECURITY regression: a crafted ``command_approval`` proposal could
    sneak a SAFE-bucket call past the resolver if we didn't reapply the
    SAFE policy at execution time. ``parse_architect_response`` accepts
    a model-supplied ``intent: "command_approval"`` straight from JSON,
    so the stored proposal can't be treated as already validated.

    Cover SAFE-shape checks here: bogus data key, off-domain target,
    too many entity_ids, unknown entity_id.
    """
    from custom_components.selora_ai.llm_client.command_policy import _validate_safe_call

    known = {"light.kitchen", "light.bedroom", "switch.lamp"}

    # Bogus data key — light.turn_on doesn't take "secret_flag".
    bad_data, err = _validate_safe_call(
        {
            "service": "light.turn_on",
            "target": {"entity_id": ["light.kitchen"]},
            "data": {"secret_flag": True},
        },
        known,
    )
    assert bad_data is None and "secret_flag" in err

    # Off-domain entity — switch.lamp can't be a light.turn_on target.
    cross, err = _validate_safe_call(
        {
            "service": "light.turn_on",
            "target": {"entity_id": ["switch.lamp"]},
            "data": {},
        },
        known,
    )
    assert cross is None and "outside the light domain" in err

    # Unknown entity — not in the allowlist snapshot.
    unknown, err = _validate_safe_call(
        {
            "service": "light.turn_on",
            "target": {"entity_id": ["light.does_not_exist"]},
            "data": {},
        },
        known,
    )
    assert unknown is None and "unknown entity_id" in err

    # Happy path — passes through with the validated shape.
    good, err = _validate_safe_call(
        {
            "service": "light.turn_on",
            "target": {"entity_id": ["light.kitchen"]},
            "data": {"brightness_pct": 80},
        },
        known,
    )
    assert err is None
    assert good["service"] == "light.turn_on"
    assert good["target"] == {"entity_id": ["light.kitchen"]}
    assert good["data"] == {"brightness_pct": 80}


def test_build_approval_result_message_tracks_per_call_success() -> None:
    """Regression: a multi-call approval with the same service repeated
    must not report failed calls as completed. Using a set of executed
    services (instead of the actually-fired indices) would dedupe and
    falsely mark every duplicate-service call as successful.
    """
    import unittest.mock as _mock

    from custom_components.selora_ai import _build_approval_result_message

    calls = [
        {
            "service": "lock.unlock",
            "target": {"entity_id": ["lock.front_door"]},
            "data": {},
        },
        {
            "service": "lock.unlock",
            "target": {"entity_id": ["lock.back_door"]},
            "data": {},
        },
    ]
    fake_hass = _mock.MagicMock()
    fake_hass.states.get.return_value = None

    # Only the first call fired (index 0). The second raised at
    # async_call time, so executed_indices stays {0}.
    text, _ids = _build_approval_result_message(fake_hass, calls, {0}, "once")
    assert "lock.front_door" in text
    # Critically: the back_door line MUST NOT appear in the message —
    # it didn't run, so reporting it as Unlocked would be a lie.
    assert "lock.back_door" not in text


@pytest.mark.asyncio
async def test_execute_command_no_target_review_service_through_approval(
    hass: HomeAssistant, setup_world
) -> None:
    """REVIEW services with no entity_id (notify.*, script.*, shell_command.*)
    must reach the approval-aware validator instead of being rejected
    upfront for missing entity_id. Otherwise a tool-capable model can
    never run an approved notify channel: it would always get back
    "entity_id must be a string or list of strings".
    """
    from custom_components.selora_ai.approval_store import ApprovalStore
    from custom_components.selora_ai.const import DOMAIN

    approval_store = ApprovalStore(hass)
    await approval_store.async_load()
    approval_store.grant_session("notify.mobile_app_pixel", session_id="sess-1")
    hass.data.setdefault(DOMAIN, {})["_approval_store"] = approval_store

    fired: list[dict] = []

    async def _ok(call):
        fired.append({"data": dict(call.data)})

    hass.services.async_register("notify", "mobile_app_pixel", _ok)

    result = await _tool_execute_command(
        hass,
        {
            "service": "notify.mobile_app_pixel",
            "data": {"message": "Hello from the test"},
            # NB: deliberately no entity_id — this used to be rejected
            # with "entity_id must be a string or list of strings".
        },
        session_id="sess-1",
    )
    assert result.get("executed") is True, result
    assert len(fired) == 1
    # And the dispatched service_data MUST NOT carry an empty
    # entity_id list — HA would treat it as an invalid target.
    assert "entity_id" not in fired[0]["data"]


@pytest.mark.asyncio
async def test_execute_command_garage_cover_requires_approval(
    hass: HomeAssistant, setup_world
) -> None:
    """A garage / gate / front door cover is the same trust model as a
    lock — opening one grants physical access. ``cover.open_cover``
    targeting an entity with device_class=garage must come back with
    ``requires_approval`` even though ``cover`` is in the SAFE
    allowlist for the other device classes (blinds, awnings).
    """
    hass.states.async_set(
        "cover.garage_door",
        "closed",
        {"friendly_name": "Garage Door", "device_class": "garage"},
    )
    result = await _tool_execute_command(
        hass,
        {"service": "cover.open_cover", "entity_id": "cover.garage_door"},
    )
    assert result["valid"] is False
    assert result.get("requires_approval") is True
    assert result.get("risk_level") == "high"


@pytest.mark.asyncio
async def test_execute_command_blind_cover_stays_safe(
    hass: HomeAssistant, setup_world
) -> None:
    """A blind / shade / awning is not a physical-access risk —
    ``cover.open_cover`` on those must continue to execute directly
    without an approval card. Otherwise every "open the bedroom blinds"
    request would prompt the user."""
    hass.states.async_set(
        "cover.bedroom_blinds",
        "closed",
        {"friendly_name": "Bedroom Blinds", "device_class": "blind"},
    )

    fired: list[dict] = []

    async def _ok(call):
        fired.append({"data": dict(call.data)})

    hass.services.async_register("cover", "open_cover", _ok)

    result = await _tool_execute_command(
        hass,
        {"service": "cover.open_cover", "entity_id": "cover.bedroom_blinds"},
    )
    assert result.get("executed") is True, result
    assert result.get("requires_approval") is not True
    assert len(fired) == 1


@pytest.mark.asyncio
async def test_execute_command_honors_session_grant(
    hass: HomeAssistant, setup_world
) -> None:
    """After the user grants Session-scope approval on lock.unlock, a
    subsequent ``execute_command`` for the same service must execute
    immediately instead of returning requires_approval. Without ``hass``
    + ``session_id`` plumbed through, the approval store wasn't being
    consulted on the tool path and the user got prompted every turn.
    """
    from custom_components.selora_ai.approval_store import ApprovalStore
    from custom_components.selora_ai.const import DOMAIN

    approval_store = ApprovalStore(hass)
    await approval_store.async_load()
    approval_store.grant_session("lock.unlock", session_id="sess-1")
    hass.data.setdefault(DOMAIN, {})["_approval_store"] = approval_store

    fired: list[dict] = []

    async def _ok(call):
        fired.append({"data": dict(call.data)})

    hass.services.async_register("lock", "unlock", _ok)

    result = await _tool_execute_command(
        hass,
        {"service": "lock.unlock", "entity_id": "lock.front"},
        session_id="sess-1",
    )
    assert result.get("executed") is True
    assert len(fired) == 1
    # And a DIFFERENT session must still be prompted — grants don't leak
    # across conversations.
    result2 = await _tool_execute_command(
        hass,
        {"service": "lock.unlock", "entity_id": "lock.front"},
        session_id="sess-OTHER",
    )
    assert result2.get("requires_approval") is True


@pytest.mark.asyncio
async def test_execute_command_service_failure_returns_error(
    hass: HomeAssistant, setup_world
) -> None:
    """A raised service exception is surfaced as executed:false."""

    async def _boom(call):
        raise RuntimeError("device offline")

    hass.services.async_register("light", "turn_on", _boom)

    result = await _tool_execute_command(
        hass,
        {"service": "light.turn_on", "entity_id": "light.kitchen_island"},
    )
    assert result["executed"] is False
    assert "device offline" in result["error"]


# ── search_entities ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_entities_matches_friendly_name(
    hass: HomeAssistant, setup_world
) -> None:
    result = await _tool_search_entities(hass, {"query": "kitchen island"})
    assert result["count"] >= 1
    top = result["matches"][0]
    assert top["entity_id"] == "light.kitchen_island"
    assert top["score"] >= 2


@pytest.mark.asyncio
async def test_search_entities_matches_alias(
    hass: HomeAssistant, setup_world
) -> None:
    """The aliases set should be searchable ('reading lamp' → bedroom_lamp)."""
    result = await _tool_search_entities(hass, {"query": "reading lamp"})
    ids = [m["entity_id"] for m in result["matches"]]
    assert "light.bedroom_lamp" in ids


@pytest.mark.asyncio
async def test_search_entities_domain_filter(
    hass: HomeAssistant, setup_world
) -> None:
    result = await _tool_search_entities(
        hass, {"query": "kitchen", "domain": "switch"}
    )
    # Coffee maker is in switch domain but doesn't mention "kitchen" — should miss.
    assert result["count"] == 0


@pytest.mark.asyncio
async def test_search_entities_limit(
    hass: HomeAssistant, setup_world
) -> None:
    result = await _tool_search_entities(hass, {"query": "light", "limit": 1})
    assert len(result["matches"]) <= 1


@pytest.mark.asyncio
async def test_search_entities_missing_query(hass: HomeAssistant) -> None:
    result = await _tool_search_entities(hass, {})
    assert "error" in result


# ── get_entity_history ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_entity_history_returns_deduped_changes(
    hass: HomeAssistant, setup_world
) -> None:
    """The handler deduplicates consecutive identical states."""
    from datetime import UTC, datetime, timedelta

    class _FakeState:
        def __init__(self, state: str, when: datetime) -> None:
            self.state = state
            self.last_changed = when

    now = datetime.now(UTC)
    fake_states = {
        "light.kitchen_island": [
            _FakeState("off", now - timedelta(hours=2)),
            _FakeState("off", now - timedelta(hours=1, minutes=55)),  # dedup
            _FakeState("on", now - timedelta(hours=1)),
            _FakeState("off", now - timedelta(minutes=30)),
        ]
    }

    fake_instance = AsyncMock()
    fake_instance.async_add_executor_job = AsyncMock(return_value=fake_states)

    with (
        patch(
            "homeassistant.components.recorder.get_instance",
            return_value=fake_instance,
        ),
        patch(
            "homeassistant.components.recorder.history.get_significant_states",
            return_value=fake_states,
        ),
    ):
        result = await _tool_get_entity_history(
            hass,
            {"entity_id": "light.kitchen_island", "hours": 3},
        )

    assert result["entity_id"] == "light.kitchen_island"
    assert result["count"] == 3
    assert [c["state"] for c in result["changes"]] == ["off", "on", "off"]


@pytest.mark.asyncio
async def test_get_entity_history_unknown_entity(hass: HomeAssistant) -> None:
    result = await _tool_get_entity_history(hass, {"entity_id": "light.nope"})
    assert "error" in result


@pytest.mark.asyncio
async def test_get_entity_history_clamps_hours(
    hass: HomeAssistant, setup_world
) -> None:
    """hours is clamped to [0.25, 24]."""
    fake_instance = AsyncMock()
    fake_instance.async_add_executor_job = AsyncMock(return_value={})

    with patch(
        "homeassistant.components.recorder.get_instance",
        return_value=fake_instance,
    ):
        result = await _tool_get_entity_history(
            hass,
            {"entity_id": "light.kitchen_island", "hours": 9999},
        )
    assert result["hours"] == 24


# ── eval_template ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eval_template_renders(hass: HomeAssistant, setup_world) -> None:
    result = await _tool_eval_template(
        hass, {"template": "{{ states('light.kitchen_island') }}"}
    )
    assert result["result"] == "off"


@pytest.mark.asyncio
async def test_eval_template_arithmetic(hass: HomeAssistant) -> None:
    result = await _tool_eval_template(hass, {"template": "{{ 1 + 2 }}"})
    assert result["result"] == "3"


@pytest.mark.asyncio
async def test_eval_template_syntax_error(hass: HomeAssistant) -> None:
    result = await _tool_eval_template(hass, {"template": "{{ states("})
    assert "error" in result


@pytest.mark.asyncio
async def test_eval_template_missing(hass: HomeAssistant) -> None:
    result = await _tool_eval_template(hass, {})
    assert "error" in result


@pytest.mark.asyncio
async def test_eval_template_length_cap(hass: HomeAssistant) -> None:
    payload = "{{ 'a' }}" + "x" * 2000
    result = await _tool_eval_template(hass, {"template": payload})
    assert "error" in result
    assert "character limit" in result["error"]


# ── large_context_only gating ────────────────────────────────────────────────


def test_large_context_only_tools_are_marked() -> None:
    """search_entities, get_entity_history, eval_template skip low-context providers."""
    by_name = {t.name: t for t in CHAT_TOOLS}
    assert by_name["search_entities"].large_context_only is True
    assert by_name["get_entity_history"].large_context_only is True
    assert by_name["eval_template"].large_context_only is True


def test_universal_tools_are_not_gated() -> None:
    """execute_command and activate_scene must be available to all providers."""
    by_name = {t.name: t for t in CHAT_TOOLS}
    assert by_name["execute_command"].large_context_only is False
    assert by_name["activate_scene"].large_context_only is False
    assert by_name["get_entity_state"].large_context_only is False
    assert by_name["validate_action"].large_context_only is False


def test_provider_filter_drops_large_only_for_low_context() -> None:
    """Simulate the filter in LLMClient._get_tools_for_provider."""
    low_ctx_tools = [t for t in CHAT_TOOLS if not t.large_context_only]
    names = {t.name for t in low_ctx_tools}
    assert "search_entities" not in names
    assert "get_entity_history" not in names
    assert "eval_template" not in names
    assert "execute_command" in names
    assert "activate_scene" in names


# ── data parameter exposed on chat-facing schema ────────────────────────────


def test_execute_command_schema_includes_data_param() -> None:
    """Tool-capable providers must see a 'data' param so they emit brightness/temperature/etc."""
    by_name = {t.name: t for t in CHAT_TOOLS}
    params = {p.name: p for p in by_name["execute_command"].params}
    assert "data" in params
    assert params["data"].type == "object"


def test_validate_action_schema_includes_data_param() -> None:
    by_name = {t.name: t for t in CHAT_TOOLS}
    params = {p.name: p for p in by_name["validate_action"].params}
    assert "data" in params
    assert params["data"].type == "object"


def test_execute_command_anthropic_schema_has_data() -> None:
    """Anthropic serializer must expose 'data' as an object property."""
    by_name = {t.name: t for t in CHAT_TOOLS}
    schema = by_name["execute_command"].to_anthropic()["input_schema"]
    assert "data" in schema["properties"]
    assert schema["properties"]["data"]["type"] == "object"
    # data is optional — service and entity_id are the only required fields
    assert "data" not in schema.get("required", [])


def test_execute_command_openai_schema_has_data() -> None:
    """OpenAI/Ollama serializer must expose 'data' as an object property."""
    by_name = {t.name: t for t in CHAT_TOOLS}
    schema = by_name["execute_command"].to_openai()["function"]["parameters"]
    assert "data" in schema["properties"]
    assert schema["properties"]["data"]["type"] == "object"


@pytest.mark.asyncio
async def test_execute_command_data_param_reaches_service(
    hass: HomeAssistant, setup_world
) -> None:
    """End-to-end: brightness_pct in 'data' is forwarded to hass.services.async_call."""
    calls: list[dict] = []

    async def _capture(call):
        calls.append({"domain": call.domain, "service": call.service, "data": dict(call.data)})

    hass.services.async_register("light", "turn_on", _capture)

    result = await _tool_execute_command(
        hass,
        {
            "service": "light.turn_on",
            "entity_id": "light.kitchen_island",
            "data": {"brightness_pct": 50},
        },
    )
    assert result["executed"] is True
    assert calls[0]["data"]["brightness_pct"] == 50


@pytest.mark.asyncio
async def test_execute_command_accepts_scene_entity(
    hass: HomeAssistant, setup_world
) -> None:
    """Regression: scene.turn_on is in the safe-command allowlist, but
    scenes aren't in COLLECTOR_DOMAINS so _collect_entity_states omits
    them. execute_command must augment its allowlist with scene-domain
    states so a documented call like scene.turn_on(scene.movie_night)
    is accepted.
    """
    calls: list[dict] = []

    async def _capture(call):
        calls.append({"service": call.service, "data": dict(call.data)})

    hass.services.async_register("scene", "turn_on", _capture)

    result = await _tool_execute_command(
        hass,
        {"service": "scene.turn_on", "entity_id": "scene.movie_night"},
    )
    assert result["executed"] is True
    assert calls[0]["service"] == "turn_on"
    assert calls[0]["data"]["entity_id"] == ["scene.movie_night"]


@pytest.mark.asyncio
async def test_execute_command_rejects_unavailable_scene(
    hass: HomeAssistant, setup_world
) -> None:
    """Unavailable scenes must still be rejected — mirrors the snapshot
    filter that skips unavailable/unknown states.
    """
    hass.states.async_set("scene.movie_night", "unavailable")
    result = await _tool_execute_command(
        hass,
        {"service": "scene.turn_on", "entity_id": "scene.movie_night"},
    )
    assert result.get("valid") is False
    assert any("not known to Home Assistant" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_execute_command_rejects_non_actionable_entity(
    hass: HomeAssistant, setup_world
) -> None:
    """Regression: an entity present in hass.states but filtered out of
    _collect_entity_states (e.g. unavailable) must not be controllable
    via the tool, even though _COMMAND_SERVICE_POLICIES would otherwise
    allow the service. Mirrors the JSON path which only sees the filtered
    entity snapshot.
    """
    # The fixture has light.kitchen_island in "off" state. Drop it into
    # an "unavailable" state — _collect_entity_states skips unavailable
    # entities, so the tool's allowlist should reject it.
    hass.states.async_set("light.kitchen_island", "unavailable")

    result = await _tool_execute_command(
        hass,
        {"service": "light.turn_on", "entity_id": "light.kitchen_island"},
    )
    assert result.get("valid") is False
    assert any("not known to Home Assistant" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_execute_command_data_param_climate_set_temperature(
    hass: HomeAssistant, setup_world
) -> None:
    """climate.set_temperature accepts temperature + hvac_mode via 'data'."""
    from homeassistant.helpers import entity_registry as er

    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("climate", "test", "thermo_uid", suggested_object_id="thermostat")
    hass.states.async_set("climate.thermostat", "heat", {"friendly_name": "Thermo"})

    calls: list[dict] = []

    async def _capture(call):
        calls.append({"service": call.service, "data": dict(call.data)})

    hass.services.async_register("climate", "set_temperature", _capture)

    result = await _tool_execute_command(
        hass,
        {
            "service": "climate.set_temperature",
            "entity_id": "climate.thermostat",
            "data": {"temperature": 21, "hvac_mode": "heat"},
        },
    )
    assert result["executed"] is True
    assert calls[0]["data"]["temperature"] == 21
    assert calls[0]["data"]["hvac_mode"] == "heat"
