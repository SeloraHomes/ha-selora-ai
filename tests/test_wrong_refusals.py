"""Inputs Home Assistant accepts that we refused.

Each was refused for a reason that did not apply: a length cap HA does not
have, a check that read a script's own inputs as targets, a denylist entry for
a harmless service, and log levels that could never match.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai.diagnostics_tools import get_logs
from custom_components.selora_ai.llm_client.command_policy import _BLOCKED_SERVICES
from custom_components.selora_ai.mcp_server.entities import _tool_eval_template
from custom_components.selora_ai.mcp_service_call import check_service_call
from custom_components.selora_ai.tool_registry import TOOL_MAP


async def test_a_template_longer_than_a_kilobyte_is_rendered(hass: HomeAssistant) -> None:
    template = "{% set total = 0 %}" + "{# padding #}" * 250 + "{{ 'ok' }}"
    assert len(template) > 1024

    result = await _tool_eval_template(hass, {"template": template})

    assert result == {"result": "ok"}


def _script(hass: HomeAssistant) -> None:
    async def _noop(_call: Any) -> None:
        return None

    for service in ("notify_me", "turn_on"):
        hass.services.async_register("script", service, _noop)


async def test_a_scripts_own_device_id_field_is_not_a_target(hass: HomeAssistant) -> None:
    """script.<name>'s data is its inputs — a field called device_id is one."""
    _script(hass)

    verdict = check_service_call(
        hass, "script.notify_me", None, {"device_id": "abc123", "message": "hi"}
    )

    assert not any("cannot go in data" in e for e in verdict.get("errors", [])), verdict


async def test_a_target_in_data_is_still_refused_where_it_targets(hass: HomeAssistant) -> None:
    """script.turn_on's data can carry a real target, so it keeps the check."""
    _script(hass)

    verdict = check_service_call(hass, "script.turn_on", None, {"device_id": "abc123"})

    assert any("cannot go in data" in e for e in verdict["errors"]), verdict


def test_harmless_services_are_not_on_the_denylist() -> None:
    """Refreshing a sensor and checking the configuration change nothing."""
    assert "homeassistant.update_entity" not in _BLOCKED_SERVICES
    assert "homeassistant.check_config" not in _BLOCKED_SERVICES
    # What the denylist is for is still on it.
    assert {"homeassistant.stop", "recorder.purge", "hassio.host_shutdown"} <= _BLOCKED_SERVICES


def test_get_logs_offers_only_levels_that_can_match(hass: HomeAssistant) -> None:
    """HA's system log keeps WARNING and above only."""
    level_param = next(p for p in TOOL_MAP["get_logs"].params if p.name == "level")
    assert level_param.enum == ("CRITICAL", "ERROR", "WARNING")

    result = get_logs(hass, level="DEBUG")

    assert "WARNING" in result["error"]


@pytest.mark.parametrize(
    "service", ["Recorder.Purge", "HOMEASSISTANT.restart", "Python_Script.Exec"]
)
async def test_a_capitalised_denylisted_service_is_still_refused(
    hass: HomeAssistant, service: str
) -> None:
    """Home Assistant lowercases a service name before running it."""
    from custom_components.selora_ai.llm_client.command_policy import _classify_call
    from custom_components.selora_ai.mcp_service_call import check_service_call

    verdict = check_service_call(hass, service, None, {}, confirmed=True)

    assert not verdict["valid"]
    assert "never run from Selora" in verdict["errors"][0]
    assert _classify_call(service, allowlist_enabled=False)[0] == "blocked"
