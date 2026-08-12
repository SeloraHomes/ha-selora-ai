"""Tests for the prompt entity-token budget.

Three layers:

* the pure arithmetic in ``llm_client.context_budget``, tested directly;
* the generic Ollama path, whose entity block must be sized against the
  rest of the payload rather than in isolation (GitHub #3 — a ~1700-entity
  install produced a 61,488-token request against a 40,960 window, from
  components that were each individually capped but never counted
  together);
* the Selora AI Local fallback, which must still produce exactly the
  hand-tuned 60 / 25 lines while the hub's context window is unknown.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from custom_components.selora_ai.const import CHAT_ATTACHMENT_MAX_COUNT
from custom_components.selora_ai.llm_client import LLMClient
from custom_components.selora_ai.llm_client.context_budget import (
    ASSUMED_CONTEXT_WINDOW,
    BOUNDED_LOCAL_MIN_ENTITY_LINES,
    CLOUD_ENTITY_LINE_TOKENS,
    LOCAL_ENTITY_LINE_TOKENS,
    MIN_ENTITY_LINES,
    RESPONSE_HEADROOM_TOKENS,
    TOOL_RESPONSE_HEADROOM_TOKENS,
    attachment_tokens,
    entity_budget,
    estimate_entity_line_tokens,
    estimate_tokens,
    fit_lines_to_tokens,
    response_headroom,
    trim_entities_to_budget,
)
from custom_components.selora_ai.llm_client.intent import _CLOUD_MAX_ENTITIES
from custom_components.selora_ai.llm_client.sanitize import _format_entity_line
from custom_components.selora_ai.providers import create_provider
from custom_components.selora_ai.providers.selora_local import (
    _SELORA_LOCAL_MAX_ENTITY_LINES,
    _SELORA_LOCAL_MAX_ENTITY_LINES_AUTOMATION,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.selora_ai.providers.selora_local import SeloraLocalProvider
    from custom_components.selora_ai.types import EntitySnapshot


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _entity(i: int, domain: str = "light") -> EntitySnapshot:
    """One entity of a large home, with the metadata a real install carries."""
    return {
        "entity_id": f"{domain}.device_{i}",
        "state": "on",
        "attributes": {"friendly_name": f"Device {i}", "brightness": 180},
        "area_name": "Living Room",
        "platform": "hue",
        "manufacturer": "Signify Netherlands B.V.",
        "model": "Hue ambiance spot LTG002",
    }


def _big_home(count: int = 1700) -> list[EntitySnapshot]:
    """The reporter's install shape: ~1700 entities across live domains."""
    domains = ("light", "switch", "sensor", "binary_sensor", "media_player")
    return [_entity(i, domains[i % len(domains)]) for i in range(count)]


# ── Pure module: estimate_tokens ─────────────────────────────────────────────


def test_estimate_tokens_empty_is_zero() -> None:
    assert estimate_tokens("") == 0


def test_estimate_tokens_is_conservative() -> None:
    """The estimate must OVERSTATE tokens relative to the ~4 chars/token a
    BPE vocabulary averages on prose — understating would let the caller
    overrun the window, which is the failure this module exists to stop."""
    text = "x" * 4000
    assert estimate_tokens(text) > len(text) / 4.0


def test_estimate_tokens_monotonic() -> None:
    assert estimate_tokens("a" * 100) < estimate_tokens("a" * 200)


def test_estimate_entity_line_tokens_charges_for_the_newline() -> None:
    line = "  - entity_id=light.kitchen; state=on"
    assert estimate_entity_line_tokens(line) == estimate_tokens(line) + 1


# ── Pure module: entity_budget ───────────────────────────────────────────────


def test_entity_budget_unknown_window_uses_the_assumed_small_window() -> None:
    """A ``None`` window must behave exactly like the assumed small one —
    this is the design decision that keeps a failed probe from producing an
    unbounded prompt."""
    assert entity_budget(None, reserved=1000) == entity_budget(
        ASSUMED_CONTEXT_WINDOW, reserved=1000
    )


def test_entity_budget_scales_with_the_window() -> None:
    small = entity_budget(8192, reserved=2000, tokens_per_line=CLOUD_ENTITY_LINE_TOKENS)
    large = entity_budget(131072, reserved=2000, tokens_per_line=CLOUD_ENTITY_LINE_TOKENS)
    assert large > small


def test_entity_budget_arithmetic_is_exact() -> None:
    """(window - reserved) // tokens_per_line."""
    assert entity_budget(10_000, reserved=2_000, tokens_per_line=50) == 160


def test_entity_budget_floors_at_minimum_when_nothing_fits() -> None:
    """An oversized system prompt must not produce an empty entity block —
    a model handed no entities invents entity_ids instead of saying it
    cannot see any."""
    assert entity_budget(4096, reserved=99_999) == MIN_ENTITY_LINES
    assert entity_budget(4096, reserved=99_999, minimum=60) == 60


def test_entity_budget_survives_nonsense_input() -> None:
    """Bad numbers from a backend probe must not take the prompt path down."""
    assert entity_budget(0, reserved=10) == MIN_ENTITY_LINES
    assert entity_budget(-5, reserved=10) == MIN_ENTITY_LINES
    assert entity_budget(8192, reserved=-10, tokens_per_line=0) == MIN_ENTITY_LINES


def test_entity_budget_never_returns_below_minimum() -> None:
    for reserved in range(0, 20_000, 977):
        assert entity_budget(4096, reserved=reserved) >= MIN_ENTITY_LINES


# ── Pure module: fit_lines_to_tokens ─────────────────────────────────────────


def test_fit_lines_measures_the_actual_lines() -> None:
    """``entity_budget`` guesses from a mean before anything is rendered;
    this bounds what actually came out."""
    lines = [_format_entity_line(e) for e in _big_home(500)]
    budget = 5_000
    kept = fit_lines_to_tokens(lines, budget)
    assert kept == lines[: len(kept)], "must keep a rank-ordered prefix"
    assert sum(estimate_entity_line_tokens(ln) for ln in kept) <= budget


def test_fit_lines_keeps_the_minimum_even_when_nothing_fits() -> None:
    lines = [_format_entity_line(e) for e in _big_home(100)]
    assert len(fit_lines_to_tokens(lines, 0, minimum=60)) == 60
    assert len(fit_lines_to_tokens(lines, 1, minimum=60)) == 60
    assert fit_lines_to_tokens(lines, 0) == []


def test_fit_lines_is_a_noop_when_the_budget_is_ample() -> None:
    lines = [_format_entity_line(e) for e in _big_home(20)]
    assert fit_lines_to_tokens(lines, 1_000_000) == lines


def test_rendered_entity_line_cost_can_exceed_the_assumed_mean() -> None:
    """Why the measured pass exists: a fully-attributed entity (area,
    platform, manufacturer, model, attributes) renders above the mean the
    pre-render budget assumes, so selecting by the mean alone overshoots."""
    lines = [_format_entity_line(e) for e in _big_home(200)]
    mean = sum(estimate_entity_line_tokens(ln) for ln in lines) / len(lines)
    assert mean > CLOUD_ENTITY_LINE_TOKENS


# ── Pure module: trim_entities_to_budget ─────────────────────────────────────


def test_trim_respects_budget_and_preserves_rank_order() -> None:
    entities = _big_home(100)
    trimmed = trim_entities_to_budget(entities, 10)
    assert len(trimmed) == 10
    assert [e["entity_id"] for e in trimmed] == [e["entity_id"] for e in entities[:10]]


def test_trim_budget_larger_than_list_keeps_everything() -> None:
    entities = _big_home(5)
    assert len(trim_entities_to_budget(entities, 500)) == 5


def test_trim_zero_or_negative_budget_yields_nothing() -> None:
    entities = _big_home(5)
    assert trim_entities_to_budget(entities, 0) == []
    assert trim_entities_to_budget(entities, -1) == []


def test_trim_does_not_mutate_or_alias_the_input() -> None:
    entities = _big_home(5)
    trimmed = trim_entities_to_budget(entities, 5)
    assert trimmed is not entities
    assert len(entities) == 5


# ── Ollama path: a 1700-entity home is always bounded ────────────────────────


def _ollama_client(hass: HomeAssistant) -> LLMClient:
    return LLMClient(hass, create_provider("ollama", hass))


def _cloud_client(hass: HomeAssistant) -> LLMClient:
    return LLMClient(hass, create_provider("anthropic", hass, api_key="test-key"))


def test_ollama_provider_is_not_low_context(hass: HomeAssistant) -> None:
    """The precondition for the bug: Ollama never overrides is_low_context,
    so it takes the full-context builder. If this ever changes, the bound
    below is testing the wrong path."""
    provider = create_provider("ollama", hass)
    assert provider.is_low_context is False
    assert provider.is_local is True


def test_ollama_entity_cap_is_bounded_and_far_below_the_cloud_cap(hass: HomeAssistant) -> None:
    client = _ollama_client(hass)
    cap = client._entity_line_cap("system prompt " * 2000)
    assert cap == BOUNDED_LOCAL_MIN_ENTITY_LINES
    assert cap < _CLOUD_MAX_ENTITIES


def test_entity_cap_accounts_for_the_whole_payload(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The budget must shrink as the OTHER parts of the request grow. This
    is the actual defect: each component was capped, none were counted
    together. Uses a mid-size known window so the result is the formula's
    output rather than the floor or the cloud clamp."""
    client = _ollama_client(hass)
    monkeypatch.setattr(type(client._provider), "context_window", 16_384, raising=False)

    bare = client._entity_line_cap("sys")
    with_ctx = client._entity_line_cap("sys", other_context="x" * 20_000)
    with_tools = client._entity_line_cap("sys", tool_tokens=4_000)
    with_prompt = client._entity_line_cap("sys" * 5_000)

    assert BOUNDED_LOCAL_MIN_ENTITY_LINES < bare < _CLOUD_MAX_ENTITIES
    assert with_ctx < bare, "context sections must consume the budget"
    assert with_tools < bare, "tool schemas must consume the budget"
    assert with_prompt < bare, "the system prompt must consume the budget"


def test_entity_cap_grows_with_the_window(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backend that reports a bigger window earns a bigger entity block,
    up to the cloud cap — the budget is not a fixed tightening."""
    client = _ollama_client(hass)
    caps = []
    for window in (8_192, 16_384, 40_960):
        monkeypatch.setattr(type(client._provider), "context_window", window, raising=False)
        caps.append(client._entity_line_cap("sys", tool_tokens=4_000))
    assert caps == sorted(caps)
    assert caps[0] < caps[-1]


def test_tool_schema_estimate_is_material(hass: HomeAssistant) -> None:
    """Tool definitions are sent beside the messages, so nothing that
    measures prompt strings sees them. They are big enough to matter."""
    client = _ollama_client(hass)
    assert client._estimate_tool_tokens() > 1000


def test_history_budget_prefers_the_real_window(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider that has not reported a window falls back to the shipped
    per-provider constant; once a probe discovers one, it takes over."""
    client = _ollama_client(hass)
    assert client._history_token_budget() == 28_000
    monkeypatch.setattr(type(client._provider), "context_window", 40_960, raising=False)
    assert 28_000 < client._history_token_budget() < 40_960


def test_history_budget_reserves_tool_schemas(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tools come off the history ceiling too, not just the entity budget.

    Reserving in one place and not the other only moves the overflow:
    whatever the entity block declines to spend, history takes. A
    tool-bearing request also declares the larger completion allowance,
    so the ceiling drops by the schemas AND by the extra headroom.
    """
    client = _ollama_client(hass)
    monkeypatch.setattr(type(client._provider), "context_window", 40_960, raising=False)
    assert client._history_token_budget() == 40_960 - RESPONSE_HEADROOM_TOKENS
    assert client._history_token_budget(tool_tokens=4_000) == (
        40_960 - TOOL_RESPONSE_HEADROOM_TOKENS - 4_000
    )


def test_headroom_matches_the_declared_max_tokens() -> None:
    """A backend validates prompt + DECLARED completion against its
    window, so the reservation has to match what the request declares.
    The tool loop declares 4096 (``base.raw_request`` /
    ``raw_request_stream``); the plain path declares 1024."""
    assert response_headroom(tool_tokens=0) == RESPONSE_HEADROOM_TOKENS
    assert response_headroom(tool_tokens=1) == TOOL_RESPONSE_HEADROOM_TOKENS
    assert TOOL_RESPONSE_HEADROOM_TOKENS == 4096


def test_entity_cap_shrinks_when_tools_raise_the_completion_allowance(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bigger completion allowance must reach the entity budget too,
    not just the history ceiling."""
    client = _ollama_client(hass)
    monkeypatch.setattr(type(client._provider), "context_window", 16_384, raising=False)
    assert client._entity_line_cap("sys", tool_tokens=1) < client._entity_line_cap("sys")


def test_entity_reservation_counts_the_current_turn(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long user message is part of the prompt the entity block sits in.
    Leaving it out lets the block spend that space twice, and nothing
    downstream recovers — the history trimmer only drops history."""
    client = _ollama_client(hass)
    monkeypatch.setattr(type(client._provider), "context_window", 16_384, raising=False)

    def _cap(message: str) -> int:
        messages = client._build_chat_messages(
            message, _big_home(1700), None, None, system_prompt="sys"
        )
        body = messages[-1]["content"]
        return len([ln for ln in body.splitlines() if ln.startswith("  - entity_id=")])

    # A pasted log / YAML blob, the realistic way a turn gets long.
    assert _cap("turn on the kitchen light " + "x" * 20_000) < _cap("turn on the kitchen light")


def test_history_budget_does_not_double_count_tools_on_the_fallback(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_PROVIDER_TOKEN_BUDGETS`` already holds room back for tool
    definitions, so an unknown window must not subtract them a second
    time — that would tighten cloud history, which this change does not
    touch."""
    client = _cloud_client(hass)
    assert client._provider.context_window is None
    assert client._history_token_budget(tool_tokens=4_000) == client._history_token_budget()


def test_ollama_chat_payload_fits_the_reported_window(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reporter's configuration: ~1700 entities against a 40,960-token
    window produced a 61,488-token request. With the window known, the whole
    payload — system prompt, tool schemas and context together — must fit
    inside it."""
    client = _ollama_client(hass)
    monkeypatch.setattr(type(client._provider), "context_window", 40_960, raising=False)

    system_prompt = "You are Selora AI. " * 500
    tool_tokens = client._estimate_tool_tokens()
    messages = client._build_chat_messages(
        "turn on the kitchen light",
        _big_home(1700),
        None,
        None,
        system_prompt=system_prompt,
        tool_tokens=tool_tokens,
    )
    body = messages[-1]["content"]
    entity_lines = [ln for ln in body.splitlines() if ln.startswith("  - entity_id=")]
    assert entity_lines, "the model must still see some entities"

    payload = estimate_tokens(system_prompt) + estimate_tokens(body) + tool_tokens
    assert payload + TOOL_RESPONSE_HEADROOM_TOKENS <= 40_960, (
        f"prompt ~{payload} + declared completion must fit the 40,960 window"
    )


def _tool_bearing_payload(
    client: LLMClient,
    *,
    window: int,
    history: list[dict[str, str]] | None,
    system_prompt: str,
) -> tuple[int, int]:
    """Build a full tool-bearing turn; return (prompt tokens, message count).

    Counts EVERY message of the request plus the system prompt and the
    tool schemas — the whole thing the backend measures, not just the
    current turn.
    """
    tool_tokens = client._estimate_tool_tokens()
    messages = client._build_chat_messages(
        "turn on the kitchen light",
        _big_home(1700),
        None,
        history,
        system_prompt=system_prompt,
        tool_tokens=tool_tokens,
    )
    payload = estimate_tokens(system_prompt) + tool_tokens
    for message in messages:
        content = message["content"]
        payload += estimate_tokens(content if isinstance(content, str) else str(content))
    return payload, len(messages)


def test_ollama_payload_fits_the_window_with_a_long_history(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reporter's window, but mid-conversation — the case the entity
    budget alone does not cover.

    History is the *other* elastic component. Sizing the entity block
    against the tool schemas frees space the history trimmer will then
    spend, landing the request at ``window - headroom + tool_tokens``.
    """
    client = _ollama_client(hass)
    monkeypatch.setattr(type(client._provider), "context_window", 40_960, raising=False)
    # Far more history than can fit, so the trimmer is the thing under
    # test rather than the size of the session.
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " + "blah " * 400}
        for i in range(60)
    ]
    payload, _ = _tool_bearing_payload(
        client, window=40_960, history=history, system_prompt="You are Selora AI. " * 500
    )
    assert payload + TOOL_RESPONSE_HEADROOM_TOKENS <= 40_960, (
        f"prompt ~{payload} + declared completion must fit the 40,960 window"
    )


def test_history_survives_when_the_window_has_room_for_it(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bound must not be satisfied by starving history to nothing.

    At 40,960 the entity block legitimately takes the space (entities are
    sized first, by design); given a window with room for both, history
    has to come back AND the whole request still has to fit.
    """
    client = _ollama_client(hass)
    monkeypatch.setattr(type(client._provider), "context_window", 65_536, raising=False)
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " + "blah " * 400}
        for i in range(60)
    ]
    payload, message_count = _tool_bearing_payload(
        client, window=65_536, history=history, system_prompt="You are Selora AI. " * 500
    )
    assert message_count > 1, "history must survive when the window affords it"
    assert payload + TOOL_RESPONSE_HEADROOM_TOKENS <= 65_536


def _attachment(n: int = 1) -> list[dict[str, str]]:
    """Attachments as the chat handlers hand them over (base64 payloads)."""
    return [{"mime_type": "image/png", "data": "iVBORw0KGgo=" * 100} for _ in range(n)]


def test_attachment_cost_shrinks_the_entity_block(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Images ride beside the text and no prompt string contains them, so
    nothing sees their cost unless it is reserved explicitly.

    Uses a window where the token formula binds rather than the
    ``_CLOUD_MAX_ENTITIES`` clamp — at 40,960 a single image still leaves
    room for the full 500 lines, so the clamp would hide the effect.
    """
    client = _ollama_client(hass)
    monkeypatch.setattr(type(client._provider), "context_window", 24_576, raising=False)

    def _entity_count(attachments: list[dict[str, str]] | None) -> int:
        messages = client._build_chat_messages(
            "what is in this picture?",
            _big_home(1700),
            None,
            None,
            system_prompt="You are Selora AI. " * 500,
            attachments=attachments,
        )
        body = messages[-1]["content"]
        text = body if isinstance(body, str) else body[-1]["text"]
        return len([ln for ln in text.splitlines() if ln.startswith("  - entity_id=")])

    assert _entity_count(_attachment(4)) < _entity_count(_attachment(1)) < _entity_count(None)


def test_attachment_payload_fits_the_window(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worst realistic vision turn: max attachments, a big home and a
    long session, against the reporter's window."""
    client = _ollama_client(hass)
    monkeypatch.setattr(type(client._provider), "context_window", 40_960, raising=False)
    system_prompt = "You are Selora AI. " * 500
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " + "blah " * 400}
        for i in range(60)
    ]
    attachments = _attachment(CHAT_ATTACHMENT_MAX_COUNT)
    messages = client._build_chat_messages(
        "what is in these pictures?",
        _big_home(1700),
        None,
        history,
        system_prompt=system_prompt,
        attachments=attachments,
    )

    payload = estimate_tokens(system_prompt) + attachment_tokens(len(attachments))
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            payload += estimate_tokens(content)
        else:
            payload += sum(estimate_tokens(b.get("text", "")) for b in content)
    assert payload + RESPONSE_HEADROOM_TOKENS <= 40_960, (
        f"prompt ~{payload} + declared completion must fit the 40,960 window"
    )


def test_history_reserves_the_condensation_notice(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The notice is prepended AFTER the budget was spent, so it has to be
    reserved before history is selected or the request lands over.

    Swept across message sizes rather than pinned to one: the overflow
    only shows when the kept turns happen to pack tight against the
    budget, leaving less slack than the notice costs. A single size
    almost certainly lands in the slack and proves nothing.
    """
    client = _ollama_client(hass)
    monkeypatch.setattr(type(client._provider), "context_window", 10_000, raising=False)
    budget = client._history_token_budget()
    system_prompt, context_prompt = "sys", "ctx"
    saw_condensation = False

    for length in range(300, 460):
        messages = [{"role": "user", "content": "x" * length} for _ in range(200)]
        kept = client._trim_history_to_budget(messages, system_prompt, context_prompt)
        assert len(kept) < len(messages), "the trimmer must actually drop something here"
        saw_condensation |= kept[0]["content"].startswith("[Earlier conversation:")
        total = estimate_tokens(system_prompt) + estimate_tokens(context_prompt)
        total += sum(estimate_tokens(m["content"]) for m in kept)
        assert total <= budget, (
            f"at length={length}, history plus the notice ({total}) must fit ({budget})"
        )

    assert saw_condensation, "the notice must actually be emitted, or this tests nothing"


def test_history_keeps_everything_when_it_fits(hass: HomeAssistant) -> None:
    """The notice reservation must not cost a message when nothing is
    dropped — no drop means no notice to pay for."""
    client = _ollama_client(hass)
    messages = [{"role": "user", "content": "short"} for _ in range(4)]
    assert len(client._trim_history_to_budget(messages, "sys", "ctx")) == len(messages)


def test_execute_command_reserves_the_command_text(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`command` is unbounded user text in the same prompt as the entity
    block, exactly like `user_message` on the chat path."""
    client = _ollama_client(hass)
    monkeypatch.setattr(type(client._provider), "context_window", 16_384, raising=False)
    short = client._entity_line_cap("sys", other_context="COMMAND: all lights off\n")
    long = client._entity_line_cap("sys", other_context="COMMAND: " + "x" * 20_000)
    assert long < short


async def test_execute_command_prompt_fits_the_window(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through execute_command's own prompt builder: a long
    category command against a big home must still fit."""
    client = _ollama_client(hass)
    monkeypatch.setattr(type(client._provider), "context_window", 16_384, raising=False)
    captured: dict[str, str] = {}

    async def _fake_send(
        *, system: str, messages: list[dict[str, str]], **_: object
    ) -> tuple[str, None]:
        captured["system"] = system
        captured["user"] = messages[0]["content"]
        return '{"calls": [], "response": "ok"}', None

    monkeypatch.setattr(client._provider, "send_request", _fake_send)
    command = "turn off all lights " + "x" * 8_000
    await client.execute_command(command, _big_home(1700))

    payload = estimate_tokens(captured["system"]) + estimate_tokens(captured["user"])
    assert payload + RESPONSE_HEADROOM_TOKENS <= 16_384, (
        f"prompt ~{payload} + declared completion must fit the 16,384 window"
    )


def test_ollama_entity_block_never_tracks_the_size_of_the_home(hass: HomeAssistant) -> None:
    """With no known window the block is still bounded — it does not grow
    with the entity count, which is what turned a large install into an
    oversized request."""
    client = _ollama_client(hass)
    messages = client._build_chat_messages(
        "turn on the kitchen light",
        _big_home(1700),
        None,
        None,
        system_prompt="You are Selora AI. " * 500,
        tool_tokens=client._estimate_tool_tokens(),
    )
    body = messages[-1]["content"]
    entity_lines = [ln for ln in body.splitlines() if ln.startswith("  - entity_id=")]
    assert 0 < len(entity_lines) <= BOUNDED_LOCAL_MIN_ENTITY_LINES


def test_entity_floor_wins_when_the_fixed_costs_alone_overflow(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deliberate trade-off: when the system prompt and tool schemas alone
    fill the window, no entity budget can rescue the request — but the block
    still floors at a usable size rather than emptying, because a model
    handed no entities invents entity_ids instead of saying it cannot see
    any. Shrinking the fixed costs is a different piece of work."""
    client = _ollama_client(hass)
    monkeypatch.setattr(type(client._provider), "context_window", 2048, raising=False)
    assert client._entity_line_cap("sys" * 10_000, tool_tokens=4_000) == (
        BOUNDED_LOCAL_MIN_ENTITY_LINES
    )


def test_ollama_chat_prompt_does_not_grow_with_the_home(hass: HomeAssistant) -> None:
    """500 entities and 1700 entities must produce the same block size —
    the bound is on the window, not on the input."""
    client = _ollama_client(hass)

    def _block_size(count: int) -> int:
        messages = client._build_chat_messages(
            "turn on the kitchen light",
            _big_home(count),
            None,
            None,
            system_prompt="You are Selora AI. " * 500,
        )
        body = messages[-1]["content"]
        return len([ln for ln in body.splitlines() if ln.startswith("  - entity_id=")])

    assert _block_size(500) == _block_size(1700)


async def test_ollama_execute_command_payload_fits_the_window(hass: HomeAssistant) -> None:
    """``execute_command`` had no entity cap of its own at all, so its block
    tracked the size of the home."""
    client = _ollama_client(hass)
    captured: dict[str, str] = {}

    async def _capture(*, system: str, messages: list[dict[str, str]]) -> tuple[str, None]:
        captured["system"] = system
        captured["prompt"] = messages[0]["content"]
        return '{"calls": [], "response": "ok"}', None

    client._provider.send_request = AsyncMock(side_effect=_capture)
    await client.execute_command("turn on the kitchen light", _big_home(1700))

    entity_lines = [ln for ln in captured["prompt"].splitlines() if ln.startswith("  - entity_id=")]
    assert entity_lines
    assert len(entity_lines) < 1700, "the block must not track the size of the home"
    payload = estimate_tokens(captured["system"]) + estimate_tokens(captured["prompt"])
    assert payload < ASSUMED_CONTEXT_WINDOW
    # The declared count must match what is actually listed.
    assert f"AVAILABLE ENTITIES ({len(entity_lines)}):" in captured["prompt"]


async def test_execute_command_bounded_for_cloud_providers_too(hass: HomeAssistant) -> None:
    """Cloud keeps its own long-standing budget, but ``execute_command``
    had no cap at all — 1700 entities must not all be serialized."""
    client = _cloud_client(hass)
    captured: dict[str, str] = {}

    async def _capture(*, system: str, messages: list[dict[str, str]]) -> tuple[str, None]:
        captured["prompt"] = messages[0]["content"]
        return '{"calls": [], "response": "ok"}', None

    client._provider.send_request = AsyncMock(side_effect=_capture)
    await client.execute_command("turn on the kitchen light", _big_home(1700))

    entity_lines = [ln for ln in captured["prompt"].splitlines() if ln.startswith("  - entity_id=")]
    assert len(entity_lines) <= _CLOUD_MAX_ENTITIES


# ── Guard: the cloud budget is untouched ─────────────────────────────────────


def test_cloud_provider_keeps_the_full_cloud_cap(hass: HomeAssistant) -> None:
    """``_CLOUD_MAX_ENTITIES`` is load-bearing for aggregate queries with no
    keyword hit. Bounding the local path must not tighten it."""
    client = _cloud_client(hass)
    assert client._entity_line_cap("You are Selora AI. " * 3000) == _CLOUD_MAX_ENTITIES


def test_cloud_chat_prompt_still_carries_the_full_cap(hass: HomeAssistant) -> None:
    client = _cloud_client(hass)
    messages = client._build_chat_messages(
        "what is the status of everything?",
        _big_home(1700),
        None,
        None,
        system_prompt="You are Selora AI. " * 500,
    )
    body = messages[-1]["content"]
    entity_lines = [ln for ln in body.splitlines() if ln.startswith("  - entity_id=")]
    assert len(entity_lines) == _CLOUD_MAX_ENTITIES


# ── Selora AI Local: the 60 / 25 fallback ────────────────────────────────────


def _local_provider(hass: HomeAssistant) -> SeloraLocalProvider:
    return create_provider("selora_local", hass)  # type: ignore[return-value]


def test_selora_local_falls_back_to_sixty_when_window_unknown(hass: HomeAssistant) -> None:
    """A hub that has not been probed yet, or whose probe failed, keeps the
    hand-tuned constant — an unknown window is never read as a large one."""
    provider = _local_provider(hass)
    assert getattr(provider, "context_window", None) is None
    provider.set_call_kind("chat_command")
    assert provider._entity_line_cap() == 60
    assert provider._entity_line_cap() == _SELORA_LOCAL_MAX_ENTITY_LINES


def test_selora_local_falls_back_to_twentyfive_for_automation(hass: HomeAssistant) -> None:
    provider = _local_provider(hass)
    provider.set_call_kind("chat_automation")
    assert provider._entity_line_cap() == 25
    assert provider._entity_line_cap() == _SELORA_LOCAL_MAX_ENTITY_LINES_AUTOMATION


def test_selora_local_block_emits_exactly_sixty_lines(hass: HomeAssistant) -> None:
    """End to end through the renderer, not just the cap helper."""
    provider = _local_provider(hass)
    provider.set_call_kind("chat_command")
    block = provider._format_entities_block(_big_home(1700))
    lines = [ln for ln in block.splitlines() if ln.startswith("- entity_id=")]
    assert len(lines) == 60
    assert "- ... (1640 more entities not listed)" in block


def test_selora_local_block_emits_exactly_twentyfive_lines_for_automation(
    hass: HomeAssistant,
) -> None:
    provider = _local_provider(hass)
    provider.set_call_kind("chat_automation")
    block = provider._format_entities_block(_big_home(1700))
    lines = [ln for ln in block.splitlines() if ln.startswith("- entity_id=")]
    assert len(lines) == 25
    assert "- ... (1675 more entities not listed)" in block


def test_selora_local_derived_cap_only_tightens(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hub serving a small window gets fewer lines — but a hub serving a
    huge one never gets MORE than the constant the LoRAs were trained
    against."""
    provider = _local_provider(hass)
    provider.set_call_kind("chat_command")

    monkeypatch.setattr(type(provider), "context_window", 131072, raising=False)
    assert provider._entity_line_cap() == _SELORA_LOCAL_MAX_ENTITY_LINES

    monkeypatch.setattr(type(provider), "context_window", 2048, raising=False)
    assert provider._entity_line_cap() < _SELORA_LOCAL_MAX_ENTITY_LINES


def test_selora_local_line_cost_matches_its_render_format(hass: HomeAssistant) -> None:
    """The budget's assumed per-line cost must track the format the
    provider actually emits, or the arithmetic is decorative."""
    provider = _local_provider(hass)
    provider.set_call_kind("chat_command")
    block = provider._format_entities_block(_big_home(60))
    lines = [ln for ln in block.splitlines() if ln.startswith("- entity_id=")]
    measured = max(estimate_entity_line_tokens(ln) for ln in lines)
    assert measured <= LOCAL_ENTITY_LINE_TOKENS * 1.5
