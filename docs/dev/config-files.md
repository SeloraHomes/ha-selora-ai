# Files over MCP

`selora_list_files` / `selora_read_file` / `selora_write_file` /
`selora_delete_file` (`config_files.py`, handlers in `mcp_server/files.py`) reach
the files no API stores: `www/` (served at `/local/`), `themes/`,
`custom_templates/`, `dashboards/`, and `blueprints/` read-only. Modelled on
ha-mcp's file tools. All four are admin-only.

- **Configuration YAML is not reachable here.** `configuration.yaml` and
  packages belong to `config_yaml` (`config-yaml.md`), whose allowlist,
  preview and configuration check a raw write would bypass.
- **Paths are plain segments**, checked before joining (`_SEGMENT_RE`: no `..`,
  no leading dot, max depth), no segment may be a symlink, the resolved path
  must stay in its folder, and `secrets.yaml` / `.storage` never resolve. The
  symlink check runs again inside each file operation, right before the open or
  replace; a folder swapped for a link in between is caught. The race is
  narrowed, not closed — closing it means descriptor-relative traversal, and
  swapping a folder needs write access to the config folder, which already
  reaches `configuration.yaml`.
- **Browser code in `www/` needs `confirmed: true`** (`.js`, `.mjs`, `.html`,
  `.svg` …, and `.css` — registered as a `css` resource it styles every page,
  and `url()` lookups can carry what it reads off the page). `selora_add_dashboard_resource` adds a `/local/` path without
  confirmation because the file was put there by HACS or the user; one this
  tool writes was put there by the agent, so writing, then registering, would
  skip the confirmation an external URL needs. The `approval_required` opt-out
  is honoured.
- **Text only, 1 MB per write.** Reads go by BYTE offset and read only the
  chunk (decoding the whole file per page made paging quadratic); an
  incremental decoder stops short of a split character, so `next_offset` is
  always on a character boundary.
- **Only a listing names a root folder.** A write to `www` while the folder is
  missing would create a FILE there and block every `/local/` file after it.
- **Replacing needs `overwrite: true`; replace and delete back up first**
  (`.selora_ai/file_backups/`, last 5 per file). New files are 0644 — the web
  server and HA read them.
- **The write machinery is `fs_safety.py`**, shared with `config_yaml`:
  exclusive creation, `mkstemp` + compare-then-`os.replace`, exact-byte reads,
  "missing" kept distinct from "empty", symlink-free backup folders (created
  tolerating a concurrent creator, then checked to be real directories), and backup
  names percent-encoded from the path (`a__b` and `a/b` must not share a
  history — pruning one deleted the other's backups).
