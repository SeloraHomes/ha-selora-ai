"""Selora AI Local names its specialist in each request's ``lora`` field.

llama-server clears a slot's cached prompt on an adapter change only when the
adapters come in the request; a global ``POST /lora-adapters`` leaves KV
computed under one specialist in place for the next. So every completion
carries the full vector, and nothing is POSTed to ``/lora-adapters``.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.selora_ai.const import (
    SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED,
    SELORA_LOCAL_OLLAMA_UNIFIED_MODEL_FAMILY,
)
from custom_components.selora_ai.providers.selora_local import SeloraLocalProvider

# Out of order and not 0..n-1, so the vector must come from discovery.
_ADAPTERS = [
    {"id": 3, "path": "/m/selora-answer.gguf"},
    {"id": 0, "path": "/m/selora-command.gguf"},
    {"id": 4, "path": "/m/selora-utilities.gguf"},
    {"id": 1, "path": "/m/selora-automation.gguf"},
    {"id": 2, "path": "/m/selora-clarification.gguf"},
]


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
    """Records every POST and the JSON body each completion carried."""

    def __init__(self, adapters: list[dict[str, Any]] | None) -> None:
        self.adapters = adapters
        self.posts: list[str] = []
        self.bodies: list[dict[str, Any]] = []

    def get(self, url: str, **_kw: Any) -> _FakeResponse:
        if url.endswith("/v1/models"):
            return _FakeResponse(200, {"data": [{"id": "selora"}]})
        if url.endswith("/lora-adapters"):
            if self.adapters is None:
                return _FakeResponse(404, None)
            return _FakeResponse(200, self.adapters)
        if url.endswith("/api/tags"):
            return _FakeResponse(200, {"models": [{"name": f"{FAM}:9.9.9"}]})
        return _FakeResponse(404, None)

    def post(self, url: str, **kw: Any) -> _FakeResponse:
        self.posts.append(url)
        if "data" in kw:
            self.bodies.append(json.loads(kw["data"]))
        return _FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]})


FAM = SELORA_LOCAL_OLLAMA_UNIFIED_MODEL_FAMILY


def make(hub: _FakeHub, **kwargs: Any) -> SeloraLocalProvider:
    hass = MagicMock()
    hass.states.async_all.return_value = []
    provider = SeloraLocalProvider(hass, host="http://hub.invalid:8080", **kwargs)
    provider._get_session = lambda: hub  # type: ignore[method-assign]
    provider._get_headers = lambda: {}  # type: ignore[method-assign]
    provider._specialist_prompts_loaded = True
    provider._specialist_prompts = {}
    return provider


async def turn(provider: SeloraLocalProvider, kind: str) -> None:
    provider.set_call_kind(kind)
    provider.set_chat_context(
        user_message="turn on the lamp", entities=[], existing_automations=[], history=[]
    )
    _, err = await provider.send_request(
        "", [{"role": "user", "content": "turn on the lamp"}], max_tokens=8
    )
    assert err is None


@pytest.mark.parametrize(
    ("kind", "target"),
    [
        ("chat_command", 0),
        ("chat_automation", 1),
        ("chat_clarification", 2),
        ("chat_answer", 3),
        ("chat_utilities", 4),
    ],
)
async def test_each_request_names_every_adapter_and_scales_its_own(kind: str, target: int) -> None:
    hub = _FakeHub(_ADAPTERS)
    await turn(make(hub), kind)

    lora = hub.bodies[-1]["lora"]
    assert {entry["id"] for entry in lora} == {0, 1, 2, 3, 4}
    assert {entry["id"]: entry["scale"] for entry in lora} == {
        i: (1.0 if i == target else 0.0) for i in range(5)
    }


async def test_switching_specialists_never_posts_to_lora_adapters() -> None:
    hub = _FakeHub(_ADAPTERS)
    provider = make(hub)
    for kind in ("chat_command", "chat_answer", "chat_command"):
        await turn(provider, kind)

    assert not any(url.endswith("/lora-adapters") for url in hub.posts)
    assert len(hub.bodies) == 3
    assert [next(e["id"] for e in b["lora"] if e["scale"] == 1.0) for b in hub.bodies] == [0, 3, 0]


async def test_an_intent_with_no_adapter_runs_every_adapter_at_zero() -> None:
    hub = _FakeHub([{"id": 0, "path": "/m/selora-command.gguf"}, {"id": 1, "path": "/m/x.gguf"}])
    await turn(make(hub), "chat_answer")

    assert hub.bodies[-1]["lora"] == [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 0.0}]


async def test_a_hub_without_adapters_gets_no_lora_field() -> None:
    hub = _FakeHub(None)
    await turn(make(hub), "chat_command")

    assert "lora" not in hub.bodies[-1]


async def test_the_ollama_backend_sends_no_lora_field() -> None:
    hub = _FakeHub(_ADAPTERS)
    provider = make(hub, selora_local_backend=SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED)
    await turn(provider, "chat_answer")

    assert "lora" not in hub.bodies[-1]
    assert not any(url.endswith("/lora-adapters") for url in hub.posts)


async def test_prewarm_sends_one_command_request_on_llama_server() -> None:
    hub = _FakeHub(_ADAPTERS)
    await make(hub).prewarm(entities=[])

    assert len(hub.bodies) == 1
    assert {e["id"]: e["scale"] for e in hub.bodies[0]["lora"]}[0] == 1.0
