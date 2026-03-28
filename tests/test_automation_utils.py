"""Comprehensive unit tests for automation_utils module."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from custom_components.selora_ai.automation_utils import (
    _parse_automation_yaml,
    _read_automations_yaml,
    assess_automation_risk,
    async_create_automation,
    async_hard_delete_automation,
    async_restore_automation,
    async_soft_delete_automation,
    async_toggle_automation,
    async_update_automation,
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
    store.soft_delete = AsyncMock(return_value=True)
    store.restore = AsyncMock()
    store.get_record = AsyncMock(return_value={"deleted_at": "2026-01-01T00:00:00"})
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


class TestAsyncSoftDeleteAutomation:
    """Tests for async_soft_delete_automation."""

    @pytest.mark.asyncio
    async def test_soft_delete_disables(
        self, hass: MagicMock, tmp_automations_yaml: Path, _patch_store: MagicMock
    ) -> None:
        result = await async_soft_delete_automation(hass, "selora_ai_existing1")
        assert result is True
        content = yaml.safe_load(tmp_automations_yaml.read_text(encoding="utf-8"))
        match = [a for a in content if a.get("id") == "selora_ai_existing1"]
        assert match[0]["initial_state"] is False

    @pytest.mark.asyncio
    async def test_soft_delete_marks_store(
        self, hass: MagicMock, tmp_automations_yaml: Path, _patch_store: MagicMock
    ) -> None:
        await async_soft_delete_automation(hass, "selora_ai_existing1")
        _patch_store.soft_delete.assert_awaited()

    @pytest.mark.asyncio
    async def test_soft_delete_missing_id(
        self, hass: MagicMock, tmp_automations_yaml: Path, _patch_store: MagicMock
    ) -> None:
        result = await async_soft_delete_automation(hass, "nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_soft_delete_bootstraps_when_no_record(
        self, hass: MagicMock, tmp_automations_yaml: Path, _patch_store: MagicMock
    ) -> None:
        """When soft_delete returns False (no record), it bootstraps then retries."""
        _patch_store.soft_delete = AsyncMock(side_effect=[False, True])
        result = await async_soft_delete_automation(hass, "selora_ai_existing1")
        assert result is True
        _patch_store.add_version.assert_awaited_once()
        assert _patch_store.soft_delete.await_count == 2


class TestAsyncHardDeleteAutomation:
    """Tests for async_hard_delete_automation."""

    @pytest.mark.asyncio
    async def test_hard_delete_removes_from_yaml(
        self, hass, tmp_automations_yaml: Path, automation_service_calls
    ) -> None:
        store = _mock_automation_store()
        await async_hard_delete_automation(hass, store, "selora_ai_existing1")

        content = yaml.safe_load(tmp_automations_yaml.read_text(encoding="utf-8"))
        ids = [a.get("id") for a in content]
        assert "selora_ai_existing1" not in ids

    @pytest.mark.asyncio
    async def test_hard_delete_purges_store(
        self, hass, tmp_automations_yaml: Path, automation_service_calls
    ) -> None:
        store = _mock_automation_store()
        await async_hard_delete_automation(hass, store, "selora_ai_existing1")
        store.purge_record.assert_awaited_once_with("selora_ai_existing1")

    @pytest.mark.asyncio
    async def test_hard_delete_raises_if_not_soft_deleted(
        self, hass, tmp_automations_yaml: Path
    ) -> None:
        store = _mock_automation_store()
        store.get_record = AsyncMock(return_value={"deleted_at": None})
        with pytest.raises(ValueError, match="must be soft-deleted"):
            await async_hard_delete_automation(hass, store, "selora_ai_existing1")

    @pytest.mark.asyncio
    async def test_hard_delete_raises_if_no_record(self, hass, tmp_automations_yaml: Path) -> None:
        store = _mock_automation_store()
        store.get_record = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="must be soft-deleted"):
            await async_hard_delete_automation(hass, store, "selora_ai_existing1")

    @pytest.mark.asyncio
    async def test_hard_delete_raises_if_not_in_yaml(
        self, hass, tmp_automations_yaml: Path
    ) -> None:
        store = _mock_automation_store()
        with pytest.raises(ValueError, match="not found in automations.yaml"):
            await async_hard_delete_automation(hass, store, "nonexistent_in_yaml")


class TestAsyncRestoreAutomation:
    """Tests for async_restore_automation."""

    @pytest.mark.asyncio
    async def test_restore_enables(
        self, hass: MagicMock, tmp_automations_yaml: Path, _patch_store: MagicMock
    ) -> None:
        result = await async_restore_automation(hass, "selora_ai_existing1")
        assert result is True
        content = yaml.safe_load(tmp_automations_yaml.read_text(encoding="utf-8"))
        match = [a for a in content if a.get("id") == "selora_ai_existing1"]
        assert match[0]["initial_state"] is True

    @pytest.mark.asyncio
    async def test_restore_calls_reload(
        self, hass: MagicMock, tmp_automations_yaml: Path, _patch_store: MagicMock
    ) -> None:
        await async_restore_automation(hass, "selora_ai_existing1")
        assert ("automation", "reload", {}) in _patch_store._service_calls

    @pytest.mark.asyncio
    async def test_restore_clears_deleted_at(
        self, hass: MagicMock, tmp_automations_yaml: Path, _patch_store: MagicMock
    ) -> None:
        await async_restore_automation(hass, "selora_ai_existing1")
        _patch_store.restore.assert_awaited_once_with("selora_ai_existing1")

    @pytest.mark.asyncio
    async def test_restore_missing_id(
        self, hass: MagicMock, tmp_automations_yaml: Path, _patch_store: MagicMock
    ) -> None:
        result = await async_restore_automation(hass, "nonexistent")
        assert result is False
