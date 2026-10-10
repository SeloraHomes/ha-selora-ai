# Next-message prediction

After a panel turn ends, the panel asks `selora_ai/predict_next_prompt` for the
message the user will most likely send next and shows it as the empty
composer's placeholder; Tab, → or the small "Tab" button fills it in. It never
sends on its own.

- **The prediction is the end of the turn.** The composer stays disabled
  (`isPredicting`, scoped to the session it was asked for) until it is in, so
  the user never starts typing before the suggestion lands;
  `PREDICT_TIMEOUT_MS` caps the wait so a hung provider cannot hold it. A
  resumption (accepting a card) is not held.
- **Silence is the default.** A wrong guess on every turn is noise, so the
  model returns a confidence and anything below `MIN_CONFIDENCE` is dropped.
  Tune the bar or the prompt (`next_prompt.py`), never add a rule for one kind
  of follow-up — the model has to infer it from the conversation.
- **A turn that ends on buttons is not predicted.** A pending approval,
  quick actions or a proposal awaiting Accept mean the next move is a click;
  the call is skipped. Accepting a card asks again, from its saved state.
- **The model always guesses; the bar decides.** Offered a "nothing" answer,
  the gateway's small model takes it on every turn, so the prompt has none and
  `MIN_CONFIDENCE` filters. A declined card is described as the user's
  decision, or the model reads "not saved" as unfinished work.
- **It belongs to the reply it followed** and is never stored. The panel shows
  it only while that reply object is the last message in the active session,
  so a new turn, an appended approval result or a session switch retires it
  without any clearing code.
- **Provider default.** Unset means on for hosted providers, off for Ollama
  (local compute per turn); Selora AI Local never predicts (no free prose).
  The toggle saves immediately and is read per request, so the advanced
  settings Save must not send it — that would pin the provider default.
- Panel only: Assist and MCP turns never ask.
