"""The background suggestions analysis (``LLMClient.analyze_home_data``).

The analysis must go over the streaming transport: Selora Cloud's proxy fails
any response whose headers take more than ~5s, which a non-streaming analysis
never meets. And a failed request must raise rather than read as "no
suggestions", or the collector reports an empty analysis forever.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai.llm_client import LLMClient
from custom_components.selora_ai.providers import create_provider

_SUGGESTION = {
    "alias": "Sunset Alert",
    "description": "Notify at sunset",
    "triggers": [{"platform": "sun", "event": "sunset"}],
    "actions": [{"action": "notify.persistent_notification", "data": {"message": "Sunset"}}],
}

_SNAPSHOT: Any = {
    "devices": [],
    "entity_states": [{"entity_id": "sun.sun", "state": "above_horizon"}],
    "automations": [],
    "recorder_history": [],
}


def _make_client(hass: HomeAssistant) -> LLMClient:
    client = LLMClient(hass, create_provider("anthropic", hass, api_key="test-key"))
    client._provider.send_request = AsyncMock(  # type: ignore[method-assign]
        side_effect=AssertionError("the analysis must stream, not send_request")
    )
    return client


async def test_analysis_streams_and_parses(hass: HomeAssistant) -> None:
    client = _make_client(hass)
    text = json.dumps([_SUGGESTION])
    seen: dict[str, Any] = {}

    async def _stream(system: str, messages: list[dict[str, str]], **kw: Any) -> AsyncIterator[str]:
        seen["max_tokens"] = kw.get("max_tokens")
        yield text[:20]
        yield text[20:]

    client._provider.send_request_stream = _stream  # type: ignore[method-assign]

    suggestions = await client.analyze_home_data(_SNAPSHOT)

    assert [s["alias"] for s in suggestions] == ["Sunset Alert"]
    assert seen["max_tokens"] > 1024


async def test_failed_request_raises_instead_of_returning_nothing(hass: HomeAssistant) -> None:
    client = _make_client(hass)

    async def _stream(*_a: Any, **_kw: Any) -> AsyncIterator[str]:
        raise ConnectionError("HTTP 500: proxy handler: unable to reach app")
        yield ""  # pragma: no cover — makes this an async generator

    client._provider.send_request_stream = _stream  # type: ignore[method-assign]

    with pytest.raises(ConnectionError, match="unable to reach app"):
        await client.analyze_home_data(_SNAPSHOT)


async def test_empty_stream_raises(hass: HomeAssistant) -> None:
    """An empty reply recorded as an analysis would defer the retry for hours."""
    client = _make_client(hass)

    async def _stream(*_a: Any, **_kw: Any) -> AsyncIterator[str]:
        return
        yield ""  # pragma: no cover — makes this an async generator

    client._provider.send_request_stream = _stream  # type: ignore[method-assign]

    with pytest.raises(ConnectionError, match="empty analysis"):
        await client.analyze_home_data(_SNAPSHOT)


async def test_cut_stream_raises(hass: HomeAssistant) -> None:
    """A stream closed before the backend ended it is half a JSON array."""
    client = _make_client(hass)

    async def _stream(*_a: Any, **_kw: Any) -> AsyncIterator[str]:
        yield '[{"alias": "Sunset'
        client._provider._last_stream_terminated = False

    client._provider.send_request_stream = _stream  # type: ignore[method-assign]

    with pytest.raises(ConnectionError, match="cut off after 18 characters"):
        await client.analyze_home_data(_SNAPSHOT)


async def test_output_cap_with_nothing_recovered_raises(hass: HomeAssistant) -> None:
    client = _make_client(hass)

    async def _stream(*_a: Any, **_kw: Any) -> AsyncIterator[str]:
        yield '[{"alias": "Sunset'
        client._provider._last_finish_reason = "max_tokens"

    client._provider.send_request_stream = _stream  # type: ignore[method-assign]

    with pytest.raises(ConnectionError, match="output cap"):
        await client.analyze_home_data(_SNAPSHOT)


async def test_output_cap_keeps_what_was_recovered(hass: HomeAssistant) -> None:
    """A retry hits the same cap, so complete suggestions are kept."""
    client = _make_client(hass)

    async def _stream(*_a: Any, **_kw: Any) -> AsyncIterator[str]:
        yield json.dumps([_SUGGESTION])
        client._provider._last_finish_reason = "max_tokens"

    client._provider.send_request_stream = _stream  # type: ignore[method-assign]

    assert [s["alias"] for s in await client.analyze_home_data(_SNAPSHOT)] == ["Sunset Alert"]
