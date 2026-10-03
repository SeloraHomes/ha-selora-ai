"""Tests for the LLM-written version summaries."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant

from custom_components.selora_ai.automation_store import AutomationStore
from custom_components.selora_ai.version_summaries import schedule_missing_summaries

from .conftest import MockStore

_PUMP = "switch.pool_pump"


def _automation(days: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "alias": "Pool pump",
        "triggers": [{"trigger": "time", "at": "07:00:00", **extra}],
        "conditions": [{"condition": "time", "weekday": days}],
        "actions": [{"action": "switch.turn_on", "target": {"entity_id": _PUMP}}],
    }


def _llm(reply: str | None) -> MagicMock:
    llm = MagicMock()
    llm.summarize_automation_change = AsyncMock(return_value=reply)
    return llm


async def _store(hass: HomeAssistant) -> AutomationStore:
    with patch("custom_components.selora_ai.automation_store.Store") as store_cls:
        store_cls.return_value = MockStore()
        return AutomationStore(hass)


async def test_a_saved_edit_gets_its_sentence_stored(hass: HomeAssistant) -> None:
    hass.states.async_set(_PUMP, "off", {"friendly_name": "Pool pump"})
    llm = _llm("Now runs on Thursdays only.")
    store = await _store(hass)
    with patch("custom_components.selora_ai.version_summaries._get_llm", return_value=llm):
        await store.add_version("a1", "", _automation(["thu", "fri"]), "Created")
        await store.add_version("a1", "", _automation(["thu"]), "Refined via chat")
        await hass.async_block_till_done(wait_background_tasks=True)

    first, second = await store.get_versions("a1")
    assert "summary" not in first
    assert second["summary"] == "Now runs on Thursdays only."
    assert second["summary_language"] == "en"
    llm.summarize_automation_change.assert_awaited_once()
    names = llm.summarize_automation_change.await_args.args[2]
    assert names == {_PUMP: "Pool pump"}


async def test_a_syntax_only_rewrite_costs_no_call(hass: HomeAssistant) -> None:
    llm = _llm("unused")
    store = await _store(hass)
    legacy = _automation(["thu"])
    legacy["trigger"] = [{"platform": "time", "at": "07:00:00"}]
    del legacy["triggers"]
    with patch("custom_components.selora_ai.version_summaries._get_llm", return_value=llm):
        await store.add_version("a1", "", legacy, "Created")
        await store.add_version("a1", "", _automation(["thu"]), "Refined via chat")
        await hass.async_block_till_done(wait_background_tasks=True)
    llm.summarize_automation_change.assert_not_awaited()


async def test_no_llm_or_no_reply_leaves_the_fallback(hass: HomeAssistant) -> None:
    store = await _store(hass)
    with patch("custom_components.selora_ai.version_summaries._get_llm", return_value=_llm(None)):
        await store.add_version("a1", "", _automation(["thu", "fri"]), "Created")
        await store.add_version("a1", "", _automation(["thu"]), "Refined via chat")
        await hass.async_block_till_done(wait_background_tasks=True)
    assert "summary" not in (await store.get_versions("a1"))[1]


async def test_history_written_before_summaries_is_backfilled(hass: HomeAssistant) -> None:
    store = await _store(hass)
    with patch("custom_components.selora_ai.version_summaries._get_llm", return_value=None):
        await store.add_version("a1", "", _automation(["thu", "fri"]), "Created")
        await store.add_version("a1", "", _automation(["thu"]), "Refined via chat")
        await hass.async_block_till_done(wait_background_tasks=True)
    versions = await store.get_versions("a1")
    llm = _llm("Now runs on Thursdays only.")
    with patch("custom_components.selora_ai.version_summaries._get_llm", return_value=llm):
        schedule_missing_summaries(hass, store, "a1", versions)
        schedule_missing_summaries(hass, store, "a1", versions)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert (await store.get_versions("a1"))[1]["summary"] == "Now runs on Thursdays only."
    llm.summarize_automation_change.assert_awaited_once()


async def test_the_enabled_state_is_never_shown_to_the_model(hass: HomeAssistant) -> None:
    """`initial_state: false -> true` came back as "and the automation is now
    enabled" on every accepted refinement. It is a fact about the save, not
    about what the automation does, so the model must not see it."""
    llm = _llm("Now runs on Thursdays only.")
    store = await _store(hass)
    with patch("custom_components.selora_ai.version_summaries._get_llm", return_value=llm):
        await store.add_version(
            "a1", "", {**_automation(["thu", "fri"]), "initial_state": False, "id": "a1"}, "Created"
        )
        await store.add_version(
            "a1", "", {**_automation(["thu"]), "initial_state": True, "id": "a1"}, "Refined"
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    before, after = llm.summarize_automation_change.await_args.args[:2]
    for document in (before, after):
        assert "initial_state" not in document
        assert not any(line.startswith("id:") for line in document.splitlines())


async def test_enabling_alone_costs_no_call(hass: HomeAssistant) -> None:
    llm = _llm("unused")
    store = await _store(hass)
    with patch("custom_components.selora_ai.version_summaries._get_llm", return_value=llm):
        await store.add_version(
            "a1", "", {**_automation(["thu"]), "initial_state": False}, "Created"
        )
        await store.add_version(
            "a1", "", {**_automation(["thu"]), "initial_state": True}, "Enabled"
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    llm.summarize_automation_change.assert_not_awaited()


async def test_a_written_summary_is_never_rewritten(hass: HomeAssistant) -> None:
    """Once a version has its sentence it keeps it: reopening the history,
    or changing the home's language, costs no further call."""
    llm = _llm("Now runs on Thursdays only.")
    store = await _store(hass)
    with patch("custom_components.selora_ai.version_summaries._get_llm", return_value=llm):
        await store.add_version("a1", "", _automation(["thu", "fri"]), "Created")
        await store.add_version("a1", "", _automation(["thu"]), "Refined via chat")
        await hass.async_block_till_done(wait_background_tasks=True)

        hass.config.language = "fr"
        for _ in range(2):
            schedule_missing_summaries(hass, store, "a1", await store.get_versions("a1"))
            await hass.async_block_till_done(wait_background_tasks=True)

    llm.summarize_automation_change.assert_awaited_once()
    assert (await store.get_versions("a1"))[1]["summary"] == "Now runs on Thursdays only."


async def test_the_model_is_handed_the_change_not_asked_to_find_it(
    hass: HomeAssistant,
) -> None:
    """Given only two documents, the model restated the whole rule ("now
    turns on Thursday at 07:00 and off Friday at 20:00") and narrated fields
    that were not edits. The computed change list is its input instead."""
    llm = _llm("No longer runs on Fridays.")
    store = await _store(hass)
    with patch("custom_components.selora_ai.version_summaries._get_llm", return_value=llm):
        await store.add_version(
            "a1", "", {**_automation(["thu", "fri"]), "initial_state": False}, "Created"
        )
        await store.add_version(
            "a1", "", {**_automation(["thu"]), "initial_state": True}, "Refined"
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    changes = llm.summarize_automation_change.await_args.kwargs["changes"]
    assert "weekday" in changes
    assert '"fri"' in changes
    assert "initial_state" not in changes
    assert "07:00" not in changes  # unchanged, so not a fact to describe


async def test_a_description_only_edit_costs_no_call(hass: HomeAssistant) -> None:
    llm = _llm("unused")
    store = await _store(hass)
    with patch("custom_components.selora_ai.version_summaries._get_llm", return_value=llm):
        await store.add_version("a1", "", {**_automation(["thu"]), "description": "a"}, "Created")
        await store.add_version("a1", "", {**_automation(["thu"]), "description": "b"}, "Edited")
        await hass.async_block_till_done(wait_background_tasks=True)
    llm.summarize_automation_change.assert_not_awaited()


async def test_the_prompt_leads_with_the_change_list(hass: HomeAssistant) -> None:
    from custom_components.selora_ai.llm_client import LLMClient
    from custom_components.selora_ai.providers import create_provider

    provider = create_provider("anthropic", hass, api_key="test-key")
    provider.send_request = AsyncMock(return_value=("No longer runs on Fridays.", None))
    client = LLMClient(hass, provider)
    result = await client.summarize_automation_change(
        "before: 1", "after: 2", {}, "en", changes="- conditions #1 weekday: [thu, fri] -> [thu]"
    )
    assert result == "No longer runs on Fridays."
    sent = provider.send_request.await_args.kwargs
    content = sent["messages"][0]["content"]
    assert content.startswith("CHANGES:\n- conditions #1 weekday")
    assert "enabled" not in sent["system"]
