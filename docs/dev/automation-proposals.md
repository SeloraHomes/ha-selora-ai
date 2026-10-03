# Chat automation proposals

A proposal is written when the user taps **Accept & Save** on the card, and the
panel chooses create vs update from one value: `refining_automation_id`
(`_getRefiningAutomationId` in `automation-crud.js`, which also drives the card's
diff preview). Both chat handlers resolve it **before** appending the assistant
message and pass it to `append_message`, so it rides the turn's `done` payload
*and* persists on the proposal. Persisting is required: nothing recomputes the
target at accept time, so a session reopened before the tap (reload, revisiting
from the sidebar) would otherwise take the create path and write a duplicate.

- **A follow-up change must not become a second automation.** "Change the time to
  7am" after an accepted card is an ordinary proposal; accepting it as a create
  writes a second `automations.yaml` entry under the same alias, and both run.
  `_resolve_proposal_write_target` takes two signals, in order, both scoped to
  what **this session already saved** (`_find_session_saved_automations`): the
  `refine_automation_id` the model
  returned, then the proposal's alias (case- and whitespace-insensitive).
  Explicit refinement (the user loaded an automation to edit) wins outright.
- **The alias and YAML come off disk, never off the chat message.** The message
  records what was PROPOSED: `_acceptAutomationWithEdits` applies the card's YAML
  editor on the way to the write and `set_automation_status` persists only status
  and id, while the Automations tab, version restore and HA's own editor never
  touch the message.
  `_find_session_saved_automation_ids` takes the ids from the `saved_automations`
  index **unioned with** the retained messages (`append_message` prunes a long
  session to its first message plus the latest 99, and the index only covers
  saves since it shipped — the same pair as `_find_active_scenes`).
  `async_yaml_automation_snapshots` then reads each one's current alias and YAML
  from `automations.yaml` in one pass, dropping ids the file no longer carries.
  Reading the message instead silently reverted accept-time edits, and an
  accept-time rename sent the follow-up back to creating a duplicate.
- **A target absent from `automations.yaml` is not a target.** It never enters the
  snapshot list, so a re-proposal of an automation deleted between turns becomes a
  fresh create rather than a failed update (`async_update_automation` fails on a
  missing id). Read off the file, not the registry — an entry whose entity was
  never materialised is still updatable.
- **The model can only claim an id because it was given one.** Every automation
  the session saved rides in the user message as reference data (id, alias,
  current YAML) under `AUTOMATIONS SAVED IN THIS SESSION` (`automation_context`).
  `_automation_reference_context` bounds it (8K chars, newest first) and returns
  **two** lists from one walk — what the model is shown, and which automations an
  inferred edit may target — so inclusion and editability cannot drift apart. One
  whose YAML does not fit is still NAMED, with empty YAML that the prompt renders
  as `_AUTOMATION_TOO_LARGE_NOTE`, but it is NOT editable: a proposal for an
  automation the model never saw is a rule composed from nothing, and writing it
  over the original discards everything the user did not mention. YAML is
  withheld rather than truncated for the same reason.
  `LLMClient.shows_automation_reference` is the same question at provider scope:
  the low-context prompt has no room for YAML, so nothing is editable there. An
  explicit refinement is unaffected (the user named the target and the panel
  diffs it). `_AUTOMATION_REFINE_RULES` (both prompt builders) tells the model to
  start from that YAML, change only what was asked, keep the alias, and name the
  id it is editing.
- **This is reference context, NOT `refining_context`.** The refinement sections
  tell the model it is modifying one specific automation, and a present
  `refining` suppresses command intents for the whole turn
  (`_REFINEMENT_SUPPRESSED_BY_LANG`). Reusing it for every saved automation would
  hijack "now make one for the porch" and swallow "turn the kitchen light on".
  Same split as scenes: `scene_context` is reference, `refining_scene_context` is
  the directive.
- **The claim is checked, never trusted.** `_pop_refine_automation_id` removes it
  from the payload (it is conversation metadata; the payload is re-validated,
  echoed on correction rounds and walked by the risk assessor) and bounds its
  shape; the resolver accepts it only if it names an automation this session
  saved — the model is quoting untrusted text back. A correction round never sees
  the reference context, so `_retry_invalid_automation` carries the original
  claim across rounds, which means **every rejection path must put the claim on
  the envelope**. The JSON-mode path mutates `data` in place and keeps it; the
  streamed path builds a fresh dict and must copy it over — otherwise a corrected
  proposal that also renamed the automation is accepted as a second one.
- **Session-scoped on purpose.** An alias collision with an automation from
  another conversation stays a create: silently overwriting is worse than a
  duplicate. A differently-named proposal with no claim is always a create.
- **A `history: []` override clears it**, alongside `refining` /
  `refining_scene` / `scenes`.
- **MCP asks the same question with different handles.** `selora_chat` takes
  `refine_automation_id` (from `selora_list_automations` or an earlier create),
  resolves it to the on-disk YAML (`_refining_context_for`) and passes it as
  `refining_context` — an external agent naming one target IS the directive case.
  An unresolvable id is **refused**, since ignoring it turns the edit into a
  second automation. The response reports `refine_automation_id`, and
  `selora_create_automation` takes it as `automation_id` to replace, restricted
  to Selora-managed entries (as `_tool_accept_automation` does), because
  `async_update_automation` re-validates through the proposal validator.
  - On MCP, the too-large rule above refuses even an EXPLICIT refinement: there
    is no confirmation card between the revision and the write, so the refusal
    points the caller at reading and editing the YAML itself. The panel's Refine
    keeps its diff, which is why it stays allowed.
  - A non-Selora automation is refused at `_refining_context_for`, since
    `selora_create_automation` would reject the revision anyway.
  - `architect_chat`'s first two arguments are positional (`user_message`,
    `entities`) and `existing_automations` holds records, not alias strings —
    `tests/test_mcp_chat_tool.py` pins the call with `autospec`.
- **A description is prose, so entity_ids are rewritten as friendly names.** It
  heads the card and is the row subtitle in Settings → Automations, and a model
  that reached a device through a tool routinely writes back the raw id.
  `_humanize_description_entity_ids` runs at the two points in `parsers.py` where
  a validated proposal is finalized and replaces only ids the state machine
  resolves (so "at 7 a.m." and non-entities are untouched). **Template spans are
  skipped wholesale**: `_PROSE_ENTITY_ID_RE` matches `{{ … }}` / `{% … %}` /
  `{# … #}` as its own alternative and passes it through, since the id inside
  `states('sensor.temperature')` resolves like prose would. Each opener also
  matches to end-of-string, so an unterminated template shields what follows.

## A rejected proposal is corrected, not guessed at

- **A model that gets a correction round is told what was wrong, with the real
  options.** `_retry_invalid_automation` feeds every validation rejection back
  through `build_service_feedback` — a non-existent service gets the
  integration's real services, an unknown entity the closest real entities
  (`_unknown_entity_feedback`), or the fact that the home has none of that kind,
  with the instruction to create it first or ask rather than substitute. An
  unknown entity used to stop the loop and go straight to a list of every
  device; now the list is only what the user sees if every round fails.
- **The prompt-aware repairs in `parsers` run for the low-context model only**
  (`LLMClient.guesses_repairs`). Swapping an unknown entity for any entity
  sharing a word with the prompt, or rebuilding a trigger from "for 10 minutes"
  / "at sunset" / "below 18", covers the phrasings someone coded for and can
  aim an automation at the wrong device. They were written for Selora AI Local,
  which gets no correction round (`_automation_retry_budget`), and stay there
  until the Allen benchmark shows the local model does as well without them.
