"""The selora_chat MCP tool, and the write path a refinement needs.

`_tool_chat` called `architect_chat(message=…, refining_automation_id=…)`: two
keywords the method does not have, no `entities` (which is positional and
required), and `existing_automations` as bare alias strings where the prompt
builder reads records. Every call raised TypeError before reaching a provider,
so the tool this server exists to expose never worked. The signature is pinned
here with autospec — a renamed or reordered parameter fails the test rather
than the tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
import pytest
import yaml

from custom_components.selora_ai.llm_client.client import LLMClient
from custom_components.selora_ai.mcp_server import _tool_chat, _tool_create_automation

SELORA_ENTRY = {
    "id": "selora_ai_aaa",
    "alias": "Aqua Rite Schedule",
    "description": "Turns the plug on Thursdays and Fridays",
    "triggers": [{"platform": "time", "at": "00:00:00"}],
    "conditions": [],
    "actions": [{"action": "switch.turn_on", "target": {"entity_id": "switch.plug"}}],
    "mode": "single",
}
HAND_WRITTEN_ENTRY = {
    "id": "my_own_automation",
    "alias": "Morning Routine",
    "triggers": [{"platform": "time", "at": "06:30:00"}],
    "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}],
}

PROPOSAL_YAML = (
    "alias: Aqua Rite Schedule\n"
    "triggers:\n- platform: time\n  at: 07:00:00\n"
    "actions:\n- action: switch.turn_on\n  target:\n    entity_id: switch.plug\n"
)


def _write_automations(hass: HomeAssistant, entries: list[dict[str, Any]]) -> Path:
    path = Path(hass.config.config_dir) / "automations.yaml"
    path.write_text(yaml.safe_dump(entries), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def plug(hass: HomeAssistant) -> None:
    """The entity every fixture automation targets. validate_automation_payload
    rejects an unknown entity_id, so the payload has to name a real one."""
    hass.states.async_set("switch.plug", "off", {"friendly_name": "Aqua Rite Plug"})


@pytest.fixture
def llm(hass: HomeAssistant) -> LLMClient:
    from custom_components.selora_ai.providers import create_provider

    return LLMClient(hass, provider=create_provider("anthropic", hass, api_key="test-key"))


async def _chat(
    hass: HomeAssistant,
    llm: LLMClient,
    arguments: dict[str, Any],
    architect_result: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], AsyncMock]:
    """Run the tool against a stubbed architect. Returns (result, the stub).

    autospec binds every call against the real ``architect_chat`` signature, so
    a keyword the method does not accept raises here.
    """
    result_payload = architect_result or {"intent": "answer", "response": "ok"}
    with (
        patch("custom_components.selora_ai.mcp_server._get_llm", return_value=llm),
        patch.object(
            LLMClient, "architect_chat", autospec=True, return_value=result_payload
        ) as stub,
    ):
        result = await _tool_chat(hass, arguments)
    return result, stub


# ── The call itself ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_reaches_the_architect(hass: HomeAssistant, llm: LLMClient) -> None:
    result, stub = await _chat(hass, llm, {"message": "which lights are on?"})
    assert result["response"] == "ok"
    assert result["session_id"]
    stub.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_architect_gets_the_home_it_needs(hass: HomeAssistant, llm: LLMClient) -> None:
    hass.states.async_set("automation.existing", "on", {"friendly_name": "Existing", "id": "e1"})
    _, stub = await _chat(hass, llm, {"message": "add a rule"})
    _self, message, entities = stub.await_args.args
    kwargs = stub.await_args.kwargs
    assert message == "add a rule"
    # entities is positional-and-required; existing_automations is read as
    # records (alias + state), not as strings.
    assert isinstance(entities, list)
    records = kwargs["existing_automations"]
    assert records and all(isinstance(r, dict) for r in records)
    assert records[0]["alias"] == "Existing"


@pytest.mark.asyncio
async def test_a_missing_message_never_reaches_the_llm(hass: HomeAssistant, llm: LLMClient) -> None:
    result, stub = await _chat(hass, llm, {"message": "   "})
    assert "error" in result
    stub.assert_not_awaited()


# ── refine_automation_id ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_refine_target_is_fed_as_its_current_yaml(
    hass: HomeAssistant, llm: LLMClient
) -> None:
    _write_automations(hass, [SELORA_ENTRY])
    _, stub = await _chat(
        hass,
        llm,
        {"message": "change the time to 7am", "refine_automation_id": "selora_ai_aaa"},
    )
    alias, yaml_text = stub.await_args.kwargs["refining_context"]
    assert alias == "Aqua Rite Schedule"
    # What the home is running, not what the session last said about it.
    assert "at: 00:00:00" in yaml_text
    # The write path owns the id, and a model that echoed it back would have it
    # stripped anyway.
    assert "\nid:" not in yaml_text and not yaml_text.startswith("id:")


@pytest.mark.asyncio
async def test_an_unresolvable_refine_target_is_refused(
    hass: HomeAssistant, llm: LLMClient
) -> None:
    # Ignoring it would turn "change the time to 7am" into a second automation
    # beside the one the caller meant to edit.
    _write_automations(hass, [SELORA_ENTRY])
    result, stub = await _chat(
        hass, llm, {"message": "change the time", "refine_automation_id": "selora_ai_gone"}
    )
    assert "not found" in result["error"]
    stub.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unresolvable_target_leaves_no_empty_session(
    hass: HomeAssistant, llm: LLMClient
) -> None:
    """This is the refusal an agent hits repeatedly — a stale or invented id —
    and a session created before the check is left empty in the user's sidebar,
    eventually evicting real conversations under the store's cap."""
    from custom_components.selora_ai.mcp_server import _get_conv_store

    _write_automations(hass, [SELORA_ENTRY])
    result, _stub = await _chat(
        hass, llm, {"message": "change the time", "refine_automation_id": "selora_ai_gone"}
    )
    assert "not found" in result["error"]
    assert await _get_conv_store(hass).list_sessions() == []


@pytest.mark.asyncio
async def test_a_local_model_cannot_refine_at_all(hass: HomeAssistant) -> None:
    """The low-context prompt never carries the automation, and this tool has no
    confirmation card between the revision and the write — the caller is told to
    pass the id straight to selora_create_automation, which would replace the
    automation with a rule composed from scratch."""
    from custom_components.selora_ai.providers import create_provider

    local = LLMClient(hass, provider=create_provider("selora_local", hass))
    assert not local.shows_automation_reference

    _write_automations(hass, [SELORA_ENTRY])
    result, stub = await _chat(
        hass,
        local,
        {"message": "change the time to 7am", "refine_automation_id": "selora_ai_aaa"},
    )
    assert "cannot be refined" in result["error"]
    assert "selora_get_automation" in result["error"]
    stub.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_hand_written_target_is_refused_before_the_work(
    hass: HomeAssistant, llm: LLMClient
) -> None:
    """selora_create_automation will not replace a non-Selora automation, so
    refining one spends a turn on a revision the instructed write path then
    rejects — and selora_list_automations returns every yaml automation, so
    naming one is an easy mistake to make."""
    _write_automations(hass, [HAND_WRITTEN_ENTRY])
    result, stub = await _chat(
        hass, llm, {"message": "change the time", "refine_automation_id": "my_own_automation"}
    )
    assert "not created by Selora AI" in result["error"]
    stub.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_refined_proposal_reports_where_it_should_be_written(
    hass: HomeAssistant, llm: LLMClient
) -> None:
    _write_automations(hass, [SELORA_ENTRY])
    result, _stub = await _chat(
        hass,
        llm,
        {"message": "change the time to 7am", "refine_automation_id": "selora_ai_aaa"},
        {
            "intent": "automation",
            "response": "Updated.",
            "automation": {"alias": "Aqua Rite Schedule"},
            "automation_yaml": PROPOSAL_YAML,
        },
    )
    assert result["refine_automation_id"] == "selora_ai_aaa"
    assert result["automation_yaml"] == PROPOSAL_YAML


@pytest.mark.asyncio
async def test_the_pending_proposal_keeps_its_target_for_the_panel(
    hass: HomeAssistant, llm: LLMClient
) -> None:
    """Sessions are shared with the panel, so a card this tool leaves pending is
    one the user can open and accept there — and the panel reads the target off
    the stored message, not off this tool's return value."""
    from custom_components.selora_ai.mcp_server import _get_conv_store

    _write_automations(hass, [SELORA_ENTRY])
    result, _stub = await _chat(
        hass,
        llm,
        {"message": "change the time to 7am", "refine_automation_id": "selora_ai_aaa"},
        {
            "intent": "automation",
            "response": "Updated.",
            "automation": {"alias": "Aqua Rite Schedule"},
            "automation_yaml": PROPOSAL_YAML,
        },
    )

    session = await _get_conv_store(hass).get_session(result["session_id"])
    assert session is not None
    assistant = session["messages"][-1]
    assert assistant["automation_status"] == "pending"
    assert assistant["refining_automation_id"] == "selora_ai_aaa"


@pytest.mark.asyncio
async def test_an_mcp_follow_up_resolves_by_alias(hass: HomeAssistant, llm: LLMClient) -> None:
    """A model that keeps the automation's name but omits the claim is the
    ordinary case, and the caller has no id of its own here — so without the
    shared claim-or-alias resolution the documented flow sends the agent to
    selora_create_automation with no automation_id, which duplicates."""
    from custom_components.selora_ai.mcp_server import _get_conv_store

    _write_automations(hass, [SELORA_ENTRY])
    store = _get_conv_store(hass)
    session = await store.create_session()
    await store.append_message(session["id"], "assistant", "saved it")
    await store.set_automation_status(session["id"], 0, "saved", automation_id="selora_ai_aaa")

    result, _stub = await _chat(
        hass,
        llm,
        {"message": "change the time to 7am", "session_id": session["id"]},
        {
            "intent": "automation",
            "response": "Updated.",
            "automation": {"alias": "Aqua Rite Schedule"},
            "automation_yaml": PROPOSAL_YAML,
        },
    )

    assert result["refine_automation_id"] == "selora_ai_aaa"
    reopened = await store.get_session(session["id"])
    assert reopened is not None
    assert reopened["messages"][-1]["refining_automation_id"] == "selora_ai_aaa"


@pytest.mark.asyncio
async def test_a_plain_mcp_proposal_stores_no_target(hass: HomeAssistant, llm: LLMClient) -> None:
    from custom_components.selora_ai.mcp_server import _get_conv_store

    result, _stub = await _chat(
        hass,
        llm,
        {"message": "make me an automation"},
        {
            "intent": "automation",
            "response": "Here you go.",
            "automation": {"alias": "Aqua Rite Schedule"},
            "automation_yaml": PROPOSAL_YAML,
        },
    )

    session = await _get_conv_store(hass).get_session(result["session_id"])
    assert session is not None
    assert "refining_automation_id" not in session["messages"][-1]


@pytest.mark.asyncio
async def test_a_claimed_target_outside_the_session_is_not_reported(
    hass: HomeAssistant, llm: LLMClient
) -> None:
    # The claim is the model quoting reference text back; a session that saved
    # nothing gives it nothing to quote.
    result, _stub = await _chat(
        hass,
        llm,
        {"message": "make one"},
        {
            "intent": "automation",
            "response": "Here you go.",
            "automation": {"alias": "Aqua Rite Schedule"},
            "automation_yaml": PROPOSAL_YAML,
            "refine_automation_id": "selora_ai_somewhere_else",
        },
    )
    assert "refine_automation_id" not in result


# ── The write path a refinement needs ────────────────────────────────


@pytest.mark.asyncio
async def test_create_replaces_in_place_when_given_an_id(hass: HomeAssistant) -> None:
    _write_automations(hass, [SELORA_ENTRY])
    update = AsyncMock(return_value=True)
    create = AsyncMock()
    with (
        patch("custom_components.selora_ai.automation_utils.async_update_automation", update),
        patch("custom_components.selora_ai.automation_utils.async_create_automation", create),
    ):
        result = await _tool_create_automation(
            hass, {"yaml": PROPOSAL_YAML, "automation_id": "selora_ai_aaa"}
        )
    assert result["status"] == "updated"
    assert result["automation_id"] == "selora_ai_aaa"
    create.assert_not_awaited()
    assert update.await_args.args[1] == "selora_ai_aaa"


@pytest.mark.asyncio
async def test_replacing_an_unknown_automation_is_refused(hass: HomeAssistant) -> None:
    _write_automations(hass, [SELORA_ENTRY])
    result = await _tool_create_automation(
        hass, {"yaml": PROPOSAL_YAML, "automation_id": "selora_ai_gone"}
    )
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_a_hand_written_automation_is_not_rewritten(hass: HomeAssistant) -> None:
    # async_update_automation re-validates through the proposal validator,
    # which a hand-written automation's YAML need not satisfy.
    _write_automations(hass, [HAND_WRITTEN_ENTRY])
    result = await _tool_create_automation(
        hass, {"yaml": PROPOSAL_YAML, "automation_id": "my_own_automation"}
    )
    assert "not created by Selora AI" in result["error"]


@pytest.mark.asyncio
async def test_a_replacement_that_raises_the_risk_says_it_was_disabled(
    hass: HomeAssistant,
) -> None:
    """The gate inside the update forces a newly-elevated automation off.
    Reporting `updated` alone tells the caller it is still running, while the
    tool promises a replacement keeps its enabled state."""
    hass.services.async_register("automation", "reload", lambda call: None)
    hass.services.async_register("shell_command", "wipe", lambda call: None)
    _write_automations(hass, [{**SELORA_ENTRY, "initial_state": True}])

    risky = (
        "alias: Aqua Rite Schedule\n"
        "triggers:\n- platform: time\n  at: 07:00:00\n"
        "actions:\n- action: shell_command.wipe\n"
    )
    result = await _tool_create_automation(hass, {"yaml": risky, "automation_id": "selora_ai_aaa"})

    assert result["status"] == "updated"
    assert result["forced_disabled"] is True
    assert "elevated-risk" in result["note"]


@pytest.mark.asyncio
async def test_a_running_automation_off_by_boot_override_still_reports_it(
    hass: HomeAssistant,
) -> None:
    """Boot override already False, but live after a manual toggle: the gate
    leaves it off by skipping the restore rather than by writing anything, so
    the file looks unchanged and only the updater knows what it did."""
    hass.services.async_register("automation", "reload", lambda call: None)
    hass.services.async_register("shell_command", "wipe", lambda call: None)
    _write_automations(hass, [{**SELORA_ENTRY, "initial_state": False}])
    hass.states.async_set("automation.aqua", "on", {"id": "selora_ai_aaa"})

    risky = (
        "alias: Aqua Rite Schedule\n"
        "triggers:\n- platform: time\n  at: 07:00:00\n"
        "actions:\n- action: shell_command.wipe\n"
    )
    result = await _tool_create_automation(hass, {"yaml": risky, "automation_id": "selora_ai_aaa"})

    assert result["forced_disabled"] is True


@pytest.mark.asyncio
async def test_an_ordinary_replacement_reports_no_forced_disable(
    hass: HomeAssistant,
) -> None:
    hass.services.async_register("automation", "reload", lambda call: None)
    _write_automations(hass, [{**SELORA_ENTRY, "initial_state": True}])

    result = await _tool_create_automation(
        hass, {"yaml": PROPOSAL_YAML, "automation_id": "selora_ai_aaa"}
    )

    assert result["status"] == "updated"
    assert "forced_disabled" not in result


@pytest.mark.asyncio
async def test_creating_without_an_id_still_creates(hass: HomeAssistant) -> None:
    _write_automations(hass, [])
    create = AsyncMock(return_value={"success": True, "automation_id": "selora_ai_new"})
    with patch("custom_components.selora_ai.automation_utils.async_create_automation", create):
        result = await _tool_create_automation(hass, {"yaml": PROPOSAL_YAML})
    assert result["status"] == "created"
    assert result["automation_id"] == "selora_ai_new"
