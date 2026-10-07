"""Changing a label or a category, keeping what carries it.

Both could be created and deleted but not changed, so renaming or recolouring
one meant deleting it — stripping it from everything it was on — and making a
new one. ``create_label`` / ``create_category`` on a name already taken now
change that one, folded into the existing tools so the chat schema stays small.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import category_registry as cr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr

from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import TOOL_CREATE_CATEGORY, TOOL_CREATE_LABEL


async def _mcp(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    return await mcp_dispatch._get_tool_handlers()[tool](hass, arguments)


async def test_a_label_is_renamed_and_recoloured_and_stays_on_its_entities(
    hass: HomeAssistant,
) -> None:
    label = lr.async_get(hass).async_create("holiday", color="blue", icon="mdi:beach")
    entry = er.async_get(hass).async_get_or_create("light", "demo", "porch")
    er.async_get(hass).async_update_entity(entry.entity_id, labels={label.label_id})

    result = await _mcp(
        hass,
        TOOL_CREATE_LABEL,
        name="holiday",
        new_name="Away",
        color="red",
        description="When nobody is home",
        clear=["icon"],
    )

    assert result["status"] == "updated", result
    assert result["changed"] == ["color", "description", "icon", "name"]
    updated = lr.async_get(hass).async_get_label(label.label_id)
    assert (updated.name, updated.color, updated.icon) == ("Away", "red", None)
    assert er.async_get(hass).async_get(entry.entity_id).labels == {label.label_id}


async def test_a_taken_name_with_nothing_to_change_is_reported(hass: HomeAssistant) -> None:
    lr.async_get(hass).async_create("holiday")

    result = await _mcp(hass, TOOL_CREATE_LABEL, name="holiday")

    assert result["status"] == "exists"
    assert len(lr.async_get(hass).async_list_labels()) == 1


async def test_a_label_change_that_cannot_be_made_is_refused(hass: HomeAssistant) -> None:
    lr.async_get(hass).async_create("holiday")
    lr.async_get(hass).async_create("Away")

    onto_another = await _mcp(hass, TOOL_CREATE_LABEL, name="holiday", new_name="Away")
    missing = await _mcp(hass, TOOL_CREATE_LABEL, name="nope", new_name="x")
    both = await _mcp(hass, TOOL_CREATE_LABEL, name="holiday", icon="mdi:x", clear=["icon"])

    assert "already exists" in onto_another["error"]
    assert "no label 'nope'" in missing["error"]
    assert "set and cleared" in both["error"]
    assert {label.name for label in lr.async_get(hass).async_list_labels()} == {"holiday", "Away"}


async def test_a_category_is_renamed_and_keeps_its_automations(hass: HomeAssistant) -> None:
    category = cr.async_get(hass).async_create(scope="automation", name="Lights", icon="mdi:lamp")
    entry = er.async_get(hass).async_get_or_create("automation", "automation", "porch")
    er.async_get(hass).async_update_entity(
        entry.entity_id, categories={"automation": category.category_id}
    )

    renamed = await _mcp(
        hass, TOOL_CREATE_CATEGORY, scope="automation", name="Lights", new_name="Lighting"
    )
    cleared = await _mcp(
        hass, TOOL_CREATE_CATEGORY, scope="automation", name="Lighting", clear=["icon"]
    )

    assert renamed["status"] == cleared["status"] == "updated"
    updated = cr.async_get(hass).async_get_category(
        scope="automation", category_id=category.category_id
    )
    assert (updated.name, updated.icon) == ("Lighting", None)
    assert er.async_get(hass).async_get(entry.entity_id).categories == {
        "automation": category.category_id
    }


async def test_a_category_rename_onto_a_taken_name_is_refused(hass: HomeAssistant) -> None:
    cr.async_get(hass).async_create(scope="script", name="Morning")
    cr.async_get(hass).async_create(scope="script", name="Evening")

    result = await _mcp(
        hass, TOOL_CREATE_CATEGORY, scope="script", name="Morning", new_name="evening"
    )

    assert "already exists" in result["error"]


async def test_a_label_is_found_by_its_name_not_its_id(hass: HomeAssistant) -> None:
    """A label renamed away from "Kitchen" keeps the id `kitchen`."""
    kitchen = lr.async_get(hass).async_create("Kitchen")
    lr.async_get(hass).async_update(kitchen.label_id, name="Cooking")

    result = await _mcp(hass, TOOL_CREATE_LABEL, name="kitchen", color="red")

    assert result["status"] == "created"
    assert lr.async_get(hass).async_get_label(kitchen.label_id).color is None
