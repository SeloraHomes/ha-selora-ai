"""Tests for context-window discovery on the local providers.

Covers the ``LLMProvider.context_window`` capability (None = unknown),
the Selora AI Local probe of llama-server's ``GET /v1/models``, the
Ollama probe of ``POST /api/show``, and the invariant that discovery
changes nothing about the request body.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai.providers.anthropic import AnthropicProvider
from custom_components.selora_ai.providers.base import LLMProvider, _positive_int
from custom_components.selora_ai.providers.gemini import GeminiProvider
from custom_components.selora_ai.providers.ollama import OllamaProvider
from custom_components.selora_ai.providers.openai import OpenAIProvider
from custom_components.selora_ai.providers.openrouter import OpenRouterProvider
from custom_components.selora_ai.providers.selora_cloud import SeloraCloudProvider
from custom_components.selora_ai.providers.selora_local import SeloraLocalProvider

# GET /v1/models as llama-server answers it (trimmed to the fields the
# provider reads); see the README's Selora AI Local section.
LLAMA_SERVER_MODELS = {
    "object": "list",
    "data": [
        {
            "id": "selorahomes/Selora-AI",
            "object": "model",
            "owned_by": "llamacpp",
            "meta": {"n_vocab": 151936, "n_ctx": 8192, "n_ctx_train": 40960},
        }
    ],
}

# Distinguishes "no body given" from a falsy one.
_UNSET = object()


class _FakeResponse:
    """Minimal aiohttp response stand-in for probe tests."""

    # ``data or {}`` would collapse a falsy body — an empty array is one —
    # into an object, which is exactly the shape the non-object cases below
    # exist to send. Only an omitted body defaults.
    def __init__(self, status: int, data: Any = _UNSET) -> None:
        self.status = status
        self._data: Any = {} if data is _UNSET else data

    async def json(self) -> Any:
        return self._data

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeSession:
    """Records probe calls and replays a canned response."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.get_calls: list[str] = []
        self.post_calls: list[tuple[str, Any]] = []

    def get(self, url: str, **_kwargs: object) -> _FakeResponse:
        self.get_calls.append(url)
        return self._response

    def post(self, url: str, **kwargs: object) -> _FakeResponse:
        self.post_calls.append((url, kwargs.get("data")))
        return self._response


class _BoomSession:
    """Session whose probes fail the way a stopped server does."""

    def get(self, url: str, **_kwargs: object) -> _FakeResponse:
        raise aiohttp.ClientConnectionError("connection refused")

    def post(self, url: str, **_kwargs: object) -> _FakeResponse:
        raise aiohttp.ClientConnectionError("connection refused")


def _local(
    hass: HomeAssistant, response: _FakeResponse
) -> tuple[SeloraLocalProvider, _FakeSession]:
    provider = SeloraLocalProvider(hass, host="http://hub:8080")
    session = _FakeSession(response)
    provider._get_session = lambda: session  # type: ignore[method-assign]
    return provider, session


def _ollama(hass: HomeAssistant, response: _FakeResponse) -> tuple[OllamaProvider, _FakeSession]:
    provider = OllamaProvider(hass, model="qwen3:8b", host="http://ollama:11434")
    session = _FakeSession(response)
    provider._get_session = lambda: session  # type: ignore[method-assign]
    return provider, session


# ── Base capability contract ─────────────────────────────────────────


class TestContextWindowDefault:
    """Unknown is the default, and unknown is None — never a number."""

    @pytest.mark.parametrize(
        "factory",
        [
            lambda hass: AnthropicProvider(hass, api_key="k"),
            lambda hass: OpenAIProvider(hass, api_key="k"),
            lambda hass: GeminiProvider(hass, api_key="k"),
            lambda hass: OpenRouterProvider(hass, api_key="k"),
            lambda hass: SeloraCloudProvider(hass, access_token="t"),
            lambda hass: OllamaProvider(hass, model="llama4"),
            lambda hass: SeloraLocalProvider(hass),
        ],
    )
    def test_unknown_before_any_probe(
        self, hass: HomeAssistant, factory: Callable[[HomeAssistant], LLMProvider]
    ) -> None:
        assert factory(hass).context_window is None

    async def test_static_provider_refresh_leaves_it_unknown(self, hass: HomeAssistant) -> None:
        provider = AnthropicProvider(hass, api_key="k")
        await provider.async_refresh_capabilities()
        assert provider.context_window is None


class TestPositiveInt:
    """Server metadata is raw JSON — coercion must reject nonsense."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (8192, 8192),
            (8192.0, 8192),
            ("8192", 8192),
            ("  8192  ", 8192),
            (0, None),
            (-1, None),
            (4096.5, None),
            (True, None),
            (False, None),
            (None, None),
            ("", None),
            ("many", None),
            ([8192], None),
        ],
    )
    def test_coercion(self, value: object, expected: int | None) -> None:
        assert _positive_int(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            float("nan"),
            float("inf"),
            float("-inf"),
            # A numeric literal too large for a float parses to inf, so a
            # server emitting one lands in the same branch.
            json.loads("1e400"),
            # json.loads accepts these non-standard literals by default,
            # so they are reachable from a real response body.
            json.loads("NaN"),
            json.loads("Infinity"),
        ],
    )
    def test_non_finite_is_rejected_without_raising(self, value: float) -> None:
        # int(nan) raises ValueError and int(inf) OverflowError; neither may
        # escape a probe that promises malformed metadata is non-fatal.
        assert _positive_int(value) is None


# ── Selora AI Local (llama-server GET /v1/models) ────────────────────


class TestSeloraLocalContextWindow:
    async def test_reads_served_n_ctx(self, hass: HomeAssistant) -> None:
        provider, session = _local(hass, _FakeResponse(200, LLAMA_SERVER_MODELS))
        await provider.async_refresh_capabilities()
        # n_ctx (served), not n_ctx_train (the model's trained maximum).
        assert provider.context_window == 8192
        assert session.get_calls == ["http://hub:8080/v1/models"]

    async def test_records_base_model_id_too(self, hass: HomeAssistant) -> None:
        provider, _ = _local(hass, _FakeResponse(200, LLAMA_SERVER_MODELS))
        await provider.async_refresh_capabilities()
        assert provider._base_model_id == "selorahomes/Selora-AI"

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"data": []},
            {"data": [{"id": "m"}]},
            {"data": [{"id": "m", "meta": {}}]},
            {"data": [{"id": "m", "meta": {"n_ctx": 0}}]},
            {"data": [{"id": "m", "meta": {"n_ctx": "unlimited"}}]},
            {"data": [{"id": "m", "meta": "not-a-dict"}]},
            {"data": ["not-a-dict"]},
            {"data": "not-a-list"},
            {"data": 7},
            {"data": [{"id": "m", "meta": {"n_ctx": float("inf")}}]},
        ],
    )
    async def test_missing_or_malformed_meta_stays_unknown(
        self, hass: HomeAssistant, body: dict[str, Any]
    ) -> None:
        provider, _ = _local(hass, _FakeResponse(200, body))
        await provider.async_refresh_capabilities()
        assert provider.context_window is None

    # ``host`` is user-configured, so a proxy or an unrelated server can
    # answer 200 with valid JSON that isn't an object. ``.get`` on one of
    # those raises AttributeError, which neither the probe's except clause
    # nor the LoRA discovery pass catches — so it would escape a hook
    # documented as non-raising, called from the get_config websocket
    # handler and from the completion path.
    _NON_OBJECT_BODIES = [
        pytest.param([], id="empty-array"),
        pytest.param([{"id": "m"}], id="bare-array"),
        pytest.param("not-json-object", id="string"),
        pytest.param(7, id="number"),
    ]

    @pytest.mark.parametrize("body", _NON_OBJECT_BODIES)
    async def test_non_object_body_is_ignored_not_fatal(
        self, hass: HomeAssistant, body: object
    ) -> None:
        provider, _ = _local(hass, _FakeResponse(200, body))
        await provider.async_refresh_capabilities()
        assert provider.context_window is None

    @pytest.mark.parametrize("body", _NON_OBJECT_BODIES)
    async def test_non_object_body_does_not_break_lora_discovery(
        self, hass: HomeAssistant, body: object
    ) -> None:
        provider = SeloraLocalProvider(hass, host="http://hub:8080")
        models = _FakeResponse(200, body)
        adapters = _FakeResponse(200, [])

        class _RoutingSession:
            def get(self, url: str, **_kwargs: object) -> _FakeResponse:
                return models if url.endswith("/v1/models") else adapters

        provider._get_session = lambda: _RoutingSession()  # type: ignore[method-assign]
        await provider._ensure_lora_discovery()
        assert provider.context_window is None
        # Discovery itself still settles, so LoRA routing is not lost to a
        # bad /v1/models body.
        assert provider._lora_slots is not None

    async def test_non_object_body_keeps_a_previous_good_reading(self, hass: HomeAssistant) -> None:
        provider, _ = _local(hass, _FakeResponse(200, []))
        provider._context_window = 8192
        await provider.async_refresh_capabilities()
        assert provider.context_window == 8192

    async def test_non_200_stays_unknown(self, hass: HomeAssistant) -> None:
        provider, _ = _local(hass, _FakeResponse(404))
        await provider.async_refresh_capabilities()
        assert provider.context_window is None

    async def test_transport_failure_is_non_fatal(self, hass: HomeAssistant) -> None:
        provider = SeloraLocalProvider(hass, host="http://hub:8080")
        provider._get_session = lambda: _BoomSession()  # type: ignore[method-assign]
        await provider.async_refresh_capabilities()
        assert provider.context_window is None

    async def test_failure_keeps_a_previous_good_reading(self, hass: HomeAssistant) -> None:
        provider = SeloraLocalProvider(hass, host="http://hub:8080")
        provider._context_window = 8192
        provider._get_session = lambda: _BoomSession()  # type: ignore[method-assign]
        await provider.async_refresh_capabilities()
        assert provider.context_window == 8192

    async def test_probe_is_ttl_cached(self, hass: HomeAssistant) -> None:
        provider, session = _local(hass, _FakeResponse(200, LLAMA_SERVER_MODELS))
        await provider.async_refresh_capabilities()
        await provider.async_refresh_capabilities()
        assert len(session.get_calls) == 1

    async def test_first_probe_runs_on_freshly_booted_host(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # time.monotonic() counts from boot; a 0.0 "never fetched"
        # sentinel would read as "fetched recently" on a low-uptime host
        # and suppress the first probe entirely.
        import time as _time

        monkeypatch.setattr(_time, "monotonic", lambda: 42.0)
        provider, session = _local(hass, _FakeResponse(200, LLAMA_SERVER_MODELS))
        await provider.async_refresh_capabilities()
        assert len(session.get_calls) == 1
        assert provider.context_window == 8192

    async def test_lora_discovery_pass_also_fills_the_window(self, hass: HomeAssistant) -> None:
        # LoRA discovery already GETs /v1/models, so the window must be
        # known from first use without waiting for a capability refresh.
        provider = SeloraLocalProvider(hass, host="http://hub:8080")
        models = _FakeResponse(200, LLAMA_SERVER_MODELS)
        adapters = _FakeResponse(200, [])

        class _RoutingSession:
            def get(self, url: str, **_kwargs: object) -> _FakeResponse:
                return models if url.endswith("/v1/models") else adapters

        provider._get_session = lambda: _RoutingSession()  # type: ignore[method-assign]
        await provider._ensure_lora_discovery()
        assert provider._base_model_id == "selorahomes/Selora-AI"
        assert provider.context_window == 8192

    def test_is_low_context_flag_is_untouched(self, hass: HomeAssistant) -> None:
        # The static prompt-shrinking policy flag is a separate concern
        # from the measured window; discovery must not move it.
        provider = SeloraLocalProvider(hass)
        provider._context_window = 40960
        assert provider.is_low_context is True


# ── Ollama (POST /api/show) ──────────────────────────────────────────


class TestOllamaContextWindow:
    async def test_posts_the_configured_model(self, hass: HomeAssistant) -> None:
        provider, session = _ollama(
            hass, _FakeResponse(200, {"model_info": {"qwen3.context_length": 40960}})
        )
        await provider.async_refresh_capabilities()
        url, body = session.post_calls[0]
        assert url == "http://ollama:11434/api/show"
        assert json.loads(body) == {"model": "qwen3:8b"}

    async def test_modelfile_num_ctx_wins(self, hass: HomeAssistant) -> None:
        provider, _ = _ollama(
            hass,
            _FakeResponse(
                200,
                {
                    "model_info": {"qwen3.context_length": 40960},
                    "parameters": "stop                           <|im_end|>\nnum_ctx    32768",
                },
            ),
        )
        await provider.async_refresh_capabilities()
        assert provider.context_window == 32768

    async def test_configured_num_ctx_above_trained_length_is_reported(
        self, hass: HomeAssistant
    ) -> None:
        # llama.cpp allocates the window it is asked for and extrapolates
        # past the trained length rather than refusing, so the configured
        # number is what is really being served. Clamping to the GGUF's
        # trained length would under-report it and make prompt sizing
        # truncate room that exists.
        provider, _ = _ollama(
            hass,
            _FakeResponse(
                200,
                {
                    "model_info": {"qwen3.context_length": 8192},
                    "parameters": "num_ctx 131072",
                },
            ),
        )
        await provider.async_refresh_capabilities()
        assert provider.context_window == 131072

    @pytest.mark.parametrize(
        "body",
        [
            {"model_info": {"qwen3.context_length": 40960}},
            {"model_info": {"gpt2.context_length": 1024}},
            {"model_info": {"qwen3.context_length": 40960}, "parameters": "temperature 0.1"},
        ],
    )
    async def test_no_modelfile_num_ctx_stays_unknown(
        self, hass: HomeAssistant, body: dict[str, Any]
    ) -> None:
        # Without a Modelfile num_ctx the daemon's own default applies, and
        # /api/show never reports it — it is set with OLLAMA_CONTEXT_LENGTH
        # and has changed between releases. A compiled-in guess could claim
        # a larger window than a server configured smaller, so unknown has
        # to stay unknown. The trained length is not a reading either.
        provider, _ = _ollama(hass, _FakeResponse(200, body))
        await provider.async_refresh_capabilities()
        assert provider.context_window is None

    async def test_removed_num_ctx_resets_to_unknown(self, hass: HomeAssistant) -> None:
        # A model recreated under the same name without `PARAMETER num_ctx`
        # falls back to the daemon default, and entry.data doesn't change,
        # so nothing else invalidates the old number. Keeping 32768 for a
        # 4096-token window would have callers size prompts past the real
        # window on every refresh from then on.
        provider, _ = _ollama(hass, _FakeResponse(200, {"parameters": "temperature 0.1"}))
        provider._context_window = 32768
        await provider.async_refresh_capabilities()
        assert provider.context_window is None

    async def test_unreadable_num_ctx_also_resets_to_unknown(self, hass: HomeAssistant) -> None:
        # "No num_ctx" and "a num_ctx we could not read" are the same
        # amount of evidence — none — and unknown is the safe state, so a
        # parsed body clears in both cases rather than keeping a number
        # nothing has re-confirmed.
        provider, _ = _ollama(hass, _FakeResponse(200, {"parameters": 17}))
        provider._context_window = 32768
        await provider.async_refresh_capabilities()
        assert provider.context_window is None

    @pytest.mark.parametrize("status", [404, 500, 503])
    async def test_non_200_never_resets(self, hass: HomeAssistant, status: int) -> None:
        # Clearing is reachable only from a body that parsed. A stopped or
        # unhappy server says nothing about the model's Modelfile.
        provider, _ = _ollama(hass, _FakeResponse(status))
        provider._context_window = 32768
        await provider.async_refresh_capabilities()
        assert provider.context_window == 32768

    async def test_num_ctx_without_model_info(self, hass: HomeAssistant) -> None:
        provider, _ = _ollama(hass, _FakeResponse(200, {"parameters": "num_ctx 16384"}))
        await provider.async_refresh_capabilities()
        assert provider.context_window == 16384

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"model_info": {}},
            {"model_info": "not-a-dict", "parameters": 17},
            {"model_info": {"qwen3.context_length": "lots"}},
            {"parameters": "temperature 0.1\nnum_ctx\nnum_ctx not-a-number"},
        ],
    )
    async def test_unreadable_body_stays_unknown(
        self, hass: HomeAssistant, body: dict[str, Any]
    ) -> None:
        provider, _ = _ollama(hass, _FakeResponse(200, body))
        await provider.async_refresh_capabilities()
        assert provider.context_window is None

    async def test_non_200_stays_unknown(self, hass: HomeAssistant) -> None:
        # An older Ollama, or a model that isn't pulled, 404s here.
        provider, _ = _ollama(hass, _FakeResponse(404))
        await provider.async_refresh_capabilities()
        assert provider.context_window is None

    async def test_transport_failure_is_non_fatal(self, hass: HomeAssistant) -> None:
        provider = OllamaProvider(hass, model="qwen3:8b", host="http://ollama:11434")
        provider._get_session = lambda: _BoomSession()  # type: ignore[method-assign]
        await provider.async_refresh_capabilities()
        assert provider.context_window is None

    async def test_failure_keeps_a_previous_good_reading(self, hass: HomeAssistant) -> None:
        provider = OllamaProvider(hass, model="qwen3:8b", host="http://ollama:11434")
        provider._context_window = 32768
        provider._get_session = lambda: _BoomSession()  # type: ignore[method-assign]
        await provider.async_refresh_capabilities()
        assert provider.context_window == 32768

    async def test_probe_is_ttl_cached(self, hass: HomeAssistant) -> None:
        provider, session = _ollama(
            hass, _FakeResponse(200, {"model_info": {"qwen3.context_length": 40960}})
        )
        await provider.async_refresh_capabilities()
        await provider.async_refresh_capabilities()
        assert len(session.post_calls) == 1

    async def test_first_probe_runs_on_freshly_booted_host(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time as _time

        monkeypatch.setattr(_time, "monotonic", lambda: 42.0)
        provider, session = _ollama(
            hass,
            _FakeResponse(
                200,
                {"model_info": {"qwen3.context_length": 40960}, "parameters": "num_ctx 32768"},
            ),
        )
        await provider.async_refresh_capabilities()
        assert len(session.post_calls) == 1
        assert provider.context_window == 32768


class TestTrainedContextLength:
    """The GGUF trained length no longer caps the reported window, so it is
    advisory only — it drives the "served in less room than it was trained
    for" hint. Asserted directly, because the reported window can no longer
    show whether the architecture-prefixed key was matched."""

    @pytest.mark.parametrize(
        "arch_key",
        ["llama.context_length", "qwen3.context_length", "some.future.arch.context_length"],
    )
    def test_architecture_prefix_is_not_hardcoded(self, arch_key: str) -> None:
        assert OllamaProvider._trained_context_length({arch_key: 40960}) == 40960

    def test_sibling_gguf_keys_are_ignored(self) -> None:
        # The same map carries embedding_length, attention.key_length and
        # friends, so the suffix has to match exactly.
        assert (
            OllamaProvider._trained_context_length(
                {"qwen3.embedding_length": 4096, "qwen3.attention.key_length": 128}
            )
            is None
        )

    async def test_under_served_model_is_reported_to_the_user(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ) -> None:
        provider, _ = _ollama(
            hass,
            _FakeResponse(
                200,
                {"model_info": {"qwen3.context_length": 40960}, "parameters": "num_ctx 4096"},
            ),
        )
        with caplog.at_level(logging.INFO):
            await provider.async_refresh_capabilities()
        assert provider.context_window == 4096
        assert "4096" in caplog.text
        assert "40960" in caplog.text
        assert "OLLAMA_CONTEXT_LENGTH" in caplog.text

    async def test_no_hint_when_the_model_is_served_in_full(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ) -> None:
        provider, _ = _ollama(
            hass,
            _FakeResponse(
                200,
                {"model_info": {"qwen3.context_length": 8192}, "parameters": "num_ctx 8192"},
            ),
        )
        with caplog.at_level(logging.INFO):
            await provider.async_refresh_capabilities()
        assert "OLLAMA_CONTEXT_LENGTH" not in caplog.text


# ── The request body must not move ───────────────────────────────────


class TestRequestBodyUnchanged:
    """Discovery is read-only: the wire format is byte-identical.

    Ollama's OpenAI-compatible endpoint has no context field (see
    ``OllamaProvider.async_refresh_capabilities``), so nothing about the
    discovered window may leak into the payload — and no prompt bytes
    change either.
    """

    def _payload(self, provider: OllamaProvider) -> dict[str, Any]:
        return dict(
            provider.build_payload(
                "system prompt",
                [{"role": "user", "content": "turn on the lights"}],
                tools=[{"type": "function", "function": {"name": "noop"}}],
                stream=True,
                max_tokens=256,
            )
        )

    def test_payload_identical_before_and_after_discovery(self, hass: HomeAssistant) -> None:
        provider = OllamaProvider(hass, model="qwen3:8b")
        before = self._payload(provider)
        provider._context_window = 32768
        after = self._payload(provider)
        assert before == after

    def test_payload_carries_no_context_field(self, hass: HomeAssistant) -> None:
        provider = OllamaProvider(hass, model="qwen3:8b")
        provider._context_window = 32768
        payload = self._payload(provider)
        assert "num_ctx" not in payload
        assert "options" not in payload
        assert set(payload) == {
            "model",
            "messages",
            "max_tokens",
            "tools",
            "stream",
            "stream_options",
        }

    def test_endpoint_is_still_openai_compatible(self, hass: HomeAssistant) -> None:
        provider = OllamaProvider(hass, model="qwen3:8b", host="http://ollama:11434")
        assert provider._endpoint == "http://ollama:11434/v1/chat/completions"
