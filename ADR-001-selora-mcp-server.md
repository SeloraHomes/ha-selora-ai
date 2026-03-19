# ADR-001: Expose Selora AI as a Model Context Protocol (MCP) Server

**Status:** Proposed
**Date:** 2026-03-18
**Deciders:** Matthew Blackmon, Gunnar Beck Nelson, Philippe Lafoucrière

---

## Context

Selora AI currently exposes its intelligence (pattern detection, automation suggestions, chat, home state) exclusively through a custom HA panel UI and WebSocket commands. There is no way for external AI agents — Claude Desktop, Claude Code, custom agentic workflows — to query Selora's data or invoke its capabilities.

Home Assistant already ships two built-in MCP integrations:
- `mcp` (client): connects HA to an external MCP server, pulling in tools.
- `mcp_server` (server): exposes HA's LLM API tools (entity control, querying) at `/api/mcp` over the standard MCP protocol (`mcp==1.26.0`).

Selora AI is built on top of HA and its pattern store, suggestion store, and LLM client represent a distinct intelligence layer — not available through HA's own MCP server. The opportunity is to expose that layer as a first-class MCP server, making Selora's capabilities callable by any MCP client.

**Forces at play:**
- End-of-month release milestone (Philippe's "safe release by Friday" expectation)
- Pattern engine (8 MRs) not yet merged to `main` — tool surface depends on it landing
- Security posture is a live concern (Philippe's Claw power user conversation 3/17); any new endpoint must use existing auth infrastructure
- `mcp==1.26.0` is already pinned and available in the HA venv — no new third-party risk
- The `HomeAssistantView` auth model (Bearer token) is well-understood and already used by HA's own `mcp_server`

---

## Decision

**Implement a dedicated Selora AI MCP server** as a new HTTP endpoint at `/api/selora_ai/mcp`, using the same MCP SDK and transport pattern as HA's built-in `mcp_server` component. Selora registers its own `HomeAssistantView` subclass and `mcp.Server` instance, independent of HA's `mcp_server` being installed.

This makes Selora's pattern intelligence, suggestions, and automation management callable from any MCP client with a valid HA long-lived access token.

---

## Options Considered

### Option A: Own Selora MCP endpoint (Recommended)

Register a `HomeAssistantView` at `/api/selora_ai/mcp` using the MCP SDK's `Server` class directly — the same pattern HA uses in `mcp_server/http.py` and `mcp_server/server.py`.

| Dimension | Assessment |
|-----------|------------|
| Complexity | Medium — ~200 lines; well-defined SDK pattern to follow |
| HA dependency | None beyond existing `http` and `conversation` deps |
| Auth | `HomeAssistantView.requires_auth = True` (default) — Bearer token, same as HA's own endpoint |
| Tool surface | Full control — Selora-branded tool names, Selora-specific data |
| Standalone | Yes — works whether or not user has `mcp_server` installed |
| SDK version risk | Low — `mcp==1.26.0` already in HA venv, pin to same version |

**Pros:** Selora tools are clearly namespaced and discoverable. No coupling to HA's `mcp_server` config. Works with Claude Desktop, Claude Code, or any standards-compliant MCP client. Future-proof as the ecosystem grows.

**Cons:** Need to maintain MCP SDK compatibility independently of HA's own component. Adds ~100 lines of transport boilerplate.

---

### Option B: Register a custom `llm.API` and rely on HA's `mcp_server`

Selora registers a `selora_ai` LLM API using HA's `homeassistant.helpers.llm` framework. Users then configure HA's `mcp_server` integration to use this API.

| Dimension | Assessment |
|-----------|------------|
| Complexity | Low — only need to subclass `llm.API` |
| HA dependency | Requires user to install and configure `mcp_server` separately |
| Auth | Handled by HA's mcp_server |
| Tool surface | Selora tools appear mixed with HA Assist tools unless user configures carefully |
| Standalone | No — Selora tools only accessible if `mcp_server` is set up |

**Pros:** Almost no transport code. Auth and HTTP handling free.

**Cons:** Creates a hard user-facing dependency — Selora's MCP value is invisible unless they've also set up `mcp_server`. Reduces the standalone story. Tool names appear in a shared namespace alongside HA's own tools. Not viable as a HACS differentiator.

---

### Option C: New WebSocket commands only (no MCP)

Add structured WebSocket commands (`selora_ai/list_patterns`, `selora_ai/accept_suggestion`, etc.) to expose the same data over Selora's existing WebSocket API.

| Dimension | Assessment |
|-----------|------------|
| Complexity | Very low — follows existing WebSocket command pattern |
| External client support | None — WebSocket commands only work from HA UI, not Claude Desktop |
| Auth | Existing `_require_admin` check |
| MCP ecosystem | Zero — not actual MCP, won't work with any external MCP client |

**Verdict:** Reasonable as a complementary internal API, but does not unlock the MCP opportunity. Not a substitute.

---

## Trade-off Analysis

Option A requires writing MCP transport boilerplate (~100 lines based on `mcp_server/http.py`) and pinning `mcp==1.26.0` in the manifest. That is a concrete cost.

The payoff is that Selora becomes independently accessible from external AI agents without any user setup beyond generating a long-lived access token — the same flow HA already documents for its own API. Option B saves the boilerplate but only delivers value if the user has also installed and configured `mcp_server`, which is an additional friction point that many HACS users won't clear.

Given that the pattern engine branches are not yet on `main`, the timing works: implement Option A now, targeting the tools that are already on `main` (automations, chat sessions, home snapshot), then expand the tool surface once the pattern engine merges.

---

## Tool Surface

The MCP server exposes the following tools. Phase 1 uses data already on `main`. Phase 2 requires the pattern engine merge.

### Phase 1 — Available now

| Tool name | Input | Returns |
|-----------|-------|---------|
| `selora_list_automations` | `status?: "pending"\|"enabled"\|"disabled"\|"deleted"` | Array of automation objects with id, alias, status, risk_assessment |
| `selora_get_automation` | `automation_id: string` | Full automation with YAML, version history, lineage |
| `selora_accept_automation` | `automation_id: string` | Confirms the automation, enables it in HA |
| `selora_delete_automation` | `automation_id: string` | Soft-deletes the automation |
| `selora_get_home_snapshot` | _(none)_ | Current entity states grouped by area |
| `selora_chat` | `message: string, session_id?: string` | LLM response, optionally creates automation |
| `selora_list_sessions` | _(none)_ | Recent chat session titles and IDs |

### Phase 2 — After pattern engine merges

| Tool name | Input | Returns |
|-----------|-------|---------|
| `selora_list_patterns` | `type?: "time_based"\|"correlation"\|"sequence"`, `min_confidence?: float` | Detected patterns with confidence scores, entity IDs, evidence |
| `selora_list_suggestions` | `status?: "pending"\|"accepted"\|"dismissed"` | Proactive automation suggestions with YAML and risk assessment |
| `selora_accept_suggestion` | `suggestion_id: string` | Creates automation from suggestion, marks accepted |
| `selora_dismiss_suggestion` | `suggestion_id: string, reason?: string` | Marks suggestion dismissed |
| `selora_trigger_scan` | _(none)_ | Runs an immediate pattern detection scan |
| `selora_get_pattern` | `pattern_id: string` | Full pattern detail including evidence and timestamps |

---

## Implementation Plan

### Step 1 — New module `mcp_server.py` in `custom_components/selora_ai/`

```
custom_components/selora_ai/
└── mcp_server.py   ← new
```

This module owns:
- `SeloraAIMCPView` — subclass of `HomeAssistantView` at `/api/selora_ai/mcp`
- `create_selora_server(hass, context)` — creates and registers the `mcp.Server` with tool handlers
- All tool handler functions (one per tool in Phase 1 above)

The HTTP view follows `mcp_server/http.py` verbatim for the request/response cycle (stateless, single-request-per-server-instance pattern). The key difference is the server is initialized from Selora's stores rather than `llm.async_get_api`.

### Step 2 — Wire into `__init__.py`

In `async_setup_entry`, after stores and LLM are initialized:

```python
from .mcp_server import register_mcp_server
register_mcp_server(hass, entry, automation_store, pattern_store, llm_client)
```

`register_mcp_server` calls `hass.http.register_view(SeloraAIMCPView(...))`. This is the same pattern Selora already uses for static paths.

### Step 3 — Update `manifest.json`

Add `mcp==1.26.0` to `requirements`. Add `http` to `dependencies` (currently only `conversation`). No other manifest changes needed.

```json
{
  "dependencies": ["conversation", "http"],
  "requirements": ["ruamel.yaml>=0.18.0", "mcp==1.26.0"]
}
```

### Step 4 — Auth

`HomeAssistantView` enforces `requires_auth = True` by default — all requests must include `Authorization: Bearer <long-lived-access-token>`. No additional auth code required. The existing `_require_admin` check from WebSocket handlers is **not** needed here because the Bearer token auth already scopes access to the token holder's permission level. MCP clients use the same token flow as HA's own REST API.

### Step 5 — Testing with Claude Desktop

Configure Claude Desktop's MCP server list:

```json
{
  "mcpServers": {
    "selora-ai": {
      "url": "http://homeassistant.local:8123/api/selora_ai/mcp",
      "headers": {
        "Authorization": "Bearer <long-lived-token>"
      }
    }
  }
}
```

The `selora_list_automations` tool should be callable immediately. This becomes the integration smoke test.

---

## Prompt Injection Consideration

Entity names and automation aliases flow into tool responses. Any MCP client that uses these values to prompt an LLM inherits the same prompt injection risk Matthew's commit `6b3fc05` addressed for the chat interface.

The tool response handlers should apply `_sanitize_untrusted_text()` (already in `llm_client.py`) to all string fields sourced from user data — entity friendly names, automation aliases, pattern descriptions — before returning them in MCP tool results. This ensures the sanitization boundary holds regardless of which interface the data exits through.

---

## Consequences

**What becomes easier:**
- External AI agents (Claude Desktop, custom scripts, CI pipelines) can query Selora's pattern data and act on suggestions without any UI interaction
- Selora becomes a genuine platform, not just a panel
- HACS differentiation — no other HA integration exposes pattern intelligence over MCP
- Future multi-home aggregation (Direction 4) has a clear integration point

**What becomes harder:**
- SDK version coupling: if HA upgrades `mcp` beyond `1.26.0`, Selora must track it
- Tool surface needs maintenance as the pattern engine and automation store evolve
- Any new tool that writes data (accept, dismiss) needs to consider concurrent access with the WebSocket handlers

**What to revisit:**
- After 30 days of real use, evaluate whether the read-only tools (list, get) are used more than the write tools (accept, dismiss) — this informs how much to invest in Phase 2 vs. optimizing Phase 1
- If HA introduces a first-class "custom LLM API" registration pattern (currently only `homeassistant.helpers.llm` internals), evaluate migrating to Option B for the write tools while keeping the dedicated endpoint for the read tools

---

## Action Items

1. [ ] Confirm pattern engine MRs (selora-ai-21 through selora-ai-28) target merge date — Phase 2 tool surface depends on it
2. [ ] Implement `mcp_server.py` with Phase 1 tools (~200 lines, follow `mcp_server/http.py` pattern)
3. [ ] Update `manifest.json` — add `http` dependency, `mcp==1.26.0` requirement
4. [ ] Wire `register_mcp_server()` into `async_setup_entry` in `__init__.py`
5. [ ] Apply `_sanitize_untrusted_text()` to all string fields in tool responses
6. [ ] Smoke test with Claude Desktop using a long-lived access token
7. [ ] Document the token setup in the README / `SeloraHomes.com/docs/selora-ai` — this is the one user-facing setup step
8. [ ] After pattern engine merge: implement Phase 2 tools (`list_patterns`, `list_suggestions`, `accept_suggestion`, `dismiss_suggestion`, `trigger_scan`)
9. [ ] Create GitLab issue for Phase 1 implementation, branch `selora-ai-<N>-mcp-server`
