# Service calls over MCP

`selora_execute_command` calls ANY Home Assistant service, sorted by risk
(`mcp_service_call.py`). Chat's `execute_command` is untouched: it keeps the
allowlist and the approval cards in `llm_client/command_policy.py`. The two share
only the classification tables and `_call_service_and_settle` (dispatch + state
read-back), so they cannot disagree about what a call DID.

- **The denylist wins** (`_BLOCKED_SERVICES`: restart, recorder purge, host
  reboot …), confirmed or not. Those belong to dedicated admin tools.
- **LOW risk runs at once, decided BY VERB**: chat's curated services
  (`_ALLOWED_COMMAND_SERVICES` — its verbs, not its domains: `scene.delete` is
  not `scene.turn_on`), REVIEW entries rated low (tts, notify, vacuum), and
  `_LOW_RISK_SERVICES` — services that change only the entity they target and
  can be set back by the next call. A domain is not a risk level:
  `todo.remove_item` deletes, `timer.finish` fires the automations waiting on it,
  `remote.send_command` sends anything. **Buttons are not low risk**: a button
  can reboot or factory-reset a device, and an input button fires whatever
  automations listen for it.
- **Targets go in `entity_id` and nowhere else.** `entity_id`, `device_id`,
  `area_id`, `floor_id`, `label_id` inside `data` are refused: they reach HA
  unseen by the risk check (a garage door named there is not elevated), and
  device/area targets expand to entities nobody checked.
- **An entity service must be given a target** (`_needs_target`): required if
  the REVIEW entry says so OR the service's schema takes `entity_id` — either
  can require, neither waives the other (`script.*` is targetless for
  `script.my_script`, but `script.turn_on` matches it and is an entity service).
  Confirmed without one, `lock.unlock` could act on every lock.
- **Everything else needs `confirmed: true`**: REVIEW entries above low, the
  garage-door elevation (`_entity_aware_review_entry`), and any service no table
  names (medium — its effect is unknown). The first call runs nothing and returns
  `requires_confirmation`, `risk_level` and the reason; MCP has no card, so the
  agent asks the user and calls again. Adding a verb to `_LOW_RISK_SERVICES` is
  a policy decision — the REVIEW table and denylist are consulted first, so a
  low-risk domain cannot shadow a risky verb listed there.
- **The install's `approval_required` opt-out is honoured** — a user who turned
  approvals off is not asked here either.
- **`selora_validate_action` returns the same verdict** (`check_service_call`),
  or a pre-flight check could approve what the call then refuses.
- **Validation is Home Assistant's, not ours**: the service must exist
  (`has_service`) and every target must be in the state machine; data keys are
  not allowlisted — the service's own schema refuses what it does not take.
  `_remote_media_content_error` still applies. Up to `_MAX_TARGETS` entities.
- **Response data is asked for when the service offers it** (`todo.get_items`,
  `calendar.get_events`, `weather.get_forecasts`) — HA refuses a response-only
  service called without `return_response`. It is household text, so every
  string is sanitized and bounded (`_bounded`). Responses nest deeper than
  `_truncate_result` looks (`todo.get_items` → entity → `items`), so
  `_within_budget` halves the longest list at any depth until it fits and sets
  `response_truncated`. A response-ONLY service is a read and skips the state
  settle wait, which would otherwise sit out its full timeout every time.

## Calendar events

Reading and adding events works through the any-service tool
(`calendar.get_events`, `calendar.create_event`), but the result leaves out each
event's `uid`, and changing or removing an event has no service — only the
`calendar/event/*` websocket commands. `calendar_manager.py` goes to the
calendar entity as those commands do (`DATA_COMPONENT.get_entity`, the
`CalendarEntityFeature` check, `WEBSOCKET_EVENT_SCHEMA`), behind
`selora_list_calendar_events` / `selora_set_calendar_event` /
`selora_delete_calendar_event`.

- **A change replaces the event** — Home Assistant's update takes a whole event,
  so the tool requires summary/start/end and says a field left out is removed.
- **`recurrence_range` is compared verbatim** by Home Assistant; anything but
  `THISANDFUTURE` would silently act on one occurrence, so it is refused.
- **Event text is untrusted** — a shared or subscribed calendar is written by
  someone else — so it is bounded and sanitized before a model reads it.
- Listing is read-only access; adding, changing and removing need admin.
