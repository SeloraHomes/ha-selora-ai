"""Tests for listing, creating, changing and deleting Assist pipelines over MCP."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import (
    TOOL_DELETE_ASSIST_PIPELINE,
    TOOL_LIST_ASSIST_PIPELINES,
    TOOL_SET_ASSIST_PIPELINE,
)

KITCHEN = {
    "name": "Kitchen",
    "language": "fr",
    "conversation_engine": "conversation.home_assistant",
    "conversation_language": "fr",
}


@pytest.fixture
async def assist(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "assist_pipeline", {})
    await hass.async_block_till_done()


async def _mcp(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[tool](hass, arguments)


async def test_the_default_pipeline_is_listed_as_preferred(
    hass: HomeAssistant, assist: None
) -> None:
    listed = await _mcp(hass, TOOL_LIST_ASSIST_PIPELINES)

    (pipeline,) = listed["pipelines"]
    assert listed["preferred"] == pipeline["id"]
    assert set(listed["engines"]) == {
        "conversation_engine",
        "stt_engine",
        "tts_engine",
        "wake_word_entity",
    }


async def test_engines_that_are_not_entities_are_listed(hass: HomeAssistant, assist: None) -> None:
    """Home Assistant's own agent is registered with the agent manager; legacy
    speech providers live in each component's provider registry."""
    hass.data["stt_providers"] = {"legacy_stt": object()}

    engines = (await _mcp(hass, TOOL_LIST_ASSIST_PIPELINES))["engines"]

    assert "conversation.home_assistant" in engines["conversation_engine"]
    assert "legacy_stt" in engines["stt_engine"]


async def test_a_pipeline_is_created_and_made_preferred(hass: HomeAssistant, assist: None) -> None:
    created = await _mcp(hass, TOOL_SET_ASSIST_PIPELINE, preferred=True, **KITCHEN)

    assert created["status"] == "created", created
    assert created["preferred"] is True
    pipeline = created["pipeline"]
    assert pipeline["name"] == "Kitchen"
    assert pipeline["stt_engine"] is None
    listed = await _mcp(hass, TOOL_LIST_ASSIST_PIPELINES)
    assert listed["preferred"] == pipeline["id"]


async def test_a_change_keeps_every_setting_it_did_not_name(
    hass: HomeAssistant, assist: None
) -> None:
    created = await _mcp(hass, TOOL_SET_ASSIST_PIPELINE, **KITCHEN)

    changed = await _mcp(
        hass,
        TOOL_SET_ASSIST_PIPELINE,
        pipeline_id=created["pipeline"]["id"],
        name="Cuisine",
        prefer_local_intents=True,
    )

    assert changed["status"] == "updated", changed
    pipeline = changed["pipeline"]
    assert pipeline["name"] == "Cuisine"
    assert pipeline["prefer_local_intents"] is True
    assert pipeline["language"] == "fr"
    assert pipeline["conversation_engine"] == "conversation.home_assistant"


async def test_an_engine_without_its_language_is_refused(hass: HomeAssistant, assist: None) -> None:
    result = await _mcp(hass, TOOL_SET_ASSIST_PIPELINE, tts_engine="tts.piper", **KITCHEN)

    assert "would refuse" in result["error"]
    assert len((await _mcp(hass, TOOL_LIST_ASSIST_PIPELINES))["pipelines"]) == 1


async def test_a_new_pipeline_needs_its_required_settings(
    hass: HomeAssistant, assist: None
) -> None:
    result = await _mcp(hass, TOOL_SET_ASSIST_PIPELINE, name="Half")

    assert "would refuse" in result["error"]


async def test_an_empty_string_turns_an_engine_off(hass: HomeAssistant, assist: None) -> None:
    created = await _mcp(
        hass, TOOL_SET_ASSIST_PIPELINE, tts_engine="tts.piper", tts_language="fr", **KITCHEN
    )

    changed = await _mcp(
        hass,
        TOOL_SET_ASSIST_PIPELINE,
        pipeline_id=created["pipeline"]["id"],
        tts_engine="",
        tts_language="",
    )

    assert changed["pipeline"]["tts_engine"] is None


async def test_a_pipeline_is_deleted_but_never_the_preferred_one(
    hass: HomeAssistant, assist: None
) -> None:
    created = await _mcp(hass, TOOL_SET_ASSIST_PIPELINE, **KITCHEN)
    preferred = (await _mcp(hass, TOOL_LIST_ASSIST_PIPELINES))["preferred"]

    refused = await _mcp(hass, TOOL_DELETE_ASSIST_PIPELINE, pipeline_id=preferred)
    deleted = await _mcp(hass, TOOL_DELETE_ASSIST_PIPELINE, pipeline_id=created["pipeline"]["id"])

    assert "preferred pipeline" in refused["error"]
    assert deleted["status"] == "deleted", deleted
    assert [p["id"] for p in (await _mcp(hass, TOOL_LIST_ASSIST_PIPELINES))["pipelines"]] == [
        preferred
    ]


async def test_an_unknown_pipeline_points_at_the_list(hass: HomeAssistant, assist: None) -> None:
    result = await _mcp(hass, TOOL_SET_ASSIST_PIPELINE, pipeline_id="nope", name="X")

    assert "list_assist_pipelines" in result["error"]


async def test_without_assist_it_says_so(hass: HomeAssistant) -> None:
    result = await _mcp(hass, TOOL_LIST_ASSIST_PIPELINES)

    assert "not set up" in result["error"]


async def test_reading_is_open_and_changing_needs_admin() -> None:
    assert TOOL_LIST_ASSIST_PIPELINES in mcp_access._READ_ONLY_TOOLS
    assert TOOL_SET_ASSIST_PIPELINE in mcp_access._ADMIN_TOOLS
    assert TOOL_DELETE_ASSIST_PIPELINE in mcp_access._ADMIN_TOOLS
