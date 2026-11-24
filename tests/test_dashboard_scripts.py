"""Dashboard writes never introduce JavaScript a browser would run.

Unit cases pin the markers; the rest drive the real chat tools against HA's
Lovelace storage, through ``test_dashboard_tools``'s ``board`` fixture.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai.dashboard_scripts import (
    introduced_script_error,
    script_strings,
)
from custom_components.selora_ai.tool_executor import ToolExecutor

from .test_dashboard_tools import (  # noqa: F401 — board is a fixture
    _make_executor,
    _second_dashboard,
    board,
)

_TEMPLATE = "[[[ return hass.states['light.lamp'].state ]]]"


@pytest.mark.parametrize(
    "card",
    [
        {"type": "custom:button-card", "name": _TEMPLATE},
        {"type": "custom:button-card", "styles": {"card": [{"color": "[[[return 'red']]]"}]}},
        {"type": "custom:config-template-card", "entities": ["${LIGHT}"]},
        {"type": "custom:apexcharts-card", "series": [{"transform": "EVAL:(x) => x * 2"}]},
        {"type": "custom:streamline-card", "variables": {"name_javascript": "return 1"}},
        {"type": "button", "tap_action": {"action": "url", "url_path": "javascript:alert(1)"}},
        {"type": "iframe", "url": " JAVA\tSCRIPT:alert(1)"},
        {"type": "iframe", "url": "\x01vbscript:msgbox"},
    ],
)
def test_browser_code_is_detected(card: dict[str, Any]) -> None:
    assert script_strings(card)
    assert introduced_script_error(card, ()) is not None


@pytest.mark.parametrize(
    "card",
    [
        {"type": "markdown", "content": "{{ states('sensor.temp') }} °C"},
        {"type": "button", "tap_action": {"action": "url", "url_path": "https://example.com"}},
        {"type": "tile", "entity": "light.lamp", "name": "Javascript corner"},
    ],
)
def test_ordinary_config_passes(card: dict[str, Any]) -> None:
    assert introduced_script_error(card, ()) is None


def test_code_already_held_passes_and_changed_code_does_not() -> None:
    stored = {"views": [{"cards": [{"type": "custom:button-card", "name": _TEMPLATE}]}]}
    moved = {
        "views": [{"cards": []}, {"cards": [{"type": "custom:button-card", "name": _TEMPLATE}]}]
    }
    assert introduced_script_error(moved, (stored,)) is None

    edited = {"views": [{"cards": [{"type": "custom:button-card", "name": _TEMPLATE + " "}]}]}
    assert introduced_script_error(edited, (stored,)) is not None


async def _stored(hass: HomeAssistant) -> dict[str, Any]:
    return await hass.data[LOVELACE_DATA].dashboards[None].async_load(False)


async def test_insert_refuses_a_js_template(board: HomeAssistant) -> None:  # noqa: F811
    before = await _stored(board)
    result = await _make_executor(board).execute(
        "insert_dashboard_card", {"card": {"type": "custom:button-card", "name": _TEMPLATE}}
    )
    assert "runs in the browser" in result["error"]
    assert await _stored(board) == before


async def test_update_refuses_a_javascript_url(board: HomeAssistant) -> None:  # noqa: F811
    executor: ToolExecutor = _make_executor(board)
    read = await executor.execute("get_dashboard_card", {"view": "living", "card_index": 0})
    result = await executor.execute(
        "update_dashboard_card",
        {
            "view": "living",
            "card_index": 0,
            "card": {
                "type": "light",
                "entity": "light.lamp",
                "tap_action": {"action": "url", "url_path": "javascript:fetch('//x')"},
            },
            "expected_fingerprint": read["fingerprint"],
        },
    )
    assert "runs in the browser" in result["error"]
    after = await executor.execute("get_dashboard_card", {"view": "living", "card_index": 0})
    assert "tap_action" not in after["card"]


async def test_the_users_own_template_can_still_be_moved(board: HomeAssistant) -> None:  # noqa: F811
    config = board.data[LOVELACE_DATA].dashboards[None]
    document = await config.async_load(False)
    document["views"][0]["cards"].append({"type": "custom:button-card", "name": _TEMPLATE})
    await config.async_save(document)

    result = await _make_executor(board).execute(
        "move_dashboard_card", {"view": 0, "from_index": 2, "to_index": 0}
    )
    assert result["status"] == "moved"
    assert (await _stored(board))["views"][0]["cards"][0]["name"] == _TEMPLATE


async def test_the_users_own_template_can_move_to_another_dashboard(
    board: HomeAssistant,  # noqa: F811
) -> None:
    """The destination never held it, so its own snapshot alone would refuse it."""
    config = board.data[LOVELACE_DATA].dashboards[None]
    document = await config.async_load(False)
    document["views"][0]["cards"][0] = {"type": "custom:button-card", "name": _TEMPLATE}
    await config.async_save(document)
    other = await _second_dashboard(
        board, document={"views": [{"title": "Brushes", "path": "brushes", "cards": []}]}
    )

    result = await _make_executor(board).execute(
        "move_dashboard_card", {"view": "living", "from_index": 0, "to_dashboard": "oral-b"}
    )
    assert result["status"] == "moved", result
    assert (await other.async_load(False))["views"][0]["cards"][0]["name"] == _TEMPLATE
