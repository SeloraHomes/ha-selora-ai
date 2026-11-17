# Health issue events

`health_events.py` announces the Health view's device problems on the bus, so
a homeowner can get a push when one arises and lose it when it clears, without
opening the panel. Its reader is a notification automation, and a push that
fires and clears within minutes teaches the owner to mute it.

## Surface

- `selora_ai_health_issue_raised` / `selora_ai_health_issue_cleared`, payload
  `HealthIssue` (`types.py`): `issue_id`, `check`, `severity`, `title`,
  `detail`, `device_id`, `entities`, `link`, `since`, `active_issues`.
- `sensor.…_health_issues`: the count of confirmed issues, a count per check,
  and the first 25 issues in an unrecorded `issues` attribute.
- `binary_sensor.…_health_problem` (`problem`): on while any is confirmed.

Both entities are unavailable while Insights is off or before its first audit,
never "no problem".

## Rules

- **Never one entity per issue.** Issues come and go every scan; entities
  that did would churn the registry and the recorder and leave orphans. The
  per-issue surface is the events, and the entities are fixed aggregates.
- **The source is the audit's findings**, the device-collapsed cards the Health
  view shows (`NOTIFY_CHECKS`), not the raw per-entity signals, so a push
  reads like the card, and a dead device with five entities is one push.
  Automation hygiene and pending updates are advice and are not announced.
- **`issue_id` is `<check>:<device_id | entity | link>`** and stays stable across
  audits; automations use it as the notification `tag`.
- **Evidence is a health scan, not an audit.** The device checks read the
  signals the last scan stored, so an audit with no new `last_scan` behind it
  (the daily periodic run, a re-run) is skipped, per check: one that errored
  on a scan can still take it on a later audit. Windows are measured
  between scan timestamps. Raised needs a later scan to still find the issue
  `HEALTH_EVENT_CONFIRM_SECS` after it first appeared, with no scan in between
  missing it; cleared needs it missing for the same window, and only for an
  issue that was raised. The window is below the default scan interval, so
  the next scan confirms.
- **A check that errored keeps its issues as they were.** Reading its empty
  findings as resolved would send an "all clear" for a failure to look.
- **Boot evidence is skipped**: no processing while `AuditRunner.is_settling()`,
  and no scan taken before the settle deadline (the first scan runs inside
  it). Devices that haven't reconnected yet are not problems.
- **Each check's first successful run is a silent baseline.** Without it, an
  upgrade would push every standing problem at once. Per check, not per
  tracker: a check that errored on the first audit baselines when it first
  succeeds. Baseline issues count in the entities and leave without a clear.
- **The cleared event repeats the raised wording.** An announced issue's
  payload is frozen, so the clear names what the owner was told about.
- **State persists in the health store** (`meta.health_events`), so a restart
  neither repeats a raise nor drops a clear it owes. The disk is written only
  when a record changes; re-processing a scan after a crash reaches the same
  records.

## Automation

```yaml
triggers:
  - trigger: event
    event_type: selora_ai_health_issue_raised
    id: raised
  - trigger: event
    event_type: selora_ai_health_issue_cleared
    id: cleared
actions:
  - action: notify.mobile_app_phone
    data:
      title: "{{ trigger.event.data.title }}"
      message: >-
        {{ trigger.event.data.detail if trigger.id == 'raised' else 'clear_notification' }}
      data:
        tag: "{{ trigger.event.data.issue_id }}"
```
