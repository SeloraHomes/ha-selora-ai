"""Comprehensive unit tests for automation_utils module."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from custom_components.selora_ai.automation_utils import (
    _parse_automation_yaml,
    _quote_yaml_booleans,
    _read_automations_yaml,
    _write_automations_yaml,
    assess_automation_risk,
    async_create_automation,
    async_delete_automation,
    async_toggle_automation,
    async_update_automation,
    count_selora_automations,
    find_stale_automations,
    get_selora_automation_cap,
    validate_action_services,
    validate_automation_payload,
)
from custom_components.selora_ai.const import (
    AUTOMATION_CAP_CEILING,
    AUTOMATION_CAP_FLOOR,
    AUTOMATION_ID_PREFIX,
    AUTOMATIONS_PER_DEVICE,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_automation_store() -> MagicMock:
    """Return a mock AutomationStore with the methods used by CRUD functions."""
    store = MagicMock()
    store.add_version = AsyncMock()
    store.purge_record = AsyncMock()
    return store


# ===================================================================
# validate_automation_payload
# ===================================================================


class TestValidateAutomationPayload:
    """Tests for validate_automation_payload."""

    # -- rejection cases --------------------------------------------------

    @pytest.mark.parametrize(
        "payload",
        [None, "string", 42, [], True],
        ids=["none", "string", "int", "list", "bool"],
    )
    def test_rejects_non_dict(self, payload: Any) -> None:
        ok, msg, result = validate_automation_payload(payload)
        assert ok is False
        assert msg == "automation payload must be an object"
        assert result is None

    @pytest.mark.parametrize(
        "alias_value",
        [None, "", "   "],
        ids=["missing", "empty", "whitespace"],
    )
    def test_rejects_missing_or_empty_alias(self, alias_value: str | None) -> None:
        payload: dict[str, Any] = {"trigger": [{"platform": "time"}], "action": [{"action": "x"}]}
        if alias_value is not None:
            payload["alias"] = alias_value
        ok, msg, _ = validate_automation_payload(payload)
        assert ok is False
        assert msg == "automation alias is required"

    def test_rejects_no_triggers(self) -> None:
        ok, msg, _ = validate_automation_payload({"alias": "Test", "action": [{"action": "x"}]})
        assert ok is False
        assert msg == "automation must include at least one trigger"

    def test_rejects_no_actions(self) -> None:
        ok, msg, _ = validate_automation_payload(
            {"alias": "Test", "trigger": [{"platform": "time"}]}
        )
        assert ok is False
        assert msg == "automation must include at least one action"

    def test_rejects_non_dict_trigger(self) -> None:
        ok, msg, _ = validate_automation_payload(
            {"alias": "Test", "trigger": ["bad"], "action": [{"action": "x"}]}
        )
        assert ok is False
        assert msg == "all triggers must be objects"

    def test_rejects_non_dict_action(self) -> None:
        ok, msg, _ = validate_automation_payload(
            {"alias": "Test", "trigger": [{"platform": "time"}], "action": ["bad"]}
        )
        assert ok is False
        assert msg == "all actions must be objects"

    def test_rejects_non_dict_condition(self) -> None:
        ok, msg, _ = validate_automation_payload(
            {
                "alias": "Test",
                "trigger": [{"platform": "time"}],
                "action": [{"action": "x"}],
                "condition": ["bad"],
            }
        )
        assert ok is False
        assert msg == "all conditions must be objects"

    def test_rejects_trigger_without_platform(self) -> None:
        ok, msg, _ = validate_automation_payload(
            {"alias": "Test", "trigger": [{"event": "sunset"}], "action": [{"action": "x"}]}
        )
        assert ok is False
        assert msg == "each trigger must include a platform"

    # -- success / normalization ------------------------------------------

    def test_valid_automation_returns_normalized(self, sample_automation: dict) -> None:
        ok, msg, result = validate_automation_payload(sample_automation)
        assert ok is True
        assert msg == ""
        assert result is not None
        assert result["alias"] == "Test automation"
        assert result["mode"] == "single"
        assert isinstance(result["triggers"], list)
        assert isinstance(result["actions"], list)
        assert isinstance(result["conditions"], list)

    def test_uses_triggers_key(self) -> None:
        payload = {
            "alias": "Alt",
            "triggers": [{"platform": "sun", "event": "sunset"}],
            "actions": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert len(result["triggers"]) == 1

    def test_wraps_single_trigger_in_list(self) -> None:
        payload = {
            "alias": "Single",
            "trigger": {"platform": "time", "at": "08:00"},
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert isinstance(result["triggers"], list)
        assert len(result["triggers"]) == 1

    def test_wraps_single_action_in_list(self) -> None:
        payload = {
            "alias": "Single",
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": {"action": "light.turn_on"},
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert isinstance(result["actions"], list)

    def test_wraps_single_condition_in_list(self) -> None:
        payload = {
            "alias": "Cond",
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [{"action": "light.turn_on"}],
            "condition": {"condition": "state", "entity_id": "light.x", "state": "on"},
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert isinstance(result["conditions"], list)
        assert len(result["conditions"]) == 1

    def test_renames_trigger_key_to_platform(self) -> None:
        payload = {
            "alias": "Rename",
            "trigger": [{"trigger": "state", "entity_id": "light.x"}],
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["triggers"][0]["platform"] == "state"
        assert "trigger" not in result["triggers"][0]

    # -- boolean / None / numeric coercion --------------------------------

    def test_coerces_boolean_true_to_on(self) -> None:
        payload = {
            "alias": "Bool",
            "trigger": [{"platform": "state", "entity_id": "light.x", "to": True}],
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["triggers"][0]["to"] == "on"

    def test_coerces_boolean_false_to_off(self) -> None:
        payload = {
            "alias": "Bool",
            "trigger": [{"platform": "state", "entity_id": "light.x", "from": False}],
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["triggers"][0]["from"] == "off"

    def test_removes_none_to_from(self) -> None:
        payload = {
            "alias": "NoneVal",
            "trigger": [{"platform": "state", "entity_id": "light.x", "to": None, "from": None}],
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert "to" not in result["triggers"][0]
        assert "from" not in result["triggers"][0]

    def test_stringifies_numeric_to_from(self) -> None:
        payload = {
            "alias": "Numeric",
            "trigger": [{"platform": "state", "entity_id": "sensor.x", "to": 42, "from": 3.14}],
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["triggers"][0]["to"] == "42"
        assert result["triggers"][0]["from"] == "3.14"

    def test_default_mode_is_single(self) -> None:
        payload = {
            "alias": "NoMode",
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["mode"] == "single"

    def test_preserves_explicit_mode(self) -> None:
        payload = {
            "alias": "Queued",
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [{"action": "light.turn_on"}],
            "mode": "queued",
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["mode"] == "queued"

    def test_invalid_mode_falls_back_to_single(self) -> None:
        payload = {
            "alias": "BadMode",
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [{"action": "light.turn_on"}],
            "mode": "bogus_mode",
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["mode"] == "single"

    def test_preserves_initial_state_true(self) -> None:
        payload = {
            "alias": "Enabled",
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [{"action": "light.turn_on"}],
            "initial_state": True,
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["initial_state"] is True

    def test_preserves_initial_state_false(self) -> None:
        payload = {
            "alias": "Disabled",
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [{"action": "light.turn_on"}],
            "initial_state": False,
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["initial_state"] is False

    def test_omits_initial_state_when_not_provided(self) -> None:
        payload = {
            "alias": "NoState",
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert "initial_state" not in result

    def test_omits_initial_state_when_non_bool(self) -> None:
        payload = {
            "alias": "StringState",
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [{"action": "light.turn_on"}],
            "initial_state": "yes",
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert "initial_state" not in result

    # -- condition state coercion -----------------------------------------

    def test_coerces_condition_state_true_to_on(self) -> None:
        payload = {
            "alias": "CondBool",
            "trigger": [{"platform": "state", "entity_id": "binary_sensor.x"}],
            "action": [{"action": "light.turn_on"}],
            "condition": [
                {"condition": "state", "entity_id": "binary_sensor.motion", "state": True}
            ],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["conditions"][0]["state"] == "on"

    def test_coerces_condition_state_false_to_off(self) -> None:
        payload = {
            "alias": "CondBool",
            "trigger": [{"platform": "state", "entity_id": "binary_sensor.x"}],
            "action": [{"action": "light.turn_on"}],
            "condition": [
                {"condition": "state", "entity_id": "binary_sensor.motion", "state": False}
            ],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["conditions"][0]["state"] == "off"

    def test_leaves_condition_state_string_unchanged(self) -> None:
        payload = {
            "alias": "CondStr",
            "trigger": [{"platform": "state", "entity_id": "binary_sensor.x"}],
            "action": [{"action": "light.turn_on"}],
            "condition": [{"condition": "state", "entity_id": "sensor.temp", "state": "25.5"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["conditions"][0]["state"] == "25.5"

    # -- time condition coercion ------------------------------------------

    def test_coerces_time_condition_integer_after_before(self) -> None:
        payload = {
            "alias": "TimeInt",
            "trigger": [{"platform": "state", "entity_id": "binary_sensor.x"}],
            "action": [{"action": "light.turn_on"}],
            "condition": [{"condition": "time", "after": 75600, "before": 79200}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["conditions"][0]["after"] == "21:00:00"
        assert result["conditions"][0]["before"] == "22:00:00"

    def test_leaves_time_condition_string_unchanged(self) -> None:
        payload = {
            "alias": "TimeStr",
            "trigger": [{"platform": "state", "entity_id": "binary_sensor.x"}],
            "action": [{"action": "light.turn_on"}],
            "condition": [{"condition": "time", "after": "21:00:00", "before": "22:00:00"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["conditions"][0]["after"] == "21:00:00"
        assert result["conditions"][0]["before"] == "22:00:00"

    # -- time trigger `at` coercion ---------------------------------------

    def test_coerces_time_trigger_integer_at(self) -> None:
        payload = {
            "alias": "TrigTimeInt",
            "trigger": [{"platform": "time", "at": 81000}],
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["triggers"][0]["at"] == "22:30:00"

    def test_coerces_time_trigger_at_midnight(self) -> None:
        payload = {
            "alias": "Midnight",
            "trigger": [{"platform": "time", "at": 0}],
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["triggers"][0]["at"] == "00:00:00"

    def test_leaves_time_trigger_string_at_unchanged(self) -> None:
        payload = {
            "alias": "TrigTimeStr",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["triggers"][0]["at"] == "07:00:00"

    # -- duration `for` coercion ------------------------------------------

    def test_coerces_duration_for_integer_to_dict_in_trigger(self) -> None:
        payload = {
            "alias": "ForInt",
            "trigger": [{"platform": "state", "entity_id": "light.x", "to": "on", "for": 300}],
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["triggers"][0]["for"] == {"seconds": 300}

    def test_coerces_duration_for_integer_to_dict_in_condition(self) -> None:
        payload = {
            "alias": "ForCond",
            "trigger": [{"platform": "state", "entity_id": "light.x"}],
            "action": [{"action": "light.turn_on"}],
            "condition": [
                {"condition": "state", "entity_id": "light.x", "state": "on", "for": 600}
            ],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["conditions"][0]["for"] == {"seconds": 600}


# ===================================================================
# validate_action_services
# ===================================================================


class TestValidateActionDomains:
    """Tests for non-actionable domain rejection in validate_automation_payload (#91).

    When ``hass`` is passed, the validator rejects actions that target domains
    with no services registered in HA's service registry (sensor, binary_sensor,
    device_tracker, person, etc.).  When ``hass`` is ``None`` the check is
    skipped — callers must either pass hass or call ``validate_action_services``.
    """

    @staticmethod
    def _hass_with_services() -> MagicMock:
        registry: dict[str, set[str]] = {
            "light": {"turn_on", "turn_off"},
            "switch": {"turn_on", "turn_off"},
            "notify": {"persistent_notification", "notify"},
            "homeassistant": {"turn_on", "turn_off", "toggle"},
        }
        mock_hass = MagicMock()
        mock_hass.services.has_service.side_effect = (
            lambda domain, service: service in registry.get(domain, set())
        )
        mock_hass.services.async_services_for_domain.side_effect = (
            lambda domain: {svc: {} for svc in registry[domain]} if domain in registry else {}
        )
        return mock_hass

    def test_binary_sensor_action_target_rejected(self) -> None:
        """Action targeting binary_sensor entity should be rejected."""
        payload = {
            "alias": "Bad",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "action": [
                {
                    "action": "binary_sensor.turn_on",
                    "target": {"entity_id": "binary_sensor.motion"},
                }
            ],
        }
        valid, reason, _ = validate_automation_payload(payload, self._hass_with_services())
        assert not valid
        assert "non-existent service" in reason.lower()

    def test_sensor_action_target_rejected(self) -> None:
        """Action targeting sensor entity should be rejected."""
        payload = {
            "alias": "Bad",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "action": [
                {
                    "action": "sensor.turn_on",
                    "target": {"entity_id": "sensor.temperature"},
                }
            ],
        }
        valid, _, _ = validate_automation_payload(payload, self._hass_with_services())
        assert not valid

    def test_notify_action_allowed(self) -> None:
        """notify.* services should be allowed (always available in HA)."""
        payload = {
            "alias": "Good",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "action": [
                {
                    "action": "notify.persistent_notification",
                    "data": {"message": "hello"},
                }
            ],
        }
        valid, _, _ = validate_automation_payload(payload, self._hass_with_services())
        assert valid

    def test_light_action_allowed(self) -> None:
        """light.* actions with light entity targets should be accepted."""
        payload = {
            "alias": "Good",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "action": [
                {
                    "action": "light.turn_on",
                    "target": {"entity_id": "light.kitchen"},
                }
            ],
        }
        valid, _, _ = validate_automation_payload(payload, self._hass_with_services())
        assert valid

    def test_no_hass_skips_domain_check(self) -> None:
        """Without hass, the domain check is skipped (structural validation only)."""
        payload = {
            "alias": "Skips domain check",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "action": [
                {
                    "action": "sensor.turn_on",
                    "target": {"entity_id": "sensor.temperature"},
                }
            ],
        }
        valid, _, _ = validate_automation_payload(payload)
        assert valid

    def test_cross_domain_action_targeting_read_only_entity_rejected(self) -> None:
        """homeassistant.turn_on targeting binary_sensor.motion should be rejected."""
        payload = {
            "alias": "Cross-domain bad",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "action": [
                {
                    "action": "homeassistant.turn_on",
                    "target": {"entity_id": "binary_sensor.motion"},
                }
            ],
        }
        valid, reason, _ = validate_automation_payload(payload, self._hass_with_services())
        assert not valid
        assert "read-only domain" in reason.lower()

    def test_cross_domain_action_targeting_actionable_entity_allowed(self) -> None:
        """homeassistant.turn_on targeting light.kitchen should be accepted."""
        payload = {
            "alias": "Cross-domain good",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "action": [
                {
                    "action": "homeassistant.turn_on",
                    "target": {"entity_id": "light.kitchen"},
                }
            ],
        }
        valid, _, _ = validate_automation_payload(payload, self._hass_with_services())
        assert valid


class TestValidateEntityReferences:
    """Tests for entity_id existence validation in validate_automation_payload.

    Catches LLM hallucinations like ``script.expose_to_siri`` (no such script)
    or stale ``automation.*`` references before the broken automation is
    written to ``automations.yaml`` and surfaces as "unavailable".
    """

    @staticmethod
    def _hass(known_entity_ids: set[str], registry_entity_ids: set[str] | None = None) -> MagicMock:
        """Build a mock hass whose state machine and entity registry know the given IDs.

        ``known_entity_ids`` populates :attr:`hass.states`. ``registry_entity_ids``
        (defaults to the same set) populates the entity registry — entities that
        exist in the registry but currently have no state still count as known.
        """
        if registry_entity_ids is None:
            registry_entity_ids = known_entity_ids
        service_registry: dict[str, set[str]] = {
            "light": {"turn_on", "turn_off"},
            "switch": {"turn_on", "turn_off"},
            "script": {"turn_on", "turn_off", "expose_to_siri"},
            "automation": {"turn_on", "turn_off", "trigger"},
            "homeassistant": {"turn_on", "turn_off", "toggle"},
        }
        hass = MagicMock()
        hass.services.has_service.side_effect = (
            lambda domain, service: service in service_registry.get(domain, set())
        )
        hass.services.async_services_for_domain.side_effect = (
            lambda domain: {svc: {} for svc in service_registry[domain]}
            if domain in service_registry
            else {}
        )
        hass.states.get.side_effect = (
            lambda eid: MagicMock() if eid in known_entity_ids else None
        )
        return hass

    @pytest.fixture(autouse=True)
    def _patch_entity_registry(self, monkeypatch: pytest.MonkeyPatch) -> list[set[str]]:
        """Patch er.async_get so we can control the registry per test."""
        registry_ids: set[str] = set()

        def _fake_async_get(_hass: Any) -> Any:
            reg = MagicMock()
            reg.async_get.side_effect = (
                lambda eid: MagicMock() if eid in registry_ids else None
            )
            return reg

        monkeypatch.setattr(
            "custom_components.selora_ai.automation_utils.er.async_get",
            _fake_async_get,
        )
        self._registry_ids = registry_ids
        return [registry_ids]

    def _set_registry(self, ids: set[str]) -> None:
        self._registry_ids.clear()
        self._registry_ids.update(ids)

    def test_rejects_unknown_action_target_entity(self) -> None:
        """Action targeting an entity that does not exist is rejected."""
        self._set_registry({"light.kitchen"})
        payload = {
            "alias": "Hallucinated entity",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "action": [
                {
                    "action": "script.turn_on",
                    "target": {"entity_id": "script.expose_to_siri"},
                }
            ],
        }
        valid, reason, _ = validate_automation_payload(payload, self._hass(set()))
        assert not valid
        assert "unknown entity_id" in reason
        assert "script.expose_to_siri" in reason

    def test_rejects_unknown_trigger_entity(self) -> None:
        """Trigger referencing a non-existent entity is rejected."""
        self._set_registry({"light.kitchen"})
        payload = {
            "alias": "Bad trigger",
            "trigger": [
                {"platform": "state", "entity_id": "automation.ghost", "to": "on"}
            ],
            "action": [
                {"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}
            ],
        }
        valid, reason, _ = validate_automation_payload(
            payload, self._hass({"light.kitchen"})
        )
        assert not valid
        assert "automation.ghost" in reason

    def test_rejects_unknown_condition_entity(self) -> None:
        """Condition referencing a non-existent entity is rejected."""
        self._set_registry({"light.kitchen"})
        payload = {
            "alias": "Bad condition",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "condition": [
                {"condition": "state", "entity_id": "switch.phantom", "state": "on"}
            ],
            "action": [
                {"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}
            ],
        }
        valid, reason, _ = validate_automation_payload(
            payload, self._hass({"light.kitchen"})
        )
        assert not valid
        assert "switch.phantom" in reason

    def test_accepts_when_all_entities_known(self) -> None:
        """When every referenced entity exists, validation passes."""
        self._set_registry({"light.kitchen", "switch.foyer"})
        payload = {
            "alias": "All known",
            "trigger": [
                {"platform": "state", "entity_id": "switch.foyer", "to": "on"}
            ],
            "action": [
                {"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}
            ],
        }
        valid, reason, _ = validate_automation_payload(
            payload, self._hass({"light.kitchen", "switch.foyer"})
        )
        assert valid, reason

    def test_registry_only_entity_accepted(self) -> None:
        """Entity in registry but without current state is treated as known."""
        # state machine has nothing; registry has the entity (disabled / no
        # state yet). Should still pass — the entity is real.
        self._set_registry({"switch.foyer", "light.kitchen"})
        payload = {
            "alias": "Disabled entity",
            "trigger": [
                {"platform": "state", "entity_id": "switch.foyer", "to": "on"}
            ],
            "action": [
                {"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}
            ],
        }
        valid, reason, _ = validate_automation_payload(payload, self._hass(set()))
        assert valid, reason

    def test_template_entity_id_skipped(self) -> None:
        """Templated entity_id values are not checked against the registry."""
        self._set_registry(set())
        payload = {
            "alias": "Templated",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "action": [
                {
                    "action": "light.turn_on",
                    "target": {"entity_id": "{{ states('input_text.target') }}"},
                }
            ],
        }
        valid, _, _ = validate_automation_payload(payload, self._hass(set()))
        assert valid

    def test_no_hass_skips_entity_check(self) -> None:
        """Without hass, entity existence is not validated."""
        payload = {
            "alias": "No hass",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "action": [
                {"action": "light.turn_on", "target": {"entity_id": "light.phantom"}}
            ],
        }
        valid, _, _ = validate_automation_payload(payload)
        assert valid

    def test_comma_separated_entity_id_split_before_lookup(self) -> None:
        """HA's legacy ``entity_id: "a, b"`` shorthand must be split, not looked
        up as a single combined string. Each ID is checked individually."""
        self._set_registry({"light.kitchen", "light.dining"})
        payload = {
            "alias": "Comma form",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "action": [
                {
                    "action": "light.turn_on",
                    "target": {"entity_id": "light.kitchen, light.dining"},
                }
            ],
        }
        valid, reason, _ = validate_automation_payload(
            payload, self._hass({"light.kitchen", "light.dining"})
        )
        assert valid, reason

    def test_comma_separated_entity_id_partial_unknown_rejected(self) -> None:
        """If one half of a comma-separated entity_id is missing, only that
        half should be reported — proving the split happened."""
        self._set_registry({"light.kitchen"})
        payload = {
            "alias": "Comma form partial",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "action": [
                {
                    "action": "light.turn_on",
                    "target": {"entity_id": "light.kitchen, light.phantom"},
                }
            ],
        }
        valid, reason, _ = validate_automation_payload(
            payload, self._hass({"light.kitchen"})
        )
        assert not valid
        assert "light.phantom" in reason
        assert "light.kitchen" not in reason

    def test_event_data_entity_id_field_not_validated(self) -> None:
        """event_data is integration-defined match data, not an HA entity ref.

        A ``platform: event`` trigger with ``event_data: {entity_id: "..."}``
        means "fire only when the event payload's entity_id field equals this
        value" — the value is opaque to HA's state machine and may be an
        integration identifier (e.g. ``my_integration.some_id``) that is not
        a registered entity. Validating it would falsely reject the payload.
        """
        self._set_registry({"light.kitchen"})
        payload = {
            "alias": "Event with entity_id payload",
            "trigger": [
                {
                    "platform": "event",
                    "event_type": "my_integration_event",
                    "event_data": {"entity_id": "my_integration.not_a_state_entity"},
                }
            ],
            "action": [
                {"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}
            ],
        }
        valid, reason, _ = validate_automation_payload(
            payload, self._hass({"light.kitchen"})
        )
        assert valid, reason

    def test_choose_branch_entity_validated(self) -> None:
        """``choose`` branches are real control-flow — entities inside their
        ``sequence`` are still validated."""
        self._set_registry({"light.kitchen"})
        payload = {
            "alias": "Choose phantom",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "action": [
                {
                    "choose": [
                        {
                            "conditions": [
                                {
                                    "condition": "state",
                                    "entity_id": "light.kitchen",
                                    "state": "on",
                                }
                            ],
                            "sequence": [
                                {
                                    "action": "light.turn_off",
                                    "target": {"entity_id": "light.phantom"},
                                }
                            ],
                        }
                    ]
                }
            ],
        }
        valid, reason, _ = validate_automation_payload(
            payload, self._hass({"light.kitchen"})
        )
        assert not valid
        assert "light.phantom" in reason

    def test_nested_condition_group_singular_dict_validated(self) -> None:
        """HA accepts ``conditions: {condition: state, ...}`` (singular dict)
        inside an and/or/not group. The walker must coerce to a list so the
        nested condition's entity_id is still checked — otherwise a phantom
        entity inside an ``and`` block slips past validation."""
        self._set_registry({"light.kitchen"})
        payload = {
            "alias": "Nested singular condition",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "condition": [
                {
                    "condition": "and",
                    "conditions": {
                        "condition": "state",
                        "entity_id": "switch.phantom",
                        "state": "on",
                    },
                }
            ],
            "action": [
                {"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}
            ],
        }
        valid, reason, _ = validate_automation_payload(
            payload, self._hass({"light.kitchen"})
        )
        assert not valid
        assert "switch.phantom" in reason

    def test_condition_as_action_step_validated(self) -> None:
        """HA allows a condition block to appear as an action step (inline
        guard). Entity references inside that step must still be validated
        — otherwise a hallucinated entity hidden in an action-step
        condition passes the unknown-entity check."""
        self._set_registry({"light.kitchen"})
        payload = {
            "alias": "Inline condition action",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "action": [
                {
                    "condition": "or",
                    "conditions": [
                        {
                            "condition": "state",
                            "entity_id": "lock.phantom",
                            "state": "unlocked",
                        }
                    ],
                },
                {"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}},
            ],
        }
        valid, reason, _ = validate_automation_payload(
            payload, self._hass({"light.kitchen"})
        )
        assert not valid
        assert "lock.phantom" in reason

    def test_shorthand_logical_condition_validated(self) -> None:
        """HA's shorthand logical syntax (``{or: [...]}``, ``{and: [...]}``,
        ``{not: [...]}``) is valid and references real entities. The walker
        must descend into those operator keys; otherwise a hallucinated
        entity nested inside ``or:`` slips past validation."""
        self._set_registry({"light.kitchen"})
        payload = {
            "alias": "Shorthand or",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "condition": [
                {
                    "or": [
                        {
                            "condition": "state",
                            "entity_id": "lock.phantom",
                            "state": "unlocked",
                        }
                    ]
                }
            ],
            "action": [
                {"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}
            ],
        }
        valid, reason, _ = validate_automation_payload(
            payload, self._hass({"light.kitchen"})
        )
        assert not valid
        assert "lock.phantom" in reason

    def test_wait_for_trigger_entity_validated(self) -> None:
        """wait_for_trigger embeds trigger dicts whose entity_id is a real
        state reference — phantom entities inside it must still be caught."""
        self._set_registry({"light.kitchen"})
        payload = {
            "alias": "Wait for phantom motion",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "action": [
                {
                    "wait_for_trigger": [
                        {
                            "platform": "state",
                            "entity_id": "binary_sensor.phantom_motion",
                            "to": "on",
                        }
                    ]
                },
                {"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}},
            ],
        }
        valid, reason, _ = validate_automation_payload(
            payload, self._hass({"light.kitchen"})
        )
        assert not valid
        assert "binary_sensor.phantom_motion" in reason

    def test_deprecated_bare_entity_id_action_form_validated(self) -> None:
        """Legacy ``service: light.turn_on, entity_id: light.foo`` is a real
        entity reference — still rejected if the entity is missing."""
        self._set_registry(set())
        payload = {
            "alias": "Deprecated form",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "action": [
                {"action": "light.turn_on", "entity_id": "light.phantom"}
            ],
        }
        valid, reason, _ = validate_automation_payload(payload, self._hass(set()))
        assert not valid
        assert "light.phantom" in reason

    def test_multiple_unknown_entities_listed(self) -> None:
        """Error message lists multiple missing entities (truncates after 3)."""
        self._set_registry(set())
        payload = {
            "alias": "Many phantoms",
            "trigger": [
                {"platform": "state", "entity_id": "switch.a", "to": "on"}
            ],
            "action": [
                {
                    "action": "light.turn_on",
                    "target": {
                        "entity_id": ["light.b", "light.c", "light.d", "light.e"]
                    },
                }
            ],
        }
        valid, reason, _ = validate_automation_payload(payload, self._hass(set()))
        assert not valid
        # Three names appear in the preview, the 4th is folded into "+N more".
        assert reason.count(".") >= 3
        assert "+" in reason and "more" in reason


class TestValidateActionServices:
    """Tests for validate_action_services."""

    @staticmethod
    def _mock_hass(services: dict[str, list[str]]) -> MagicMock:
        registry = {
            domain: set(svcs) for domain, svcs in services.items()
        }
        hass = MagicMock()
        hass.services.has_service.side_effect = (
            lambda domain, service: service in registry.get(domain, set())
        )
        return hass

    def test_valid_service_passes(self) -> None:
        hass = self._mock_hass({"light": ["turn_on", "turn_off"]})
        auto = {"alias": "Test", "action": [{"action": "light.turn_on"}]}
        assert validate_action_services(hass, auto) is True

    def test_nonexistent_service_rejected(self) -> None:
        hass = self._mock_hass({"light": ["turn_on"]})
        auto = {"alias": "Bad", "action": [{"action": "tts.google_translate_en_com"}]}
        assert validate_action_services(hass, auto) is False

    def test_nonexistent_domain_rejected(self) -> None:
        hass = self._mock_hass({"light": ["turn_on"]})
        auto = {"alias": "Bad", "action": [{"action": "tts.speak"}]}
        assert validate_action_services(hass, auto) is False

    def test_malformed_service_rejected(self) -> None:
        hass = self._mock_hass({"light": ["turn_on"]})
        auto = {"alias": "Bad", "action": [{"action": "no_dot_here"}]}
        assert validate_action_services(hass, auto) is False

    def test_no_actions_passes(self) -> None:
        hass = self._mock_hass({"light": ["turn_on"]})
        auto = {"alias": "Empty", "action": []}
        assert validate_action_services(hass, auto) is True

    def test_multiple_actions_all_valid(self) -> None:
        hass = self._mock_hass({"light": ["turn_on"], "notify": ["persistent_notification"]})
        auto = {
            "alias": "Multi",
            "action": [
                {"action": "light.turn_on"},
                {"action": "notify.persistent_notification"},
            ],
        }
        assert validate_action_services(hass, auto) is True

    def test_multiple_actions_one_invalid(self) -> None:
        hass = self._mock_hass({"light": ["turn_on"]})
        auto = {
            "alias": "Mixed",
            "action": [
                {"action": "light.turn_on"},
                {"action": "tts.google_translate_en_com"},
            ],
        }
        assert validate_action_services(hass, auto) is False

    def test_uses_service_key_fallback(self) -> None:
        hass = self._mock_hass({"light": ["turn_on"]})
        auto = {"alias": "Old", "action": [{"service": "light.turn_on"}]}
        assert validate_action_services(hass, auto) is True

    def test_non_string_service_skipped(self) -> None:
        hass = self._mock_hass({"light": ["turn_on"]})
        auto = {"alias": "Weird", "action": [{"action": 123}]}
        assert validate_action_services(hass, auto) is True


# ===================================================================
# assess_automation_risk
# ===================================================================


class TestAssessAutomationRisk:
    """Tests for assess_automation_risk."""

    def test_normal_automation(self, sample_automation: dict) -> None:
        result = assess_automation_risk(sample_automation)
        assert result["level"] == "normal"
        assert result["flags"] == []

    @pytest.mark.parametrize(
        "service_domain",
        ["shell_command", "python_script", "pyscript", "rest_command", "hassio"],
    )
    def test_elevated_compute_capability(self, service_domain: str) -> None:
        auto = {
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [{"service": f"{service_domain}.run"}],
        }
        result = assess_automation_risk(auto)
        assert result["level"] == "elevated"
        assert "compute_capability" in result["flags"]

    @pytest.mark.parametrize("service", ["script.turn_on", "script.toggle"])
    def test_elevated_indirect_execution(self, service: str) -> None:
        auto = {
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [{"service": service}],
        }
        result = assess_automation_risk(auto)
        assert result["level"] == "elevated"
        assert "indirect_execution" in result["flags"]

    def test_webhook_trigger_remote_ingress(self) -> None:
        auto = {
            "trigger": [{"platform": "webhook", "webhook_id": "abc"}],
            "action": [{"action": "light.turn_on"}],
        }
        result = assess_automation_risk(auto)
        assert result["level"] == "elevated"
        assert "remote_ingress_trigger" in result["flags"]

    @pytest.mark.parametrize(
        ("entity_domain", "expected_tag"),
        [
            ("lock", "Access control"),
            ("cover", "Entry point"),
            ("camera", "Camera"),
            ("alarm_control_panel", "Security system"),
            ("person", "Presence"),
            ("device_tracker", "Presence"),
            ("vacuum", "Appliance"),
            ("media_player", "Media device"),
        ],
    )
    def test_scrutiny_entity_domains(self, entity_domain: str, expected_tag: str) -> None:
        auto = {
            "trigger": [{"platform": "state", "entity_id": f"{entity_domain}.test"}],
            "action": [{"action": "light.turn_on"}],
        }
        result = assess_automation_risk(auto)
        assert expected_tag in result["scrutiny_tags"]

    @pytest.mark.parametrize(
        ("service_domain", "expected_tag"),
        [("notify", "Notification"), ("tts", "Notification")],
    )
    def test_scrutiny_service_domains(self, service_domain: str, expected_tag: str) -> None:
        auto = {
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [{"service": f"{service_domain}.speak"}],
        }
        result = assess_automation_risk(auto)
        assert expected_tag in result["scrutiny_tags"]

    def test_entity_ids_collected_from_lists(self) -> None:
        auto = {
            "trigger": [{"platform": "state", "entity_id": ["lock.front", "lock.back"]}],
            "action": [{"action": "light.turn_on"}],
        }
        result = assess_automation_risk(auto)
        assert "Access control" in result["scrutiny_tags"]

    def test_singular_dict_action_section_collected(self) -> None:
        """HA accepts singular dict sections (``action: {...}``) as well as
        lists. Raw callers like ``tool_executor`` pass payloads in that
        shape, so the entity walker must coerce them — otherwise lock/camera
        scrutiny tags would silently disappear for these automations.
        """
        auto = {
            "trigger": {"platform": "time", "at": "08:00"},
            "action": {
                "action": "lock.lock",
                "target": {"entity_id": "lock.front_door"},
            },
        }
        result = assess_automation_risk(auto)
        assert "Access control" in result["scrutiny_tags"]

    def test_nested_condition_group_singular_dict_scrutiny_tagged(self) -> None:
        """An ``and`` group whose ``conditions`` field is a singular dict must
        still surface scrutiny tags for sensitive entity domains inside it."""
        auto = {
            "trigger": [{"platform": "time", "at": "08:00"}],
            "condition": [
                {
                    "condition": "and",
                    "conditions": {
                        "condition": "state",
                        "entity_id": "lock.front_door",
                        "state": "locked",
                    },
                }
            ],
            "action": [{"action": "light.turn_on"}],
        }
        result = assess_automation_risk(auto)
        assert "Access control" in result["scrutiny_tags"]

    def test_condition_as_action_step_scrutiny_tagged(self) -> None:
        """A condition block used as an action step must still surface
        scrutiny tags for sensitive entity domains nested inside it."""
        auto = {
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [
                {
                    "condition": "and",
                    "conditions": [
                        {
                            "condition": "state",
                            "entity_id": "lock.front_door",
                            "state": "locked",
                        }
                    ],
                },
                {"action": "light.turn_on"},
            ],
        }
        result = assess_automation_risk(auto)
        assert "Access control" in result["scrutiny_tags"]

    def test_shorthand_logical_condition_scrutiny_tagged(self) -> None:
        """Shorthand ``{or: [...]}`` / ``{and: [...]}`` / ``{not: [...]}``
        condition syntax must still surface scrutiny tags for sensitive
        entity domains nested inside it."""
        auto = {
            "trigger": [{"platform": "time", "at": "08:00"}],
            "condition": [
                {
                    "and": [
                        {
                            "condition": "state",
                            "entity_id": "lock.front_door",
                            "state": "locked",
                        }
                    ]
                }
            ],
            "action": [{"action": "light.turn_on"}],
        }
        result = assess_automation_risk(auto)
        assert "Access control" in result["scrutiny_tags"]

    def test_wait_for_trigger_entity_scrutiny_tagged(self) -> None:
        """wait_for_trigger blocks reference real state entities — sensitive
        domains inside them (camera, lock, etc.) must still surface a
        scrutiny tag in the risk assessment."""
        auto = {
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [
                {
                    "wait_for_trigger": [
                        {
                            "platform": "state",
                            "entity_id": "camera.front",
                            "to": "recording",
                        }
                    ]
                },
                {"action": "light.turn_on"},
            ],
        }
        result = assess_automation_risk(auto)
        assert "Camera" in result["scrutiny_tags"]

    def test_singular_dict_condition_section_collected(self) -> None:
        """Singular dict ``condition: {...}`` must also be walked."""
        auto = {
            "trigger": [{"platform": "time", "at": "08:00"}],
            "condition": {"condition": "state", "entity_id": "camera.porch", "state": "idle"},
            "action": [{"action": "light.turn_on"}],
        }
        result = assess_automation_risk(auto)
        assert "Camera" in result["scrutiny_tags"]

    def test_flags_are_deduplicated_and_sorted(self) -> None:
        auto = {
            "trigger": [
                {"platform": "webhook", "webhook_id": "a"},
                {"platform": "webhook", "webhook_id": "b"},
            ],
            "action": [
                {"service": "shell_command.run"},
                {"service": "pyscript.do"},
            ],
        }
        result = assess_automation_risk(auto)
        assert result["flags"] == sorted(set(result["flags"]))

    def test_scrutiny_tags_are_deduplicated_and_sorted(self) -> None:
        auto = {
            "trigger": [
                {"platform": "state", "entity_id": "lock.a"},
                {"platform": "state", "entity_id": "lock.b"},
            ],
            "action": [{"action": "light.turn_on"}],
        }
        result = assess_automation_risk(auto)
        assert result["scrutiny_tags"] == sorted(set(result["scrutiny_tags"]))

    def test_uses_action_key_for_service(self) -> None:
        """The action dict may use 'action' instead of 'service' for the service name."""
        auto = {
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [{"action": "shell_command.run"}],
        }
        result = assess_automation_risk(auto)
        assert result["level"] == "elevated"
        assert "compute_capability" in result["flags"]

    def test_entity_ids_from_conditions(self) -> None:
        auto = {
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [{"action": "light.turn_on"}],
            "condition": [{"condition": "state", "entity_id": "camera.front"}],
        }
        result = assess_automation_risk(auto)
        assert "Camera" in result["scrutiny_tags"]

    # -- Nested action containers ------------------------------------------
    #
    # HA automations can hide service calls inside choose/if/parallel/repeat
    # blocks. The risk gate must descend into those containers; otherwise an
    # LLM can wrap shell_command.foo in a top-level choose and bypass the
    # enabled-on-create force-disable.

    def test_elevated_service_inside_choose_sequence(self) -> None:
        auto = {
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [
                {
                    "choose": [
                        {
                            "conditions": [
                                {"condition": "state", "entity_id": "sensor.x", "state": "on"}
                            ],
                            "sequence": [{"service": "shell_command.foo"}],
                        }
                    ],
                    "default": [{"service": "light.turn_on"}],
                }
            ],
        }
        result = assess_automation_risk(auto)
        assert result["level"] == "elevated"
        assert "compute_capability" in result["flags"]

    def test_elevated_service_inside_if_then(self) -> None:
        auto = {
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [
                {
                    "if": [{"condition": "state", "entity_id": "sensor.x", "state": "on"}],
                    "then": [{"action": "python_script.run"}],
                    "else": [{"service": "light.turn_off"}],
                }
            ],
        }
        result = assess_automation_risk(auto)
        assert result["level"] == "elevated"
        assert "compute_capability" in result["flags"]

    def test_elevated_service_inside_parallel(self) -> None:
        auto = {
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [
                {
                    "parallel": [
                        {"service": "light.turn_on"},
                        {"service": "rest_command.poke"},
                    ]
                }
            ],
        }
        result = assess_automation_risk(auto)
        assert result["level"] == "elevated"
        assert "compute_capability" in result["flags"]

    def test_elevated_service_inside_repeat_sequence(self) -> None:
        auto = {
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [
                {
                    "repeat": {
                        "count": 3,
                        "sequence": [{"action": "script.turn_on"}],
                    }
                }
            ],
        }
        result = assess_automation_risk(auto)
        assert result["level"] == "elevated"
        assert "indirect_execution" in result["flags"]

    def test_elevated_service_deeply_nested(self) -> None:
        """choose → sequence → if → then → shell_command must still flag."""
        auto = {
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [
                {
                    "choose": [
                        {
                            "conditions": [
                                {"condition": "state", "entity_id": "sensor.x", "state": "on"}
                            ],
                            "sequence": [
                                {
                                    "if": [
                                        {
                                            "condition": "state",
                                            "entity_id": "sensor.y",
                                            "state": "on",
                                        }
                                    ],
                                    "then": [{"service": "shell_command.danger"}],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        result = assess_automation_risk(auto)
        assert result["level"] == "elevated"
        assert "compute_capability" in result["flags"]

    def test_normal_choose_with_only_safe_services(self) -> None:
        """Recursion must not flag a choose/if/parallel block with only
        safe services. Regression guard against over-eager descent that
        treats a wrapper dict's own keys as service calls."""
        auto = {
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [
                {
                    "choose": [
                        {
                            "conditions": [
                                {"condition": "state", "entity_id": "sensor.x", "state": "on"}
                            ],
                            "sequence": [{"service": "light.turn_on"}],
                        }
                    ],
                    "default": [{"service": "light.turn_off"}],
                },
                {"parallel": [{"service": "switch.turn_on"}]},
                {"repeat": {"count": 2, "sequence": [{"service": "light.toggle"}]}},
            ],
        }
        result = assess_automation_risk(auto)
        assert result["level"] == "normal"
        assert result["flags"] == []

    def test_recursion_does_not_traverse_service_data(self) -> None:
        """A service-call ``data`` field that happens to use ``action`` as a
        key (legitimate for some integrations) must not be re-interpreted
        as a nested service call."""
        auto = {
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [
                {
                    "service": "notify.notify",
                    "data": {
                        # 'action' here is a notification-action button id,
                        # not a service name. Must be ignored.
                        "actions": [{"action": "shell_command.evil", "title": "Run"}],
                    },
                }
            ],
        }
        result = assess_automation_risk(auto)
        assert result["level"] == "normal"


# ===================================================================
# _read_automations_yaml
# ===================================================================


class TestReadAutomationsYaml:
    """Tests for _read_automations_yaml."""

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        assert _read_automations_yaml(tmp_path / "missing.yaml") == []

    def test_empty_file(self, tmp_path: Path) -> None:
        p = tmp_path / "automations.yaml"
        p.write_text("", encoding="utf-8")
        assert _read_automations_yaml(p) == []

    def test_file_with_empty_list_literal(self, tmp_path: Path) -> None:
        p = tmp_path / "automations.yaml"
        p.write_text("[]", encoding="utf-8")
        assert _read_automations_yaml(p) == []

    def test_valid_yaml_list(self, tmp_path: Path) -> None:
        p = tmp_path / "automations.yaml"
        data = [{"id": "a", "alias": "Test"}]
        p.write_text(yaml.dump(data), encoding="utf-8")
        result = _read_automations_yaml(p)
        assert len(result) == 1
        assert result[0]["id"] == "a"

    def test_invalid_yaml_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "automations.yaml"
        p.write_text("{{invalid: yaml: [", encoding="utf-8")
        assert _read_automations_yaml(p) == []

    def test_yaml_dict_returns_empty(self, tmp_path: Path) -> None:
        """If YAML parses to a dict instead of list, should return []."""
        p = tmp_path / "automations.yaml"
        p.write_text("key: value\n", encoding="utf-8")
        assert _read_automations_yaml(p) == []


# ===================================================================
# _parse_automation_yaml
# ===================================================================


class TestParseAutomationYaml:
    """Tests for _parse_automation_yaml."""

    def test_valid_dict(self) -> None:
        result = _parse_automation_yaml("alias: Test\ntrigger: []\n")
        assert isinstance(result, dict)
        assert result["alias"] == "Test"

    def test_non_dict_returns_none(self) -> None:
        assert _parse_automation_yaml("- item1\n- item2\n") is None

    def test_invalid_yaml_returns_none(self) -> None:
        assert _parse_automation_yaml("{{bad yaml") is None

    def test_scalar_returns_none(self) -> None:
        assert _parse_automation_yaml("just a string") is None


# ===================================================================
# Async CRUD functions
# ===================================================================


@pytest.fixture
def automation_service_calls(hass) -> list[tuple[str, str, dict]]:
    """Register dummy automation services so CRUD tests don't raise ServiceNotFound.

    Returns a list that accumulates (domain, service, data) tuples for assertions.
    """
    calls: list[tuple[str, str, dict]] = []

    async def _track(call):
        calls.append((call.domain, call.service, dict(call.data)))

    hass.services.async_register("automation", "reload", _track)
    hass.services.async_register("automation", "turn_on", _track)
    hass.services.async_register("automation", "turn_off", _track)
    return calls


@pytest.fixture
def _patch_store(hass, automation_service_calls):
    """Patch _get_automation_store for CRUD tests."""
    store = _mock_automation_store()
    store._service_calls = automation_service_calls

    with patch(
        "custom_components.selora_ai.automation_utils._get_automation_store",
        return_value=store,
    ):
        yield store


class TestAsyncCreateAutomation:
    """Tests for async_create_automation."""

    @pytest.mark.asyncio
    async def test_creates_automation(
        self, hass: MagicMock, tmp_automations_yaml: Path, _patch_store: MagicMock
    ) -> None:
        hass.states.async_set("light.x", "off")
        suggestion = {
            "alias": "New automation",
            "description": "Test desc",
            "trigger": [{"platform": "time", "at": "09:00"}],
            "action": [{"action": "light.turn_on", "target": {"entity_id": "light.x"}}],
        }
        result = await async_create_automation(hass, suggestion)
        assert result["success"] is True
        assert result["automation_id"] is not None
        assert result["automation_id"].startswith(AUTOMATION_ID_PREFIX)

    @pytest.mark.asyncio
    async def test_calls_reload(
        self, hass: MagicMock, tmp_automations_yaml: Path, _patch_store: MagicMock
    ) -> None:
        suggestion = {
            "alias": "Reload test",
            "trigger": [{"platform": "time", "at": "09:00"}],
            "action": [{"action": "light.turn_on"}],
        }
        await async_create_automation(hass, suggestion)
        assert ("automation", "reload", {}) in _patch_store._service_calls

    @pytest.mark.asyncio
    async def test_records_version(
        self, hass: MagicMock, tmp_automations_yaml: Path, _patch_store: MagicMock
    ) -> None:
        suggestion = {
            "alias": "Version test",
            "trigger": [{"platform": "time", "at": "09:00"}],
            "action": [{"action": "light.turn_on"}],
        }
        await async_create_automation(hass, suggestion, session_id="sess1")
        _patch_store.add_version.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rejects_empty_alias(
        self, hass: MagicMock, tmp_automations_yaml: Path, _patch_store: MagicMock
    ) -> None:
        result = await async_create_automation(
            hass,
            {"alias": "", "trigger": [{"platform": "time"}], "action": [{"action": "x"}]},
        )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_rejects_missing_triggers(
        self, hass: MagicMock, tmp_automations_yaml: Path, _patch_store: MagicMock
    ) -> None:
        result = await async_create_automation(
            hass, {"alias": "No triggers", "action": [{"action": "x"}]}
        )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_writes_to_yaml_file(
        self, hass: MagicMock, tmp_automations_yaml: Path, _patch_store: MagicMock
    ) -> None:
        suggestion = {
            "alias": "Write test",
            "trigger": [{"platform": "time", "at": "10:00"}],
            "action": [{"action": "light.turn_off"}],
        }
        await async_create_automation(hass, suggestion)
        content = yaml.safe_load(tmp_automations_yaml.read_text(encoding="utf-8"))
        aliases = [a["alias"] for a in content]
        assert any("[Selora AI]" in a for a in aliases)


class TestAsyncUpdateAutomation:
    """Tests for async_update_automation."""

    @pytest.mark.asyncio
    async def test_updates_existing_automation(
        self, hass: MagicMock, tmp_automations_yaml: Path, _patch_store: MagicMock
    ) -> None:
        hass.states.async_set("light.porch", "off")
        updated = {
            "alias": "Updated alias",
            "trigger": [{"platform": "sun", "event": "sunrise"}],
            "action": [{"action": "light.turn_off", "target": {"entity_id": "light.porch"}}],
        }
        result = await async_update_automation(hass, "selora_ai_existing1", updated)
        assert result is True

        content = yaml.safe_load(tmp_automations_yaml.read_text(encoding="utf-8"))
        match = [a for a in content if a.get("id") == "selora_ai_existing1"]
        assert match[0]["alias"] == "Updated alias"

    @pytest.mark.asyncio
    async def test_returns_false_for_missing_id(
        self, hass: MagicMock, tmp_automations_yaml: Path, _patch_store: MagicMock
    ) -> None:
        result = await async_update_automation(hass, "nonexistent_id", {"alias": "X"})
        assert result is False

    @pytest.mark.asyncio
    async def test_preserves_initial_state(
        self, hass: MagicMock, tmp_automations_yaml: Path, _patch_store: MagicMock
    ) -> None:
        updated = {
            "alias": "Keep state",
            "trigger": [{"platform": "sun", "event": "sunset"}],
            "action": [{"action": "light.turn_on"}],
        }
        await async_update_automation(hass, "selora_ai_existing1", updated)
        content = yaml.safe_load(tmp_automations_yaml.read_text(encoding="utf-8"))
        match = [a for a in content if a.get("id") == "selora_ai_existing1"]
        assert "initial_state" in match[0]


class TestQuoteYamlBooleans:
    """Tests for _quote_yaml_booleans — wraps YAML-boolean strings for ruamel."""

    def test_wraps_on_off_in_double_quoted_scalar(self) -> None:
        from ruamel.yaml.scalarstring import DoubleQuotedScalarString

        automations = [{"trigger": [{"platform": "state", "to": "on", "from": "off"}]}]
        _quote_yaml_booleans(automations)
        assert isinstance(automations[0]["trigger"][0]["to"], DoubleQuotedScalarString)
        assert isinstance(automations[0]["trigger"][0]["from"], DoubleQuotedScalarString)

    def test_wraps_all_yaml_boolean_variants(self) -> None:
        from ruamel.yaml.scalarstring import DoubleQuotedScalarString

        automations = [
            {
                "trigger": [
                    {"platform": "state", "to": "yes", "from": "no"},
                    {"platform": "state", "to": "true", "from": "false"},
                    {"platform": "state", "to": "y", "from": "n"},
                ]
            }
        ]
        _quote_yaml_booleans(automations)
        for trig in automations[0]["trigger"]:
            assert isinstance(trig["to"], DoubleQuotedScalarString)
            assert isinstance(trig["from"], DoubleQuotedScalarString)

    def test_leaves_non_boolean_strings_alone(self) -> None:
        from ruamel.yaml.scalarstring import DoubleQuotedScalarString

        automations = [{"trigger": [{"platform": "state", "to": "home", "from": "away"}]}]
        _quote_yaml_booleans(automations)
        assert not isinstance(automations[0]["trigger"][0]["to"], DoubleQuotedScalarString)
        assert not isinstance(automations[0]["trigger"][0]["from"], DoubleQuotedScalarString)

    def test_coerces_bool_values_to_quoted_strings(self) -> None:
        from ruamel.yaml.scalarstring import DoubleQuotedScalarString

        automations = [{"trigger": [{"platform": "state", "to": True, "from": False}]}]
        _quote_yaml_booleans(automations)
        assert isinstance(automations[0]["trigger"][0]["to"], DoubleQuotedScalarString)
        assert str(automations[0]["trigger"][0]["to"]) == "on"
        assert isinstance(automations[0]["trigger"][0]["from"], DoubleQuotedScalarString)
        assert str(automations[0]["trigger"][0]["from"]) == "off"

    def test_case_insensitive(self) -> None:
        from ruamel.yaml.scalarstring import DoubleQuotedScalarString

        automations = [{"trigger": [{"platform": "state", "to": "ON", "from": "Off"}]}]
        _quote_yaml_booleans(automations)
        assert isinstance(automations[0]["trigger"][0]["to"], DoubleQuotedScalarString)
        assert str(automations[0]["trigger"][0]["to"]) == "ON"


class TestWriteAutomationsYamlQuoting:
    """Tests that _write_automations_yaml quotes boolean-like trigger values in YAML output."""

    def test_on_off_survive_ruamel_round_trip(self, tmp_path: Path) -> None:
        from ruamel.yaml import YAML

        path = tmp_path / "automations.yaml"
        automations = [
            {
                "id": "test_1",
                "alias": "Round trip test",
                "trigger": [
                    {"platform": "state", "entity_id": "sensor.x", "from": "on", "to": "off"}
                ],
                "action": [{"action": "notify.notify"}],
            }
        ]
        _write_automations_yaml(path, automations)

        reparsed = YAML().load(path)
        assert reparsed[0]["trigger"][0]["from"] == "on"
        assert reparsed[0]["trigger"][0]["to"] == "off"

    def test_raw_yaml_contains_double_quotes(self, tmp_path: Path) -> None:
        path = tmp_path / "automations.yaml"
        automations = [
            {
                "id": "test_q",
                "alias": "Quote check",
                "trigger": [
                    {"platform": "state", "entity_id": "sensor.x", "from": "on", "to": "off"}
                ],
                "action": [{"action": "notify.notify"}],
            }
        ]
        _write_automations_yaml(path, automations)

        raw = path.read_text(encoding="utf-8")
        assert '"on"' in raw
        assert '"off"' in raw

    def test_double_round_trip(self, tmp_path: Path) -> None:
        from ruamel.yaml import YAML

        path = tmp_path / "automations.yaml"
        automations = [
            {
                "id": "test_drt",
                "alias": "Double round trip",
                "trigger": [
                    {"platform": "state", "entity_id": "sensor.x", "from": "on", "to": "off"}
                ],
                "action": [{"action": "notify.notify"}],
            }
        ]
        _write_automations_yaml(path, automations)
        first_read = YAML().load(path)
        _write_automations_yaml(path, list(first_read))
        second_read = YAML().load(path)
        assert second_read[0]["trigger"][0]["from"] == "on"
        assert second_read[0]["trigger"][0]["to"] == "off"

    def test_read_write_round_trip_quotes_bare_booleans(self, tmp_path: Path) -> None:
        """Simulates HA writing bare on/off (no quotes), then our code re-reading and re-writing.

        Previously _read_automations_yaml used yaml.safe_load which turned bare
        on/off into Python bools True/False, and _quote_yaml_booleans only handled
        str values — so the re-written file would still have bare on/off.
        """
        from ruamel.yaml import YAML

        path = tmp_path / "automations.yaml"
        # Write a file with bare on/off (as HA's own dumper would)
        path.write_text(
            "- id: test_ha\n"
            "  alias: HA wrote this\n"
            "  trigger:\n"
            "  - platform: state\n"
            "    entity_id: sensor.x\n"
            "    from: off\n"
            "    to: on\n"
            "  action:\n"
            "  - action: notify.notify\n",
            encoding="utf-8",
        )

        # Our read + write cycle should add quotes
        data = _read_automations_yaml(path)
        _write_automations_yaml(path, data)

        raw = path.read_text(encoding="utf-8")
        assert '"on"' in raw
        assert '"off"' in raw

        reparsed = YAML().load(path)
        assert reparsed[0]["trigger"][0]["from"] == "off"
        assert reparsed[0]["trigger"][0]["to"] == "on"

    def test_time_strings_survive_pyyaml_round_trip(self, tmp_path: Path) -> None:
        """Time strings like 23:46:00 must be quoted to survive PyYAML.

        YAML 1.1 treats bare HH:MM:SS as sexagesimal integers
        (23:46:00 → 85560).  Our writer must double-quote them.
        """
        path = tmp_path / "automations.yaml"
        automations = [
            {
                "id": "test_time",
                "alias": "Night Lock",
                "trigger": [{"platform": "time", "at": "23:46:00"}],
                "condition": [{"condition": "time", "after": "22:00:00", "before": "06:00:00"}],
                "action": [{"action": "lock.lock", "target": {"entity_id": "lock.yale"}}],
            }
        ]
        _write_automations_yaml(path, automations)

        raw = path.read_text(encoding="utf-8")
        assert '"23:46:00"' in raw
        assert '"22:00:00"' in raw
        assert '"06:00:00"' in raw

        # Verify PyYAML reads them back as strings, not integers
        loaded = yaml.safe_load(raw)
        assert loaded[0]["trigger"][0]["at"] == "23:46:00"
        assert loaded[0]["condition"][0]["after"] == "22:00:00"
        assert loaded[0]["condition"][0]["before"] == "06:00:00"


class TestCreateAutomationValidation:
    """Tests that async_create_automation validates and coerces trigger values."""

    @pytest.mark.asyncio
    async def test_boolean_triggers_coerced_in_yaml(
        self, hass, tmp_automations_yaml: Path, _patch_store
    ) -> None:
        hass.states.async_set("sensor.dryer", "off")
        suggestion = {
            "alias": "Dryer Done",
            "trigger": [
                {"platform": "state", "entity_id": "sensor.dryer", "from": True, "to": False}
            ],
            "action": [{"action": "notify.notify", "data": {"message": "done"}}],
        }
        result = await async_create_automation(hass, suggestion)
        assert result["success"] is True

        # yaml.safe_load proves coercion: bare on/off would parse as bool True/False
        content = yaml.safe_load(tmp_automations_yaml.read_text(encoding="utf-8"))
        new = [
            a for a in content if a["id"].startswith(AUTOMATION_ID_PREFIX) and "Dryer" in a["alias"]
        ]
        assert new[0]["triggers"][0]["from"] == "on"
        assert new[0]["triggers"][0]["to"] == "off"

        # Also verify the raw file text contains double-quoted values
        raw = tmp_automations_yaml.read_text(encoding="utf-8")
        assert '"on"' in raw
        assert '"off"' in raw

    @pytest.mark.asyncio
    async def test_null_from_stripped(self, hass, tmp_automations_yaml: Path, _patch_store) -> None:
        hass.states.async_set("sensor.x", "off")
        suggestion = {
            "alias": "Null From Test",
            "trigger": [{"platform": "state", "entity_id": "sensor.x", "from": None, "to": "on"}],
            "action": [{"action": "notify.notify"}],
        }
        result = await async_create_automation(hass, suggestion)
        assert result["success"] is True

        content = yaml.safe_load(tmp_automations_yaml.read_text(encoding="utf-8"))
        new = [a for a in content if "Null From" in a.get("alias", "")]
        assert "from" not in new[0]["triggers"][0]

    @pytest.mark.asyncio
    async def test_initial_state_defaults_to_false(
        self, hass, tmp_automations_yaml: Path, _patch_store
    ) -> None:
        suggestion = {
            "alias": "Default State Test",
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [{"action": "notify.notify"}],
        }
        result = await async_create_automation(hass, suggestion)
        assert result["success"] is True

        content = yaml.safe_load(tmp_automations_yaml.read_text(encoding="utf-8"))
        new = [a for a in content if "Default State" in a.get("alias", "")]
        assert new[0]["initial_state"] is False

    @pytest.mark.asyncio
    async def test_suggestion_initial_state_true_is_ignored(
        self, hass, tmp_automations_yaml: Path, _patch_store
    ) -> None:
        """Security: a suggestion (LLM-generated) cannot smuggle initial_state=True
        past the user-confirmation step. async_create_automation requires the caller
        to pass enabled=True explicitly; the suggestion field is dropped."""
        suggestion = {
            "alias": "Smuggled Enabled",
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [{"action": "notify.notify"}],
            "initial_state": True,
        }
        ok, _, normalized = validate_automation_payload(suggestion)
        assert ok is True
        # No enabled=True kwarg → must be written disabled even though the
        # suggestion claims initial_state=True.
        result = await async_create_automation(hass, normalized)
        assert result["success"] is True

        content = yaml.safe_load(tmp_automations_yaml.read_text(encoding="utf-8"))
        new = [a for a in content if "Smuggled Enabled" in a.get("alias", "")]
        assert new[0]["initial_state"] is False

    @pytest.mark.asyncio
    async def test_explicit_enabled_kwarg_enables(
        self, hass, tmp_automations_yaml: Path, _patch_store
    ) -> None:
        """Caller-confirmed enable-on-create paths (quick-create, scheduled actions,
        MCP enabled=True) must still produce an enabled automation."""
        suggestion = {
            "alias": "Quick Create Enabled",
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [{"action": "notify.notify"}],
        }
        ok, _, normalized = validate_automation_payload(suggestion)
        assert ok is True
        result = await async_create_automation(hass, normalized, enabled=True)
        assert result["success"] is True

        content = yaml.safe_load(tmp_automations_yaml.read_text(encoding="utf-8"))
        new = [a for a in content if "Quick Create" in a.get("alias", "")]
        assert new[0]["initial_state"] is True

    @pytest.mark.asyncio
    async def test_elevated_risk_forced_disabled_even_when_enabled_requested(
        self, hass, tmp_automations_yaml: Path, _patch_store
    ) -> None:
        """Elevated-risk automations (shell_command, python_script, webhook, etc.)
        must be written disabled regardless of the enabled kwarg, so the user has
        to consciously enable them after review."""
        # Webhook trigger flags as 'remote_ingress_trigger' in assess_automation_risk
        # without requiring any non-default HA service to exist.
        suggestion = {
            "alias": "Risky Webhook",
            "trigger": [{"platform": "webhook", "webhook_id": "selora-test"}],
            "action": [{"action": "notify.notify", "data": {"message": "hi"}}],
        }
        ok, _, normalized = validate_automation_payload(suggestion)
        assert ok is True
        result = await async_create_automation(hass, normalized, enabled=True)
        assert result["success"] is True
        assert result.get("forced_disabled") is True
        assert result.get("risk_level") == "elevated"

        content = yaml.safe_load(tmp_automations_yaml.read_text(encoding="utf-8"))
        new = [a for a in content if "Risky Webhook" in a.get("alias", "")]
        assert new[0]["initial_state"] is False

    @pytest.mark.asyncio
    async def test_invalid_suggestion_rejected(
        self, hass, tmp_automations_yaml: Path, _patch_store
    ) -> None:
        result = await async_create_automation(hass, {"alias": "", "trigger": [], "action": []})
        assert result["success"] is False


class TestUpdateAutomationValidation:
    """Tests that async_update_automation validates and coerces trigger values."""

    @pytest.mark.asyncio
    async def test_boolean_triggers_coerced_on_update(
        self, hass, tmp_automations_yaml: Path, _patch_store
    ) -> None:
        hass.states.async_set("sensor.test", "off")
        updated = {
            "alias": "Updated Automation",
            "trigger": [
                {"platform": "state", "entity_id": "sensor.test", "from": True, "to": False}
            ],
            "action": [{"action": "notify.notify", "data": {"message": "updated"}}],
        }
        result = await async_update_automation(hass, "selora_ai_existing1", updated)
        assert result is True

        content = yaml.safe_load(tmp_automations_yaml.read_text(encoding="utf-8"))
        match = [a for a in content if a.get("id") == "selora_ai_existing1"]
        assert match[0]["triggers"][0]["from"] == "on"
        assert match[0]["triggers"][0]["to"] == "off"

    @pytest.mark.asyncio
    async def test_invalid_update_rejected(
        self, hass, tmp_automations_yaml: Path, _patch_store
    ) -> None:
        result = await async_update_automation(
            hass, "selora_ai_existing1", {"alias": "", "trigger": [], "action": []}
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_invalid_update_does_not_modify_yaml(
        self, hass, tmp_automations_yaml: Path, _patch_store
    ) -> None:
        before = tmp_automations_yaml.read_text(encoding="utf-8")
        await async_update_automation(
            hass, "selora_ai_existing1", {"alias": "", "trigger": [], "action": []}
        )
        after = tmp_automations_yaml.read_text(encoding="utf-8")
        assert before == after


class TestAsyncToggleAutomation:
    """Tests for async_toggle_automation."""

    @pytest.mark.asyncio
    async def test_toggle_enable(
        self, hass, tmp_automations_yaml: Path, automation_service_calls
    ) -> None:
        result = await async_toggle_automation(
            hass, "selora_ai_existing1", "automation.selora_ai_existing1", True
        )
        assert result is True
        assert (
            "automation",
            "turn_on",
            {"entity_id": "automation.selora_ai_existing1"},
        ) in automation_service_calls

    @pytest.mark.asyncio
    async def test_toggle_disable(
        self, hass, tmp_automations_yaml: Path, automation_service_calls
    ) -> None:
        result = await async_toggle_automation(
            hass, "selora_ai_existing1", "automation.selora_ai_existing1", False
        )
        assert result is True
        assert (
            "automation",
            "turn_off",
            {"entity_id": "automation.selora_ai_existing1"},
        ) in automation_service_calls

    @pytest.mark.asyncio
    async def test_toggle_missing_id(self, hass: MagicMock, tmp_automations_yaml: Path) -> None:
        result = await async_toggle_automation(hass, "nonexistent", "automation.nonexistent", True)
        assert result is False

    @pytest.mark.asyncio
    async def test_toggle_persists_initial_state(
        self, hass: MagicMock, tmp_automations_yaml: Path
    ) -> None:
        await async_toggle_automation(
            hass, "selora_ai_existing1", "automation.selora_ai_existing1", False
        )
        content = yaml.safe_load(tmp_automations_yaml.read_text(encoding="utf-8"))
        match = [a for a in content if a.get("id") == "selora_ai_existing1"]
        assert match[0]["initial_state"] is False


class TestAsyncDeleteAutomation:
    """Tests for async_delete_automation."""

    @pytest.mark.asyncio
    async def test_delete_removes_from_yaml(
        self, hass: MagicMock, tmp_automations_yaml: Path, _patch_store: MagicMock
    ) -> None:
        result = await async_delete_automation(hass, "selora_ai_existing1")
        assert result is True
        content = yaml.safe_load(tmp_automations_yaml.read_text(encoding="utf-8"))
        ids = [a.get("id") for a in content]
        assert "selora_ai_existing1" not in ids

    @pytest.mark.asyncio
    async def test_delete_purges_store(
        self, hass: MagicMock, tmp_automations_yaml: Path, _patch_store: MagicMock
    ) -> None:
        await async_delete_automation(hass, "selora_ai_existing1")
        _patch_store.purge_record.assert_awaited_once_with("selora_ai_existing1")

    @pytest.mark.asyncio
    async def test_delete_missing_id(
        self, hass: MagicMock, tmp_automations_yaml: Path, _patch_store: MagicMock
    ) -> None:
        result = await async_delete_automation(hass, "nonexistent")
        assert result is False


# ===================================================================
# Automation Cap & Stale Detection
# ===================================================================


def _make_automation_state(
    entity_id: str,
    uid: str,
    last_triggered: str | datetime | None = None,
    friendly_name: str = "Test",
    state_value: str = "on",
    last_updated: datetime | None = None,
) -> MagicMock:
    """Create a mock HA automation state object."""
    from datetime import UTC, datetime as _dt, timedelta

    state = MagicMock()
    state.entity_id = entity_id
    state.state = state_value
    # Default last_updated to 30 days ago so never-triggered automations
    # are old enough to be flagged as stale by default.
    state.last_updated = last_updated or (_dt.now(UTC) - timedelta(days=30))
    state.attributes = {
        "id": uid,
        "friendly_name": friendly_name,
        "last_triggered": last_triggered,
    }
    return state


def _make_registries(device_count: int):
    """Create mock device and entity registries with N devices, each having an entity."""
    device_reg = MagicMock()
    device_reg.devices = {f"dev_{i}": MagicMock() for i in range(device_count)}

    entity_reg = MagicMock()
    # Each device gets one entity so it counts towards the cap
    entities = {}
    for i in range(device_count):
        entry = MagicMock()
        entry.device_id = f"dev_{i}"
        entities[f"entity_{i}"] = entry
    entity_reg.entities = entities

    return device_reg, entity_reg


class TestAutomationCap:
    """Tests for get_selora_automation_cap."""

    def _get_cap(self, device_count: int) -> int:
        hass = MagicMock()
        dev_reg, ent_reg = _make_registries(device_count)
        with (
            patch("homeassistant.helpers.device_registry.async_get", return_value=dev_reg),
            patch("homeassistant.helpers.entity_registry.async_get", return_value=ent_reg),
        ):
            return get_selora_automation_cap(hass)

    def test_cap_scales_with_devices(self) -> None:
        assert self._get_cap(10) == 15  # floor(1.5 * 10)

    def test_cap_floor_on_zero_devices(self) -> None:
        assert self._get_cap(0) == AUTOMATION_CAP_FLOOR

    def test_cap_floor_on_small_home(self) -> None:
        assert self._get_cap(2) == AUTOMATION_CAP_FLOOR  # floor(1.5 * 2) = 3 < 5

    def test_cap_large_home(self) -> None:
        assert self._get_cap(60) == 90  # floor(1.5 * 60)

    def test_cap_ceiling_on_very_large_home(self) -> None:
        assert self._get_cap(500) == AUTOMATION_CAP_CEILING

    def test_cap_odd_device_count(self) -> None:
        assert self._get_cap(7) == 10  # floor(1.5 * 7) = floor(10.5) = 10

    def test_devices_without_entities_not_counted(self) -> None:
        """Infrastructure devices with no entities shouldn't inflate the cap."""
        hass = MagicMock()
        device_reg = MagicMock()
        # 20 devices in registry
        device_reg.devices = {f"dev_{i}": MagicMock() for i in range(20)}

        entity_reg = MagicMock()
        # Only 10 devices have entities
        entities = {}
        for i in range(10):
            entry = MagicMock()
            entry.device_id = f"dev_{i}"
            entities[f"entity_{i}"] = entry
        entity_reg.entities = entities

        with (
            patch("homeassistant.helpers.device_registry.async_get", return_value=device_reg),
            patch("homeassistant.helpers.entity_registry.async_get", return_value=entity_reg),
        ):
            cap = get_selora_automation_cap(hass)
        assert cap == 15  # floor(1.5 * 10), not floor(1.5 * 20)


class TestCountSeloraAutomations:
    """Tests for count_selora_automations."""

    def test_counts_only_selora_automations(self) -> None:
        hass = MagicMock()
        hass.states.async_all.return_value = [
            _make_automation_state("automation.a", "selora_ai_abc123"),
            _make_automation_state("automation.b", "selora_ai_def456"),
            _make_automation_state("automation.c", "user_custom_123"),
        ]
        assert count_selora_automations(hass) == 2

    def test_counts_zero_when_none(self) -> None:
        hass = MagicMock()
        hass.states.async_all.return_value = [
            _make_automation_state("automation.c", "user_custom_123"),
        ]
        assert count_selora_automations(hass) == 0

    def test_enabled_only_skips_disabled(self) -> None:
        hass = MagicMock()
        hass.states.async_all.return_value = [
            _make_automation_state("automation.a", "selora_ai_aaa", state_value="on"),
            _make_automation_state("automation.b", "selora_ai_bbb", state_value="off"),
            _make_automation_state("automation.c", "selora_ai_ccc", state_value="on"),
        ]
        assert count_selora_automations(hass, enabled_only=True) == 2
        assert count_selora_automations(hass) == 3


class TestFindStaleAutomations:
    """Tests for find_stale_automations."""

    def test_enabled_never_triggered_is_stale(self) -> None:
        hass = MagicMock()
        hass.states.async_all.return_value = [
            _make_automation_state(
                "automation.a", "selora_ai_abc123",
                last_triggered=None,
                friendly_name="Never triggered",
                state_value="on",
            ),
        ]
        stale = find_stale_automations(hass)
        assert len(stale) == 1
        assert stale[0]["automation_id"] == "selora_ai_abc123"
        assert stale[0]["last_triggered"] is None

    def test_disabled_never_triggered_not_stale(self) -> None:
        """Disabled automations can't trigger — they shouldn't be flagged as stale."""
        hass = MagicMock()
        hass.states.async_all.return_value = [
            _make_automation_state(
                "automation.a", "selora_ai_abc123",
                last_triggered=None,
                state_value="off",
            ),
        ]
        stale = find_stale_automations(hass)
        assert len(stale) == 0

    def test_recently_triggered_not_stale(self) -> None:
        from datetime import UTC, datetime

        recent = datetime.now(UTC).isoformat()
        hass = MagicMock()
        hass.states.async_all.return_value = [
            _make_automation_state(
                "automation.a", "selora_ai_abc123",
                last_triggered=recent,
            ),
        ]
        stale = find_stale_automations(hass)
        assert len(stale) == 0

    def test_old_trigger_is_stale(self) -> None:
        from datetime import UTC, datetime, timedelta

        old = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        hass = MagicMock()
        hass.states.async_all.return_value = [
            _make_automation_state(
                "automation.a", "selora_ai_abc123",
                last_triggered=old,
                friendly_name="Old automation",
            ),
        ]
        stale = find_stale_automations(hass)
        assert len(stale) == 1
        assert stale[0]["alias"] == "Old automation"

    def test_ignores_non_selora_automations(self) -> None:
        hass = MagicMock()
        hass.states.async_all.return_value = [
            _make_automation_state(
                "automation.user", "user_custom",
                last_triggered=None,
            ),
        ]
        stale = find_stale_automations(hass)
        assert len(stale) == 0

    def test_mixed_stale_and_active(self) -> None:
        from datetime import UTC, datetime, timedelta

        recent = datetime.now(UTC).isoformat()
        old = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        hass = MagicMock()
        hass.states.async_all.return_value = [
            _make_automation_state("automation.a", "selora_ai_aaa", last_triggered=recent),
            _make_automation_state("automation.b", "selora_ai_bbb", last_triggered=old),
            _make_automation_state("automation.c", "selora_ai_ccc", last_triggered=None),
            _make_automation_state("automation.d", "user_custom", last_triggered=None),
            _make_automation_state("automation.e", "selora_ai_eee", last_triggered=None, state_value="off"),
        ]
        stale = find_stale_automations(hass)
        assert len(stale) == 2
        stale_ids = {s["automation_id"] for s in stale}
        assert stale_ids == {"selora_ai_bbb", "selora_ai_ccc"}

    def test_unparseable_last_triggered_skipped(self) -> None:
        """Automations with unparseable last_triggered strings are skipped."""
        hass = MagicMock()
        hass.states.async_all.return_value = [
            _make_automation_state(
                "automation.bad", "selora_ai_bad",
                last_triggered="not-a-date",
            ),
        ]
        stale = find_stale_automations(hass)
        assert len(stale) == 0

    def test_native_datetime_last_triggered(self) -> None:
        """HA typically provides last_triggered as a native datetime object."""
        from datetime import UTC, datetime, timedelta

        old_dt = datetime.now(UTC) - timedelta(days=10)
        hass = MagicMock()
        state = _make_automation_state("automation.a", "selora_ai_aaa")
        state.attributes["last_triggered"] = old_dt
        hass.states.async_all.return_value = [state]
        stale = find_stale_automations(hass)
        assert len(stale) == 1

    def test_naive_datetime_last_triggered(self) -> None:
        """Naive datetimes (no tzinfo) should be treated as UTC."""
        from datetime import datetime, timedelta

        old_naive = datetime.now() - timedelta(days=10)
        hass = MagicMock()
        state = _make_automation_state("automation.a", "selora_ai_aaa")
        state.attributes["last_triggered"] = old_naive
        hass.states.async_all.return_value = [state]
        stale = find_stale_automations(hass)
        assert len(stale) == 1

    def test_unexpected_last_triggered_type_skipped(self) -> None:
        """Unexpected types for last_triggered are skipped with a warning."""
        hass = MagicMock()
        state = _make_automation_state("automation.a", "selora_ai_aaa")
        state.attributes["last_triggered"] = 12345  # not str, datetime, or None
        hass.states.async_all.return_value = [state]
        stale = find_stale_automations(hass)
        assert len(stale) == 0

    def test_newly_created_never_triggered_not_stale(self) -> None:
        """An automation created recently that hasn't triggered yet is not stale."""
        from datetime import UTC, datetime

        hass = MagicMock()
        hass.states.async_all.return_value = [
            _make_automation_state(
                "automation.new", "selora_ai_new",
                last_triggered=None,
                last_updated=datetime.now(UTC),  # just created
            ),
        ]
        stale = find_stale_automations(hass)
        assert len(stale) == 0
