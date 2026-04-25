"""Tests for accept_suggestion and dismiss_suggestion tools."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.selora_ai.llm_client import (
    LLMClient,
    _read_prompt_files,
    _suggestions_prompt,
)
from custom_components.selora_ai.providers import create_provider
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import (
    CHAT_TOOLS,
    TOOL_ACCEPT_SUGGESTION,
    TOOL_DISMISS_SUGGESTION,
    TOOL_MAP,
)
from custom_components.selora_ai.types import SuggestionDict

import custom_components.selora_ai.llm_client as _llm_mod


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


def _make_client(hass) -> LLMClient:
    provider = create_provider("anthropic", hass, api_key="test-key")
    return LLMClient(hass, provider)


def _make_suggestion(
    *,
    suggestion_id: str = "sug-1",
    description: str = "Turn on hall light on motion",
    confidence: float = 0.85,
    evidence_summary: str = "Motion sensor triggered 12 times last week",
    status: str = "pending",
    automation_data: dict | None = None,
) -> SuggestionDict:
    return SuggestionDict(
        suggestion_id=suggestion_id,
        description=description,
        confidence=confidence,
        evidence_summary=evidence_summary,
        status=status,
        automation_data=automation_data
        or {
            "alias": "Motion Hall Light",
            "triggers": [
                {
                    "platform": "state",
                    "entity_id": "binary_sensor.hallway_motion",
                    "to": "on",
                }
            ],
            "actions": [
                {"service": "light.turn_on", "target": {"entity_id": "light.hallway"}}
            ],
        },
    )


# ── ToolRegistry ────────────────────────────────────────────────────


class TestAcceptSuggestionRegistry:
    """accept_suggestion is correctly registered."""

    def test_in_chat_tools(self) -> None:
        assert TOOL_ACCEPT_SUGGESTION in CHAT_TOOLS

    def test_in_tool_map(self) -> None:
        assert "accept_suggestion" in TOOL_MAP

    def test_requires_admin(self) -> None:
        assert TOOL_ACCEPT_SUGGESTION.requires_admin

    def test_suggestion_id_required(self) -> None:
        param = next(p for p in TOOL_ACCEPT_SUGGESTION.params if p.name == "suggestion_id")
        assert param.required is True


class TestDismissSuggestionRegistry:
    """dismiss_suggestion is correctly registered."""

    def test_in_chat_tools(self) -> None:
        assert TOOL_DISMISS_SUGGESTION in CHAT_TOOLS

    def test_in_tool_map(self) -> None:
        assert "dismiss_suggestion" in TOOL_MAP

    def test_requires_admin(self) -> None:
        assert TOOL_DISMISS_SUGGESTION.requires_admin

    def test_suggestion_id_required(self) -> None:
        param = next(p for p in TOOL_DISMISS_SUGGESTION.params if p.name == "suggestion_id")
        assert param.required is True

    def test_reason_optional(self) -> None:
        param = next(p for p in TOOL_DISMISS_SUGGESTION.params if p.name == "reason")
        assert not getattr(param, "required", False)


# ── ToolExecutor — accept_suggestion ────────────────────────────────


class TestAcceptSuggestionHandler:
    """Tests for the executor accept_suggestion handler."""

    @pytest.mark.asyncio
    async def test_happy_path(self, hass) -> None:
        """Looks up suggestion in PatternStore, creates automation, marks accepted."""
        suggestion = _make_suggestion()
        mock_store = MagicMock()
        mock_store.get_suggestions = AsyncMock(return_value=[suggestion])
        mock_store.update_suggestion_status = AsyncMock(return_value=True)

        executor = _make_executor(hass, is_admin=True)
        with (
            patch("custom_components.selora_ai._get_pattern_store", return_value=mock_store),
            patch(
                "custom_components.selora_ai.automation_utils.async_create_automation",
                new_callable=AsyncMock,
                return_value={"success": True, "automation_id": "selora_ai_abc12345"},
            ) as mock_create,
        ):
            result = await executor.execute("accept_suggestion", {"suggestion_id": "sug-1"})

        assert result["status"] == "accepted"
        assert result["suggestion_id"] == "sug-1"
        assert result["automation_id"] == "selora_ai_abc12345"
        assert "risk_assessment" in result
        mock_create.assert_awaited_once()
        mock_store.update_suggestion_status.assert_awaited_once_with("sug-1", status="accepted")

    @pytest.mark.asyncio
    async def test_missing_suggestion_id(self, hass) -> None:
        """Returns error when suggestion_id is empty."""
        executor = _make_executor(hass, is_admin=True)
        result = await executor.execute("accept_suggestion", {})
        assert "error" in result
        assert "suggestion_id" in result["error"]

    @pytest.mark.asyncio
    async def test_suggestion_not_found(self, hass) -> None:
        """Returns error when suggestion_id doesn't exist in PatternStore."""
        mock_store = MagicMock()
        mock_store.get_suggestions = AsyncMock(return_value=[])

        executor = _make_executor(hass, is_admin=True)
        with patch("custom_components.selora_ai._get_pattern_store", return_value=mock_store):
            result = await executor.execute("accept_suggestion", {"suggestion_id": "nonexistent"})

        assert "error" in result
        assert "not found" in result["error"]
        mock_store.get_suggestions.assert_awaited_once_with(status="pending")

    @pytest.mark.asyncio
    async def test_already_accepted_rejected(self, hass) -> None:
        """Cannot accept a suggestion that is no longer pending (prevents duplicates)."""
        mock_store = MagicMock()
        # get_suggestions(status="pending") returns empty — the suggestion exists but is accepted
        mock_store.get_suggestions = AsyncMock(return_value=[])

        executor = _make_executor(hass, is_admin=True)
        with patch("custom_components.selora_ai._get_pattern_store", return_value=mock_store):
            result = await executor.execute("accept_suggestion", {"suggestion_id": "sug-1"})

        assert "error" in result
        assert "not found" in result["error"] or "not pending" in result["error"]

    @pytest.mark.asyncio
    async def test_no_automation_data(self, hass) -> None:
        """Returns error when suggestion has no automation_data."""
        suggestion = _make_suggestion()
        suggestion["automation_data"] = {}
        mock_store = MagicMock()
        mock_store.get_suggestions = AsyncMock(return_value=[suggestion])

        executor = _make_executor(hass, is_admin=True)
        with patch("custom_components.selora_ai._get_pattern_store", return_value=mock_store):
            result = await executor.execute("accept_suggestion", {"suggestion_id": "sug-1"})

        assert "error" in result

    @pytest.mark.asyncio
    async def test_store_unavailable(self, hass) -> None:
        """Returns error when PatternStore is not initialised."""
        executor = _make_executor(hass, is_admin=True)
        with patch("custom_components.selora_ai._get_pattern_store", return_value=None):
            result = await executor.execute("accept_suggestion", {"suggestion_id": "sug-1"})

        assert "error" in result

    @pytest.mark.asyncio
    async def test_automation_creation_failure(self, hass) -> None:
        """Returns error when automation creation fails."""
        suggestion = _make_suggestion()
        mock_store = MagicMock()
        mock_store.get_suggestions = AsyncMock(return_value=[suggestion])

        executor = _make_executor(hass, is_admin=True)
        with (
            patch("custom_components.selora_ai._get_pattern_store", return_value=mock_store),
            patch(
                "custom_components.selora_ai.automation_utils.async_create_automation",
                new_callable=AsyncMock,
                return_value={"success": False, "automation_id": None},
            ),
        ):
            result = await executor.execute("accept_suggestion", {"suggestion_id": "sug-1"})

        assert "error" in result
        mock_store.update_suggestion_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_admin_rejected(self, hass) -> None:
        """Non-admin users cannot call accept_suggestion."""
        executor = _make_executor(hass, is_admin=False)
        result = await executor.execute("accept_suggestion", {"suggestion_id": "sug-1"})

        assert "error" in result
        assert "admin" in result["error"].lower()


# ── ToolExecutor — dismiss_suggestion ───────────────────────────────


class TestDismissSuggestionHandler:
    """Tests for the executor dismiss_suggestion handler."""

    @pytest.mark.asyncio
    async def test_happy_path(self, hass) -> None:
        """Dismisses suggestion in PatternStore with reason."""
        mock_store = MagicMock()
        mock_store.update_suggestion_status = AsyncMock(return_value=True)

        executor = _make_executor(hass, is_admin=True)
        with patch("custom_components.selora_ai._get_pattern_store", return_value=mock_store):
            result = await executor.execute(
                "dismiss_suggestion", {"suggestion_id": "sug-2", "reason": "not useful"}
            )

        assert result["status"] == "dismissed"
        assert result["suggestion_id"] == "sug-2"
        assert result["reason"] == "not useful"
        mock_store.update_suggestion_status.assert_awaited_once()
        call_kwargs = mock_store.update_suggestion_status.call_args
        assert call_kwargs[0][0] == "sug-2"
        assert call_kwargs[1]["status"] == "dismissed"
        assert call_kwargs[1]["dismissal_reason"] == "not useful"
        assert "dismissed_at" in call_kwargs[1]

    @pytest.mark.asyncio
    async def test_missing_suggestion_id(self, hass) -> None:
        """Returns error when suggestion_id is empty."""
        executor = _make_executor(hass, is_admin=True)
        result = await executor.execute("dismiss_suggestion", {})
        assert "error" in result
        assert "suggestion_id" in result["error"]

    @pytest.mark.asyncio
    async def test_suggestion_not_found(self, hass) -> None:
        """Returns error when PatternStore can't find the suggestion."""
        mock_store = MagicMock()
        mock_store.update_suggestion_status = AsyncMock(return_value=False)

        executor = _make_executor(hass, is_admin=True)
        with patch("custom_components.selora_ai._get_pattern_store", return_value=mock_store):
            result = await executor.execute(
                "dismiss_suggestion", {"suggestion_id": "nonexistent"}
            )

        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_store_unavailable(self, hass) -> None:
        """Returns error when PatternStore is not initialised."""
        executor = _make_executor(hass, is_admin=True)
        with patch("custom_components.selora_ai._get_pattern_store", return_value=None):
            result = await executor.execute("dismiss_suggestion", {"suggestion_id": "sug-2"})

        assert "error" in result

    @pytest.mark.asyncio
    async def test_non_admin_rejected(self, hass) -> None:
        """Non-admin users cannot call dismiss_suggestion."""
        executor = _make_executor(hass, is_admin=False)
        result = await executor.execute("dismiss_suggestion", {"suggestion_id": "sug-2"})

        assert "error" in result
        assert "admin" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_without_reason(self, hass) -> None:
        """dismiss_suggestion defaults reason to 'user-declined'."""
        mock_store = MagicMock()
        mock_store.update_suggestion_status = AsyncMock(return_value=True)

        executor = _make_executor(hass, is_admin=True)
        with patch("custom_components.selora_ai._get_pattern_store", return_value=mock_store):
            result = await executor.execute("dismiss_suggestion", {"suggestion_id": "sug-3"})

        assert result["status"] == "dismissed"
        assert result["reason"] == "user-declined"
        call_kwargs = mock_store.update_suggestion_status.call_args
        assert call_kwargs[1]["dismissal_reason"] == "user-declined"


# ── Prompt wiring ───────────────────────────────────────────────────


class TestAcceptDismissPromptWiring:
    """accept_suggestion and dismiss_suggestion are referenced in the suggestions prompt."""

    def test_accept_in_prompt(self) -> None:
        text = _suggestions_prompt()
        assert "accept_suggestion" in text

    def test_dismiss_in_prompt(self) -> None:
        text = _suggestions_prompt()
        assert "dismiss_suggestion" in text

    def test_suggestion_id_retention_hint(self) -> None:
        text = _suggestions_prompt()
        assert "suggestion_id" in text
        assert "list_suggestions" in text

    def test_re_list_before_accept_or_dismiss(self) -> None:
        """Prompt instructs LLM to re-call list_suggestions before accept/dismiss."""
        text = _suggestions_prompt()
        assert "first call list_suggestions" in text
        assert "not available across turns" in text

    def test_json_prompt_includes_accept_dismiss(self, hass) -> None:
        prompt = _make_client(hass)._build_architect_system_prompt(tools_available=True)
        assert "accept_suggestion" in prompt
        assert "dismiss_suggestion" in prompt

    def test_stream_prompt_includes_accept_dismiss(self, hass) -> None:
        prompt = _make_client(hass)._build_architect_stream_system_prompt(tools_available=True)
        assert "accept_suggestion" in prompt
        assert "dismiss_suggestion" in prompt

    def test_prompts_exclude_without_tools(self, hass) -> None:
        """accept/dismiss not mentioned when tools are unavailable."""
        client = _make_client(hass)
        json_prompt = client._build_architect_system_prompt(tools_available=False)
        stream_prompt = client._build_architect_stream_system_prompt(tools_available=False)
        assert "accept_suggestion" not in json_prompt
        assert "accept_suggestion" not in stream_prompt
