"""A follow-up change edits the automation just saved instead of duplicating it.

"Create an automation to turn the plug on Thursdays and Fridays" followed by
"change the time to 7am" produces two ordinary proposals — nothing in the
automation payload marks the second as replacing the first — so accepting both
wrote two automations.yaml entries under one alias. Home Assistant loads both,
the health check reports the pair, and both keep running.

Two halves: the model is handed the automations this session saved (id, alias,
current YAML) so it can edit the real thing and name which one it is editing,
and the write target is resolved from that claim or from the alias. Both halves
read the automation off disk, never off the chat message — the message records
what was PROPOSED, and the user can edit a proposal before accepting it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
import pytest
import yaml

from custom_components.selora_ai import (
    _automation_reference_context,
    _find_refining_automation_id,
    _find_session_saved_automation_ids,
    _find_session_saved_automations,
    _resolve_proposal_write_target,
)
from custom_components.selora_ai.automation_utils import async_yaml_automation_snapshots
from custom_components.selora_ai.llm_client.parsers import (
    _humanize_description_entity_ids,
    _pop_refine_automation_id,
)

AQUA_YAML = "alias: Aqua Rite Schedule\ndescription: Turns the plug on\n"


def _saved_message(automation_id: str, alias: str, yaml_text: str = AQUA_YAML) -> dict[str, Any]:
    return {
        "role": "assistant",
        "automation_status": "saved",
        "automation_id": automation_id,
        "automation": {"alias": alias},
        "automation_yaml": yaml_text,
    }


def _entry(automation_id: str, alias: str, at: str = "00:00:00") -> dict[str, Any]:
    return {
        "id": automation_id,
        "alias": alias,
        "description": f"Runs {alias}",
        "triggers": [{"platform": "time", "at": at}],
        "actions": [{"action": "switch.turn_on", "target": {"entity_id": "switch.plug"}}],
    }


def _write_automations(hass: HomeAssistant, entries: list[dict[str, Any]]) -> Path:
    path = Path(hass.config.config_dir) / "automations.yaml"
    path.write_text(yaml.safe_dump(entries), encoding="utf-8")
    return path


# ── Session bookkeeping ──────────────────────────────────────────────


def test_saved_ids_are_oldest_first() -> None:
    messages = [
        _saved_message("selora_ai_aaa", "Aqua Rite Schedule"),
        _saved_message("selora_ai_bbb", "Porch Lights"),
    ]
    assert _find_session_saved_automation_ids(None, messages) == [
        "selora_ai_aaa",
        "selora_ai_bbb",
    ]


def test_a_refined_automation_is_listed_once_at_its_newest_position() -> None:
    messages = [
        _saved_message("selora_ai_aaa", "Aqua Rite Schedule"),
        _saved_message("selora_ai_bbb", "Porch Lights"),
        _saved_message("selora_ai_aaa", "Aqua Rite Schedule"),
    ]
    # Re-saving moves it to the end of the scan order, so the most recently
    # touched automation is the first candidate a follow-up is matched against.
    assert _find_session_saved_automation_ids(None, messages) == [
        "selora_ai_bbb",
        "selora_ai_aaa",
    ]


def test_unsaved_and_idless_proposals_are_not_candidates() -> None:
    messages = [
        {"automation_status": "pending", "automation": {"alias": "Never Accepted"}},
        {"automation_status": "declined", "automation_id": "x", "automation": {"alias": "No"}},
        {"automation_status": "saved", "automation": {"alias": "No id"}},
    ]
    assert _find_session_saved_automation_ids(None, messages) == []


def test_an_id_survives_its_message_being_pruned() -> None:
    """``append_message`` keeps the first message and the latest 99, so in a
    long session the message that recorded the save is gone — and an id that
    disappears is a follow-up that creates a duplicate."""
    session = {"saved_automations": ["selora_ai_aaa"]}
    assert _find_session_saved_automation_ids(session, []) == ["selora_ai_aaa"]


def test_the_index_and_the_retained_messages_are_unioned() -> None:
    # The index is only written from the moment it shipped, so a session that
    # saved automations before that still has them in its messages. Reading
    # either source alone drops half of such a session.
    session = {"saved_automations": ["selora_ai_new"]}
    messages = [_saved_message("selora_ai_old", "Older One")]
    assert _find_session_saved_automation_ids(session, messages) == [
        "selora_ai_old",
        "selora_ai_new",
    ]


def test_an_id_in_both_sources_is_listed_once_newest_last() -> None:
    session = {"saved_automations": ["selora_ai_bbb", "selora_ai_aaa"]}
    messages = [_saved_message("selora_ai_aaa", "Aqua Rite Schedule")]
    assert _find_session_saved_automation_ids(session, messages) == [
        "selora_ai_bbb",
        "selora_ai_aaa",
    ]


def test_a_malformed_index_is_ignored() -> None:
    messages = [_saved_message("selora_ai_aaa", "Aqua Rite Schedule")]
    assert _find_session_saved_automation_ids({"saved_automations": "nope"}, messages) == [
        "selora_ai_aaa"
    ]
    assert _find_session_saved_automation_ids({}, messages) == ["selora_ai_aaa"]


def test_explicit_refinement_wins_and_takes_the_latest() -> None:
    messages = [
        {"automation_status": "refining", "automation_id": "selora_ai_aaa"},
        {"automation_status": "refining", "automation_id": "selora_ai_bbb"},
    ]
    assert _find_refining_automation_id(messages) == "selora_ai_bbb"
    assert _find_refining_automation_id([{"automation_status": "saved"}]) is None


@pytest.mark.parametrize("terminator", ["pending", "saved", "declined"])
def test_a_finished_refinement_stops_being_the_target(terminator: str) -> None:
    """The ``refining`` message stays in the session forever. Without the
    terminator the scan keeps returning its id — and since the id is persisted
    on every later proposal, accepting an unrelated new automation would replace
    the old one instead of creating it."""
    messages = [
        {"automation_status": "refining", "automation_id": "selora_ai_aaa"},
        {"automation_status": terminator, "automation_id": "selora_ai_aaa"},
    ]
    assert _find_refining_automation_id(messages) is None


# ── The saved automation is read off disk, not off the message ───────


@pytest.mark.asyncio
async def test_an_edit_made_at_accept_time_is_what_the_model_sees(hass: HomeAssistant) -> None:
    """The card's YAML editor is applied on accept; ``set_automation_status``
    then persists only the status and id, so the message still holds the
    pre-edit proposal. Prompting from it would have a follow-up quietly revert
    the user's own change — and a rename would send it off to create a
    duplicate, since the alias no longer matches."""
    _write_automations(hass, [_entry("selora_ai_aaa", "Pool Schedule", at="06:00:00")])
    messages = [_saved_message("selora_ai_aaa", "Aqua Rite Schedule")]

    saved = await _find_session_saved_automations(hass, None, messages)

    assert len(saved) == 1
    automation_id, alias, yaml_text = saved[0]
    assert automation_id == "selora_ai_aaa"
    assert alias == "Pool Schedule"
    assert "at: 06:00:00" in yaml_text
    assert "Turns the plug on" not in yaml_text  # the stale proposal's text
    # The id is returned alongside; repeating it in the YAML invites the model
    # to echo it into a payload where the write path strips it anyway.
    assert "\nid:" not in yaml_text and not yaml_text.startswith("id:")


@pytest.mark.asyncio
async def test_an_automation_deleted_between_turns_drops_out(hass: HomeAssistant) -> None:
    # async_update_automation matches on the id and fails outright when it is
    # gone, so a re-proposal has to create instead of failing.
    _write_automations(hass, [_entry("selora_ai_other", "Something Else")])
    saved = await _find_session_saved_automations(
        hass, None, [_saved_message("selora_ai_aaa", "Aqua Rite Schedule")]
    )
    assert saved == []


@pytest.mark.asyncio
async def test_snapshots_keep_the_order_they_were_asked_for(hass: HomeAssistant) -> None:
    _write_automations(
        hass,
        [
            _entry("selora_ai_aaa", "Aqua Rite Schedule"),
            _entry("selora_ai_bbb", "Porch Lights"),
            {"alias": "Hand-written, no id"},
        ],
    )
    snapshots = await async_yaml_automation_snapshots(
        hass, ["selora_ai_bbb", "selora_ai_aaa", "selora_ai_gone"]
    )
    assert [aid for aid, _alias, _yaml in snapshots] == ["selora_ai_bbb", "selora_ai_aaa"]
    assert await async_yaml_automation_snapshots(hass, []) == []


# ── The store records the id where pruning cannot reach it ───────────


@pytest.mark.asyncio
async def test_accepting_a_proposal_indexes_its_id(hass: HomeAssistant) -> None:
    from custom_components.selora_ai.conversation_store import ConversationStore

    store = ConversationStore(hass)
    session = await store.create_session()
    await store.append_message(session["id"], "user", "make me an automation")
    await store.append_message(session["id"], "assistant", "here you go")
    assert await store.set_automation_status(
        session["id"], 1, "saved", automation_id="selora_ai_aaa"
    )

    saved = (await store.get_session(session["id"]) or {}).get("saved_automations")
    assert saved == ["selora_ai_aaa"]


@pytest.mark.asyncio
async def test_a_pending_proposal_remembers_what_it_would_replace(hass: HomeAssistant) -> None:
    """The panel decides create-vs-update from ``refining_automation_id`` on the
    message. The target is inferred from session state when the turn is
    generated and nothing recomputes it, so a session reopened before the card
    is accepted has only the stored message to read it from — without this,
    Accept took the create path and wrote the duplicate."""
    from custom_components.selora_ai.conversation_store import ConversationStore

    store = ConversationStore(hass)
    session = await store.create_session()
    await store.append_message(
        session["id"],
        "assistant",
        "Updated the schedule.",
        automation={"alias": "Aqua Rite Schedule"},
        automation_yaml=AQUA_YAML,
        automation_status="pending",
        refining_automation_id="selora_ai_aaa",
    )

    reopened = await store.get_session(session["id"])
    assert reopened is not None
    assert reopened["messages"][0]["refining_automation_id"] == "selora_ai_aaa"


def test_both_chat_handlers_persist_the_target() -> None:
    """A one-way guard against drift, in the spirit of the frontend-contract
    test: the store accepting the field proves nothing if a handler forgets to
    pass it, which is how the value came to live only in the transient
    websocket payload. Asserted structurally because there is no harness that
    drives either chat handler end to end."""
    import ast

    source = Path("custom_components/selora_ai/__init__.py").read_text(encoding="utf-8")
    assistant_appends = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append_message"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "assistant"
    ]
    # The streaming handler, the non-streaming handler, and the approval
    # follow-up append. Fewer means the scan missed one and proves nothing.
    assert len(assistant_appends) >= 2
    carrying = [
        call
        for call in assistant_appends
        if any(kw.arg == "refining_automation_id" for kw in call.keywords)
    ]
    assert len(carrying) == 2


@pytest.mark.asyncio
async def test_a_plain_proposal_stores_no_target(hass: HomeAssistant) -> None:
    from custom_components.selora_ai.conversation_store import ConversationStore

    store = ConversationStore(hass)
    session = await store.create_session()
    await store.append_message(
        session["id"],
        "assistant",
        "Here you go.",
        automation={"alias": "Porch Lights"},
        automation_yaml=AQUA_YAML,
        automation_status="pending",
    )

    reopened = await store.get_session(session["id"])
    assert reopened is not None
    # Absent, not empty: the panel's resolver treats any value as a target.
    assert "refining_automation_id" not in reopened["messages"][0]


@pytest.mark.asyncio
async def test_only_a_save_is_indexed(hass: HomeAssistant) -> None:
    from custom_components.selora_ai.conversation_store import ConversationStore

    store = ConversationStore(hass)
    session = await store.create_session()
    await store.append_message(session["id"], "assistant", "proposal")
    await store.set_automation_status(session["id"], 0, "declined", automation_id="selora_ai_aaa")
    await store.set_automation_status(session["id"], 0, "refining", automation_id="selora_ai_bbb")

    assert (await store.get_session(session["id"]) or {}).get("saved_automations") is None


@pytest.mark.asyncio
async def test_re_saving_moves_the_id_to_the_end(hass: HomeAssistant) -> None:
    from custom_components.selora_ai.conversation_store import ConversationStore

    store = ConversationStore(hass)
    session = await store.create_session()
    for _ in range(3):
        await store.append_message(session["id"], "assistant", "proposal")
    for index, automation_id in enumerate(["selora_ai_aaa", "selora_ai_bbb", "selora_ai_aaa"]):
        await store.set_automation_status(
            session["id"], index, "saved", automation_id=automation_id
        )

    saved = (await store.get_session(session["id"]) or {}).get("saved_automations")
    assert saved == ["selora_ai_bbb", "selora_ai_aaa"]


@pytest.mark.asyncio
async def test_the_index_is_bounded(hass: HomeAssistant) -> None:
    from custom_components.selora_ai.conversation_store import (
        _SESSION_MAX_SAVED_AUTOMATIONS,
        ConversationStore,
    )

    store = ConversationStore(hass)
    session = await store.create_session()
    await store.append_message(session["id"], "assistant", "proposal")
    for n in range(_SESSION_MAX_SAVED_AUTOMATIONS + 5):
        await store.set_automation_status(session["id"], 0, "saved", automation_id=f"selora_ai_{n}")

    saved = (await store.get_session(session["id"]) or {}).get("saved_automations")
    assert len(saved) == _SESSION_MAX_SAVED_AUTOMATIONS
    # The tail is what can be spared — the newest are what a follow-up means.
    assert saved[-1] == f"selora_ai_{_SESSION_MAX_SAVED_AUTOMATIONS + 4}"


# ── Reference context handed to the model ────────────────────────────


def test_reference_context_sanitizes_the_alias_it_prints() -> None:
    context, editable = _automation_reference_context(
        [("selora_ai_aaa", "Aqua\nRite\tSchedule", AQUA_YAML)]
    )
    assert context is not None
    automation_id, alias, yaml_text = context[0]
    assert automation_id == "selora_ai_aaa"
    assert "\n" not in alias and "\t" not in alias
    assert yaml_text == AQUA_YAML
    # The editable list keeps the raw alias: the write-target match compares it
    # against the alias the proposal actually carries.
    assert editable == [("selora_ai_aaa", "Aqua\nRite\tSchedule", AQUA_YAML)]


def test_an_automation_with_no_yaml_is_named_but_not_editable() -> None:
    # Named so the model can say it cannot edit it; not editable because a
    # proposal for an automation nobody showed it is composed from nothing.
    context, editable = _automation_reference_context([("selora_ai_aaa", "Aqua Rite Schedule", "")])
    assert context == [("selora_ai_aaa", "Aqua Rite Schedule", "")]
    assert editable == []
    assert _automation_reference_context([]) == (None, [])


def test_an_oversized_automation_is_named_but_not_editable() -> None:
    huge = "alias: Huge\n" + ("x" * 20_000)
    context, editable = _automation_reference_context([("selora_ai_huge", "Huge One", huge)])
    assert context == [("selora_ai_huge", "Huge One", "")]
    assert editable == []


def test_the_budget_spends_on_the_newest_first() -> None:
    bulky = "alias: Bulky\ndescription: " + ("x" * 9_000) + "\n"
    context, editable = _automation_reference_context(
        [
            ("selora_ai_old", "Old One", bulky),
            ("selora_ai_new", "Aqua Rite Schedule", AQUA_YAML),
        ]
    )
    # Oldest-first out, newest-first through the budget: the one a follow-up
    # most likely means keeps its YAML and stays editable.
    assert context == [
        ("selora_ai_old", "Old One", ""),
        ("selora_ai_new", "Aqua Rite Schedule", AQUA_YAML),
    ]
    assert editable == [("selora_ai_new", "Aqua Rite Schedule", AQUA_YAML)]


def test_the_prompt_carries_the_id_the_model_has_to_quote(hass: HomeAssistant) -> None:
    from custom_components.selora_ai.llm_client.client import LLMClient
    from custom_components.selora_ai.providers import create_provider

    client = LLMClient(hass, provider=create_provider("anthropic", hass, api_key="test-key"))
    messages = client._build_chat_messages(
        "change the time to 7am",
        [],
        None,
        None,
        system_prompt="sys",
        automation_context=[("selora_ai_aaa", "Aqua Rite Schedule", AQUA_YAML)],
    )
    body = messages[-1]["content"]
    assert "AUTOMATIONS SAVED IN THIS SESSION:" in body
    assert "automation_id: selora_ai_aaa" in body
    assert "Untrusted automation reference data" in body
    assert "description: Turns the plug on" in body


def test_a_named_but_unshown_automation_renders_a_note(hass: HomeAssistant) -> None:
    from custom_components.selora_ai.llm_client.client import LLMClient
    from custom_components.selora_ai.providers import create_provider

    client = LLMClient(hass, provider=create_provider("anthropic", hass, api_key="test-key"))
    messages = client._build_chat_messages(
        "change the time to 7am",
        [],
        None,
        None,
        system_prompt="sys",
        automation_context=[("selora_ai_huge", "Aqua Rite Schedule", "")],
    )
    body = messages[-1]["content"]
    assert "automation_id: selora_ai_huge" in body
    assert "too large to include here" in body


# ── The model's claim ────────────────────────────────────────────────


def test_the_claim_is_taken_out_of_the_payload() -> None:
    payload = {"alias": "Aqua Rite Schedule", "refine_automation_id": " selora_ai_aaa "}
    assert _pop_refine_automation_id(payload) == "selora_ai_aaa"
    # It is conversation metadata, not an automation field — HA's schema has
    # never heard of it.
    assert "refine_automation_id" not in payload


def test_a_claim_that_is_not_an_id_is_dropped() -> None:
    assert _pop_refine_automation_id({"refine_automation_id": ""}) is None
    assert _pop_refine_automation_id({"refine_automation_id": 7}) is None
    assert _pop_refine_automation_id({"refine_automation_id": "a" * 65}) is None
    assert _pop_refine_automation_id({"refine_automation_id": "id with spaces"}) is None
    assert _pop_refine_automation_id({}) is None
    assert _pop_refine_automation_id("not a dict") is None


def test_a_streamed_proposal_surfaces_its_claim_without_leaking_it(hass: HomeAssistant) -> None:
    from custom_components.selora_ai.llm_client.client import LLMClient
    from custom_components.selora_ai.providers import create_provider

    hass.states.async_set("switch.plug", "off", {"friendly_name": "Aqua Rite Plug"})
    client = LLMClient(hass, provider=create_provider("anthropic", hass, api_key="test-key"))
    text = (
        "Updated the schedule.\n\n"
        "```automation\n"
        "{\n"
        '  "refine_automation_id": "selora_ai_aaa",\n'
        '  "alias": "Aqua Rite Schedule",\n'
        '  "description": "Turns the plug on at 07:00",\n'
        '  "triggers": [{"platform": "time", "at": "07:00:00"}],\n'
        '  "conditions": [],\n'
        '  "actions": [{"service": "switch.turn_on", "target": {"entity_id": "switch.plug"}}]\n'
        "}\n"
        "```"
    )
    result = client.parse_streamed_response(text)
    assert result["refine_automation_id"] == "selora_ai_aaa"
    assert "refine_automation_id" not in result["automation"]
    assert "refine_automation_id" not in result["automation_yaml"]


def test_a_rejected_proposal_still_carries_its_claim(hass: HomeAssistant) -> None:
    """A validation failure is where the claim matters most: the correction
    round re-prompts with the rejected payload alone and cannot restate what
    was being edited, so `_retry_invalid_automation` carries this value
    forward. Dropped here, a corrected proposal that also renamed the
    automation resolves to no target and is accepted as a second one."""
    from custom_components.selora_ai.llm_client.client import LLMClient
    from custom_components.selora_ai.providers import create_provider

    client = LLMClient(hass, provider=create_provider("anthropic", hass, api_key="test-key"))
    text = (
        "Updated the schedule.\n\n"
        "```automation\n"
        "{\n"
        '  "refine_automation_id": "selora_ai_aaa",\n'
        '  "alias": "Aqua Rite Schedule",\n'
        '  "triggers": [{"platform": "time", "at": "07:00:00"}],\n'
        '  "actions": [{"service": "switch.turn_on", '
        '"target": {"entity_id": "switch.nonexistent"}}]\n'
        "}\n"
        "```"
    )
    result = client.parse_streamed_response(text)
    assert result.get("validation_error")
    assert result.get("automation") is None
    assert result["refine_automation_id"] == "selora_ai_aaa"
    # And not inside the payload echoed back to the model.
    assert "refine_automation_id" not in result["rejected_automation"]


# ── Write-target resolution ──────────────────────────────────────────


def test_same_alias_follow_up_targets_the_saved_automation() -> None:
    saved = [("selora_ai_aaa", "Aqua Rite Schedule", AQUA_YAML)]
    assert _resolve_proposal_write_target({"alias": "Aqua Rite Schedule"}, saved) == "selora_ai_aaa"


def test_alias_match_ignores_case_and_spacing() -> None:
    saved = [("selora_ai_aaa", "Aqua Rite Schedule", AQUA_YAML)]
    assert (
        _resolve_proposal_write_target({"alias": " aqua rite   schedule "}, saved)
        == "selora_ai_aaa"
    )


def test_a_differently_named_proposal_still_creates() -> None:
    saved = [("selora_ai_aaa", "Aqua Rite Schedule", AQUA_YAML)]
    # "Now also make one for the porch" in the same session is a new
    # automation, not an edit of the one on screen.
    assert _resolve_proposal_write_target({"alias": "Porch Lights"}, saved) is None


def test_a_claimed_id_carries_the_edit_through_a_rename() -> None:
    saved = [("selora_ai_aaa", "Aqua Rite Schedule", AQUA_YAML)]
    target = _resolve_proposal_write_target({"alias": "Pool Schedule"}, saved, "selora_ai_aaa")
    assert target == "selora_ai_aaa"


def test_a_claim_outside_the_session_selects_nothing() -> None:
    # The id is quoted by the model out of untrusted text; one the session
    # never saved must not pick a write target.
    saved = [("selora_ai_aaa", "Aqua Rite Schedule", AQUA_YAML)]
    assert (
        _resolve_proposal_write_target({"alias": "Pool Schedule"}, saved, "selora_ai_someone_elses")
        is None
    )


def test_the_claim_beats_a_newer_automation_sharing_the_alias() -> None:
    saved = [
        ("selora_ai_aaa", "Aqua Rite Schedule", AQUA_YAML),
        ("selora_ai_bbb", "Aqua Rite Schedule", AQUA_YAML),
    ]
    target = _resolve_proposal_write_target({"alias": "Aqua Rite Schedule"}, saved, "selora_ai_aaa")
    assert target == "selora_ai_aaa"


def test_no_proposal_and_no_history_resolve_to_nothing() -> None:
    saved = [("selora_ai_aaa", "Aqua Rite Schedule", AQUA_YAML)]
    assert _resolve_proposal_write_target(None, saved) is None
    assert _resolve_proposal_write_target({"alias": "A"}, []) is None
    assert _resolve_proposal_write_target({"alias": ""}, saved) is None


# ── Descriptions read as names, not ids ──────────────────────────────


def test_entity_ids_in_a_description_become_friendly_names(hass: HomeAssistant) -> None:
    hass.states.async_set(
        "switch.basement_pool_room_grillplats_plug_aqua_rite",
        "off",
        {"friendly_name": "GRILLPLATS plug Aqua Rite"},
    )
    automation = {
        "description": (
            "Turns switch.basement_pool_room_grillplats_plug_aqua_rite on at 07:00 "
            "on Thursdays and Fridays."
        )
    }
    _humanize_description_entity_ids(automation, hass)
    assert automation["description"] == (
        "Turns GRILLPLATS plug Aqua Rite on at 07:00 on Thursdays and Fridays."
    )


def test_unresolvable_ids_and_prose_punctuation_are_left_alone(hass: HomeAssistant) -> None:
    automation = {"description": "Runs at 7 a.m. and pokes switch.not_a_real_entity."}
    _humanize_description_entity_ids(automation, hass)
    assert automation["description"] == "Runs at 7 a.m. and pokes switch.not_a_real_entity."


def test_a_template_is_not_rewritten(hass: HomeAssistant) -> None:
    """The id inside a template resolves, so the state lookup cannot tell it
    from prose — rewriting it to the friendly name produces template text that
    no longer renders."""
    hass.states.async_set("sensor.temperature", "21", {"friendly_name": "Living Room Temp"})
    automation = {"description": "Reports {{ states('sensor.temperature') }} each morning"}
    _humanize_description_entity_ids(automation, hass)
    assert automation["description"] == ("Reports {{ states('sensor.temperature') }} each morning")


def test_prose_around_a_template_is_still_rewritten(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.temperature", "21", {"friendly_name": "Living Room Temp"})
    hass.states.async_set("switch.plug", "off", {"friendly_name": "Aqua Rite Plug"})
    automation = {
        "description": "Turns switch.plug on when {{ states('sensor.temperature') }} drops"
    }
    _humanize_description_entity_ids(automation, hass)
    assert automation["description"] == (
        "Turns Aqua Rite Plug on when {{ states('sensor.temperature') }} drops"
    )


def test_statement_and_comment_blocks_are_left_alone(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.temperature", "21", {"friendly_name": "Living Room Temp"})
    automation = {
        "description": (
            "{% if is_state('sensor.temperature', '21') %}warm{% endif %} "
            "{# sensor.temperature is the hallway probe #}"
        )
    }
    _humanize_description_entity_ids(automation, hass)
    assert "sensor.temperature" in automation["description"]
    assert "Living Room Temp" not in automation["description"]


def test_an_unterminated_template_shields_what_follows(hass: HomeAssistant) -> None:
    # Broken template text is still the user's text; guessing where the span
    # ends by rewriting inside it is the one thing that cannot be undone.
    hass.states.async_set("sensor.temperature", "21", {"friendly_name": "Living Room Temp"})
    automation = {"description": "Reports {{ states('sensor.temperature')"}
    _humanize_description_entity_ids(automation, hass)
    assert automation["description"] == "Reports {{ states('sensor.temperature')"


def test_humanizing_needs_hass() -> None:
    automation = {"description": "Turns switch.foo on"}
    _humanize_description_entity_ids(automation, None)
    assert automation["description"] == "Turns switch.foo on"


def test_the_json_envelope_description_is_humanized_too(hass: HomeAssistant) -> None:
    """The model writes a user-facing summary at the top level, separate from
    the automation's own description — and that is the one the chat handler
    persists and the proposal card prefers, so leaving it alone kept the raw
    entity_id on screen."""
    from custom_components.selora_ai.llm_client.parsers import parse_architect_response

    hass.states.async_set("switch.plug", "off", {"friendly_name": "Aqua Rite Plug"})
    payload = json.dumps(
        {
            "intent": "automation",
            "response": "Here's the automation.",
            "description": "Turns switch.plug on at 07:00",
            "automation": {
                "alias": "Aqua Rite Schedule",
                "description": "Turns switch.plug on at 07:00",
                "triggers": [{"platform": "time", "at": "07:00:00"}],
                "conditions": [],
                "actions": [{"service": "switch.turn_on", "target": {"entity_id": "switch.plug"}}],
            },
        }
    )

    result = parse_architect_response(payload, hass, [])

    expected = "Turns Aqua Rite Plug on at 07:00"
    assert result["description"] == expected
    assert result["automation"]["description"] == expected


def test_a_parsed_proposal_carries_the_name_in_both_the_payload_and_the_yaml(
    hass: HomeAssistant,
) -> None:
    from custom_components.selora_ai.llm_client.client import LLMClient
    from custom_components.selora_ai.providers import create_provider

    hass.states.async_set(
        "switch.basement_pool_room_grillplats_plug_aqua_rite",
        "off",
        {"friendly_name": "GRILLPLATS plug Aqua Rite"},
    )
    client = LLMClient(hass, provider=create_provider("anthropic", hass, api_key="test-key"))
    text = (
        "Updated schedule.\n\n"
        "```automation\n"
        "{\n"
        '  "alias": "Aqua Rite Schedule",\n'
        '  "description": "Turns switch.basement_pool_room_grillplats_plug_aqua_rite on at 07:00",\n'
        '  "triggers": [{"platform": "time", "at": "07:00:00"}],\n'
        '  "conditions": [],\n'
        '  "actions": [{"service": "switch.turn_on", "target": '
        '{"entity_id": "switch.basement_pool_room_grillplats_plug_aqua_rite"}}]\n'
        "}\n"
        "```"
    )
    result = client.parse_streamed_response(text)
    assert result["intent"] == "automation"
    expected = "Turns GRILLPLATS plug Aqua Rite on at 07:00"
    # The card reads the payload, the automations.yaml row reads the dumped
    # YAML — the id must be gone from both.
    assert result["automation"]["description"] == expected
    assert result["description"] == expected
    assert f"description: {expected}" in result["automation_yaml"]
