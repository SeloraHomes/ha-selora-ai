"""Tests for agent_steps — the agent-activity step protocol used by the chat
'what's happening' timeline."""

from __future__ import annotations

from custom_components.selora_ai.agent_steps import (
    decode_step,
    encode_step,
    encode_tool_step,
    is_step_chunk,
    make_step,
    tool_step_icon,
    tool_step_label,
)
from custom_components.selora_ai.const import STREAM_STEP_PREFIX


def test_make_step_defaults_to_done() -> None:
    step = make_step("draft", "draft", "Drafted the automation")
    assert step == {
        "id": "draft",
        "kind": "draft",
        "label": "Drafted the automation",
        "status": "done",
    }


def test_make_step_includes_detail_when_given() -> None:
    step = make_step("validate", "validate", "Checked it", status="warn", detail="bad service")
    assert step["status"] == "warn"
    assert step["detail"] == "bad service"


def test_make_step_omits_empty_detail() -> None:
    assert "detail" not in make_step("x", "info", "y", detail=None)
    assert "detail" not in make_step("x", "info", "y", detail="")


def test_encode_decode_roundtrip() -> None:
    step = make_step("tool-1", "tool", "Checked device details")
    chunk = encode_step(step)
    assert is_step_chunk(chunk)
    assert chunk.startswith(STREAM_STEP_PREFIX)
    assert decode_step(chunk) == step


def test_plain_text_is_not_a_step_chunk() -> None:
    assert not is_step_chunk("I can see your Reolink doorbell")
    assert decode_step("just prose") is None


def test_decode_rejects_malformed_json() -> None:
    assert decode_step(STREAM_STEP_PREFIX + "{not json") is None


def test_decode_rejects_step_without_required_keys() -> None:
    assert decode_step(STREAM_STEP_PREFIX + '{"id": "x"}') is None  # missing label
    assert decode_step(STREAM_STEP_PREFIX + '{"label": "y"}') is None  # missing id
    assert decode_step(STREAM_STEP_PREFIX + "[1,2,3]") is None  # not an object


def test_tool_step_label_known_tool() -> None:
    assert tool_step_label("get_device_triggers") == "Checked available triggers"


def test_tool_step_label_unknown_tool_is_humanised() -> None:
    assert tool_step_label("some_new_tool") == "Used some new tool"


def test_tool_step_icon_is_specific_not_a_generic_wrench() -> None:
    # Read actions get meaning-matched icons, not a one-size wrench.
    assert tool_step_icon("search_entities") == "mdi:magnify"
    assert tool_step_icon("get_entity_state") == "mdi:eye-outline"
    assert tool_step_icon("list_devices") == "mdi:format-list-bulleted"
    # The map never resolves to the old generic wrench.
    assert all(
        tool_step_icon(t) != "mdi:wrench-outline"
        for t in ("search_entities", "list_devices", "get_device", "get_entity_state")
    )


def test_tool_step_icon_unknown_tool_falls_back() -> None:
    assert tool_step_icon("some_new_tool") == "mdi:cog-outline"


def test_encode_tool_step_builds_decodable_tool_kind() -> None:
    chunk = encode_tool_step(2, "get_entity_state")
    step = decode_step(chunk)
    assert step is not None
    assert step["id"] == "tool-2"
    assert step["kind"] == "tool"
    assert step["label"] == "Read entity state"
    assert step["status"] == "done"
    assert step["icon"] == "mdi:eye-outline"


def test_make_step_includes_icon_when_given() -> None:
    assert make_step("x", "tool", "y", icon="mdi:magnify")["icon"] == "mdi:magnify"
    assert "icon" not in make_step("x", "tool", "y")
