# Answer length

A how-to answer must not run to pages. Asked how to build an alarm panel from
the home's door sensors, the model answered with an entity inventory, a design
and pages of `configuration.yaml` — before checking an alarm panel was even
what the user wanted. The system prompt's PLAN FIRST rule asks for an outline;
models ignore it once a tool has handed them device data.

- **A "how can I…?" turn gets its answer shape on the turn itself.**
  `_is_howto_request` (`llm_client/intent.py`; interrogative phrasing only,
  en/fr/de/es/it — an imperative asks for the thing done) appends
  `_HOWTO_DIRECTIVE` LAST in the current user message: the approach in three
  sentences, confirm the direction (offer to set it up, or ask the one deciding
  question), quick_actions, stop. Recency is what the model weighs; the
  ground-truth block rides in the same place for the same reason.
- **Past the prose budget the stream is abandoned, not finished.**
  `_stream_request_with_tools` counts the answer's characters
  (`CHAT_PROSE_BUDGET_CHARS`, or `CHAT_HOWTO_BUDGET_CHARS` on a how-to turn).
  Past it, reading stops — every further token is paid for and discarded —
  `STREAM_RESET` is yielded, and one more round runs with no tools,
  `CHAT_CONDENSE_MAX_TOKENS` and `_CONDENSE_DIRECTIVE`, which hands back the
  partial so the model condenses rather than starts over. Once per turn: the
  token cap bounds the rewrite.
- **Payload blocks and entity markers are exempt** (`_over_prose_budget`). A
  long automation is the automation's length, and a "which lights are on?"
  answer is long because the home is.
- **`STREAM_RESET` has three consumers.** `_consume_stream_with_guards` zeroes
  its byte count; the chat_stream handler clears `full_text` AND its
  `sent_chars` cursor — left at the discarded answer's length it skips the
  whole (shorter) rewrite and the bubble sits blank until `done` — and sends
  `{"type": "reset"}`; the panel empties the bubble.
- **A cut-off reply is an interruption, not an answer.** An OpenAI-compatible
  stream always ends with a `finish_reason` and `[DONE]`; one that closes with
  neither was cut in between (`last_stream_unterminated`). That, or the output
  cap, on a plain answer sets `validation_error: truncated_response`
  (`mark_cut_prose`), so the panel shows the received text with Retry instead
  of a finished-looking bubble.
- The streamed tool path only. The non-streaming `chat` handler and MCP have no
  budget.
