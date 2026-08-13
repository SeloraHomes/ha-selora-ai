"""What each adapter puts on the wire, and how a rejection corrects it.

Which parameters a chat-completions request must carry is a property of
the *model*, not of the endpoint, and the model field in settings is free
text. Two 400s from the same GPT-5-family model showed the shape of it:
``max_tokens`` rejected in favour of ``max_completion_tokens``, then
function tools refused unless ``reasoning_effort`` is stated explicitly.
Neither was catchable by a table of model names, and the first was not
caught at all — the payload tests asserted the token cap's *value* and
never its *key*.

So these pin two things: which parameters each adapter sends by
construction, and the repair path that reads the server's own remedy out
of a 400 and retries once — a rename, a value the request must state, and
the withdrawal of one — so the next model to change the rules costs a
retried request rather than a release.
"""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.selora_ai.providers.anthropic import AnthropicProvider
from custom_components.selora_ai.providers.gemini import GeminiProvider
from custom_components.selora_ai.providers.ollama import OllamaProvider
from custom_components.selora_ai.providers.openai import OpenAIProvider
from custom_components.selora_ai.providers.openai_compat import TOKEN_CAP_KEYS
from custom_components.selora_ai.providers.openrouter import OpenRouterProvider
from custom_components.selora_ai.providers.selora_cloud import SeloraCloudProvider

_MESSAGES = [{"role": "user", "content": "how many lights are on?"}]

# The verbatim body api.openai.com returns for a GPT-5-family model sent
# ``max_tokens`` — the failure this module exists for.
_OPENAI_REJECTS_MAX_TOKENS = (
    '{"error": {"message": "Unsupported parameter: \'max_tokens\' is not supported with '
    'this model. Use \'max_completion_tokens\' instead.", "type": "invalid_request_error", '
    '"param": "max_tokens", "code": "unsupported_parameter"}}'
)

# The mirror image: a backend that only knows the older spelling.
_BACKEND_REJECTS_MAX_COMPLETION_TOKENS = (
    '{"error": {"message": "Unsupported parameter: \'max_completion_tokens\'. '
    'Use \'max_tokens\' instead.", "type": "invalid_request_error"}}'
)

# The same model's next refusal, once the cap was right: a parameter we
# never send, whose value the request has to state explicitly before the
# model will accept function tools at all.
_OPENAI_DEMANDS_REASONING_EFFORT = (
    '{"error": {"message": "Function tools with reasoning_effort are not supported for '
    "gpt-5.6-terra in /v1/chat/completions. To use function tools, use /v1/responses or set "
    'reasoning_effort to \'none\'.", "type": "invalid_request_error"}}'
)

# A backend that has no such knob, for the withdrawal path.
_BACKEND_REJECTS_REASONING_EFFORT = (
    '{"error": {"message": "Unsupported parameter: \'reasoning_effort\'.", '
    '"type": "invalid_request_error"}}'
)


class _FakeResponse:
    """Async-context-manager stand-in for an aiohttp response."""

    def __init__(self, status: int, body: str = "", payload: dict | None = None) -> None:
        self.status = status
        self._body = body
        self._payload = payload or {}

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def text(self) -> str:
        return self._body

    async def json(self) -> dict:
        return self._payload


class _RecordingSession:
    """Serves a scripted list of responses and records each request body."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self.bodies: list[dict[str, Any]] = []

    def post(self, *_a: object, **kwargs: object) -> _FakeResponse:
        import json

        raw = kwargs.get("data")
        assert isinstance(raw, bytes)
        self.bodies.append(json.loads(raw.decode("utf-8")))
        return self._responses.pop(0)


def _completion(text: str = "Three.") -> dict[str, Any]:
    return {"choices": [{"message": {"content": text}}]}


# ---------------------------------------------------------------------------
# Which key each adapter serializes the cap under
# ---------------------------------------------------------------------------


class TestTokenCapKeyPerAdapter:
    def test_openai_uses_max_completion_tokens(self, hass) -> None:
        # The reasoning families reject the other spelling outright, and
        # the older ones accept both — so this is the name that works for
        # every model the free-text settings field can name.
        provider = OpenAIProvider(hass, api_key="sk-test", model="gpt-5.4")
        payload = provider.build_payload("sys", _MESSAGES, max_tokens=4096)
        assert payload["max_completion_tokens"] == 4096
        assert "max_tokens" not in payload

    @pytest.mark.parametrize("model", ["gpt-4o", "gpt-5", "o3", "gpt-5.6-terra"])
    def test_openai_key_does_not_depend_on_the_model_name(self, hass, model: str) -> None:
        # No model list to rot: a snapshot or a family that does not exist
        # yet gets the same key as the catalog default.
        provider = OpenAIProvider(hass, api_key="sk-test", model=model)
        assert "max_completion_tokens" in provider.build_payload("sys", _MESSAGES)

    def test_ollama_uses_max_tokens(self, hass) -> None:
        provider = OllamaProvider(hass, model="llama4")
        payload = provider.build_payload("sys", _MESSAGES, max_tokens=2048)
        assert payload["max_tokens"] == 2048
        assert "max_completion_tokens" not in payload

    def test_openrouter_uses_max_tokens(self, hass) -> None:
        # The gateway normalizes the cap onto whatever the routed vendor
        # wants, so the OpenAI-compat default is the right one to send.
        provider = OpenRouterProvider(hass, api_key="sk-or", model="openai/gpt-5.4")
        payload = provider.build_payload("sys", _MESSAGES, max_tokens=1500)
        assert payload["max_tokens"] == 1500
        assert "max_completion_tokens" not in payload

    def test_selora_cloud_uses_max_tokens(self, hass) -> None:
        provider = SeloraCloudProvider(hass, access_token="ey.test")
        payload = provider.build_payload("sys", _MESSAGES, max_tokens=900)
        assert payload["max_tokens"] == 900
        assert "max_completion_tokens" not in payload

    def test_anthropic_uses_max_tokens(self, hass) -> None:
        # /v1/messages has one spelling and requires it on every request.
        provider = AnthropicProvider(hass, api_key="sk-ant-test")
        payload = provider.build_payload("sys", _MESSAGES, max_tokens=3000)
        assert payload["max_tokens"] == 3000

    def test_gemini_sends_no_output_cap(self, hass) -> None:
        # Deliberate: on the 2.5 series maxOutputTokens counts thinking
        # tokens too, so capping a chat turn at 1024 can return an empty
        # candidate with finishReason MAX_TOKENS. Asserted so the omission
        # reads as a decision rather than a hole.
        provider = GeminiProvider(hass, api_key="k")
        payload = provider.build_payload("sys", _MESSAGES, max_tokens=1024)
        assert "generationConfig" not in payload


# ---------------------------------------------------------------------------
# repair_payload — correcting a wrong guess from the server's own error
# ---------------------------------------------------------------------------


class TestRepairPayload:
    def test_renames_to_the_key_the_server_names(self, hass) -> None:
        provider = OllamaProvider(hass, model="llama4")
        repaired = provider.repair_payload(
            {"model": "m", "max_tokens": 512},
            400,
            _OPENAI_REJECTS_MAX_TOKENS,
        )
        assert repaired == {"model": "m", "max_completion_tokens": 512}

    def test_renames_in_the_other_direction_too(self, hass) -> None:
        provider = OpenAIProvider(hass, api_key="sk-test", model="legacy")
        repaired = provider.repair_payload(
            {"model": "m", "max_completion_tokens": 512},
            400,
            _BACKEND_REJECTS_MAX_COMPLETION_TOKENS,
        )
        assert repaired == {"model": "m", "max_tokens": 512}

    def test_remembers_the_correction(self, hass) -> None:
        # One rejected request pays the retry; every later call on this
        # provider is right the first time.
        provider = OllamaProvider(hass, model="llama4")
        provider.repair_payload({"max_tokens": 512}, 400, _OPENAI_REJECTS_MAX_TOKENS)
        assert "max_completion_tokens" in provider.build_payload("sys", _MESSAGES)

    @pytest.mark.parametrize("status", [401, 429, 500])
    def test_ignores_non_400_statuses(self, hass, status: int) -> None:
        provider = OpenAIProvider(hass, api_key="sk-test")
        assert (
            provider.repair_payload({"max_tokens": 1}, status, _OPENAI_REJECTS_MAX_TOKENS) is None
        )

    def test_ignores_a_400_that_names_no_replacement(self, hass) -> None:
        provider = OpenAIProvider(hass, api_key="sk-test")
        body = '{"error": {"message": "Invalid value for temperature."}}'
        assert provider.repair_payload({"max_tokens": 1}, 400, body) is None

    def test_ignores_a_replacement_that_is_not_a_cap_key(self, hass) -> None:
        # Repair only what this hook understands — a message telling us to
        # use some other parameter must surface as the real error.
        provider = OpenAIProvider(hass, api_key="sk-test")
        body = '{"error": {"message": "Use \'reasoning_effort\' instead."}}'
        assert provider.repair_payload({"max_tokens": 1}, 400, body) is None

    def test_ignores_when_the_stale_key_is_absent(self, hass) -> None:
        provider = OpenAIProvider(hass, api_key="sk-test")
        assert provider.repair_payload({"model": "m"}, 400, _OPENAI_REJECTS_MAX_TOKENS) is None

    def test_leaves_the_original_payload_untouched(self, hass) -> None:
        provider = OllamaProvider(hass, model="llama4")
        payload = {"model": "m", "max_tokens": 512}
        provider.repair_payload(payload, 400, _OPENAI_REJECTS_MAX_TOKENS)
        assert payload == {"model": "m", "max_tokens": 512}

    def test_base_providers_repair_nothing_by_default(self, hass) -> None:
        # Anthropic and Gemini keep the base no-op: their APIs have one
        # spelling, so a 400 there means something a rename can't fix.
        anthropic = AnthropicProvider(hass, api_key="sk-ant-test")
        gemini = GeminiProvider(hass, api_key="k")
        assert anthropic.repair_payload({"max_tokens": 1}, 400, "boom") is None
        assert gemini.repair_payload({"max_tokens": 1}, 400, "boom") is None


# ---------------------------------------------------------------------------
# The retry, end to end through the request template methods
# ---------------------------------------------------------------------------


class TestRequestRetriesOnRepair:
    async def test_send_request_retries_with_the_corrected_key(
        self, hass, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = OllamaProvider(hass, model="llama4")
        session = _RecordingSession(
            [
                _FakeResponse(400, _OPENAI_REJECTS_MAX_TOKENS),
                _FakeResponse(200, payload=_completion()),
            ]
        )
        monkeypatch.setattr(provider, "_get_session", lambda: session)

        text, error = await provider.send_request("sys", _MESSAGES, max_tokens=777)

        assert (text, error) == ("Three.", None)
        assert "max_tokens" in session.bodies[0]
        assert session.bodies[1]["max_completion_tokens"] == 777
        assert "max_tokens" not in session.bodies[1]

    async def test_send_request_retries_only_once(
        self, hass, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A server still rejecting the corrected body is saying something
        # the error no longer explains; the user gets that error rather
        # than a provider guessing in a loop.
        provider = OllamaProvider(hass, model="llama4")
        session = _RecordingSession(
            [
                _FakeResponse(400, _OPENAI_REJECTS_MAX_TOKENS),
                _FakeResponse(400, _BACKEND_REJECTS_MAX_COMPLETION_TOKENS),
            ]
        )
        monkeypatch.setattr(provider, "_get_session", lambda: session)

        text, error = await provider.send_request("sys", _MESSAGES)

        assert text is None
        assert error is not None
        assert error.startswith("HTTP 400")
        assert len(session.bodies) == 2

    async def test_unrepairable_error_is_not_retried(
        self, hass, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = OllamaProvider(hass, model="llama4")
        session = _RecordingSession([_FakeResponse(401, '{"error": {"message": "bad key"}}')])
        monkeypatch.setattr(provider, "_get_session", lambda: session)

        _text, error = await provider.send_request("sys", _MESSAGES)

        assert error is not None
        assert error.startswith("HTTP 401")
        assert len(session.bodies) == 1

    async def test_stream_retries_with_the_corrected_key(
        self, hass, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The reported failure surfaced as "LLM stream: HTTP 400" — the
        # chat path opens its connection through raw_request_stream, so
        # the retry has to live there too, not only on send_request.
        provider = OllamaProvider(hass, model="llama4")
        ok = _FakeResponse(200)
        session = _RecordingSession([_FakeResponse(400, _OPENAI_REJECTS_MAX_TOKENS), ok])
        monkeypatch.setattr(provider, "_get_session", lambda: session)

        opened = [resp async for resp in provider.raw_request_stream("sys", _MESSAGES)]

        assert opened == [ok]
        assert session.bodies[1]["max_completion_tokens"] == 4096

    async def test_a_raising_repair_hook_keeps_the_real_error(
        self, hass, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = OllamaProvider(hass, model="llama4")

        def _boom(*_a: object, **_kw: object) -> None:
            raise RuntimeError("repair bug")

        monkeypatch.setattr(provider, "repair_payload", _boom)
        session = _RecordingSession([_FakeResponse(400, _OPENAI_REJECTS_MAX_TOKENS)])
        monkeypatch.setattr(provider, "_get_session", lambda: session)

        _text, error = await provider.send_request("sys", _MESSAGES)

        assert error is not None
        assert error.startswith("HTTP 400")


class TestServerDirectedParams:
    """ "Set X to 'Y'" — a value the request must state before it is accepted."""

    def test_sets_the_parameter_the_server_names(self, hass) -> None:
        provider = OpenAIProvider(hass, api_key="sk-test", model="gpt-5.6-terra")
        repaired = provider.repair_payload(
            {"model": "gpt-5.6-terra", "tools": [{"type": "function"}]},
            400,
            _OPENAI_DEMANDS_REASONING_EFFORT,
        )
        assert repaired is not None
        assert repaired["reasoning_effort"] == "none"
        # The tools stay: dropping them would answer the user with a
        # silently degraded turn instead of the reply they asked for.
        assert repaired["tools"] == [{"type": "function"}]

    def test_the_directive_reaches_every_later_payload(self, hass) -> None:
        provider = OpenAIProvider(hass, api_key="sk-test", model="gpt-5.6-terra")
        provider.repair_payload({"tools": []}, 400, _OPENAI_DEMANDS_REASONING_EFFORT)
        prepared = provider.prepare_payload(provider.build_payload("sys", _MESSAGES))
        assert prepared["reasoning_effort"] == "none"

    def test_applies_after_a_subclass_has_finished_the_body(self, hass) -> None:
        # OpenRouter and Selora Cloud add their own routing and sampling
        # settings on top of the shared body, so a directive has to be
        # applied at the request boundary rather than inside build_payload.
        provider = OpenRouterProvider(hass, api_key="sk-or", model="openai/gpt-5.6-terra")
        provider.repair_payload({"tools": []}, 400, _OPENAI_DEMANDS_REASONING_EFFORT)
        prepared = provider.prepare_payload(provider.build_payload("sys", _MESSAGES))
        assert prepared["reasoning_effort"] == "none"
        assert prepared["provider"] == {"sort": "latency"}

    def test_ignores_a_parameter_outside_the_allowlist(self, hass) -> None:
        # A backend must not be able to rewrite the sampling settings this
        # integration pins on purpose.
        provider = OpenRouterProvider(hass, api_key="sk-or")
        body = '{"error": {"message": "set temperature to \'1\'"}}'
        assert provider.repair_payload({"temperature": 0.2}, 400, body) is None
        assert provider.prepare_payload(provider.build_payload("sys", _MESSAGES))[
            "temperature"
        ] == pytest.approx(0.2)

    def test_no_retry_when_the_value_is_already_set(self, hass) -> None:
        provider = OpenAIProvider(hass, api_key="sk-test")
        payload = {"reasoning_effort": "none", "tools": []}
        assert provider.repair_payload(payload, 400, _OPENAI_DEMANDS_REASONING_EFFORT) is None

    def test_withdraws_a_directive_a_backend_rejects(self, hass) -> None:
        provider = OpenAIProvider(hass, api_key="sk-test")
        provider.repair_payload({"tools": []}, 400, _OPENAI_DEMANDS_REASONING_EFFORT)

        repaired = provider.repair_payload(
            {"tools": [], "reasoning_effort": "none"},
            400,
            _BACKEND_REJECTS_REASONING_EFFORT,
        )

        assert repaired == {"tools": []}
        assert "reasoning_effort" not in provider.prepare_payload(
            provider.build_payload("sys", _MESSAGES)
        )

    def test_never_withdraws_a_parameter_we_send_by_construction(self, hass) -> None:
        # Only a learned directive is withdrawable. Dropping tools or the
        # token cap to make a request succeed trades an error the user can
        # act on for a turn that quietly does less.
        provider = OpenAIProvider(hass, api_key="sk-test")
        body = '{"error": {"message": "Unsupported parameter: \'tools\'."}}'
        assert provider.repair_payload({"tools": [{"type": "function"}]}, 400, body) is None

    async def test_stream_retries_with_the_directed_parameter(
        self, hass, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The failure arrives on the tool-bearing chat stream, which opens
        # its connection through raw_request_stream.
        provider = OpenAIProvider(hass, api_key="sk-test", model="gpt-5.6-terra")
        ok = _FakeResponse(200)
        session = _RecordingSession([_FakeResponse(400, _OPENAI_DEMANDS_REASONING_EFFORT), ok])
        monkeypatch.setattr(provider, "_get_session", lambda: session)

        opened = [
            resp
            async for resp in provider.raw_request_stream(
                "sys", _MESSAGES, tools=[{"type": "function"}]
            )
        ]

        assert opened == [ok]
        assert "reasoning_effort" not in session.bodies[0]
        assert session.bodies[1]["reasoning_effort"] == "none"
        assert session.bodies[1]["tools"] == [{"type": "function"}]

    def test_directives_are_per_instance(self, hass) -> None:
        # A learned value must not leak into a provider built for another
        # model — the dict is instance state, not a class attribute.
        learned = OpenAIProvider(hass, api_key="sk-test", model="gpt-5.6-terra")
        learned.repair_payload({"tools": []}, 400, _OPENAI_DEMANDS_REASONING_EFFORT)
        fresh = OpenAIProvider(hass, api_key="sk-test", model="gpt-4o")
        assert "reasoning_effort" not in fresh.prepare_payload(
            fresh.build_payload("sys", _MESSAGES)
        )


class TestTokenCapKeysConstant:
    def test_holds_exactly_the_two_spellings(self) -> None:
        # repair_payload derives the stale key by elimination, which only
        # works while this pair has exactly two members.
        assert TOKEN_CAP_KEYS == ("max_tokens", "max_completion_tokens")
