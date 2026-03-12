# feat: Cross-session automation lineage tracking

**Labels:** `enhancement`, `automations`, `UX`
**Parent work item:** https://gitlab.com/groups/selorahomes/products/-/work_items/43
**Depends on:** `feat/automation-lifecycle` (automation-lifecycle-management.md)
**Suggested branch:** `feat/automation-lineage`

---

## Summary

Each automation version today records the single `session_id` that produced it. This issue extends that to a full lineage graph — tracking every chat session that contributed to an automation across its entire version history, enabling users to trace exactly how an automation evolved and jump back to any conversation that shaped it.

---

## Background / Current State

`AutomationVersion` (introduced in `feat/automation-lifecycle`) stores a single `session_id: str | None`. This captures which session created a version but loses the chain across multiple refinement rounds. If an automation is refined across three separate sessions, only the latest `session_id` is recoverable per version — the broader history of "which sessions touched this automation" requires traversing all version records manually.

`ConversationStore` in `__init__.py:103–252` stores sessions and messages but has no reverse index from automation → sessions.

---

## Goals

1. **Automation → session index** — For any automation, instantly retrieve all sessions that contributed to its evolution, in chronological order.
2. **Session → automation index** — For any session, show which automations were created or refined within it.
3. **Lineage view in panel** — A timeline showing the full edit history across sessions: "Created in session A → Refined in session B → Restored in session C".
4. **Session jump** — Clicking any lineage node navigates to that session in the chat tab, scrolled to the message that produced the version.

---

## Proposed Implementation

### `AutomationRecord` extension

Add a `lineage` field to `AutomationRecord` in `automation_store.py`:

```python
class LineageEntry(TypedDict):
    version_id: str
    session_id: str
    message_index: int | None   # position in session message list
    action: str                 # "created" | "updated" | "restored" | "refined"
    timestamp: str              # ISO datetime

class AutomationRecord(TypedDict):
    automation_id: str
    current_version_id: str
    versions: list[AutomationVersion]
    deleted_at: str | None
    lineage: list[LineageEntry]  # NEW — ordered chronologically
```

`lineage` is append-only. Every call to `AutomationStore.add_version()` appends a `LineageEntry` if a `session_id` is provided.

---

### Reverse index: session → automations

Add a top-level index to the `AutomationStore` data structure:

```python
# In store root:
{
    "records": { automation_id: AutomationRecord },
    "session_index": { session_id: [automation_id, ...] }   # NEW
}
```

Updated on every `add_version()` call. Allows `O(1)` lookup of automations touched by a given session.

---

### New websocket handlers in `__init__.py`

| Type | Action |
|---|---|
| `selora_ai/get_automation_lineage` | Returns `lineage` list for an `automation_id` with session metadata joined in |
| `selora_ai/get_session_automations` | Returns all automations that were created or refined in a given `session_id` |

`get_automation_lineage` response joins each `LineageEntry` with the session's title/preview from `ConversationStore` so the panel can render human-readable labels without a second round-trip.

---

### Panel: lineage timeline

In the version history drawer (introduced in `feat/automation-panel-ui`), add a "Lineage" tab alongside the version list:

- Vertical timeline, one node per `LineageEntry`
- Each node shows: action label, relative timestamp, session title snippet
- Clicking a node navigates to that session in the chat tab, scrolled to `message_index`
- Nodes with no `session_id` (e.g. manual YAML edits) show "Manual edit" with no navigation link

In the chat tab, add an "Automations" sidebar or badge on sessions that produced automations, linking to `get_session_automations`.

---

### `ConversationStore` extension

Add a helper `get_session_preview(session_id) -> str` that returns the session's first user message truncated to 60 characters — used by `get_automation_lineage` to populate human-readable labels without loading full session data.

---

## Files Affected

| File | Change type |
|---|---|
| `automation_store.py` | `LineageEntry` type, `lineage` field on `AutomationRecord`, session index, `add_version()` update |
| `__init__.py` | New `get_automation_lineage` and `get_session_automations` handlers; `ConversationStore.get_session_preview()` |
| Panel frontend | Lineage timeline in version history drawer; session → automation badges in chat tab |

---

## Acceptance Criteria

- [ ] Every `add_version()` call with a `session_id` appends a `LineageEntry` to the automation's lineage
- [ ] `get_automation_lineage` returns the full ordered lineage with session title previews joined in
- [ ] `get_session_automations` returns all automations touched by a session via the reverse index
- [ ] Panel lineage timeline renders all contributing sessions chronologically
- [ ] Clicking a lineage node navigates to the correct session and scrolls to the producing message
- [ ] Manual YAML edits with no `session_id` render as "Manual edit" nodes with no navigation link
- [ ] Session index stays consistent with `AutomationRecord.lineage` — no orphaned references

---

## Out of Scope

- Lineage across automations that were cloned or duplicated from one another
- Lineage for automations created outside of Selora (user-authored automations in `automations.yaml`)
