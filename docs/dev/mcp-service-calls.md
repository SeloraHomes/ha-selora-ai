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

## Restarting

`homeassistant.restart` stays on the denylist: the generic tool must not reach
it. `selora_restart_home_assistant` (`restart_manager.py`) is the dedicated
path, because YAML edits and new integrations only take effect after one:

- **HA's own config check runs first** and its errors come back, redacted as
  the YAML editor's are (`_redact_problem`: HA quotes the rejected value, which
  can be a password). HA's restart service checks too, but only by raising
  after the call — a client that fired it would not learn why nothing happened.
- **It asks first** (`requires_confirmation`), and is refused while a database
  upgrade runs, as HA refuses it.
- **Started, not awaited** — HA stops under the call, so the answer leaves first.
- `set_config_yaml`'s `restart_required` and a HACS integration install name
  this tool in their hint.

## What is not a target

- **A `script.<name>` call's `data` is the script's own inputs** (fields), so a
  field named `device_id` or `area_id` is not a hidden target
  (`_data_is_script_input`). `script.turn_on`/`turn_off`/`toggle`/`reload` keep
  the check: their data can carry real targets.
- **The denylist is for the irreversible**, per its own rationale. Refreshing a
  sensor (`homeassistant.update_entity`) and the read-only
  `homeassistant.check_config` are not, and are not on it.

## Firing events

`selora_fire_event` (`event_bus.py`, MCP only, admin) fires an event on the bus —
for event-triggered automations, Node-RED, or simulating what triggers an
automation to test it.

- **Home Assistant's own events are refused**: core's `EVENT_*` constants (read
  at runtime from `homeassistant.const`), `*_registry_updated`, `*_reloaded`
  (a component announcing it re-read its config: scene, automation, store),
  `homeassistant_*` and a few component system events (`automation_triggered`,
  `user_*` …). Device events (`zha_event`, a button's own) stay allowed —
  simulating one is how an automation is tested. They
  are how the system tells its parts what happened; firing one by hand reports
  something that did not.
- **It asks first, naming the automations it starts** (event triggers matching
  the type, read from each automation's config), honouring the `approval_required`
  opt-out like a service call. A custom button event can start an unlock. Other
  subscribers cannot be seen from here, and the confirmation says so.
- The type is 1–64 characters (HA's own limit), no whitespace; `data` is a JSON
  object under 16 000 characters, passed through `json` so only plain values
  reach the bus.
