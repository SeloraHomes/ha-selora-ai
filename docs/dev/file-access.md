# Temporary browser-code access

`selora_request_file_access` (`file_access.py`, handler in `mcp_server/files.py`,
websocket in `websocket/file_access.py`, panel in `panel/render-file-access.js`)
lets ONE MCP client write browser code (`.js`, `.css`, `.html` …) into ONE
`www/` subfolder for up to an hour, after a person approves it. Everything else
about files is `config-files.md`.

- **The agent asks; only a person grants.** Browser code runs with every viewer's
  session, so the rule in `config-files.md` stands: an agent's say-so, or a
  `confirmed` flag it sets itself, never writes it. There is no tool that
  approves, and `approval_required` (the confirmation opt-out) does not apply.
- **Approval needs a login session.** `decide` and `revoke` are admin-only AND
  refuse a long-lived access token (`login_session_required`): an agent holding
  the user's token could otherwise call the websocket and approve itself. The
  browser and the companion app use normal refresh tokens.
- **Bound to the credential that asked** (`CallerCredential`, set in
  `_dispatch`): an MCP token, or an HA token by its refresh token, is its own
  key, so a grant approved for one app is not another's; a Selora login is keyed
  by its user, the finest identity it carries. Selora's own chat has no
  credential, so it can neither ask nor use a grant — it is the lane most exposed
  to prompt injection (device names, camera descriptions, web text).
- **The asker is named by its app first** ("ChatGPT", "Claude" —
  `mcp_server/clients.py`): with one owner per home, the account says nothing.
  The server is stateless, so each `initialize`'s `clientInfo.name` is kept
  under the credential and User-Agent (two apps on one Selora account differ by
  User-Agent), with the User-Agent itself as fallback after a restart. It is
  self-reported, so the dialog shows the credential the grant is bound to beside
  it ("Connected with: Your Selora account"). Without an app name, the kind and
  a recognisable name (email, key name, HA user) phrase it — never an internal
  id like `user:2`.
- **One subfolder, never `www` itself**, plain segments. A grant covers
  `<folder>/…`, which is one card and its vendored libraries, not everything
  `/local/` serves.
- **Expiry is an absolute time checked on every write** (`grant_for`). The
  timers only end grants and lapse requests on time for the notifications; a
  timer that never fired still lets nothing through. 15/30/60 minutes; an
  unanswered request lapses after 15.
- **Stored, because an agent may restart Home Assistant** after installing
  something. A restart keeps an unexpired grant running (its timer re-armed at
  load) and its write record complete; one that lapsed while down ends at load.
  Requests, grants and the hourly ask log are in `selora_ai.file_access`.
- **Notifications go to the admins' phones and to Home Assistant.** A persistent
  notification, and a push through each active admin's `notify.mobile_app_*`,
  link to `/selora-ai?file_access=<id>`, which opens the approval dialog. iOS
  opens `url`, Android `clickAction`; the push is `time-sensitive`. Answering or
  lapsing clears it (`clear_notification` by `tag`). Three asks per credential
  per hour, counted from a log the pruning does not touch — every ask buzzes a
  phone. Asking again while one is pending returns it and notifies nobody.
- **An installer's request is the installer's alone.** A Connect token with a
  support session (`sid`) is keyed per session, so a grant never carries over
  to the next one. Nobody in the home is notified: no push, and no persistent
  notification, which every HA user would see. The tool result carries an
  `approve_url` on the hub's remote address instead, and the installer is
  already in that chat. Only the HA user the installer signs in as during the
  session sees and answers the request (`visible_to`). That user is the
  `command_line` credential whose username is the token's email, never the
  owner. To any other admin, the request does not exist. The grant ends when
  that user is removed at session end (`user_removed`), and the ended summary
  raises no alert. The homeowner lent the hub out as an admin, so a live alert
  would add nothing. The record stays in storage and in the WARNING log.
- **A grant's id is its request's id**, so the one link opens the request, the
  live grant and the ended summary.
- **Every write is checked and recorded** (`code_findings.py`): network calls,
  outside addresses, reads of the session (`hassTokens`, `localStorage`,
  `hass.auth`), text run as code, admin websocket commands, the services called
  (sensitive domains flagged), encoded code. Deterministic, never a model: the
  agent that wrote the file must not be able to argue its way past it. Findings
  are facts for the person, not a verdict — the agent gets them back in the
  write result, the person sees them in the panel and in the summary
  notification when the grant ends. A file written again keeps one entry with
  a count. Past 25 findings each kind keeps its first, credentials and code
  from text rank first, and one more finding counts the rest at their worst
  severity: 25 harmless addresses above a token read must not hide it.
- **No compressed copies in `www/`.** The static server sends `card.js.br`
  for `card.js` to a browser that accepts brotli, typed as JavaScript, so a
  `.br` or `.gz` name is refused, grant or not: what runs must be the file
  that was checked.
- **The record is the agent's, so the agent can't edit it.** A rewrite or a
  delete keeps the earlier version's findings, marked `earlier`: the bad
  version may have run in a browser before a clean one replaced it. A full
  record (200 files) refuses new files instead of dropping old entries, or a
  flood of harmless files would push a flagged one out. Dismissing a summary
  needs a login session, like deciding. Over MCP, any `persistent_notification`
  call on a `selora_file_access*` id is refused (`mcp_service_call.py`): a
  `create` over the id would rewrite the record as surely as a dismiss.
  An agent holding an HA token can still clear the notification through Home
  Assistant itself; the panel's record, and the WARNING log line per request,
  write and end, stay.
- **Actions are listed even when a helper calls them.** A card routes presses
  through one `_call(domain, service)`, so `callService` itself names nothing;
  the scan finds that helper and lists the literal pairs passed to it (a
  variable service reads "cover (any action)"). Only matching the helper's
  calls keeps `["scene", "script"]` or `tile("climate", …)` off the list.
  The panel shows them in one row, sensitive domains marked.
- **A grant shows time left, h:mm, not a clock time** — the way Connect shows
  a support session, without seconds, since it runs in the background.
- **Dashboard config is unaffected.** `dashboard_scripts.py` still refuses
  JavaScript in card config (`[[[ ]]]`, `${ }`, `EVAL:`, `javascript:`); a grant
  is for card FILES, which a `/local/` resource then loads.
