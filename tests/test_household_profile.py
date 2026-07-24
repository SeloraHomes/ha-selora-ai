"""Tests for the household profile ("soul/memory") feature.

Covers the sanitizer, the prompt-block helper, injection into all four
system-prompt builders, and the LLMClient hot-update path.
"""

from __future__ import annotations

from custom_components.selora_ai.const import (
    HOUSEHOLD_PROFILE_LOCAL_MAX_CHARS,
    HOUSEHOLD_PROFILE_MAX_CHARS,
)
from custom_components.selora_ai.helpers import sanitize_household_profile
from custom_components.selora_ai.llm_client import LLMClient
from custom_components.selora_ai.llm_client.prompts import (
    _household_profile_block,
    build_architect_stream_system_prompt,
    build_architect_system_prompt,
    build_minimal_architect_system_prompt,
    build_suggestions_system_prompt,
)
from custom_components.selora_ai.providers import create_provider


class TestSanitizeHouseholdProfile:
    """The stored-value sanitizer."""

    def test_empty_and_none(self) -> None:
        assert sanitize_household_profile(None, 2000) == ""
        assert sanitize_household_profile("", 2000) == ""
        assert sanitize_household_profile("   \n\n ", 2000) == ""

    def test_preserves_newlines(self) -> None:
        out = sanitize_household_profile("Line 1\nLine 2\nLine 3", 2000)
        assert out == "Line 1\nLine 2\nLine 3"

    def test_strips_control_chars_but_keeps_tab(self) -> None:
        # NUL / bell / other C0 controls are dropped; tab and newline survive.
        out = sanitize_household_profile("a\x00b\x07c\td\ne", 2000)
        assert out == "abc\td\ne"

    def test_normalizes_crlf(self) -> None:
        out = sanitize_household_profile("a\r\nb\rc", 2000)
        assert out == "a\nb\nc"

    def test_collapses_blank_line_runs(self) -> None:
        out = sanitize_household_profile("a\n\n\n\n\nb", 2000)
        assert out == "a\n\nb"

    def test_hard_caps_length(self) -> None:
        out = sanitize_household_profile("x" * 5000, 100)
        assert len(out) == 100
        assert out.endswith("...")

    def test_trailing_whitespace_per_line_stripped(self) -> None:
        out = sanitize_household_profile("hello   \nworld\t\t", 2000)
        assert out == "hello\nworld"


class TestHouseholdProfileBlock:
    """The prompt-block helper."""

    def test_empty_in_empty_out(self) -> None:
        assert _household_profile_block(None) == ""
        assert _household_profile_block("") == ""
        assert _household_profile_block("   ") == ""

    def test_cloud_block_labels_and_contains_text(self) -> None:
        block = _household_profile_block("Family of 4, dog named Rex")
        assert "HOUSEHOLD PROFILE" in block
        assert "Family of 4, dog named Rex" in block
        # Safety framing: cannot override safety/confirmation/risk rules.
        assert "MUST NOT override" in block
        assert "never as instructions" in block.lower()

    def test_cloud_cap(self) -> None:
        block = _household_profile_block("x" * 5000)
        # The profile text itself is capped to the cloud limit.
        assert "x" * HOUSEHOLD_PROFILE_MAX_CHARS not in block
        assert len(block) < HOUSEHOLD_PROFILE_MAX_CHARS + 600

    def test_local_cap_is_tighter(self) -> None:
        block = _household_profile_block("y" * 5000, local=True)
        assert block.count("y") <= HOUSEHOLD_PROFILE_LOCAL_MAX_CHARS
        # Local form is compact — no multi-line safety paragraph.
        assert "HOME CONTEXT" in block
        assert "HOUSEHOLD PROFILE" not in block

    def test_local_much_shorter_than_cloud(self) -> None:
        text = "z" * 5000
        assert len(_household_profile_block(text, local=True)) < len(
            _household_profile_block(text)
        )


class TestBuilderInjection:
    """Each of the four builders injects the block when a profile is set."""

    PROFILE = "Family of 4. Kids in bed by 20:30. Dog named Rex."

    def test_architect_json_mode(self) -> None:
        with_p = build_architect_system_prompt(household_profile=self.PROFILE)
        without_p = build_architect_system_prompt()
        assert self.PROFILE in with_p
        assert "HOUSEHOLD PROFILE" in with_p
        assert self.PROFILE not in without_p
        assert "HOUSEHOLD PROFILE" not in without_p

    def test_architect_stream_mode(self) -> None:
        with_p = build_architect_stream_system_prompt(household_profile=self.PROFILE)
        assert self.PROFILE in with_p
        assert "HOUSEHOLD PROFILE" not in build_architect_stream_system_prompt()

    def test_minimal_local_mode(self) -> None:
        with_p = build_minimal_architect_system_prompt(
            "answer", household_profile=self.PROFILE
        )
        assert self.PROFILE in with_p
        assert "HOME CONTEXT" in with_p
        assert "HOME CONTEXT" not in build_minimal_architect_system_prompt("answer")

    def test_minimal_local_respects_cap(self) -> None:
        long_profile = "w" * 5000
        prompt = build_minimal_architect_system_prompt(
            "command", household_profile=long_profile
        )
        # The injected profile run is truncated to the local cap: the full
        # 5000-char run never survives, only a capped slice.
        assert "w" * (HOUSEHOLD_PROFILE_LOCAL_MAX_CHARS + 1) not in prompt
        assert "w" * (HOUSEHOLD_PROFILE_LOCAL_MAX_CHARS - 3) in prompt

    def test_suggestions_builder(self) -> None:
        with_p = build_suggestions_system_prompt(5, household_profile=self.PROFILE)
        assert self.PROFILE in with_p
        # The JSON-only closing instruction must remain last.
        assert with_p.rstrip().endswith("No explanation.")
        assert "HOUSEHOLD PROFILE" not in build_suggestions_system_prompt(5)


class TestLLMClientWiring:
    """Constructor arg + hot setter thread the profile into prompts."""

    def _client(self, hass, profile: str = "") -> LLMClient:
        provider = create_provider("anthropic", hass, api_key="test-key")
        return LLMClient(hass, provider, household_profile=profile)

    def test_constructor_stores_profile(self, hass) -> None:
        client = self._client(hass, "Dog named Rex")
        assert client._household_profile == "Dog named Rex"

    def test_none_coerced_to_empty(self, hass) -> None:
        provider = create_provider("anthropic", hass, api_key="test-key")
        client = LLMClient(hass, provider, household_profile=None)  # type: ignore[arg-type]
        assert client._household_profile == ""

    def test_set_household_profile_hot_update(self, hass) -> None:
        client = self._client(hass, "old")
        client.set_household_profile("new profile")
        assert client._household_profile == "new profile"
        client.set_household_profile(None)
        assert client._household_profile == ""
