"""Tests for the camera snapshot MCP tool, which answers with an image block."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.selora_ai.mcp_server import access as mcp_access
from custom_components.selora_ai.mcp_server import cameras as mcp_cameras
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import TOOL_GET_CAMERA_IMAGE
from custom_components.selora_ai.selora_auth import SeloraAuthContext

JPEG = b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9"
ADMIN = SeloraAuthContext("admin", None, True, "ha_token")


def Image(content_type: str, content: bytes) -> SimpleNamespace:  # noqa: N802
    """Home Assistant's camera ``Image`` shape, without importing the camera
    component — it needs image libraries the test environment lacks."""
    return SimpleNamespace(content_type=content_type, content=content)


@pytest.fixture(autouse=True)
def _camera_loaded(hass: HomeAssistant) -> None:
    hass.config.components.add("camera")


async def _call(hass: HomeAssistant, image: Any, **arguments: Any) -> tuple[dict, AsyncMock]:
    fetch = (
        AsyncMock(side_effect=image)
        if isinstance(image, Exception)
        else AsyncMock(return_value=image)
    )
    with patch.object(mcp_cameras, "_fetch_image", fetch):
        result = await mcp_dispatch._jsonrpc_dispatch(
            hass,
            "tools/call",
            {"name": TOOL_GET_CAMERA_IMAGE, "arguments": arguments},
            ADMIN,
        )
    return result, fetch


async def test_a_snapshot_comes_back_as_an_image_block(hass: HomeAssistant) -> None:
    hass.states.async_set("camera.front_door", "idle")

    result, fetch = await _call(hass, Image("image/jpg", JPEG), entity_id="camera.front_door")

    text, image = result["content"]
    assert text["type"] == "text"
    assert '"bytes": ' in text["text"]
    assert image == {
        "type": "image",
        "data": base64.b64encode(JPEG).decode(),
        "mimeType": "image/jpeg",
    }
    assert fetch.await_args.args[1:] == ("camera.front_door", 1280, 720)


async def test_the_size_asked_for_is_clamped(hass: HomeAssistant) -> None:
    hass.states.async_set("camera.front_door", "idle")

    _result, fetch = await _call(
        hass, Image("image/jpeg", JPEG), entity_id="camera.front_door", width=10000, height=50
    )

    assert fetch.await_args.args[2:] == (3840, 120)


async def test_only_a_camera_is_asked(hass: HomeAssistant) -> None:
    hass.states.async_set("light.kitchen", "on")

    result, fetch = await _call(hass, Image("image/jpeg", JPEG), entity_id="light.kitchen")

    assert "is not a camera" in result["content"][0]["text"]
    assert len(result["content"]) == 1
    fetch.assert_not_awaited()


async def test_a_camera_with_no_image_says_so(hass: HomeAssistant) -> None:
    hass.states.async_set("camera.garage", "unavailable")

    result, _fetch = await _call(
        hass, HomeAssistantError("Unable to get image"), entity_id="camera.garage"
    )

    assert "gave no image" in result["content"][0]["text"]


async def test_what_is_not_an_image_is_not_sent(hass: HomeAssistant) -> None:
    hass.states.async_set("camera.front_door", "idle")

    result, _fetch = await _call(hass, Image("text/html", b"<html>"), entity_id="camera.front_door")

    assert "not an image" in result["content"][0]["text"]


async def test_an_oversized_snapshot_asks_for_a_smaller_one(hass: HomeAssistant) -> None:
    hass.states.async_set("camera.front_door", "idle")

    result, _fetch = await _call(
        hass, Image("image/png", b"\0" * (5 * 1024 * 1024)), entity_id="camera.front_door"
    )

    assert "smaller width and height" in result["content"][0]["text"]


async def test_a_read_only_credential_cannot_look(hass: HomeAssistant) -> None:
    hass.states.async_set("camera.front_door", "idle")
    read_only = SeloraAuthContext("viewer", None, False, "ha_token")

    assert TOOL_GET_CAMERA_IMAGE in mcp_access._ADMIN_TOOLS
    assert not mcp_access._can_access_tool(read_only, TOOL_GET_CAMERA_IMAGE)


async def test_no_camera_integration_says_so(hass: HomeAssistant) -> None:
    hass.states.async_set("camera.front_door", "idle")
    hass.config.components.remove("camera")

    result, fetch = await _call(hass, Image("image/jpeg", JPEG), entity_id="camera.front_door")

    assert "not loaded" in result["content"][0]["text"]
    fetch.assert_not_awaited()
