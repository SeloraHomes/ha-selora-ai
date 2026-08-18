"""Tests for the area / entity / device registry chat tools.

These drive the REAL Home Assistant registries against a real ``hass``, so a
change to HA's registry API (``async_update_entity`` keyword set, alias types,
floor auto-creation) fails here rather than silently at runtime.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers import (
    floor_registry as fr,
)
import pytest

from custom_components.selora_ai import registry_manager as rm
from custom_components.selora_ai.llm_client.intent import _is_config_request
from custom_components.selora_ai.mcp_server import _preview_delete_area
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import (
    CHAT_TOOLS,
    CONFIG_TOOL_NAMES,
    TOOL_LANES,
    TOOL_MAP,
)

_REGISTRY_TOOL_NAMES = (
    "list_areas",
    "assign_area",
    "create_area",
    "update_area",
    "delete_area",
    "update_entity",
    "update_device",
    "list_services",
)


def _make_executor(hass: HomeAssistant, *, is_admin: bool = True) -> ToolExecutor:
    return ToolExecutor(hass, MagicMock(), is_admin=is_admin)


@pytest.fixture
async def registry_home(hass: HomeAssistant) -> HomeAssistant:
    """A hass with two areas, a device in one, and a few entities."""
    area_reg = ar.async_get(hass)
    area_reg.async_create("Living Room")
    area_reg.async_create("Bedroom")

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    # A real MockConfigEntry, not a MagicMock: every attribute of a MagicMock is
    # itself truthy, so ``config_entry.disabled_by`` reads as set and HA marks
    # every device it owns ``DeviceEntryDisabler.CONFIG_ENTRY``. That is
    # invisible until a test looks at ``disabled_by`` — and then it fails on
    # some interpreter/HA combinations and not others, because whether the flag
    # is computed at creation depends on internals no test should depend on.
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    config_entry = MockConfigEntry(domain="demo", entry_id="test_entry")
    config_entry.add_to_hass(hass)

    device = dev_reg.async_get_or_create(
        config_entry_id="test_entry",
        identifiers={("demo", "lamp-1")},
        name="Hallway Lamp",
    )
    ent_reg.async_get_or_create(
        "light", "demo", "lamp-1-main", device_id=device.id, suggested_object_id="hallway_lamp"
    )
    ent_reg.async_get_or_create("light", "demo", "standalone", suggested_object_id="floor_lamp")
    hass.states.async_set("light.hallway_lamp", "off")
    hass.states.async_set("light.floor_lamp", "off")
    return hass


# ── Registration ────────────────────────────────────────────────────────────


def test_registry_tools_are_registered() -> None:
    """Every registry tool is in CHAT_TOOLS and reachable by name."""
    names = {t.name for t in CHAT_TOOLS}
    for tool in _REGISTRY_TOOL_NAMES:
        assert tool in names, f"{tool} missing from CHAT_TOOLS"
        assert tool in TOOL_MAP


def test_write_tools_require_admin() -> None:
    """Read tools stay open; every mutating registry tool is admin-gated."""
    for tool in ("list_areas", "list_services"):
        assert TOOL_MAP[tool].requires_admin is False
    for tool in (
        "assign_area",
        "create_area",
        "update_area",
        "delete_area",
        "update_entity",
        "update_device",
    ):
        assert TOOL_MAP[tool].requires_admin is True


def test_registry_tools_are_large_context_only() -> None:
    """The on-device model must never be handed registry surgery."""
    for tool in _REGISTRY_TOOL_NAMES:
        assert TOOL_MAP[tool].large_context_only is True


def test_registry_tools_are_in_the_config_lane() -> None:
    """A trimmed config turn must still see every registry tool.

    Guards the failure this whole lane exists to prevent: a config request
    falls through to a trimmed schema and the tool it needs is not in it.
    """
    for tool in _REGISTRY_TOOL_NAMES:
        assert tool in CONFIG_TOOL_NAMES
    assert TOOL_LANES["config"] is CONFIG_TOOL_NAMES


async def test_executor_rejects_non_admin(registry_home: HomeAssistant) -> None:
    executor = _make_executor(registry_home, is_admin=False)
    result = await executor.execute("assign_area", {"area": "Bedroom"})
    assert "admin" in result["error"]


# ── Config-intent detection ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "Assign the Living Room Lights to the Living Room",
        "rename this to Reading Lamp",
        "add an alias for the kettle",
        "hide that sensor",
        "expose the porch light to Assist",
        "create a new area called Study",
        "what rooms do I have?",
        "move the lamp to the upstairs floor",
    ],
)
def test_config_requests_are_detected(message: str) -> None:
    assert _is_config_request(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "turn off the kitchen light",
        "lock the front door",
        "is the bedroom warm?",
        "turn on the lights in the living room",
        "set the thermostat to 21",
    ],
)
def test_device_commands_are_not_config(message: str) -> None:
    """A false positive strips execute_command from a real command turn."""
    assert _is_config_request(message) is False


def test_known_area_name_rescues_a_bare_placement() -> None:
    """ "Move the lamp to the Study" has no area noun — the registry supplies it."""
    assert _is_config_request("move the desk lamp to the Study") is False
    assert _is_config_request("move the desk lamp to the Study", ["Study", "Bedroom"]) is True


# ── Areas ───────────────────────────────────────────────────────────────────


async def test_list_areas_reports_counts(registry_home: HomeAssistant) -> None:
    result = await _make_executor(registry_home).execute("list_areas", {})
    names = {a["name"] for a in result["areas"]}
    assert {"Living Room", "Bedroom"} <= names
    assert result["area_count"] == len(result["areas"])


async def test_create_area_is_idempotent_by_name(registry_home: HomeAssistant) -> None:
    """A duplicate name reports the existing area instead of creating a twin."""
    executor = _make_executor(registry_home)
    first = await executor.execute("create_area", {"name": "Study"})
    assert first["status"] == "created"

    again = await executor.execute("create_area", {"name": "study"})
    assert again["status"] == "exists"
    assert again["area_id"] == first["area_id"]


async def test_create_area_creates_a_missing_floor(registry_home: HomeAssistant) -> None:
    result = await _make_executor(registry_home).execute(
        "create_area", {"name": "Attic", "floor": "Top Floor"}
    )
    assert result["created_floor"] == "Top Floor"
    assert fr.async_get(registry_home).async_get_floor_by_name("Top Floor") is not None


async def test_update_area_refuses_a_name_collision(registry_home: HomeAssistant) -> None:
    result = await _make_executor(registry_home).execute(
        "update_area", {"area": "Bedroom", "new_name": "Living Room"}
    )
    assert "already exists" in result["error"]


async def test_update_area_ignores_blank_optionals(registry_home: HomeAssistant) -> None:
    """An empty string must not blank the area's name."""
    result = await _make_executor(registry_home).execute(
        "update_area", {"area": "Bedroom", "new_name": "", "icon": ""}
    )
    assert result["status"] == "unchanged"
    assert ar.async_get(registry_home).async_get_area_by_name("Bedroom") is not None


async def test_unknown_area_error_lists_the_real_ones(registry_home: HomeAssistant) -> None:
    result = await _make_executor(registry_home).execute(
        "assign_area", {"area": "Lounge", "entity_ids": ["light.floor_lamp"]}
    )
    assert "Living Room" in result["error"]
    assert "Bedroom" in result["error"]


# ── Assignment ──────────────────────────────────────────────────────────────


async def test_assign_entity_to_area(registry_home: HomeAssistant) -> None:
    result = await _make_executor(registry_home).execute(
        "assign_area", {"area": "Bedroom", "entity_ids": ["light.floor_lamp"]}
    )
    assert result["status"] == "assigned"
    assert result["entities_assigned"] == ["light.floor_lamp"]

    entry = er.async_get(registry_home).async_get("light.floor_lamp")
    area = ar.async_get(registry_home).async_get_area_by_name("Bedroom")
    assert entry.area_id == area.id


async def test_assign_accepts_a_bare_string_for_an_array_param(
    registry_home: HomeAssistant,
) -> None:
    """Models emit a string where the schema says array; refusing helps nobody."""
    result = await _make_executor(registry_home).execute(
        "assign_area", {"area": "Bedroom", "entity_ids": "light.floor_lamp"}
    )
    assert result["entities_assigned"] == ["light.floor_lamp"]


async def test_assign_clears_the_override_when_the_device_already_matches(
    registry_home: HomeAssistant,
) -> None:
    """The entity should INHERIT, not get pinned to the same area.

    A stored override outlives the coincidence: moving the device later would
    otherwise strand this entity in the old area with nothing to explain it.
    """
    area = ar.async_get(registry_home).async_get_area_by_name("Living Room")
    dev_reg = dr.async_get(registry_home)
    device = next(iter(dev_reg.devices.values()))
    dev_reg.async_update_device(device.id, area_id=area.id)

    ent_reg = er.async_get(registry_home)
    ent_reg.async_update_entity("light.hallway_lamp", area_id=area.id)

    result = await _make_executor(registry_home).execute(
        "assign_area", {"area": "Living Room", "entity_ids": ["light.hallway_lamp"]}
    )
    assert result["entities_now_inheriting"] == ["light.hallway_lamp"]
    assert ent_reg.async_get("light.hallway_lamp").area_id is None


async def test_assign_device_moves_it_and_reports_carried_entities(
    registry_home: HomeAssistant,
) -> None:
    dev_reg = dr.async_get(registry_home)
    device = next(iter(dev_reg.devices.values()))

    result = await _make_executor(registry_home).execute(
        "assign_area", {"area": "Bedroom", "device_ids": [device.id]}
    )
    assert result["devices_moved"] == [device.id]
    assert result["entities_carried_with_devices"] == 1

    area = ar.async_get(registry_home).async_get_area_by_name("Bedroom")
    assert dev_reg.async_get(device.id).area_id == area.id


async def test_assign_reports_unknown_entities_without_failing_the_batch(
    registry_home: HomeAssistant,
) -> None:
    result = await _make_executor(registry_home).execute(
        "assign_area",
        {"area": "Bedroom", "entity_ids": ["light.floor_lamp", "light.does_not_exist"]},
    )
    assert result["entities_assigned"] == ["light.floor_lamp"]
    assert result["failed"][0]["entity_id"] == "light.does_not_exist"


async def test_assign_requires_a_target(registry_home: HomeAssistant) -> None:
    result = await _make_executor(registry_home).execute("assign_area", {"area": "Bedroom"})
    assert "at least one" in result["error"]


# ── Entities ────────────────────────────────────────────────────────────────


async def test_update_entity_renames_the_friendly_name(registry_home: HomeAssistant) -> None:
    result = await _make_executor(registry_home).execute(
        "update_entity", {"entity_id": "light.floor_lamp", "new_name": "Reading Lamp"}
    )
    assert result["status"] == "updated"
    assert er.async_get(registry_home).async_get("light.floor_lamp").name == "Reading Lamp"


async def test_update_entity_toggles_hidden_both_ways(registry_home: HomeAssistant) -> None:
    """``False`` is a real request here, unlike a blank string."""
    executor = _make_executor(registry_home)
    ent_reg = er.async_get(registry_home)

    await executor.execute("update_entity", {"entity_id": "light.floor_lamp", "hidden": True})
    assert ent_reg.async_get("light.floor_lamp").hidden_by is er.RegistryEntryHider.USER

    await executor.execute("update_entity", {"entity_id": "light.floor_lamp", "hidden": False})
    assert ent_reg.async_get("light.floor_lamp").hidden_by is None


async def test_update_entity_refuses_a_cross_domain_id_change(
    registry_home: HomeAssistant,
) -> None:
    result = await _make_executor(registry_home).execute(
        "update_entity",
        {"entity_id": "light.floor_lamp", "new_entity_id": "switch.floor_lamp"},
    )
    assert "domain" in result["error"]


async def test_entity_id_rename_asks_before_renaming(registry_home: HomeAssistant) -> None:
    """An entity_id rename breaks references HA will not rewrite — it asks first."""
    result = await _make_executor(registry_home).execute(
        "update_entity",
        {"entity_id": "light.floor_lamp", "new_entity_id": "light.reading_lamp"},
    )
    assert result["requires_approval"] is True
    assert result["destructive"]["verb"] == "rename_id"
    # The card is a question — nothing has moved yet.
    assert er.async_get(registry_home).async_get("light.floor_lamp") is not None


async def test_confirming_a_rename_applies_it(registry_home: HomeAssistant) -> None:
    """The confirm path replays the held arguments through the same code."""
    from custom_components.selora_ai.mcp_server import _tool_update_entity

    held = await _make_executor(registry_home).execute(
        "update_entity",
        {"entity_id": "light.floor_lamp", "new_entity_id": "light.reading_lamp"},
    )
    assert "payload" not in held["destructive"]
    result = await _tool_update_entity(
        registry_home,
        {"entity_id": "light.floor_lamp", "new_entity_id": "light.reading_lamp"},
    )
    assert result["entity_id"] == "light.reading_lamp"
    assert result["previous_entity_id"] == "light.floor_lamp"


async def test_update_entity_rejects_a_yaml_entity(registry_home: HomeAssistant) -> None:
    """A state with no registry entry cannot be renamed this way."""
    registry_home.states.async_set("sensor.yaml_only", "5")
    result = await _make_executor(registry_home).execute(
        "update_entity", {"entity_id": "sensor.yaml_only", "new_name": "Nope"}
    )
    assert "entity registry" in result["error"]


async def test_update_entity_noop_when_nothing_asked(registry_home: HomeAssistant) -> None:
    result = await _make_executor(registry_home).execute(
        "update_entity", {"entity_id": "light.floor_lamp", "new_name": "", "aliases": []}
    )
    assert result["status"] == "unchanged"


# ── Devices ─────────────────────────────────────────────────────────────────


async def test_update_device_renames_by_name(registry_home: HomeAssistant) -> None:
    result = await _make_executor(registry_home).execute(
        "update_device", {"device": "Hallway Lamp", "new_name": "Entry Lamp"}
    )
    assert result["status"] == "updated"
    dev_reg = dr.async_get(registry_home)
    device = next(iter(dev_reg.devices.values()))
    assert device.name_by_user == "Entry Lamp"
    assert device.name == "Hallway Lamp"


async def test_update_device_moves_area_and_counts_entities(
    registry_home: HomeAssistant,
) -> None:
    result = await _make_executor(registry_home).execute(
        "update_device", {"device": "Hallway Lamp", "area": "Bedroom"}
    )
    assert result["area"] == "Bedroom"
    assert result["entities_moved"] == 1


async def test_update_device_unknown_name(registry_home: HomeAssistant) -> None:
    result = await _make_executor(registry_home).execute(
        "update_device", {"device": "No Such Device", "new_name": "X"}
    )
    assert "No device named" in result["error"]


# ── Delete confirmation ─────────────────────────────────────────────────────


async def test_delete_area_returns_an_approval_card(registry_home: HomeAssistant) -> None:
    """The chat path never deletes directly — it asks."""
    await _make_executor(registry_home).execute(
        "assign_area", {"area": "Bedroom", "entity_ids": ["light.floor_lamp"]}
    )
    result = await _make_executor(registry_home).execute("delete_area", {"area": "Bedroom"})

    assert result["requires_approval"] is True
    assert result["delete"]["kind"] == "area"
    assert result["delete"]["name"] == "Bedroom"
    # Blast radius has to be on the card: the tool loop discards the model's prose.
    assert "holds" in result["delete"]["label"]
    # Still there — the card is a question, not the deletion.
    assert ar.async_get(registry_home).async_get_area_by_name("Bedroom") is not None


async def test_preview_delete_area_reports_an_unknown_area(
    registry_home: HomeAssistant,
) -> None:
    result = await _preview_delete_area(registry_home, {"area": "Nowhere"})
    assert "error" in result


async def test_delete_area_unassigns_rather_than_deletes(
    registry_home: HomeAssistant,
) -> None:
    """Confirming the card leaves the entity alive, just homeless."""
    await _make_executor(registry_home).execute(
        "assign_area", {"area": "Bedroom", "entity_ids": ["light.floor_lamp"]}
    )
    area = ar.async_get(registry_home).async_get_area_by_name("Bedroom")

    result = await rm.async_delete_area(registry_home, area.id)
    assert result["status"] == "deleted"
    assert result["unassigned_entities"] == 1
    assert er.async_get(registry_home).async_get("light.floor_lamp") is not None


# ── Services ────────────────────────────────────────────────────────────────


async def test_list_services_without_domain_returns_names_only(
    registry_home: HomeAssistant,
) -> None:
    result = await _make_executor(registry_home).execute("list_services", {})
    assert "domains" in result
    assert "note" in result


async def test_list_services_for_a_domain(registry_home: HomeAssistant) -> None:
    registry_home.services.async_register("demo", "do_thing", lambda call: None)
    result = await _make_executor(registry_home).execute("list_services", {"domain": "demo"})
    assert result["services"][0]["service"] == "demo.do_thing"


async def test_list_services_unknown_domain(registry_home: HomeAssistant) -> None:
    result = await _make_executor(registry_home).execute(
        "list_services", {"domain": "not_a_domain"}
    )
    assert "error" in result


# ── Config-lane false positives (regressions) ───────────────────────────────


@pytest.mark.parametrize(
    ("message", "areas"),
    [
        # "tagged"/"labeled" are targeting qualifiers, not config verbs.
        ("turn off all lights tagged holiday", None),
        ("turn on the light labeled Kitchen", None),
        # A known area name is not evidence unless it is the DESTINATION.
        ("put the Study lamp on", ["Study"]),
        ("put the light on in the Study", ["Study"]),
        # Substring containment would make "Den" match "garden".
        ("move the pot to the garden", ["Den"]),
    ],
)
def test_device_commands_never_lose_execute_command(message: str, areas: list[str] | None) -> None:
    """The expensive failure mode: a real command trimmed to the config lane.

    The config lane has no ``execute_command``, so a false positive here leaves
    an ordinary device-control turn with no tool able to carry it out.
    """
    assert _is_config_request(message, areas) is False


@pytest.mark.parametrize(
    ("message", "areas"),
    [
        ("move the desk lamp to the Study", ["Study", "Bedroom"]),
        ("label the kitchen lights as holiday", None),
        ("tag these as kids", None),
        ("add the holiday label to these lights", None),
        ("remove the holiday label", None),
    ],
)
def test_real_config_requests_still_detected(message: str, areas: list[str] | None) -> None:
    """Narrowing the patterns must not cost the true positives."""
    assert _is_config_request(message, areas) is True


# ── Alias clearing ──────────────────────────────────────────────────────────


async def test_empty_alias_array_clears_area_aliases(registry_home: HomeAssistant) -> None:
    """An explicit [] is a request to drop every alias, not an omitted argument."""
    executor = _make_executor(registry_home)
    await executor.execute("update_area", {"area": "Bedroom", "aliases": ["sleeping room"]})
    assert ar.async_get(registry_home).async_get_area_by_name("Bedroom").aliases

    result = await executor.execute("update_area", {"area": "Bedroom", "aliases": []})
    assert result["status"] == "updated"
    assert ar.async_get(registry_home).async_get_area_by_name("Bedroom").aliases == set()


def _named_aliases(hass: HomeAssistant, entity_id: str) -> list[str]:
    """The user-facing aliases, minus HA's computed-name sentinel."""
    entry = er.async_get(hass).async_get(entity_id)
    return [a for a in entry.aliases or () if isinstance(a, str)]


async def test_empty_alias_array_clears_entity_aliases(registry_home: HomeAssistant) -> None:
    executor = _make_executor(registry_home)
    before = er.async_get(registry_home).async_get("light.floor_lamp").aliases
    sentinels = [a for a in before or () if not isinstance(a, str)]

    await executor.execute(
        "update_entity", {"entity_id": "light.floor_lamp", "aliases": ["reading lamp"]}
    )
    assert _named_aliases(registry_home, "light.floor_lamp") == ["reading lamp"]

    result = await executor.execute(
        "update_entity", {"entity_id": "light.floor_lamp", "aliases": []}
    )
    assert result["status"] == "updated"
    assert _named_aliases(registry_home, "light.floor_lamp") == []
    # HA's computed-name sentinel is not a user alias and must survive a clear.
    after = er.async_get(registry_home).async_get("light.floor_lamp").aliases
    assert [a for a in after or () if not isinstance(a, str)] == sentinels


async def test_omitted_aliases_are_left_alone(registry_home: HomeAssistant) -> None:
    """A rename must not wipe aliases as a side effect."""
    executor = _make_executor(registry_home)
    await executor.execute("update_area", {"area": "Bedroom", "aliases": ["sleeping room"]})

    await executor.execute("update_area", {"area": "Bedroom", "new_name": "Main Bedroom"})
    area = ar.async_get(registry_home).async_get_area_by_name("Main Bedroom")
    assert area.aliases == {"sleeping room"}


async def test_empty_string_aliases_is_treated_as_absent(registry_home: HomeAssistant) -> None:
    """Models fill unused params with "" — that must not wipe aliases."""
    executor = _make_executor(registry_home)
    await executor.execute("update_area", {"area": "Bedroom", "aliases": ["sleeping room"]})

    result = await executor.execute("update_area", {"area": "Bedroom", "aliases": ""})
    assert result["status"] == "unchanged"
    assert ar.async_get(registry_home).async_get_area_by_name("Bedroom").aliases


async def test_redundant_alias_clear_reports_unchanged(registry_home: HomeAssistant) -> None:
    """Clearing aliases on an area that has none writes nothing."""
    result = await _make_executor(registry_home).execute(
        "update_area", {"area": "Bedroom", "aliases": []}
    )
    assert result["status"] == "unchanged"


# ── Destructive-action gating ───────────────────────────────────────────────


async def test_disabling_an_entity_asks_first(registry_home: HomeAssistant) -> None:
    """A disabled entity leaves the state machine — that is not a metadata edit."""
    result = await _make_executor(registry_home).execute(
        "update_entity", {"entity_id": "light.floor_lamp", "disabled": True}
    )
    assert result["requires_approval"] is True
    assert result["destructive"]["verb"] == "disable"
    assert er.async_get(registry_home).async_get("light.floor_lamp").disabled_by is None


async def test_disabling_a_device_reports_its_entity_count(
    registry_home: HomeAssistant,
) -> None:
    """The blast radius is the point: the user is thinking about one thing."""
    device = next(iter(dr.async_get(registry_home).devices.values()))
    result = await _make_executor(registry_home).execute(
        "update_device", {"device": device.id, "disabled": True}
    )
    assert result["destructive"]["kind"] == "device"
    assert "entit" in result["destructive"]["label"]
    assert dr.async_get(registry_home).async_get(device.id).disabled_by is None


async def test_reversible_entity_edits_still_execute(registry_home: HomeAssistant) -> None:
    """Renaming, aliasing, hiding, and exposing stay direct — all reversible."""
    executor = _make_executor(registry_home)
    for arguments in (
        {"entity_id": "light.floor_lamp", "new_name": "Reading Lamp"},
        {"entity_id": "light.floor_lamp", "hidden": True},
        {"entity_id": "light.floor_lamp", "aliases": ["desk light"]},
    ):
        result = await executor.execute("update_entity", arguments)
        assert result["status"] == "updated", arguments
        assert "requires_approval" not in result


async def test_reversible_device_edits_still_execute(registry_home: HomeAssistant) -> None:
    result = await _make_executor(registry_home).execute(
        "update_device", {"device": "Hallway Lamp", "area": "Bedroom"}
    )
    assert result["status"] == "updated"
    assert "requires_approval" not in result


async def test_an_impossible_rename_errors_instead_of_asking(
    registry_home: HomeAssistant,
) -> None:
    """Never spend a confirmation on a change that cannot happen."""
    result = await _make_executor(registry_home).execute(
        "update_entity",
        {"entity_id": "light.floor_lamp", "new_entity_id": "switch.floor_lamp"},
    )
    assert "domain" in result["error"]
    assert "requires_approval" not in result


async def test_a_referenced_rename_errors_instead_of_asking(
    registry_home: HomeAssistant,
) -> None:
    """A blocked rename is refused outright, not offered for confirmation."""
    from unittest.mock import patch

    with patch(
        "custom_components.selora_ai.group_manager.group_dependents",
        return_value={
            "automations": ["automation.porch"],
            "scripts": [],
            "scenes": [],
            "groups": [],
        },
    ):
        result = await _make_executor(registry_home).execute(
            "update_entity",
            {"entity_id": "light.floor_lamp", "new_entity_id": "light.reading_lamp"},
        )
    assert "referenced by" in result["error"]
    assert "requires_approval" not in result


async def test_dashboard_reference_blocks_an_entity_id_rename(
    registry_home: HomeAssistant,
) -> None:
    """Lovelace is the one referrer core ships no *_with_entity helper for."""
    from unittest.mock import patch

    with patch(
        "custom_components.selora_ai.recipes.dashboard.async_dashboards_with_entity",
        return_value=(["lovelace"], []),
    ):
        error = await rm.validate_entity_id_rename(
            registry_home, "light.floor_lamp", "light.reading_lamp"
        )
    assert error is not None
    assert "dashboard" in error


async def test_unreadable_dashboard_blocks_an_entity_id_rename(
    registry_home: HomeAssistant,
) -> None:
    """A dashboard we could not parse is not a dashboard we know is clean.

    YAML dashboards are the case that matters: this integration cannot repair
    one, so the user would have to hand-edit a file after working out why a
    card went blank.
    """
    from unittest.mock import patch

    with patch(
        "custom_components.selora_ai.recipes.dashboard.async_dashboards_with_entity",
        return_value=([], ["yaml-board"]),
    ):
        error = await rm.validate_entity_id_rename(
            registry_home, "light.floor_lamp", "light.reading_lamp"
        )
    assert error is not None
    assert "yaml-board" in error
    assert "could not be read" in error


# ── Config-lane routing regressions ─────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        # "hidden" is an adjective here, not a visibility request.
        "turn off the hidden lights",
        "turn on the hidden lamp",
        # Group/scene/automation operations live in the COMMAND lane, which is
        # where update_group / delete_scene are.
        "rename the bedroom group",
        "delete the movie night scene",
        "create a group for my bedroom lights",
        "add the lamp to the downstairs group",
    ],
)
def test_command_lane_operations_are_not_claimed_by_config(message: str) -> None:
    """The config lane has no execute_command and no group tools.

    A false positive here leaves the turn with a schema that cannot carry out
    the request at all.
    """
    assert _is_config_request(message) is False


def test_group_rename_can_still_reach_update_group() -> None:
    from custom_components.selora_ai.tool_registry import COMMAND_TOOL_NAMES, CONFIG_TOOL_NAMES

    message = "rename the bedroom group"
    lane = CONFIG_TOOL_NAMES if _is_config_request(message) else COMMAND_TOOL_NAMES
    assert "update_group" in lane


# ── Confirmed actions bind to the captured target ───────────────────────────


async def test_device_confirm_uses_the_captured_device_id(
    registry_home: HomeAssistant,
) -> None:
    """The card must act on the device it described, not on the name.

    A device renamed while the card is open, with another device taking its old
    name, would otherwise be resolved by that name at confirm time.
    """
    dev_reg = dr.async_get(registry_home)
    original = next(iter(dev_reg.devices.values()))

    held = await _make_executor(registry_home).execute(
        "update_device", {"device": "Hallway Lamp", "disabled": True}
    )
    assert held["destructive"]["target_id"] == original.id

    # The name now belongs to a different device.
    dev_reg.async_update_device(original.id, name_by_user="Renamed Lamp")
    impostor = dev_reg.async_get_or_create(
        config_entry_id="test_entry",
        identifiers={("demo", "lamp-2")},
        name="Hallway Lamp",
    )

    from custom_components.selora_ai.mcp_server import _tool_update_device

    payload = {"device": held["destructive"]["target_id"], "disabled": True}
    await _tool_update_device(registry_home, payload)

    assert dev_reg.async_get(original.id).disabled_by is dr.DeviceEntryDisabler.USER
    assert dev_reg.async_get(impostor.id).disabled_by is None


async def test_entity_confirm_refuses_a_reused_entity_id(
    registry_home: HomeAssistant,
) -> None:
    """entity_id is mutable — the fingerprint is what carries identity."""
    from custom_components.selora_ai import _stale_entity

    entry = er.async_get(registry_home).async_get("light.floor_lamp")
    held = await _make_executor(registry_home).execute(
        "update_entity", {"entity_id": "light.floor_lamp", "disabled": True}
    )
    assert held["destructive"]["fingerprint"] == entry.id

    assert _stale_entity(registry_home, "light.floor_lamp", entry.id) is None
    assert "different entity" in _stale_entity(
        registry_home, "light.floor_lamp", "some-other-registry-id"
    )
    assert "no longer exists" in _stale_entity(registry_home, "light.gone", entry.id)


# ── Helper inventory ────────────────────────────────────────────────────────


async def test_list_helpers_ignores_non_helper_config_entries(
    registry_home: HomeAssistant,
) -> None:
    """source == "user" plus options is not a helper marker.

    The fixture's own config entry is an ordinary integration; listing it as a
    helper would make list_helpers wrong on any real install.
    """
    result = await _make_executor(registry_home).execute("list_helpers", {})
    domains = {h["domain"] for h in result["config_entry_helpers"]}
    assert "demo" not in domains
    assert "demo" not in domains


async def test_area_delete_card_carries_an_instance_fingerprint(
    registry_home: HomeAssistant,
) -> None:
    """area_id is name-derived, so delete+recreate reuses it."""
    from custom_components.selora_ai import _fingerprint_changed

    registry = ar.async_get(registry_home)
    original = registry.async_get_area_by_name("Bedroom")

    card = await _preview_delete_area(registry_home, {"area": "Bedroom"})
    descriptor = card["delete"]
    assert descriptor["fingerprint"] == original.created_at.isoformat()

    registry.async_delete(original.id)
    recreated = registry.async_create("Bedroom")
    assert recreated.id == original.id
    assert _fingerprint_changed(descriptor, recreated.created_at) is True


async def test_rename_onto_a_yaml_entity_id_is_refused(
    registry_home: HomeAssistant,
) -> None:
    """State without a registry entry still blocks the id — HA checks both."""
    registry_home.states.async_set("light.yaml_only", "on")
    error = await rm.validate_entity_id_rename(registry_home, "light.floor_lamp", "light.yaml_only")
    assert error is not None
    assert "already in use" in error


async def test_disable_plus_rename_discloses_both_changes(
    registry_home: HomeAssistant,
) -> None:
    """One update_entity call can do two destructive things.

    The payload replayed on confirm applies both, so a label naming only the
    disable would have the user approving an entity_id rename they were never
    shown — and the tool loop discards the model's prose, so the card is the
    only place it can be said.
    """
    result = await _make_executor(registry_home).execute(
        "update_entity",
        {
            "entity_id": "light.floor_lamp",
            "disabled": True,
            "new_entity_id": "light.reading_lamp",
        },
    )
    card = result["destructive"]
    assert "Disable" in card["label"]
    assert "light.reading_lamp" in card["label"]
    # The card is bounded — the replay arguments stay in the tool log.
    assert "payload" not in card


async def test_disable_only_card_does_not_mention_a_rename(
    registry_home: HomeAssistant,
) -> None:
    result = await _make_executor(registry_home).execute(
        "update_entity", {"entity_id": "light.floor_lamp", "disabled": True}
    )
    assert "Rename" not in result["destructive"]["label"]


async def test_device_and_its_entity_in_one_call_leaves_no_override(
    registry_home: HomeAssistant,
) -> None:
    """Devices move first, so the entity inherits instead of being pinned.

    With the entity handled first it sees the device still in the old area and
    writes an explicit override — redundant the moment the device moves, and it
    stops the entity following the device ever again.
    """
    device = next(iter(dr.async_get(registry_home).devices.values()))

    result = await _make_executor(registry_home).execute(
        "assign_area",
        {
            "area": "Bedroom",
            "device_ids": [device.id],
            "entity_ids": ["light.hallway_lamp"],
        },
    )
    assert result["devices_moved"] == [device.id]

    ent_reg = er.async_get(registry_home)
    area = ar.async_get(registry_home).async_get_area_by_name("Bedroom")
    assert dr.async_get(registry_home).async_get(device.id).area_id == area.id
    # Inheriting, not pinned.
    assert ent_reg.async_get("light.hallway_lamp").area_id is None


async def test_device_move_still_carries_its_other_entities(
    registry_home: HomeAssistant,
) -> None:
    device = next(iter(dr.async_get(registry_home).devices.values()))
    result = await _make_executor(registry_home).execute(
        "assign_area", {"area": "Bedroom", "device_ids": [device.id]}
    )
    assert result["entities_carried_with_devices"] == 1


@pytest.mark.parametrize(
    "message",
    [
        # Command-shaped verb, but an icon is not a device command.
        "set the icon of sensor.temperature",
        "change the icon for the porch light",
        # Matched no config verb at all before.
        "disable the entity sensor.temperature",
        "disable light.floor_lamp",
        # The exposure vocabulary only covered "expose".
        "remove sensor.temperature from Assist",
        "stop exposing the kettle to Assist",
    ],
)
def test_entity_registry_phrasings_reach_update_entity(message: str) -> None:
    """These are the operations update_entity advertises.

    Routed to the command lane they become impossible — it has no update_entity
    — so the tool exists but cannot be called by the words that ask for it.
    """
    assert _is_config_request(message) is True
    assert "update_entity" in CONFIG_TOOL_NAMES


@pytest.mark.parametrize(
    "message",
    [
        "enable the automation.porch",
        "set the thermostat to 21",
        "open the garage",
        "turn off the hidden lights",
    ],
)
def test_new_entity_shapes_do_not_capture_commands(message: str) -> None:
    assert _is_config_request(message) is False


async def test_list_helpers_uses_ha_declared_helper_domains(
    registry_home: HomeAssistant,
) -> None:
    """Derived from integration_type: helper, not a hand-kept list.

    The literal list named ``times_of_the_day`` — not a domain; HA calls it
    ``tod`` — and omitted filter/manual/otp, so those helpers were invisible.
    """
    from custom_components.selora_ai.registry_manager import _config_entry_helper_domains

    # The fixture's own entry is an ordinary integration, not a helper.
    assert await _config_entry_helper_domains(registry_home) == set()

    result = await _make_executor(registry_home).execute("list_helpers", {})
    assert result["config_entry_helpers"] == []


async def test_helper_domain_lookup_survives_a_bad_manifest(
    registry_home: HomeAssistant,
) -> None:
    """An inventory must not break the tool when one integration fails to load."""
    from unittest.mock import patch

    from custom_components.selora_ai.registry_manager import _config_entry_helper_domains

    with patch("homeassistant.loader.async_get_integrations", side_effect=RuntimeError("boom")):
        assert await _config_entry_helper_domains(registry_home) == set()


async def test_ambiguous_area_alias_is_refused_not_guessed(
    registry_home: HomeAssistant,
) -> None:
    """HA's own lookup returns a LIST of areas per alias — they are not unique."""
    registry = ar.async_get(registry_home)
    living = registry.async_get_area_by_name("Living Room")
    bedroom = registry.async_get_area_by_name("Bedroom")
    registry.async_update(living.id, aliases={"lounge"})
    registry.async_update(bedroom.id, aliases={"lounge"})

    area, error = rm.resolve_area(registry_home, "lounge")
    assert area is None
    assert "alias of 2 areas" in error

    result = await _make_executor(registry_home).execute(
        "assign_area", {"area": "lounge", "entity_ids": ["light.floor_lamp"]}
    )
    assert "alias of 2 areas" in result["error"]
    assert er.async_get(registry_home).async_get("light.floor_lamp").area_id is None


async def test_a_unique_alias_still_resolves(registry_home: HomeAssistant) -> None:
    registry = ar.async_get(registry_home)
    bedroom = registry.async_get_area_by_name("Bedroom")
    registry.async_update(bedroom.id, aliases={"sleeping room"})

    area, error = rm.resolve_area(registry_home, "sleeping room")
    assert error is None
    assert area.id == bedroom.id


@pytest.mark.parametrize(
    "message",
    [
        "rename the lamp to Reading Light and turn it on",
        "turn it on and rename it",
        "hide that sensor and turn off the porch light",
    ],
)
def test_compound_turns_keep_the_full_schema(message: str) -> None:
    """Every lane holds one operation class, so trimming drops half the request."""
    from custom_components.selora_ai.llm_client.intent import _is_compound_request

    assert _is_compound_request(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "set the icon of sensor.temperature",
        "rename this to Reading Lamp",
        "turn off the kitchen light and the hall light",
    ],
)
def test_single_class_turns_still_trim(message: str) -> None:
    from custom_components.selora_ai.llm_client.intent import _is_compound_request

    assert _is_compound_request(message) is False


async def test_ambiguous_floor_alias_is_refused(registry_home: HomeAssistant) -> None:
    """A floor is chosen when filing an area — the wrong one is a wrong storey."""
    registry = fr.async_get(registry_home)
    upstairs = registry.async_create("Upstairs")
    attic = registry.async_create("Attic")
    registry.async_update(upstairs.floor_id, aliases={"top"})
    registry.async_update(attic.floor_id, aliases={"top"})

    floor, error = rm.resolve_floor(registry_home, "top")
    assert floor is None
    assert "alias of 2 floors" in error

    result = await _make_executor(registry_home).execute(
        "create_area", {"name": "Loft", "floor": "top"}
    )
    # The area is created, but not silently filed on an arbitrary storey.
    assert "created_floor" in result or result.get("floor_id") is None


async def test_renamed_device_resolves_by_its_current_name(
    registry_home: HomeAssistant,
) -> None:
    """name_by_user is what the user calls it; the vendor name is the fallback."""
    dev_reg = dr.async_get(registry_home)
    device = next(iter(dev_reg.devices.values()))
    dev_reg.async_update_device(device.id, name_by_user="Entry Lamp")

    found, error = rm.resolve_device(registry_home, "Entry Lamp")
    assert error is None
    assert found.id == device.id

    # The old vendor name still finds it, since nothing else answers to it.
    found, error = rm.resolve_device(registry_home, "Hallway Lamp")
    assert error is None
    assert found.id == device.id


async def test_vendor_name_does_not_collide_with_a_current_name(
    registry_home: HomeAssistant,
) -> None:
    """Matching both names at once made an unambiguous request fail.

    Device A is renamed away from "Hallway Lamp"; device B is now called that.
    "Hallway Lamp" means B — one answer, not an ambiguity error.
    """
    dev_reg = dr.async_get(registry_home)
    original = next(iter(dev_reg.devices.values()))
    dev_reg.async_update_device(original.id, name_by_user="Entry Lamp")

    other = dev_reg.async_get_or_create(
        config_entry_id="test_entry",
        identifiers={("demo", "lamp-2")},
        name="Some Vendor Thing",
    )
    dev_reg.async_update_device(other.id, name_by_user="Hallway Lamp")

    found, error = rm.resolve_device(registry_home, "Hallway Lamp")
    assert error is None
    assert found.id == other.id


@pytest.mark.parametrize(
    "message",
    [
        "set the aliases for light.floor_lamp",
        "set the icon of sensor.temperature",
        "change the icon for the porch light",
        "set the icon to mdi:sofa",
        "set the name of light.floor_lamp",
    ],
)
def test_registry_edits_beat_the_command_veto(message: str) -> None:
    """These open with command syntax but edit the registry.

    Vetoed, they land in a lane with no update_entity and cannot be carried out.
    """
    assert _is_config_request(message) is True


def test_a_mentioned_icon_is_not_an_icon_edit() -> None:
    """Matching the bare noun sent a device command to a lane with no
    execute_command."""
    assert _is_config_request("turn on the light with the bulb icon") is False


async def test_entity_reused_during_rename_validation_is_refused(
    registry_home: HomeAssistant,
) -> None:
    """validate_entity_id_rename awaits; an entity_id is mutable and reusable.

    If the original entity is renamed away and another takes its id inside that
    window, the write — which addresses entities by id — would land on the
    wrong one.
    """
    from unittest.mock import patch

    ent_reg = er.async_get(registry_home)
    victim = ent_reg.async_get("light.floor_lamp")

    async def _swap_during_validation(hass, entity_id, new_entity_id):
        # Rename the target away and give its id to a different entity. The
        # state has to go too: HA treats an id present in the state machine as
        # unavailable, which is what blocks a rename onto it.
        registry_home.states.async_remove("light.floor_lamp")
        ent_reg.async_update_entity("light.floor_lamp", new_entity_id="light.moved_away")
        other = ent_reg.async_get_or_create(
            "light", "demo", "impostor", suggested_object_id="tmp_impostor"
        )
        ent_reg.async_update_entity(other.entity_id, new_entity_id="light.floor_lamp")
        return None  # validation "passes"

    with patch(
        "custom_components.selora_ai.registry_manager.validate_entity_id_rename",
        _swap_during_validation,
    ):
        result = await rm.async_update_entity(
            registry_home,
            entity_id="light.floor_lamp",
            new_entity_id="light.reading_lamp",
        )

    assert "no longer refers to the same entity" in result["error"]
    # The impostor was not renamed, and the original kept its new id.
    assert ent_reg.async_get("light.reading_lamp") is None
    assert ent_reg.async_get("light.moved_away").id == victim.id


async def test_an_ordinary_rename_still_succeeds(registry_home: HomeAssistant) -> None:
    """The recheck must not block the uncontended case."""
    result = await rm.async_update_entity(
        registry_home, entity_id="light.floor_lamp", new_entity_id="light.reading_lamp"
    )
    assert result["entity_id"] == "light.reading_lamp"
