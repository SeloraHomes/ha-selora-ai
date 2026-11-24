# Support-session activity for Connect

`support_activity.py` records what an installer's AI did during a Connect
support session, so Connect can show it on the session afterwards. Today that
means the browser-code access of `file-access.md`; other write tools can report
through the same `async_record`.

- **The integration never talks to Connect.** Each event is a file in
  `<config>/selora_ai/support_activity/`. Selora OS reads it out of the VM and
  posts it with the installation's token, the same split as the insights
  export.
- **One file per event, never changed after.** It is written to `.<name>.tmp`,
  fsynced, then renamed; only `*.json` is ready. Names are
  `<UTC %Y%m%dT%H%M%S%fZ>-<event_id>.json`, so a byte-wise sort is time order.
  The host keeps a cursor (the last name Connect accepted) and never writes
  into the VM. Connect drops repeats by `event_id`.
- **The outbox prunes itself**: 30 days, 2,000 files at most. A hub without
  Selora OS has nothing reading it. A failed write is logged and never fails
  the change it reports.
- **Only a support session's records are reported.** A request carries
  `support_session` when its token had a `sid`. The home's own requests stay
  on the hub.

## Event

Every event has `schema_version` (1), `event_id` (hex), `session_id` (Connect's
`sid`, integer), `occurred_at` (ISO 8601, UTC, hub clock), `type`,
`request_id` (one per access, shared by all its events) and `agent` (the app
that asked, e.g. "Claude").

| `type` | Extra fields |
|---|---|
| `access_requested` | `folder`, `reason` (agent's words, sanitized, ≤300), `minutes` |
| `access_granted` | `folder`, `minutes`, `answered_by` (HA user name), `expires_at` |
| `access_denied` | `answered_by` |
| `file_written` | `file` (relative to the config dir), `action` (`created`, `replaced`, `deleted`), `size` (bytes), `sha256` (null for a delete), `summary`, `findings` |
| `access_ended` | `reason` (`expired`, `revoked`, `session_ended`), `files` (count) |

`findings` are this version's, each `{kind, severity, detail, line}`, with
`severity` one of `high`, `medium`, `info`. A version replaced later keeps its
own event, so nothing a file did is lost. Untrusted text (reason, agent,
details) is sanitized but must still be escaped where it is shown.
