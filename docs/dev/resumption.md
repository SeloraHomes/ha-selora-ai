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
  gives the model a way to say the proposal was the whole request — told only
  to "continue", a model invents work.
- **A self-contained client action never resumes.** Adding cameras to
  Frigate is the whole request. Replayed, the model called the tool again and
  reported the cameras it had just added as refused; given a
  `remaining_intent`, models declared the add itself as the remainder and the
  resumed turn added the camera again while Frigate was still restarting. Kinds
  in `_SELF_CONTAINED_CLIENT_ACTIONS` (`__init__.py`, mirrored as
  `SELF_CONTAINED` in `client-actions.js`) do not resume at all, their tools
  take no `remaining_intent`, and the panel skips the call so no error bubble
  shows. A real follow-up ("…and put it on the dashboard") is dropped too, on
  purpose: the camera reaches HA only once Frigate is back, a minute after the
  confirm, so a resumed turn would act on an entity that does not exist yet.
- **"Nothing left" is the model's word, not our inference.** With nothing
  outstanding, a replayed turn could only restate the outcome line ("Deleted the
  Cameras dashboard." then "The Cameras dashboard has been removed."). The
  directive asks for `_NOTHING_LEFT` as its last word then; when it ran no tool
  and nothing else is left to show (no words, card or chips), the turn comes back
  `discarded` and nothing is persisted. A turn that ran tools is kept, read or
  write alike, with its steps as the bubble and no "no response" fallback. Inferring it from
  the reply's shape dropped, in turn, an invalid automation awaiting correction,
  a question back to the user, a failed tool call and a blocked command — and a
  bare "Done." became a "no entity matched" clarification under the command
  policy. A resumed turn's prose is never streamed (steps are), so the marker never shows;
  said beside other words it is stripped, and only as a trailing standalone word. `tests/evals/test_resumption.py`
  checks a real model uses it, and still does the rest when there is a rest.
- **A resumed turn raises no synthetic automation spinner.** The directive's
  "If … do that part" reads as a rule to `_is_definite_automation`; an
  automation the turn really proposes still gets the spinner from the
  mid-stream opener check. On a cloud provider it still gets the automation
  idle budget, since what remains is often an automation.
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
