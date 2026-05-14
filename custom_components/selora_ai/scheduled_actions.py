"""ScheduledTaskTracker — manages delayed and scheduled device actions.

Supports two scheduling modes:
  1. Relative delay — "in 10 minutes" — uses HA's async_call_later (in-memory).
  2. Absolute time — "at 11 PM" — creates a one-shot automation (persisted).

Tasks are tracked per chat session so "cancel that" resolves to the most
recent pending task in the current conversation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo
import logging
import re
from typing import Any
import uuid

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

_MAX_DELAY_SECONDS = 86400  # 24 hours
_MAX_PENDING_TASKS = 50  # per-instance cap to prevent unbounded growth


class ScheduledTask:
    """A single scheduled action."""

    def __init__(
        self,
        schedule_id: str,
        session_id: str,
        description: str,
        calls: list[dict[str, Any]],
        fires_at: datetime,
        schedule_type: str,
        cancel_fn: CALLBACK_TYPE | None = None,
        automation_id: str | None = None,
    ) -> None:
        self.schedule_id = schedule_id
        self.session_id = session_id
        self.description = description
        self.calls = calls
        self.created_at = datetime.now(UTC)
        self.fires_at = fires_at
        self.schedule_type = schedule_type  # "delay" or "automation"
        self.status = "pending"
        self._cancel_fn = cancel_fn
        self.automation_id = automation_id

    def cancel(self) -> bool:
        """Cancel this task (sync). Returns True if it was pending.

        For in-memory timers this cancels immediately.  For automation-backed
        tasks, call ``async_cancel`` instead to also delete the automation.
        """
        if self.status != "pending":
            return False
        if self._cancel_fn is not None:
            self._cancel_fn()
        self.status = "cancelled"
        return True

    def to_dict(self) -> dict[str, Any]:
        """Serialize for websocket/frontend consumption."""
        return {
            "schedule_id": self.schedule_id,
            "session_id": self.session_id,
            "description": self.description,
            "calls": self.calls,
            "created_at": self.created_at.isoformat(),
            "fires_at": self.fires_at.isoformat(),
            "schedule_type": self.schedule_type,
            "status": self.status,
        }


def validate_delay_seconds(seconds: int | float) -> tuple[bool, str]:
    """Validate a relative delay value."""
    if isinstance(seconds, bool):
        return False, "delay_seconds must be a number, not a boolean"
    if not isinstance(seconds, (int, float)):
        return False, "delay_seconds must be a number"
    if seconds <= 0:
        return False, "delay must be positive"
    if seconds > _MAX_DELAY_SECONDS:
        return False, f"delay cannot exceed {_MAX_DELAY_SECONDS} seconds (24 hours)"
    return True, ""


def scheduled_time_to_delay(
    time_str: str,
    local_tz: tzinfo | None = None,
) -> tuple[bool, int, str]:
    """Convert an HH:MM:SS time string to seconds-from-now.

    *local_tz* is the user/HA-configured timezone.  When provided the
    time string is interpreted in that timezone (e.g. "23:00:00" means
    11 PM local).  Falls back to UTC when *local_tz* is ``None``.

    If the time is earlier than now, it is assumed to mean tomorrow.
    Returns (ok, delay_seconds, reason).
    """
    if not isinstance(time_str, str):
        return False, 0, "scheduled_time must be a string"
    try:
        parts = time_str.strip().split(":")
        if len(parts) < 2 or len(parts) > 3:
            return False, 0, "scheduled_time must be HH:MM or HH:MM:SS"
        hour, minute = int(parts[0]), int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
            return False, 0, "scheduled_time contains out-of-range values"
    except ValueError:
        return False, 0, "scheduled_time must be HH:MM:SS format"

    tz = local_tz or UTC
    now_local = datetime.now(tz)
    target = now_local.replace(hour=hour, minute=minute, second=second, microsecond=0)
    if target <= now_local:
        target += timedelta(days=1)

    delay = int((target - now_local).total_seconds())
    if delay > _MAX_DELAY_SECONDS:
        return False, 0, f"scheduled time is more than {_MAX_DELAY_SECONDS} seconds away (24 hours)"
    return True, delay, ""


class ScheduledTaskTracker:
    """Tracks pending delayed/scheduled actions across chat sessions."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._tasks: dict[str, ScheduledTask] = {}

    def pending_count(self) -> int:
        """Return the number of currently pending tasks."""
        return sum(1 for t in self._tasks.values() if t.status == "pending")

    async def schedule_delayed(
        self,
        session_id: str,
        calls: list[dict[str, Any]],
        delay_seconds: int | float,
        description: str,
    ) -> ScheduledTask:
        """Schedule service calls after a relative delay.

        Uses HA's async_call_later which is an in-memory timer.
        Does not survive HA restarts.

        Raises ``RuntimeError`` when the pending task cap is reached.
        """
        self._enforce_pending_cap()
        schedule_id = f"sched_{uuid.uuid4().hex[:12]}"
        fires_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)

        @callback
        def _on_timer_fire(_now: datetime) -> None:
            """Execute the service calls when the timer fires."""
            task = self._tasks.get(schedule_id)
            if not task or task.status != "pending":
                return
            _LOGGER.info("Executing delayed action %s: %s", schedule_id, description)

            async def _execute_calls() -> None:
                had_failure = False
                for call in calls:
                    service = call.get("service", "")
                    if not service or "." not in service:
                        continue
                    domain_part, service_name = service.split(".", 1)
                    target = call.get("target", {})
                    data = call.get("data", {})
                    try:
                        await self._hass.services.async_call(
                            domain_part, service_name, {**data, **target}, blocking=False
                        )
                    except Exception:  # noqa: BLE001 — third-party service handlers may raise beyond HA's hierarchy
                        _LOGGER.exception(
                            "Delayed action %s: failed to call %s", schedule_id, service
                        )
                        had_failure = True
                task.status = "partial_failure" if had_failure else "executed"

            self._hass.async_create_task(_execute_calls(), f"selora_ai_delayed_{schedule_id}")

        cancel_fn = async_call_later(self._hass, delay_seconds, _on_timer_fire)

        task = ScheduledTask(
            schedule_id=schedule_id,
            session_id=session_id,
            description=description,
            calls=calls,
            fires_at=fires_at,
            schedule_type="delay",
            cancel_fn=cancel_fn,
        )
        self._tasks[schedule_id] = task
        self._cleanup_old_tasks()

        _LOGGER.info(
            "Scheduled delayed action %s: %s (fires in %ds)",
            schedule_id,
            description,
            delay_seconds,
        )
        return task

    async def schedule_at_time(
        self,
        session_id: str,
        calls: list[dict[str, Any]],
        scheduled_time: str,
        description: str,
    ) -> ScheduledTask:
        """Schedule service calls at an absolute time via a persisted automation.

        Creates a one-shot ``[Selora AI]`` automation with a time trigger so the
        action survives HA restarts.  The automation is created *enabled* and
        fires once at the given HH:MM:SS.

        Raises ``RuntimeError`` when the pending task cap is reached or
        automation creation fails.
        """
        from .automation_utils import async_create_automation, async_delete_automation

        self._enforce_pending_cap()

        # Validate the time string before persisting anything — a malformed
        # value like "noon" or "23" would leave behind a broken automation.
        ok, _delay, reason = scheduled_time_to_delay(scheduled_time)
        if not ok:
            raise RuntimeError(f"Invalid scheduled time: {reason}")

        schedule_id = f"sched_{uuid.uuid4().hex[:12]}"

        actions: list[dict[str, Any]] = []
        for call in calls:
            service = call.get("service", "")
            if not service:
                continue
            action: dict[str, Any] = {"action": service}
            target = call.get("target")
            if target:
                action["target"] = target
            data = call.get("data")
            if data:
                action["data"] = data
            actions.append(action)

        if not actions:
            raise RuntimeError("No valid service calls to schedule")

        # Compute the fire date in HA's local timezone so the template
        # condition (which uses HA's now()) matches the trigger date.
        parts = scheduled_time.strip().split(":")
        hour, minute = int(parts[0]), int(parts[1])
        second = int(parts[2]) if len(parts) > 2 else 0
        local_tz = dt_util.get_default_time_zone()
        now_local = datetime.now(local_tz)
        fires_at_local = now_local.replace(hour=hour, minute=minute, second=second, microsecond=0)
        if fires_at_local <= now_local:
            fires_at_local += timedelta(days=1)
        fire_date = fires_at_local.strftime("%Y-%m-%d")
        fires_at_utc = fires_at_local.astimezone(UTC)

        automation_data: dict[str, Any] = {
            "alias": f"Scheduled: {description[:40]}",
            "description": (f"One-shot scheduled action ({schedule_id}) [session:{session_id}]"),
            "triggers": [{"platform": "time", "at": scheduled_time}],
            # Only fire on the intended date so this behaves as one-shot.
            "conditions": [
                {
                    "condition": "template",
                    "value_template": "{{ now().strftime('%Y-%m-%d') == '" + fire_date + "' }}",
                },
            ],
            "actions": actions,
            "mode": "single",
        }

        # One-shot scheduled actions must be active so the time trigger fires.
        result = await async_create_automation(
            self._hass,
            automation_data,
            version_message=f"Scheduled action {schedule_id}",
            enabled=True,
        )

        if not result.get("success"):
            raise RuntimeError("Failed to create scheduled automation")

        automation_id: str = result["automation_id"] or ""

        # Risk gate inside async_create_automation may have forced the
        # automation disabled. A disabled one-shot automation will never
        # fire, so reporting the schedule as pending would silently swallow
        # the action. Roll back the automation and surface a clear error to
        # the caller so it can ask the user to use a less risky action or
        # enable a manually-reviewed automation instead.
        if result.get("forced_disabled"):
            if automation_id:
                await async_delete_automation(self._hass, automation_id)
            risk_flags = ", ".join(
                action.get("action", "") for action in actions if action.get("action")
            )
            raise RuntimeError(
                "Scheduled action uses elevated-risk services "
                f"({risk_flags}); refusing to schedule a disabled "
                "automation that would never fire. Ask the user to "
                "review and enable manually, or pick a safer service."
            )

        task = ScheduledTask(
            schedule_id=schedule_id,
            session_id=session_id,
            description=description,
            calls=calls,
            fires_at=fires_at_utc,
            schedule_type="automation",
            automation_id=automation_id,
        )
        self._tasks[schedule_id] = task

        # Schedule a callback shortly after the fire time to mark the task
        # as executed and clean up the one-shot automation.
        mark_time = fires_at_utc + timedelta(seconds=60)

        @callback
        def _mark_executed(_now: datetime) -> None:
            if task.status == "pending":
                task.status = "executed"
                _LOGGER.info("Marked scheduled task %s as executed", schedule_id)
                self._hass.async_create_task(
                    self._cleanup_automation(automation_id),
                    f"selora_ai_cleanup_{schedule_id}",
                )

        task._cancel_fn = async_track_point_in_utc_time(self._hass, _mark_executed, mark_time)

        _LOGGER.info(
            "Scheduled automation %s (%s): %s at %s",
            schedule_id,
            automation_id,
            description,
            scheduled_time,
        )
        return task

    async def _cleanup_automation(self, automation_id: str) -> None:
        """Delete a one-shot scheduled automation after it fires."""
        from .automation_utils import async_delete_automation

        try:
            await async_delete_automation(self._hass, automation_id)
        except Exception:  # noqa: BLE001 — best-effort cleanup
            _LOGGER.warning("Failed to clean up automation %s", automation_id)

    async def async_cleanup_stale_automations(self) -> int:
        """Clean up stale one-shot automations and restore pending ones.

        Called on integration startup.  For each one-shot scheduled
        automation in ``automations.yaml``:

        - If the fire datetime has passed → delete (stale).
        - If the fire datetime is still in the future → restore into
          ``_tasks`` so "cancel that" works after a restart.

        Returns the number of stale automations removed.
        """
        from pathlib import Path

        from .automation_utils import _read_automations_yaml, async_delete_automation

        automations_path = Path(self._hass.config.config_dir) / "automations.yaml"
        existing = await self._hass.async_add_executor_job(_read_automations_yaml, automations_path)

        local_tz = dt_util.get_default_time_zone()
        now_local = datetime.now(local_tz)

        stale_ids: list[str] = []
        for auto in existing:
            desc = auto.get("description", "")
            if "One-shot scheduled action" not in desc:
                continue
            auto_id = auto.get("id", "")
            if not auto_id:
                continue

            # Extract fire date from the template condition
            fire_date_str: str | None = None
            for cond in auto.get("conditions", []):
                tmpl = cond.get("value_template", "")
                date_match = re.search(r"== '(\d{4}-\d{2}-\d{2})'", tmpl)
                if date_match:
                    fire_date_str = date_match.group(1)
                    break
            if not fire_date_str:
                continue

            # Extract trigger time (HH:MM:SS) from the time trigger
            trigger_time_str: str | None = None
            for trig in auto.get("triggers", []):
                if trig.get("platform") == "time":
                    trigger_time_str = trig.get("at", "")
                    break

            # Build the full fire datetime in local timezone
            try:
                parts = (trigger_time_str or "00:00:00").split(":")
                hour, minute = int(parts[0]), int(parts[1])
                second = int(parts[2]) if len(parts) > 2 else 0
                fire_dt = datetime.strptime(fire_date_str, "%Y-%m-%d").replace(
                    hour=hour, minute=minute, second=second, tzinfo=local_tz
                )
            except ValueError, IndexError:
                # Can't parse — treat as stale to be safe
                stale_ids.append(auto_id)
                continue

            if fire_dt <= now_local:
                # Fire time has passed — stale
                stale_ids.append(auto_id)
            else:
                # Still pending — restore into _tasks for cancellation
                self._restore_task_from_automation(auto, auto_id, fire_dt)

        removed = 0
        for aid in stale_ids:
            try:
                await async_delete_automation(self._hass, aid)
                removed += 1
            except Exception:  # noqa: BLE001 — best-effort cleanup
                _LOGGER.warning("Failed to remove stale scheduled automation %s", aid)

        if removed:
            _LOGGER.info("Cleaned up %d stale one-shot scheduled automations", removed)
        if self._tasks:
            _LOGGER.info("Restored %d pending scheduled tasks from automations", len(self._tasks))
        return removed

    def _restore_task_from_automation(
        self,
        auto: dict[str, Any],
        automation_id: str,
        fire_dt: datetime,
    ) -> None:
        """Rebuild a ScheduledTask from a persisted one-shot automation."""
        desc = auto.get("description", "")

        # Extract schedule_id: "One-shot scheduled action (sched_xxxx)"
        sid_match = re.search(r"\((sched_\w+)\)", desc)
        schedule_id = sid_match.group(1) if sid_match else f"sched_restored_{automation_id}"

        # Extract session_id: "[session:yyyy]"
        sess_match = re.search(r"\[session:([^\]]+)\]", desc)
        session_id = sess_match.group(1) if sess_match else ""

        # Rebuild calls from the automation actions
        calls: list[dict[str, Any]] = []
        for action in auto.get("actions", []):
            service = action.get("action", "")
            if not service:
                continue
            call: dict[str, Any] = {"service": service}
            if action.get("target"):
                call["target"] = action["target"]
            if action.get("data"):
                call["data"] = action["data"]
            calls.append(call)

        alias = auto.get("alias", "")
        task_desc = alias.removeprefix("Scheduled: ") if alias.startswith("Scheduled: ") else alias

        fires_at_utc = fire_dt.astimezone(UTC)

        task = ScheduledTask(
            schedule_id=schedule_id,
            session_id=session_id,
            description=task_desc,
            calls=calls,
            fires_at=fires_at_utc,
            schedule_type="automation",
            automation_id=automation_id,
        )
        self._tasks[schedule_id] = task

        # Re-register the post-fire cleanup callback
        mark_time = fires_at_utc + timedelta(seconds=60)

        @callback
        def _mark_executed(_now: datetime) -> None:
            if task.status == "pending":
                task.status = "executed"
                _LOGGER.info("Marked restored task %s as executed", schedule_id)
                self._hass.async_create_task(
                    self._cleanup_automation(automation_id),
                    f"selora_ai_cleanup_{schedule_id}",
                )

        task._cancel_fn = async_track_point_in_utc_time(self._hass, _mark_executed, mark_time)

    def cancel_task(self, schedule_id: str) -> bool:
        """Cancel a scheduled task by ID (sync — for in-memory timers)."""
        task = self._tasks.get(schedule_id)
        if task is None:
            return False
        result = task.cancel()
        self._cleanup_old_tasks()
        return result

    async def async_cancel_task(self, schedule_id: str) -> bool:
        """Cancel a scheduled task by ID, deleting the automation if needed.

        For automation-backed tasks, returns ``False`` if the persisted
        automation could not be removed — the task stays pending so the
        caller does not report a cancellation that didn't actually happen.
        """
        task = self._tasks.get(schedule_id)
        if task is None:
            return False
        if task.status != "pending":
            return False

        if task.automation_id:
            from .automation_utils import async_delete_automation

            try:
                deleted = await async_delete_automation(self._hass, task.automation_id)
            except Exception:  # noqa: BLE001 — third-party/IO failures
                _LOGGER.warning(
                    "Failed to delete automation %s for task %s",
                    task.automation_id,
                    schedule_id,
                )
                deleted = False

            if not deleted:
                _LOGGER.error(
                    "Cannot cancel task %s: persisted automation %s was not removed",
                    schedule_id,
                    task.automation_id,
                )
                return False

        task.cancel()
        self._cleanup_old_tasks()
        return True

    def cancel_all_pending(self) -> int:
        """Cancel all pending tasks. Returns the count cancelled.

        Called during integration unload to prevent timers firing after teardown.
        Only cancels in-memory timers synchronously; automation-backed tasks
        are left in place (they persist in automations.yaml intentionally).
        """
        count = 0
        for task in list(self._tasks.values()):
            if task.cancel():
                count += 1
        self._tasks.clear()
        return count

    def get_pending_tasks(self, session_id: str | None = None) -> list[ScheduledTask]:
        """Return pending tasks, optionally filtered by session. Most recent first."""
        tasks = [
            t
            for t in self._tasks.values()
            if t.status == "pending" and (session_id is None or t.session_id == session_id)
        ]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks

    def get_latest_pending(self, session_id: str) -> ScheduledTask | None:
        """Return the most recent pending task for a session."""
        pending = self.get_pending_tasks(session_id)
        return pending[0] if pending else None

    def get_all_tasks(self) -> list[dict[str, Any]]:
        """Return all tasks as dicts for websocket responses."""
        return [t.to_dict() for t in self._tasks.values()]

    def _enforce_pending_cap(self) -> None:
        """Reject new schedules when the pending task limit is reached."""
        self._cleanup_old_tasks()
        if self.pending_count() >= _MAX_PENDING_TASKS:
            raise RuntimeError(
                f"Too many pending scheduled actions (limit {_MAX_PENDING_TASKS}). "
                "Cancel some before scheduling more."
            )

    def _cleanup_old_tasks(self) -> None:
        """Remove completed/cancelled tasks older than 1 hour."""
        cutoff = datetime.now(UTC) - timedelta(hours=1)
        to_remove = [
            sid
            for sid, task in self._tasks.items()
            if task.status in ("executed", "cancelled", "partial_failure")
            and task.created_at < cutoff
        ]
        for sid in to_remove:
            del self._tasks[sid]
