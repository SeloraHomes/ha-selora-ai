# The Alexa voice credential

Selora OS provisions voice and must deliver the credential. Writing it into our
config entry means editing `.storage/core.config_entries`, safe only with HA
stopped — so linking the skill restarted the owner's home, unannounced. The OS
now also writes `<config>/.selora/alexa_credential.json` (0600 in a 0700
directory, renamed into place — no locking). `alexa_credential_file.py` reads it,
`_alexa_credentials` prefers it, and applying a credential rebuilds a validator in
place with no restart.

- **Never write what the file carries into the config entry.** Once the OS sees
  our ack it drops the Alexa keys from its *desired* entry data, and its
  reconciler compares desired against live key by key (`cmp_view` in
  `modules/selora-ai.nix`, including `selora_alexa_*`). A key we add reads as
  drift and triggers a Core restart every reconcile. The file is a transport, not
  a staging area.
- **The panel's Connect relink is the other door.**
  `oauth_link._apply_alexa_block` writes the same four keys, so it takes
  `delivered_by_file` (asked of the credential actually delivered, not the OS
  version) and, when the file delivers, DROPS the keys — stale ones are the same
  drift. Defaults to False.
- **The entry fallback is not legacy tolerance** — it serves OSes predating the
  channel and every hub until the OS sees our ack, so hub and integration update
  independently. Inbound and outbound ask the ONE resolver
  (`alexa_connect.build_client` and `alexa_view._installation_id` both call
  `_alexa_credentials`), so both read the sources in the same order, name the
  same installation, and apply the device-entry and disabled-entry rules. Missing
  the outbound half silently kills proactive reporting. The Connect URL is not in
  the file; it comes off an entry or the compiled-in default.
- **An eligible entry says voice is wanted; the file only says what the
  credential is.** The file outlives entries, so `_alexa_eligible_entries` keeps
  `exclude_entry_id`, the disabled-entry and stray-entry rules.
- **Absent and unreadable are different answers.** Absent is a WITHDRAWAL and
  clears the validator (the revoke path). Malformed or truncated leaves the last
  whole credential standing, cached under `hass.data[DOMAIN]` — which also keeps
  `_alexa_credentials` synchronous (the read is an executor job).
- **The poll is a timer, and not optional** — the credential arrives minutes after
  linking. `async_track_time_interval` at `CREDENTIAL_POLL_SECONDS` (30s),
  armed/disarmed inside `_async_sync_alexa_runtime`. Its job is a BACKGROUND task,
  so tests need `wait_background_tasks=True`. Disarming cannot cancel a tick
  already suspended on the file read, so `_async_poll_alexa_credential` (module-
  level so tests can drive it) takes the armed handle as a generation token and
  drops its answer if teardown replaced it.
- **Everything the sync logs is gated on the credential fingerprint changing** —
  it runs every 30 seconds. File-read and ack-write failures come back as strings
  and are logged on the transition by the caller; `alexa_credential_file` logs
  nothing, which also keeps the credential's bytes away from the logger.
- **The ack is `<config>/.selora/alexa_applied.json`: `{"channel": 1}` plus
  `applied_digest`.** `channel` declares the CAPABILITY and is written on every
  setup, credential or none — a rotating credential is momentarily un-applied,
  and conflating the two would bring restarts back on every rotation.
  `applied_digest` follows the VALIDATOR (a credential refused for overlapping
  MCP's key space is delivered, not applied). Nothing is written once no entry
  remains — a reload's unload would otherwise retract the digest.
- **The digest is over the file's raw bytes**, which end in a newline (`jq -cS`
  through `printf '%s\n'`): `hashlib.sha256(path.read_bytes()).hexdigest()`. A
  re-serialised hash never matches. The test fixture is byte-identical.
- **Each ack write stages under its own temp name** — two syncs can overlap, and
  a shared name lets one `O_TRUNC` empty the file the other is renaming. A garbled
  ack reads as no ack.
- **Nothing logs the key or the file's contents, at any level** — a test asserts
  it against real output. The truncated-file error is the tempting place to quote
  bytes.
- **Key spaces stay disjoint** — audience `selora-alexa`, scope prefix `alexa:`,
  no fallback to the MCP key or epoch. The OS emits all five members or none, so
  a missing member means the bytes are not whole. Only `installation_id` may be a
  JSON number (Connect marshals an int64); accepting numbers elsewhere turns
  `"jwt_key": 123` into a credential that replaces the cached good one and then
  raises out of `decode_jwt_key` every 30 seconds.
