"""Tests for the no-response guard (`_empty_response_fallback`).

A provider that ends a stream cleanly with no content must not paint a
silent empty assistant bubble. The guard substitutes a bounded message
only when there is no *renderable* structural payload — and the slim
``q`` entity list is NOT renderable (the WS ``done`` event drops it), so
an empty reply carrying only ``q`` must still get the fallback.
"""

from __future__ import annotations

from custom_components.selora_ai import _empty_response_fallback
from custom_components.selora_ai.const import STREAM_EMPTY_RESPONSE_MESSAGE


def test_empty_with_only_q_gets_fallback() -> None:
    """`q` is not forwarded/rendered by the done event — not structural."""
    parsed = {"intent": "answer", "response": "", "r": "", "q": ["sensor.x"]}
    assert _empty_response_fallback("answer", "", parsed) == STREAM_EMPTY_RESPONSE_MESSAGE


def test_whitespace_only_gets_fallback() -> None:
    assert _empty_response_fallback("answer", "\n  \n", {}) == STREAM_EMPTY_RESPONSE_MESSAGE


def test_fallback_localized() -> None:
    """The substitute follows the request language, like other chat strings."""
    from custom_components.selora_ai import _EMPTY_RESPONSE_BY_LANG

    fr = _empty_response_fallback("answer", "", {}, "fr")
    assert fr == _EMPTY_RESPONSE_BY_LANG["fr"]
    assert fr != STREAM_EMPTY_RESPONSE_MESSAGE
    # Region subtag stripped (zh-Hant → zh); unknown → English.
    assert _empty_response_fallback("answer", "", {}, "zh-Hant") == _EMPTY_RESPONSE_BY_LANG["zh"]
    assert _empty_response_fallback("answer", "", {}, "xx") == STREAM_EMPTY_RESPONSE_MESSAGE


def test_empty_gets_fallback() -> None:
    assert _empty_response_fallback("answer", "", {}) == STREAM_EMPTY_RESPONSE_MESSAGE


def test_nonempty_response_untouched() -> None:
    assert _empty_response_fallback("answer", "All good.", {}) == "All good."


def test_structural_payload_keeps_empty_response() -> None:
    """An automation/scene/quick-action turn legitimately carries empty prose;
    the panel renders a card/chips from these object keys."""
    for key in ("automation", "scene", "quick_actions"):
        parsed = {"intent": "answer", "response": "", key: ["something"]}
        assert _empty_response_fallback("answer", "", parsed) == ""


def test_yaml_only_gets_fallback() -> None:
    """YAML without its object renders no card — the panel gates on
    msg.automation / msg.scene, not the *_yaml fields."""
    for key in ("automation_yaml", "scene_yaml"):
        parsed = {"intent": "answer", "response": "", key: "alias: x\ntrigger: []"}
        assert _empty_response_fallback("answer", "", parsed) == STREAM_EMPTY_RESPONSE_MESSAGE


def test_calls_not_structural_gets_fallback() -> None:
    """`calls` is never forwarded/rendered by the done event. A downgraded
    delayed_command (calls but intent=answer, empty prose) must NOT suppress
    the fallback — otherwise the bubble is blank."""
    parsed = {"intent": "answer", "response": "", "calls": [{"service": "light.turn_on"}]}
    assert _empty_response_fallback("answer", "", parsed) == STREAM_EMPTY_RESPONSE_MESSAGE


def test_command_approval_kept_only_with_matching_intent() -> None:
    """An approval card renders only when intent == command_approval."""
    parsed = {"command_approval": {"service": "lock.unlock"}, "response": ""}
    # Matching intent → renders the card, keep empty prose.
    assert _empty_response_fallback("command_approval", "", parsed) == ""
    # Non-approval intent → done sends None, nothing renders → fallback.
    assert _empty_response_fallback("answer", "", parsed) == STREAM_EMPTY_RESPONSE_MESSAGE
