# feat: Automation hard delete — user-initiated permanent removal before 30-day window

**Labels:** `enhancement`, `automations`, `UX`
**Parent work item:** https://gitlab.com/groups/selorahomes/products/-/work_items/43
**Depends on:** `feat/automation-lifecycle` (automation-lifecycle-management.md)
**Suggested branch:** `feat/automation-hard-delete`

---

## Summary

The lifecycle issue establishes soft delete with a 30-day automatic purge. This issue adds user-initiated hard delete — the ability to permanently remove an automation and all its version history before the 30-day window expires. Requires an explicit confirmation step to prevent accidental data loss.

---

## Background / Current State

`async_soft_delete_automation()` (introduced in `feat/automation-lifecycle`) sets `deleted_at` and disables the automation in `automations.yaml`. The 30-day cron in `collector.py` handles eventual permanent removal. There is currently no path for a user to bypass that window intentionally.

---

## Goals

1. **Hard delete action** — From the "Recently Deleted" section in the panel, a user can choose to permanently delete an automation immediately rather than waiting for the cron.
2. **Confirmation gate** — Requires a two-step confirmation (e.g. type the automation alias) to prevent accidental permanent loss.
3. **Backend enforcement** — Hard delete is only permitted on automations already in soft-deleted state (`deleted_at` is set). Active automations cannot be hard deleted directly; they must be soft-deleted first.
4. **Complete removal** — Removes the entry from `automations.yaml`, purges the `AutomationRecord` and all `AutomationVersion` entries from `AutomationStore`, and triggers an `automation.reload`.

---

## Proposed Implementation

### New function in `automation_utils.py`

```python
async def async_hard_delete_automation(
    hass: HomeAssistant,
    automation_store: AutomationStore,
    automation_id: str,
) -> None:
    """Permanently delete a soft-deleted automation and all version history.

    Raises ValueError if the automation is not in soft-deleted state.
    """
```

- Reads `AutomationStore` — raises `ValueError` if `deleted_at` is `None` (must soft-delete first)
- Removes the automation entry from `automations.yaml` via `_read_automations_yaml` / `_write_automations_yaml`
- Calls `automation_store.purge_record(automation_id)` to remove all version history
- Calls `automation.reload`

### New websocket handler in `__init__.py`

| Type | Action |
|---|---|
| `selora_ai/hard_delete_automation` | Validates soft-deleted state, calls `async_hard_delete_automation()` |

Registered in `async_setup()` alongside existing handlers (lines 903–916).

### Panel UI

In the "Recently Deleted" section (introduced in `feat/automation-panel-ui`), add a "Permanently Delete" button per entry. Clicking it opens a confirmation modal requiring the user to type the automation alias before the action is enabled. On confirm, calls `selora_ai/hard_delete_automation`.

---

## Files Affected

| File | Change type |
|---|---|
| `automation_utils.py` | New `async_hard_delete_automation()` |
| `automation_store.py` | New `purge_record(automation_id)` method |
| `__init__.py` | New `selora_ai/hard_delete_automation` websocket handler |
| Panel frontend | Confirmation modal + "Permanently Delete" button in deleted section |

---

## Acceptance Criteria

- [ ] `hard_delete_automation` raises an error if called on an automation that is not soft-deleted
- [ ] Confirmed hard delete removes the automation from `automations.yaml` and all version records from `AutomationStore`
- [ ] `automation.reload` is triggered after removal
- [ ] Panel confirmation modal requires typing the automation alias before the action is enabled
- [ ] Hard-deleted automation no longer appears in any panel view or `get_automations` response
- [ ] Soft-delete → hard delete path works end-to-end; automatic 30-day purge path is unaffected

---

## Out of Scope

- Bypassing soft delete entirely (direct hard delete from active state) — not supported by design
- Bulk hard delete — follow-up if needed
