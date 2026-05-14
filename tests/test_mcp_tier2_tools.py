"""Tests for Tier 2/3 tools: execute_command, activate_scene, search_entities,
get_entity_history, eval_template, and the large_context_only gating."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

from custom_components.selora_ai.mcp_server import (
    _tool_eval_template,
    _tool_execute_command,
    _tool_get_entity_history,
    _tool_search_entities,
)
from custom_components.selora_ai.tool_registry import CHAT_TOOLS


@pytest.fixture
async def setup_world(hass: HomeAssistant):
    """A small home: lights in two areas, a switch, and a scene entity."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)

    entry = MockConfigEntry(domain="test", entry_id="mock_entry_tier2")
    entry.add_to_hass(hass)

    kitchen = area_reg.async_create("Kitchen")
    bedroom = area_reg.async_create("Master Bedroom")

    # Kitchen island light (target for search query "kitchen island")
    ent_reg.async_get_or_create(
        "light",
        "test",
        "kitchen_island_uid",
        suggested_object_id="kitchen_island",
    )
    ent_reg.async_update_entity("light.kitchen_island", area_id=kitchen.id)
    hass.states.async_set(
        "light.kitchen_island",
        "off",
        {"friendly_name": "Kitchen Island Light"},
    )

    # Bedroom lamp (alias match target)
    ent_reg.async_get_or_create(
        "light",
        "test",
        "bedroom_lamp_uid",
        suggested_object_id="bedroom_lamp",
    )
    ent_reg.async_update_entity(
        "light.bedroom_lamp",
        area_id=bedroom.id,
        aliases={"reading lamp"},
    )
    hass.states.async_set(
        "light.bedroom_lamp",
        "on",
        {"friendly_name": "Bedroom Lamp"},
    )

    # A switch (different domain)
    ent_reg.async_get_or_create(
        "switch",
        "test",
        "coffee_uid",
        suggested_object_id="coffee_maker",
    )
    hass.states.async_set("switch.coffee_maker", "off", {"friendly_name": "Coffee Maker"})

    # Scene
    hass.states.async_set("scene.movie_night", "scening")

    return {"kitchen_id": kitchen.id, "bedroom_id": bedroom.id}


# ── execute_command ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_command_runs_service(
    hass: HomeAssistant, setup_world
) -> None:
    """execute_command invokes hass.services and returns post-state."""
    calls: list[dict] = []

    async def _capture(call):
        calls.append(
            {
                "domain": call.domain,
                "service": call.service,
                "data": dict(call.data),
            }
        )
        # Simulate the side effect
        hass.states.async_set("light.kitchen_island", "on")

    hass.services.async_register("light", "turn_on", _capture)

    result = await _tool_execute_command(
        hass,
        {
            "service": "light.turn_on",
            "entity_id": "light.kitchen_island",
            "data": {"brightness_pct": 60},
        },
    )

    assert result["executed"] is True
    assert result["service"] == "light.turn_on"
    assert result["entity_ids"] == ["light.kitchen_island"]
    assert result["states"][0]["state"] == "on"

    assert len(calls) == 1
    assert calls[0]["domain"] == "light"
    assert calls[0]["service"] == "turn_on"
    assert calls[0]["data"]["entity_id"] == ["light.kitchen_island"]
    assert calls[0]["data"]["brightness_pct"] == 60


@pytest.mark.asyncio
async def test_execute_command_rejects_invalid_call(
    hass: HomeAssistant, setup_world
) -> None:
    """Unknown verb is rejected before any service is called."""
    result = await _tool_execute_command(
        hass,
        {"service": "light.explode", "entity_id": "light.kitchen_island"},
    )
    assert result["valid"] is False
    assert any("not a valid light service" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_execute_command_rejects_unknown_entity(
    hass: HomeAssistant, setup_world
) -> None:
    """Unknown entity_id is rejected (caught by validate_command_action)."""
    result = await _tool_execute_command(
        hass,
        {"service": "light.turn_on", "entity_id": "light.ghost"},
    )
    assert result["valid"] is False
    assert any("not known to Home Assistant" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_execute_command_rejects_domain_outside_allowlist(
    hass: HomeAssistant, setup_world
) -> None:
    """Lock domain is not in the safe-command allowlist."""
    result = await _tool_execute_command(
        hass,
        {"service": "lock.unlock", "entity_id": "lock.front"},
    )
    assert result["valid"] is False
    assert any("allowlist" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_execute_command_service_failure_returns_error(
    hass: HomeAssistant, setup_world
) -> None:
    """A raised service exception is surfaced as executed:false."""

    async def _boom(call):
        raise RuntimeError("device offline")

    hass.services.async_register("light", "turn_on", _boom)

    result = await _tool_execute_command(
        hass,
        {"service": "light.turn_on", "entity_id": "light.kitchen_island"},
    )
    assert result["executed"] is False
    assert "device offline" in result["error"]


# ── search_entities ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_entities_matches_friendly_name(
    hass: HomeAssistant, setup_world
) -> None:
    result = await _tool_search_entities(hass, {"query": "kitchen island"})
    assert result["count"] >= 1
    top = result["matches"][0]
    assert top["entity_id"] == "light.kitchen_island"
    assert top["score"] >= 2


@pytest.mark.asyncio
async def test_search_entities_matches_alias(
    hass: HomeAssistant, setup_world
) -> None:
    """The aliases set should be searchable ('reading lamp' → bedroom_lamp)."""
    result = await _tool_search_entities(hass, {"query": "reading lamp"})
    ids = [m["entity_id"] for m in result["matches"]]
    assert "light.bedroom_lamp" in ids


@pytest.mark.asyncio
async def test_search_entities_domain_filter(
    hass: HomeAssistant, setup_world
) -> None:
    result = await _tool_search_entities(
        hass, {"query": "kitchen", "domain": "switch"}
    )
    # Coffee maker is in switch domain but doesn't mention "kitchen" — should miss.
    assert result["count"] == 0


@pytest.mark.asyncio
async def test_search_entities_limit(
    hass: HomeAssistant, setup_world
) -> None:
    result = await _tool_search_entities(hass, {"query": "light", "limit": 1})
    assert len(result["matches"]) <= 1


@pytest.mark.asyncio
async def test_search_entities_missing_query(hass: HomeAssistant) -> None:
    result = await _tool_search_entities(hass, {})
    assert "error" in result


# ── get_entity_history ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_entity_history_returns_deduped_changes(
    hass: HomeAssistant, setup_world
) -> None:
    """The handler deduplicates consecutive identical states."""
    from datetime import UTC, datetime, timedelta

    class _FakeState:
        def __init__(self, state: str, when: datetime) -> None:
            self.state = state
            self.last_changed = when

    now = datetime.now(UTC)
    fake_states = {
        "light.kitchen_island": [
            _FakeState("off", now - timedelta(hours=2)),
            _FakeState("off", now - timedelta(hours=1, minutes=55)),  # dedup
            _FakeState("on", now - timedelta(hours=1)),
            _FakeState("off", now - timedelta(minutes=30)),
        ]
    }

    fake_instance = AsyncMock()
    fake_instance.async_add_executor_job = AsyncMock(return_value=fake_states)

    with (
        patch(
            "homeassistant.components.recorder.get_instance",
            return_value=fake_instance,
        ),
        patch(
            "homeassistant.components.recorder.history.get_significant_states",
            return_value=fake_states,
        ),
    ):
        result = await _tool_get_entity_history(
            hass,
            {"entity_id": "light.kitchen_island", "hours": 3},
        )

    assert result["entity_id"] == "light.kitchen_island"
    assert result["count"] == 3
    assert [c["state"] for c in result["changes"]] == ["off", "on", "off"]


@pytest.mark.asyncio
async def test_get_entity_history_unknown_entity(hass: HomeAssistant) -> None:
    result = await _tool_get_entity_history(hass, {"entity_id": "light.nope"})
    assert "error" in result


@pytest.mark.asyncio
async def test_get_entity_history_clamps_hours(
    hass: HomeAssistant, setup_world
) -> None:
    """hours is clamped to [0.25, 24]."""
    fake_instance = AsyncMock()
    fake_instance.async_add_executor_job = AsyncMock(return_value={})

    with patch(
        "homeassistant.components.recorder.get_instance",
        return_value=fake_instance,
    ):
        result = await _tool_get_entity_history(
            hass,
            {"entity_id": "light.kitchen_island", "hours": 9999},
        )
    assert result["hours"] == 24


# ── eval_template ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eval_template_renders(hass: HomeAssistant, setup_world) -> None:
    result = await _tool_eval_template(
        hass, {"template": "{{ states('light.kitchen_island') }}"}
    )
    assert result["result"] == "off"


@pytest.mark.asyncio
async def test_eval_template_arithmetic(hass: HomeAssistant) -> None:
    result = await _tool_eval_template(hass, {"template": "{{ 1 + 2 }}"})
    assert result["result"] == "3"


@pytest.mark.asyncio
async def test_eval_template_syntax_error(hass: HomeAssistant) -> None:
    result = await _tool_eval_template(hass, {"template": "{{ states("})
    assert "error" in result


@pytest.mark.asyncio
async def test_eval_template_missing(hass: HomeAssistant) -> None:
    result = await _tool_eval_template(hass, {})
    assert "error" in result


@pytest.mark.asyncio
async def test_eval_template_length_cap(hass: HomeAssistant) -> None:
    payload = "{{ 'a' }}" + "x" * 2000
    result = await _tool_eval_template(hass, {"template": payload})
    assert "error" in result
    assert "character limit" in result["error"]


# ── large_context_only gating ────────────────────────────────────────────────


def test_large_context_only_tools_are_marked() -> None:
    """search_entities, get_entity_history, eval_template skip low-context providers."""
    by_name = {t.name: t for t in CHAT_TOOLS}
    assert by_name["search_entities"].large_context_only is True
    assert by_name["get_entity_history"].large_context_only is True
    assert by_name["eval_template"].large_context_only is True


def test_universal_tools_are_not_gated() -> None:
    """execute_command and activate_scene must be available to all providers."""
    by_name = {t.name: t for t in CHAT_TOOLS}
    assert by_name["execute_command"].large_context_only is False
    assert by_name["activate_scene"].large_context_only is False
    assert by_name["get_entity_state"].large_context_only is False
    assert by_name["validate_action"].large_context_only is False


def test_provider_filter_drops_large_only_for_low_context() -> None:
    """Simulate the filter in LLMClient._get_tools_for_provider."""
    low_ctx_tools = [t for t in CHAT_TOOLS if not t.large_context_only]
    names = {t.name for t in low_ctx_tools}
    assert "search_entities" not in names
    assert "get_entity_history" not in names
    assert "eval_template" not in names
    assert "execute_command" in names
    assert "activate_scene" in names


# ── data parameter exposed on chat-facing schema ────────────────────────────


def test_execute_command_schema_includes_data_param() -> None:
    """Tool-capable providers must see a 'data' param so they emit brightness/temperature/etc."""
    by_name = {t.name: t for t in CHAT_TOOLS}
    params = {p.name: p for p in by_name["execute_command"].params}
    assert "data" in params
    assert params["data"].type == "object"


def test_validate_action_schema_includes_data_param() -> None:
    by_name = {t.name: t for t in CHAT_TOOLS}
    params = {p.name: p for p in by_name["validate_action"].params}
    assert "data" in params
    assert params["data"].type == "object"


def test_execute_command_anthropic_schema_has_data() -> None:
    """Anthropic serializer must expose 'data' as an object property."""
    by_name = {t.name: t for t in CHAT_TOOLS}
    schema = by_name["execute_command"].to_anthropic()["input_schema"]
    assert "data" in schema["properties"]
    assert schema["properties"]["data"]["type"] == "object"
    # data is optional — service and entity_id are the only required fields
    assert "data" not in schema.get("required", [])


def test_execute_command_openai_schema_has_data() -> None:
    """OpenAI/Ollama serializer must expose 'data' as an object property."""
    by_name = {t.name: t for t in CHAT_TOOLS}
    schema = by_name["execute_command"].to_openai()["function"]["parameters"]
    assert "data" in schema["properties"]
    assert schema["properties"]["data"]["type"] == "object"


@pytest.mark.asyncio
async def test_execute_command_data_param_reaches_service(
    hass: HomeAssistant, setup_world
) -> None:
    """End-to-end: brightness_pct in 'data' is forwarded to hass.services.async_call."""
    calls: list[dict] = []

    async def _capture(call):
        calls.append({"domain": call.domain, "service": call.service, "data": dict(call.data)})

    hass.services.async_register("light", "turn_on", _capture)

    result = await _tool_execute_command(
        hass,
        {
            "service": "light.turn_on",
            "entity_id": "light.kitchen_island",
            "data": {"brightness_pct": 50},
        },
    )
    assert result["executed"] is True
    assert calls[0]["data"]["brightness_pct"] == 50


@pytest.mark.asyncio
async def test_execute_command_rejects_non_actionable_entity(
    hass: HomeAssistant, setup_world
) -> None:
    """Regression: an entity present in hass.states but filtered out of
    _collect_entity_states (e.g. unavailable) must not be controllable
    via the tool, even though _COMMAND_SERVICE_POLICIES would otherwise
    allow the service. Mirrors the JSON path which only sees the filtered
    entity snapshot.
    """
    # The fixture has light.kitchen_island in "off" state. Drop it into
    # an "unavailable" state — _collect_entity_states skips unavailable
    # entities, so the tool's allowlist should reject it.
    hass.states.async_set("light.kitchen_island", "unavailable")

    result = await _tool_execute_command(
        hass,
        {"service": "light.turn_on", "entity_id": "light.kitchen_island"},
    )
    assert result.get("valid") is False
    assert any("not known to Home Assistant" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_execute_command_data_param_climate_set_temperature(
    hass: HomeAssistant, setup_world
) -> None:
    """climate.set_temperature accepts temperature + hvac_mode via 'data'."""
    from homeassistant.helpers import entity_registry as er

    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("climate", "test", "thermo_uid", suggested_object_id="thermostat")
    hass.states.async_set("climate.thermostat", "heat", {"friendly_name": "Thermo"})

    calls: list[dict] = []

    async def _capture(call):
        calls.append({"service": call.service, "data": dict(call.data)})

    hass.services.async_register("climate", "set_temperature", _capture)

    result = await _tool_execute_command(
        hass,
        {
            "service": "climate.set_temperature",
            "entity_id": "climate.thermostat",
            "data": {"temperature": 21, "hvac_mode": "heat"},
        },
    )
    assert result["executed"] is True
    assert calls[0]["data"]["temperature"] == 21
    assert calls[0]["data"]["hvac_mode"] == "heat"
