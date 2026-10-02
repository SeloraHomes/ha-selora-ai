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
- **Import is deliberately absent.** Fetching YAML from an LLM-chosen URL and
  writing it to the config directory is a different risk class; it belongs behind
  a confirmation card naming the source.
