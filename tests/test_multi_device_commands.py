"""Tests for commands that cover several devices in one turn.

A response whose tool calls are all ``execute_command`` ends the turn — the
loop writes the confirmation from the results instead of asking the model
again. So "turn off all the lights" has to fit in one response: several
entities per call and several calls side by side. When the tool took one
entity per call, a cloud model sent one call, the turn ended, and only one
light went off.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.selora_ai.llm_client import LLMClient
from custom_components.selora_ai.providers import create_provider
from custom_components.selora_ai.llm_client.command_policy import (
    build_executed_confirmation,
    validate_command_action,
)
from custom_components.selora_ai.tool_executor import (
    ToolExecutor,
    commands_run_together,
    execute_command_arguments,
)
from custom_components.selora_ai.tool_registry import TOOL_MAP

LIGHTS = {
    "light.family_room_main_lights": "Family Room Main Lights",
    "light.family_room_sconces": "Family Room Sconces",
    "light.master_bedroom_main_lights": "Master Bedroom Main Lights",
    "light.kitchen": "Kitchen",
}


@pytest.fixture(autouse=True)
def _no_settle_wait() -> Any:
    """The mocked service changes no state; don't sit out the read-back wait."""
    from unittest.mock import patch

    from custom_components.selora_ai.mcp_server import commands

    with patch.object(commands, "_STATE_SETTLE_TIMEOUT", 0.01):
        yield


@pytest.fixture
def home(hass: HomeAssistant) -> list[Any]:
    for entity_id, name in LIGHTS.items():
        hass.states.async_set(entity_id, "on", {"friendly_name": name})
    return async_mock_service(hass, "light", "turn_off")


def test_entity_ids_fold_into_the_one_argument_the_command_path_reads() -> None:
    assert execute_command_arguments(
        {"service": "light.turn_off", "entity_ids": ["light.a", "light.b", "light.a"]}
    ) == {"service": "light.turn_off", "entity_id": ["light.a", "light.b"]}
    assert execute_command_arguments(
        {"service": "light.turn_off", "entity_id": "light.a", "entity_ids": ["light.b"]}
    ) == {"service": "light.turn_off", "entity_id": ["light.a", "light.b"]}
    assert execute_command_arguments({"service": "light.turn_off", "entity_ids": ["light.a"]}) == {
        "service": "light.turn_off",
        "entity_id": "light.a",
    }


def test_the_tool_takes_several_devices_and_says_the_turn_ends() -> None:
    schema = TOOL_MAP["execute_command"].to_anthropic()["input_schema"]
    assert schema["properties"]["entity_ids"]["type"] == "array"
    assert "entity_id" not in schema.get("required", [])
    description = TOOL_MAP["execute_command"].description
    assert "ENDS your turn" in description
    assert "EVERY call" in description


async def test_one_call_turns_off_several_lights(hass: HomeAssistant, home: list[Any]) -> None:
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)

    result = await executor.execute(
        "execute_command",
        {
            "service": "light.turn_off",
            "entity_ids": ["light.family_room_main_lights", "light.family_room_sconces"],
        },
    )

    assert result["executed"] is True, result
    assert home[0].data["entity_id"] == [
        "light.family_room_main_lights",
        "light.family_room_sconces",
    ]


async def test_all_the_lights_in_one_response_are_all_turned_off(
    hass: HomeAssistant, home: list[Any]
) -> None:
    """Two calls side by side in the one response — and the confirmation
    names every light, once."""
    client = LLMClient(hass, create_provider("anthropic", hass, api_key="test-key"))
    provider = client._provider
    provider.raw_request = AsyncMock(return_value={"stub": True})
    provider.append_tool_result = MagicMock()
    ids = list(LIGHTS)
    provider.extract_tool_calls = MagicMock(
        return_value=[
            {
                "name": "execute_command",
                "arguments": {"service": "light.turn_off", "entity_ids": ids[:3]},
                "id": "1",
            },
            {
                "name": "execute_command",
                "arguments": {"service": "light.turn_off", "entity_id": ids[3]},
                "id": "2",
            },
        ]
    )
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)

    text, error, log = await client._send_request_with_tools(
        system="s", messages=[], tool_executor=executor, tools=[]
    )

    assert error is None
    turned_off = [eid for call in home for eid in call.data["entity_id"]]
    assert sorted(turned_off) == sorted(ids)
    for name in LIGHTS.values():
        assert name in text, text
    assert provider.raw_request.await_count == 1  # the write-only round ended the turn


async def test_an_approval_for_several_devices_keeps_every_target(hass: HomeAssistant) -> None:
    """The approval card is built from the tool log, which must carry the
    folded targets — not the raw ``entity_ids`` its readers don't look at."""
    from custom_components.selora_ai.llm_client.command_policy import (
        _pending_approval_calls_from_log,
    )

    for entity_id in ("lock.front_door", "lock.back_door"):
        hass.states.async_set(entity_id, "locked", {"friendly_name": entity_id})
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)

    result = await executor.execute(
        "execute_command",
        {"service": "lock.unlock", "entity_ids": ["lock.front_door", "lock.back_door"]},
    )

    assert result.get("requires_approval") is True, result
    pending = _pending_approval_calls_from_log(executor.call_log)
    assert [call["target"]["entity_id"] for call in pending] == [
        ["lock.front_door", "lock.back_door"]
    ]


def test_a_safe_call_covers_a_whole_category() -> None:
    lights = [f"light.l{i}" for i in range(16)]
    assert validate_command_action("light.turn_off", lights, known_entity_ids=set(lights))["valid"]


def test_a_call_that_needs_approval_stays_small(hass: HomeAssistant) -> None:
    doors = [f"cover.garage_{i}" for i in range(4)]
    for door in doors:
        hass.states.async_set(door, "closed", {"device_class": "garage"})
    locks = [f"lock.door_{i}" for i in range(4)]

    covers = validate_command_action(
        "cover.open_cover", doors, known_entity_ids=set(doors), hass=hass
    )
    unlock = validate_command_action("lock.unlock", locks, known_entity_ids=set(locks), hass=hass)

    assert covers["valid"] is False and "at most 3" in covers["errors"][0]
    assert unlock["valid"] is False and "max 3" in unlock["errors"][0]


def test_one_action_reads_as_one_sentence() -> None:
    text = build_executed_confirmation(
        [
            {"service": "light.turn_off", "entity_ids": ["light.a", "light.b"]},
            {"service": "switch.turn_off", "entity_ids": ["switch.c", "light.a"]},
            {"service": "light.turn_on", "entity_ids": ["light.d"]},
        ],
        lambda eid: eid.split(".")[1].upper(),
        language="en",
    )

    assert text.startswith("Turned off A, B, C. Turned on D.")
    assert "[[entities:light.a,light.b,switch.c,light.d]]" in text


def test_a_device_in_two_actions_is_named_in_both() -> None:
    text = build_executed_confirmation(
        [
            {"service": "light.turn_on", "entity_ids": ["light.lamp"]},
            {"service": "light.turn_off", "entity_ids": ["light.lamp"]},
        ],
        lambda _eid: "Lamp",
        language="en",
    )

    assert text.startswith("Turned on Lamp. Turned off Lamp.")
    assert "[[entities:light.lamp]]" in text


def test_actions_are_confirmed_in_the_order_they_ran() -> None:
    text = build_executed_confirmation(
        [
            {"service": "light.turn_on", "entity_ids": ["light.lamp"]},
            {"service": "light.turn_off", "entity_ids": ["light.lamp"]},
            {"service": "light.turn_on", "entity_ids": ["light.lamp"]},
        ],
        lambda _eid: "Lamp",
        language="en",
    )

    assert text.startswith("Turned on Lamp. Turned off Lamp. Turned on Lamp.")


def test_two_devices_sharing_a_name_are_both_named() -> None:
    text = build_executed_confirmation(
        [{"service": "light.turn_off", "entity_ids": ["light.lamp", "light.lamp_2"]}],
        lambda _eid: "Lamp",
        language="en",
    )

    assert text.startswith("Turned off Lamp, Lamp.")


def _command(service: str, *ids: str) -> dict[str, Any]:
    return {"name": "execute_command", "arguments": {"service": service, "entity_ids": list(ids)}}


def test_only_commands_that_commute_run_together() -> None:
    assert commands_run_together(
        [_command("light.turn_off", "light.a", "light.b"), _command("switch.turn_off", "switch.c")]
    )
    # One call, nothing to overlap.
    assert not commands_run_together([_command("light.turn_off", "light.a")])
    # Different verbs: on then off is not off then on.
    assert not commands_run_together(
        [_command("light.turn_on", "light.a"), _command("switch.turn_off", "switch.c")]
    )
    # Different data: a group can share members with another target.
    assert not commands_run_together(
        [
            {
                "name": "execute_command",
                "arguments": {
                    "service": "light.turn_on",
                    "entity_id": "light.downstairs",
                    "data": {"brightness_pct": 20},
                },
            },
            {
                "name": "execute_command",
                "arguments": {"service": "light.turn_on", "entity_id": "light.all_lights"},
            },
        ]
    )
    # A toggle depends on the state it finds.
    assert not commands_run_together(
        [_command("light.toggle", "light.a"), _command("light.toggle", "light.b")]
    )
    # The same device twice.
    assert not commands_run_together(
        [_command("light.turn_off", "light.a"), _command("light.turn_off", "light.a")]
    )
    # A scene sets other devices; the model's order stands.
    assert not commands_run_together(
        [_command("scene.turn_on", "scene.a"), _command("scene.turn_on", "scene.b")]
    )
    # A read in the round.
    assert not commands_run_together(
        [_command("light.turn_off", "light.a"), {"name": "search_entities", "arguments": {}}]
    )


def _both_must_be_in_flight() -> Any:
    """An execute that only returns once the other call has started too —
    so a loop running the calls one after another times out on it."""
    barrier = asyncio.Barrier(2)

    async def _execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        await asyncio.wait_for(barrier.wait(), 1)
        args = execute_command_arguments(arguments)
        ids = args.get("entity_id")
        return {
            "executed": True,
            "service": args["service"],
            "entity_ids": [ids] if isinstance(ids, str) else ids,
        }

    executor = MagicMock()
    executor.execute = _execute
    executor.logged_arguments = ToolExecutor.logged_arguments
    executor.call_log = []
    return executor


ROUND = [
    {
        "name": "execute_command",
        "arguments": {"service": "light.turn_off", "entity_ids": ["light.a"]},
        "id": "1",
    },
    {
        "name": "execute_command",
        "arguments": {"service": "switch.turn_off", "entity_id": "switch.b"},
        "id": "2",
    },
]


async def test_a_round_of_commands_runs_together(hass: HomeAssistant) -> None:
    client = LLMClient(hass, create_provider("anthropic", hass, api_key="test-key"))
    provider = client._provider
    provider.raw_request = AsyncMock(return_value={"stub": True})
    provider.append_tool_result = MagicMock()
    provider.extract_tool_calls = MagicMock(return_value=ROUND)

    text, error, _log = await client._send_request_with_tools(
        system="s", messages=[], tool_executor=_both_must_be_in_flight(), tools=[]
    )

    assert error is None
    assert text.startswith("Turned off light.a, switch.b."), text


async def test_a_streamed_round_of_commands_runs_together(hass: HomeAssistant) -> None:
    client = LLMClient(hass, create_provider("anthropic", hass, api_key="test-key"))

    async def _raw_stream(system: str, messages: list[Any], *, tools: Any = None) -> Any:
        yield object()

    def _stream_with_tools(resp: Any, tool_calls: list[Any], content_blocks: list[Any]) -> Any:
        async def _gen() -> Any:
            tool_calls.extend(ROUND)
            if False:
                yield ""

        return _gen()

    client._provider.raw_request_stream = _raw_stream
    client._provider.stream_with_tools = _stream_with_tools
    client._provider.append_streaming_tool_results = lambda *a, **k: None

    chunks = [
        chunk
        async for chunk in client._stream_request_with_tools(
            "s", [{"role": "user", "content": "x"}], _both_must_be_in_flight(), tools=[]
        )
    ]

    assert "Turned off light.a, switch.b." in "".join(c for c in chunks if isinstance(c, str))


def test_the_local_model_turns_off_a_whole_floor_in_one_call() -> None:
    """Selora AI Local fans "turn off the upstairs lights" out itself; it
    chunks by the policy's cap, so sixteen lights are one call — under the
    per-envelope call limit that a chunk of three overran."""
    from custom_components.selora_ai.providers.selora_local.commands.light import (
        _CommandsLightMixin,
    )

    lights = [f"light.up_{i}" for i in range(16)]
    handler = _CommandsLightMixin()
    handler._light_scope_targets = lambda _msg, _lights: lights  # type: ignore[method-assign]
    handler._resolve_named_light = lambda _msg, _lights: None  # type: ignore[method-assign]

    calls, _response = handler._light_clause_calls("turn off the upstairs lights", [])

    assert calls == [{"service": "light.turn_off", "target": {"entity_id": lights}}]
