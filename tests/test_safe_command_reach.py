"""A SAFE command reaches only the entity_ids checked against the snapshot.

Dispatch merges ``data`` and ``target`` into the service call, so an
``area_id``, ``device_id``, ``floor_id`` or ``label_id`` next to an allowed
entity would reach devices nobody checked — a cover in the garage among
them, without the approval card a garage door gets.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.const import (
    CONF_COMMAND_ALLOWLIST_ENABLED,
    CONF_ENTRY_TYPE,
    CONF_LLM_PROVIDER,
    DOMAIN,
    ENTRY_TYPE_LLM,
)
from custom_components.selora_ai.llm_client.command_policy import (
    _validate_safe_call,
    apply_command_policy,
)

_ENTITIES = [{"entity_id": "light.kitchen", "state": "off", "attributes": {}}]
_EXTRA_TARGETS = ["area_id", "device_id", "floor_id", "label_id"]


def _llm_entry(hass: Any, *, allowlist: bool) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        entry_id="test_llm_entry",
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_LLM, CONF_LLM_PROVIDER: "selora_local"},
        options={CONF_COMMAND_ALLOWLIST_ENABLED: allowlist},
    ).add_to_hass(hass)


def _turn_on(target: dict[str, Any], data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "intent": "command",
        "response": "Turning on the kitchen light.",
        "calls": [{"service": "light.turn_on", "target": target, "data": data or {}}],
    }


@pytest.mark.parametrize("allowlist", [True, False], ids=["allowlist", "opt_out"])
@pytest.mark.parametrize("key", _EXTRA_TARGETS)
def test_extra_target_key_is_refused(hass: Any, allowlist: bool, key: str) -> None:
    _llm_entry(hass, allowlist=allowlist)
    result = apply_command_policy(
        _turn_on({"entity_id": ["light.kitchen"], key: "garage"}), _ENTITIES, hass=hass
    )
    assert result["intent"] == "answer"
    assert not result.get("calls")
    assert key in result["validation_error"]


@pytest.mark.parametrize("allowlist", [True, False], ids=["allowlist", "opt_out"])
@pytest.mark.parametrize("key", ["entity_id", *_EXTRA_TARGETS])
def test_target_key_in_data_is_refused(hass: Any, allowlist: bool, key: str) -> None:
    _llm_entry(hass, allowlist=allowlist)
    result = apply_command_policy(
        _turn_on({"entity_id": ["light.kitchen"]}, {key: "cover.garage_door"}),
        _ENTITIES,
        hass=hass,
    )
    assert result["intent"] == "answer"
    assert not result.get("calls")


def test_the_dispatched_target_holds_only_entity_ids(hass: Any) -> None:
    _llm_entry(hass, allowlist=True)
    result = apply_command_policy(
        _turn_on({"entity_id": "light.kitchen"}, {"brightness_pct": 40}), _ENTITIES, hass=hass
    )
    assert result["intent"] == "command"
    assert result["calls"] == [
        {
            "service": "light.turn_on",
            "target": {"entity_id": "light.kitchen"},
            "data": {"brightness_pct": 40},
        }
    ]


@pytest.mark.parametrize("key", _EXTRA_TARGETS)
def test_validate_safe_call_refuses_extra_targets(key: str) -> None:
    call = {"service": "light.turn_on", "target": {"entity_id": ["light.kitchen"], key: "x"}}
    validated, err = _validate_safe_call(call, {"light.kitchen"})
    assert validated is None
    assert err is not None
    assert key in err
