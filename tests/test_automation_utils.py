"""Comprehensive unit tests for automation_utils module."""

from __future__ import annotations

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
    validate_action_services,
    validate_automation_payload,
)
from custom_components.selora_ai.const import AUTOMATION_ID_PREFIX

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
        assert isinstance(result["trigger"], list)
        assert isinstance(result["action"], list)
        assert isinstance(result["condition"], list)

    def test_uses_triggers_key(self) -> None:
        payload = {
            "alias": "Alt",
            "triggers": [{"platform": "sun", "event": "sunset"}],
            "actions": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert len(result["trigger"]) == 1

    def test_wraps_single_trigger_in_list(self) -> None:
        payload = {
            "alias": "Single",
            "trigger": {"platform": "time", "at": "08:00"},
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert isinstance(result["trigger"], list)
        assert len(result["trigger"]) == 1

    def test_wraps_single_action_in_list(self) -> None:
        payload = {
            "alias": "Single",
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": {"action": "light.turn_on"},
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert isinstance(result["action"], list)

    def test_wraps_single_condition_in_list(self) -> None:
        payload = {
            "alias": "Cond",
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [{"action": "light.turn_on"}],
            "condition": {"condition": "state", "entity_id": "light.x", "state": "on"},
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert isinstance(result["condition"], list)
        assert len(result["condition"]) == 1

    def test_renames_trigger_key_to_platform(self) -> None:
        payload = {
            "alias": "Rename",
            "trigger": [{"trigger": "state", "entity_id": "light.x"}],
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["trigger"][0]["platform"] == "state"
        assert "trigger" not in result["trigger"][0]

    # -- boolean / None / numeric coercion --------------------------------

    def test_coerces_boolean_true_to_on(self) -> None:
        payload = {
            "alias": "Bool",
            "trigger": [{"platform": "state", "entity_id": "light.x", "to": True}],
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["trigger"][0]["to"] == "on"

    def test_coerces_boolean_false_to_off(self) -> None:
        payload = {
            "alias": "Bool",
            "trigger": [{"platform": "state", "entity_id": "light.x", "from": False}],
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["trigger"][0]["from"] == "off"

    def test_removes_none_to_from(self) -> None:
        payload = {
            "alias": "NoneVal",
            "trigger": [{"platform": "state", "entity_id": "light.x", "to": None, "from": None}],
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert "to" not in result["trigger"][0]
        assert "from" not in result["trigger"][0]

    def test_stringifies_numeric_to_from(self) -> None:
        payload = {
            "alias": "Numeric",
            "trigger": [{"platform": "state", "entity_id": "sensor.x", "to": 42, "from": 3.14}],
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["trigger"][0]["to"] == "42"
        assert result["trigger"][0]["from"] == "3.14"

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
        assert result["condition"][0]["state"] == "on"

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
        assert result["condition"][0]["state"] == "off"

    def test_leaves_condition_state_string_unchanged(self) -> None:
        payload = {
            "alias": "CondStr",
            "trigger": [{"platform": "state", "entity_id": "binary_sensor.x"}],
            "action": [{"action": "light.turn_on"}],
            "condition": [{"condition": "state", "entity_id": "sensor.temp", "state": "25.5"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["condition"][0]["state"] == "25.5"

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
        assert result["condition"][0]["after"] == "21:00:00"
        assert result["condition"][0]["before"] == "22:00:00"

    def test_leaves_time_condition_string_unchanged(self) -> None:
        payload = {
            "alias": "TimeStr",
            "trigger": [{"platform": "state", "entity_id": "binary_sensor.x"}],
            "action": [{"action": "light.turn_on"}],
            "condition": [{"condition": "time", "after": "21:00:00", "before": "22:00:00"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["condition"][0]["after"] == "21:00:00"
        assert result["condition"][0]["before"] == "22:00:00"

    # -- time trigger `at` coercion ---------------------------------------

    def test_coerces_time_trigger_integer_at(self) -> None:
        payload = {
            "alias": "TrigTimeInt",
            "trigger": [{"platform": "time", "at": 81000}],
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["trigger"][0]["at"] == "22:30:00"

    def test_coerces_time_trigger_at_midnight(self) -> None:
        payload = {
            "alias": "Midnight",
            "trigger": [{"platform": "time", "at": 0}],
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["trigger"][0]["at"] == "00:00:00"

    def test_leaves_time_trigger_string_at_unchanged(self) -> None:
        payload = {
            "alias": "TrigTimeStr",
            "trigger": [{"platform": "time", "at": "07:00:00"}],
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["trigger"][0]["at"] == "07:00:00"

    # -- duration `for` coercion ------------------------------------------

    def test_coerces_duration_for_integer_to_dict_in_trigger(self) -> None:
        payload = {
            "alias": "ForInt",
            "trigger": [{"platform": "state", "entity_id": "light.x", "to": "on", "for": 300}],
            "action": [{"action": "light.turn_on"}],
        }
        ok, _, result = validate_automation_payload(payload)
        assert ok is True
        assert result["trigger"][0]["for"] == {"seconds": 300}

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
        assert result["condition"][0]["for"] == {"seconds": 600}


# ===================================================================
# validate_action_services
# ===================================================================


class TestValidateActionServices:
    """Tests for validate_action_services."""

    @staticmethod
    def _mock_hass(services: dict[str, list[str]]) -> MagicMock:
        hass = MagicMock()
        hass.services.async_services.return_value = {
            domain: {svc: None for svc in svcs} for domain, svcs in services.items()
        }
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
        assert new[0]["trigger"][0]["from"] == "on"
        assert new[0]["trigger"][0]["to"] == "off"

        # Also verify the raw file text contains double-quoted values
        raw = tmp_automations_yaml.read_text(encoding="utf-8")
        assert '"on"' in raw
        assert '"off"' in raw

    @pytest.mark.asyncio
    async def test_null_from_stripped(self, hass, tmp_automations_yaml: Path, _patch_store) -> None:
        suggestion = {
            "alias": "Null From Test",
            "trigger": [{"platform": "state", "entity_id": "sensor.x", "from": None, "to": "on"}],
            "action": [{"action": "notify.notify"}],
        }
        result = await async_create_automation(hass, suggestion)
        assert result["success"] is True

        content = yaml.safe_load(tmp_automations_yaml.read_text(encoding="utf-8"))
        new = [a for a in content if "Null From" in a.get("alias", "")]
        assert "from" not in new[0]["trigger"][0]

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
    async def test_initial_state_true_preserved_through_normalized(
        self, hass, tmp_automations_yaml: Path, _patch_store
    ) -> None:
        """Regression: quick-create sets initial_state=True, validates, then passes
        normalized dict to async_create_automation. The created automation must be enabled."""
        suggestion = {
            "alias": "Quick Create Enabled",
            "trigger": [{"platform": "time", "at": "08:00"}],
            "action": [{"action": "notify.notify"}],
            "initial_state": True,
        }
        # Simulate __init__.py quick-create flow: validate then pass normalized
        ok, _, normalized = validate_automation_payload(suggestion)
        assert ok is True
        result = await async_create_automation(hass, normalized)
        assert result["success"] is True

        content = yaml.safe_load(tmp_automations_yaml.read_text(encoding="utf-8"))
        new = [a for a in content if "Quick Create" in a.get("alias", "")]
        assert new[0]["initial_state"] is True

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
        assert match[0]["trigger"][0]["from"] == "on"
        assert match[0]["trigger"][0]["to"] == "off"

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
