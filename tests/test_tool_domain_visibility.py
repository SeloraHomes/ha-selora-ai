"""What the read tools can see.

``COLLECTOR_DOMAINS`` decides what is worth snapshotting and pattern-analysing,
and is additionally the second half of the safe-command allowlist. It used to
decide what the READ tools could show as well, so a home's cameras — a domain
with no safe-command table — were invisible to every one of them at once:
asked for a dashboard view of all the cameras, the assistant answered that the
Reolinks expose motion sensors and no camera entities, which is a description
of the filter presented as a fact about the house.

These pin the split per surface, because each one is separately capable of
being the tool the model happens to reach for.
"""

from __future__ import annotations

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.selora_ai.entity_capabilities import (
    COLLECTOR_DOMAINS,
    is_inspectable_entity,
)
from custom_components.selora_ai.mcp_server import (
    _tool_find_entities_by_area,
    _tool_get_device,
    _tool_get_home_snapshot,
    _tool_list_devices,
    _tool_search_entities,
)


async def _setup_lovelace(hass: HomeAssistant) -> None:
    """A storage-mode default dashboard with one empty page."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "lovelace", {})
    await hass.async_block_till_done()
    config = hass.data["lovelace"].dashboards[None]
    await config.async_save({"views": [{"title": "Home", "cards": []}]})


async def _stored_views(hass: HomeAssistant) -> list[dict]:
    config = hass.data["lovelace"].dashboards[None]
    document = await config.async_load(False)
    return list(document.get("views", []))


@pytest.fixture
async def reolink(hass: HomeAssistant):
    """A camera device shaped like the Reolink integration's output."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain="reolink", entry_id="mock_reolink")
    entry.add_to_hass(hass)

    area_reg = ar.async_get(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    garden = area_reg.async_create("Garden")
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("reolink", "front_gate")},
        name="Front Gate",
        manufacturer="Reolink",
        model="RLC-811A",
    )
    dev_reg.async_update_device(device.id, area_id=garden.id)

    ent_reg.async_get_or_create(
        "camera",
        "reolink",
        "front_gate_clear",
        suggested_object_id="front_gate_clear",
        device_id=device.id,
    )
    hass.states.async_set("camera.front_gate_clear", "idle", {"friendly_name": "Front Gate Clear"})

    # The entities that WERE visible, and so became the whole answer.
    ent_reg.async_get_or_create(
        "binary_sensor",
        "reolink",
        "front_gate_motion",
        suggested_object_id="front_gate_motion",
        device_id=device.id,
    )
    hass.states.async_set(
        "binary_sensor.front_gate_motion", "off", {"friendly_name": "Front Gate Motion"}
    )

    # Assist plumbing: a registry entity with nothing in the house behind it.
    ent_reg.async_get_or_create("tts", "reolink", "piper", suggested_object_id="piper")
    hass.states.async_set("tts.piper", "unknown", {"friendly_name": "Piper"})

    return {"device_id": device.id, "area": "Garden"}


def test_camera_is_inspectable_but_not_collected() -> None:
    """The split itself: discovery is not permission, and not analysis."""
    assert is_inspectable_entity("camera.front_gate_clear") is True
    assert "camera" not in COLLECTOR_DOMAINS


def test_assist_plumbing_stays_hidden() -> None:
    """The deny-list is four domains wide and nothing else."""
    assert is_inspectable_entity("tts.piper") is False
    assert is_inspectable_entity("conversation.home_assistant") is False
    assert is_inspectable_entity("update.core") is True


@pytest.mark.asyncio
async def test_search_entities_finds_a_camera(hass: HomeAssistant, reolink) -> None:
    result = await _tool_search_entities(hass, {"query": "front gate", "domain": "camera"})
    assert [m["entity_id"] for m in result["matches"]] == ["camera.front_gate_clear"]


@pytest.mark.asyncio
async def test_search_entities_lists_a_whole_domain(hass: HomeAssistant, reolink) -> None:
    """ "All my cameras" has no name to search for, so the domain stands alone.

    Refusing a bare domain filter meant the one phrasing the tool description
    advertises came back an error, and the model relayed that as being unable
    to do it.
    """
    result = await _tool_search_entities(hass, {"domain": "camera"})
    assert [m["entity_id"] for m in result["matches"]] == ["camera.front_gate_clear"]


@pytest.mark.asyncio
async def test_search_entities_still_needs_one_filter(hass: HomeAssistant, reolink) -> None:
    """A call carrying no query and no filter is the only refusal left."""
    result = await _tool_search_entities(hass, {})
    assert "domain" in result["error"]


@pytest.mark.asyncio
async def test_home_snapshot_carries_the_camera(hass: HomeAssistant, reolink) -> None:
    result = await _tool_get_home_snapshot(hass)
    garden = [e["entity_id"] for e in result["areas"]["Garden"]]
    assert "camera.front_gate_clear" in garden


@pytest.mark.asyncio
async def test_home_snapshot_inherits_the_device_area(hass: HomeAssistant, reolink) -> None:
    """An entity with no area of its own is placed by its device's.

    An entity-level ``area_id`` is an override that most homes never set, so
    reading it alone reported an entire house as unassigned — in a snapshot
    whose whole shape is a grouping by area.
    """
    ent_reg = er.async_get(hass)
    assert ent_reg.async_get("camera.front_gate_clear").area_id is None

    result = await _tool_get_home_snapshot(hass)
    assert "camera.front_gate_clear" in [e["entity_id"] for e in result["areas"]["Garden"]]
    assert "camera.front_gate_clear" not in [e["entity_id"] for e in result["unassigned"]]


@pytest.mark.asyncio
async def test_find_entities_by_area_carries_the_camera(hass: HomeAssistant, reolink) -> None:
    result = await _tool_find_entities_by_area(hass, {"area": "garden"})
    assert "camera.front_gate_clear" in [e["entity_id"] for e in result["entities"]]


@pytest.mark.asyncio
async def test_get_device_lists_the_camera(hass: HomeAssistant, reolink) -> None:
    """The failing answer named this device's sensors and denied the camera."""
    result = await _tool_get_device(hass, {"device_id": reolink["device_id"]})
    ids = [e["entity_id"] for e in result["entities"]]
    assert "camera.front_gate_clear" in ids
    assert "binary_sensor.front_gate_motion" in ids


@pytest.mark.asyncio
async def test_list_devices_keeps_a_camera_only_device(
    hass: HomeAssistant, hass_admin_user
) -> None:
    """A device whose every entity is a camera is a device, not an empty row.

    ``list_devices`` drops a device with no visible entities, so the domain
    filter did not merely trim that row's `entities` — it removed the device
    from the listing outright.
    """
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain="reolink", entry_id="mock_camera_only")
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("reolink", "doorbell")},
        name="Doorbell",
        manufacturer="Reolink",
    )
    ent_reg.async_get_or_create(
        "camera",
        "reolink",
        "doorbell_clear",
        suggested_object_id="doorbell_clear",
        device_id=device.id,
    )
    hass.states.async_set("camera.doorbell_clear", "idle", {"friendly_name": "Doorbell Clear"})

    result = await _tool_list_devices(hass, {})
    row = next(d for d in result["devices"] if d["device_id"] == device.id)
    assert [e["entity_id"] for e in row["entities"]] == ["camera.doorbell_clear"]
    assert row["domains"] == ["camera"]


@pytest.mark.asyncio
async def test_diagnostic_noise_still_filtered(hass: HomeAssistant) -> None:
    """Widening the domains must not widen the per-domain exclusions.

    ``is_actionable_entity``'s patterns are what keep a camera's own IR
    illuminator out of a lighting answer, and they still run.
    """
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        "light",
        "test",
        "ir_uid",
        suggested_object_id="front_gate_ir_light",
        entity_category=EntityCategory.CONFIG,
    )
    hass.states.async_set(
        "light.front_gate_ir_light", "off", {"friendly_name": "Front Gate IR Light"}
    )
    assert is_inspectable_entity("light.front_gate_ir_light") is False

    result = await _tool_search_entities(hass, {"query": "front gate"})
    assert result["matches"] == []


# ── The whole request, end to end ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_camera_dashboard_page_is_created_filled(hass: HomeAssistant, reolink) -> None:
    """ "A dashboard with all my cameras", from resolution to stored cards.

    Every other test here stops at a tool boundary, and this failure lived
    between them: the search found nothing, the model guessed `camera.*` ids
    from the device names, the card write refused them as unknown entities —
    correctly — and the page had already been created, so the user was left
    with an empty dashboard under a reply explaining why it was empty.
    """
    from custom_components.selora_ai.dashboard_manager import async_add_view

    await _setup_lovelace(hass)

    found = await _tool_search_entities(hass, {"domain": "camera"})
    camera_ids = [m["entity_id"] for m in found["matches"]]
    assert camera_ids == ["camera.front_gate_clear"]

    result = await async_add_view(
        hass,
        title="All Cameras",
        path="all-cameras",
        cards=[
            {"type": "picture-entity", "entity": cid, "camera_view": "live"} for cid in camera_ids
        ],
    )
    assert result["status"] == "created"
    assert result["cards_added"] == 1
    assert "empty" not in result["note"]

    stored = await _stored_views(hass)
    assert stored[result["view_index"]]["cards"][0]["entity"] == "camera.front_gate_clear"


@pytest.mark.asyncio
async def test_an_unresolvable_card_leaves_no_page_behind(hass: HomeAssistant, reolink) -> None:
    """All-or-nothing: the refusal that used to arrive one write too late."""
    from custom_components.selora_ai.dashboard_manager import async_add_view

    await _setup_lovelace(hass)
    before = await _stored_views(hass)

    result = await async_add_view(
        hass,
        title="All Cameras",
        cards=[{"type": "picture-entity", "entity": "camera.invented"}],
    )
    assert "camera.invented" in result["error"]
    assert await _stored_views(hass) == before


@pytest.mark.asyncio
async def test_a_placeholder_card_is_refused_not_dropped(hass: HomeAssistant, reolink) -> None:
    """`{}` is falsy AND schema-valid, so dropping it as padding reports
    success having created the empty page the whole argument prevents."""
    from custom_components.selora_ai.dashboard_manager import async_add_view

    await _setup_lovelace(hass)
    before = await _stored_views(hass)

    result = await async_add_view(hass, title="All Cameras", cards=[{}])
    assert "type" in result["error"]
    assert await _stored_views(hass) == before

    # A list of padding alone still means "no cards", and creates the page.
    result = await async_add_view(hass, title="Empty On Purpose", cards=[None, ""])
    assert result["status"] == "created"
    assert result["cards_added"] == 0


@pytest.mark.asyncio
async def test_a_cards_argument_of_the_wrong_shape_is_refused(hass: HomeAssistant, reolink) -> None:
    """A present-but-malformed `cards` is not an omission.

    Nothing validates tool arguments against the schema before dispatch, so a
    model emitting a string where the list belongs reached a coercion that read
    it as absent — and created the empty page all of this exists to prevent.
    """
    from custom_components.selora_ai.tool_executor import add_view_kwargs
    from custom_components.selora_ai.dashboard_manager import async_add_view

    await _setup_lovelace(hass)
    before = await _stored_views(hass)

    kwargs = add_view_kwargs({"title": "All Cameras", "cards": "camera.front_gate_clear"})
    result = await async_add_view(hass, **kwargs)
    assert "type" in result["error"]
    assert await _stored_views(hass) == before

    # A bare card object where the list belongs is accepted, not refused.
    kwargs = add_view_kwargs(
        {
            "title": "One Camera",
            "cards": {"type": "picture-entity", "entity": "camera.front_gate_clear"},
        }
    )
    result = await async_add_view(hass, **kwargs)
    assert result["cards_added"] == 1


@pytest.mark.asyncio
async def test_a_password_helper_is_listed_but_its_value_is_not(hass: HomeAssistant) -> None:
    """Widening the domains must not bulk-export secrets.

    `input_text` and `text` entities hold their value AS their state, and
    `mode: password` is HA's own mark that it is one. The entity stays
    discoverable — hiding it would report it to its owner as absent, the
    failure this change is about — and only the value is withheld.
    """
    hass.states.async_set(
        "input_text.wifi_password",
        "hunter2-correct-horse",
        {"friendly_name": "WiFi Password", "mode": "password"},
    )
    hass.states.async_set(
        "input_text.guest_note",
        "back door key under mat",
        {"friendly_name": "Guest Note", "mode": "text"},
    )

    found = await _tool_search_entities(hass, {"query": "password"})
    secret = next(m for m in found["matches"] if m["entity_id"] == "input_text.wifi_password")
    assert secret["state"] == "***"

    snapshot = await _tool_get_home_snapshot(hass)
    rows = {e["entity_id"]: e["state"] for e in snapshot["unassigned"]}
    assert rows["input_text.wifi_password"] == "***"
    # A plain text helper is not a secret, and reading it is the point of it.
    assert rows["input_text.guest_note"] == "back door key under mat"
