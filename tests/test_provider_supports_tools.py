"""Per-provider tool-calling capability (`supports_tools`).

GitHub #3: on an install whose Ollama model has no tool block in its chat
template, every chat turn died with HTTP 400 ``"<model> does not support
tools"`` — the integration attached a tool schema to the request purely
because the provider was not ``is_low_context``, and Ollama rejects the
whole request rather than ignoring the schema.

The flag is fail-closed for Ollama (unknown ⇒ no tools, discovered from
``POST /api/show``) and defaults to True everywhere else, so the cloud
providers keep sending tools exactly as before. These tests pin the base
default, the probe's caching rules, and the gate in ``LLMClient``.

Ollama's answer is not stable: a model re-pulled under the same name swaps
its chat template without touching ``entry.data``, so nothing reloads the
entry. That makes staleness in BOTH directions a real failure mode, and
most of what follows is about which answers may be cached and for how
long.
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN202
import json
import time as _time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.selora_ai.const import (
    DEFAULT_OLLAMA_HOST,
    HEALTH_CHECK_TIMEOUT,
)
from custom_components.selora_ai.llm_client import LLMClient
from custom_components.selora_ai.providers import OllamaProvider, create_provider
from custom_components.selora_ai.providers.ollama import _CAPABILITIES_TTL_S

# An automation request: reaches the provider instead of one of the
# deterministic pre-provider short-circuits (safety / multi-target /
# unspecified-target), so these tests exercise the real request path.
_MESSAGE = "create an automation that turns on the porch light at sunset"
_ENTITIES: list[dict[str, Any]] = [
    {"entity_id": "light.porch", "state": "off", "attributes": {"friendly_name": "Porch Light"}}
]
_ANSWER = '{"intent": "answer", "response": "ok"}'
_OPENAI_BODY: dict[str, Any] = {"choices": [{"message": {"content": _ANSWER}}]}
_ANTHROPIC_BODY: dict[str, Any] = {"content": [{"type": "text", "text": _ANSWER}]}

_SHOW = "/api/show"
_CHAT = "/v1/chat/completions"
_CAPS_WITH_TOOLS: dict[str, Any] = {"capabilities": ["completion", "tools"]}
_CAPS_WITHOUT_TOOLS: dict[str, Any] = {"capabilities": ["completion", "vision"]}

_MISSING = object()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _response_cm(status: int, body: Any = _MISSING) -> MagicMock:
    """An ``async with``-able mock HTTP response."""
    payload = {} if body is _MISSING else body
    response = MagicMock()
    response.status = status
    response.json = AsyncMock(return_value=payload)
    response.text = AsyncMock(return_value=json.dumps(payload))

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _mock_post_session(status: int, body: Any = _MISSING) -> tuple[MagicMock, dict[str, Any]]:
    """Single-answer session; ``captured`` holds the call count and the last
    call's args so a test can assert URL / body / timeout."""
    captured: dict[str, Any] = {"calls": 0}
    cm = _response_cm(status, body)

    def _post(*args: Any, **kwargs: Any) -> MagicMock:
        captured["calls"] += 1
        captured["args"] = args
        captured["kwargs"] = kwargs
        return cm

    session = MagicMock()
    session.post = _post
    return session, captured


def _broken_session() -> MagicMock:
    """A session whose every request dies in transport."""
    session = MagicMock()
    session.post = MagicMock(side_effect=aiohttp.ClientError("boom"))
    return session


def _chat_session(probe_body: Any, *, probe_status: int = 200) -> tuple[MagicMock, dict[str, Any]]:
    """Session answering both endpoints a chat turn touches.

    The capability probe and the chat request share one session, so a turn
    that probes inline needs ``/api/show`` and the chat endpoint to answer
    differently.
    """
    captured: dict[str, Any] = {"urls": [], "bodies": {}}
    routes = {_SHOW: (probe_status, probe_body), _CHAT: (200, _OPENAI_BODY)}

    def _post(url: str, **kwargs: Any) -> MagicMock:
        captured["urls"].append(url)
        for fragment, (status, body) in routes.items():
            if fragment in url:
                captured["bodies"][fragment] = kwargs.get("data")
                return _response_cm(status, body)
        raise AssertionError(f"unrouted POST to {url}")

    session = MagicMock()
    session.post = _post
    return session, captured


class _Clock:
    """Hand-driven monotonic clock — TTL expiry is otherwise unobservable,
    and a real wait would put 300s of sleep in CI."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, start: float = 1000.0) -> None:
        self._now = start
        monkeypatch.setattr(_time, "monotonic", lambda: self._now)

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def expire_ttl(self) -> None:
        self.advance(_CAPABILITIES_TTL_S + 1)


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    return _Clock(monkeypatch)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serve(provider: Any, monkeypatch, session: MagicMock) -> None:
    """Point a provider's HTTP session at a fake."""
    monkeypatch.setattr(provider, "_get_session", lambda: session)


def _answering(
    hass, monkeypatch, body: Any, *, status: int = 200, model: str = "llama4"
) -> tuple[OllamaProvider, dict[str, Any]]:
    """An unprobed Ollama provider whose ``/api/show`` answers ``body``."""
    provider = OllamaProvider(hass, model=model)
    session, captured = _mock_post_session(status, body)
    _serve(provider, monkeypatch, session)
    return provider, captured


async def _probed(hass, monkeypatch, body: Any, *, model: str = "llama4") -> OllamaProvider:
    """…and with the probe already settled from that answer."""
    provider, _ = _answering(hass, monkeypatch, body, model=model)
    await provider.async_refresh_tool_capability()
    return provider


async def _chat(client: LLMClient) -> dict[str, Any]:
    return await client.architect_chat(_MESSAGE, entities=_ENTITIES, tool_executor=MagicMock())


async def _stream(client: LLMClient) -> list[str]:
    return [
        chunk
        async for chunk in client.architect_chat_stream(
            _MESSAGE, entities=_ENTITIES, tool_executor=MagicMock()
        )
    ]


def _route_streams(client: LLMClient, provider: Any, monkeypatch, *, expect: str) -> None:
    """Stub both streaming paths so the one under test yields the answer and
    the other fails loudly. ``expect`` is ``"tools"`` or ``"plain"``."""

    async def _taken(*_args: Any, **_kwargs: Any):
        yield _ANSWER

    async def _forbidden(*_args: Any, **_kwargs: Any):
        raise AssertionError(f"this turn must take the {expect} stream path")
        yield ""  # pragma: no cover — generator marker

    monkeypatch.setattr(
        client, "_stream_request_with_tools", _taken if expect == "tools" else _forbidden
    )
    monkeypatch.setattr(
        provider, "send_request_stream", _taken if expect == "plain" else _forbidden
    )


def _hooks_mocked(provider: Any) -> Any:
    """Stub both refresh hooks so a test can assert which one the gate uses:
    the tool-only hook is free, the broad one may cost a request."""
    provider.async_refresh_tool_capability = AsyncMock()
    provider.async_refresh_capabilities = AsyncMock()
    return provider


def _sent_tools(captured: dict[str, Any]) -> bool:
    """Did the outgoing chat body carry a tool schema?"""
    return "tools" in json.loads(captured["bodies"][_CHAT])


def _probes(captured: dict[str, Any]) -> int:
    return sum(1 for url in captured["urls"] if _SHOW in url)


# ---------------------------------------------------------------------------
# Base default — cloud providers are unchanged
# ---------------------------------------------------------------------------


class TestBaseDefault:
    @pytest.mark.parametrize("provider_type", ["anthropic", "gemini", "openai", "openrouter"])
    def test_cloud_providers_support_tools(self, hass, provider_type: str) -> None:
        """Every key-based cloud provider inherits the True default — this
        fix must not narrow tool calling anywhere it already worked."""
        assert create_provider(provider_type, hass, api_key="k").supports_tools is True

    def test_selora_cloud_supports_tools(self, hass) -> None:
        assert create_provider("selora_cloud", hass).supports_tools is True

    def test_selora_local_supports_tools(self, hass) -> None:
        """Selora AI Local keeps the base True — its tools are withheld by
        the separate ``is_low_context`` branch, which this change leaves
        untouched."""
        provider = create_provider("selora_local", hass)
        assert provider.supports_tools is True
        assert provider.is_low_context is True

    def test_ollama_is_false_before_probing(self, hass) -> None:
        """Unknown means no tools: a schema sent to a model that can't take
        one loses the whole turn to an HTTP 400, while withholding it only
        loses tool calling."""
        assert OllamaProvider(hass, model="mistral").supports_tools is False

    @pytest.mark.parametrize(
        "provider_type", ["anthropic", "gemini", "openai", "openrouter", "selora_cloud"]
    )
    async def test_tool_refresh_touches_no_network(
        self, hass, monkeypatch, provider_type: str
    ) -> None:
        """The tool-only hook runs on every tool-bearing turn, so for a
        provider with nothing to discover it must be a true no-op — not the
        vision catalog fetch (or Selora Cloud token refresh) that the broad
        hook performs."""
        provider = create_provider(provider_type, hass, api_key="k")
        forbidden = MagicMock()
        forbidden.get = MagicMock(side_effect=AssertionError("no request may leave the gate"))
        forbidden.post = MagicMock(side_effect=AssertionError("no request may leave the gate"))
        _serve(provider, monkeypatch, forbidden)

        await provider.async_refresh_tool_capability()

        assert provider.supports_tools is True


# ---------------------------------------------------------------------------
# The probe itself — POST /api/show
# ---------------------------------------------------------------------------


class TestOllamaCapabilityProbe:
    async def test_reads_tools_from_capabilities(self, hass, monkeypatch) -> None:
        provider, captured = _answering(hass, monkeypatch, _CAPS_WITH_TOOLS)

        await provider.async_refresh_tool_capability()

        assert provider.supports_tools is True
        assert captured["args"][0] == f"{DEFAULT_OLLAMA_HOST}{_SHOW}"
        assert json.loads(captured["kwargs"]["data"]) == {"model": "llama4"}
        assert isinstance(captured["kwargs"]["timeout"], aiohttp.ClientTimeout)
        assert captured["kwargs"]["timeout"].total == HEALTH_CHECK_TIMEOUT

    async def test_absent_tools_capability_means_no_tools(self, hass, monkeypatch) -> None:
        """Issue #3's model: a template with completion/vision, no tools."""
        provider = await _probed(hass, monkeypatch, _CAPS_WITHOUT_TOOLS, model="mistral")

        assert provider.supports_tools is False

    async def test_broad_refresh_still_probes_tools(self, hass, monkeypatch) -> None:
        """``get_config`` calls the broad hook, so Ollama must reach the tool
        probe through it too — the split is about what the *chat gate* skips,
        not about dropping a discovery path."""
        provider, captured = _answering(hass, monkeypatch, _CAPS_WITH_TOOLS)

        await provider.async_refresh_capabilities()

        assert captured["calls"] == 1
        assert provider.supports_tools is True

    async def test_settled_answer_is_reused_within_ttl(self, hass, monkeypatch) -> None:
        """The chat gate refreshes on every tool-bearing turn and get_config
        on every panel load; a settled answer must not re-hit the server."""
        provider, captured = _answering(hass, monkeypatch, _CAPS_WITH_TOOLS)

        await provider.async_refresh_tool_capability()
        await provider.async_refresh_tool_capability()

        assert captured["calls"] == 1
        assert provider.supports_tools is True

    async def test_first_probe_runs_on_freshly_booted_host(self, hass, monkeypatch) -> None:
        """time.monotonic() counts from boot, so a 0.0 never-probed sentinel
        would read as "just fetched" on a host with seconds of uptime and
        suppress the first probe entirely."""
        _Clock(monkeypatch, start=12.0)
        provider, captured = _answering(hass, monkeypatch, _CAPS_WITH_TOOLS)

        await provider.async_refresh_tool_capability()

        assert captured["calls"] == 1
        assert provider.supports_tools is True


class TestProbeFailureHandling:
    """Which failures are an answer and which are "ask again". Getting this
    wrong is what pins a tool-capable model as tool-less until the entry
    reloads — or keeps a tool-less one collecting HTTP 400s.
    """

    # Nothing here says anything about the model's template: the server is
    # reachable but unwilling (loading, rate-limited) or unreachable
    # outright. 408/425/429 in particular come from reverse proxies in
    # front of Ollama, not from Ollama's own capability logic.
    _INCONCLUSIVE = [
        pytest.param(_broken_session, id="transport-error"),
        *[
            pytest.param(
                lambda status=status: _mock_post_session(status, {"error": "later"})[0],
                id=f"http-{status}",
            )
            for status in (408, 425, 429, 500, 502, 503)
        ],
    ]

    @pytest.mark.parametrize("failing_session", _INCONCLUSIVE)
    async def test_inconclusive_probe_stays_retryable(
        self, hass, monkeypatch, failing_session
    ) -> None:
        """An inconclusive probe must neither cache nor stamp: the flag
        stays fail-closed for this turn, and the very next call re-probes so
        a recovered server self-heals without an entry reload."""
        provider = OllamaProvider(hass, model="llama4")
        _serve(provider, monkeypatch, failing_session())

        await provider.async_refresh_tool_capability()
        assert provider.supports_tools is False

        session, captured = _mock_post_session(200, _CAPS_WITH_TOOLS)
        _serve(provider, monkeypatch, session)
        await provider.async_refresh_tool_capability()

        assert provider.supports_tools is True
        assert captured["calls"] == 1

    @pytest.mark.parametrize("status", [400, 404])
    async def test_definitive_client_error_is_cached(self, hass, monkeypatch, status: int) -> None:
        """The mirror image: an Ollama predating /api/show 404s, an unpulled
        model 404s, and a server too old for the ``model`` field 400s. Those
        are answers, so they cache like one and stop the retries."""
        provider, _ = _answering(hass, monkeypatch, {"error": "not found"}, status=status)

        await provider.async_refresh_tool_capability()
        assert provider.supports_tools is False

        session, captured = _mock_post_session(200, _CAPS_WITH_TOOLS)
        _serve(provider, monkeypatch, session)
        await provider.async_refresh_tool_capability()

        assert captured["calls"] == 0

    # A 200 whose body isn't the documented ``{"capabilities": [...]}``.
    # Two distinct hazards: a non-iterable raises from ``in`` (and this
    # probe runs inline on the chat path, so it would abort a chat turn),
    # and ``in`` against a *string* is a substring test — "vision,tools"
    # would report tool support that isn't there and put the HTTP 400 back.
    @pytest.mark.parametrize(
        "body",
        [
            pytest.param([], id="array-body"),
            pytest.param("capabilities", id="string-body"),
            pytest.param(7, id="number-body"),
            pytest.param(None, id="null-body"),
            pytest.param({}, id="key-absent"),
            pytest.param({"capabilities": None}, id="null-capabilities"),
            pytest.param({"capabilities": 1}, id="number-capabilities"),
            pytest.param({"capabilities": True}, id="bool-capabilities"),
            pytest.param({"capabilities": {"tools": True}}, id="object-capabilities"),
            pytest.param({"capabilities": "tools"}, id="string-capabilities-exact"),
            pytest.param({"capabilities": "vision,tools"}, id="string-capabilities-substring"),
        ],
    )
    async def test_malformed_body_never_reports_tools(self, hass, monkeypatch, body) -> None:
        provider = await _probed(hass, monkeypatch, body, model="mistral")

        assert provider.supports_tools is False


# ---------------------------------------------------------------------------
# Staleness — an Ollama model is mutable under a fixed name
# ---------------------------------------------------------------------------


class TestCapabilityStaleness:
    """`ollama pull <name>` or recreating a custom model swaps the chat
    template while entry.data — and so the provider instance — stays put.
    Nothing reloads the entry, so the cache has to expire on its own or the
    process keeps answering from a template that is gone.
    """

    async def test_repulled_model_gains_tools_after_ttl(self, hass, monkeypatch, clock) -> None:
        provider = await _probed(hass, monkeypatch, _CAPS_WITHOUT_TOOLS)
        assert provider.supports_tools is False

        # Same name, new template — only the clock tells us to look again.
        session, captured = _mock_post_session(200, _CAPS_WITH_TOOLS)
        _serve(provider, monkeypatch, session)
        clock.advance(_CAPABILITIES_TTL_S - 1)
        await provider.async_refresh_tool_capability()
        assert provider.supports_tools is False
        assert captured["calls"] == 0

        clock.advance(2)
        await provider.async_refresh_tool_capability()
        assert provider.supports_tools is True
        assert captured["calls"] == 1

    async def test_repulled_model_loses_tools_after_ttl(self, hass, monkeypatch, clock) -> None:
        """The dangerous direction: a cached True outlives the template that
        justified it, and then every turn dies on HTTP 400."""
        provider = await _probed(hass, monkeypatch, _CAPS_WITH_TOOLS)
        assert provider.supports_tools is True

        session, _ = _mock_post_session(200, _CAPS_WITHOUT_TOOLS)
        _serve(provider, monkeypatch, session)
        clock.expire_ttl()
        await provider.async_refresh_tool_capability()

        assert provider.supports_tools is False

    async def test_failed_revalidation_keeps_last_known_answer(
        self, hass, monkeypatch, clock
    ) -> None:
        """Expiry means "ask again", not "assume the worst" — a blip during
        revalidation must not flap a working model to tool-less, and must
        leave the probe retryable rather than waiting out another TTL."""
        provider = await _probed(hass, monkeypatch, _CAPS_WITH_TOOLS)

        _serve(provider, monkeypatch, _broken_session())
        clock.expire_ttl()
        await provider.async_refresh_tool_capability()
        assert provider.supports_tools is True

        session, captured = _mock_post_session(200, _CAPS_WITHOUT_TOOLS)
        _serve(provider, monkeypatch, session)
        await provider.async_refresh_tool_capability()
        assert captured["calls"] == 1
        assert provider.supports_tools is False


# ---------------------------------------------------------------------------
# LLMClient gate — the tool schema never reaches an incapable model
# ---------------------------------------------------------------------------

_GATE_CASES = [
    pytest.param(_CAPS_WITH_TOOLS, True, id="capable"),
    pytest.param(_CAPS_WITHOUT_TOOLS, False, id="tool-less"),
]


class TestChatToolGate:
    @pytest.mark.parametrize(("probe_body", "tools_expected"), _GATE_CASES)
    async def test_unprobed_model_is_asked_then_gated(
        self, hass, monkeypatch, probe_body, tools_expected: bool
    ) -> None:
        """Ollama discovers support lazily, so the gate must ask rather than
        assume — a turn landing before any panel load would otherwise lose
        tool calling — and must then honour whichever answer came back."""
        provider = OllamaProvider(hass, model="llama4")
        session, captured = _chat_session(probe_body)
        _serve(provider, monkeypatch, session)

        result = await _chat(LLMClient(hass, provider))

        assert _probes(captured) == 1
        assert _sent_tools(captured) is tools_expected
        assert result["intent"] == "answer"

    async def test_stale_capable_model_stops_getting_tools_after_ttl(
        self, hass, monkeypatch, clock
    ) -> None:
        """A model detected as tool-capable, then re-pulled under the same
        name without a tool block. Nothing reloads the entry, so the gate
        itself has to notice — otherwise every turn attaches tools and
        collects Ollama's HTTP 400."""
        provider = OllamaProvider(hass, model="llama4")
        capable, first = _chat_session(_CAPS_WITH_TOOLS)
        _serve(provider, monkeypatch, capable)
        client = LLMClient(hass, provider)

        await _chat(client)
        assert _sent_tools(first) is True

        tool_less, second = _chat_session(_CAPS_WITHOUT_TOOLS)
        _serve(provider, monkeypatch, tool_less)
        clock.expire_ttl()

        await _chat(client)

        assert _sent_tools(second) is False

    async def test_turn_within_ttl_does_not_re_probe(self, hass, monkeypatch, clock) -> None:
        """Revalidating per turn must not mean a request per turn — the
        provider's TTL absorbs the second turn."""
        provider = OllamaProvider(hass, model="llama4")
        session, captured = _chat_session(_CAPS_WITH_TOOLS)
        _serve(provider, monkeypatch, session)
        client = LLMClient(hass, provider)

        await _chat(client)
        clock.advance(_CAPABILITIES_TTL_S / 2)
        await _chat(client)

        assert _probes(captured) == 1

    async def test_capable_provider_is_still_revalidated(self, hass) -> None:
        """The gate asks on every tool-bearing turn, including one whose flag
        already reads True — a cached True goes stale too, and only a
        refresh can catch it."""
        provider = _hooks_mocked(create_provider("anthropic", hass, api_key="k"))
        provider.raw_request = AsyncMock(return_value=_ANTHROPIC_BODY)

        await _chat(LLMClient(hass, provider))

        provider.async_refresh_tool_capability.assert_awaited_once()
        assert provider.raw_request.await_args.kwargs["tools"]

    async def test_gate_does_not_trigger_vision_discovery(self, hass) -> None:
        """Only the tool-only hook. The broad one also discovers vision,
        which costs OpenRouter a catalog fetch and Selora Cloud a token
        refresh — up to their 10s timeout in front of a text turn that
        never needed either."""
        provider = _hooks_mocked(create_provider("openrouter", hass, api_key="k"))
        provider.raw_request = AsyncMock(return_value=_OPENAI_BODY)

        await _chat(LLMClient(hass, provider))

        provider.async_refresh_capabilities.assert_not_awaited()

    async def test_no_refresh_without_a_tool_executor(self, hass) -> None:
        """Nothing to gate on a turn that was never going to send tools —
        the Assist and MCP paths pass no executor and must not pay for a
        capability check of either kind."""
        provider = _hooks_mocked(create_provider("anthropic", hass, api_key="k"))
        provider.send_request = AsyncMock(return_value=(_ANSWER, None))
        client = LLMClient(hass, provider)

        await client.architect_chat(_MESSAGE, entities=_ENTITIES)

        provider.async_refresh_tool_capability.assert_not_awaited()
        provider.async_refresh_capabilities.assert_not_awaited()

    async def test_cloud_provider_still_gets_tools(self, hass) -> None:
        """End to end with the real (no-op) refresh in the path: adding the
        gate must not cost the cloud providers their tool calling."""
        provider = create_provider("anthropic", hass, api_key="k")
        provider.raw_request = AsyncMock(return_value=_ANTHROPIC_BODY)

        await _chat(LLMClient(hass, provider))

        assert provider.raw_request.await_args.kwargs["tools"]


class TestStreamToolGate:
    """The streaming entry point carries its own copy of the gate."""

    @pytest.mark.parametrize(("probe_body", "tools_expected"), _GATE_CASES)
    async def test_unprobed_model_is_asked_then_gated(
        self, hass, monkeypatch, probe_body, tools_expected: bool
    ) -> None:
        provider, _ = _answering(hass, monkeypatch, probe_body)
        client = LLMClient(hass, provider)
        _route_streams(client, provider, monkeypatch, expect="tools" if tools_expected else "plain")

        assert await _stream(client) == [_ANSWER]

    async def test_stale_capable_model_loses_the_tool_stream(
        self, hass, monkeypatch, clock
    ) -> None:
        provider = await _probed(hass, monkeypatch, _CAPS_WITH_TOOLS)
        assert provider.supports_tools is True

        session, _ = _mock_post_session(200, _CAPS_WITHOUT_TOOLS)
        _serve(provider, monkeypatch, session)
        clock.expire_ttl()

        client = LLMClient(hass, provider)
        _route_streams(client, provider, monkeypatch, expect="plain")

        assert await _stream(client) == [_ANSWER]
