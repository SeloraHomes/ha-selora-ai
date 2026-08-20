"""Tests for the blueprint chat/MCP tools.

A blueprint is a parameterised automation template — one of the commonest ways a
home gets its automations, and previously invisible here. The valuable half is
that an automation can be BUILT from one: `use_blueprint` carries no triggers or
actions, which the automation validator rejected outright.
"""

from __future__ import annotations

import pathlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import CONFIG_TOOL_NAMES, TOOL_MAP

_BLUEPRINT_TOOLS = ("list_blueprints", "get_blueprint")


def _executor(hass: HomeAssistant) -> ToolExecutor:
    return ToolExecutor(hass, MagicMock(), is_admin=True)


def _blueprint(name: str, inputs: dict[str, Any]) -> MagicMock:
    bp = MagicMock()
    bp.name = name
    bp.inputs = inputs
    bp.metadata = {"description": f"{name} does a thing"}
    return bp


@pytest.fixture
def installed(hass: HomeAssistant) -> HomeAssistant:
    """One automation blueprint with a required and an optional input."""
    motion = _blueprint(
        "Motion light",
        {
            "motion_entity": {
                "name": "Motion sensor",
                "description": "What triggers it",
                "selector": {"entity": {"domain": "binary_sensor"}},
            },
            "no_motion_wait": {
                "name": "Wait time",
                "selector": {"number": {"min": 0, "max": 3600}},
                "default": 120,
            },
        },
    )
    store = MagicMock()
    store.async_get_blueprints = AsyncMock(return_value={"homeassistant/motion_light.yaml": motion})
    store.async_get_blueprint = AsyncMock(return_value=motion)
    hass.data["blueprint"] = {"automation": store}
    return hass


# ── Registration ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", _BLUEPRINT_TOOLS)
def test_blueprint_tools_are_registered_and_lane_reachable(name: str) -> None:
    assert name in TOOL_MAP
    assert name in CONFIG_TOOL_NAMES
    assert TOOL_MAP[name].large_context_only is True


@pytest.mark.parametrize("name", _BLUEPRINT_TOOLS)
def test_blueprint_reads_are_admin_gated(name: str) -> None:
    """Home Assistant decorates `blueprint/list` with require_admin. A blueprint
    carries its author's source URL and input defaults — config detail HA does
    not show a non-admin — so anything less here puts this tool surface below
    HA's own authorization boundary."""
    assert TOOL_MAP[name].requires_admin is True


@pytest.mark.parametrize("name", _BLUEPRINT_TOOLS)
async def test_a_non_admin_cannot_read_blueprints(installed: HomeAssistant, name: str) -> None:
    executor = ToolExecutor(installed, MagicMock(), is_admin=False)
    result = await executor.execute(name, {"domain": "automation", "path": "x.yaml"})
    assert "requires admin" in result["error"]


@pytest.mark.parametrize("name", _BLUEPRINT_TOOLS)
def test_every_blueprint_tool_reaches_mcp(name: str) -> None:
    from custom_components.selora_ai import mcp_server

    mcp_name = f"selora_{name}"
    assert any(t.name == mcp_name for t in mcp_server._TOOL_DEFINITIONS)
    assert mcp_name in mcp_server._get_tool_handlers()
    assert mcp_server._DERIVED_MCP_TOOLS[mcp_name] == name


@pytest.mark.parametrize("name", _BLUEPRINT_TOOLS)
def test_mcp_blueprint_access_matches_the_chat_definitions(name: str) -> None:
    """A read-only MCP credential must not reach them either."""
    from custom_components.selora_ai import mcp_server

    mcp_name = f"selora_{name}"
    assert mcp_name in mcp_server._ADMIN_TOOLS
    assert mcp_name not in mcp_server._READ_ONLY_TOOLS


# ── Read ────────────────────────────────────────────────────────────────────


async def test_blueprints_are_listed_with_their_input_names(installed: HomeAssistant) -> None:
    """A caller that knows a blueprint exists still cannot use it without
    knowing what it wants filled in."""
    result = await _executor(installed).execute("list_blueprints", {})

    assert result["count"] == 1
    entry = result["blueprints"][0]
    assert entry["domain"] == "automation"
    assert entry["path"] == "homeassistant/motion_light.yaml"
    assert entry["inputs"] == ["motion_entity", "no_motion_wait"]


async def test_a_blueprint_that_will_not_load_is_reported_not_dropped(
    hass: HomeAssistant,
) -> None:
    """The store reports a broken file as the exception rather than raising;
    dropping it leaves the user wondering where their file went."""
    store = MagicMock()
    store.async_get_blueprints = AsyncMock(return_value={"broken.yaml": ValueError("bad yaml")})
    hass.data["blueprint"] = {"automation": store}

    result = await _executor(hass).execute("list_blueprints", {})
    assert result["blueprints"][0]["path"] == "broken.yaml"
    assert "could not be loaded" in result["blueprints"][0]["error"]


async def test_no_blueprint_support_is_stated_not_silently_empty(hass: HomeAssistant) -> None:
    result = await _executor(hass).execute("list_blueprints", {})
    assert result["count"] == 0
    assert "not set up" in result["note"]


async def test_get_blueprint_reports_selectors_and_requiredness(
    installed: HomeAssistant,
) -> None:
    """The selector is the difference between a working automation and one HA
    rejects at reload; required-ness comes from the absence of a default."""
    result = await _executor(installed).execute(
        "get_blueprint", {"domain": "automation", "path": "homeassistant/motion_light.yaml"}
    )

    motion = result["inputs"]["motion_entity"]
    assert motion["required"] is True
    assert motion["selector"] == {"entity": {"domain": "binary_sensor"}}
    assert "default" not in motion

    wait = result["inputs"]["no_motion_wait"]
    assert wait["required"] is False
    assert wait["default"] == 120


async def test_an_unknown_domain_names_the_ones_that_exist(installed: HomeAssistant) -> None:
    result = await _executor(installed).execute(
        "get_blueprint", {"domain": "lights", "path": "x.yaml"}
    )
    assert "No blueprints for" in result["error"]


async def test_an_unreadable_blueprint_is_an_error_not_a_crash(
    installed: HomeAssistant,
) -> None:
    installed.data["blueprint"]["automation"].async_get_blueprint = AsyncMock(
        side_effect=FileNotFoundError("nope")
    )
    result = await _executor(installed).execute(
        "get_blueprint", {"domain": "automation", "path": "gone.yaml"}
    )
    assert "Could not read that blueprint" in result["error"]


# ── Building an automation from one ─────────────────────────────────────────


def test_a_blueprint_automation_validates() -> None:
    """It carries neither triggers nor actions — the blueprint supplies both —
    so requiring a trigger rejected every one of them."""
    from custom_components.selora_ai.automation_utils import validate_automation_payload

    ok, error, normalized = validate_automation_payload(
        {
            "alias": "Hallway motion",
            "use_blueprint": {
                "path": "homeassistant/motion_light.yaml",
                "input": {"motion_entity": "binary_sensor.hall"},
            },
        }
    )
    assert ok, error
    assert normalized["use_blueprint"]["path"] == "homeassistant/motion_light.yaml"
    assert "triggers" not in normalized
    assert "actions" not in normalized


def test_a_blueprint_automation_carries_its_outer_fields() -> None:
    """`id` is deliberately absent from `normalized` on BOTH paths — it is the
    writer's to assign, and `normalized` exists for risk assessment."""
    from custom_components.selora_ai.automation_utils import validate_automation_payload

    ok, _error, normalized = validate_automation_payload(
        {
            "alias": "Hallway motion",
            "id": "abc123",
            "mode": "restart",
            "description": "from a blueprint",
            "initial_state": False,
            "use_blueprint": {"path": "x.yaml", "input": {}},
        }
    )
    assert ok
    assert normalized["mode"] == "restart"
    assert normalized["description"] == "from a blueprint"
    assert normalized["initial_state"] is False
    assert "id" not in normalized


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [("SINGLE", "single"), ("garbage", "single"), ("  Restart  ", "restart"), (None, "single")],
)
async def test_a_blueprint_mode_is_normalized_like_any_other(
    supplied: str | None, expected: str
) -> None:
    """Copying it verbatim let `mode: "garbage"` reach the file, where HA
    rejects the automation at reload while the caller was told it succeeded."""
    from custom_components.selora_ai.automation_utils import validate_automation_payload

    payload: dict[str, Any] = {
        "alias": "A",
        "use_blueprint": {"path": "x.yaml", "input": {}},
    }
    if supplied is not None:
        payload["mode"] = supplied

    ok, _error, normalized = validate_automation_payload(payload)
    assert ok
    assert normalized["mode"] == expected


async def test_a_blueprint_payload_that_cannot_serialise_is_refused() -> None:
    """The round-trip check the ordinary path has always run."""
    from custom_components.selora_ai.automation_utils import validate_automation_payload

    ok, error, _ = validate_automation_payload(
        {"alias": "A", "use_blueprint": {"path": "x.yaml", "input": {"when": object()}}}
    )
    assert not ok
    assert "serialization failed" in error


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"alias": "A", "use_blueprint": "x.yaml"}, "must be an object"),
        ({"alias": "A", "use_blueprint": {}}, "requires a 'path'"),
        ({"alias": "A", "use_blueprint": {"path": "x", "input": []}}, "input must be an object"),
    ],
)
def test_a_malformed_use_blueprint_is_refused(payload: dict[str, Any], expected: str) -> None:
    """Accepting the key must not mean accepting anything under it."""
    from custom_components.selora_ai.automation_utils import validate_automation_payload

    ok, error, _ = validate_automation_payload(payload)
    assert not ok
    assert expected in error


def test_an_ordinary_automation_still_needs_a_trigger() -> None:
    """The blueprint branch must not become an escape hatch for every payload."""
    from custom_components.selora_ai.automation_utils import validate_automation_payload

    ok, error, _ = validate_automation_payload({"alias": "A", "actions": [{"action": "x.y"}]})
    assert not ok
    assert "trigger" in error


async def test_a_blueprint_payload_survives_the_write_normalizer() -> None:
    """prepare_write_payload indexed normalized["triggers"], so every blueprint
    automation raised KeyError before any YAML was written."""
    from custom_components.selora_ai.automation_utils import prepare_write_payload

    updated = {
        "alias": "Hallway motion",
        "use_blueprint": {"path": "x.yaml", "input": {"motion_entity": "binary_sensor.hall"}},
    }
    ok, error, normalized = await prepare_write_payload(None, updated)

    assert ok, error
    # Empty lists alongside use_blueprint are not a config HA accepts.
    assert "triggers" not in updated
    assert "actions" not in updated
    assert updated["use_blueprint"]["path"] == "x.yaml"
    assert normalized is not None


async def test_an_ordinary_payload_still_gets_plural_keys() -> None:
    """The blueprint branch must not skip normalization for everything else."""
    from custom_components.selora_ai.automation_utils import prepare_write_payload

    updated = {
        "alias": "A",
        "trigger": [{"platform": "state", "entity_id": "light.a"}],
        "action": [{"action": "light.turn_on"}],
    }
    ok, error, _ = await prepare_write_payload(None, updated)

    assert ok, error
    assert updated["triggers"]
    assert updated["actions"]
    assert "trigger" not in updated


async def test_a_blueprint_automation_is_written_as_a_blueprint_instance(
    hass: HomeAssistant,
) -> None:
    """Asserting success is worthless here: HA logs an invalid item at reload
    and the call still reports success, so the caller is told a working
    automation exists when the user has a broken one. Read the file."""
    import yaml

    from custom_components.selora_ai.automation_utils import async_create_automation

    result = await async_create_automation(
        hass,
        {
            "alias": "Hallway motion",
            "use_blueprint": {
                "path": "homeassistant/motion_light.yaml",
                "input": {"motion_entity": "binary_sensor.hall"},
            },
        },
    )
    assert result["success"] is True

    written = yaml.safe_load(
        (pathlib.Path(hass.config.config_dir) / "automations.yaml").read_text()
    )
    entry = next(a for a in written if a["alias"] == "Hallway motion")

    assert entry["use_blueprint"]["path"] == "homeassistant/motion_light.yaml"
    assert entry["use_blueprint"]["input"] == {"motion_entity": "binary_sensor.hall"}
    # Not even empty ones — HA merges these over the blueprint's own config.
    assert "triggers" not in entry
    assert "actions" not in entry
    assert "conditions" not in entry


async def test_an_ordinary_automation_is_still_written_with_its_actions(
    hass: HomeAssistant,
) -> None:
    """The blueprint branch must not strip the normal path's config."""
    import yaml

    from custom_components.selora_ai.automation_utils import async_create_automation

    hass.states.async_set("light.a", "off")
    await async_create_automation(
        hass,
        {
            "alias": "Plain",
            "triggers": [{"trigger": "state", "entity_id": "light.a"}],
            "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.a"}}],
        },
    )

    written = yaml.safe_load(
        (pathlib.Path(hass.config.config_dir) / "automations.yaml").read_text()
    )
    entry = next(a for a in written if a["alias"] == "Plain")
    assert entry["actions"]
    assert entry["triggers"]
    assert "use_blueprint" not in entry


@pytest.mark.parametrize("key", ["triggers", "actions", "conditions"])
async def test_a_blueprint_payload_drops_leftover_plural_keys(key: str) -> None:
    """Converting an existing automation in the YAML editor leaves them behind,
    and HA merges a surviving `actions` OVER the blueprint's substituted config
    — an empty list invalidates it, a populated one silently replaces it."""
    from custom_components.selora_ai.automation_utils import prepare_write_payload

    updated = {
        "alias": "Converted",
        "use_blueprint": {"path": "x.yaml", "input": {}},
        key: [] if key == "conditions" else [{"action": "light.turn_on"}],
    }
    ok, error, _ = await prepare_write_payload(None, updated)

    assert ok, error
    assert key not in updated


def test_a_blueprint_automation_is_not_claimed_to_be_assessed() -> None:
    """Its actions live in the blueprint file, so there is nothing here to
    inspect — but reporting a bare "normal" would claim we looked."""
    from custom_components.selora_ai.automation_utils import assess_automation_risk

    risk = assess_automation_risk({"alias": "A", "use_blueprint": {"path": "x.yaml", "input": {}}})
    assert "blueprint_unassessed" in risk["scrutiny_tags"]
    # A scrutiny tag, not a flag: a flag forces "elevated", which would land
    # every blueprint automation disabled.
    assert risk["level"] == "normal"


@pytest.mark.parametrize(
    "payload",
    [
        {"alias": " A ", "mode": "  Restart  ", "use_blueprint": {"path": "x.yaml"}},
        {
            "alias": " A ",
            "mode": "  Restart  ",
            "triggers": [{"trigger": "state", "entity_id": "light.a"}],
            "actions": [{"action": "light.turn_on"}],
        },
    ],
    ids=["blueprint", "ordinary"],
)
async def test_normalized_fields_reach_the_written_payload(payload: dict[str, Any]) -> None:
    """Normalizing a field validation then never applies is theatre: the value
    validated as "restart" and was written verbatim, and HA rejects the
    automation at reload. Both paths — nothing else copies them."""
    from custom_components.selora_ai.automation_utils import prepare_write_payload

    ok, error, _ = await prepare_write_payload(None, payload)

    assert ok, error
    assert payload["mode"] == "restart"
    assert payload["alias"] == "A"


async def test_the_save_still_owns_id_and_initial_state() -> None:
    """apply_managed_fields settles those; writing them here would fight it."""
    from custom_components.selora_ai.automation_utils import prepare_write_payload

    payload = {
        "alias": "A",
        "initial_state": True,
        "use_blueprint": {"path": "x.yaml"},
    }
    ok, _error, _ = await prepare_write_payload(None, payload)
    assert ok
    # Untouched by the normalizer, left for the save to settle.
    assert payload["initial_state"] is True
    assert "id" not in payload


async def test_a_script_blueprint_path_is_refused_for_an_automation(
    hass: HomeAssistant,
) -> None:
    """list_blueprints returns every domain by default, and automations.yaml's
    loader searches only the automation store — so a script blueprint's path
    writes an entry HA rejects at reload while the write itself succeeds."""
    from custom_components.selora_ai.automation_utils import async_create_automation

    automation_store = MagicMock()
    automation_store.async_get_blueprints = AsyncMock(
        return_value={"ha/motion.yaml": _blueprint("M", {})}
    )
    script_store = MagicMock()
    script_store.async_get_blueprints = AsyncMock(
        return_value={"ha/confirm.yaml": _blueprint("C", {})}
    )
    hass.data["blueprint"] = {"automation": automation_store, "script": script_store}

    result = await async_create_automation(
        hass,
        {"alias": "Nope", "use_blueprint": {"path": "ha/confirm.yaml", "input": {}}},
    )
    assert result["success"] is False

    # The config dir is shared across tests, so assert the ENTRY is absent
    # rather than the file.
    import yaml

    path = pathlib.Path(hass.config.config_dir) / "automations.yaml"
    written = yaml.safe_load(path.read_text()) if path.exists() else []
    assert not [a for a in (written or []) if a.get("alias") == "Nope"]


async def test_an_automation_blueprint_path_is_accepted(hass: HomeAssistant) -> None:
    from custom_components.selora_ai.automation_utils import async_create_automation

    store = MagicMock()
    store.async_get_blueprints = AsyncMock(return_value={"ha/motion.yaml": _blueprint("M", {})})
    hass.data["blueprint"] = {"automation": store}

    result = await async_create_automation(
        hass,
        {"alias": "Yes", "use_blueprint": {"path": "ha/motion.yaml", "input": {}}},
    )
    assert result["success"] is True


async def test_no_blueprint_store_does_not_block_the_write(hass: HomeAssistant) -> None:
    """A missing store is not evidence the path is wrong, and blocking every
    blueprint automation on it would be worse than the case it guards."""
    from custom_components.selora_ai.automation_utils import async_create_automation

    result = await async_create_automation(
        hass,
        {"alias": "Unverifiable", "use_blueprint": {"path": "ha/motion.yaml", "input": {}}},
    )
    assert result["success"] is True


def test_the_listing_says_only_automation_blueprints_can_be_used() -> None:
    """The default listing returns script and template blueprints too."""
    assert "domain is 'automation'" in TOOL_MAP["list_blueprints"].description


async def test_updating_onto_a_bad_blueprint_path_is_refused(hass: HomeAssistant) -> None:
    """Converting an existing automation through the YAML editor reaches the
    update path, not the create path — a bad path was written, rejected at
    reload, and reported as a success."""
    from custom_components.selora_ai.automation_utils import prepare_write_payload

    store = MagicMock()
    store.async_get_blueprints = AsyncMock(return_value={"ha/motion.yaml": _blueprint("M", {})})
    hass.data["blueprint"] = {"automation": store}

    ok, error, _ = await prepare_write_payload(
        hass, {"alias": "A", "use_blueprint": {"path": "ha/nope.yaml", "input": {}}}
    )
    assert not ok
    assert "not an automation blueprint" in error


async def test_updating_onto_a_good_blueprint_path_is_allowed(hass: HomeAssistant) -> None:
    from custom_components.selora_ai.automation_utils import prepare_write_payload

    store = MagicMock()
    store.async_get_blueprints = AsyncMock(return_value={"ha/motion.yaml": _blueprint("M", {})})
    hass.data["blueprint"] = {"automation": store}

    ok, error, _ = await prepare_write_payload(
        hass, {"alias": "A", "use_blueprint": {"path": "ha/motion.yaml", "input": {}}}
    )
    assert ok, error


@pytest.mark.parametrize("value", [ValueError("bad yaml"), None], ids=["exception", "none"])
async def test_a_listed_but_unloadable_blueprint_is_refused(
    hass: HomeAssistant, value: Any
) -> None:
    """The store reports a malformed or wrong-domain file as the exception in
    place of the blueprint, so membership alone accepts a path that cannot
    produce an automation."""
    from custom_components.selora_ai.automation_utils import async_create_automation

    store = MagicMock()
    store.async_get_blueprints = AsyncMock(return_value={"ha/broken.yaml": value})
    hass.data["blueprint"] = {"automation": store}

    result = await async_create_automation(
        hass, {"alias": "Broken", "use_blueprint": {"path": "ha/broken.yaml", "input": {}}}
    )
    assert result["success"] is False


async def test_a_null_input_is_written_as_a_mapping(hass: HomeAssistant) -> None:
    """`input:` with nothing under it is the natural YAML for a blueprint that
    takes none, and parses as None. HA's schema wants a mapping, so left alone
    it writes, reports success, and is rejected at reload."""
    import yaml

    from custom_components.selora_ai.automation_utils import async_create_automation

    result = await async_create_automation(
        hass,
        {"alias": "No inputs", "use_blueprint": {"path": "ha/simple.yaml", "input": None}},
    )
    assert result["success"] is True

    written = yaml.safe_load(
        (pathlib.Path(hass.config.config_dir) / "automations.yaml").read_text()
    )
    entry = next(a for a in written if a["alias"] == "No inputs")
    assert entry["use_blueprint"]["input"] == {}


def test_a_null_input_normalizes_in_the_validator() -> None:
    from custom_components.selora_ai.automation_utils import validate_automation_payload

    ok, error, normalized = validate_automation_payload(
        {"alias": "A", "use_blueprint": {"path": "x.yaml", "input": None}}
    )
    assert ok, error
    assert normalized["use_blueprint"]["input"] == {}


def test_a_non_mapping_input_is_still_refused() -> None:
    """Normalizing None must not become "accept anything"."""
    from custom_components.selora_ai.automation_utils import validate_automation_payload

    ok, error, _ = validate_automation_payload(
        {"alias": "A", "use_blueprint": {"path": "x.yaml", "input": ["a", "b"]}}
    )
    assert not ok
    assert "input must be an object" in error


@pytest.mark.parametrize("supplied", [None, {"motion": "binary_sensor.hall"}])
async def test_the_update_path_writes_the_normalized_blueprint(
    hass: HomeAssistant, supplied: Any
) -> None:
    """The create path normalizes `input: null` to {}; the update path returned
    without copying it back, so the writer persisted the null and HA rejected
    the automation at reload while the save reported success."""
    from custom_components.selora_ai.automation_utils import prepare_write_payload

    updated = {"alias": "A", "use_blueprint": {"path": "x.yaml", "input": supplied}}
    ok, error, _ = await prepare_write_payload(None, updated)

    assert ok, error
    assert updated["use_blueprint"]["input"] == (supplied if supplied else {})
    assert updated["use_blueprint"]["input"] is not None
