---
name: selora-mcp
description: Uses the Selora AI MCP server to inspect home state, validate/create automations, manage automation lifecycle, and handle proactive pattern/suggestion workflows. Use when users ask to analyze their home, review Selora automations, validate YAML, create/enable/delete automations, list or act on suggestions/patterns, trigger a scan, or continue Selora chat sessions.
metadata:
  mcp-server: selora-ai
  phase: 1
  version: 2.0.0
  compatibility: claude-code,codex,openclaw
---

# Selora MCP Skill

## Purpose
Use Selora MCP tools to move from read-only context to validated and explicitly approved home automation changes.

## Preconditions
1. Selora MCP server is reachable at `/api/selora_ai/mcp`.
2. A valid Home Assistant Bearer token is configured.
3. For admin-gated or mutating tools, caller has Home Assistant admin privileges.

If preconditions fail, stop and report what is missing.

## Trigger Phrases
Activate this skill when requests include language such as:
- "analyze my home"
- "show/list my Selora automations"
- "validate this automation YAML"
- "create/enable/delete this automation"
- "list suggestions" or "show proactive suggestions"
- "list patterns" or "show detected patterns"
- "accept/dismiss this suggestion"
- "run/trigger a Selora scan"
- "continue my Selora chat/session"

## Do Not Trigger For
Do not activate this skill when the request is primarily about:
- Generic Home Assistant troubleshooting unrelated to Selora MCP tools
- Frontend/UI styling, dashboard layout, or Lovelace card design changes
- Non-Selora automation management unless explicitly requested to migrate into Selora flow
- Pure documentation writing that does not require MCP tool execution

## Tool Surface
Read/context tools:
- `selora_get_home_snapshot`
- `selora_list_automations`
- `selora_get_automation`
- `selora_validate_automation`
- `selora_list_sessions`
- `selora_chat` (admin-gated, stateful)
- `selora_list_patterns`
- `selora_get_pattern`
- `selora_list_suggestions`

Mutating/admin-gated tools:
- `selora_create_automation`
- `selora_accept_automation`
- `selora_delete_automation`
- `selora_accept_suggestion`
- `selora_dismiss_suggestion`
- `selora_trigger_scan`

## Identifier Integrity Rules
1. Never invent `automation_id`, `session_id`, `pattern_id`, or `suggestion_id` values.
2. Resolve IDs from tool output only.
3. If target identity is ambiguous, ask for clarification before mutating calls.

## Safe Call Sequencing

### A) Home context and discovery
1. Call `selora_get_home_snapshot` first.
2. Use `selora_list_automations` / `selora_get_automation` for automation context.

### B) YAML-first automation flow
1. Call `selora_validate_automation` with external YAML.
2. If invalid, return errors and stop.
3. If valid, show normalized YAML + risk.
4. Ask explicit confirmation.
5. Call `selora_create_automation` with `enabled=false` unless user explicitly requests immediate enablement.
6. Optionally call `selora_accept_automation` after explicit approval.

### C) Natural-language automation flow
1. Call `selora_chat` with `message` (and `session_id` if continuing).
2. If automation YAML is returned, summarize risk and ask for explicit approval.
3. Only then call `selora_create_automation` or `selora_accept_automation`.

### D) Pattern/suggestion flow
1. Optionally call `selora_trigger_scan` when user asks for fresh results.
2. Call `selora_list_suggestions` and/or `selora_list_patterns`.
3. If needed, call `selora_get_pattern` for full detail.
4. Ask explicit confirmation before `selora_accept_suggestion` or `selora_dismiss_suggestion`.

## Admin and Write Boundaries
Rules:
1. Never execute mutating tools without clear user authorization in the current thread.
2. For create/accept operations, default to disabled-by-default unless explicitly requested otherwise.
3. For delete/dismiss operations, require explicit confirmation.
4. If auth/admin checks fail, stop and report.

## Confirmation Protocol
Use explicit confirmations such as:
- `Yes, create automation <automation_id or alias>.`
- `Yes, enable automation <automation_id>.`
- `Yes, delete automation <automation_id>.`
- `Yes, accept suggestion <suggestion_id>.`
- `Yes, dismiss suggestion <suggestion_id>.`

If intent is implied but not explicit, ask and wait.

## Risk-Gated Policy
1. Always surface `risk_assessment` before mutating automation or suggestion actions when available.
2. If risk is high or safety flags are present, require a second confirmation.
3. If risk data is missing, state that and require confirmation.

## Don't Do This
1. Do not skip validation for externally provided YAML.
2. Do not invent IDs.
3. Do not bulk-mutate without explicit user request.
4. Do not silently enable automations after create.
5. Do not mutate non-Selora automations through Selora lifecycle tools.

## Output Contract for Agents
After tool calls:
1. State what was read or changed.
2. Include key IDs.
3. Include risk summary for any proposed or accepted automation.
4. Before mutating calls, provide a concise confirmation prompt.

## Platform Notes
- Compatible with Claude Code, Codex, and OpenClaw.
- If runtime supports `allowed-tools`, align it with this skill’s read-first and confirmation-gated mutation policy.

## Synchronization Source of Truth
Canonical source file:
- `.claude/skills/selora-mcp/SKILL.md`

Mirror targets that must remain identical:
- `.codex/skills/selora-mcp/SKILL.md`
- `.openclaw/skills/selora-mcp/SKILL.md`
