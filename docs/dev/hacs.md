# HACS over MCP

`selora_hacs_search` / `_info` / `_install` / `_remove` / `_add_repository`
(`hacs_bridge.py`, handlers in `mcp_server/hacs.py`) manage HACS repositories:
dashboard cards (category `plugin`), themes, integrations. All admin-only, as
every HACS command is `require_admin`.

- **Through HACS's websocket commands, in-process.** HACS has no service API;
  its panel drives it over `hacs/*` commands. `_call` looks the handler up in
  `hass.data["websocket_api"]` (as `registered_storage_collection` does),
  validates the message with the command's own schema, and calls it with
  `_InProcessConnection`, a stand-in that settles one future from
  `send_message` / `send_result` / `send_error` / `async_handle_exception`.
  That is HACS's frontend protocol — the most stable surface it has — not its
  private Python objects. Each command has a timeout (a download fetches from
  GitHub).
- **Only `_COMMANDS`.** It is not a generic websocket client; a tool picks an
  operation, never a command name.
- **Risk by category, always confirmed** (honouring the `approval_required`
  opt-out): installing a card or theme is third-party code every browser runs —
  the same bar as an external dashboard resource; an integration, AppDaemon app
  or python_script is third-party Python running inside Home Assistant after a
  restart; a custom repository trusts whoever controls it on GitHub. The
  confirmation's `reason` says which. Removing runs at once, like other removals
  over MCP.
- **Repositories are named by id or `owner/repo`**, resolved through
  `hacs/repositories/list` — the id is what HACS's commands take.
- **GitHub text is untrusted** (names, descriptions, topics, authors, releases)
  and sanitized on the way out.
- **Tests drive a fake HACS registered with HA's real decorators**
  (`websocket_command`, `require_admin`, `async_response`), so the schema, the
  admin check against the stand-in user and the background-task reply all run
  as they do for HACS.
