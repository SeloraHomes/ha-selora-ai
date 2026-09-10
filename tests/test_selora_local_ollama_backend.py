"""The Ollama backend, exercised without an Ollama daemon.

Every disconnect covered here was invisible to the existing suite,
because the suite only ever constructs the llama-server backend.

The session is faked at the aiohttp boundary, so the provider's real
branching, model naming, prompt selection and tag resolution all run. No
socket is opened and no version is written down: tags come from the
family constant plus deliberately fictional numbers.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.selora_ai.const import (
    SELORA_LOCAL_BACKEND_LLAMA,
    SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED,
    SELORA_LOCAL_OLLAMA_UNIFIED_MODEL_FAMILY,
)
from custom_components.selora_ai.providers.selora_local import (
    _SELORA_LOCAL_PREWARM_KINDS,
    _SELORA_LOCAL_PROMPT_FILENAMES,
    _SELORA_LOCAL_UNIFIED_PROMPT_KEY,
    SeloraLocalProvider,
)

FAM = SELORA_LOCAL_OLLAMA_UNIFIED_MODEL_FAMILY
HOST = "http://hub.invalid:11434"


class _FakeResponse:
    def __init__(self, status: int = 200, payload: Any = None) -> None:
        self.status = status
        self._payload = payload

    async def json(self) -> Any:
        return self._payload

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeSession:
    """Replays canned payloads by URL suffix and records what was asked for."""

    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        self.routes = routes or {}
        self.gets: list[str] = []
        self.posts: list[str] = []

    def get(self, url: str, **_kw: Any) -> _FakeResponse:
        self.gets.append(url)
        for suffix, payload in self.routes.items():
            if url.endswith(suffix):
                return _FakeResponse(200, payload)
        return _FakeResponse(404, None)

    def post(self, url: str, **_kw: Any) -> _FakeResponse:
        self.posts.append(url)
        for suffix, payload in self.routes.items():
            if url.endswith(suffix):
                return _FakeResponse(200, payload)
        return _FakeResponse(200, {})


def make(
    backend: str, *, model: str | None = None, routes: dict[str, Any] | None = None
) -> SeloraLocalProvider:
    hass = MagicMock()
    hass.states.async_all.return_value = []
    provider = SeloraLocalProvider(
        hass, host=HOST, selora_local_backend=backend, selora_local_ollama_model=model
    )
    session = _FakeSession(routes)
    provider._get_session = lambda: session  # type: ignore[method-assign]
    provider._get_headers = lambda: {}  # type: ignore[method-assign]
    provider._fake_session = session  # type: ignore[attr-defined]
    return provider


def payload_for(
    provider: SeloraLocalProvider,
    intent_kind: str = "chat_command",
    prompts: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Run the real build_payload; return (model asked for, system prompt)."""
    provider._specialist_prompts = (
        {"command": "COMMAND PROMPT", _SELORA_LOCAL_UNIFIED_PROMPT_KEY: "ROUTER PROMPT"}
        if prompts is None
        else prompts
    )
    provider._specialist_prompts_loaded = True
    provider.set_call_kind(intent_kind)
    provider.set_chat_context(
        user_message="turn on the kitchen light", entities=[], existing_automations=[], history=[]
    )
    out = provider.build_payload("CALLER FALLBACK", [{"role": "user", "content": "x"}])
    return provider._model, out["messages"][0]["content"]


# ── which runtime the provider thinks it is talking to ───────────────────────


def test_the_backend_defaults_to_llama_server() -> None:
    """An install that predates the setting must keep the hub behaviour."""
    hass = MagicMock()
    assert SeloraLocalProvider(hass, host=HOST)._backend == SELORA_LOCAL_BACKEND_LLAMA


# ── which HTTP surface each backend is treated as having ─────────────────────


@pytest.mark.parametrize(
    ("backend", "expected"),
    [
        (SELORA_LOCAL_BACKEND_LLAMA, "/health"),
        (SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED, "/api/tags"),
    ],
)
async def test_health_check_asks_the_endpoint_the_server_actually_serves(
    backend: str, expected: str
) -> None:
    """Ollama serves no /health, so probing it reports a healthy host as
    down and a correct configuration always reads as unreachable."""
    provider = make(backend, routes={"/api/tags": {"models": []}, "/health": {}})
    assert await provider.health_check() is True
    assert provider._fake_session.gets == [f"{HOST}{expected}"]


async def test_the_ollama_backend_never_probes_for_lora_slots() -> None:
    """There is no /lora-adapters on an Ollama daemon. Probing it 404s and
    then arms the discovery retry schedule, which refuses real requests."""
    provider = make(SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED)
    await provider._ensure_lora_discovery()
    await provider._activate_lora_for_kind("chat_command")
    assert not any("lora-adapters" in url for url in provider._fake_session.gets)
    assert not provider._fake_session.posts


async def test_the_llama_backend_still_discovers_and_activates() -> None:
    """The guard must not have switched the hub path off as well."""
    provider = make(
        SELORA_LOCAL_BACKEND_LLAMA,
        routes={
            "/v1/models": {"data": [{"id": "selora"}]},
            "/lora-adapters": [{"id": 0, "path": "/m/selora-command.gguf"}],
        },
    )
    await provider._activate_lora_for_kind("chat_command")
    assert any("lora-adapters" in url for url in provider._fake_session.gets)
    assert provider._lora_slots == {"command": 0}


# ── the configured model reaches the provider ────────────────────────────────


def test_configured_model_is_kept() -> None:
    provider = make(SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED, model="cand:tag")
    assert provider._ollama_model_override == "cand:tag"


def test_configured_model_is_trimmed() -> None:
    provider = make(SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED, model="  cand:tag \n")
    assert provider._ollama_model_override == "cand:tag"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_unset_configured_model_is_none(value: str | None) -> None:
    provider = make(SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED, model=value)
    assert provider._ollama_model_override is None


# ── which model the backend addresses ────────────────────────────────────────


async def test_the_model_is_resolved_from_the_host() -> None:
    routes = {
        "/api/tags": {
            "models": [
                {"name": f"{FAM}:9.9.9-rc"},
                {"name": f"{FAM}:9.9.9"},
                {"name": f"{FAM}-command:9.9.9"},
                {"name": "llama4:latest"},
            ]
        }
    }
    provider = make(SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED, routes=routes)
    await provider._activate_lora_for_kind("chat_command")
    assert provider._unified_model == f"{FAM}:9.9.9"
    assert payload_for(provider)[0] == f"{FAM}:9.9.9"


async def test_the_configured_model_wins_over_the_host() -> None:
    """A benchmark run must be able to drive a candidate tag without
    republishing over the one everyone else is served."""
    routes = {"/api/tags": {"models": [{"name": f"{FAM}:9.9.9"}]}}
    provider = make(SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED, model="candidate:tag", routes=routes)
    await provider._activate_lora_for_kind("chat_command")
    assert provider._unified_model == "candidate:tag"
    assert provider._fake_session.gets == []


async def test_an_unreachable_host_falls_back_but_keeps_looking() -> None:
    """The bare family is what we send when nobody answered, not an
    answer. If it latched, a host that booted five seconds late would be
    served :latest for the rest of the process."""
    provider = make(SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED)  # no /api/tags route -> 404
    await provider._activate_lora_for_kind("chat_command")
    assert provider._unified_model == FAM
    assert provider._unified_model_settled is False
    provider._fake_session.routes["/api/tags"] = {"models": [{"name": f"{FAM}:9.9.9"}]}
    provider._discovery_retry_after = 0.0  # step past the backoff window
    await provider._activate_lora_for_kind("chat_command")
    assert provider._unified_model == f"{FAM}:9.9.9"
    assert provider._unified_model_settled is True


async def test_an_unreachable_host_is_not_re_probed_on_every_request() -> None:
    """Nothing settles while the host is down, so without a backoff each
    request re-runs /api/tags and waits out the timeout before the chat
    call can start. The user pays that on every single turn."""
    provider = make(SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED)  # no /api/tags route -> 404
    for _ in range(3):
        await provider._activate_lora_for_kind("chat_command")
    assert provider._fake_session.gets.count(f"{HOST}/api/tags") == 1
    assert provider._discovery_retry_after > 0.0


async def test_the_model_is_the_same_for_every_intent() -> None:
    """It is one self-routing model; asking for a per-intent name asks
    for a model that does not exist on this tier."""
    routes = {"/api/tags": {"models": [{"name": f"{FAM}:9.9.9"}]}}
    seen = set()
    for kind in ("chat_command", "chat_automation", "chat_answer", "chat_clarification"):
        provider = make(SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED, routes=routes)
        await provider._activate_lora_for_kind(kind)
        seen.add(payload_for(provider, kind)[0])
    assert seen == {f"{FAM}:9.9.9"}


def test_the_llama_backend_still_names_the_intent() -> None:
    assert payload_for(make(SELORA_LOCAL_BACKEND_LLAMA))[0] == "command"


# ── which system prompt each backend sends ───────────────────────────────────


def test_the_router_prompt_is_registered_for_loading() -> None:
    assert _SELORA_LOCAL_PROMPT_FILENAMES[_SELORA_LOCAL_UNIFIED_PROMPT_KEY].endswith(".txt")


def test_the_router_prompt_file_is_bundled_and_readable() -> None:
    loaded = SeloraLocalProvider._load_specialist_prompts()
    assert loaded.get(_SELORA_LOCAL_UNIFIED_PROMPT_KEY, "").strip()


async def test_the_ollama_backend_sends_the_router_prompt_for_every_intent() -> None:
    routes = {"/api/tags": {"models": [{"name": f"{FAM}:9.9.9"}]}}
    for kind in ("chat_command", "chat_automation", "chat_answer"):
        provider = make(SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED, routes=routes)
        await provider._activate_lora_for_kind(kind)
        assert payload_for(provider, kind)[1].startswith("ROUTER PROMPT")


def test_the_llama_backend_keeps_its_specialist_prompt() -> None:
    assert payload_for(make(SELORA_LOCAL_BACKEND_LLAMA))[1].startswith("COMMAND PROMPT")


def test_a_missing_prompt_still_degrades_to_the_callers_prompt() -> None:
    provider = make(SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED)
    assert payload_for(provider, prompts={})[1].startswith("CALLER FALLBACK")


# ── prompt shape vs runtime window ───────────────────────────────────────────


@pytest.mark.parametrize(
    "backend", [SELORA_LOCAL_BACKEND_LLAMA, SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED]
)
def test_the_low_context_prompt_shape_holds_on_both_runtimes(backend: str) -> None:
    """The flag is about the request shape the models were trained on, not
    about how much room a runtime was launched with. A runtime given a
    bigger window still gets the filtered request, because that is the one
    in the corpus."""
    assert make(backend).is_low_context is True


# ── the served context window ────────────────────────────────────────────────


async def test_the_served_window_is_read_from_the_ollama_daemon() -> None:
    """An Ollama daemon answers /v1/models with no `meta` block, so the
    llama-server probe leaves the window unknown forever and the entity
    cap silently falls back to its constants. /api/show is where the
    daemon reports it."""
    provider = make(
        SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED,
        routes={
            "/api/tags": {"models": [{"name": f"{FAM}:9.9.9"}]},
            "/api/show": {"parameters": "stop  <|im_end|>\nnum_ctx  8192\n"},
        },
    )
    await provider.async_refresh_capabilities()
    assert provider.context_window == 8192
    assert provider._fake_session.posts == [f"{HOST}/api/show"]
    assert not any("/v1/models" in url for url in provider._fake_session.gets)


async def test_the_window_probe_asks_about_the_resolved_model() -> None:
    """/api/show is per-model, so the tag has to be settled first — asking
    about the family when a real tag is on the host reads a different
    model's Modelfile."""
    provider = make(
        SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED,
        routes={"/api/tags": {"models": [{"name": f"{FAM}:9.9.9"}]}, "/api/show": {}},
    )
    await provider.async_refresh_capabilities()
    assert provider._unified_model == f"{FAM}:9.9.9"


async def test_a_model_with_no_num_ctx_leaves_the_window_unknown() -> None:
    """Without a Modelfile num_ctx the daemon's own default applies and
    /api/show never reports it. Unknown beats a guess: naming more room
    than exists is the one direction this must not be wrong in."""
    provider = make(
        SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED,
        routes={
            "/api/tags": {"models": [{"name": f"{FAM}:9.9.9"}]},
            "/api/show": {"parameters": "stop  <|im_end|>\n"},
        },
    )
    await provider.async_refresh_capabilities()
    assert provider.context_window is None


async def test_a_failed_window_probe_keeps_the_last_reading() -> None:
    provider = make(
        SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED,
        routes={"/api/tags": {"models": [{"name": f"{FAM}:9.9.9"}]}},  # no /api/show
    )
    provider._context_window = 4096
    await provider.async_refresh_capabilities()
    assert provider.context_window == 4096


async def test_the_llama_backend_still_reads_the_served_window() -> None:
    """The branch must not have taken the hub path with it."""
    provider = make(
        SELORA_LOCAL_BACKEND_LLAMA,
        routes={"/v1/models": {"data": [{"id": "selora", "meta": {"n_ctx": 2048}}]}},
    )
    await provider.async_refresh_capabilities()
    assert provider.context_window == 2048
    assert not provider._fake_session.posts


# ── pre-warm ─────────────────────────────────────────────────────────────────


def _entities(count: int) -> list[dict[str, str]]:
    return [
        {"entity_id": f"light.l{i}", "state": "off", "friendly_name": f"Light {i}"}
        for i in range(count)
    ]


def test_the_llama_backend_warms_every_specialist() -> None:
    """Each one has its own LoRA and its own trained prompt, so each has
    a prefix of its own to fill."""
    provider = make(SELORA_LOCAL_BACKEND_LLAMA)
    assert provider._prewarm_kinds(_entities(80)) == _SELORA_LOCAL_PREWARM_KINDS


@pytest.mark.parametrize("home_size", [2, 80])
def test_the_ollama_backend_warms_each_prefix_once(home_size: int) -> None:
    """One model behind one trained prompt, so three of the four kinds
    render the same body — warming each would re-send what the previous
    request just cached. Automation is the one that really differs: it
    carries the EXISTING AUTOMATIONS block and a tighter entity cap."""
    provider = make(SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED)
    kinds = provider._prewarm_kinds(_entities(home_size))
    assert len(kinds) < len(_SELORA_LOCAL_PREWARM_KINDS)
    assert "chat_automation" in kinds


def test_the_ollama_backend_drops_only_repeats() -> None:
    """Every kind still has its prefix filled by one of the survivors —
    dropping a kind whose body nothing else covers would hand its first
    real request the cold prefill this exists to avoid."""
    provider = make(SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED)
    entities = _entities(80)
    kept = provider._prewarm_kinds(entities)

    def body(kind: str) -> str:
        provider.set_call_kind(kind)
        provider.set_chat_context(
            user_message="warmup", entities=entities, existing_automations=[], history=[]
        )
        out = provider._build_training_user_content()
        provider.set_call_kind(None)
        return out

    assert {body(k) for k in _SELORA_LOCAL_PREWARM_KINDS} == {body(k) for k in kept}


def test_choosing_the_prefixes_leaves_no_call_kind_behind() -> None:
    """It sets the kind to render each candidate; leaving one set would
    route the next real request to the wrong specialist."""
    provider = make(SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED)
    provider._prewarm_kinds(_entities(80))
    assert provider._call_kind.get() is None


# ── pre-warm must not read llama state on the Ollama runtime ─────────────────


async def test_prewarm_warms_every_ollama_prefix() -> None:
    """The stop-early guard asks "has the host answered discovery?", and on
    the llama path reads ``_lora_slots`` to decide. On this runtime that
    stays ``None`` BY DESIGN — there is no slot endpoint — so reading it
    here ended the loop after the first prefix and left the rest cold,
    defeating the deduplication ``_prewarm_kinds`` exists to do.
    """
    # An invented tag, per the guard in test_ollama_unified_tags: a shipped
    # version written into a fixture is a version written down.
    provider = make(SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED, model="candidate:tag")
    warmed: list[str | None] = []

    async def _fake_send(*_args: Any, **_kwargs: Any) -> tuple[str, str | None]:
        warmed.append(provider._call_kind.get())
        return "ok", None

    provider.send_request = _fake_send  # type: ignore[method-assign]
    entities = _entities(80)
    expected = provider._prewarm_kinds(entities)
    assert len(expected) > 1, "fixture must exercise more than one prefix"

    await provider.prewarm(entities)

    assert provider._lora_slots is None, "the Ollama path must not discover slots"
    assert len(warmed) == len(expected)


async def test_prewarm_still_stops_early_when_the_ollama_host_is_silent() -> None:
    """Scoping the guard must not remove it: with no configured model and a
    host that never resolves one, the remaining prefixes would each re-probe
    a host already known to be down."""
    provider = make(SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED)
    warmed: list[str | None] = []

    async def _fake_send(*_args: Any, **_kwargs: Any) -> tuple[str, str | None]:
        warmed.append(provider._call_kind.get())
        return "ok", None

    provider.send_request = _fake_send  # type: ignore[method-assign]
    entities = _entities(80)
    assert len(provider._prewarm_kinds(entities)) > 1

    await provider.prewarm(entities)

    assert not provider._unified_model_settled
    assert len(warmed) == 1
