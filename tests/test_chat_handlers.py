"""End-to-end tests for the chat websocket handlers, via ``chat_harness``.

Everything here is a whole turn: the handler assembles context, the (scripted)
provider replies, and the handler persists, sends, and resolves. The bugs this
covers were all invisible to helper-level tests — a value that was computed
correctly and then not passed, not persisted, or not sent.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
import pytest

from tests.chat_harness import ChatHarness

AQUA_ENTRY = {
    "id": "selora_ai_aaa",
    "alias": "Aqua Rite Schedule",
    "description": "Turns the plug on Thursdays and Fridays",
    "triggers": [{"platform": "time", "at": "00:00:00"}],
    "actions": [{"action": "switch.turn_on", "target": {"entity_id": "switch.plug"}}],
    "mode": "single",
}


def _proposal(alias: str = "Aqua Rite Schedule", at: str = "07:00:00") -> dict[str, Any]:
    """A parsed automation reply, as ``architect_chat`` returns one."""
    automation = {
        "alias": alias,
        "description": f"Turns the plug on at {at}",
        "triggers": [{"platform": "time", "at": at}],
        "conditions": [],
        "actions": [{"action": "switch.turn_on", "target": {"entity_id": "switch.plug"}}],
        "mode": "single",
    }
    return {
        "intent": "automation",
        "response": "Here's the automation.",
        "automation": automation,
        "automation_yaml": f"alias: {alias}\n",
    }


def _streamed_block(alias: str = "Aqua Rite Schedule", extra: str = "") -> str:
    """Raw provider text carrying a fenced automation block."""
    return (
        "Updated the schedule.\n\n"
        "```automation\n"
        "{\n"
        f"{extra}"
        f'  "alias": "{alias}",\n'
        '  "description": "Turns the plug on at 07:00",\n'
        '  "triggers": [{"platform": "time", "at": "07:00:00"}],\n'
        '  "conditions": [],\n'
        '  "actions": [{"service": "switch.turn_on", "target": {"entity_id": "switch.plug"}}]\n'
        "}\n"
        "```"
    )


@pytest.fixture
async def harness(hass: HomeAssistant) -> ChatHarness:
    hass.states.async_set("switch.plug", "off", {"friendly_name": "Aqua Rite Plug"})
    return await ChatHarness.create(hass)


# ── The harness drives the real handlers ─────────────────────────────


@pytest.mark.asyncio
async def test_a_plain_turn_answers_and_persists(harness: ChatHarness) -> None:
    turn = await harness.chat(
        "which lights are on?", reply={"intent": "answer", "response": "Just the porch."}
    )
    assert not turn.errors
    assert turn.done["response"] == "Just the porch."
    roles = [(m["role"], m["content"]) for m in await harness.messages()]
    assert roles == [("user", "which lights are on?"), ("assistant", "Just the porch.")]


@pytest.mark.asyncio
async def test_a_streamed_turn_emits_tokens_then_done(harness: ChatHarness) -> None:
    turn = await harness.stream("say hi", chunks=["Hel", "lo."])
    assert turn.tokens() == "Hello."
    assert turn.done["type"] == "done"
    assert turn.done["response"] == "Hello."
    assert [m["role"] for m in await harness.messages()] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_a_non_admin_is_refused_before_the_llm(harness: ChatHarness) -> None:
    turn = await harness.chat("hello", reply={"intent": "answer", "response": "hi"}, is_admin=False)
    assert turn.errors == [
        ("admin_required", "Selora AI panel actions require an administrator account")
    ]
    assert not turn.architect_calls


# ── A follow-up edits the automation instead of duplicating it ───────


@pytest.mark.asyncio
async def test_a_first_proposal_has_no_write_target(harness: ChatHarness) -> None:
    turn = await harness.chat("turn the plug on at 7am on Thursdays", reply=_proposal())
    assert turn.done["refining_automation_id"] is None
    assert turn.asked["automation_context"] is None
    assert (await harness.messages())[-1]["automation_status"] == "pending"


@pytest.mark.asyncio
async def test_a_follow_up_targets_the_automation_the_session_saved(
    harness: ChatHarness,
) -> None:
    first = await harness.chat("turn the plug on at midnight", reply=_proposal(at="00:00:00"))
    await harness.save_proposal(first.done["automation_message_index"], "selora_ai_aaa")
    harness.write_automations([AQUA_ENTRY])

    second = await harness.chat("change the time to 7am", reply=_proposal())

    # What the panel is told, and what a reopened session would read.
    assert second.done["refining_automation_id"] == "selora_ai_aaa"
    assert (await harness.messages())[-1]["refining_automation_id"] == "selora_ai_aaa"


@pytest.mark.asyncio
async def test_the_follow_up_turn_carries_the_saved_automation_as_context(
    harness: ChatHarness,
) -> None:
    first = await harness.chat("turn the plug on at midnight", reply=_proposal(at="00:00:00"))
    await harness.save_proposal(first.done["automation_message_index"], "selora_ai_aaa")
    harness.write_automations([AQUA_ENTRY])

    second = await harness.chat("change the time to 7am", reply=_proposal())

    context = second.asked["automation_context"]
    assert context is not None
    automation_id, alias, yaml_text = context[0]
    assert (automation_id, alias) == ("selora_ai_aaa", "Aqua Rite Schedule")
    # Read off disk, so an accept-time edit is what the model sees.
    assert "at: 00:00:00" in yaml_text


@pytest.mark.asyncio
async def test_an_unrelated_proposal_in_the_same_session_still_creates(
    harness: ChatHarness,
) -> None:
    first = await harness.chat("turn the plug on at midnight", reply=_proposal(at="00:00:00"))
    await harness.save_proposal(first.done["automation_message_index"], "selora_ai_aaa")
    harness.write_automations([AQUA_ENTRY])

    second = await harness.chat(
        "now make one for the porch lights", reply=_proposal(alias="Porch Lights")
    )
    assert second.done["refining_automation_id"] is None


@pytest.mark.asyncio
async def test_a_streamed_follow_up_resolves_and_persists_its_target(
    harness: ChatHarness,
) -> None:
    """The streaming handler is a separate assembly of the same steps, and the
    parse runs for real here — the claim is extracted from the model's block by
    `parse_streamed_response`, not scripted."""
    first = await harness.stream("turn the plug on at midnight", chunks=_streamed_block())
    await harness.save_proposal(first.done["automation_message_index"], "selora_ai_aaa")
    harness.write_automations([AQUA_ENTRY])

    second = await harness.stream(
        "call it Pool Schedule instead",
        chunks=_streamed_block(
            alias="Pool Schedule", extra='  "refine_automation_id": "selora_ai_aaa",\n'
        ),
    )

    # A rename: the alias no longer matches, so only the model's claim can
    # carry the edit — and it survives into the stored message.
    assert second.done["refining_automation_id"] == "selora_ai_aaa"
    assert (await harness.messages())[-1]["refining_automation_id"] == "selora_ai_aaa"
    # The claim never reaches the automation the user would accept.
    assert "refine_automation_id" not in second.done["automation"]


@pytest.mark.asyncio
async def test_a_correction_round_keeps_the_target(harness: ChatHarness) -> None:
    """A proposal that fails validation goes through
    `_retry_invalid_automation`, which re-prompts with the rejected payload
    alone — so the claim has to survive the rejection or a corrected proposal
    that also renames the automation resolves to nothing."""
    first = await harness.stream("turn the plug on at midnight", chunks=_streamed_block())
    await harness.save_proposal(first.done["automation_message_index"], "selora_ai_aaa")
    harness.write_automations([AQUA_ENTRY])

    invalid = (
        "Updated the schedule.\n\n"
        "```automation\n"
        "{\n"
        '  "refine_automation_id": "selora_ai_aaa",\n'
        '  "alias": "Aqua Rite Schedule",\n'
        '  "triggers": [{"platform": "time", "at": "07:00:00"}],\n'
        '  "conditions": [],\n'
        '  "actions": [{"service": "frobnicate.do_thing", "target": '
        '{"entity_id": "switch.plug"}}]\n'
        "}\n"
        "```"
    )
    turn = await harness.stream(
        "change the time to 7am and call it Pool Schedule",
        chunks=invalid,
        retry_reply=_proposal(alias="Pool Schedule"),
    )

    # The retry produced a renamed automation, so the alias cannot match — the
    # carried claim is the only thing left pointing at the original.
    assert turn.done["automation"]["alias"] == "Pool Schedule"
    assert turn.done["refining_automation_id"] == "selora_ai_aaa"
    assert (await harness.messages())[-1]["refining_automation_id"] == "selora_ai_aaa"


@pytest.mark.asyncio
async def test_a_correction_round_cannot_redirect_the_target(harness: ChatHarness) -> None:
    """The target is settled by the parse that saw the reference context. An id
    a correction round volunteers is ungrounded, and honouring one that names
    another automation the session saved would point the write at an automation
    the user never asked to change."""
    first = await harness.chat("turn the plug on at midnight", reply=_proposal(at="00:00:00"))
    await harness.save_proposal(first.done["automation_message_index"], "selora_ai_aaa")
    other = await harness.chat("one for the porch too", reply=_proposal(alias="Porch Lights"))
    await harness.save_proposal(other.done["automation_message_index"], "selora_ai_bbb")
    harness.write_automations(
        [AQUA_ENTRY, {**AQUA_ENTRY, "id": "selora_ai_bbb", "alias": "Porch Lights"}]
    )

    invalid = (
        "Updated.\n\n"
        "```automation\n"
        "{\n"
        '  "refine_automation_id": "selora_ai_aaa",\n'
        '  "alias": "Aqua Rite Schedule",\n'
        '  "triggers": [{"platform": "time", "at": "07:00:00"}],\n'
        '  "conditions": [],\n'
        '  "actions": [{"service": "frobnicate.do_thing", "target": '
        '{"entity_id": "switch.plug"}}]\n'
        "}\n"
        "```"
    )
    corrected = _proposal(alias="Pool Schedule")
    corrected["refine_automation_id"] = "selora_ai_bbb"

    turn = await harness.stream(
        "change the time to 7am and call it Pool Schedule",
        chunks=invalid,
        retry_reply=corrected,
    )

    assert turn.done["refining_automation_id"] == "selora_ai_aaa"


@pytest.mark.asyncio
async def test_a_target_survives_the_session_being_pruned(harness: ChatHarness) -> None:
    """``append_message`` keeps the first message and the latest 99, so in a
    long conversation the message that recorded the save is gone by the time
    the follow-up arrives."""
    first = await harness.chat("turn the plug on at midnight", reply=_proposal(at="00:00:00"))
    await harness.save_proposal(first.done["automation_message_index"], "selora_ai_aaa")
    harness.write_automations([AQUA_ENTRY])

    session_id = harness.session_id or ""
    for n in range(120):
        await harness.store.append_message(session_id, "user", f"filler {n}")

    # Precondition: the accepted proposal really is gone from the messages.
    assert all(m.get("automation_status") != "saved" for m in await harness.messages())

    turn = await harness.chat("change the time to 7am", reply=_proposal())
    assert turn.done["refining_automation_id"] == "selora_ai_aaa"
    assert turn.asked["automation_context"] is not None


@pytest.mark.asyncio
async def test_a_claim_the_session_never_saved_is_ignored(harness: ChatHarness) -> None:
    harness.write_automations([AQUA_ENTRY, {**AQUA_ENTRY, "id": "selora_ai_elsewhere"}])
    turn = await harness.stream(
        "make me an automation",
        chunks=_streamed_block(extra='  "refine_automation_id": "selora_ai_elsewhere",\n'),
    )
    assert turn.done["refining_automation_id"] is None


@pytest.mark.asyncio
async def test_an_automation_too_large_to_show_is_not_an_inferred_target(
    harness: ChatHarness,
) -> None:
    """It is named in the prompt so the model can say it cannot edit it, but a
    proposal for it is a rule composed from nothing — writing that over the
    original would discard everything the user did not mention."""
    first = await harness.chat("turn the plug on at midnight", reply=_proposal(at="00:00:00"))
    await harness.save_proposal(first.done["automation_message_index"], "selora_ai_aaa")
    bulky = {**AQUA_ENTRY, "description": "x" * 9_000}
    harness.write_automations([bulky])

    second = await harness.chat("change the time to 7am", reply=_proposal())

    context = second.asked["automation_context"]
    assert context is not None
    assert context[0][0] == "selora_ai_aaa"
    assert context[0][2] == ""  # named, not shown
    assert second.done["refining_automation_id"] is None


@pytest.mark.asyncio
async def test_a_local_model_never_gets_an_inferred_target(hass: HomeAssistant) -> None:
    """The low-context prompt has no room for an automation's YAML, so the
    model composes a fresh rule. Writing that over the original would discard
    whatever the user did not mention this turn — worse than the duplicate."""
    hass.states.async_set("switch.plug", "off", {"friendly_name": "Aqua Rite Plug"})
    harness = await ChatHarness.create(hass, provider="selora_local")
    assert not harness.llm.shows_automation_reference

    first = await harness.chat("turn the plug on at midnight", reply=_proposal(at="00:00:00"))
    await harness.save_proposal(first.done["automation_message_index"], "selora_ai_aaa")
    harness.write_automations([AQUA_ENTRY])

    second = await harness.chat("change the time to 7am", reply=_proposal())
    assert second.done["refining_automation_id"] is None
    assert "refining_automation_id" not in (await harness.messages())[-1]


@pytest.mark.asyncio
async def test_a_finished_refinement_does_not_capture_the_next_proposal(
    harness: ChatHarness,
) -> None:
    """The ``refining`` marker stays in the session after its result is saved —
    on its own message, which a later save does not overwrite. A later
    unrelated proposal would inherit that target and, now that the target is
    persisted, replace that automation on accept instead of creating one."""
    first = await harness.chat("turn the plug on at midnight", reply=_proposal(at="00:00:00"))
    await harness.store.set_automation_status(
        harness.session_id or "",
        first.done["automation_message_index"],
        "refining",
        automation_id="selora_ai_aaa",
    )
    harness.write_automations([AQUA_ENTRY])

    # The refinement's own result, saved on its own message — the marker above
    # is still in the session.
    second = await harness.chat("change the time to 7am", reply=_proposal())
    await harness.save_proposal(second.done["automation_message_index"], "selora_ai_aaa")

    third = await harness.chat(
        "now make one for the porch lights", reply=_proposal(alias="Porch Lights")
    )

    assert third.done["refining_automation_id"] is None
    assert "refining_automation_id" not in (await harness.messages())[-1]


@pytest.mark.asyncio
async def test_a_clean_slate_request_inherits_no_target(harness: ChatHarness) -> None:
    """``history: []`` replaces all session-derived context, so a proposal must
    not land on an automation the supplied history never mentions."""
    first = await harness.chat("turn the plug on at midnight", reply=_proposal(at="00:00:00"))
    await harness.store.set_automation_status(
        harness.session_id or "",
        first.done["automation_message_index"],
        "refining",
        automation_id="selora_ai_aaa",
    )
    harness.write_automations([AQUA_ENTRY])

    second = await harness.chat("change the time to 7am", reply=_proposal(), history=[])

    assert second.done["refining_automation_id"] is None
    assert second.asked["automation_context"] is None


# ── Refinement suppresses live device commands ───────────────────────


@pytest.mark.asyncio
async def test_a_command_during_refinement_runs_nothing(harness: ChatHarness) -> None:
    """While a proposal is being refined, a command intent means "fold this in",
    not "operate the house now"."""
    first = await harness.chat("turn the plug on at midnight", reply=_proposal(at="00:00:00"))
    await harness.store.set_automation_status(
        harness.session_id or "", first.done["automation_message_index"], "refining"
    )

    turn = await harness.chat(
        "also turn on the plug",
        reply={
            "intent": "command",
            "response": "Turned on the plug.",
            "calls": [{"service": "switch.turn_on", "target": {"entity_id": "switch.plug"}}],
        },
    )

    assert turn.done["intent"] == "answer"
    assert turn.done["executed"] == []
    assert harness.hass.states.get("switch.plug").state == "off"
