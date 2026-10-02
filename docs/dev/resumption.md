# Resumption — continuing after a confirmation

A turn that proposes something ENDS there (the tool loop short-circuits on
`requires_approval`), so "create a scene **and add it to the dashboard**" used to
produce the first half and silently drop the second. Resumption is the general
fix.

- **The confirmation is the re-entry point, and has to be** — the second half
  needs the thing the user has not yet tapped into existence.
- **It re-enters `selora_ai/chat_stream` with `resume_proposal_id`** — one
  streaming implementation, with tool steps shown like any turn.
- **The client sends an id and nothing else.** Directive, approval status and
  depth are read from the STORED proposal, so a panel cannot resume an unapproved
  card or slip past the cap.
- **Two doors, one resolver.** `_resume_request` answers for a `command_approval`
  proposal_id (client actions, service approvals, deletes) and for a `scene_id`
  (a scene is accepted from its own card). Refused on a denial, an unsaved scene
  and an unknown id.
- **A declaration is preferred, not required.** `remaining_intent` on a carded
  tool result (lifted once in `ToolExecutor.execute`) or beside a scene block says
  exactly what is left. Models routinely announce the follow-up in prose and leave
  it unset, so when absent the user's own last message is REPLAYED with a note
  that the proposal now exists, and the model decides what remains. That directive
  says plainly a short confirmation is right when the proposal was the whole
  request — told only to "continue", a model invents work.
- **The replayed request is RECORDED on the proposal** (`origin_request`, written
  at append time). Walking back through the session is wrong once pruning kept the
  first message pinned and dropped the proposal's own request — the scan would
  replay an unrelated request from the start of the conversation. The scan remains
  as the fallback for proposals written before the field.
- **The cap is once**: a card proposed during a resumed turn is stamped
  `resume_depth: 1` and refused thereafter, and a resumed turn persists no
  `remaining_intent`.
- **The directive is never persisted** (`_persist_user_turn` skips it) — it would
  put server-written words in the user's mouth in their own transcript.
- **`remaining_intent` must not reach MCP** — it is in `_PANEL_ONLY_PARAMS`; an MCP
  client using it would wait for a resumption that never comes.
