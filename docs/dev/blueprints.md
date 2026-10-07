# Blueprints

`blueprint_manager.py` backs `list_blueprints` and `get_blueprint`. Blueprints
are reachable in-process: `hass.data["blueprint"]` is a
`dict[domain, DomainBlueprints]` published by the automation and script
components (the same object the websocket API serves).

- **The reads are useless without the write.** A blueprint automation is
  `use_blueprint: {path, input}` with NEITHER triggers nor actions, so every write
  path needs its own branch, and there are three:
  - `validate_automation_payload` accepts the shape (`path` present, `input` a
    mapping). Whether the inputs satisfy the blueprint is the blueprint's own
    schema question, answered by HA at reload — restating it here would go stale.
  - `prepare_write_payload` strips **both** trigger/action key spellings: HA
    merges a surviving `actions` OVER the substituted config (empty invalidates,
    populated silently replaces), and a payload with both shapes is ordinary
    after converting one in the YAML editor.
  - `async_create_automation` copies `use_blueprint` into the entry instead of
    the default empty `triggers`/`actions`. This one fails SILENTLY — HA logs the
    invalid item at reload while the function returns success — so **assert the
    written YAML**, not `result["success"]`.
- **The outer fields are still validated.** `mode`, `initial_state` and the YAML
  round-trip check apply to a blueprint instance too; `_finalize_payload` is the
  one implementation both branches end in. `prepare_write_payload` COPIES the
  normalized outer fields onto the payload it writes (otherwise
  `mode: " Restart "` validates as `restart` and is written unchanged). `id` and
  `initial_state` stay out — `apply_managed_fields` owns them.
- **Risk assessment records `blueprint_unassessed` as a scrutiny tag, not a
  flag.** The actions live in the blueprint file, but any flag forces `elevated`,
  landing every blueprint automation disabled for a file the user installed
  themselves; a bare `normal` would claim we looked.
- **Only an AUTOMATION blueprint can back an automation.** `automations.yaml`'s
  loader searches only the automation store, so a script/template blueprint path
  writes an entry HA rejects at reload. `_blueprint_path_error` enforces it inside
  `prepare_write_payload` — **async for that reason** — so no write path (the YAML
  editor reaches the update path) can skip it. Membership is not loadability: a
  malformed or wrong-domain file is the EXCEPTION in place of the blueprint, so
  the value is checked. Skipped when blueprints are not set up — a missing store
  is not evidence the path is wrong.
- **`get_blueprint` returns selectors and required-ness** ("required" = no
  `default`). Neither is in the listing, and composing without them is guessing.
- **A blueprint that fails to parse is reported, not dropped** —
  `async_get_blueprints` returns the exception in place of the blueprint.
- **The reads are admin-gated, like HA's own** (`blueprint/list` is
  `require_admin`): a blueprint carries its source URL and input defaults. Same
  reasoning as `get_logs` / `get_automation_traces` — read-only is not
  unprivileged.
- **Import and delete are MCP-only, behind a confirmation** (`blueprint_import.py`).
  Fetching YAML from a model-chosen URL and writing it to the config directory is
  a different risk class, so:
  - Only HA's DEDICATED readers fetch (forum, GitHub, gists, HA website), each
    called directly by host, over https. Never `fetch_blueprint_from_url`: it
    falls through to the generic reader — even for an allowed host's URL it does
    not recognise — which follows redirects and re-resolves the hostname, so no
    address check made beforehand keeps it out of the home network.
  - The result is re-validated with the STORE's own domain schema before the
    preview: the importer checks only the generic schema and the store writes
    what it is given, so an automation blueprint without actions would "import"
    and then fail to load.
  - The `content_hash` is the whole SHA-256 — the source picks the text, and a
    truncated digest is short enough to find two texts sharing it.
  - The first call writes nothing and returns a `content_hash` of the fetched
    text; the confirmed call refetches and writes only if the hash still matches.
  - **An existing blueprint is replaced only with `overwrite`** — the only way an
    author's new version reaches what is built on it, since delete refuses a
    blueprint in use. The preview's `replaces` names the users and
    `would_break`: each user's stored inputs against the new version's required
    ones (no default), the set `BlueprintInputs.validate` refuses — computed from
    `inputs_with_default`, since `MissingInput` keeps no names. A user's inputs
    are its `_blueprint_inputs` — NOT `raw_config`, which for a blueprint
    automation or script is the expanded config without `use_blueprint`; a user
    whose inputs cannot be read is `unchecked` and blocks like a break.
  - A confirmed overwrite is refused while anything would break, or if the
    file's hash (`replaces_hash`) changed since the preview. The hash check and
    the write are ONE executor job (`_replace_if_digest`, temp file +
    `os.replace`), then the store's cache is set and its users reloaded as
    `async_add_blueprint` would — through its private `_blueprints` /
    `_reload_blueprint_consumers` (present from 2025.1).
  - **The save path must stay inside the blueprints folder** (`_inside`, plus
    HA's `raise_if_invalid_path`): the GitHub importer builds the name from the
    URL's last segment AFTER percent-decoding, so `..%2F` arrives as `../` — a
    file landing in `packages/` is configuration, not a blueprint.
  - Delete names what uses the blueprint (HA refuses one in use) or asks first.
    It checks the FILE exists rather than loading it — a blueprint that fails to
    parse is listed, and is exactly the kind worth deleting.
  - Not in chat: there it would want a card showing the source.
