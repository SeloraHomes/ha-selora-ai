# feat: Automation panel UI — version history, diff viewer & restore

**Labels:** `enhancement`, `automations`, `frontend`, `UX`
**Parent work item:** https://gitlab.com/groups/selorahomes/products/-/work_items/43
**Depends on:** `feat/automation-lifecycle` (automation-lifecycle-management.md)
**Suggested branch:** `feat/automation-panel-ui`

---

## Summary

Expose the version history, diff comparison, soft delete, and restore capabilities introduced in the backend lifecycle issue directly in the Selora AI side panel. Users should be able to review an automation's full edit history, compare any two versions side-by-side, restore a prior version, and recover soft-deleted automations — all without leaving the panel.

---

## Background / Current State

The side panel (`/selora-ai`) is websocket-driven. The `get_automations` handler (`__init__.py:729–796`) currently returns live HA automation state. With the lifecycle backend in place, it will also return `version_count`, `current_version_id`, `deleted_at`, and `is_deleted` per automation. The panel has no UI today for surfacing any of this metadata.

---

## Goals

1. **Version history drawer** — Clicking an automation opens a side drawer listing all versions chronologically: timestamp, version message (e.g. "Refined via chat session"), and a preview toggle.
2. **Diff viewer** — Selecting two versions renders a unified diff with additions highlighted green and removals highlighted red. Backed by `selora_ai/get_automation_diff` websocket handler.
3. **Restore prior version** — A "Restore this version" button on any historical version calls `selora_ai/update_automation_yaml` with that version's YAML, creating a new version record with message `"Restored from version <version_id>"`.
4. **Soft delete UI** — A delete button on each automation triggers `selora_ai/soft_delete_automation`. The automation is immediately hidden from the default list and shown in a separate "Deleted" section (toggle to reveal).
5. **Restore deleted automation** — Within the "Deleted" section, a "Restore" button calls `selora_ai/restore_automation`, moves the automation back to the active list.
6. **Load into chat** — A "Refine in chat" button on any automation (or version) calls `selora_ai/load_automation_to_session` and navigates the user to the chat tab with that session active.

---

## Proposed Implementation

### Automation list view changes

Add per-row actions to the existing automations list:

| Action | Websocket call | Notes |
|---|---|---|
| Version history | `get_automation_versions` | Opens drawer |
| Refine in chat | `load_automation_to_session` | Navigates to chat tab |
| Delete | `soft_delete_automation` | Moves to deleted section |

Show `version_count` as a badge on each automation row (e.g. `v3`). Grey out and move automations with `is_deleted: true` into a collapsible "Recently Deleted" section at the bottom of the list.

---

### Version history drawer

Triggered by clicking the version badge or a "History" icon. Content:

- Ordered list of versions (newest first), each showing:
  - Relative timestamp (e.g. "2 days ago") with full ISO on hover
  - Version message string
  - Session link if `session_id` is set (navigates to that chat session)
  - "View YAML" toggle (inline collapsible code block)
  - "Compare with current" button — pre-selects this version in the diff viewer
  - "Restore this version" button

---

### Diff viewer

A modal or split-pane view. User selects two versions from dropdowns (defaulting to current vs. previous). Renders the output of `get_automation_diff` line by line:

- Lines prefixed `+` — green background
- Lines prefixed `-` — red background
- Context lines — neutral

Uses the unified diff format returned by Python's `difflib.unified_diff` on the backend — no additional parsing needed on the frontend.

---

### "Recently Deleted" section

Collapsed by default. Toggling open calls `get_automations` with `include_deleted: true`. Each entry shows:

- Automation alias
- `deleted_at` timestamp + days remaining before permanent purge (30 − elapsed days)
- "Restore" button → `restore_automation`
- If ≤ 3 days remaining, show a warning badge

---

### "Refine in chat" flow

Calls `load_automation_to_session`, which returns a `session_id`. The panel switches to the chat tab and sets the active session to the returned `session_id`. The first message in the session will be the automation YAML pre-loaded as assistant context. The user can immediately type a refinement request.

---

## Files Affected

All changes are in the panel frontend (JavaScript/TypeScript under `www/` or equivalent panel source):

| Area | Change |
|---|---|
| Automation list component | Version badge, per-row action menu, deleted section |
| Version history drawer | New component |
| Diff viewer | New modal/pane component |
| Recently deleted section | New collapsible component |
| Chat tab | Handle `load_automation_to_session` navigation |
| Websocket client | Add calls for `get_automation_versions`, `get_automation_diff`, `soft_delete_automation`, `restore_automation`, `load_automation_to_session` |

---

## Acceptance Criteria

- [ ] Each automation row shows a version count badge; clicking it opens the version history drawer
- [ ] Version history drawer lists all versions with timestamp, message, and session link
- [ ] Diff viewer renders a colour-coded unified diff between any two selected versions
- [ ] "Restore this version" creates a new version and updates the live automation in HA
- [ ] Delete button soft-deletes the automation; it disappears from the active list immediately
- [ ] "Recently Deleted" section is accessible via toggle and shows days remaining before purge
- [ ] Restore button in deleted section brings the automation back to the active list
- [ ] "Refine in chat" navigates to a pre-loaded chat session with `automation_status: "refining"`
- [ ] All actions provide loading states and error feedback if the websocket call fails

---

## Out of Scope

- Backend versioning and soft delete logic — covered in `feat/automation-lifecycle`
- Hard delete before 30 days — separate issue
- Cross-session lineage view — separate issue
