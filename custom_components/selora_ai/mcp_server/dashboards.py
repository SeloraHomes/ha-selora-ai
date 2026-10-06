"""MCP tools for dashboards, their resources, and configuration YAML."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .common import _sanitize

_LOGGER = logging.getLogger(__name__)


# ── Dashboard tools ───────────────────────────────────────────────────────────


async def _tool_list_dashboards(hass: HomeAssistant, _arguments: dict[str, Any]) -> dict[str, Any]:
    """Every dashboard, with whether it can be edited.

    Without this an MCP client has no way to learn a non-default dashboard's
    ``url_path``, which every other tool here takes as ``dashboard_target``.
    """
    from ..dashboard_manager import list_dashboards  # noqa: PLC0415

    return {"dashboards": await list_dashboards(hass)}


async def _tool_insert_dashboard_card(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Append a card to a view.

    The other write tools all edit a card that is already there, so without this
    a view created with ``selora_add_dashboard_view`` could never be filled.
    """
    from ..dashboard_manager import async_insert_card  # noqa: PLC0415

    return await async_insert_card(hass, arguments)


async def _tool_get_dashboard(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """A dashboard's views, and the cards on one of them."""
    from ..dashboard_manager import async_get_dashboard  # noqa: PLC0415
    from ..tool_executor import _opt_str  # noqa: PLC0415

    return await async_get_dashboard(
        hass, _opt_str(arguments.get("dashboard_target")), arguments.get("view")
    )


async def _tool_get_dashboard_card(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """One card's full configuration plus its fingerprint."""
    from ..dashboard_manager import async_get_card  # noqa: PLC0415
    from ..tool_executor import _as_index, _opt_str  # noqa: PLC0415

    return await async_get_card(
        hass,
        _opt_str(arguments.get("dashboard_target")),
        arguments.get("view"),
        _as_index(arguments.get("card_index")),
    )


async def _tool_add_dashboard_view(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Append a view to a dashboard."""
    from ..dashboard_manager import async_add_view  # noqa: PLC0415
    from ..tool_executor import add_view_kwargs  # noqa: PLC0415

    return await async_add_view(hass, **add_view_kwargs(arguments))


async def _tool_update_dashboard_view(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Change a view's title, path, icon, layout or options."""
    from ..dashboard_manager import async_update_view  # noqa: PLC0415
    from ..tool_executor import update_view_kwargs  # noqa: PLC0415

    return await async_update_view(hass, **update_view_kwargs(arguments))


async def _tool_remove_dashboard_view(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Remove a view outright (MCP clients run their own confirmation)."""
    from ..dashboard_manager import async_remove_view  # noqa: PLC0415
    from ..tool_executor import _opt_str  # noqa: PLC0415

    return await async_remove_view(
        hass,
        target=_opt_str(arguments.get("dashboard_target")),
        view=arguments.get("view"),
        expected_fingerprint=_opt_str(arguments.get("expected_fingerprint")),
    )


async def _tool_update_dashboard_card(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Replace one card."""
    from ..dashboard_manager import async_update_card  # noqa: PLC0415
    from ..tool_executor import _as_index, _opt_str  # noqa: PLC0415

    card = arguments.get("card")
    if not isinstance(card, dict):
        return {"error": "card must be an object with a 'type' field."}
    return await async_update_card(
        hass,
        target=_opt_str(arguments.get("dashboard_target")),
        view=arguments.get("view"),
        card_index=_as_index(arguments.get("card_index")),
        card=card,
        expected_fingerprint=_opt_str(arguments.get("expected_fingerprint")),
    )


async def _tool_remove_dashboard_card(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Remove one card."""
    from ..dashboard_manager import async_remove_card  # noqa: PLC0415
    from ..tool_executor import _as_index, _opt_str  # noqa: PLC0415

    return await async_remove_card(
        hass,
        target=_opt_str(arguments.get("dashboard_target")),
        view=arguments.get("view"),
        card_index=_as_index(arguments.get("card_index")),
        expected_fingerprint=_opt_str(arguments.get("expected_fingerprint")),
    )


async def _tool_move_dashboard_card(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Reposition one card or several, within a view or onto another view or dashboard."""
    from ..dashboard_manager import async_move_card  # noqa: PLC0415
    from ..tool_executor import move_card_kwargs  # noqa: PLC0415

    return await async_move_card(hass, **move_card_kwargs(arguments))


async def _tool_group_dashboard_cards(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Wrap existing cards in a grid or stack."""
    from ..dashboard_manager import async_group_cards  # noqa: PLC0415
    from ..tool_executor import _as_index, _opt_str  # noqa: PLC0415

    raw = arguments.get("card_indices")
    container = arguments.get("container")
    return await async_group_cards(
        hass,
        target=_opt_str(arguments.get("dashboard_target")),
        view=arguments.get("view"),
        card_indices=[_as_index(i) for i in raw] if isinstance(raw, list) else [],
        container=container if isinstance(container, dict) else {},
        expected_view_fingerprint=_opt_str(arguments.get("expected_view_fingerprint")),
    )


async def _tool_create_dashboard(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a dashboard on the spot — MCP has no panel to defer it to."""
    from ..dashboard_manager import async_create_dashboard  # noqa: PLC0415
    from ..tool_executor import _bool_default_true, _opt_bool, _opt_str  # noqa: PLC0415

    return await async_create_dashboard(
        hass,
        title=str(arguments.get("title", "")),
        url_path=_opt_str(arguments.get("url_path")),
        icon=_opt_str(arguments.get("icon")),
        require_admin=_opt_bool(arguments.get("require_admin")) or False,
        show_in_sidebar=_bool_default_true(_opt_bool(arguments.get("show_in_sidebar"))),
    )


async def _tool_delete_dashboard(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a dashboard on the spot (MCP clients run their own confirmation)."""
    from ..dashboard_manager import async_delete_dashboard  # noqa: PLC0415

    return await async_delete_dashboard(hass, str(arguments.get("dashboard_target", "")))


async def _tool_update_dashboard(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Change a dashboard's title, icon, sidebar visibility or admin-only flag."""
    from ..dashboard_manager import async_update_dashboard  # noqa: PLC0415
    from ..tool_executor import update_dashboard_kwargs  # noqa: PLC0415

    return await async_update_dashboard(hass, **update_dashboard_kwargs(arguments))


async def _tool_list_dashboard_resources(
    hass: HomeAssistant, _arguments: dict[str, Any]
) -> dict[str, Any]:
    """The JS and CSS resources custom cards load — see ``dashboard_resources``."""
    from ..dashboard_resources import async_list_resources  # noqa: PLC0415

    return await async_list_resources(hass)


async def _tool_add_dashboard_resource(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Register a resource; an external URL only once confirmed."""
    from ..dashboard_resources import async_add_resource  # noqa: PLC0415
    from ..tool_executor import _opt_bool  # noqa: PLC0415

    return await async_add_resource(
        hass,
        str(arguments.get("url", "")),
        str(arguments.get("type") or "module"),
        confirmed=_opt_bool(arguments.get("confirmed")) is True,
    )


async def _tool_remove_dashboard_resource(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Unregister a resource outright (MCP clients run their own confirmation)."""
    from ..dashboard_resources import async_remove_resource  # noqa: PLC0415

    return await async_remove_resource(hass, str(arguments.get("resource", "")))


async def _tool_update_dashboard_resource(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Point a resource at a new URL or type — see ``dashboard_resources``."""
    from ..dashboard_resources import async_update_resource  # noqa: PLC0415
    from ..tool_executor import _opt_bool, _opt_str  # noqa: PLC0415

    return await async_update_resource(
        hass,
        str(arguments.get("resource", "")),
        url=_opt_str(arguments.get("url")),
        resource_type=_opt_str(arguments.get("type")),
        confirmed=_opt_bool(arguments.get("confirmed")) is True,
    )


async def _tool_set_dashboard_strategy(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Generate a dashboard from a strategy — see ``dashboard_manager``."""
    from ..dashboard_manager import async_set_dashboard_strategy  # noqa: PLC0415
    from ..tool_executor import _opt_bool, _opt_options, _opt_str  # noqa: PLC0415

    return await async_set_dashboard_strategy(
        hass,
        target=_opt_str(arguments.get("dashboard_target")),
        strategy=_opt_options(arguments.get("strategy")),
        confirmed=_opt_bool(arguments.get("confirmed")) is True,
        expected_fingerprint=_opt_str(arguments.get("fingerprint")),
    )


async def _tool_get_config_yaml(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Read configuration YAML — see ``config_yaml``."""
    from ..config_yaml import async_read  # noqa: PLC0415
    from ..tool_executor import _opt_str  # noqa: PLC0415

    return await async_read(
        hass,
        str(arguments.get("file") or "configuration.yaml"),
        _opt_str(arguments.get("yaml_path")),
    )


async def _tool_set_config_yaml(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Preview, or with the preview's token apply, a configuration YAML edit."""
    from ..config_yaml import async_edit  # noqa: PLC0415
    from ..tool_executor import _opt_str  # noqa: PLC0415

    content = arguments.get("content")
    return await async_edit(
        hass,
        file=str(arguments.get("file") or "configuration.yaml"),
        yaml_path=str(arguments.get("yaml_path", "")),
        action=str(arguments.get("action", "")),
        content=content if isinstance(content, str) else None,
        confirm_token=_opt_str(arguments.get("confirm_token")),
    )


async def _preview_remove_dashboard_view(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Resolve a view removal WITHOUT performing it.

    The label carries the card count because that is the blast radius the user
    cannot see from the request: "delete the Garage page" does not say it takes
    eleven cards with it, and the tool-loop short-circuit discards whatever
    prose the model wrote.

    The view's content hash doubles as the identity check. A view has no id, and
    its index shifts when an earlier view is removed, so the hash is re-verified
    against the freshly-loaded document at confirm time.

    A caller may also pass ``expected_fingerprint`` from its own earlier read, in
    which case the mismatch is caught here rather than after the user has already
    tapped confirm on a card naming the wrong page.
    """
    from ..dashboard_manager import (  # noqa: PLC0415
        _flat_cards,
        _load_config,
        _views,
        _writable_dashboard,
        resolve_view,
        view_fingerprint,
    )
    from ..tool_executor import _opt_str  # noqa: PLC0415

    target = _opt_str(arguments.get("dashboard_target"))
    config, error = _writable_dashboard(hass, target)
    if error or config is None:
        return {"error": error or "Dashboard not found."}
    document, error = await _load_config(config)
    if error:
        return {"error": error}

    index, error = resolve_view(document, arguments.get("view"))
    if error or index is None:
        return {"error": error}

    view = _views(document)[index]
    expected = _opt_str(arguments.get("expected_fingerprint"))
    if expected and view_fingerprint(view) != expected:
        return {
            "error": (
                "That view has changed since it was read — the index now points at "
                "a different page. Read the dashboard again and retry."
            )
        }

    title = _sanitize(view.get("title") or view.get("path") or f"view {index}")
    card_count = len(_flat_cards(view))
    label = f"Delete the {title} page from {target or 'lovelace'}"
    if card_count:
        label = f"{label} — and its {card_count} card{'s' if card_count != 1 else ''}"

    return {
        "requires_approval": True,
        "destructive": {
            "kind": "dashboard_view",
            "verb": "remove_view",
            "target_id": f"{target or ''}#{index}",
            "entity_id": "",
            "name": title,
            "label": label,
            # The CONTENT of the view, not its card count. Counts collide — two
            # pages with two cards each are indistinguishable — so a reorder
            # between this card and the tap would pass a count check and delete
            # the wrong page.
            "fingerprint": view_fingerprint(view),
        },
    }
