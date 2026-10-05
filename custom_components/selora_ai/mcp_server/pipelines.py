"""MCP tools for Assist pipelines: list, create or change, delete."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant


async def _tool_list_assist_pipelines(
    hass: HomeAssistant, _arguments: dict[str, Any]
) -> dict[str, Any]:
    """Every pipeline and the engines to pick from — see ``assist_pipelines``."""
    from ..assist_pipelines import async_list_pipelines  # noqa: PLC0415

    return await async_list_pipelines(hass)


async def _tool_set_assist_pipeline(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Create a pipeline, or change the one named, and optionally prefer it."""
    from ..assist_pipelines import async_set_pipeline  # noqa: PLC0415
    from ..tool_executor import _opt_bool, _opt_str  # noqa: PLC0415

    return await async_set_pipeline(
        hass,
        arguments,
        pipeline_id=_opt_str(arguments.get("pipeline_id")),
        preferred=_opt_bool(arguments.get("preferred")) is True,
    )


async def _tool_delete_assist_pipeline(
    hass: HomeAssistant, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Delete a pipeline that is not the preferred one."""
    from ..assist_pipelines import async_delete_pipeline  # noqa: PLC0415

    return await async_delete_pipeline(hass, str(arguments.get("pipeline_id") or ""))
