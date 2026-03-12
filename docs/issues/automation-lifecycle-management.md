# feat: Automation lifecycle management — versioning, conversation refinement, soft delete & restore

**Labels:** `enhancement`, `automations`, `UX`
**Parent work item:** https://gitlab.com/groups/selorahomes/products/-/work_items/43
**Suggested branch:** `feat/automation-lifecycle`

---

## Summary

The current automation system creates automations as static YAML entries in `automations.yaml` with no history, no versioning, and no recovery path. This issue introduces a full lifecycle management layer: the ability to load an existing automation back into a chat session for refinement, persist multiple versions, compare diffs between versions, soft-delete with a 30-day retention window, and restore from soft-deleted state.

---

## Background / Current State

Automations are written via `async_create_automation()` and `async_update_automation()` in `automation_utils.py`. Once written to `/config/automations.yaml`, there is no history. `ConversationStore` in `__init__.py` (lines 103–252) tracks chat sessions and automation proposal statuses (`pending → accepted → saved`) but has no concept of a version chain or tombstoned records. The `get_automations` websocket handler (`__init__.py:729–796`) returns live automations but carries no metadata layer.

---

## Goals

1. **Load into conversation for refinement** — A user can select any existing Selora automation and open it as context in a new or existing chat session, enabling iterative improvement without starting from scratch.
2. **Automation versioning** — Every `create` and `update` event produces a new immutable version record. The live automation always points to the latest accepted version.
3. **Version comparison** — Users can select two versions and view a structured YAML diff.
4. **Soft delete** — Deleting an automation marks it with `deleted_at` rather than removing it from `automations.yaml` immediately. It is disabled in HA and hidden from the default UI.
5. **Restore** — Soft-deleted automations can be restored within 30 days, re-enabling them in HA.
6. **Cron cleanup** — A daily background job purges soft-deleted automations older than 30 days, removing them from `automations.yaml` and version history permanently.

---

## Proposed Implementation

### 1. New file: `automation_store.py`

Backed by HA's `Store` API — the same pattern used by `ConversationStore` in `__init__.py:103`. No custom SQLite; the data volume (even at 50 automations × 20 versions) does not justify schema management overhead.

```python
class AutomationVersion(TypedDict):
    version_id: str          # uuid4
    automation_id: str       # selora_ai_<id>
    created_at: str          # ISO datetime
    yaml: str
    data: dict               # parsed automation dict
    message: str             # e.g. "Initial creation", "Refined via chat session abc123"
    session_id: str | None   # which chat session produced this

class AutomationRecord(TypedDict):
    automation_id: str
    current_version_id: str
    versions: list[AutomationVersion]
    deleted_at: str | None   # ISO datetime or None
```

Store key: `selora_ai_automations` via `hass.helpers.storage.Store`.

---

### 2. Changes to `automation_utils.py`

- `async_create_automation()` — after writing to `automations.yaml`, call `AutomationStore.add_version()` with message `"Created"` and optional `session_id`.
- `async_update_automation()` — after writing, call `AutomationStore.add_version()` with message and `session_id`.
- New `async_soft_delete_automation(automation_id)` — sets `deleted_at`, disables the automation in `automations.yaml` (`initial_state: False`), triggers reload. Does **not** remove from file yet.
- New `async_restore_automation(automation_id)` — clears `deleted_at`, re-enables automation in `automations.yaml`, triggers reload.
- New `async_purge_deleted_automations(older_than_days=30)` — removes records and `automations.yaml` entries for automations where `deleted_at` is beyond threshold. Uses Python `difflib` for any comparison steps — no new dependencies.

---

### 3. New websocket handlers in `__init__.py`

Registered in `async_setup()` alongside existing handlers (lines 903–916):

| Type | Action |
|---|---|
| `selora_ai/get_automation_versions` | Returns ordered version list for an `automation_id` |
| `selora_ai/get_automation_diff` | Returns unified diff between two `version_id`s via `difflib.unified_diff` |
| `selora_ai/soft_delete_automation` | Calls `async_soft_delete_automation()` |
| `selora_ai/restore_automation` | Calls `async_restore_automation()` |
| `selora_ai/load_automation_to_session` | Creates/extends a session with the automation YAML as assistant context, sets `automation_status: "refining"` |

`load_automation_to_session` follows the existing session pattern in `ConversationStore` — it inserts a message with `role: assistant`, `intent: automation`, and populates `automation_yaml` and `automation` from the live version. From that point, refinement flows through the existing `selora_ai/chat` handler (lines 366–500).

---

### 4. Changes to `collector.py` — daily purge cron

Add a second scheduled task alongside `_scheduled_cycle()`. Use `async_track_time_interval` (already imported) to run `async_purge_deleted_automations(30)` daily. Mirrors the existing interval setup pattern in `async_setup_entry()`.

---

### 5. `const.py` additions

```python
AUTOMATION_SOFT_DELETE_DAYS = 30
AUTOMATION_STORE_KEY = "selora_ai_automations"
```

---

### 6. `get_automations` handler update (`__init__.py:729–796`)

Merge live HA automation state with `AutomationStore` metadata to return per-automation:

- `version_count`
- `current_version_id`
- `deleted_at`
- `is_deleted`

Soft-deleted automations are included in the response only when `include_deleted: true` is passed in the websocket message.

---

## Files Affected

| File | Change type |
|---|---|
| `automation_utils.py` | Add soft delete, restore, purge, version hooks |
| `__init__.py` | New websocket handlers, `load_to_session`, purge scheduling |
| `collector.py` | Daily purge cron via `async_track_time_interval` |
| `const.py` | New constants |
| `automation_store.py` *(new)* | `AutomationStore`, `AutomationRecord`, `AutomationVersion` |

---

## Acceptance Criteria

- [ ] Creating or updating an automation via any path (chat, YAML editor, collector suggestions) produces a version record in `AutomationStore`
- [ ] `get_automation_versions` returns an ordered version history with timestamps and session references
- [ ] `get_automation_diff` returns a unified diff between any two versions
- [ ] `soft_delete_automation` disables the automation in HA and sets `deleted_at`; the automation does not appear in the default `get_automations` response
- [ ] `restore_automation` re-enables the automation in HA and clears `deleted_at`
- [ ] `load_automation_to_session` opens a chat session with the automation's current YAML pre-loaded and `automation_status: "refining"`; subsequent chat messages refine it via the existing chat handler
- [ ] Daily purge job permanently removes automations with `deleted_at` older than 30 days from both the store and `automations.yaml`
- [ ] No automation is permanently deleted without having passed through the 30-day soft-delete window

---

## Out of Scope

- Frontend/panel UI changes (surface version history, diff viewer, restore button) — separate issue
- Hard delete before 30 days — follow-up
- Cross-session automation lineage tracking — follow-up
