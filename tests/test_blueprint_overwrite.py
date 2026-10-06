"""Replacing a saved blueprint with its author's new version.

An import onto an existing path was refused with "delete it first", and a
blueprint in use cannot be deleted — so an automation built on one could never
get its update. Overwriting now asks, names what uses it and what the new
version would break, and refuses while anything would. Home Assistant's own
blueprint store, on a private directory; only the download is stubbed.
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai import blueprint_import
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import TOOL_IMPORT_BLUEPRINT

from .test_mcp_blueprint_import import URL, YAML, _imported, store  # noqa: F401

PATH = "someone/motion_light.yaml"
# The new version: another input, with a default — existing users still load.
V2 = YAML.replace(
    "    light: {name: Light}\n",
    "    light: {name: Light}\n    brightness: {name: Brightness, default: 80}\n",
)
# A version requiring an input nobody sets yet.
V3 = YAML.replace(
    "    light: {name: Light}\n", "    light: {name: Light}\n    delay: {name: Delay}\n"
)


async def _mcp(hass: HomeAssistant, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[TOOL_IMPORT_BLUEPRINT](
        hass, {"url": URL, **arguments}
    )


async def _import(hass: HomeAssistant, text: str, **arguments: Any) -> dict[str, Any]:
    with patch.object(blueprint_import, "_fetch", AsyncMock(return_value=_imported(text))):
        return await _mcp(hass, **arguments)


@pytest.fixture
async def saved(hass: HomeAssistant, store: Any) -> Any:  # noqa: F811
    preview = await _import(hass, YAML)
    await _import(hass, YAML, confirmed=True, content_hash=preview["content_hash"])
    # One automation built on it, setting the two inputs v1 asks for.
    hall = SimpleNamespace(
        _blueprint_inputs={
            "use_blueprint": {
                "path": PATH,
                "input": {"motion": "binary_sensor.hall", "light": "light.hall"},
            }
        }
    )
    hass.data["automation"] = SimpleNamespace(get_entity=lambda _eid: hall)
    with patch.object(blueprint_import, "_users", return_value=["automation.hall"]):
        yield store


def _on_disk(tmp_path: pathlib.Path) -> str:
    return (tmp_path / "blueprints" / "automation" / PATH).read_text()


async def test_replacing_needs_to_be_asked_for(hass: HomeAssistant, saved: Any) -> None:
    result = await _import(hass, V2)

    assert "overwrite=true" in result["error"]


async def test_a_new_version_replaces_the_old_and_its_users_reload(
    hass: HomeAssistant, saved: Any, tmp_path: pathlib.Path
) -> None:
    preview = await _import(hass, V2, overwrite=True)
    result = await _import(
        hass,
        V2,
        overwrite=True,
        confirmed=True,
        content_hash=preview["content_hash"],
        replaces_hash=preview["replaces"]["file_hash"],
    )

    assert preview["replaces"]["used_by"] == ["automation.hall"]
    assert preview["replaces"]["would_break"] == []
    assert result["status"] == "replaced", result
    assert "brightness" in _on_disk(tmp_path)
    saved._reload_blueprint_consumers.assert_awaited()


async def test_a_version_that_would_break_a_user_is_not_saved(
    hass: HomeAssistant, saved: Any, tmp_path: pathlib.Path
) -> None:
    preview = await _import(hass, V3, overwrite=True)
    result = await _import(
        hass,
        V3,
        overwrite=True,
        confirmed=True,
        content_hash=preview["content_hash"],
        replaces_hash=preview["replaces"]["file_hash"],
    )

    assert preview["replaces"]["would_break"] == [
        {"entity_id": "automation.hall", "missing_inputs": ["delay"]}
    ]
    assert "do not set" in result["error"]
    assert "delay" not in _on_disk(tmp_path)


async def test_a_file_changed_since_the_preview_is_not_replaced(
    hass: HomeAssistant, saved: Any, tmp_path: pathlib.Path
) -> None:
    preview = await _import(hass, V2, overwrite=True)
    path = tmp_path / "blueprints" / "automation" / PATH
    path.write_text(_on_disk(tmp_path) + "# edited by hand\n")

    result = await _import(
        hass,
        V2,
        overwrite=True,
        confirmed=True,
        content_hash=preview["content_hash"],
        replaces_hash=preview["replaces"]["file_hash"],
    )

    assert "not the one shown" in result["error"]
    assert "edited by hand" in _on_disk(tmp_path)


async def test_a_real_automation_built_on_it_is_checked(
    hass: HomeAssistant, tmp_path: pathlib.Path
) -> None:
    """A blueprint automation's `raw_config` is the expanded one; its inputs
    as written live elsewhere. Nothing faked here but the download."""
    from homeassistant.setup import async_setup_component

    hass.config.config_dir = str(tmp_path)
    blueprint_file = tmp_path / "blueprints" / "automation" / PATH
    blueprint_file.parent.mkdir(parents=True)
    blueprint_file.write_text(YAML)
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "id": "hall",
                "alias": "Hall motion",
                "use_blueprint": {
                    "path": PATH,
                    "input": {"motion": "binary_sensor.hall", "light": "light.hall"},
                },
            }
        },
    )
    await hass.async_block_till_done()

    preview = await _import(hass, V3, overwrite=True)

    assert preview["replaces"]["used_by"] == ["automation.hall_motion"]
    assert preview["replaces"]["would_break"] == [
        {"entity_id": "automation.hall_motion", "missing_inputs": ["delay"]}
    ]


async def test_a_user_whose_inputs_cannot_be_read_blocks_it(
    hass: HomeAssistant, saved: Any
) -> None:
    hass.data["automation"] = SimpleNamespace(get_entity=lambda _eid: SimpleNamespace())

    preview = await _import(hass, V2, overwrite=True)
    result = await _import(
        hass,
        V2,
        overwrite=True,
        confirmed=True,
        content_hash=preview["content_hash"],
        replaces_hash=preview["replaces"]["file_hash"],
    )

    assert preview["replaces"]["would_break"] == [
        {"entity_id": "automation.hall", "unchecked": True}
    ]
    assert "could not be read" in result["error"]
