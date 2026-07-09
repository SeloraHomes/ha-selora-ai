"""Tests for the get_entity_state, find_entities_by_area, and validate_action tools."""

from __future__ import annotations

import asyncio

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import (
    area_registry as ar,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
import pytest

from custom_components.selora_ai.mcp_server import (
    _tool_find_entities_by_area,
    _tool_get_entity_state,
    _tool_validate_action,
)


@pytest.fixture
async def setup_entities(hass: HomeAssistant):
    """Set up areas, a device, and entities with mixed area assignments."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)

    entry = MockConfigEntry(domain="test", entry_id="mock_entry_entities")
    entry.add_to_hass(hass)

    kitchen = area_reg.async_create("Kitchen")
    living = area_reg.async_create("Living Room")

    # Entity assigned directly via entity registry (kitchen)
    ent_reg.async_get_or_create(
        "light",
        "test",
        "kitchen_light_uid",
        suggested_object_id="kitchen_light",
    )
    ent_reg.async_update_entity("light.kitchen_light", area_id=kitchen.id)
    hass.states.async_set(
        "light.kitchen_light",
        "on",
        {"friendly_name": "Kitchen Light", "brightness": 200},
    )

    # Entity inherits area from its device (living room)
    device = dev_reg.async_get_or_create(
        config_entry_id="mock_entry_entities",
        identifiers={("test", "living_lamp")},
        name="Living Lamp",
    )
    dev_reg.async_update_device(device.id, area_id=living.id)
    ent_reg.async_get_or_create(
        "light",
        "test",
        "living_lamp_uid",
        device_id=device.id,
        suggested_object_id="living_lamp",
    )
    hass.states.async_set("light.living_lamp", "off", {"friendly_name": "Living Lamp"})

    # Climate entity in living room (different domain, same area as living_lamp)
    climate_device = dev_reg.async_get_or_create(
        config_entry_id="mock_entry_entities",
        identifiers={("test", "thermostat")},
        name="Thermostat",
    )
    dev_reg.async_update_device(climate_device.id, area_id=living.id)
    ent_reg.async_get_or_create(
        "climate",
        "test",
        "thermostat_uid",
        device_id=climate_device.id,
        suggested_object_id="thermostat",
    )
    hass.states.async_set(
        "climate.thermostat",
        "heat",
        {
            "friendly_name": "Living Thermostat",
            "temperature": 72,
            "current_temperature": 68,
            "hvac_action": "heating",
        },
    )

    # Unassigned entity (no area)
    ent_reg.async_get_or_create(
        "switch",
        "test",
        "unassigned_uid",
        suggested_object_id="unassigned",
    )
    hass.states.async_set("switch.unassigned", "off")

    return {"kitchen_id": kitchen.id, "living_id": living.id}


# ── get_entity_state ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_entity_state_returns_state(hass: HomeAssistant, setup_entities) -> None:
    result = await _tool_get_entity_state(hass, {"entity_id": "light.kitchen_light"})
    assert result["entity_id"] == "light.kitchen_light"
    assert result["state"] == "on"
    assert result["domain"] == "light"
    assert result["area"] == "Kitchen"
    assert result["friendly_name"] == "Kitchen Light"
    assert result["attributes"]["brightness"] == 200


@pytest.mark.asyncio
async def test_get_entity_state_climate_attrs(hass: HomeAssistant, setup_entities) -> None:
    result = await _tool_get_entity_state(hass, {"entity_id": "climate.thermostat"})
    assert result["state"] == "heat"
    assert result["attributes"]["temperature"] == 72
    assert result["attributes"]["current_temperature"] == 68
    assert result["attributes"]["hvac_action"] == "heating"


@pytest.mark.asyncio
async def test_get_entity_state_missing_id(hass: HomeAssistant) -> None:
    result = await _tool_get_entity_state(hass, {})
    assert "error" in result


@pytest.mark.asyncio
async def test_get_entity_state_unknown_entity(hass: HomeAssistant) -> None:
    result = await _tool_get_entity_state(hass, {"entity_id": "light.does_not_exist"})
    assert "error" in result
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_get_entity_state_malformed_id(hass: HomeAssistant) -> None:
    result = await _tool_get_entity_state(hass, {"entity_id": "no_dot"})
    assert "error" in result
    assert "malformed" in result["error"]


@pytest.mark.asyncio
async def test_get_entity_state_resolves_area_via_device(
    hass: HomeAssistant, setup_entities
) -> None:
    """Regression: entities assigned to an area only through their device
    must still report the room name. Without the device-area fallback this
    returned area: null and the LLM lost room context.
    """
    result = await _tool_get_entity_state(hass, {"entity_id": "light.living_lamp"})
    assert result["entity_id"] == "light.living_lamp"
    assert result["area"] == "Living Room"


@pytest.mark.asyncio
async def test_get_entity_state_no_area_when_neither_assigned(
    hass: HomeAssistant, setup_entities
) -> None:
    """Entities with neither entity- nor device-level area still return None."""
    result = await _tool_get_entity_state(hass, {"entity_id": "switch.unassigned"})
    assert result["entity_id"] == "switch.unassigned"
    assert result["area"] is None


# ── find_entities_by_area ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_entities_by_area_basic(hass: HomeAssistant, setup_entities) -> None:
    result = await _tool_find_entities_by_area(hass, {"area": "Kitchen"})
    assert result["count"] == 1
    assert result["entities"][0]["entity_id"] == "light.kitchen_light"
    assert result["area_matches"] == ["Kitchen"]


@pytest.mark.asyncio
async def test_find_entities_by_area_resolves_via_device(
    hass: HomeAssistant, setup_entities
) -> None:
    """Entity with no entity-level area should inherit its device's area."""
    result = await _tool_find_entities_by_area(hass, {"area": "Living Room"})
    entity_ids = {e["entity_id"] for e in result["entities"]}
    assert "light.living_lamp" in entity_ids
    assert "climate.thermostat" in entity_ids


@pytest.mark.asyncio
async def test_find_entities_by_area_case_insensitive(hass: HomeAssistant, setup_entities) -> None:
    result = await _tool_find_entities_by_area(hass, {"area": "kitchen"})
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_find_entities_by_area_domain_filter(hass: HomeAssistant, setup_entities) -> None:
    result = await _tool_find_entities_by_area(hass, {"area": "Living Room", "domain": "light"})
    entity_ids = {e["entity_id"] for e in result["entities"]}
    assert entity_ids == {"light.living_lamp"}


@pytest.mark.asyncio
async def test_find_entities_by_area_no_match(hass: HomeAssistant, setup_entities) -> None:
    result = await _tool_find_entities_by_area(hass, {"area": "Basement"})
    assert result["count"] == 0
    assert result["entities"] == []
    assert result["area_matches"] == []


@pytest.mark.asyncio
async def test_find_entities_by_area_missing_area(hass: HomeAssistant) -> None:
    result = await _tool_find_entities_by_area(hass, {})
    assert "error" in result


# ── validate_action ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_action_valid_light_turn_on(hass: HomeAssistant, setup_entities) -> None:
    result = await _tool_validate_action(
        hass,
        {
            "service": "light.turn_on",
            "entity_id": "light.kitchen_light",
            "data": {"brightness_pct": 80},
        },
    )
    assert result["valid"] is True
    assert result["errors"] == []
    assert result["domain"] == "light"
    assert "brightness_pct" in result["allowed_data_keys"]


@pytest.mark.asyncio
async def test_validate_action_rejects_unknown_domain(hass: HomeAssistant) -> None:
    # ``python_script.exec`` is on the BLOCKED denylist — it can never
    # be auto-approved via the chat-driven approval flow.
    result = await _tool_validate_action(
        hass,
        {"service": "python_script.exec", "entity_id": "python_script.foo"},
    )
    assert result["valid"] is False
    assert any("allowlist" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_action_flags_review_service(hass: HomeAssistant) -> None:
    """REVIEW-bucket services (lock.unlock, tts.*, …) come back as
    ``valid=False`` but with ``requires_approval=True`` so the tool path
    can route the call to the chat approval card rather than hard-reject."""
    result = await _tool_validate_action(
        hass,
        {"service": "lock.unlock", "entity_id": "lock.front_door"},
    )
    assert result["valid"] is False
    assert result.get("requires_approval") is True
    assert result.get("risk_level") == "high"


@pytest.mark.asyncio
async def test_validate_action_rejects_unknown_verb(hass: HomeAssistant) -> None:
    result = await _tool_validate_action(
        hass,
        {"service": "light.explode", "entity_id": "light.kitchen_light"},
    )
    assert result["valid"] is False
    assert any("not a valid light service" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_action_rejects_unknown_entity(hass: HomeAssistant, setup_entities) -> None:
    result = await _tool_validate_action(
        hass,
        {"service": "light.turn_on", "entity_id": "light.ghost"},
    )
    assert result["valid"] is False
    assert any("not known to Home Assistant" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_action_rejects_domain_mismatch(hass: HomeAssistant, setup_entities) -> None:
    result = await _tool_validate_action(
        hass,
        {"service": "light.turn_on", "entity_id": "switch.unassigned"},
    )
    assert result["valid"] is False
    assert any("switch domain, not light" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_action_rejects_unsupported_param(
    hass: HomeAssistant, setup_entities
) -> None:
    result = await _tool_validate_action(
        hass,
        {
            "service": "light.turn_on",
            "entity_id": "light.kitchen_light",
            "data": {"volume_level": 0.5},
        },
    )
    assert result["valid"] is False
    assert any("unsupported parameters" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_action_malformed_service(hass: HomeAssistant) -> None:
    result = await _tool_validate_action(hass, {"service": "no_dot", "entity_id": "light.x"})
    assert result["valid"] is False
    assert any("<domain>.<verb>" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_action_data_must_be_object(hass: HomeAssistant) -> None:
    result = await _tool_validate_action(
        hass,
        {
            "service": "light.turn_on",
            "entity_id": "light.kitchen_light",
            "data": "not-an-object",
        },
    )
    assert result["valid"] is False
    assert any("data must be an object" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_validate_action_rejects_unavailable_entity(
    hass: HomeAssistant, setup_entities
) -> None:
    """Regression: validate_action must use the same allowlist as
    execute_command. An entity that's unavailable in hass.states would
    otherwise be approved by validate_action's pre-flight check but
    then rejected by execute_command's dispatch — a confusing
    'validated but failed to run' workflow for the model.
    """
    from custom_components.selora_ai.mcp_server import _tool_execute_command

    # Drop the entity into "unavailable" so _collect_entity_states skips it.
    hass.states.async_set("light.kitchen_light", "unavailable")

    # Pre-flight check must reject.
    validation = await _tool_validate_action(
        hass,
        {"service": "light.turn_on", "entity_id": "light.kitchen_light"},
    )
    assert validation["valid"] is False
    assert any("not known to Home Assistant" in e for e in validation["errors"])

    # And the dispatcher must agree.
    execution = await _tool_execute_command(
        hass,
        {"service": "light.turn_on", "entity_id": "light.kitchen_light"},
    )
    assert execution.get("valid") is False


@pytest.mark.asyncio
async def test_execute_command_waits_for_delayed_state(hass: HomeAssistant, setup_entities) -> None:
    """Regression: execute_command must report the post-command state, not the
    stale pre-command one.

    blocking=True awaits the service handler but not the state-change event.
    Integrations that update state a beat after the handler returns (coordinator
    / poll-backed lights) would otherwise be read back at their old value — the
    "commands executed but lights still report off" bug. The read-back must wait
    for the fresh state.
    """
    from custom_components.selora_ai.mcp_server import _tool_execute_command

    async def _delayed_turn_off(call: ServiceCall) -> None:
        # Simulate a handler that returns before the state propagates.
        async def _apply() -> None:
            await asyncio.sleep(0.05)
            hass.states.async_set("light.kitchen_light", "off", {"friendly_name": "Kitchen Light"})

        hass.async_create_task(_apply())

    hass.services.async_register("light", "turn_off", _delayed_turn_off)

    execution = await _tool_execute_command(
        hass,
        {"service": "light.turn_off", "entity_id": "light.kitchen_light"},
    )

    assert execution["executed"] is True
    assert execution["states"] == [{"entity_id": "light.kitchen_light", "state": "off"}]


@pytest.mark.asyncio
async def test_execute_command_no_op_returns_promptly(hass: HomeAssistant, setup_entities) -> None:
    """Regression: an idempotent no-op command must not stall on the settle
    timeout.

    Turning on an already-on light commonly completes without emitting *any*
    state event (the integration short-circuits and writes nothing). Because the
    target already satisfies the requested action, execute_command must skip the
    settle wait and return immediately rather than block for the full timeout.
    The service handler here writes no state at all; the short wait_for makes a
    regression to waiting out _STATE_SETTLE_TIMEOUT fail the test.
    """
    from custom_components.selora_ai.mcp_server import _tool_execute_command

    handler_calls: list[str] = []

    async def _silent_turn_on(call: ServiceCall) -> None:
        # Already-on light: a real integration short-circuits, writing nothing.
        handler_calls.append("light.turn_on")

    hass.services.async_register("light", "turn_on", _silent_turn_on)

    execution = await asyncio.wait_for(
        _tool_execute_command(
            hass,
            {"service": "light.turn_on", "entity_id": "light.kitchen_light"},
        ),
        timeout=1.0,
    )

    assert handler_calls == ["light.turn_on"]  # the command really ran
    assert execution["executed"] is True
    assert execution["states"] == [{"entity_id": "light.kitchen_light", "state": "on"}]


@pytest.mark.asyncio
async def test_execute_command_mixed_batch_settles_on_transitioning_only(
    hass: HomeAssistant, setup_entities
) -> None:
    """Regression: a batch mixing an already-satisfied target with one that must
    transition must return as soon as the transitioning one settles.

    kitchen_light is already on and emits no event; living_lamp is off and
    transitions a beat later. Only the transitioning target should be armed, so
    the already-on one can't keep the settle wait pending until the timeout.
    """
    from custom_components.selora_ai.mcp_server import _tool_execute_command

    async def _turn_on(call: ServiceCall) -> None:
        for eid in call.data.get("entity_id", []):
            st = hass.states.get(eid)
            if st is not None and st.state != "on":
                # Off target: reflect the change a beat after the handler returns.
                async def _apply(entity_id: str = eid) -> None:
                    await asyncio.sleep(0.05)
                    hass.states.async_set(entity_id, "on", {"friendly_name": entity_id})

                hass.async_create_task(_apply())
            # Already-on target: a real integration writes nothing here.

    hass.services.async_register("light", "turn_on", _turn_on)

    execution = await asyncio.wait_for(
        _tool_execute_command(
            hass,
            {
                "service": "light.turn_on",
                "entity_id": ["light.kitchen_light", "light.living_lamp"],
            },
        ),
        timeout=1.0,
    )

    assert execution["executed"] is True
    states = {s["entity_id"]: s["state"] for s in execution["states"]}
    assert states == {"light.kitchen_light": "on", "light.living_lamp": "on"}


@pytest.mark.asyncio
async def test_execute_command_waits_for_terminal_not_transitional_state(
    hass: HomeAssistant, setup_entities
) -> None:
    """Regression: for a service with a known terminal state, the read-back must
    wait for that state, not the transitional one.

    cover.open_cover emits "opening" before "open". Settling on the first event
    would report the transitional "opening"; the watch must hold until the cover
    reaches "open".
    """
    from homeassistant.helpers import entity_registry as er

    from custom_components.selora_ai.mcp_server import _tool_execute_command

    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("cover", "test", "garage_uid", suggested_object_id="garage")
    hass.states.async_set("cover.garage", "closed", {"friendly_name": "Garage"})

    async def _open_cover(call: ServiceCall) -> None:
        # Transitional state synchronously, terminal state a beat later.
        hass.states.async_set("cover.garage", "opening", {"friendly_name": "Garage"})

        async def _finish() -> None:
            await asyncio.sleep(0.05)
            hass.states.async_set("cover.garage", "open", {"friendly_name": "Garage"})

        hass.async_create_task(_finish())

    hass.services.async_register("cover", "open_cover", _open_cover)

    execution = await asyncio.wait_for(
        _tool_execute_command(
            hass,
            {"service": "cover.open_cover", "entity_id": "cover.garage"},
        ),
        timeout=1.0,
    )

    assert execution["executed"] is True
    assert execution["states"] == [{"entity_id": "cover.garage", "state": "open"}]
