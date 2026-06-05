// Chat and messaging actions (prototype-assigned to SeloraAIArchitectPanel)

import {
  buildEntityMarker,
  pruneStaleSelections,
} from "./chat-autocomplete.js";

export function _quickStart(message) {
  this._input = message;
  this._sendMessage();
}

export function _selectQuickAction(action) {
  const text = action.value || action.label;
  // Command-approval buttons carry a sentinel value of the form
  // ``approve:<scope>:<proposal_id>``. These do not roundtrip through
  // the LLM — they call the resolve_approval WS handler directly so a
  // denied call never runs and an approved call can't be silently
  // rewritten between display and execution.
  //
  // Find the originating message by proposal_id rather than by action
  // reference: the quick-actions normalizer in quick-actions.js
  // returns COPIES of approval actions (mode/icon/tone/description
  // upgraded), so ``msg.quick_actions.includes(action)`` would never
  // match the clicked copy. Without this we'd lose the originatingMsg
  // pointer and the card would never flip to "resolving" / "approved".
  if (typeof text === "string" && text.startsWith("approve:")) {
    const parts = text.split(":");
    if (parts.length >= 3) {
      const scope = parts[1];
      const proposalId = parts.slice(2).join(":");
      let originatingMsg = null;
      for (const msg of this._messages) {
        if (msg.command_approval?.proposal_id === proposalId) {
          msg._qa_used = true;
          originatingMsg = msg;
          break;
        }
      }
      this._resolveApproval(originatingMsg, scope, proposalId);
      return;
    }
  }
  // Non-approval actions: action objects are not normalised, so the
  // identity check still works for marking the originating message.
  for (const msg of this._messages) {
    if (msg.quick_actions && msg.quick_actions.includes(action)) {
      msg._qa_used = true;
      break;
    }
  }
  this._quickStart(text);
}

// Flip the approval card's entity scope between "this" (default,
// per-entity grant) and "all" (service-wide wildcard grant). Mounted
// on the panel prototype so the inline chip click handler can call
// ``host._toggleApprovalScope(msg)`` without piping anything through
// a custom event. Triggers a re-render so the chip's label updates
// immediately even though the WS payload only goes out on click.
export function _toggleApprovalScope(msg) {
  if (!msg) return;
  msg._entityScope = msg._entityScope === "all" ? "this" : "all";
  this._messages = [...this._messages];
}

export async function _resolveApproval(originatingMsg, scope, proposalId) {
  if (!this._activeSessionId) return;
  // Re-entry guard: a rapid double-click on the same card must not
  // produce two WS calls. The server has its own in-flight guard, but
  // we still want to suppress the UI feedback loop locally so the user
  // isn't tempted to click again while the request is in flight.
  if (originatingMsg && originatingMsg._resolving) return;
  // Stash the action row BEFORE nulling so a transient WS/server
  // failure can restore the buttons. Without this restore, a failed
  // resolve leaves the message in "pending" server-side with no
  // visible affordance to retry — the user has to reload the
  // session to get the buttons back.
  let savedQuickActions = null;
  if (originatingMsg) {
    savedQuickActions = originatingMsg.quick_actions;
    originatingMsg._resolving = true;
    // Hide the action row immediately so the buttons can't be clicked
    // a second time. We also stash a synthetic "Working…" pill in
    // approval_status so the card renders a spinner-equivalent state
    // rather than disappearing entirely.
    originatingMsg.quick_actions = null;
    originatingMsg.approval_status = "resolving";
    this._messages = [...this._messages];
  }
  try {
    const result = await this.hass.callWS({
      type: "selora_ai/resolve_approval",
      session_id: this._activeSessionId,
      proposal_id: proposalId,
      scope,
      // Per-entity vs wildcard recording is driven by the scope chip
      // on the card. Defaults to "this" (least-privilege) when the
      // user never touched it. The server ignores entity_scope for
      // the ``once``/``deny`` scopes.
      entity_scope: originatingMsg?._entityScope || "this",
    });
    if (originatingMsg) {
      originatingMsg.approval_status = result.status;
      this._messages = [...this._messages];
    }
    // The server persists the friendly "Locked the Front Door."
    // message + [[entities:…]] markers and returns it in
    // ``result_message``. Display that directly so reloading the
    // session shows the same content (instead of having the live
    // and post-reload views diverge).
    if (result.result_message) {
      this._messages = [...this._messages, result.result_message];
    }
  } catch (err) {
    // Restore the action row so the user can retry — the request never
    // resolved so the approval is still pending server-side (unless
    // the in-flight guard rejected us, in which case the original
    // resolution is on its way and we'll see its outcome shortly, so
    // leave the "resolving" state in place).
    // Also clear ``_qa_used`` — _selectQuickAction flipped it true on
    // the first click, and ``renderQuickActions(..., {used:true})``
    // sets ``pointer-events: none`` on the row, which would leave the
    // restored buttons visibly present but inert.
    if (originatingMsg && err?.code !== "in_flight") {
      originatingMsg.approval_status = "pending";
      originatingMsg.quick_actions = savedQuickActions;
      originatingMsg._qa_used = false;
      this._messages = [...this._messages];
    }
    this._showToast?.(`Approval failed: ${err.message || err}`, "error");
  } finally {
    if (originatingMsg) {
      originatingMsg._resolving = false;
    }
  }
}

// Truncation detection: a clean "done" event whose prose looks like the
// model was about to continue ("…in your setup:", "**Lights:**\n-"). A
// clean stop after a colon / dash / open bullet / unterminated bold is not
// something the model does on a useful answer; in practice it correlates
// with upstream truncation (gateway dropped the rest of the stream). The
// caller surfaces it as a retryable interruption. Skipped when the turn is
// structurally complete (automation, scene, command calls, quick actions).
export function looksTruncatedResponse(responseText, hasStructured) {
  if (hasStructured) return false;
  const trimmed = (responseText || "").trim();
  if (trimmed.length === 0 || trimmed.length >= 400) return false;
  // Truly unterminated bold means an ODD number of `**` markers. A closed
  // span like `**Foo**` followed by more prose on the same line is NOT
  // truncation — a naive `\*\*[^*\n]*$` test matched the closing `**` plus
  // trailing text and falsely flagged complete answers ending in a question
  // after a bold word.
  const unterminatedBold = ((trimmed.match(/\*\*/g) || []).length & 1) === 1;
  return (
    /[:,\-]\s*$/.test(trimmed) || // dangling colon / comma / bullet dash
    unterminatedBold ||
    /^\s*-\s*$/.test(trimmed.split(/\n/).pop() || "") || // last line is just a bullet
    /\b(the|an?)\s*$/i.test(trimmed) // ends on a bare article — never a valid sentence end
  );
}

// If a streaming response stalls or the WS drops, finalise the active
// assistant bubble with an explanatory notice and a Retry affordance.
// `reason` is shown verbatim under the bubble; `retryPayload` is what
// Retry will resend — it must include any [[entity:…]] marker that was
// appended during _sendMessage, otherwise the retry loses the
// disambiguating context for the exact turn the user is retrying.
// `isOwnSession`: host-level state (_loading, _streaming) is only
// reset if the displayed conversation still matches the one this turn
// was sent in. `sessionHasOtherLiveStream`: when truthy, another turn
// in the same conversation is still streaming, so this interruption
// must not clear the host busy flags — they belong to that other
// turn. Both default to "single-turn / own-session" semantics when
// omitted (legacy callers).
function _finaliseInterruption(
  host,
  assistantMsg,
  retryPayload,
  reason,
  isOwnSession,
  sessionHasOtherLiveStream,
) {
  if (!assistantMsg || assistantMsg._streaming === false) return;
  assistantMsg._streaming = false;
  assistantMsg._interrupted = true;
  assistantMsg._interruptReason = reason;
  assistantMsg._retryWith = retryPayload;
  host._messages = [...host._messages];
  const ownsView = isOwnSession === undefined || isOwnSession();
  const newerLive =
    sessionHasOtherLiveStream !== undefined && sessionHasOtherLiveStream();
  if (ownsView && !newerLive) {
    host._loading = false;
    host._streaming = false;
    // Same focus-restore as the happy ``done`` path — textarea was
    // disabled mid-stream so the user can keep typing without a click.
    host.updateComplete.then(() => {
      const ta = host.shadowRoot?.querySelector(".composer-textarea");
      if (ta && !ta.disabled) ta.focus();
    });
  }
}

export async function _sendMessage() {
  if (!this._input.trim() || this._loading) return;
  const userMsg = this._input;
  // Resolve autocomplete chips into an explicit entity marker so the
  // backend never has to fuzzy-match the device the user named. The marker
  // is appended to the WS payload only — the user bubble stays clean.
  const activeSelections = pruneStaleSelections(
    userMsg,
    this._autocompleteSelections || [],
  );
  const marker = buildEntityMarker(activeSelections);
  const userMsgForSend = marker ? userMsg + marker : userMsg;
  // Keep an object reference so we can splice the SAME bubble back
  // onto _messages if the user navigates away and reopens this
  // session before ``done`` persists the pair server-side.
  const userMsgObj = { role: "user", content: userMsg };
  this._messages = [...this._messages, userMsgObj];
  this._input = "";
  // Reset shell-style history cursor — a fresh send starts a new
  // draft. ArrowUp on the next turn begins from the newest message,
  // not from wherever the previous walk left off.
  this._historyIndex = null;
  this._historyDraft = "";
  this._autocompleteSelections = [];
  // The "new automation" entry point shapes the welcome copy and
  // composer placeholder. Once the user actually sends their first
  // message the chat behaves normally — clear the flag so an empty
  // chat later (e.g. after deleting messages) shows the default
  // welcome instead of stale "New Automation" framing.
  this._newAutomationMode = false;
  this._autocomplete = {
    open: false,
    items: [],
    activeIndex: 0,
    trigger: null,
  };
  this._loading = true;
  // Per-turn session token: the user can switch to another conversation
  // (+ New chat, open a different session) WITHOUT sending a new
  // message. Snapshotting the session id at send time lets late events
  // tell whether the currently-displayed session still matches the one
  // they belong to, so a stale ``done`` cannot yank _activeSessionId
  // back to the old conversation or unblock the composer on a thread
  // it doesn't own.
  const turnSessionId = this._activeSessionId;
  const isOwnSession = () => this._activeSessionId === turnSessionId;
  // Reset textarea height after clearing input
  const ta = this.shadowRoot?.querySelector(".composer-textarea");
  if (ta) ta.style.height = "auto";

  const sendStartedAt = Date.now();
  const assistantMsg = {
    role: "assistant",
    content: "",
    timestamp: new Date(sendStartedAt).toISOString(),
    _streaming: true,
    _sentAt: sendStartedAt,
  };
  this._messages = [...this._messages, assistantMsg];

  // Stream-health watchers — both must be torn down on done/error/retry/stop.
  // Stored on `this` so _stopStreaming() can reach them too; otherwise the
  // disconnect listener would leak across every Send the user makes.
  //
  // The watchdog runs in two phases. Pre-first-token we allow a long grace
  // (some prompts spend the first minute on tool-calling rounds or chewing
  // through a large home snapshot before any text streams). Once tokens
  // start arriving the gap between tokens has to be much shorter — that's
  // when we're confident silence means the server actually stalled.
  const PRE_TOKEN_GRACE_MS = 120_000;
  const POST_TOKEN_GRACE_MS = 45_000;
  // Per-turn override raised by a synthetic token carrying
  // `idle_timeout_ms` (cloud automation streams). Provider pauses
  // between real chunks on heavy-reasoning prompts can exceed the 45s
  // default; the backend already extends its own idle watchdog for
  // those turns, so the client must match or it cancels first.
  let postTokenGraceMs = POST_TOKEN_GRACE_MS;
  let firstTokenSeen = false;
  let lastActivityAt = Date.now();
  let watchdog = null;
  let onDisconnect = null;
  // Per-turn entry registered in host._streams once subscription
  // succeeds. The previous single-slot host pointers (_streamUnsub /
  // _streamTeardown) could only track ONE stream, so as soon as a
  // second send started (in this or another session) the old slot
  // was overwritten and Stop / panel detach could no longer reach
  // the older subscription. With background streams now allowed
  // across session switches we need to track every live stream
  // independently so _stopStreaming can target the user's current
  // session and disconnectedCallback can kill them all.
  this._streams ||= new Set();
  const entry = {
    sessionId: turnSessionId,
    userMsg: userMsgObj,
    assistantMsg,
    teardown: () => {},
    cancel: () => {},
  };
  let tornDown = false;
  const teardown = () => {
    // Idempotent: watchdog + disconnect listener may try to tear
    // each other down (e.g. watchdog interruption races a real WS
    // disconnect), and _stopStreaming + a final ``done`` can both
    // call teardown. Re-entering would double-removeEventListener
    // (harmless but noisy) and re-clear local closures we still
    // need read-only above.
    if (tornDown) return;
    tornDown = true;
    if (watchdog) {
      clearInterval(watchdog);
      watchdog = null;
    }
    if (onDisconnect) {
      this.hass.connection.removeEventListener("disconnected", onDisconnect);
      onDisconnect = null;
    }
  };
  entry.teardown = teardown;

  let localUnsub = null;
  const cancelSubscription = () => {
    const u = localUnsub;
    if (!u) return;
    localUnsub = null;
    try {
      u();
    } catch (_) {
      /* unsub may already be torn down */
    }
    // Entry stays in _streams even after the WS is cancelled, so
    // _openSession can reattach interrupted turns the backend never
    // got to persist. Removal happens explicitly on the success path
    // (done) and from _stopStreaming.
  };
  entry.cancel = cancelSubscription;

  // True iff another stream entry for the same session is still
  // mid-flight. Same-session overlap is possible because Retry on a
  // previously-interrupted bubble only checks _loading, not _streaming
  // — once the first token of an existing turn cleared _loading, a
  // Retry click can fire a second concurrent turn in the same
  // conversation. The older turn's late ``done`` then needs to leave
  // _loading / _streaming alone, otherwise it would hide the Stop
  // button and re-enable the composer while the newer subscription
  // is still running.
  //
  // Iterate over a snapshot — the host._streams Set can be mutated
  // mid-traversal (a concurrent event handler might delete its own
  // entry or _openSession may add/remove entries while restoring
  // background turns), and iterating a Set during mutation is
  // unspecified.
  const sessionHasOtherLiveStream = () => {
    for (const e of [...(this._streams || [])]) {
      if (e === entry) continue;
      if (e.sessionId === entry.sessionId && e.assistantMsg?._streaming) {
        return true;
      }
    }
    return false;
  };

  try {
    const subscribePayload = {
      type: "selora_ai/chat_stream",
      message: userMsgForSend,
    };
    if (this._activeSessionId) {
      subscribePayload.session_id = this._activeSessionId;
    }

    this._streaming = true;

    // 1) WS-level disconnect: HA emits "disconnected" when the socket
    //    drops mid-stream. We won't get any further events for this
    //    subscription, so finalise the bubble immediately.
    onDisconnect = () => {
      teardown();
      // Cancel the subscription too. The WS dropped, but HA's client
      // tracks subscription handles and replays them on reconnect —
      // leaving this one registered would let the same chat_stream
      // turn re-run after reconnect (same "subscription replay →
      // duplicate send" failure the done path explicitly avoids).
      cancelSubscription();
      _finaliseInterruption(
        this,
        assistantMsg,
        userMsgForSend,
        "Connection to Home Assistant was lost mid-response.",
        isOwnSession,
        sessionHasOtherLiveStream,
      );
      // A disconnect on a brand-new session that never received its
      // first ``done`` leaves entry.sessionId === null with no way to
      // ever match it back to a sidebar entry — drop it so it doesn't
      // linger forever in _streams.
      if (entry.sessionId === null) this._streams?.delete(entry);
    };
    this.hass.connection.addEventListener("disconnected", onDisconnect);

    // 2) Stall watchdog: covers cases where the WS stays up but the
    //    integration stops emitting tokens (provider hung, entry
    //    reload, upstream proxy dropped the SSE).
    //
    //    Bail-out keys off assistantMsg._streaming alone — the per-turn
    //    flag flipped false by every finalisation path (done, error,
    //    disconnect, _stopStreaming, prior watchdog interruption). The
    //    _activeTurn check was removed because it killed the watchdog
    //    for background streams as soon as the user sent a new message
    //    in another session, leaving the original WS subscription
    //    dangling and the original bubble stuck on "streaming…" with
    //    no Retry — the exact background-stream case this branch is
    //    trying to support.
    watchdog = setInterval(() => {
      if (assistantMsg._streaming === false) {
        teardown();
        return;
      }
      // Nudge a re-render so the cycling "Building automation..." spinner
      // label in render-chat.js advances even when no token has arrived.
      if (isOwnSession === undefined || isOwnSession()) this.requestUpdate();
      const grace = firstTokenSeen ? postTokenGraceMs : PRE_TOKEN_GRACE_MS;
      if (Date.now() - lastActivityAt > grace) {
        teardown();
        cancelSubscription();
        _finaliseInterruption(
          this,
          assistantMsg,
          userMsgForSend,
          firstTokenSeen
            ? "The server stopped responding."
            : "The server didn't reply in time.",
          isOwnSession,
          sessionHasOtherLiveStream,
        );
        // Stall on a first-turn send that never received any
        // session_id leaves the entry unrestoreable. Drop it.
        if (entry.sessionId === null) this._streams?.delete(entry);
      }
    }, 5_000);

    localUnsub = await this.hass.connection.subscribeMessage((event) => {
      // Drop any event delivered after this turn was interrupted /
      // superseded. The bubble was already finalised (`server stopped
      // responding`) and a newer turn may own host state — late
      // tokens from this turn must not overwrite the new bubble's
      // content or unblock the composer for a turn it doesn't own.
      if (assistantMsg._streaming === false) return;
      if (event.type === "token") {
        // Synthetic tokens (e.g. the backend-injected ```automation
        // sentinel for cloud automation turns) bump activity but must
        // not flip firstTokenSeen — otherwise the watchdog shortens to
        // postTokenGraceMs and a slow real first chunk between 45s and
        // STREAM_AUTOMATION_IDLE_TIMEOUT_S still gets cancelled. The
        // sentinel also carries the server-side idle budget so the
        // post-token grace can match — otherwise a >45s pause between
        // real provider chunks is cancelled by the client even though
        // the server is still within its allowed window.
        if (typeof event.idle_timeout_ms === "number") {
          postTokenGraceMs = event.idle_timeout_ms;
        }
        if (!event.synthetic) firstTokenSeen = true;
        lastActivityAt = Date.now();
        assistantMsg.content += event.text;
        this._messages = [...this._messages];
        // Only clear _loading when this turn is the most recent live
        // one in its session. With same-session overlap (Retry path)
        // an OLDER turn's tokens must not unblock the composer while
        // a newer turn in the same conversation is still streaming.
        if (isOwnSession() && !sessionHasOtherLiveStream()) {
          this._loading = false;
        }
      } else if (event.type === "heartbeat") {
        // Server is alive but has nothing to forward yet (slow first
        // token, or JSON output being suppressed by the backend). Bump
        // the watchdog without flipping firstTokenSeen so the longer
        // pre-token grace stays in effect.
        lastActivityAt = Date.now();
      } else if (event.type === "done") {
        teardown();
        const responseText = event.response || assistantMsg.content || "";
        if (this._config?.developer_mode) {
          const markerCount = (
            responseText.match(/\[\[entit(?:y|ies):[^\]]+\]\]/g) || []
          ).length;
          console.groupCollapsed(
            `Selora chat done · ${markerCount} entity marker(s)`,
          );
          console.log("raw response:\n" + responseText);
          console.log("event:", event);
          console.groupEnd();
        }
        // Truncation detection: bubble landed clean ("done" event)
        // but the response looks like the LLM was about to continue
        // ("…in your setup:", "**Lights:**\n-"). The model emitting
        // a clean stop after a colon / dash / open bullet / dangling
        // bold marker is not something it does intentionally on a
        // useful answer; in practice it correlates with upstream
        // truncation (gateway dropped the rest of the stream). Surface
        // it as an interruption with Retry instead of a dead-end bubble.
        // Skip this when the turn is structurally complete (automation,
        // scene, command calls, quick actions) — those don't end with
        // the dangling-prose pattern we're catching here.
        const hasStructured =
          event.automation ||
          event.scene ||
          (event.executed && event.executed.length) ||
          (event.quick_actions && event.quick_actions.length);
        if (looksTruncatedResponse(responseText, hasStructured)) {
          cancelSubscription();
          // Preserve any tokens already streamed so the user can see
          // what was received before retrying.
          assistantMsg.content = responseText;
          // Finalise BEFORE writing _activeSessionId from event.
          // First-turn-of-new-session sends with turnSessionId === null;
          // adopting event.session_id first would make isOwnSession()
          // return false inside _finaliseInterruption and leave the
          // composer stuck on Stop with no Retry.
          _finaliseInterruption(
            this,
            assistantMsg,
            userMsgForSend,
            "Response looks cut short — try again.",
            isOwnSession,
            sessionHasOtherLiveStream,
          );
          if (event.session_id) {
            if (entry.sessionId === null) entry.sessionId = event.session_id;
            if (isOwnSession() && event.session_id !== this._activeSessionId) {
              this._activeSessionId = event.session_id;
            }
            // Sidebar refresh runs even when the user has switched away
            // — the truncated turn is still persisted server-side and
            // the list preview needs to reflect it.
            this._loadSessions();
          }
          // Drop the entry: the backend appended the user/assistant
          // pair before emitting this ``done``, so a future
          // _openSession will read the interrupted bubble from
          // session.messages. Leaving it in _streams would splice
          // a duplicate copy on top of the persisted one every time
          // the user reopened this conversation.
          this._streams?.delete(entry);
          return;
        }
        assistantMsg.content = responseText;
        assistantMsg.automation = event.automation || null;
        assistantMsg.automation_yaml = event.automation_yaml || null;
        assistantMsg.automation_status = event.automation ? "pending" : null;
        assistantMsg.automation_message_index =
          event.automation_message_index ?? null;
        assistantMsg.refining_automation_id =
          event.refining_automation_id || null;
        assistantMsg.scene = event.scene || null;
        assistantMsg.scene_yaml = event.scene_yaml || null;
        assistantMsg.scene_status = event.scene_status || null;
        assistantMsg.scene_message_index = event.scene_message_index ?? null;
        assistantMsg.refine_scene_id = event.refine_scene_id || null;
        assistantMsg.quick_actions = event.quick_actions || null;
        assistantMsg.command_approval = event.command_approval || null;
        assistantMsg.approval_status = event.command_approval
          ? "pending"
          : null;
        assistantMsg.tool_calls = event.tool_calls || null;
        assistantMsg._replyMs = Date.now() - sendStartedAt;
        assistantMsg._streaming = false;
        this._messages = [...this._messages];
        // Release the composer when the user is on the conversation
        // this ``done`` belongs to AND no newer turn in that same
        // conversation is still streaming. The second clause matters
        // when Retry on an old interrupted bubble fired a parallel
        // turn before this one finished — letting the older ``done``
        // clear _loading / _streaming then would hide Stop while the
        // newer subscription is still running.
        if (isOwnSession() && !sessionHasOtherLiveStream()) {
          this._loading = false;
          this._streaming = false;
          // Composer textarea was disabled during streaming, which
          // dropped focus. Restore it AFTER lit re-renders with
          // ?disabled=false so the user can keep typing without a
          // mouse click. updateComplete waits for the next paint —
          // calling .focus() before the textarea is enabled again
          // would no-op.
          this.updateComplete.then(() => {
            const ta = this.shadowRoot?.querySelector(".composer-textarea");
            if (ta && !ta.disabled) ta.focus();
          });
        }
        // Actually unsubscribe (calls the unsub function and clears
        // the local reference). The previous `this._streamUnsub =
        // null` left HA's websocket client thinking the chat_stream
        // subscription was still active, so any WS reconnect — a
        // brief network blip, HA core restart, the tab being thrown
        // into sleep and resumed — replayed every still-active
        // subscription, re-running the turn and persisting a second
        // user+assistant pair to the session. That's the
        // "I reloaded and my message was sent twice" symptom.
        cancelSubscription();
        // Drop the entry on the success path: the backend now has the
        // full user+assistant pair, so a future _openSession will read
        // it from session.messages — no need to keep a restore handle.
        this._streams?.delete(entry);
        if (event.validation_error) {
          // ``validation_target`` is set only for scene/automation
          // proposal validation. Command-level errors (e.g.
          // ``no_matching_entity_for_command``) leave it null —
          // labelling those as "Automation validation failed" was
          // misleading. Use a neutral label and skip the toast
          // entirely for command rejections, since the chat bubble
          // already shows the user-facing explanation.
          if (event.validation_target === "scene") {
            this._showToast(
              `Scene validation failed: ${event.validation_error}`,
              "error",
            );
          } else if (event.validation_target === "automation") {
            this._showToast(
              `Automation validation failed: ${event.validation_error}`,
              "error",
            );
          }
          // No toast for command-level validation — the bubble
          // already says what went wrong, a duplicate toast is noise.
        }

        // Update session tracking. The _activeSessionId write must be
        // guarded by isOwnSession() — otherwise a late ``done`` from a
        // turn the user has since navigated away from would yank the
        // UI back to the old session. The sidebar refresh runs
        // unconditionally: the backend just persisted a new turn (and,
        // for a first-message implicit session, the session entry
        // itself), and the list needs to reflect that whether or not
        // the user is still on this conversation.
        if (event.session_id) {
          // First-turn sends with turnSessionId === null — the
          // backend assigns the real id on done. Adopt it into the
          // entry so any later restore lookup (interrupted retry,
          // session reopen) can match by sessionId.
          if (entry.sessionId === null) entry.sessionId = event.session_id;
          if (isOwnSession() && event.session_id !== this._activeSessionId) {
            this._activeSessionId = event.session_id;
          }
          this._loadSessions();
        }
      } else if (event.type === "error") {
        teardown();
        cancelSubscription();
        // Adopt event.session_id when present so a first-turn error
        // (turnSessionId === null) still leaves a restoreable entry
        // keyed by the real session id. Backends that emit ``error``
        // without a session_id (e.g. provider failure before the
        // session was committed) leave the entry orphaned — but no
        // session exists in the sidebar to reopen anyway.
        if (event.session_id && entry.sessionId === null) {
          entry.sessionId = event.session_id;
        }
        // Use the same finalisation path as interruptions so the user
        // gets a Retry affordance instead of a dead-end error bubble.
        _finaliseInterruption(
          this,
          assistantMsg,
          userMsgForSend,
          event.message || "Couldn't reach the LLM provider.",
          isOwnSession,
          sessionHasOtherLiveStream,
        );
        // Backend errored before assigning a session id — entry
        // can't be matched back to anything in the sidebar; drop.
        if (entry.sessionId === null) this._streams?.delete(entry);
      }
    }, subscribePayload);
    // Register the entry now that the subscription is live. Going
    // through host._streams (a Set, not a slot) means _stopStreaming
    // and disconnectedCallback can reach every concurrent stream
    // instead of clobbering each other's pointers.
    this._streams.add(entry);
  } catch (err) {
    teardown();
    cancelSubscription();
    _finaliseInterruption(
      this,
      assistantMsg,
      userMsgForSend,
      err.message || "Couldn't start the chat session.",
      isOwnSession,
      sessionHasOtherLiveStream,
    );
  }
}

// Re-send a message that was previously interrupted. Called from the
// Retry control rendered under interrupted bubbles.
export function _retryMessage(text, sourceMsg) {
  if (!text || this._loading) return;
  // Drop the source interrupted entry from _streams before kicking
  // off the new turn. Otherwise the next _openSession on this
  // conversation would splice the stale interrupted bubble back
  // alongside the successful retry, and the set would keep growing
  // with one orphan entry per retry over a long-lived panel.
  if (sourceMsg && this._streams) {
    for (const e of this._streams) {
      if (e.assistantMsg === sourceMsg) {
        this._streams.delete(e);
        break;
      }
    }
  }
  // Splice the failed pair (user echo + interrupted assistant bubble)
  // out of the visible thread so a successful retry REPLACES the error
  // instead of stacking under it — otherwise users see the original
  // prompt twice plus the dead error bubble for every retry. Walk
  // backward to find sourceMsg's index, then drop it plus the
  // preceding user message when it matches the retry text. Falls back
  // to a no-op if the structure isn't what we expect (defensive
  // against renderers that may have inserted system rows in between).
  if (sourceMsg) {
    const idx = this._messages.indexOf(sourceMsg);
    const prev = idx > 0 ? this._messages[idx - 1] : null;
    // Match on role only — `text` is the raw send payload which may
    // include the [[entity:...]] marker, while prev.content holds the
    // clean user text. Comparing them directly drops the splice on any
    // prompt that used autocomplete chips, leaving the duplicate-echo
    // bug live for the exact heavy-automation case this fix targets.
    if (idx >= 0 && prev && prev.role === "user") {
      this._messages = [
        ...this._messages.slice(0, idx - 1),
        ...this._messages.slice(idx + 1),
      ];
    }
  }
  this._input = text;
  this._sendMessage();
}

// `opts.all` kills every live stream (used by panel disconnectedCallback
// where the host is going away and background turns can't keep mutating
// a detached element). The user-driven Stop button calls with no opts so
// it only targets the stream belonging to the conversation currently on
// screen — background turns in other sessions stay alive so they can
// finish or be retried from their own session.
export function _stopStreaming(opts) {
  const streams = this._streams;
  const all = !!opts?.all;
  const currentSession = this._activeSessionId;
  const note = "\n\n_Cancelled by user_";
  const cancelledHere = [];
  if (streams) {
    for (const entry of [...streams]) {
      if (!all && entry.sessionId !== currentSession) continue;
      try {
        entry.teardown();
      } catch (_) {
        /* best-effort */
      }
      try {
        entry.cancel();
      } catch (_) {
        /* best-effort */
      }
      // cancel() removes from set; defensive in case teardown ran twice
      streams.delete(entry);
      if (entry.assistantMsg && entry.sessionId === currentSession) {
        cancelledHere.push(entry.assistantMsg);
      }
    }
  }
  this._streaming = false;
  this._loading = false;
  if (cancelledHere.length > 0) {
    for (const msg of cancelledHere) {
      msg._streaming = false;
      if (!msg.content?.endsWith(note)) {
        msg.content = (msg.content || "") + note;
      }
    }
    this._messages = [...this._messages];
  } else if (!all) {
    // Fallback for the legacy path where no entry was registered yet
    // (e.g. user hit Stop between _sendMessage's UI flip and the
    // subscribe-completes line). Annotate whatever assistant bubble is
    // visible so the click still produces feedback.
    const lastMsg = this._messages[this._messages.length - 1];
    if (lastMsg && lastMsg.role === "assistant") {
      lastMsg._streaming = false;
      if (!lastMsg.content?.endsWith(note)) {
        lastMsg.content = (lastMsg.content || "") + note;
      }
      this._messages = [...this._messages];
    } else {
      this._messages = [
        ...this._messages,
        { role: "assistant", content: note.trimStart() },
      ];
    }
  }
}

export function _scrollChatToBottom() {
  const container = this.shadowRoot?.getElementById("chat-messages");
  if (!container) return;
  container.scrollTop = container.scrollHeight;
  this._chatScrolledAway = false;
}

// Tracks whether the user has scrolled away from the bottom so the
// "go to bottom" button can be shown only when relevant. Only flips
// the flag when the container is actually scrollable past the
// threshold — otherwise a stray scroll event during render (e.g.
// streaming tokens reflowing layout) could mark a fits-in-viewport
// chat as "scrolled away".
export function _onChatScroll(e) {
  const container = e.currentTarget;
  if (!container) return;
  const overflow = container.scrollHeight - container.clientHeight;
  if (overflow <= 80) {
    if (this._chatScrolledAway) this._chatScrolledAway = false;
    return;
  }
  const distance = overflow - container.scrollTop;
  this._chatScrolledAway = distance > 80;
}

export async function _copyMessageText(msg, btn) {
  try {
    const text = msg.content || "";
    await navigator.clipboard.writeText(text);
    btn.classList.add("copied");
    const icon = btn.querySelector("ha-icon");
    if (icon) icon.setAttribute("icon", "mdi:check");
    setTimeout(() => {
      btn.classList.remove("copied");
      if (icon) icon.setAttribute("icon", "mdi:content-copy");
    }, 1500);
  } catch (_) {
    /* clipboard not available */
  }
}
