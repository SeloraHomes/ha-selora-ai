"""Tests for the fields the save owns rather than the payload.

``apply_managed_fields`` is shared by :func:`async_update_automation` and the
write preview the panel uses, so what the panel shows and what the writer writes
cannot diverge. These pin the decisions themselves.
"""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.selora_ai.automation_utils import apply_managed_fields

SHELL: dict[str, Any] = {"action": "shell_command.rm", "data": {}}


def _benign(**extra: Any) -> dict[str, Any]:
    return {
        "alias": "Lights",
        "triggers": [{"trigger": "state", "entity_id": "binary_sensor.x"}],
        "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.a"}}],
        **extra,
    }


def _elevated(**extra: Any) -> dict[str, Any]:
    return _benign(actions=[SHELL], **extra)


class TestIdIsTheSavesChoice:
    def test_rewrites_a_submitted_id(self) -> None:
        updated = _benign(id="whatever-the-model-said")
        apply_managed_fields(
            _benign(id="real"),
            updated,
            "real",
            preserve_enabled_state=True,
            new_is_elevated=False,
            captured_live=None,
        )
        assert updated["id"] == "real"

    def test_adds_a_missing_id(self) -> None:
        updated = _benign()
        apply_managed_fields(
            _benign(id="real"),
            updated,
            "real",
            preserve_enabled_state=False,
            new_is_elevated=False,
            captured_live=None,
        )
        assert updated["id"] == "real"


class TestPreserveMode:
    @pytest.mark.parametrize("on_disk", [True, False])
    def test_copies_the_on_disk_boot_override(self, on_disk: bool) -> None:
        updated = _benign(initial_state=not on_disk)
        apply_managed_fields(
            _benign(initial_state=on_disk),
            updated,
            "id",
            preserve_enabled_state=True,
            new_is_elevated=False,
            captured_live=None,
        )
        assert updated["initial_state"] is on_disk

    def test_drops_the_key_when_the_file_omits_it(self) -> None:
        updated = _benign(initial_state=True)
        apply_managed_fields(
            _benign(),
            updated,
            "id",
            preserve_enabled_state=True,
            new_is_elevated=False,
            captured_live=None,
        )
        assert "initial_state" not in updated


class TestRiskEscalation:
    def test_forces_disabled_when_risk_rises(self) -> None:
        updated = _elevated()
        escalating, forced = apply_managed_fields(
            _benign(initial_state=True),
            updated,
            "id",
            preserve_enabled_state=True,
            new_is_elevated=True,
            captured_live=True,
        )
        assert (escalating, forced) == (True, True)
        assert updated["initial_state"] is False

    def test_forces_disabled_when_the_runtime_state_is_unknown(self) -> None:
        # No boot override and an indeterminate entity: HA would restore the
        # last runtime state on reload, which may be on.
        updated = _elevated()
        _, forced = apply_managed_fields(
            _benign(),
            updated,
            "id",
            preserve_enabled_state=True,
            new_is_elevated=True,
            captured_live=None,
        )
        assert forced is True
        assert updated["initial_state"] is False

    @pytest.mark.parametrize(
        ("existing", "captured_live"),
        [(_benign(initial_state=False), True), (_benign(), False)],
    )
    def test_leaves_an_already_disabled_automation_alone(
        self, existing: dict[str, Any], captured_live: bool
    ) -> None:
        updated = _elevated()
        _, forced = apply_managed_fields(
            existing,
            updated,
            "id",
            preserve_enabled_state=True,
            new_is_elevated=True,
            captured_live=captured_live,
        )
        assert forced is False

    def test_does_not_fire_when_it_was_already_elevated(self) -> None:
        # The gate covers escalation. An automation the user reviewed and
        # deliberately enabled at elevated risk survives ordinary edits.
        updated = _elevated()
        escalating, forced = apply_managed_fields(
            _elevated(initial_state=True),
            updated,
            "id",
            preserve_enabled_state=True,
            new_is_elevated=True,
            captured_live=True,
        )
        assert (escalating, forced) == (False, False)
        assert updated["initial_state"] is True


class TestExplicitMode:
    def test_honors_a_submitted_value(self) -> None:
        updated = _benign(initial_state=True)
        apply_managed_fields(
            _benign(initial_state=False),
            updated,
            "id",
            preserve_enabled_state=False,
            new_is_elevated=False,
            captured_live=None,
        )
        assert updated["initial_state"] is True

    def test_an_omitted_key_removes_the_override(self) -> None:
        updated = _benign()
        apply_managed_fields(
            _benign(initial_state=True),
            updated,
            "id",
            preserve_enabled_state=False,
            new_is_elevated=False,
            captured_live=None,
        )
        assert "initial_state" not in updated

    def test_never_forces_disabled(self) -> None:
        updated = _elevated(initial_state=True)
        escalating, forced = apply_managed_fields(
            _benign(initial_state=True),
            updated,
            "id",
            preserve_enabled_state=False,
            new_is_elevated=True,
            captured_live=True,
        )
        assert (escalating, forced) == (False, False)
        assert updated["initial_state"] is True
