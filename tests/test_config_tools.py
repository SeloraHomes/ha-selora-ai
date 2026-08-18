"""Tests for the script, label, helper, and diagnostics chat tools.

The script tests drive the real ``scripts.yaml`` round-trip and HA's own
``script.reload``, and the label tests drive the real label/entity/device/area
registries — so an HA API change fails here rather than at runtime.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers import (
    label_registry as lr,
)
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai import label_manager as lm
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import CHAT_TOOLS, TOOL_MAP

_TIER2_TOOLS = (
    "list_scripts",
    "get_script",
    "set_script",
    "delete_script",
    "list_labels",
    "create_label",
    "assign_labels",
    "delete_label",
    "list_helpers",
    "get_logs",
    "get_automation_traces",
)


def _make_executor(hass: HomeAssistant, *, is_admin: bool = True) -> ToolExecutor:
    return ToolExecutor(hass, MagicMock(), is_admin=is_admin)


@pytest.fixture
async def script_home(hass: HomeAssistant) -> HomeAssistant:
    """A hass with the script component up and an empty scripts.yaml.

    ``configuration.yaml`` has to exist: ``script.reload`` re-reads the whole
    config, and the packaged test config dir ships without one.
    """
    Path(hass.config.path("scripts.yaml")).write_text("{}\n", encoding="utf-8")
    Path(hass.config.path("configuration.yaml")).write_text(
        "script: !include scripts.yaml\n", encoding="utf-8"
    )
    assert await async_setup_component(hass, "script", {"script": {}})
    await hass.async_block_till_done()
    return hass


# ── Registration ────────────────────────────────────────────────────────────


def test_tier2_tools_are_registered() -> None:
    names = {t.name for t in CHAT_TOOLS}
    for tool in _TIER2_TOOLS:
        assert tool in names, f"{tool} missing from CHAT_TOOLS"


def test_tier2_tools_are_large_context_only() -> None:
    for tool in _TIER2_TOOLS:
        assert TOOL_MAP[tool].large_context_only is True


def test_tier2_write_tools_require_admin() -> None:
    for tool in ("set_script", "delete_script", "create_label", "assign_labels", "delete_label"):
        assert TOOL_MAP[tool].requires_admin is True
    for tool in ("list_scripts", "get_script", "list_labels", "list_helpers"):
        assert TOOL_MAP[tool].requires_admin is False


def test_diagnostics_are_admin_only() -> None:
    """Read-only, but admin-gated to match Home Assistant.

    Core guards ``system_log/list`` and every ``trace/*`` command with
    ``require_admin``: logs carry exception text and config detail, traces
    expose what automations do and when. Neither belongs to a read-only
    credential just because it performs no write.
    """
    from custom_components.selora_ai.mcp_server import _ADMIN_TOOLS, _READ_ONLY_TOOLS

    for tool in ("get_logs", "get_automation_traces"):
        assert TOOL_MAP[tool].requires_admin is True
    for tool in ("selora_get_logs", "selora_get_automation_traces"):
        assert tool in _ADMIN_TOOLS
        assert tool not in _READ_ONLY_TOOLS


# ── Scripts ─────────────────────────────────────────────────────────────────


async def test_set_script_creates_and_lists(script_home: HomeAssistant) -> None:
    executor = _make_executor(script_home)
    result = await executor.execute(
        "set_script",
        {
            "alias": "Movie Night",
            "sequence": [{"service": "light.turn_off", "target": {"entity_id": "light.a"}}],
        },
    )
    assert result["status"] == "created"
    assert result["entity_id"] == "script.movie_night"

    listing = await executor.execute("list_scripts", {})
    assert listing["count"] == 1
    assert listing["scripts"][0]["alias"] == "Movie Night"


async def test_set_script_replaces_by_alias(script_home: HomeAssistant) -> None:
    """A second call with the same alias edits it rather than making a twin."""
    executor = _make_executor(script_home)
    await executor.execute(
        "set_script", {"alias": "Movie Night", "sequence": [{"delay": {"seconds": 1}}]}
    )
    # Replacing an existing script discards its sequence, so it asks first.
    held = await executor.execute(
        "set_script",
        {
            "alias": "Movie Night",
            "sequence": [{"delay": {"seconds": 1}}, {"delay": {"seconds": 2}}],
        },
    )
    assert held["requires_approval"] is True
    assert held["destructive"]["verb"] == "replace"

    from custom_components.selora_ai.mcp_server import _tool_set_script

    # The card carries no payload — the replay arguments live in the tool log.
    assert "payload" not in held["destructive"]
    result = await _tool_set_script(
        script_home,
        {
            "alias": "Movie Night",
            "sequence": [{"delay": {"seconds": 1}}, {"delay": {"seconds": 2}}],
        },
    )
    assert result["status"] == "updated"
    assert result["step_count"] == 2

    listing = await executor.execute("list_scripts", {})
    assert listing["count"] == 1


async def test_get_script_resolves_by_entity_id_and_alias(script_home: HomeAssistant) -> None:
    executor = _make_executor(script_home)
    await executor.execute(
        "set_script", {"alias": "Movie Night", "sequence": [{"delay": {"seconds": 1}}]}
    )
    by_entity = await executor.execute("get_script", {"script": "script.movie_night"})
    by_alias = await executor.execute("get_script", {"script": "Movie Night"})
    assert by_entity["object_id"] == by_alias["object_id"] == "movie_night"
    assert by_entity["config"]["alias"] == "Movie Night"


async def test_set_script_rejects_an_invalid_sequence(script_home: HomeAssistant) -> None:
    """Validation runs BEFORE the write, so scripts.yaml is never left broken."""
    result = await _make_executor(script_home).execute(
        "set_script", {"alias": "Bad", "sequence": [{"not_a_real_action": True}]}
    )
    assert "error" in result
    assert Path(script_home.config.path("scripts.yaml")).read_text().strip() in ("{}", "")


async def test_set_script_requires_a_sequence(script_home: HomeAssistant) -> None:
    result = await _make_executor(script_home).execute(
        "set_script", {"alias": "Empty", "sequence": []}
    )
    assert "sequence" in result["error"]


async def test_get_script_unknown(script_home: HomeAssistant) -> None:
    result = await _make_executor(script_home).execute("get_script", {"script": "nope"})
    assert "error" in result


async def test_delete_script_returns_an_approval_card(script_home: HomeAssistant) -> None:
    executor = _make_executor(script_home)
    await executor.execute(
        "set_script", {"alias": "Movie Night", "sequence": [{"delay": {"seconds": 1}}]}
    )
    result = await executor.execute("delete_script", {"script": "Movie Night"})

    assert result["requires_approval"] is True
    assert result["delete"]["kind"] == "script"
    assert result["delete"]["target_id"] == "movie_night"
    # The card is a question — the script is still there.
    assert (await executor.execute("list_scripts", {}))["count"] == 1


async def test_delete_script_removes_it(script_home: HomeAssistant) -> None:
    from custom_components.selora_ai.script_manager import async_delete_script

    executor = _make_executor(script_home)
    await executor.execute(
        "set_script", {"alias": "Movie Night", "sequence": [{"delay": {"seconds": 1}}]}
    )
    result = await async_delete_script(script_home, "movie_night")
    assert result["status"] == "deleted"
    assert (await executor.execute("list_scripts", {}))["count"] == 0


# ── Labels ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def label_home(hass: HomeAssistant) -> HomeAssistant:
    ar.async_get(hass).async_create("Kitchen")
    er.async_get(hass).async_get_or_create(
        "light", "demo", "lamp", suggested_object_id="kitchen_lamp"
    )
    return hass


async def test_create_label_is_idempotent(label_home: HomeAssistant) -> None:
    executor = _make_executor(label_home)
    first = await executor.execute("create_label", {"name": "holiday"})
    again = await executor.execute("create_label", {"name": "holiday"})
    assert first["status"] == "created"
    assert again["status"] == "exists"
    assert again["label_id"] == first["label_id"]


async def test_assign_labels_creates_unknown_labels(label_home: HomeAssistant) -> None:
    result = await _make_executor(label_home).execute(
        "assign_labels", {"add_labels": ["holiday"], "entity_ids": ["light.kitchen_lamp"]}
    )
    assert result["labels_created"] == ["holiday"]
    entry = er.async_get(label_home).async_get("light.kitchen_lamp")
    assert len(entry.labels) == 1


async def test_assign_labels_preserves_labels_it_was_not_told_about(
    label_home: HomeAssistant,
) -> None:
    """The delta semantics exist precisely so an unrelated label survives."""
    registry = lr.async_get(label_home)
    other = registry.async_create("battery-powered")
    er.async_get(label_home).async_update_entity("light.kitchen_lamp", labels={other.label_id})

    await _make_executor(label_home).execute(
        "assign_labels", {"add_labels": ["holiday"], "entity_ids": ["light.kitchen_lamp"]}
    )
    labels = er.async_get(label_home).async_get("light.kitchen_lamp").labels
    assert other.label_id in labels
    assert len(labels) == 2


async def test_assign_labels_removes(label_home: HomeAssistant) -> None:
    executor = _make_executor(label_home)
    await executor.execute(
        "assign_labels", {"add_labels": ["holiday"], "entity_ids": ["light.kitchen_lamp"]}
    )
    await executor.execute(
        "assign_labels", {"remove_labels": ["holiday"], "entity_ids": ["light.kitchen_lamp"]}
    )
    assert er.async_get(label_home).async_get("light.kitchen_lamp").labels == set()


async def test_assign_labels_to_an_area(label_home: HomeAssistant) -> None:
    result = await _make_executor(label_home).execute(
        "assign_labels", {"add_labels": ["downstairs"], "areas": ["Kitchen"]}
    )
    assert result["updated"]["areas"]
    area = ar.async_get(label_home).async_get_area_by_name("Kitchen")
    assert len(area.labels) == 1


async def test_assign_labels_needs_a_target(label_home: HomeAssistant) -> None:
    result = await _make_executor(label_home).execute("assign_labels", {"add_labels": ["holiday"]})
    assert "entity_id" in result["error"]


async def test_assign_labels_needs_a_label(label_home: HomeAssistant) -> None:
    result = await _make_executor(label_home).execute(
        "assign_labels", {"entity_ids": ["light.kitchen_lamp"]}
    )
    assert "add or remove" in result["error"]


async def test_assign_labels_reports_unknown_targets(label_home: HomeAssistant) -> None:
    result = await _make_executor(label_home).execute(
        "assign_labels", {"add_labels": ["holiday"], "device_ids": ["nope"]}
    )
    assert result["failed"][0]["target"] == "nope"


async def test_list_labels_counts_each_target_kind(label_home: HomeAssistant) -> None:
    executor = _make_executor(label_home)
    await executor.execute(
        "assign_labels",
        {"add_labels": ["holiday"], "entity_ids": ["light.kitchen_lamp"], "areas": ["Kitchen"]},
    )
    listing = await executor.execute("list_labels", {})
    record = next(r for r in listing["labels"] if r["name"] == "holiday")
    assert record["entity_count"] == 1
    assert record["area_count"] == 1


async def test_delete_label_returns_an_approval_card(label_home: HomeAssistant) -> None:
    executor = _make_executor(label_home)
    await executor.execute(
        "assign_labels", {"add_labels": ["holiday"], "entity_ids": ["light.kitchen_lamp"]}
    )
    result = await executor.execute("delete_label", {"label": "holiday"})
    assert result["delete"]["kind"] == "label"
    assert "on 1 target" in result["delete"]["label"]
    assert lr.async_get(label_home).async_get_label_by_name("holiday") is not None


async def test_delete_label_strips_it_from_targets(label_home: HomeAssistant) -> None:
    executor = _make_executor(label_home)
    await executor.execute(
        "assign_labels", {"add_labels": ["holiday"], "entity_ids": ["light.kitchen_lamp"]}
    )
    label = lr.async_get(label_home).async_get_label_by_name("holiday")

    result = lm.async_delete_label(label_home, label.label_id)
    assert result["status"] == "deleted"
    assert result["removed_from_entities"] == 1


# ── Helpers ─────────────────────────────────────────────────────────────────


async def test_list_helpers_finds_storage_helpers(hass: HomeAssistant) -> None:
    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"guest_mode": {"name": "Guest Mode"}}}
    )
    await hass.async_block_till_done()

    result = await _make_executor(hass).execute("list_helpers", {})
    ids = {h["entity_id"] for h in result["helpers"]}
    assert "input_boolean.guest_mode" in ids


async def test_list_helpers_filters_by_domain(hass: HomeAssistant) -> None:
    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"guest_mode": None}}
    )
    await hass.async_block_till_done()

    result = await _make_executor(hass).execute("list_helpers", {"domain": "counter"})
    assert result["helpers"] == []


# ── Diagnostics ─────────────────────────────────────────────────────────────


async def test_get_logs_without_system_log_is_not_an_error(hass: HomeAssistant) -> None:
    """An install without system_log gets an explanation, not a crash."""
    result = await _make_executor(hass).execute("get_logs", {})
    assert result["entries"] == []
    assert "message" in result


async def test_get_logs_rejects_a_bad_level(hass: HomeAssistant) -> None:
    result = await _make_executor(hass).execute("get_logs", {"level": "LOUD"})
    assert "level must be one of" in result["error"]


async def test_get_logs_reads_captured_errors(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "system_log", {})
    await hass.async_block_till_done()

    import logging

    logging.getLogger("test.selora").error("something broke in the kitchen")
    await hass.async_block_till_done()

    result = await _make_executor(hass).execute("get_logs", {"contains": "kitchen"})
    assert result["count"] == 1
    assert "something broke" in result["entries"][0]["message"]


async def test_get_traces_for_an_unknown_automation(hass: HomeAssistant) -> None:
    result = await _make_executor(hass).execute(
        "get_automation_traces", {"automation": "automation.nope"}
    )
    assert "error" in result


async def test_get_traces_explains_a_missing_config_id(hass: HomeAssistant) -> None:
    """A YAML automation with no id is never traced — say so, don't return empty."""
    hass.states.async_set("automation.legacy", "on", {"friendly_name": "Legacy"})
    result = await _make_executor(hass).execute(
        "get_automation_traces", {"automation": "automation.legacy"}
    )
    assert "no config id" in result["error"]


# ── Lane routing and card synthesis ─────────────────────────────────────────


def test_every_delete_tool_is_in_both_allowlists() -> None:
    """Both are allowlists, and a miss fails silently with an empty reply."""
    from custom_components.selora_ai.llm_client.command_policy import (
        _DELETE_KINDS,
        _DELETE_TOOLS,
    )

    for tool in (
        "delete_automation",
        "delete_scene",
        "delete_group",
        "delete_area",
        "delete_script",
        "delete_label",
    ):
        assert tool in _DELETE_TOOLS
    for kind in ("automation", "scene", "group", "area", "script", "label"):
        assert kind in _DELETE_KINDS


@pytest.mark.parametrize(
    ("message", "expected_tool"),
    [
        ("delete the Movie Night script", "delete_script"),
        ("remove the holiday label", "delete_label"),
        ("get rid of the Study area", "delete_area"),
    ],
)
def test_delete_phrasings_reach_their_tool(message: str, expected_tool: str) -> None:
    """Whichever lane a delete phrasing lands in must contain its tool.

    The config detector claims the phrasings that name the noun; everything
    else falls through to ``command``. Either way the tool has to be reachable,
    or the request dead-ends on the exact wording that asks for it.
    """
    from custom_components.selora_ai.llm_client.intent import _is_config_request
    from custom_components.selora_ai.tool_registry import (
        COMMAND_TOOL_NAMES,
        CONFIG_TOOL_NAMES,
    )

    lane = CONFIG_TOOL_NAMES if _is_config_request(message) else COMMAND_TOOL_NAMES
    assert expected_tool in lane


def test_script_creation_is_not_claimed_by_the_config_lane() -> None:
    """Creation is automation-shaped and needs the full schema, not a trim."""
    from custom_components.selora_ai.llm_client.intent import _is_config_request

    assert _is_config_request("create a script that turns off the lights at 11pm") is False
    assert _is_config_request("make me a Movie Night script") is False
    # Management verbs ARE claimed.
    assert _is_config_request("delete the Movie Night script") is True
    assert _is_config_request("list my scripts") is True


def test_running_a_script_stays_a_command() -> None:
    """'run the movie night script' must keep execute_command."""
    from custom_components.selora_ai.llm_client.intent import _is_config_request

    assert _is_config_request("run the movie night script") is False


def test_label_phrasings_are_config() -> None:
    from custom_components.selora_ai.llm_client.intent import _is_config_request

    assert _is_config_request("label the kitchen lights as holiday") is True
    assert _is_config_request("tag these as kids") is True


# ── Review regressions ──────────────────────────────────────────────────────


async def test_malformed_scripts_yaml_never_gets_overwritten(
    script_home: HomeAssistant,
) -> None:
    """A parse failure must abort the write, not read as an empty file.

    Every mutation rewrites the whole file, so treating "unparseable" as "{}"
    means the next set_script silently discards every script in it.
    """
    path = Path(script_home.config.path("scripts.yaml"))
    original = "movie_night:\n  alias: Movie Night\n  sequence: [ unclosed\n"
    path.write_text(original, encoding="utf-8")

    executor = _make_executor(script_home)
    result = await executor.execute(
        "set_script", {"alias": "New One", "sequence": [{"delay": {"seconds": 1}}]}
    )
    assert "error" in result
    assert path.read_text(encoding="utf-8") == original


async def test_scripts_yaml_that_is_a_list_is_refused(script_home: HomeAssistant) -> None:
    """automations.yaml is a list; scripts.yaml is a mapping. Do not rewrite it."""
    path = Path(script_home.config.path("scripts.yaml"))
    original = "- alias: Wrong Shape\n"
    path.write_text(original, encoding="utf-8")

    result = await _make_executor(script_home).execute(
        "set_script", {"alias": "New One", "sequence": [{"delay": {"seconds": 1}}]}
    )
    assert "error" in result
    assert path.read_text(encoding="utf-8") == original


async def test_reads_also_report_a_malformed_file(script_home: HomeAssistant) -> None:
    """A read must not present a broken file as an empty script list."""
    Path(script_home.config.path("scripts.yaml")).write_text(
        "movie_night: [ unclosed\n", encoding="utf-8"
    )
    result = await _make_executor(script_home).execute("list_scripts", {})
    assert "error" in result


async def test_concurrent_script_writes_do_not_lose_each_other(
    script_home: HomeAssistant,
) -> None:
    """Both writers read the same snapshot without the lock; the second wins."""
    import asyncio

    executor = _make_executor(script_home)
    held_a, held_b = await asyncio.gather(
        executor.execute("set_script", {"alias": "A", "sequence": [{"delay": {"seconds": 1}}]}),
        executor.execute("set_script", {"alias": "B", "sequence": [{"delay": {"seconds": 2}}]}),
    )
    assert held_a["status"] == "created"
    assert held_b["status"] == "created"

    listing = await executor.execute("list_scripts", {})
    assert {s["alias"] for s in listing["scripts"]} == {"A", "B"}


async def test_label_counts_survive_the_overview_cap(label_home: HomeAssistant) -> None:
    """A label sorting past the display cap still reports its real usage.

    label_overview() truncates at 50; looking a label up in that result made
    deletion of the 51st-onward report zero removals and show a card with no
    blast radius.
    """
    from custom_components.selora_ai.label_manager import label_usage

    registry = lr.async_get(label_home)
    for i in range(60):
        registry.async_create(f"zz-label-{i:03d}")
    target = registry.async_create("zzz-last")
    er.async_get(label_home).async_update_entity("light.kitchen_lamp", labels={target.label_id})

    overview = lm.label_overview(label_home)
    assert len(overview["labels"]) < overview["count"]  # capped, as designed
    assert all(r["label_id"] != target.label_id for r in overview["labels"])

    assert label_usage(label_home, target.label_id)["entity_count"] == 1
    result = lm.async_delete_label(label_home, target.label_id)
    assert result["removed_from_entities"] == 1


async def test_delete_label_card_names_targeting_automations(
    label_home: HomeAssistant,
) -> None:
    """A carrier loses a tag; a targeter silently stops matching."""
    from unittest.mock import patch

    from custom_components.selora_ai.mcp_server import _preview_delete_label

    await _make_executor(label_home).execute("create_label", {"name": "holiday"})
    label = lr.async_get(label_home).async_get_label_by_name("holiday")

    with patch(
        "homeassistant.components.automation.automations_with_label",
        return_value=["automation.holiday_lights"],
    ):
        result = await _preview_delete_label(label_home, {"label": "holiday"})

    assert result["delete"]["target_id"] == label.label_id
    assert "target it" in result["delete"]["label"]


# ── Destructive-action gating ───────────────────────────────────────────────


def test_destructive_tools_and_verbs_are_allowlisted() -> None:
    """Both are allowlists; a miss means an empty reply and no card."""
    from custom_components.selora_ai.llm_client.command_policy import (
        _DESTRUCTIVE_TOOLS,
        _DESTRUCTIVE_VERBS,
    )

    assert {"update_entity", "update_device", "set_script"} <= _DESTRUCTIVE_TOOLS
    assert {"disable", "rename_id", "replace"} <= _DESTRUCTIVE_VERBS


async def test_creating_a_script_does_not_ask(script_home: HomeAssistant) -> None:
    """Creation discards nothing — a card there teaches users to tap through."""
    result = await _make_executor(script_home).execute(
        "set_script", {"alias": "Brand New", "sequence": [{"delay": {"seconds": 1}}]}
    )
    assert result["status"] == "created"
    assert "requires_approval" not in result


# ── Review round 3 regressions ──────────────────────────────────────────────


async def test_get_script_never_returns_a_partial_sequence(
    script_home: HomeAssistant,
) -> None:
    """A truncated sequence fed back to set_script is silent data loss.

    get_script is documented as the thing to call before a wholesale replace,
    so a shortened sequence would be edited and written back with every omitted
    step gone.
    """
    executor = _make_executor(script_home)
    long_sequence = [
        {"service": "light.turn_on", "target": {"entity_id": f"light.l{i}"}} for i in range(400)
    ]
    await executor.execute("set_script", {"alias": "Huge", "sequence": long_sequence})

    result = await executor.execute("get_script", {"script": "Huge"})
    assert result["editable"] is False
    assert result["sequence_omitted"] is True
    assert result["step_count"] == 400
    # Omitted, never shortened — a partial copy is the hazard.
    assert "sequence" not in result["config"]


async def test_get_script_returns_a_normal_sequence_whole(
    script_home: HomeAssistant,
) -> None:
    executor = _make_executor(script_home)
    sequence = [{"delay": {"seconds": i}} for i in range(30)]
    await executor.execute("set_script", {"alias": "Normal", "sequence": sequence})

    result = await executor.execute("get_script", {"script": "Normal"})
    assert result["editable"] is True
    assert len(result["config"]["sequence"]) == 30


async def test_assign_labels_creates_nothing_when_a_removal_is_unknown(
    label_home: HomeAssistant,
) -> None:
    """Validation precedes mutation, or a failed call leaves an orphan label."""
    before = {label.label_id for label in lr.async_get(label_home).async_list_labels()}

    result = await _make_executor(label_home).execute(
        "assign_labels",
        {
            "add_labels": ["brand-new"],
            "remove_labels": ["does-not-exist"],
            "entity_ids": ["light.kitchen_lamp"],
        },
    )
    assert "error" in result
    after = {label.label_id for label in lr.async_get(label_home).async_list_labels()}
    assert after == before


async def test_label_delete_card_carries_an_instance_fingerprint(
    label_home: HomeAssistant,
) -> None:
    """label_id is name-derived, so a recreated label reuses it."""
    from custom_components.selora_ai import _fingerprint_changed
    from custom_components.selora_ai.mcp_server import _preview_delete_label

    await _make_executor(label_home).execute("create_label", {"name": "holiday"})
    registry = lr.async_get(label_home)
    original = registry.async_get_label_by_name("holiday")

    card = await _preview_delete_label(label_home, {"label": "holiday"})
    descriptor = card["delete"]
    assert descriptor["fingerprint"] == original.created_at.isoformat()

    # Delete and recreate: same id, different instance.
    registry.async_delete(original.label_id)
    recreated = registry.async_create("holiday")
    assert recreated.label_id == original.label_id
    assert _fingerprint_changed(descriptor, recreated.created_at) is True
    assert _fingerprint_changed(descriptor, original.created_at) is False


async def test_script_delete_card_carries_a_config_fingerprint(
    script_home: HomeAssistant,
) -> None:
    """An alias survives an edit, so the fingerprint hashes the whole config.

    Without this, editing a script while its delete card is open lets the stale
    card destroy the new content.
    """
    from custom_components.selora_ai.mcp_server import _preview_delete_script, _tool_set_script
    from custom_components.selora_ai.script_manager import async_delete_script

    executor = _make_executor(script_home)
    await executor.execute(
        "set_script", {"alias": "Movie Night", "sequence": [{"delay": {"seconds": 1}}]}
    )
    card = await _preview_delete_script(script_home, {"script": "Movie Night"})
    fingerprint = card["delete"]["fingerprint"]
    assert len(fingerprint) == 64  # sha256 hex

    # Same alias, different sequence — the edit the alias check could not see.
    await _tool_set_script(
        script_home,
        {
            "object_id": "movie_night",
            "alias": "Movie Night",
            "sequence": [{"delay": {"seconds": 9}}],
        },
    )

    result = await async_delete_script(script_home, "movie_night", expected_fingerprint=fingerprint)
    assert "has changed" in result["error"]
    assert (await executor.execute("list_scripts", {}))["count"] == 1


async def test_stale_replace_card_cannot_overwrite_a_newer_edit(
    script_home: HomeAssistant,
) -> None:
    from custom_components.selora_ai.mcp_server import _tool_set_script
    from custom_components.selora_ai.script_manager import async_set_script

    executor = _make_executor(script_home)
    await executor.execute(
        "set_script", {"alias": "Movie Night", "sequence": [{"delay": {"seconds": 1}}]}
    )
    held = await executor.execute(
        "set_script", {"alias": "Movie Night", "sequence": [{"delay": {"seconds": 2}}]}
    )
    fingerprint = held["destructive"]["fingerprint"]

    # Someone edits it while the card is open.
    await _tool_set_script(
        script_home,
        {
            "object_id": "movie_night",
            "alias": "Movie Night",
            "sequence": [{"delay": {"seconds": 99}}],
        },
    )

    result = await async_set_script(
        script_home,
        alias="Movie Night",
        sequence=[{"delay": {"seconds": 2}}],
        object_id="movie_night",
        expected_fingerprint=fingerprint,
    )
    assert "has changed" in result["error"]

    current = await executor.execute("get_script", {"script": "movie_night"})
    assert current["config"]["sequence"] == [{"delay": {"seconds": 99}}]


async def test_matching_fingerprint_still_applies(script_home: HomeAssistant) -> None:
    """The guard must not block the ordinary case."""
    from custom_components.selora_ai.mcp_server import _preview_delete_script
    from custom_components.selora_ai.script_manager import async_delete_script

    executor = _make_executor(script_home)
    await executor.execute(
        "set_script", {"alias": "Movie Night", "sequence": [{"delay": {"seconds": 1}}]}
    )
    card = await _preview_delete_script(script_home, {"script": "Movie Night"})
    result = await async_delete_script(
        script_home, "movie_night", expected_fingerprint=card["delete"]["fingerprint"]
    )
    assert result["status"] == "deleted"


async def test_traces_with_datetime_timestamps_are_json_safe() -> None:
    """as_short_dict returns datetimes; the MCP dispatcher json.dumps has no default."""
    import json as _json
    from datetime import UTC, datetime

    from custom_components.selora_ai.diagnostics_tools import _json_safe

    raw = {"start": datetime(2026, 1, 1, tzinfo=UTC), "finish": None}
    safe = _json_safe(raw)
    assert safe["start"] == "2026-01-01T00:00:00+00:00"
    assert safe["finish"] is None
    _json.dumps(safe, ensure_ascii=False)  # must not raise


async def test_assign_labels_creates_nothing_when_every_target_is_invalid(
    label_home: HomeAssistant,
) -> None:
    """Creating a label is a mutation; bogus targets must not leave one behind."""
    before = {label.label_id for label in lr.async_get(label_home).async_list_labels()}

    result = await _make_executor(label_home).execute(
        "assign_labels",
        {"add_labels": ["brand-new"], "device_ids": ["no-such-device"]},
    )
    assert "error" in result
    assert result["failed"][0]["target"] == "no-such-device"
    after = {label.label_id for label in lr.async_get(label_home).async_list_labels()}
    assert after == before


async def test_assign_labels_still_works_with_one_valid_target(
    label_home: HomeAssistant,
) -> None:
    """A partial failure must not block the targets that did resolve."""
    result = await _make_executor(label_home).execute(
        "assign_labels",
        {
            "add_labels": ["holiday"],
            "entity_ids": ["light.kitchen_lamp"],
            "device_ids": ["no-such-device"],
        },
    )
    assert result["status"] == "updated"
    assert result["updated"]["entities"] == ["light.kitchen_lamp"]
    assert result["failed"][0]["target"] == "no-such-device"


def test_mixed_delete_and_destructive_share_one_card() -> None:
    """Nothing carries a held action to the next turn.

    Synthesis only ever sees the current turn's tool_log, so an action dropped
    here is a request the user made, was never told about, and never gets.
    """
    from custom_components.selora_ai.llm_client.command_policy import (
        _build_delete_approval_response,
        _pending_deletes_from_log,
        _pending_destructive_from_log,
    )

    tool_log = [
        {
            "tool": "delete_scene",
            "result": {
                "requires_approval": True,
                "delete": {
                    "kind": "scene",
                    "target_id": "sid",
                    "entity_id": "scene.movie",
                    "name": "Movie",
                    "label": "Movie",
                },
            },
        },
        {
            "tool": "update_entity",
            "arguments": {"entity_id": "light.x", "disabled": True},
            "result": {
                "requires_approval": True,
                "destructive": {
                    "kind": "entity",
                    "verb": "disable",
                    "target_id": "light.x",
                    "entity_id": "light.x",
                    "name": "X",
                    "label": "Disable X",
                    "fingerprint": "abc",
                },
            },
        },
    ]
    deletes = _pending_deletes_from_log(tool_log)
    actions = _pending_destructive_from_log(tool_log)
    assert deletes and actions

    upgraded = _build_delete_approval_response(
        {"intent": "answer", "response": ""},
        deletes,
        tool_log,
        None,
        language="en",
        actions=actions,
    )
    proposal = upgraded["command_approval"]
    assert len(proposal["deletes"]) == 1
    assert len(proposal["actions"]) == 1
    # Neutral wording once it is not purely deletions.
    assert (
        proposal["quick_actions"][0]["label"] != "Delete" if proposal.get("quick_actions") else True
    )
    assert upgraded["quick_actions"][0]["label"] == "Apply"


async def test_slug_collision_does_not_overwrite_an_unrelated_script(
    script_home: HomeAssistant,
) -> None:
    """An alias does not round-trip through a slug.

    "Movie Night" does not resolve against a script aliased "Something Else",
    but slugify still produces movie_night — which that script occupies. Taking
    the slug unchecked overwrote it, and the preview called the call a creation
    so no confirmation was ever shown.
    """
    from custom_components.selora_ai.mcp_server import _tool_set_script

    # Occupy the slug with a script whose alias is different.
    await _tool_set_script(
        script_home,
        {
            "object_id": "movie_night",
            "alias": "Something Else",
            "sequence": [{"delay": {"seconds": 1}}],
        },
    )

    result = await _make_executor(script_home).execute(
        "set_script", {"alias": "Movie Night", "sequence": [{"delay": {"seconds": 5}}]}
    )
    assert result["status"] == "created"
    assert result["object_id"] != "movie_night"

    listing = await _make_executor(script_home).execute("list_scripts", {})
    assert listing["count"] == 2
    survivor = await _make_executor(script_home).execute("get_script", {"script": "movie_night"})
    assert survivor["config"]["alias"] == "Something Else"
    assert survivor["config"]["sequence"] == [{"delay": {"seconds": 1}}]


async def test_editing_by_alias_still_replaces_in_place(script_home: HomeAssistant) -> None:
    """Unique-slug allocation must not turn every edit into a duplicate."""
    from custom_components.selora_ai.mcp_server import _tool_set_script

    executor = _make_executor(script_home)
    await executor.execute(
        "set_script", {"alias": "Movie Night", "sequence": [{"delay": {"seconds": 1}}]}
    )
    held = await executor.execute(
        "set_script", {"alias": "Movie Night", "sequence": [{"delay": {"seconds": 2}}]}
    )
    assert held["requires_approval"] is True

    await _tool_set_script(
        script_home, {"alias": "Movie Night", "sequence": [{"delay": {"seconds": 2}}]}
    )
    assert (await executor.execute("list_scripts", {}))["count"] == 1


async def test_genuine_booleans_survive_the_yaml_round_trip(
    script_home: HomeAssistant,
) -> None:
    """continue_on_error / retain are real booleans, not entity states.

    The automation quoter turns every bool outside its allowlist into the
    string "on"/"off", which silently rewrote script actions — in every script
    in the file, on every write.
    """
    from custom_components.selora_ai.mcp_server import _tool_set_script

    await _tool_set_script(
        script_home,
        {
            "alias": "Publisher",
            "sequence": [
                {
                    "service": "mqtt.publish",
                    "data": {"topic": "t", "payload": "p", "retain": False},
                    "continue_on_error": True,
                }
            ],
        },
    )
    result = await _make_executor(script_home).execute("get_script", {"script": "Publisher"})
    step = result["config"]["sequence"][0]
    assert step["continue_on_error"] is True
    assert step["data"]["retain"] is False


async def test_state_values_are_still_quoted(script_home: HomeAssistant) -> None:
    """A bare `on` in a state position must come back as a string, not a bool."""
    from custom_components.selora_ai.mcp_server import _tool_set_script

    await _tool_set_script(
        script_home,
        {
            "alias": "Waiter",
            "sequence": [
                {"wait_for_trigger": [{"trigger": "state", "entity_id": "light.a", "to": "on"}]}
            ],
        },
    )
    result = await _make_executor(script_home).execute("get_script", {"script": "Waiter"})
    assert result["config"]["sequence"][0]["wait_for_trigger"][0]["to"] == "on"


async def test_an_unrelated_script_is_not_rewritten(script_home: HomeAssistant) -> None:
    """Every write rewrites the whole file, so a neighbour must survive intact."""
    from custom_components.selora_ai.mcp_server import _tool_set_script

    await _tool_set_script(
        script_home,
        {
            "alias": "Neighbour",
            "sequence": [{"service": "mqtt.publish", "data": {"retain": True}}],
        },
    )
    await _tool_set_script(
        script_home, {"alias": "Unrelated", "sequence": [{"delay": {"seconds": 1}}]}
    )
    result = await _make_executor(script_home).execute("get_script", {"script": "Neighbour"})
    assert result["config"]["sequence"][0]["data"]["retain"] is True


async def test_replacement_preserves_fields_and_variables(
    script_home: HomeAssistant,
) -> None:
    """set_script has no parameter for these; dropping them breaks every caller."""
    from custom_components.selora_ai.mcp_server import _tool_set_script

    await _tool_set_script(
        script_home,
        {
            "object_id": "greeter",
            "alias": "Greeter",
            "sequence": [{"delay": {"seconds": 1}}],
        },
    )
    # Add metadata the tool cannot express, as the UI or a package would.
    path = Path(script_home.config.path("scripts.yaml"))
    import yaml as _yaml

    raw = _yaml.safe_load(path.read_text())
    raw["greeter"]["fields"] = {"who": {"selector": {"text": None}}}
    raw["greeter"]["variables"] = {"greeting": "hello"}
    raw["greeter"]["max"] = 5
    path.write_text(_yaml.safe_dump(raw))

    await _tool_set_script(
        script_home,
        {
            "object_id": "greeter",
            "alias": "Greeter",
            "sequence": [{"delay": {"seconds": 2}}],
        },
    )
    result = await _make_executor(script_home).execute("get_script", {"script": "greeter"})
    config = result["config"]
    assert config["sequence"] == [{"delay": {"seconds": 2}}]
    assert config["fields"] == {"who": {"selector": {"text": None}}}
    assert config["variables"] == {"greeting": "hello"}
    assert config["max"] == 5


async def test_boolean_service_data_named_state_is_preserved(
    script_home: HomeAssistant,
) -> None:
    """`state` is a real service field on some integrations.

    evohome.set_dhw_override takes a boolean `state`; rewriting it by key name
    changed valid service data in every script in the file.
    """
    from custom_components.selora_ai.mcp_server import _tool_set_script

    await _tool_set_script(
        script_home,
        {
            "alias": "DHW",
            "sequence": [
                {"service": "evohome.set_dhw_override", "data": {"state": True, "mode": "x"}}
            ],
        },
    )
    result = await _make_executor(script_home).execute("get_script", {"script": "DHW"})
    assert result["config"]["sequence"][0]["data"]["state"] is True


async def test_bare_on_in_a_state_position_is_read_as_a_string(
    script_home: HomeAssistant,
) -> None:
    """The reader is ruamel in round-trip mode: YAML 1.2, so `on` is a string.

    This is why no boolean needs rewriting on the way out — a bool reaching the
    writer is always a genuine bool.
    """
    Path(script_home.config.path("scripts.yaml")).write_text(
        "waiter:\n"
        "  alias: Waiter\n"
        "  sequence:\n"
        "    - wait_for_trigger:\n"
        "        - trigger: state\n"
        "          entity_id: light.a\n"
        "          to: on\n",
        encoding="utf-8",
    )
    result = await _make_executor(script_home).execute("get_script", {"script": "waiter"})
    assert result["config"]["sequence"][0]["wait_for_trigger"][0]["to"] == "on"


async def test_creation_racing_a_creation_does_not_become_a_replacement(
    script_home: HomeAssistant,
) -> None:
    """The preview classifies create-vs-replace, then awaits before the write.

    Another caller can create the alias inside that window. Without the
    create-only expectation carried into the lock, the write re-resolves, sees
    a replacement, and overwrites a script that never got a confirmation card.
    """
    from custom_components.selora_ai.mcp_server import _preview_set_script, _tool_set_script
    from custom_components.selora_ai import script_manager as sm

    real_load = sm._load
    raced = False

    async def _load_then_race(hass):
        # Yield exactly where the preview does, and let the "other caller" win.
        nonlocal raced
        result = await real_load(hass)
        if not raced:
            raced = True
            await _tool_set_script(
                hass,
                {
                    "object_id": "movie_night",
                    "alias": "Movie Night",
                    "sequence": [{"delay": {"seconds": 42}}],
                },
            )
        return result

    with patch.object(sm, "_load", _load_then_race):
        result = await _preview_set_script(
            script_home,
            {"alias": "Movie Night", "sequence": [{"delay": {"seconds": 1}}]},
        )

    # Refused, not silently applied.
    assert "error" in result
    assert "while this request was in flight" in result["error"]

    # The winner's script is intact.
    survivor = await _make_executor(script_home).execute("get_script", {"script": "movie_night"})
    assert survivor["config"]["sequence"] == [{"delay": {"seconds": 42}}]


async def test_uncontended_creation_still_executes(script_home: HomeAssistant) -> None:
    """The guard must not turn ordinary creation into a refusal."""
    from custom_components.selora_ai.mcp_server import _preview_set_script

    result = await _preview_set_script(
        script_home, {"alias": "Quiet", "sequence": [{"delay": {"seconds": 1}}]}
    )
    assert result["status"] == "created"


async def test_ambiguous_script_alias_is_refused_not_guessed(
    script_home: HomeAssistant,
) -> None:
    """HA permits two scripts with the same alias; picking one is destructive.

    delete_script and set_script would have removed or overwritten whichever
    happened to come first in the mapping.
    """
    from custom_components.selora_ai.mcp_server import _tool_set_script
    from custom_components.selora_ai.script_manager import async_delete_script

    for object_id in ("movie_a", "movie_b"):
        await _tool_set_script(
            script_home,
            {
                "object_id": object_id,
                "alias": "Movie Night",
                "sequence": [{"delay": {"seconds": 1}}],
            },
        )

    executor = _make_executor(script_home)
    read = await executor.execute("get_script", {"script": "Movie Night"})
    assert "2 scripts are named" in read["error"]

    removed = await async_delete_script(script_home, "Movie Night")
    assert "2 scripts are named" in removed["error"]
    assert (await executor.execute("list_scripts", {}))["count"] == 2

    written = await _tool_set_script(
        script_home, {"alias": "Movie Night", "sequence": [{"delay": {"seconds": 9}}]}
    )
    assert "2 scripts are named" in written["error"]
    assert (await executor.execute("list_scripts", {}))["count"] == 2


async def test_object_id_disambiguates_a_shared_alias(script_home: HomeAssistant) -> None:
    """The error tells the caller to use an object_id — that must then work."""
    from custom_components.selora_ai.mcp_server import _tool_set_script
    from custom_components.selora_ai.script_manager import async_delete_script

    for object_id in ("movie_a", "movie_b"):
        await _tool_set_script(
            script_home,
            {
                "object_id": object_id,
                "alias": "Movie Night",
                "sequence": [{"delay": {"seconds": 1}}],
            },
        )
    result = await async_delete_script(script_home, "movie_a")
    assert result["status"] == "deleted"
    assert (await _make_executor(script_home).execute("list_scripts", {}))["count"] == 1


def test_held_actions_are_named_when_a_service_approval_wins() -> None:
    """A REVIEW call and a destructive action cannot share one card.

    They ask different questions with different scopes, so the service card
    wins — but nothing carries the held action forward, so it must be said out
    loud rather than silently dropped.
    """
    from custom_components.selora_ai.llm_client.command_policy import (
        synthesize_approval_from_tool_log,
    )

    tool_log = [
        {
            "tool": "execute_command",
            "arguments": {"service": "lock.unlock", "entity_id": "lock.front"},
            "result": {
                "requires_approval": True,
                "risk_level": "review",
                "call": {"service": "lock.unlock", "entity_id": "lock.front"},
            },
        },
        {
            "tool": "update_entity",
            "arguments": {"entity_id": "sensor.temperature", "disabled": True},
            "result": {
                "requires_approval": True,
                "destructive": {
                    "kind": "entity",
                    "verb": "disable",
                    "target_id": "sensor.temperature",
                    "entity_id": "sensor.temperature",
                    "name": "Temperature",
                    "label": "Disable Temperature",
                    "fingerprint": "fp",
                },
            },
        },
    ]
    upgraded = synthesize_approval_from_tool_log(
        {"intent": "answer", "response": ""}, tool_log, None, language="en"
    )
    assert "Disable Temperature" in upgraded["response"]
    assert "not touched" in upgraded["response"]


async def test_metadata_can_push_a_modest_script_over_the_limit(
    script_home: HomeAssistant,
) -> None:
    """editable must reflect the WHOLE result, not just the sequence.

    _truncate_result trims the longest list in an oversized result — the
    sequence — so a sequence under any per-sequence cap still loses steps once
    fields/variables push the payload over. Reporting editable there hands back
    a partial copy for a wholesale replace.
    """
    import yaml as _yaml

    from custom_components.selora_ai.mcp_server import _tool_set_script

    await _tool_set_script(
        script_home,
        {
            "object_id": "big",
            "alias": "Big",
            "sequence": [{"delay": {"seconds": i}} for i in range(120)],
        },
    )
    # Bulk the script out with metadata set_script cannot express.
    path = Path(script_home.config.path("scripts.yaml"))
    raw = _yaml.safe_load(path.read_text())
    raw["big"]["variables"] = {f"v{i}": "x" * 200 for i in range(60)}
    path.write_text(_yaml.safe_dump(raw))

    result = await _make_executor(script_home).execute("get_script", {"script": "big"})
    assert result["editable"] is False
    assert result["sequence_omitted"] is True
    assert "sequence" not in result["config"]
    # And the sequence alone is well under the old per-sequence ceiling.
    assert result["step_count"] == 120


async def test_a_small_script_with_metadata_stays_editable(
    script_home: HomeAssistant,
) -> None:
    """The stricter check must not make ordinary scripts uneditable."""
    import yaml as _yaml

    from custom_components.selora_ai.mcp_server import _tool_set_script

    await _tool_set_script(
        script_home,
        {"object_id": "small", "alias": "Small", "sequence": [{"delay": {"seconds": 1}}]},
    )
    path = Path(script_home.config.path("scripts.yaml"))
    raw = _yaml.safe_load(path.read_text())
    raw["small"]["fields"] = {"who": {"selector": {"text": None}}}
    path.write_text(_yaml.safe_dump(raw))

    result = await _make_executor(script_home).execute("get_script", {"script": "small"})
    assert result["editable"] is True
    assert result["config"]["fields"] == {"who": {"selector": {"text": None}}}


async def test_a_large_replacement_card_stays_bounded(script_home: HomeAssistant) -> None:
    """The card must not carry the sequence the model just sent.

    _truncate_result only finds lists at the top level or one dict deep, so a
    sequence nested under destructive.payload bypassed MAX_TOOL_RESULT_CHARS
    entirely and re-entered the model's context in full.
    """
    import json as _json

    from custom_components.selora_ai.const import MAX_TOOL_RESULT_CHARS
    from custom_components.selora_ai.mcp_server import _tool_set_script

    big = [{"service": "light.turn_on", "target": {"entity_id": f"light.l{i}"}} for i in range(400)]
    await _tool_set_script(
        script_home, {"object_id": "big", "alias": "Big", "sequence": [{"delay": {"seconds": 1}}]}
    )

    held = await _make_executor(script_home).execute(
        "set_script", {"object_id": "big", "alias": "Big", "sequence": big}
    )
    assert held["requires_approval"] is True
    assert len(_json.dumps(held, default=str)) < MAX_TOOL_RESULT_CHARS


def test_replay_arguments_come_from_the_tool_log() -> None:
    """Dropping the payload from the card must not lose the replay."""
    from custom_components.selora_ai.llm_client.command_policy import (
        _pending_destructive_from_log,
    )

    actions = _pending_destructive_from_log(
        [
            {
                "tool": "set_script",
                "arguments": {"object_id": "big", "alias": "Big", "sequence": [{"delay": 1}]},
                "result": {
                    "requires_approval": True,
                    "destructive": {
                        "kind": "script",
                        "verb": "replace",
                        "target_id": "big",
                        "entity_id": "script.big",
                        "name": "Big",
                        "label": "Replace the Big script",
                        "fingerprint": "fp",
                    },
                },
            }
        ]
    )
    assert len(actions) == 1
    assert actions[0]["payload"]["sequence"] == [{"delay": 1}]


async def test_external_edit_during_validation_is_not_clobbered(
    script_home: HomeAssistant,
) -> None:
    """SCRIPTS_YAML_LOCK only serialises this module's callers.

    HA's own script editor or a second worker can rewrite scripts.yaml while
    async_validate_config_item is awaiting, and writing the pre-await snapshot
    back would silently discard it.
    """
    import yaml as _yaml

    from custom_components.selora_ai import script_manager as sm
    from custom_components.selora_ai.mcp_server import _tool_set_script

    path = Path(script_home.config.path("scripts.yaml"))
    real_validate = None
    fired = False

    async def _validate_then_external_write(hass, object_id, config):
        # Someone outside this module writes the file mid-validation.
        nonlocal fired
        result = await real_validate(hass, object_id, config)
        if not fired:
            fired = True
            raw = _yaml.safe_load(path.read_text()) or {}
            raw["outsider"] = {"alias": "Outsider", "sequence": [{"delay": {"seconds": 7}}]}
            path.write_text(_yaml.safe_dump(raw))
        return result

    from homeassistant.components.script import config as script_config

    real_validate = script_config.async_validate_config_item
    with patch.object(script_config, "async_validate_config_item", _validate_then_external_write):
        result = await _tool_set_script(
            script_home, {"alias": "Mine", "sequence": [{"delay": {"seconds": 1}}]}
        )
    assert result["status"] == "created"

    # Both survive: ours was written onto the re-read file, not over it.
    listing = await _make_executor(script_home).execute("list_scripts", {})
    assert {s["alias"] for s in listing["scripts"]} == {"Mine", "Outsider"}
    assert sm.SCRIPTS_YAML_LOCK.locked() is False


async def test_external_edit_to_our_target_is_refused(script_home: HomeAssistant) -> None:
    """An unrelated concurrent edit is preserved; one to OUR script is refused."""
    import yaml as _yaml

    from custom_components.selora_ai.mcp_server import _preview_delete_script, _tool_set_script

    path = Path(script_home.config.path("scripts.yaml"))
    await _tool_set_script(
        script_home,
        {"object_id": "mine", "alias": "Mine", "sequence": [{"delay": {"seconds": 1}}]},
    )
    card = await _preview_delete_script(script_home, {"script": "mine"})
    fingerprint = card["delete"]["fingerprint"]

    real_validate = None
    fired = False

    async def _validate_then_touch_target(hass, object_id, config):
        nonlocal fired
        result = await real_validate(hass, object_id, config)
        if not fired:
            fired = True
            raw = _yaml.safe_load(path.read_text())
            raw["mine"]["sequence"] = [{"delay": {"seconds": 99}}]
            path.write_text(_yaml.safe_dump(raw))
        return result

    from homeassistant.components.script import config as script_config

    real_validate = script_config.async_validate_config_item
    with patch.object(script_config, "async_validate_config_item", _validate_then_touch_target):
        result = await _tool_set_script(
            script_home,
            {
                "object_id": "mine",
                "alias": "Mine",
                "sequence": [{"delay": {"seconds": 2}}],
                "expected_fingerprint": fingerprint,
            },
        )
    assert "has changed" in result["error"]

    current = await _make_executor(script_home).execute("get_script", {"script": "mine"})
    assert current["config"]["sequence"] == [{"delay": {"seconds": 99}}]
