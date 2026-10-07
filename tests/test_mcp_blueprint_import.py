"""Tests for importing a blueprint from a URL and deleting one, over MCP.

The store is Home Assistant's own ``DomainBlueprints``, writing to a private
directory; only the network fetch is stubbed.
"""

from __future__ import annotations

import logging
import pathlib
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.components.blueprint.importer import ImportedBlueprint
from homeassistant.components.blueprint.models import Blueprint, DomainBlueprints
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.core import HomeAssistant
import pytest
import voluptuous as vol

from custom_components.selora_ai import blueprint_import
from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import (
    TOOL_DELETE_BLUEPRINT,
    TOOL_IMPORT_BLUEPRINT,
)

URL = "https://github.com/someone/blueprints/blob/main/motion_light.yaml"
YAML = """\
blueprint:
  name: Motion light
  description: Turn a light on when motion is detected.
  domain: automation
  input:
    motion: {name: Motion sensor}
    light: {name: Light}
triggers:
  - trigger: state
    entity_id: !input motion
    to: "on"
actions:
  - action: light.turn_on
    target:
      entity_id: !input light
"""


def _imported(text: str = YAML) -> ImportedBlueprint:
    from homeassistant.util import yaml as yaml_util

    blueprint = Blueprint(yaml_util.parse_yaml(text), schema=BLUEPRINT_SCHEMA)
    return ImportedBlueprint("someone/motion_light", text, blueprint)


@pytest.fixture
def store(hass: HomeAssistant, tmp_path: pathlib.Path) -> Any:
    hass.config.config_dir = str(tmp_path)
    in_use: set[str] = set()
    domain_store = DomainBlueprints(
        hass,
        "automation",
        logging.getLogger(__name__),
        lambda _hass, path: path in in_use,
        AsyncMock(),
        BLUEPRINT_SCHEMA,
    )
    domain_store.in_use = in_use
    return domain_store


async def _mcp(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[tool](hass, arguments)


async def test_an_import_is_previewed_then_saved_once_confirmed(
    hass: HomeAssistant, store: Any, tmp_path: pathlib.Path
) -> None:
    saved = tmp_path / "blueprints" / "automation" / "someone" / "motion_light.yaml"
    with patch.object(blueprint_import, "_fetch", AsyncMock(return_value=_imported())):
        preview = await _mcp(hass, TOOL_IMPORT_BLUEPRINT, url=URL)
        assert preview["requires_confirmation"] is True
        assert preview["name"] == "Motion light"
        assert preview["inputs"] == ["light", "motion"]
        assert not saved.exists()

        result = await _mcp(
            hass,
            TOOL_IMPORT_BLUEPRINT,
            url=URL,
            confirmed=True,
            content_hash=preview["content_hash"],
        )

    assert result["status"] == "imported", result
    assert saved.exists()
    assert (await store.async_get_blueprint("someone/motion_light.yaml")).name == "Motion light"


async def test_only_the_blueprint_shown_is_saved(hass: HomeAssistant, store: Any) -> None:
    with patch.object(blueprint_import, "_fetch", AsyncMock(return_value=_imported())):
        preview = await _mcp(hass, TOOL_IMPORT_BLUEPRINT, url=URL)
    changed = YAML.replace("Motion light", "Motion light (now with more)")
    with patch.object(blueprint_import, "_fetch", AsyncMock(return_value=_imported(changed))):
        result = await _mcp(
            hass,
            TOOL_IMPORT_BLUEPRINT,
            url=URL,
            confirmed=True,
            content_hash=preview["content_hash"],
        )

    assert "not the one shown" in result["error"]


async def test_an_existing_blueprint_is_not_overwritten(hass: HomeAssistant, store: Any) -> None:
    with patch.object(blueprint_import, "_fetch", AsyncMock(return_value=_imported())):
        preview = await _mcp(hass, TOOL_IMPORT_BLUEPRINT, url=URL)
        await _mcp(
            hass,
            TOOL_IMPORT_BLUEPRINT,
            url=URL,
            confirmed=True,
            content_hash=preview["content_hash"],
        )
        again = await _mcp(hass, TOOL_IMPORT_BLUEPRINT, url=URL)

    assert "already exists" in again["error"]


async def test_a_url_its_host_reader_does_not_know_is_refused(
    hass: HomeAssistant, store: Any
) -> None:
    """Not handed on to the generic reader, which follows redirects anywhere."""
    with patch(
        "homeassistant.components.blueprint.importer.fetch_blueprint_from_generic_url"
    ) as generic:
        result = await _mcp(hass, TOOL_IMPORT_BLUEPRINT, url="https://github.com/not-a-blueprint")

    assert "not a blueprint Home Assistant can import" in result["error"]
    generic.assert_not_called()


async def test_a_blueprint_its_domain_would_reject_is_not_saved(
    hass: HomeAssistant, store: Any
) -> None:
    def _strict(data: Any) -> Any:
        if "actions" not in data:
            raise vol.Invalid("an automation blueprint needs actions")
        return BLUEPRINT_SCHEMA(data)

    store._blueprint_schema = _strict
    no_actions = YAML.split("actions:")[0]
    with patch.object(blueprint_import, "_fetch", AsyncMock(return_value=_imported(no_actions))):
        result = await _mcp(hass, TOOL_IMPORT_BLUEPRINT, url=URL)

    assert "not a usable automation blueprint" in result["error"]


async def test_a_save_the_disk_refuses_is_reported(hass: HomeAssistant, store: Any) -> None:
    with patch.object(blueprint_import, "_fetch", AsyncMock(return_value=_imported())):
        preview = await _mcp(hass, TOOL_IMPORT_BLUEPRINT, url=URL)
        with patch.object(
            store, "async_add_blueprint", AsyncMock(side_effect=OSError("read-only"))
        ):
            result = await _mcp(
                hass,
                TOOL_IMPORT_BLUEPRINT,
                url=URL,
                confirmed=True,
                content_hash=preview["content_hash"],
            )

    assert "not saved" in result["error"]
    assert len(preview["content_hash"]) == 64


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/x/y/blob/main/b.yaml",
        "https://router.lan/blueprint.yaml",
        "https://192.168.1.1/blueprint.yaml",
        "https://example.com/b.yaml",
    ],
)
async def test_only_known_hosts_over_https_are_fetched(
    hass: HomeAssistant, store: Any, url: str
) -> None:
    fetch = AsyncMock(return_value=_imported())
    with patch.object(blueprint_import, "_fetch", fetch):
        result = await _mcp(hass, TOOL_IMPORT_BLUEPRINT, url=url)

    assert "community forum, GitHub" in result["error"]
    fetch.assert_not_awaited()


async def test_a_failed_download_is_an_import_error(hass: HomeAssistant, store: Any) -> None:
    import aiohttp

    failing = AsyncMock(side_effect=aiohttp.ClientError("404, message='Not Found'"))
    with patch.object(blueprint_import, "_fetch", failing):
        result = await _mcp(hass, TOOL_IMPORT_BLUEPRINT, url=URL)

    assert "not a blueprint Home Assistant can import" in result["error"]


async def test_a_broken_blueprint_file_can_be_deleted(
    hass: HomeAssistant, store: Any, tmp_path: pathlib.Path
) -> None:
    broken = tmp_path / "blueprints" / "automation" / "someone" / "broken.yaml"
    broken.parent.mkdir(parents=True)
    broken.write_text("blueprint: [not, a, mapping]\n")

    asked = await _mcp(hass, TOOL_DELETE_BLUEPRINT, domain="automation", path="someone/broken.yaml")
    deleted = await _mcp(
        hass,
        TOOL_DELETE_BLUEPRINT,
        domain="automation",
        path="someone/broken.yaml",
        confirmed=True,
    )
    escape = await _mcp(hass, TOOL_DELETE_BLUEPRINT, domain="automation", path="../../x.yaml")

    assert asked["requires_confirmation"] is True
    assert deleted["status"] == "deleted", deleted
    assert not broken.exists()
    assert "No blueprint" in escape["error"]


async def test_a_blueprint_is_deleted_once_confirmed_unless_in_use(
    hass: HomeAssistant, store: Any, tmp_path: pathlib.Path
) -> None:
    with patch.object(blueprint_import, "_fetch", AsyncMock(return_value=_imported())):
        preview = await _mcp(hass, TOOL_IMPORT_BLUEPRINT, url=URL)
        await _mcp(
            hass,
            TOOL_IMPORT_BLUEPRINT,
            url=URL,
            confirmed=True,
            content_hash=preview["content_hash"],
        )
    path = "someone/motion_light.yaml"

    store.in_use.add(path)
    with patch.object(blueprint_import, "_users", return_value=["automation.hall_light"]):
        refused = await _mcp(hass, TOOL_DELETE_BLUEPRINT, domain="automation", path=path)
    store.in_use.clear()
    asked = await _mcp(hass, TOOL_DELETE_BLUEPRINT, domain="automation", path=path)
    deleted = await _mcp(
        hass, TOOL_DELETE_BLUEPRINT, domain="automation", path=path, confirmed=True
    )

    assert "automation.hall_light" in refused["error"]
    assert asked["requires_confirmation"] is True
    assert deleted["status"] == "deleted", deleted
    assert not (tmp_path / "blueprints" / "automation" / path).exists()


async def test_importing_and_deleting_need_admin() -> None:
    assert TOOL_IMPORT_BLUEPRINT in mcp_access._ADMIN_TOOLS
    assert TOOL_DELETE_BLUEPRINT in mcp_access._ADMIN_TOOLS


async def test_a_malformed_host_is_not_a_url(hass: HomeAssistant, store: Any) -> None:
    result = await _mcp(hass, TOOL_IMPORT_BLUEPRINT, url="https://xn--.com/b.yaml")

    assert "error" in result


@pytest.mark.parametrize(
    ("domain", "finder"),
    [
        ("automation", "homeassistant.components.automation.automations_with_blueprint"),
        ("script", "homeassistant.components.script.scripts_with_blueprint"),
        ("template", "homeassistant.components.template.helpers.templates_with_blueprint"),
    ],
)
def test_what_uses_a_blueprint_is_asked_of_its_domain(
    hass: HomeAssistant, domain: str, finder: str
) -> None:
    """Each lookup is imported where Home Assistant defines it — a wrong path
    would silently report the blueprint unused."""
    with patch(finder, return_value=[f"{domain}.user"]):
        assert blueprint_import._users(hass, domain, "x.yaml") == [f"{domain}.user"]


async def test_a_url_naming_a_file_outside_the_folder_is_refused(
    hass: HomeAssistant, store: Any, tmp_path: pathlib.Path
) -> None:
    """GitHub's importer decodes `%2F` in the last segment, so `..%2F` arrives
    as `../`: the file would land outside the blueprints folder."""
    imported = _imported()
    imported = ImportedBlueprint(
        "someone/../../../../packages/evil", imported.raw_data, imported.blueprint
    )
    with patch.object(blueprint_import, "_fetch", AsyncMock(return_value=imported)):
        result = await _mcp(hass, TOOL_IMPORT_BLUEPRINT, url=URL)

    assert "outside the blueprints folder" in result["error"]
    assert not (tmp_path / "packages").exists()
