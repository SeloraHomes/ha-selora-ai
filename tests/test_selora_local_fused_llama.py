"""The fused build served by llama-server gets the router prompt, not the specialists'.

Selora OS can serve the fused self-routing model from llama-server, with no
adapters, under ``SELORA_LOCAL_UNIFIED_MODEL_ALIAS``. That model was trained on
the router prompt for every intent, so the llama backend must send it what the
ollama-unified backend sends: the router prompt, its token budget and no
``lora`` field. Sending it the specialists' prompts fails nowhere and degrades
every answer, which is why an adapter-less hub must not settle before its model
id is known, and why a hub that changes model is noticed without a restart.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import MagicMock

import aiohttp
import pytest

from custom_components.selora_ai.const import (
    SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED,
    SELORA_LOCAL_UNIFIED_MODEL_ALIAS,
)
from custom_components.selora_ai.providers.selora_local import SeloraLocalProvider
from custom_components.selora_ai.providers.selora_local.runtime.serving import (
    _SELORA_LOCAL_MODEL_RECHECK_RETRY_S,
    _SELORA_LOCAL_MODEL_RECHECK_S,
)
from custom_components.selora_ai.providers.selora_local.utilities.rag import (
    _selora_local_load_doc_chunks,
)

_ADAPTERS = [
    {"id": 0, "path": "/m/selora-command.f16.gguf"},
    {"id": 1, "path": "/m/selora-automation.f16.gguf"},
    {"id": 2, "path": "/m/selora-answer.f16.gguf"},
    {"id": 3, "path": "/m/selora-clarification.f16.gguf"},
    {"id": 4, "path": "/m/selora-utilities.f16.gguf"},
]
_BASE_ID = "/var/lib/selorahomes/models/qwen3_17b_base.Q6_K.gguf"

_PROMPTS = {
    "command": "COMMAND PROMPT",
    "automation": "AUTOMATION PROMPT",
    "answer": "ANSWER PROMPT",
    "clarification": "CLARIFICATION PROMPT",
    "utilities": "UTILITIES PROMPT",
    "unified": "ROUTER PROMPT",
}

_KINDS = ("chat_command", "chat_automation", "chat_answer", "chat_clarification", "chat_utilities")


class _FakeResponse:
    def __init__(self, status: int = 200, payload: Any = None) -> None:
        self.status = status
        self._payload = payload

    async def json(self) -> Any:
        return self._payload

    async def text(self) -> str:
        return ""

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeHub:
    """A llama-server whose model can change under the provider, as Selora OS does."""

    def __init__(
        self, model_id: str | None, adapters: list[dict[str, Any]], *, models_status: int = 200
    ) -> None:
        self.model_id = model_id
        self.adapters = adapters
        self.models_status = models_status
        self.models_gets = 0
        self.bodies: list[dict[str, Any]] = []
        self.refusing = False

    def get(self, url: str, **_kw: Any) -> _FakeResponse:
        if url.endswith("/v1/models"):
            self.models_gets += 1
            if self.models_status != 200:
                return _FakeResponse(self.models_status, None)
            return _FakeResponse(200, {"data": [{"id": self.model_id, "meta": {"n_ctx": 8192}}]})
        if url.endswith("/lora-adapters"):
            return _FakeResponse(200, self.adapters)
        return _FakeResponse(404, None)

    def post(self, url: str, **kw: Any) -> _FakeResponse:
        if self.refusing:
            raise aiohttp.ClientConnectionError("Connection refused")
        body = json.loads(kw["data"]) if "data" in kw else {}
        known = {a["id"] for a in self.adapters}
        if any(e["id"] not in known for e in body.get("lora", [])):
            # llama-server rejects a request naming an adapter it has not loaded.
            return _FakeResponse(400, {"error": {"message": "invalid adapter id"}})
        self.bodies.append(body)
        return _FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]})

    def fused(self) -> None:
        self.model_id = SELORA_LOCAL_UNIFIED_MODEL_ALIAS
        self.adapters = []

    def specialists(self) -> None:
        self.model_id = _BASE_ID
        self.adapters = _ADAPTERS


def fused_hub() -> _FakeHub:
    return _FakeHub(SELORA_LOCAL_UNIFIED_MODEL_ALIAS, [])


def specialist_hub() -> _FakeHub:
    return _FakeHub(_BASE_ID, _ADAPTERS)


def make(hub: _FakeHub, **kwargs: Any) -> SeloraLocalProvider:
    hass = MagicMock()
    hass.states.async_all.return_value = []
    provider = SeloraLocalProvider(hass, host="http://hub.invalid:8080", **kwargs)
    provider._get_session = lambda: hub  # type: ignore[method-assign]
    provider._get_headers = lambda: {}  # type: ignore[method-assign]
    provider._specialist_prompts_loaded = True
    provider._specialist_prompts = dict(_PROMPTS)
    _selora_local_load_doc_chunks()
    return provider


async def turn(
    provider: SeloraLocalProvider, kind: str, message: str = "turn on the lamp"
) -> tuple[str | None, str | None]:
    provider.set_call_kind(kind)
    provider.set_chat_context(
        user_message=message, entities=[], existing_automations=[], history=[]
    )
    return await provider.send_request("", [{"role": "user", "content": message}], max_tokens=8)


def system_of(body: dict[str, Any]) -> str:
    return next(m["content"] for m in body["messages"] if m["role"] == "system")


def user_of(body: dict[str, Any]) -> str:
    return [m["content"] for m in body["messages"] if m["role"] == "user"][-1]


def let_the_recheck_fall_due(provider: SeloraLocalProvider) -> None:
    """Stand in for the clock: the routing was last confirmed a re-check interval ago."""
    provider._routed_model_checked_at = time.monotonic() - _SELORA_LOCAL_MODEL_RECHECK_S - 1


# ── the fused build on llama-server ──────────────────────────────────────────


@pytest.mark.parametrize("kind", _KINDS)
async def test_the_fused_model_gets_the_router_prompt_for_every_intent(kind: str) -> None:
    hub = fused_hub()
    _, err = await turn(make(hub), kind)

    assert err is None
    assert system_of(hub.bodies[-1]) == "ROUTER PROMPT"
    assert "lora" not in hub.bodies[-1]


async def test_the_fused_model_keeps_llama_servers_request_shape() -> None:
    """Only the model changed: the runtime is still llama-server, not Ollama."""
    hub = fused_hub()
    await turn(make(hub), "chat_command")

    body = hub.bodies[-1]
    assert body["model"] == SELORA_LOCAL_UNIFIED_MODEL_ALIAS
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_effort" not in body


async def test_a_utilities_question_still_carries_the_docs_block() -> None:
    hub = fused_hub()
    await turn(make(hub), "chat_utilities", "how do I update home assistant core")

    assert system_of(hub.bodies[-1]) == "ROUTER PROMPT"
    assert "RELEVANT DOCS:" in user_of(hub.bodies[-1])


async def test_the_specialist_bundle_is_routed_exactly_as_before() -> None:
    hub = specialist_hub()
    await turn(make(hub), "chat_answer")

    body = hub.bodies[-1]
    assert system_of(body) == "ANSWER PROMPT"
    assert {e["id"]: e["scale"] for e in body["lora"]}[2] == 1.0


async def test_the_alias_beside_adapters_is_not_the_fused_build() -> None:
    """The fused build has no adapters to route between; a hub that loaded them is
    serving specialists whatever it named the base."""
    hub = _FakeHub(SELORA_LOCAL_UNIFIED_MODEL_ALIAS, _ADAPTERS)
    await turn(make(hub), "chat_answer")

    assert system_of(hub.bodies[-1]) == "ANSWER PROMPT"
    assert "lora" in hub.bodies[-1]


async def test_another_model_without_adapters_still_gets_specialist_prompts() -> None:
    hub = _FakeHub(_BASE_ID, [])
    await turn(make(hub), "chat_answer")

    assert system_of(hub.bodies[-1]) == "ANSWER PROMPT"
    assert "lora" not in hub.bodies[-1]


@pytest.mark.parametrize("automation", [False, True])
async def test_the_fused_model_is_budgeted_like_the_ollama_one(automation: bool) -> None:
    """Both send the router prompt, so both reserve for it."""
    hub = fused_hub()
    llama = make(hub)
    await llama._ensure_lora_discovery()
    ollama = make(hub, selora_local_backend=SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED)

    assert llama._reserved_tokens(automation=automation) == ollama._reserved_tokens(
        automation=automation
    )
    assert llama._reserved_tokens(automation=automation) != make(specialist_hub())._reserved_tokens(
        automation=automation
    )


async def test_prewarm_sends_the_fused_model_one_request_per_distinct_turn() -> None:
    hub = fused_hub()
    provider = make(hub)
    await provider._ensure_lora_discovery()
    kinds = provider._prewarm_kinds([])

    await provider.prewarm(entities=[])

    assert len(hub.bodies) == len(kinds)
    assert all(system_of(b) == "ROUTER PROMPT" and "lora" not in b for b in hub.bodies)


# ── an adapter-less hub must not be guessed at ──────────────────────────────


async def test_no_adapters_and_no_model_id_does_not_settle() -> None:
    hub = _FakeHub(SELORA_LOCAL_UNIFIED_MODEL_ALIAS, [], models_status=503)
    provider = make(hub)

    _, err = await turn(provider, "chat_answer")

    assert err is not None
    assert hub.bodies == [], "no request may go out with a guessed prompt"
    assert provider._lora_slots is None


async def test_it_settles_on_the_router_prompt_once_the_model_answers() -> None:
    hub = _FakeHub(SELORA_LOCAL_UNIFIED_MODEL_ALIAS, [], models_status=503)
    provider = make(hub)
    await turn(provider, "chat_answer")

    hub.models_status = 200
    provider._discovery_retry_after = 0.0
    _, err = await turn(provider, "chat_answer")

    assert err is None
    assert system_of(hub.bodies[-1]) == "ROUTER PROMPT"


# ── a hub that changes model while Home Assistant runs ──────────────────────


async def test_a_hub_moving_to_the_fused_build_is_followed() -> None:
    hub = specialist_hub()
    provider = make(hub)
    await turn(provider, "chat_answer")
    assert "lora" in hub.bodies[-1]

    hub.fused()
    let_the_recheck_fall_due(provider)
    _, err = await turn(provider, "chat_answer")

    assert err is None
    assert system_of(hub.bodies[-1]) == "ROUTER PROMPT"
    assert "lora" not in hub.bodies[-1], "the adapter ids are gone with the adapters"


async def test_a_hub_moving_back_to_the_specialists_is_followed() -> None:
    hub = fused_hub()
    provider = make(hub)
    await turn(provider, "chat_answer")

    hub.specialists()
    let_the_recheck_fall_due(provider)
    await turn(provider, "chat_answer")

    assert system_of(hub.bodies[-1]) == "ANSWER PROMPT"
    assert {e["id"]: e["scale"] for e in hub.bodies[-1]["lora"]}[2] == 1.0


async def test_the_model_is_not_re_asked_before_the_interval() -> None:
    hub = fused_hub()
    provider = make(hub)
    for kind in _KINDS:
        await turn(provider, kind)

    assert hub.models_gets == 1


async def test_a_failed_re_check_keeps_the_routing() -> None:
    """A hub that is restarting has not changed model; dropping the routing would
    fail requests that work again the moment it is back."""
    hub = fused_hub()
    provider = make(hub)
    await turn(provider, "chat_answer")

    hub.models_status = 503
    let_the_recheck_fall_due(provider)
    await provider._ensure_lora_discovery()

    assert provider._lora_slots is not None
    assert provider._serves_unified_model


async def test_an_unchanged_model_keeps_the_routing_and_waits_another_interval() -> None:
    hub = specialist_hub()
    provider = make(hub)
    await turn(provider, "chat_answer")
    gets = hub.models_gets

    let_the_recheck_fall_due(provider)
    await turn(provider, "chat_answer")
    await turn(provider, "chat_answer")

    assert hub.models_gets == gets + 1
    assert "lora" in hub.bodies[-1]


class _SlowModelsResponse(_FakeResponse):
    def __init__(self, inner: _FakeResponse, gate: asyncio.Event) -> None:
        super().__init__(inner.status, inner._payload)
        self._gate = gate

    async def json(self) -> Any:
        await self._gate.wait()
        return await super().json()


class _SlowModelsHub(_FakeHub):
    """A hub whose GET /v1/models answers only once the test opens the gate."""

    def __init__(self) -> None:
        super().__init__(_BASE_ID, _ADAPTERS)
        self.gate = asyncio.Event()
        self.gate.set()

    def get(self, url: str, **kw: Any) -> _FakeResponse:
        resp = super().get(url, **kw)
        return _SlowModelsResponse(resp, self.gate) if url.endswith("/v1/models") else resp


async def test_a_request_during_a_re_check_waits_for_its_answer() -> None:
    """A second request must not route on the routing a re-check is still asking about."""
    hub = _SlowModelsHub()
    provider = make(hub)
    await turn(provider, "chat_answer")
    sent = len(hub.bodies)

    hub.fused()
    hub.gate.clear()
    let_the_recheck_fall_due(provider)
    first = asyncio.create_task(turn(provider, "chat_answer"))
    for _ in range(5):
        await asyncio.sleep(0)
    second = asyncio.create_task(turn(provider, "chat_answer"))
    for _ in range(5):
        await asyncio.sleep(0)
    hub.gate.set()
    await asyncio.gather(first, second)

    bodies = hub.bodies[sent:]
    assert len(bodies) == 2
    assert all(system_of(b) == "ROUTER PROMPT" and "lora" not in b for b in bodies)


# ── a failed completion is the earliest sign of a swap ───────────────────────


def swapped_to_fused_moments_after_a_check(hub: _FakeHub, *, refusing: bool = False) -> None:
    """The swap restarts llama-server just after a check, so no re-check is due."""
    hub.fused()
    hub.refusing = refusing


@pytest.mark.parametrize("refusing", [False, True], ids=["rejected-adapter", "refused"])
async def test_a_failed_request_after_a_swap_rechecks_on_the_next_one(refusing: bool) -> None:
    hub = specialist_hub()
    provider = make(hub)
    await turn(provider, "chat_answer")

    swapped_to_fused_moments_after_a_check(hub, refusing=refusing)
    _, err = await turn(provider, "chat_answer")
    assert err is not None
    hub.refusing = False
    _, err = await turn(provider, "chat_answer")

    assert err is None
    assert system_of(hub.bodies[-1]) == "ROUTER PROMPT"
    assert "lora" not in hub.bodies[-1]


async def test_a_failed_streamed_request_rechecks_too() -> None:
    hub = specialist_hub()
    provider = make(hub)
    await turn(provider, "chat_answer")

    swapped_to_fused_moments_after_a_check(hub)
    provider.set_call_kind("chat_answer")
    provider.set_chat_context(
        user_message="how warm is it", entities=[], existing_automations=[], history=[]
    )
    with pytest.raises(ConnectionError):
        async for _ in provider.send_request_stream(
            "", [{"role": "user", "content": "how warm is it"}]
        ):
            pass
    _, err = await turn(provider, "chat_answer")

    assert err is None
    assert "lora" not in hub.bodies[-1]


async def test_a_failed_tool_request_rechecks_too() -> None:
    hub = specialist_hub()
    provider = make(hub)
    await turn(provider, "chat_answer")

    swapped_to_fused_moments_after_a_check(hub)
    provider.set_call_kind("chat_answer")
    with pytest.raises(ConnectionError):
        await provider.raw_request("", [{"role": "user", "content": "turn on the lamp"}])
    _, err = await turn(provider, "chat_answer")

    assert err is None
    assert "lora" not in hub.bodies[-1]


async def test_a_failure_on_an_unchanged_hub_keeps_the_routing() -> None:
    hub = specialist_hub()
    provider = make(hub)
    await turn(provider, "chat_answer")
    gets = hub.models_gets

    hub.refusing = True
    await turn(provider, "chat_answer")
    hub.refusing = False
    await turn(provider, "chat_answer")
    await turn(provider, "chat_answer")

    assert hub.models_gets == gets + 1, "one re-read, then the interval again"
    assert "lora" in hub.bodies[-1]


async def test_a_failure_during_a_re_check_keeps_the_next_one_due() -> None:
    """The probe may have read the old model just before the hub restarted."""
    hub = _SlowModelsHub()
    provider = make(hub)
    await turn(provider, "chat_answer")

    hub.gate.clear()
    let_the_recheck_fall_due(provider)
    probe = asyncio.create_task(provider._recheck_routed_model())
    for _ in range(5):
        await asyncio.sleep(0)
    provider._completion_failed()
    hub.gate.set()
    await probe

    assert provider._routed_model_recheck_due()


async def test_a_failed_re_check_asks_again_soon() -> None:
    """A hub back from a restart may serve the other model and accept the old shape."""
    hub = fused_hub()
    provider = make(hub)
    await turn(provider, "chat_answer")

    hub.models_status = 503
    let_the_recheck_fall_due(provider)
    await turn(provider, "chat_answer")
    hub.models_status = 200
    hub.specialists()
    assert provider._routed_model_checked_at is not None
    provider._routed_model_checked_at -= _SELORA_LOCAL_MODEL_RECHECK_RETRY_S
    await turn(provider, "chat_answer")

    assert system_of(hub.bodies[-1]) == "ANSWER PROMPT"
    assert "lora" in hub.bodies[-1]


async def test_a_failed_re_check_is_probed_once_per_retry_not_twice_per_request() -> None:
    """Each request would otherwise wait out a hung probe before and under its lock."""
    hub = fused_hub()
    provider = make(hub)
    await turn(provider, "chat_answer")
    gets = hub.models_gets

    hub.models_status = 503
    let_the_recheck_fall_due(provider)
    await turn(provider, "chat_answer")
    await turn(provider, "chat_answer")

    assert hub.models_gets == gets + 1
    assert provider._serves_unified_model
