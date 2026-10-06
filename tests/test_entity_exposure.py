"""An entity's full attributes, and which voice assistants can see it.

``get_entity_state`` returned a handful of attributes chosen per domain, so a
vacuum's battery or a sensor's extra readings were invisible, and
``update_entity`` could expose an entity to Assist only — "let Alexa control
the porch light" had no tool.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai import entity_exposure
from custom_components.selora_ai.mcp_server.definitions import _TOOL_DEFINITIONS
from custom_components.selora_ai.mcp_server.entities import _tool_get_entity_state
from custom_components.selora_ai.mcp_server.names import TOOL_UPDATE_ENTITY
from custom_components.selora_ai.mcp_server.registry import _tool_update_entity


@pytest.fixture
async def porch(hass: HomeAssistant) -> er.RegistryEntry:
    assert await async_setup_component(hass, "homeassistant", {})
    entry = er.async_get(hass).async_get_or_create("light", "test", "porch")
    hass.states.async_set(entry.entity_id, "on", {"friendly_name": "Porch"})
    return entry


def _options(hass: HomeAssistant, entity_id: str) -> dict[str, Any]:
    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None
    return dict(entry.options)


# ── attributes ──────────────────────────────────────────────────────────────


async def test_every_attribute_is_returned_as_json(hass: HomeAssistant) -> None:
    hass.states.async_set(
        "vacuum.downstairs",
        "docked",
        {
            "battery_level": 87,
            "fan_speed_list": ("quiet", "max"),
            "supported_modes": {"b", "a"},
            "last_run": datetime(2026, 10, 1, 9, 30, tzinfo=UTC),
            "nested": {"room": {"name": "Kitchen"}},
        },
    )

    attributes = (await _tool_get_entity_state(hass, {"entity_id": "vacuum.downstairs"}))[
        "attributes"
    ]

    assert attributes["battery_level"] == 87
    assert attributes["fan_speed_list"] == ["quiet", "max"]
    assert attributes["supported_modes"] == ["a", "b"]
    assert attributes["last_run"] == "2026-10-01T09:30:00+00:00"
    assert attributes["nested"] == {"room": {"name": "Kitchen"}}


async def test_credentials_in_attributes_are_not_returned(hass: HomeAssistant) -> None:
    """A camera's token opens its stream; this read is open to read-only
    credentials, camera images are not."""
    hass.states.async_set(
        "camera.door",
        "idle",
        {
            "access_token": "s3cr3t",
            "entity_picture": "/api/camera_proxy/camera.door?token=s3cr3t&width=200",
            "brand": "Acme",
        },
    )

    result = await _tool_get_entity_state(hass, {"entity_id": "camera.door"})

    assert "s3cr3t" not in str(result)
    assert result["attributes"]["entity_picture"] == "/api/camera_proxy/camera.door?width=200"
    assert result["attributes"]["brand"] == "Acme"


async def test_adjacent_and_deeply_nested_credentials_are_stripped(hass: HomeAssistant) -> None:
    hass.states.async_set(
        "camera.yard",
        "idle",
        {
            "stream": "/cam?token=sk-aa&access_token=sk-bb&authSig=sk-cc&width=200",
            "deep": {"a": {"b": {"c": {"access_token": "sk-dd"}}}},
        },
    )

    result = await _tool_get_entity_state(hass, {"entity_id": "camera.yard"})

    for secret in ("sk-aa", "sk-bb", "sk-cc", "sk-dd"):
        assert secret not in str(result), secret
    assert result["attributes"]["stream"] == "/cam?width=200"
    assert result["attributes"]["deep"] == {"a": {"b": {"c": "{…}"}}}


async def test_a_huge_payload_is_abandoned_early(hass: HomeAssistant) -> None:
    """Converting stops at the budget instead of building the whole thing."""
    from custom_components.selora_ai.mcp_server import entities

    calls = 0
    real = entities._AttrConverter.convert

    def counting(self: Any, value: Any, depth: int = 0) -> Any:
        nonlocal calls
        calls += 1
        return real(self, value, depth)

    payload = {f"k{i}": {f"j{j}": "v" * 200 for j in range(40)} for i in range(40)}
    hass.states.async_set("sensor.blob", "ok", {"payload": payload, "unit": "x"})

    with patch.object(entities._AttrConverter, "convert", counting):
        result = await _tool_get_entity_state(hass, {"entity_id": "sensor.blob"})

    assert result["attributes_omitted"] == ["payload"]
    assert result["attributes"]["unit"] == "x"
    assert calls < 100


async def test_an_oversized_attribute_is_named_not_dropped_silently(hass: HomeAssistant) -> None:
    hass.states.async_set(
        "sensor.feed", "ok", {"payload": ["x" * 250] * 40, "unit_of_measurement": "items"}
    )

    result = await _tool_get_entity_state(hass, {"entity_id": "sensor.feed"})

    assert result["attributes_omitted"] == ["payload"]
    assert result["attributes"]["unit_of_measurement"] == "items"


# ── exposure ────────────────────────────────────────────────────────────────


async def test_reading_exposure_records_nothing(
    hass: HomeAssistant, porch: er.RegistryEntry
) -> None:
    """Core's own check writes the default it computes, pinning it."""
    result = await _tool_get_entity_state(hass, {"entity_id": porch.entity_id})

    assert result["exposed_to"] == {"assist": True}
    assert "conversation" not in _options(hass, porch.entity_id)


async def test_an_assistant_the_hub_does_not_use_is_refused(
    hass: HomeAssistant, porch: er.RegistryEntry
) -> None:
    result = await _tool_update_entity(
        hass, {"entity_id": porch.entity_id, "new_name": "Front", "expose_to_alexa": True}
    )

    assert "no Alexa skill is linked" in result["error"]
    assert er.async_get(hass).async_get(porch.entity_id).name is None
    assert "cloud.alexa" not in _options(hass, porch.entity_id)


async def test_alexa_with_selora_skill_linked(hass: HomeAssistant, porch: er.RegistryEntry) -> None:
    with patch.object(entity_exposure, "_selora_alexa_linked", return_value=True):
        result = await _tool_update_entity(
            hass, {"entity_id": porch.entity_id, "expose_to_alexa": False}
        )
        state = await _tool_get_entity_state(hass, {"entity_id": porch.entity_id})
        google = await _tool_update_entity(
            hass, {"entity_id": porch.entity_id, "expose_to_google": True}
        )

    assert result["exposed"] == {"alexa": False}
    assert result["changed"] == ["expose_to_alexa"]
    assert _options(hass, porch.entity_id)["cloud.alexa"]["should_expose"] is False
    assert state["exposed_to"] == {"assist": True, "alexa": False}
    assert "Home Assistant Cloud" in google["error"]


async def test_home_assistant_cloud_brings_both(
    hass: HomeAssistant, porch: er.RegistryEntry
) -> None:
    hass.data["cloud"] = SimpleNamespace(is_logged_in=True)
    try:
        result = await _tool_update_entity(
            hass,
            {"entity_id": porch.entity_id, "expose_to_google": True, "expose_to_assist": False},
        )
        state = await _tool_get_entity_state(hass, {"entity_id": porch.entity_id})
    finally:
        hass.data.pop("cloud")

    assert result["exposed"] == {"assist": False, "google": True}
    assert state["exposed_to"]["google"] is True
    assert state["exposed_to"]["assist"] is False


def test_the_schema_offers_every_assistant() -> None:
    (tool,) = [t for t in _TOOL_DEFINITIONS if t.name == TOOL_UPDATE_ENTITY]

    assert {"expose_to_assist", "expose_to_alexa", "expose_to_google"} <= set(
        tool.inputSchema["properties"]
    )
