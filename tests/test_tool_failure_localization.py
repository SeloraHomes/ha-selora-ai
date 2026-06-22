"""Tests that tool-loop exhaustion messaging stays localized.

`_tool_failure_response` must use the locale-aware `_exhaustion_text`
default; callers must not force an English `suffix` (a regression that
made non-English replies partly/entirely English).
"""

from __future__ import annotations

from custom_components.selora_ai.llm_client.command_policy import (
    _EXHAUSTION_NO_EXEC_BY_LANG,
    _EXHAUSTION_RAN_OUT_BY_LANG,
    _tool_failure_response,
)


def test_no_exec_localized_french() -> None:
    out = _tool_failure_response([], language="fr")
    assert out == _EXHAUSTION_NO_EXEC_BY_LANG["fr"]


def test_no_exec_localized_german() -> None:
    out = _tool_failure_response(None, language="de")
    assert out == _EXHAUSTION_NO_EXEC_BY_LANG["de"]


def test_ran_out_localized_french() -> None:
    log = [
        {
            "tool": "execute_command",
            "result": {"executed": True, "service": "light.turn_off", "entity_ids": ["light.kitchen"]},
        }
    ]
    out = _tool_failure_response(log, language="fr")
    assert _EXHAUSTION_RAN_OUT_BY_LANG["fr"] in out


def test_unknown_locale_falls_back_to_english() -> None:
    out = _tool_failure_response([], language="xx")
    assert out == _EXHAUSTION_NO_EXEC_BY_LANG["en"]
