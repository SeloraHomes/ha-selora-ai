"""Tests for removing a device, entity, integration or blueprint from chat.

One tool, ``remove_from_home``, returns a delete card; confirming goes through
the same ``_resolve_delete_approval`` every delete card does.
"""

from __future__ import annotations

import logging
import pathlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.blueprint.models import DomainBlueprints
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    mock_integration,
)

from custom_components.selora_ai import _resolve_delete_approval
from custom_components.selora_ai.llm_client.command_policy import _pending_deletes_from_log
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import TOOL_MAP


def _executor(hass: HomeAssistant) -> ToolExecutor:
    return ToolExecutor(hass, MagicMock(), is_admin=True)


async def _confirm(hass: HomeAssistant, descriptor: dict[str, Any]) -> MagicMock:
    store = MagicMock()
    store.set_approval_status = AsyncMock()
    store.append_message = AsyncMock(return_value={"role": "assistant"})
    connection = MagicMock()
    await _resolve_delete_approval(
        hass,
        connection,
        {"id": 1},
        store,
        "sess",
        0,
        {"approval_kind": "delete", "deletes": [descriptor]},
        "delete",
        language="en",
    )
    return connection


async def _card(hass: HomeAssistant, **arguments: Any) -> dict[str, Any]:
    """The tool's card, as the synthesizer collects it from the tool log."""
    executor = _executor(hass)
    result = await executor.execute("remove_from_home", arguments)
    assert result.get("requires_approval"), result
    (descriptor,) = _pending_deletes_from_log(executor.call_log)
    return descriptor


def _speaker(hass: HomeAssistant) -> tuple[MockConfigEntry, dr.DeviceEntry, str]:
    entry = MockConfigEntry(domain="sonos", title="Sonos")
    entry.add_to_hass(hass)
    entry.supports_remove_device = True
    entry.mock_state(hass, ConfigEntryState.LOADED)
    mock_integration(
        hass, MockModule("sonos", async_remove_config_entry_device=AsyncMock(return_value=True))
    )
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("sonos", "bedroom")}, name="Bedroom"
    )
    entity = er.async_get(hass).async_get_or_create(
        "media_player", "sonos", "bedroom", device_id=device.id, config_entry=entry
    )
    return entry, device, entity.entity_id


async def test_a_device_is_removed_from_its_card(hass: HomeAssistant) -> None:
    _, device, entity_id = _speaker(hass)

    descriptor = await _card(hass, kind="device", id=device.id)
    assert "Bedroom" in descriptor["label"]
    assert dr.async_get(hass).async_get(device.id) is not None
    connection = await _confirm(hass, descriptor)

    connection.send_error.assert_not_called()
    assert dr.async_get(hass).async_get(device.id) is None
    assert er.async_get(hass).async_get(entity_id) is None


async def test_an_orphaned_entity_is_removed_from_its_card(hass: HomeAssistant) -> None:
    _, _, entity_id = _speaker(hass)
    hass.states.async_set(entity_id, "unavailable", {"restored": True})

    descriptor = await _card(hass, kind="entity", id=entity_id)
    connection = await _confirm(hass, descriptor)

    connection.send_error.assert_not_called()
    assert er.async_get(hass).async_get(entity_id) is None


async def test_an_integration_is_removed_from_its_card(hass: HomeAssistant) -> None:
    entry, _, _ = _speaker(hass)

    descriptor = await _card(hass, kind="integration", id=entry.entry_id)
    assert "Sonos" in descriptor["label"]
    connection = await _confirm(hass, descriptor)

    connection.send_error.assert_not_called()
    assert hass.config_entries.async_get_entry(entry.entry_id) is None


async def test_a_blueprint_is_deleted_only_if_it_is_the_file_shown(
    hass: HomeAssistant, tmp_path: pathlib.Path
) -> None:
    hass.config.config_dir = str(tmp_path)
    DomainBlueprints(
        hass,
        "automation",
        logging.getLogger(__name__),
        lambda _hass, _path: False,
        AsyncMock(),
        BLUEPRINT_SCHEMA,
    )
    file = tmp_path / "blueprints" / "automation" / "me" / "lights.yaml"
    file.parent.mkdir(parents=True)
    file.write_text("blueprint: {name: Lights, domain: automation}\n")

    descriptor = await _card(hass, kind="blueprint", id="me/lights.yaml", domain="automation")
    file.write_text("blueprint: {name: Something else, domain: automation}\n")
    refused = await _confirm(hass, descriptor)

    assert "changed since it was shown" in refused.send_error.call_args.args[2]
    assert file.exists()

    descriptor = await _card(hass, kind="blueprint", id="me/lights.yaml", domain="automation")
    done = await _confirm(hass, descriptor)

    done.send_error.assert_not_called()
    assert not file.exists()


async def test_what_cannot_be_removed_is_refused_before_any_card(hass: HomeAssistant) -> None:
    _, _, entity_id = _speaker(hass)
    hass.states.async_set(entity_id, "playing")

    result = await _executor(hass).execute("remove_from_home", {"kind": "entity", "id": entity_id})

    assert "still provided" in result["error"]


def test_one_admin_tool_with_a_small_schema() -> None:
    import json

    tool = TOOL_MAP["remove_from_home"]
    assert tool.requires_admin and tool.large_context_only
    assert len(json.dumps(tool.to_anthropic())) < 2000


def test_the_health_and_removal_tools_are_in_both_lanes() -> None:
    """A model given a lane, not the full schema, still has them: "remove
    sensor.old" or "fix the Hue repair" falls through to command or config."""
    from custom_components.selora_ai.tool_registry import COMMAND_TOOL_NAMES, CONFIG_TOOL_NAMES

    for name in (
        "check_system",
        "reload_integration",
        "fix_repair",
        "ignore_repair",
        "remove_from_home",
    ):
        assert name in COMMAND_TOOL_NAMES, name
        assert name in CONFIG_TOOL_NAMES, name
