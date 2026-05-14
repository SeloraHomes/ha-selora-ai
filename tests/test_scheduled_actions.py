"""Tests for ScheduledTaskTracker — delayed action scheduling and cancellation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.selora_ai.scheduled_actions import (
    ScheduledTaskTracker,
    scheduled_time_to_delay,
    validate_delay_seconds,
)

# ── validate_delay_seconds ────────────────────────────────────────────


class TestValidateDelaySeconds:
    @pytest.mark.parametrize("value", [600, 30.5, 86400])
    def test_valid_values_accepted(self, value: int | float) -> None:
        ok, reason = validate_delay_seconds(value)
        assert ok is True
        assert reason == ""

    @pytest.mark.parametrize(
        ("value", "expected_substr"),
        [
            (0, "positive"),
            (-10, "positive"),
            (86401, "24 hours"),
            ("ten", "number"),
            (True, "boolean"),
        ],
    )
    def test_invalid_values_rejected(self, value: Any, expected_substr: str) -> None:
        ok, reason = validate_delay_seconds(value)
        assert ok is False
        assert expected_substr in reason.lower()


# ── ScheduledTaskTracker ──────────────────────────────────────────────


class TestScheduledTaskTracker:
    @pytest.mark.asyncio
    async def test_schedule_delayed_creates_task(self, hass: MagicMock) -> None:
        tracker = ScheduledTaskTracker(hass)
        calls = [{"service": "light.turn_on", "target": {"entity_id": "light.porch"}}]

        with patch(
            "custom_components.selora_ai.scheduled_actions.async_call_later"
        ) as mock_call_later:
            mock_call_later.return_value = MagicMock()  # cancel callback
            task = await tracker.schedule_delayed(
                "session_1", calls, 600, "Turn on porch in 10 min"
            )

        assert task.schedule_id.startswith("sched_")
        assert task.status == "pending"
        assert task.session_id == "session_1"
        assert task.schedule_type == "delay"
        mock_call_later.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_pending_task(self, hass: MagicMock) -> None:
        tracker = ScheduledTaskTracker(hass)
        cancel_fn = MagicMock()

        with patch(
            "custom_components.selora_ai.scheduled_actions.async_call_later",
            return_value=cancel_fn,
        ):
            task = await tracker.schedule_delayed(
                "session_1",
                [{"service": "light.turn_on", "target": {"entity_id": "light.porch"}}],
                600,
                "Porch light in 10 min",
            )

        result = tracker.cancel_task(task.schedule_id)
        assert result is True
        assert task.status == "cancelled"
        cancel_fn.assert_called_once()

    def test_cancel_nonexistent_returns_false(self, hass: MagicMock) -> None:
        tracker = ScheduledTaskTracker(hass)
        assert tracker.cancel_task("nonexistent") is False

    @pytest.mark.asyncio
    async def test_get_latest_pending(self, hass: MagicMock) -> None:
        tracker = ScheduledTaskTracker(hass)
        with patch(
            "custom_components.selora_ai.scheduled_actions.async_call_later",
            return_value=MagicMock(),
        ):
            await tracker.schedule_delayed("session_1", [{"service": "light.turn_on"}], 60, "First")
            task2 = await tracker.schedule_delayed(
                "session_1", [{"service": "light.turn_off"}], 120, "Second"
            )

        latest = tracker.get_latest_pending("session_1")
        assert latest is not None
        assert latest.schedule_id == task2.schedule_id

    @pytest.mark.asyncio
    async def test_get_latest_pending_wrong_session(self, hass: MagicMock) -> None:
        tracker = ScheduledTaskTracker(hass)
        with patch(
            "custom_components.selora_ai.scheduled_actions.async_call_later",
            return_value=MagicMock(),
        ):
            await tracker.schedule_delayed("session_1", [{"service": "light.turn_on"}], 60, "Task")

        assert tracker.get_latest_pending("session_2") is None

    @pytest.mark.asyncio
    async def test_cancel_does_not_cancel_twice(self, hass: MagicMock) -> None:
        tracker = ScheduledTaskTracker(hass)
        with patch(
            "custom_components.selora_ai.scheduled_actions.async_call_later",
            return_value=MagicMock(),
        ):
            task = await tracker.schedule_delayed(
                "session_1", [{"service": "light.turn_on"}], 60, "Task"
            )

        assert tracker.cancel_task(task.schedule_id) is True
        assert tracker.cancel_task(task.schedule_id) is False  # already cancelled

    @pytest.mark.asyncio
    async def test_to_dict_serialization(self, hass: MagicMock) -> None:
        tracker = ScheduledTaskTracker(hass)
        with patch(
            "custom_components.selora_ai.scheduled_actions.async_call_later",
            return_value=MagicMock(),
        ):
            task = await tracker.schedule_delayed(
                "session_1", [{"service": "light.turn_on"}], 60, "Task"
            )

        d = task.to_dict()
        assert d["schedule_id"] == task.schedule_id
        assert d["status"] == "pending"
        assert d["schedule_type"] == "delay"
        assert "created_at" in d
        assert "fires_at" in d

    @pytest.mark.asyncio
    async def test_cancel_all_pending(self, hass: MagicMock) -> None:
        tracker = ScheduledTaskTracker(hass)
        cancel_fns = [MagicMock(), MagicMock()]

        with patch(
            "custom_components.selora_ai.scheduled_actions.async_call_later",
            side_effect=cancel_fns,
        ):
            await tracker.schedule_delayed("s1", [{"service": "light.turn_on"}], 60, "First")
            task2 = await tracker.schedule_delayed(
                "s1", [{"service": "light.turn_off"}], 120, "Second"
            )

        # Cancel one manually so only 1 remains pending
        tracker.cancel_task(task2.schedule_id)
        count = tracker.cancel_all_pending()
        assert count == 1
        # All cancel callbacks should have been invoked
        cancel_fns[0].assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_all_pending_clears_tasks(self, hass: MagicMock) -> None:
        tracker = ScheduledTaskTracker(hass)
        with patch(
            "custom_components.selora_ai.scheduled_actions.async_call_later",
            return_value=MagicMock(),
        ):
            await tracker.schedule_delayed("s1", [{"service": "light.turn_on"}], 60, "Task")

        tracker.cancel_all_pending()
        assert tracker.get_all_tasks() == []

    @pytest.mark.asyncio
    async def test_pending_cap_rejects_when_full(self, hass: MagicMock) -> None:
        tracker = ScheduledTaskTracker(hass)
        with patch(
            "custom_components.selora_ai.scheduled_actions.async_call_later",
            return_value=MagicMock(),
        ), patch(
            "custom_components.selora_ai.scheduled_actions._MAX_PENDING_TASKS", 2,
        ):
            await tracker.schedule_delayed("s1", [{"service": "light.turn_on"}], 60, "First")
            await tracker.schedule_delayed("s1", [{"service": "light.turn_on"}], 60, "Second")
            with pytest.raises(RuntimeError, match="Too many pending"):
                await tracker.schedule_delayed(
                    "s1", [{"service": "light.turn_on"}], 60, "Third"
                )

    @pytest.mark.asyncio
    async def test_schedule_at_time_creates_automation(self, hass: MagicMock) -> None:
        tracker = ScheduledTaskTracker(hass)
        calls = [{"service": "light.turn_on", "target": {"entity_id": "light.porch"}}]

        with patch(
            "custom_components.selora_ai.automation_utils.async_create_automation",
            return_value={"success": True, "automation_id": "selora_ai_abc123"},
        ) as mock_create, patch(
            "custom_components.selora_ai.scheduled_actions.async_track_point_in_utc_time",
            return_value=MagicMock(),
        ):
            task = await tracker.schedule_at_time(
                "session_1", calls, "23:00:00", "Porch light at 11 PM"
            )

        assert task.schedule_id.startswith("sched_")
        assert task.status == "pending"
        assert task.schedule_type == "automation"
        assert task.automation_id == "selora_ai_abc123"
        mock_create.assert_called_once()
        # Verify the automation has a time trigger
        created = mock_create.call_args[0][1]
        assert created["triggers"][0]["platform"] == "time"
        assert created["triggers"][0]["at"] == "23:00:00"
        # One-shot scheduled actions must be created enabled — async_create_automation
        # now takes that decision via the explicit enabled=True kwarg, not a
        # smuggled initial_state field on the suggestion.
        assert mock_create.call_args.kwargs.get("enabled") is True
        assert "initial_state" not in created
        # Verify one-shot date condition is present and uses local date
        assert len(created["conditions"]) == 1
        cond = created["conditions"][0]
        assert cond["condition"] == "template"
        assert "%Y-%m-%d" in cond["value_template"]

    @pytest.mark.asyncio
    async def test_schedule_at_time_rejects_forced_disabled(self, hass: MagicMock) -> None:
        """If async_create_automation force-disables an elevated-risk action,
        the scheduler must roll back and raise — a disabled one-shot would
        never fire and silently swallow the user's request."""
        tracker = ScheduledTaskTracker(hass)
        calls = [{"service": "script.evening_routine"}]

        with (
            patch(
                "custom_components.selora_ai.automation_utils.async_create_automation",
                return_value={
                    "success": True,
                    "automation_id": "selora_ai_risky01",
                    "risk_level": "elevated",
                    "forced_disabled": True,
                },
            ) as mock_create,
            patch(
                "custom_components.selora_ai.automation_utils.async_delete_automation",
                return_value=True,
            ) as mock_delete,
            pytest.raises(RuntimeError, match="elevated-risk"),
        ):
            await tracker.schedule_at_time(
                "session_1", calls, "23:00:00", "Evening routine"
            )

        mock_create.assert_called_once()
        # Zombie automation must be cleaned up so we don't leave a disabled
        # one-shot in automations.yaml.
        mock_delete.assert_called_once_with(hass, "selora_ai_risky01")
        # And the scheduler must not have registered the task.
        assert tracker._tasks == {}

    @pytest.mark.asyncio
    async def test_schedule_at_time_uses_local_date(self, hass: MagicMock) -> None:
        """Fire date must use HA local timezone, not UTC."""
        # 2026-04-25 23:00 EDT = 2026-04-26 03:00 UTC.
        # If computed in UTC, the date would be April 26 — wrong for the
        # template condition which compares against HA's now() (local).
        eastern = ZoneInfo("America/New_York")
        fake_now_local = datetime(2026, 4, 25, 20, 0, 0, tzinfo=eastern)

        tracker = ScheduledTaskTracker(hass)
        with patch(
            "custom_components.selora_ai.automation_utils.async_create_automation",
            return_value={"success": True, "automation_id": "selora_ai_tz"},
        ) as mock_create, patch(
            "custom_components.selora_ai.scheduled_actions.dt_util.get_default_time_zone",
            return_value=eastern,
        ), patch(
            "custom_components.selora_ai.scheduled_actions.datetime",
        ) as mock_dt, patch(
            "custom_components.selora_ai.scheduled_actions.async_track_point_in_utc_time",
            return_value=MagicMock(),
        ):
            mock_dt.now.return_value = fake_now_local
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await tracker.schedule_at_time(
                "s1", [{"service": "light.turn_on"}], "23:00:00", "Test"
            )

        created = mock_create.call_args[0][1]
        cond = created["conditions"][0]["value_template"]
        # Must contain local date (April 25), not UTC date (April 26)
        assert "2026-04-25" in cond

    @pytest.mark.asyncio
    async def test_schedule_at_time_marks_executed_after_fire(
        self, hass: MagicMock
    ) -> None:
        tracker = ScheduledTaskTracker(hass)
        track_callbacks: list[Any] = []

        def capture_track(hass: Any, cb: Any, when: Any) -> MagicMock:
            track_callbacks.append(cb)
            return MagicMock()

        with patch(
            "custom_components.selora_ai.automation_utils.async_create_automation",
            return_value={"success": True, "automation_id": "selora_ai_mark"},
        ), patch(
            "custom_components.selora_ai.scheduled_actions.async_track_point_in_utc_time",
            side_effect=capture_track,
        ):
            task = await tracker.schedule_at_time(
                "s1", [{"service": "light.turn_on"}], "23:00:00", "Task"
            )

        assert task.status == "pending"
        # Simulate the post-fire callback
        assert len(track_callbacks) == 1
        track_callbacks[0](datetime.now(UTC))
        assert task.status == "executed"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_time", ["noon", "23", "25:00:00", ""])
    async def test_schedule_at_time_rejects_invalid_time(
        self, hass: MagicMock, bad_time: str
    ) -> None:
        tracker = ScheduledTaskTracker(hass)
        with pytest.raises(RuntimeError, match="Invalid scheduled time"):
            await tracker.schedule_at_time(
                "s1", [{"service": "light.turn_on"}], bad_time, "Bad time"
            )

    @pytest.mark.asyncio
    async def test_schedule_at_time_raises_on_failure(self, hass: MagicMock) -> None:
        tracker = ScheduledTaskTracker(hass)
        with patch(
            "custom_components.selora_ai.automation_utils.async_create_automation",
            return_value={"success": False, "automation_id": None},
        ):
            with pytest.raises(RuntimeError, match="Failed to create"):
                await tracker.schedule_at_time(
                    "s1", [{"service": "light.turn_on"}], "23:00:00", "Task"
                )

    @pytest.mark.asyncio
    async def test_async_cancel_task_deletes_automation(self, hass: MagicMock) -> None:
        tracker = ScheduledTaskTracker(hass)
        with patch(
            "custom_components.selora_ai.automation_utils.async_create_automation",
            return_value={"success": True, "automation_id": "selora_ai_xyz"},
        ):
            task = await tracker.schedule_at_time(
                "s1", [{"service": "light.turn_on"}], "23:00:00", "Task"
            )

        with patch(
            "custom_components.selora_ai.automation_utils.async_delete_automation",
            return_value=True,
        ) as mock_delete:
            result = await tracker.async_cancel_task(task.schedule_id)

        assert result is True
        assert task.status == "cancelled"
        mock_delete.assert_called_once_with(hass, "selora_ai_xyz")

    @pytest.mark.asyncio
    async def test_async_cancel_task_stays_pending_on_delete_failure(
        self, hass: MagicMock
    ) -> None:
        tracker = ScheduledTaskTracker(hass)
        with patch(
            "custom_components.selora_ai.automation_utils.async_create_automation",
            return_value={"success": True, "automation_id": "selora_ai_fail"},
        ), patch(
            "custom_components.selora_ai.scheduled_actions.async_track_point_in_utc_time",
            return_value=MagicMock(),
        ):
            task = await tracker.schedule_at_time(
                "s1", [{"service": "light.turn_on"}], "23:00:00", "Task"
            )

        with patch(
            "custom_components.selora_ai.automation_utils.async_delete_automation",
            return_value=False,
        ):
            result = await tracker.async_cancel_task(task.schedule_id)

        assert result is False
        assert task.status == "pending"

    @pytest.mark.asyncio
    async def test_async_cancel_task_stays_pending_on_delete_exception(
        self, hass: MagicMock
    ) -> None:
        tracker = ScheduledTaskTracker(hass)
        with patch(
            "custom_components.selora_ai.automation_utils.async_create_automation",
            return_value={"success": True, "automation_id": "selora_ai_exc"},
        ), patch(
            "custom_components.selora_ai.scheduled_actions.async_track_point_in_utc_time",
            return_value=MagicMock(),
        ):
            task = await tracker.schedule_at_time(
                "s1", [{"service": "light.turn_on"}], "23:00:00", "Task"
            )

        with patch(
            "custom_components.selora_ai.automation_utils.async_delete_automation",
            side_effect=OSError("disk full"),
        ):
            result = await tracker.async_cancel_task(task.schedule_id)

        assert result is False
        assert task.status == "pending"

    @pytest.mark.asyncio
    async def test_cleanup_stale_automations_removes_past_date(
        self, hass: MagicMock
    ) -> None:
        tracker = ScheduledTaskTracker(hass)
        automations = [
            {
                "id": "selora_ai_stale1",
                "alias": "Scheduled: Porch light",
                "description": "[Selora AI] One-shot scheduled action (sched_abc) [session:s1]",
                "triggers": [{"platform": "time", "at": "11:00:00"}],
                "conditions": [
                    {
                        "condition": "template",
                        "value_template": "{{ now().strftime('%Y-%m-%d') == '2025-01-01' }}",
                    },
                ],
                "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}],
            },
            {
                "id": "selora_ai_current",
                "alias": "Scheduled: Bedroom light",
                "description": "[Selora AI] One-shot scheduled action (sched_def) [session:s2]",
                "triggers": [{"platform": "time", "at": "23:00:00"}],
                "conditions": [
                    {
                        "condition": "template",
                        "value_template": "{{ now().strftime('%Y-%m-%d') == '2099-12-31' }}",
                    },
                ],
                "actions": [{"action": "light.turn_off"}],
            },
            {
                "id": "selora_ai_regular",
                "alias": "Sunset lights",
                "description": "[Selora AI] Turn on lights at sunset",
            },
        ]

        async def fake_executor_job(func: Any, *args: Any) -> Any:
            return automations

        hass.async_add_executor_job = fake_executor_job

        with patch(
            "custom_components.selora_ai.automation_utils.async_delete_automation",
        ) as mock_delete, patch(
            "custom_components.selora_ai.scheduled_actions.async_track_point_in_utc_time",
            return_value=MagicMock(),
        ):
            removed = await tracker.async_cleanup_stale_automations()

        assert removed == 1
        mock_delete.assert_called_once_with(hass, "selora_ai_stale1")

    @pytest.mark.asyncio
    async def test_cleanup_restores_pending_automations(
        self, hass: MagicMock
    ) -> None:
        tracker = ScheduledTaskTracker(hass)
        automations = [
            {
                "id": "selora_ai_future",
                "alias": "Scheduled: Porch light",
                "description": "[Selora AI] One-shot scheduled action (sched_abc) [session:sess_42]",
                "triggers": [{"platform": "time", "at": "23:00:00"}],
                "conditions": [
                    {
                        "condition": "template",
                        "value_template": "{{ now().strftime('%Y-%m-%d') == '2099-12-31' }}",
                    },
                ],
                "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.porch"}}],
            },
        ]

        async def fake_executor_job(func: Any, *args: Any) -> Any:
            return automations

        hass.async_add_executor_job = fake_executor_job

        with patch(
            "custom_components.selora_ai.automation_utils.async_delete_automation",
        ), patch(
            "custom_components.selora_ai.scheduled_actions.async_track_point_in_utc_time",
            return_value=MagicMock(),
        ):
            await tracker.async_cleanup_stale_automations()

        # The pending automation should be restored into _tasks
        pending = tracker.get_pending_tasks("sess_42")
        assert len(pending) == 1
        assert pending[0].schedule_id == "sched_abc"
        assert pending[0].automation_id == "selora_ai_future"
        assert pending[0].session_id == "sess_42"

    @pytest.mark.asyncio
    async def test_cleanup_removes_same_day_past_time(
        self, hass: MagicMock
    ) -> None:
        """Same-day automation whose trigger time has passed should be cleaned up."""
        tracker = ScheduledTaskTracker(hass)
        eastern = ZoneInfo("America/New_York")
        # "Now" is 2026-04-25 14:00 local, automation was for 11:00 same day
        fake_now = datetime(2026, 4, 25, 14, 0, 0, tzinfo=eastern)
        automations = [
            {
                "id": "selora_ai_missed",
                "alias": "Scheduled: Morning light",
                "description": "[Selora AI] One-shot scheduled action (sched_xyz) [session:s1]",
                "triggers": [{"platform": "time", "at": "11:00:00"}],
                "conditions": [
                    {
                        "condition": "template",
                        "value_template": "{{ now().strftime('%Y-%m-%d') == '2026-04-25' }}",
                    },
                ],
                "actions": [{"action": "light.turn_on"}],
            },
        ]

        async def fake_executor_job(func: Any, *args: Any) -> Any:
            return automations

        hass.async_add_executor_job = fake_executor_job

        with patch(
            "custom_components.selora_ai.automation_utils.async_delete_automation",
        ) as mock_delete, patch(
            "custom_components.selora_ai.scheduled_actions.datetime",
        ) as mock_dt, patch(
            "custom_components.selora_ai.scheduled_actions.dt_util.get_default_time_zone",
            return_value=eastern,
        ):
            mock_dt.now.return_value = fake_now
            mock_dt.strptime = datetime.strptime
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            await tracker.async_cleanup_stale_automations()

        mock_delete.assert_called_once_with(hass, "selora_ai_missed")
        assert tracker.get_pending_tasks() == []


# ── scheduled_time_to_delay ──────────────────────────────────────────


class TestScheduledTimeToDelay:
    def test_future_time_today(self) -> None:
        # Mock "now" to 10:00:00 UTC, schedule for 14:00:00 → 4h = 14400s
        fake_now = datetime(2026, 4, 25, 10, 0, 0, tzinfo=UTC)
        with patch(
            "custom_components.selora_ai.scheduled_actions.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            ok, delay, reason = scheduled_time_to_delay("14:00:00")
        assert ok is True
        assert delay == 14400
        assert reason == ""

    def test_past_time_wraps_to_tomorrow(self) -> None:
        # Mock "now" to 22:00:00 UTC, schedule for 06:00:00 → wraps to tomorrow
        fake_now = datetime(2026, 4, 25, 22, 0, 0, tzinfo=UTC)
        with patch(
            "custom_components.selora_ai.scheduled_actions.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            ok, delay, reason = scheduled_time_to_delay("06:00:00")
        assert ok is True
        assert delay == 28800  # 8 hours

    def test_hh_mm_format_accepted(self) -> None:
        fake_now = datetime(2026, 4, 25, 10, 0, 0, tzinfo=UTC)
        with patch(
            "custom_components.selora_ai.scheduled_actions.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            ok, delay, _ = scheduled_time_to_delay("12:30")
        assert ok is True
        assert delay == 9000  # 2h30m

    def test_local_timezone_respected(self) -> None:
        """'23:00:00' in America/New_York should differ from UTC interpretation."""
        # 2026-04-25 is EDT (UTC-4). Now is 20:00 UTC = 16:00 EDT.
        # "23:00:00" local = 23:00 EDT = 03:00 UTC next day.
        # Delay from 16:00 EDT to 23:00 EDT = 7h = 25200s.
        eastern = ZoneInfo("America/New_York")
        fake_now = datetime(2026, 4, 25, 20, 0, 0, tzinfo=UTC).astimezone(eastern)
        with patch(
            "custom_components.selora_ai.scheduled_actions.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            ok, delay, _ = scheduled_time_to_delay("23:00:00", local_tz=eastern)
        assert ok is True
        assert delay == 25200

    @pytest.mark.parametrize(
        ("value", "expected_substr"),
        [
            ("noon", "HH:MM"),
            (1400, "string"),
            ("25:00:00", "out-of-range"),
            ("14", "HH:MM"),
        ],
    )
    def test_invalid_values_rejected(self, value: Any, expected_substr: str) -> None:
        ok, _, reason = scheduled_time_to_delay(value)
        assert ok is False
        assert expected_substr in reason
