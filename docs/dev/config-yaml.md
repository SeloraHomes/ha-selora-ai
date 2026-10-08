# Editing configuration YAML over MCP

`selora_get_config_yaml` / `selora_set_config_yaml` (`config_yaml.py`) reach what
no API does: YAML-only integrations, package files, themes. Modelled on ha-mcp's
`ha_config_set_yaml`, because a wrong write can keep Home Assistant from booting.
Admin-only, always on, MCP only.

- **Three kinds of file, checked twice.** `configuration.yaml`, one file directly
  in the packages folder that `homeassistant: packages: !include_dir_named
  <folder>` names — no default (HA loads no packages folder unless configured,
  and a guessed one takes writes that report success and never apply), only a
  RELATIVE folder (an absolute one is not under the config folder), and not the
  `!include_dir_merge_named` form, whose files are mappings of package names
  rather than the integration keys this tool writes — and
  `themes/<name>.yaml`. Names are checked as plain `[a-z0-9_-]` before joining,
  and the joined path again after symlinks resolve. `secrets.yaml` and `.storage`
  are unreachable by construction.
- **ha-mcp's key allowlist** (`_ALLOWED_KEYS`), `automation` / `script` / `scene`
  in package files only, a theme's name in a theme file, and ONE nested path:
  `frontend.themes`, whose value must be exactly `!include_dir_merge_named themes`
  — the include that makes the themes folder load, which ha-mcp cannot add. Not
  `!include_dir_named`: it wraps each file in its name, and the theme files this
  tool writes are already keyed by the theme, so the frontend would drop them. `homeassistant`, `http` and the rest of `frontend`
  stay out.
- **A key whose value is an `!include` is refused** — its content is in the other
  file, and replacing the tag would orphan it.
- **Two steps.** Without `confirm_token` nothing is written; the result carries
  the diff and a token = hash(path, current file bytes, action, key, content). The
  same request with that token writes; a changed file or request gets a fresh
  preview. The returned diff is capped (`diff_truncated`); the token still binds
  the whole edit. MCP has no card — this is the confirmation.
- **Round-trip without reformatting.** ruamel keeps comments, key order and HA's
  tags, but NOT indentation: a file is re-dumped in the first style in `_STYLES`
  that reproduces it byte for byte, or every list in `configuration.yaml` would
  be re-indented and bury the change in its own diff.
- **No symlinks, file or folder.** One inside the config folder passes the
  boundary check while aliasing `secrets.yaml` or `.storage` (a `themes` link to
  the config folder makes `themes/secrets.yaml` the real one). `secrets.yaml` and
  `.storage` are also refused by name after resolving.
- **Compare-and-replace is one synchronous step** (`_replace_if_unchanged`), after
  everything awaited (the baseline check, the backup). `_WRITE_LOCK` serialises
  this tool only; the user, HA's editors or a git pull can save meanwhile, and a
  re-read followed by an awaited write still loses their change. A mismatch
  aborts with nothing written. The rollback uses the same step. The temp file
  is written BEFORE the compare, so only compare → `os.replace` remain; that
  narrows the window, it does not close it — a filesystem has no
  compare-and-swap against writers that take no lock.
- **The new bytes go to a `mkstemp` file** (unique, exclusive) and replace the
  file atomically; backups open with `O_EXCL | O_NOFOLLOW`. A predictable temp
  name could be a planted symlink, and writing through it escapes the folder
  `resolve_file` verified.
- **Bytes as they are on disk.** Files are read and written with `newline=""`:
  universal-newline reading turns CRLF into LF, so the token and the conflict
  check would not be bound to the real bytes, and a one-key edit would convert
  the whole file. Edits are made on the LF form and written back in the file's
  own line endings.
- **Backup, check, roll back.** The file is copied to
  `.selora_ai/config_backups/` (last 10 kept) before the write. HA's
  `async_check_ha_config_file` runs before and after; problems the edit
  INTRODUCED roll it back. **Warnings count**: an invalid integration block is a
  warning (HA boots without that integration), errors are core-only — comparing
  errors alone missed exactly what the check is for. Positions (`line 12`) are
  stripped before comparing, since an edit moves the problems already there,
  and problems are COUNTED (`Counter`), not a set — without positions a second
  `rest` entry with the first one's mistake reads identically. A rollback
  restores only what this edit wrote: if the file no longer matches the new
  text (someone saved during the check), it is left alone and the error names
  the backup.
- **The backup folder must be real directories at every level** (`_backup`): a
  planted `.selora_ai` symlink would take the unmasked copy outside the folder.
- **Owner-only.** Backups (and their folder, 0700) and new files are created
  0600 with `os.open`, never wider first; a rewrite keeps the file's mode — a
  0600 `configuration.yaml` must not come back 0644.
- **Backups are read masked and restored without leaving the hub.**
  `backups=true` lists a file's backups and `backup` reads one, masked like the
  file. `action: restore` writes one back WHOLE, through the same preview,
  token, check and rollback: the token binds the backup's bytes (its name
  stands in for the key), and the caller never handles the text. Handing it
  back through the caller would not work, because a masked read can't put the
  credentials back. A restore reloads what it changed when every changed key
  has a reload service, and otherwise reports `restart_required`.
- **Parse errors give position and a redacted problem, never `str(exc)`**:
  ruamel quotes the failing source line, and its problem text can quote values
  (a duplicate key names both), so quoted parts are dropped.
- **Check diagnostics are redacted before they are returned** (`_redact_problem`):
  HA's messages quote the rejected configuration (`got {'password': ...}`), so
  the `got …` tail and value literals are dropped; what is wrong and the key
  path (`@ data['rest'][0]`) survive. Comparison uses the raw messages.
- **Activation.** A theme file is called live only if `configuration.yaml` loads
  the themes folder (`themes_folder_not_loaded` with a hint otherwise —
  `reload_themes` succeeds either way, loading nothing). Themes and
  `frontend.themes` run `frontend.reload_themes`; keys
  in `_RELOAD_SERVICES` run their reload; anything else reports
  `restart_required`. A reload failing after the write is reported, not raised.
- **Credentials are masked STRUCTURALLY** (`_mask_tree`): the YAML is parsed and
  every value under a key naming a credential (`_SENSITIVE_KEY_RE`, hyphens read
  as underscores for header names like `X-API-Key`) is replaced. Only
  REFERENCES are kept (`_is_reference`: `!secret`, the `!include` family) — not
  every tag: `!env_var NAME fallback` carries a literal fallback. Line matching was tried first and
  each round missed a shape — block scalars, unindented lists, flow style
  (`headers: {Authorization: Bearer x}`) — that the parser sees as plain
  values. Diffs are taken between the two masked documents, since a hunk's
  context can start inside a credential whose key is above it — the new one
  masked against the old, so a changed credential reads `*** (changed)` and a
  new one `*** (new)`: masking both to `***` made a password-only edit an
  empty diff, a confirmation for a change nobody could see. Still a best
  effort by key name, which is why reading is admin-gated too.
- **`add` never overwrites** — not a mapping entry, and not `frontend.themes`,
  which may hold inline themes the include would drop.
- **Tests use a private `tmp_path` config folder.** PHCC's `testing_config` is
  shared by every test (and every pytest process) in the venv.
