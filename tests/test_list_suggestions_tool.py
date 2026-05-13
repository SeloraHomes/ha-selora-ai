"""Tests for the list_suggestions tool (ToolExecutor + ToolRegistry + prompt wiring)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.selora_ai.llm_client import LLMClient
from custom_components.selora_ai.llm_client.prompts import build_architect_stream_system_prompt, build_architect_system_prompt
from custom_components.selora_ai.llm_client.prompts import (
    _read_prompt_files,
    _suggestions_prompt,
)
from custom_components.selora_ai.providers import create_provider
from custom_components.selora_ai.llm_client.prompts import build_architect_stream_system_prompt, build_architect_system_prompt
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import CHAT_TOOLS, TOOL_LIST_SUGGESTIONS, TOOL_MAP
from custom_components.selora_ai.types import SuggestionDict

import custom_components.selora_ai.llm_client.prompts as _llm_mod


@pytest.fixture(autouse=True)
def _enable_custom_component(enable_custom_integrations):
    """Auto-enable custom integrations so our domain is discoverable."""


@pytest.fixture(autouse=True)
def _preload_prompts():
    """Load prompt files so system prompts include tool-policy text."""
    _llm_mod._TOOL_POLICY_TEXT, _llm_mod._DEVICE_KNOWLEDGE_TEXT = _read_prompt_files()


def _make_executor(hass, *, is_admin: bool = False) -> ToolExecutor:
    """Create a ToolExecutor with a mocked DeviceManager."""
    dm = MagicMock()
    return ToolExecutor(hass, dm, is_admin=is_admin)


def _make_suggestion(
    *,
    suggestion_id: str = "sug-1",
    description: str = "Turn on hall light on motion",
    confidence: float = 0.85,
    evidence_summary: str = "Motion sensor triggered 12 times last week",
    status: str = "pending",
) -> SuggestionDict:
    return SuggestionDict(
        suggestion_id=suggestion_id,
        description=description,
        confidence=confidence,
        evidence_summary=evidence_summary,
        status=status,
    )


def _make_client(hass) -> LLMClient:
    provider = create_provider("anthropic", hass, api_key="test-key")
    return LLMClient(hass, provider)


# ── ToolRegistry ────────────────────────────────────────────────────


class TestToolRegistry:
    """list_suggestions is correctly registered."""

    def test_in_chat_tools(self) -> None:
        assert TOOL_LIST_SUGGESTIONS in CHAT_TOOLS

    def test_in_tool_map(self) -> None:
        assert "list_suggestions" in TOOL_MAP

    def test_not_admin(self) -> None:
        assert not TOOL_LIST_SUGGESTIONS.requires_admin

    def test_has_status_enum(self) -> None:
        param = TOOL_LIST_SUGGESTIONS.params[0]
        assert param.name == "status"
        assert set(param.enum) == {"pending", "accepted", "dismissed", "snoozed"}


# ── ToolExecutor._list_suggestions ──────────────────────────────────


class TestListSuggestionsHandler:
    """Tests for the executor handler."""

    @pytest.mark.asyncio
    async def test_happy_path(self, hass) -> None:
        """Returns filtered suggestion fields."""
        suggestions = [_make_suggestion(), _make_suggestion(suggestion_id="sug-2", confidence=0.723)]
        mock_store = MagicMock()
        mock_store.get_suggestions = AsyncMock(return_value=suggestions)

        executor = _make_executor(hass)
        with patch("custom_components.selora_ai._get_pattern_store", return_value=mock_store):
            result = await executor.execute("list_suggestions", {"status": "pending"})

        assert len(result["suggestions"]) == 2
        assert result["total"] == 2
        assert result["suggestions"][0]["suggestion_id"] == "sug-1"
        assert result["suggestions"][0]["confidence"] == 0.85
        assert result["suggestions"][1]["confidence"] == 0.72  # rounded to 2 decimals

    @pytest.mark.asyncio
    async def test_empty_store(self, hass) -> None:
        """Store has no suggestions for the requested status."""
        mock_store = MagicMock()
        mock_store.get_suggestions = AsyncMock(return_value=[])

        executor = _make_executor(hass)
        with patch("custom_components.selora_ai._get_pattern_store", return_value=mock_store):
            result = await executor.execute("list_suggestions", {})

        assert result["suggestions"] == []
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_store_unavailable(self, hass) -> None:
        """Pattern store not initialised yet."""
        executor = _make_executor(hass)
        with patch("custom_components.selora_ai._get_pattern_store", return_value=None):
            result = await executor.execute("list_suggestions", {})

        assert result["suggestions"] == []
        assert "message" in result

    @pytest.mark.asyncio
    async def test_caps_at_ten(self, hass) -> None:
        """Only the first 10 suggestions are returned."""
        suggestions = [_make_suggestion(suggestion_id=f"sug-{i}") for i in range(15)]
        mock_store = MagicMock()
        mock_store.get_suggestions = AsyncMock(return_value=suggestions)

        executor = _make_executor(hass)
        with patch("custom_components.selora_ai._get_pattern_store", return_value=mock_store):
            result = await executor.execute("list_suggestions", {"status": "pending"})

        assert len(result["suggestions"]) == 10
        assert result["total"] == 15

    @pytest.mark.asyncio
    async def test_default_status_is_pending(self, hass) -> None:
        """Omitting status defaults to 'pending'."""
        mock_store = MagicMock()
        mock_store.get_suggestions = AsyncMock(return_value=[])

        executor = _make_executor(hass)
        with patch("custom_components.selora_ai._get_pattern_store", return_value=mock_store):
            await executor.execute("list_suggestions", {})

        mock_store.get_suggestions.assert_called_once_with(status="pending")

    @pytest.mark.asyncio
    async def test_invalid_status_rejected(self, hass) -> None:
        """An invalid status value returns an error without hitting the store."""
        mock_store = MagicMock()
        mock_store.get_suggestions = AsyncMock(return_value=[])

        executor = _make_executor(hass)
        with patch("custom_components.selora_ai._get_pattern_store", return_value=mock_store):
            result = await executor.execute("list_suggestions", {"status": "bogus"})

        assert "error" in result
        assert "bogus" in result["error"]
        mock_store.get_suggestions.assert_not_called()

    @pytest.mark.asyncio
    async def test_only_selected_fields_returned(self, hass) -> None:
        """Extra fields on SuggestionDict are not leaked to the LLM."""
        suggestion: SuggestionDict = {
            "suggestion_id": "sug-1",
            "pattern_id": "pat-1",
            "source": "pattern",
            "confidence": 0.9,
            "description": "desc",
            "evidence_summary": "evidence",
            "automation_yaml": "trigger: ...",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00",
        }
        mock_store = MagicMock()
        mock_store.get_suggestions = AsyncMock(return_value=[suggestion])

        executor = _make_executor(hass)
        with patch("custom_components.selora_ai._get_pattern_store", return_value=mock_store):
            result = await executor.execute("list_suggestions", {})

        item = result["suggestions"][0]
        assert set(item.keys()) == {"suggestion_id", "description", "confidence", "evidence_summary"}


# ── Prompt wiring ───────────────────────────────────────────────────


class TestSuggestionsPrompt:
    """SUGGESTIONS block is gated on tool availability."""

    def test_shared_helper_content(self) -> None:
        text = _suggestions_prompt()
        assert "list_suggestions" in text
        assert "evidence_summary" in text

    def test_json_prompt_includes_suggestions_when_tools_available(self, hass) -> None:
        prompt = build_architect_system_prompt(tools_available=True)
        assert "list_suggestions" in prompt
        assert "SUGGESTIONS:" in prompt

    def test_json_prompt_excludes_suggestions_without_tools(self, hass) -> None:
        prompt = build_architect_system_prompt(tools_available=False)
        assert "list_suggestions" not in prompt
        assert "SUGGESTIONS:" not in prompt

    def test_stream_prompt_includes_suggestions_when_tools_available(self, hass) -> None:
        prompt = build_architect_stream_system_prompt(tools_available=True)
        assert "list_suggestions" in prompt
        assert "SUGGESTIONS:" in prompt

    def test_stream_prompt_excludes_suggestions_without_tools(self, hass) -> None:
        prompt = build_architect_stream_system_prompt(tools_available=False)
        assert "list_suggestions" not in prompt
        assert "SUGGESTIONS:" not in prompt

    def test_both_prompts_use_same_block(self, hass) -> None:
        """The SUGGESTIONS block is identical in both prompts (shared helper)."""
        client = _make_client(hass)
        json_prompt = build_architect_system_prompt(tools_available=True)
        stream_prompt = build_architect_stream_system_prompt(tools_available=True)
        block = _suggestions_prompt()
        assert block in json_prompt
        assert block in stream_prompt

    def test_default_is_no_tools(self, hass) -> None:
        """Calling without tools_available defaults to False (no suggestions block)."""
        prompt = build_architect_system_prompt()
        assert "SUGGESTIONS:" not in prompt
