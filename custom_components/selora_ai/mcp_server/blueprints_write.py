"""MCP tools that import a blueprint from a URL or delete one, after a confirmation."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant


async def _tool_import_blueprint(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """See ``blueprint_import``."""
    from ..blueprint_import import async_import_blueprint  # noqa: PLC0415
    from ..tool_executor import _opt_bool, _opt_str  # noqa: PLC0415

    return await async_import_blueprint(
        hass,
        str(arguments.get("url") or ""),
        confirmed=_opt_bool(arguments.get("confirmed")) is True,
        content_hash=_opt_str(arguments.get("content_hash")),
        overwrite=_opt_bool(arguments.get("overwrite")) is True,
        replaces_hash=_opt_str(arguments.get("replaces_hash")),
    )


async def _tool_delete_blueprint(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """See ``blueprint_import``."""
    from ..blueprint_import import async_delete_blueprint  # noqa: PLC0415
    from ..tool_executor import _opt_bool  # noqa: PLC0415

    return await async_delete_blueprint(
        hass,
        str(arguments.get("domain") or ""),
        str(arguments.get("path") or ""),
        confirmed=_opt_bool(arguments.get("confirmed")) is True,
    )
