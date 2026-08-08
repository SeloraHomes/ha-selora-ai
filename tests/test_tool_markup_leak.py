"""Tests for leaked tool-call markup handling in ``llm_client.parsers``.

When a model spends its tool budget and the final round withholds tools, it
sometimes writes the tool call it *wanted* to make as plain text instead of a
real tool_use block — ``<tool_calls><invoke name="list_devices">…`` — often
mangled with tokenizer special-token pieces (``< |  | DSML |  | tool_calls>``).
None of it executes and it must never reach the panel.

- ``strip_leaked_tool_markup`` — non-streaming: truncate from the marker.
- ``MarkupLeakGuard`` — streaming: suppress the leak across chunk boundaries.
"""

from __future__ import annotations

import pytest

from custom_components.selora_ai.llm_client.parsers import (
    MarkupLeakGuard,
    strip_leaked_tool_markup,
)

# The exact leak from the bug report, mangled special tokens and all.
MANGLED_LEAK = (
    "Let me check the Reolink doorbell device for any device triggers.\n\n"
    "< |  | DSML |  | tool_calls>\n"
    '< |  | DSML |  | invoke name="list_devices">\n'
    '< |  | DSML |  | parameter name="domain" string="true">binary_sensor'
    "</ |  | DSML |  | parameter>\n"
    "</ |  | DSML |  | invoke>\n"
    "</ |  | DSML |  | tool_calls>"
)

# The clean Anthropic-style form (no tokenizer mangling).
CLEAN_LEAK = (
    "Let me look that up.\n\n"
    '<tool_calls>\n<invoke name="list_devices">\n'
    '<parameter name="domain">binary_sensor</parameter>\n'
    "</invoke>\n</tool_calls>"
)


class TestStripLeakedToolMarkup:
    def test_strips_mangled_leak_keeping_prose(self) -> None:
        out = strip_leaked_tool_markup(MANGLED_LEAK)
        assert out == "Let me check the Reolink doorbell device for any device triggers."

    def test_strips_clean_anthropic_leak(self) -> None:
        out = strip_leaked_tool_markup(CLEAN_LEAK)
        assert out == "Let me look that up."

    def test_all_leak_returns_empty(self) -> None:
        assert strip_leaked_tool_markup('<invoke name="x">y</invoke>').strip() == ""

    def test_passthrough_when_no_marker(self) -> None:
        text = "Your kitchen light is on and the front door is locked."
        assert strip_leaked_tool_markup(text) == text

    @pytest.mark.parametrize(
        "text",
        [
            "Set the thermostat when temp < 20 degrees.",
            "Trigger when humidity < 40 and it is < 5pm.",
            "Compare a < b and keep going.",
            "",
            None,
        ],
    )
    def test_no_false_positive_on_prose_angle_brackets(self, text: str | None) -> None:
        assert strip_leaked_tool_markup(text) == text


# DeepSeek fences its special tokens with U+FF5C FULLWIDTH VERTICAL LINE, not
# ASCII "|", and writes U+2581 where the token name has an underscore. Built
# from codepoints so the intent survives an editor that "helpfully" normalises
# the glyphs — matching only ASCII "|" let every DeepSeek leak reach the panel
# while the ASCII fixtures above kept CI green.
_FW = "｜"  # ｜
_SEP = "▁"  # ▁

FULLWIDTH_LEAK = (
    "Let me check the Lutron Caséta integration for any Pico remotes.\n\n"
    f"<{_FW}DSML{_FW}tool_calls>\n"
    f'<{_FW}DSML{_FW}invoke name="get_entity_history">\n'
    f'<{_FW}DSML{_FW}parameter name="entity_id" string="true">light.master_bedroom_main_lights'
    f"</{_FW}DSML{_FW}parameter>\n"
    f"</{_FW}DSML{_FW}invoke>\n"
    f"</{_FW}DSML{_FW}tool_calls>"
)

# DeepSeek's native token names, with no DSML connective at all.
NATIVE_LEAK = (
    f"Checking now.\n\n<{_FW}tool{_SEP}calls{_SEP}begin{_FW}>\n"
    f"<{_FW}tool{_SEP}call{_SEP}begin{_FW}>get_entity_history"
)


def _drive(guard: MarkupLeakGuard, chunks: list[str]) -> str:
    """Feed chunks through the guard and return the concatenated emission."""
    out = "".join(guard.feed(c) for c in chunks)
    return out + guard.flush()


def _chunked(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


class TestFullwidthPipeLeak:
    """The delimiter is not always ASCII — see the module constants above."""

    def test_strip_removes_fullwidth_leak(self) -> None:
        assert strip_leaked_tool_markup(FULLWIDTH_LEAK) == (
            "Let me check the Lutron Caséta integration for any Pico remotes."
        )

    def test_strip_removes_native_token_leak(self) -> None:
        assert strip_leaked_tool_markup(NATIVE_LEAK) == "Checking now."

    @pytest.mark.parametrize("size", [1, 2, 3, 5, 11, 64])
    def test_guard_suppresses_fullwidth_at_every_chunk_boundary(self, size: int) -> None:
        guard = MarkupLeakGuard()
        out = _drive(guard, _chunked(FULLWIDTH_LEAK, size))
        assert guard.suppressed
        assert out == "Let me check the Lutron Caséta integration for any Pico remotes."

    @pytest.mark.parametrize("size", [1, 2, 3, 5, 11, 64])
    def test_guard_suppresses_native_tokens_at_every_chunk_boundary(self, size: int) -> None:
        guard = MarkupLeakGuard()
        out = _drive(guard, _chunked(NATIVE_LEAK, size))
        assert guard.suppressed
        assert out == "Checking now."

    @pytest.mark.parametrize(
        "text",
        [
            "The reading is 12│34 on the display.",
            "Use a ｜ to separate the columns.",
            "temp < 20 and a < b",
        ],
    )
    def test_no_false_positive_on_prose_containing_pipes(self, text: str) -> None:
        assert strip_leaked_tool_markup(text) == text
        guard = MarkupLeakGuard()
        assert _drive(guard, _chunked(text, 3)) == text
        assert not guard.suppressed


class TestMarkupLeakGuard:
    def test_suppresses_leak_streamed_in_one_chunk(self) -> None:
        assert _drive(MarkupLeakGuard(), [MANGLED_LEAK]) == (
            "Let me check the Reolink doorbell device for any device triggers."
        )

    def test_suppresses_leak_split_across_chunks(self) -> None:
        # Split the marker mid-token so the guard must hold back across feeds.
        chunks = ["Looking now.\n\n<tool", "_calls>\n<invoke ", 'name="x">a</invoke>']
        assert _drive(MarkupLeakGuard(), chunks) == "Looking now."

    def test_suppresses_leak_split_char_by_char(self) -> None:
        guard = MarkupLeakGuard()
        assert _drive(guard, list(CLEAN_LEAK)) == "Let me look that up."
        assert guard.suppressed is True

    def test_normal_prose_passes_through_unchanged(self) -> None:
        chunks = ["The kitchen light ", "is on and temp < 20", " right now."]
        assert _drive(MarkupLeakGuard(), chunks) == (
            "The kitchen light is on and temp < 20 right now."
        )

    def test_angle_bracket_not_a_marker_is_released(self) -> None:
        # A '<' followed by non-marker text must not be swallowed.
        assert _drive(MarkupLeakGuard(), ["a < b < c done"]) == "a < b < c done"

    def test_dsml_mangling_split_across_chunks(self) -> None:
        chunks = ["Checking.\n\n< |  | DS", "ML |  | tool_calls>\n... junk"]
        assert _drive(MarkupLeakGuard(), chunks) == "Checking."

    def test_partial_marker_at_stream_end_is_dropped(self) -> None:
        # Stream cut off right after opening a leak — the dangling tag is junk.
        assert _drive(MarkupLeakGuard(), ["Done.", " <invo"]) == "Done. "

    def test_trailing_angle_in_prose_survives_when_unambiguous(self) -> None:
        assert _drive(MarkupLeakGuard(), ["value is 5 < 9"]) == "value is 5 < 9"

    @pytest.mark.parametrize(
        ("chunks", "expected"),
        [
            # Prose word that starts with a keyword, split exactly after the
            # keyword — must NOT be suppressed once the next chunk arrives.
            (["The <parameter", "ized> form works."], "The <parameterized> form works."),
            (["Use <invoke", "able> helpers."], "Use <invokeable> helpers."),
            (["A <tool_calls", "back> pattern."], "A <tool_callsback> pattern."),
            # Split exactly after the keyword, then a real delimiter → leak.
            (["Looking. <invoke", ' name="x">a</invoke>'], "Looking."),
            (["Looking. <parameter", ">v</parameter>"], "Looking."),
        ],
    )
    def test_keyword_at_chunk_edge_waits_for_delimiter(
        self, chunks: list[str], expected: str
    ) -> None:
        assert _drive(MarkupLeakGuard(), chunks) == expected

    def test_keyword_at_chunk_edge_not_suppressed_prematurely(self) -> None:
        # After the ambiguous first chunk, nothing is emitted yet and the
        # guard has NOT latched into suppression.
        guard = MarkupLeakGuard()
        assert guard.feed("The <parameter") == "The"
        assert guard.suppressed is False
        assert guard.feed("ized> works") == " <parameterized> works"
        assert guard.suppressed is False
