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
      // Pass the panel's active language so the persisted "Done"
      // message matches the locale the user has been seeing in chat.
      ...(this.hass?.language ? { language: this.hass.language } : {}),
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
// When `myTurn` is provided, host-level state (_loading, _streaming) is
// only reset if that turn is still the active one. Without this guard, a
// late-delivered ``done``/``error``/disconnect event from a prior,
// already-finalised turn would clobber the next turn's host state and
// leave the composer stuck — the user could keep typing into a thread
// the assistant would never reply on again (ha-integration#108).
function _finaliseInterruption(
  host,
  assistantMsg,
  retryPayload,
  reason,
  myTurn,
) {
  if (!assistantMsg || assistantMsg._streaming === false) return;
  assistantMsg._streaming = false;
  assistantMsg._interrupted = true;
  assistantMsg._interruptReason = reason;
  assistantMsg._retryWith = retryPayload;
  host._messages = [...host._messages];
  if (myTurn === undefined || host._activeTurn === myTurn) {
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
  this._messages = [...this._messages, { role: "user", content: userMsg }];
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
  // Per-turn token: every callback below (watchdog, WS event handler,
  // disconnect listener) must check that the host's _activeTurn still
  // matches this turn before mutating shared host state. Without this,
  // a late event from a previous, already-finalised turn — the long
  // automation request whose generation eventually finishes on the
  // server after the watchdog fired — would clobber the new turn's
  // _streaming / _loading flags and cancel its subscription, leaving
  // the composer stuck and the thread unrecoverable (ha-integration#108).
  const myTurn = (this._activeTurn = (this._activeTurn || 0) + 1);
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
  let firstTokenSeen = false;
  let lastActivityAt = Date.now();
  let watchdog = null;
  let onDisconnect = null;
  const teardown = () => {
    if (watchdog) {
      clearInterval(watchdog);
      watchdog = null;
    }
    if (onDisconnect) {
      this.hass.connection.removeEventListener("disconnected", onDisconnect);
      onDisconnect = null;
    }
    if (this._streamTeardown === teardown) {
      this._streamTeardown = null;
    }
  };
  this._streamTeardown = teardown;

  // Capture the unsub locally so a late callback from THIS turn can
  // only cancel its own subscription, never the next turn's. The host
  // pointer (this._streamUnsub) is still kept in sync for _stopStreaming
  // / disconnect paths, but is only cleared if it still matches ours.
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
    if (this._streamUnsub === u) this._streamUnsub = null;
  };

  try {
    const subscribePayload = {
      type: "selora_ai/chat_stream",
      message: userMsgForSend,
    };
    if (this._activeSessionId) {
      subscribePayload.session_id = this._activeSessionId;
    }
    // Forward the panel's active language so the backend renders LLM
    // responses and synthesized confirmations in the user's locale,
    // even when hass.config.language (server-wide) differs from the
    // viewing user's frontend locale.
    if (this.hass?.language) {
      subscribePayload.language = this.hass.language;
    }

    this._streaming = true;

    // 1) WS-level disconnect: HA emits "disconnected" when the socket
    //    drops mid-stream. We won't get any further events for this
    //    subscription, so finalise the bubble immediately.
    onDisconnect = () => {
      teardown();
      // Cancel BEFORE the WS auto-resubscribes on reconnect — otherwise
      // the server can rerun this turn, persist duplicate messages, and
      // late tokens keep arriving for a finalised bubble.
      cancelSubscription();
      _finaliseInterruption(
        this,
        assistantMsg,
        userMsgForSend,
        this._t(
          "chat_actions_interrupt_disconnect",
          "Connection to Home Assistant was lost mid-response.",
        ),
        myTurn,
      );
    };
    this.hass.connection.addEventListener("disconnected", onDisconnect);

    // 2) Stall watchdog: covers cases where the WS stays up but the
    //    integration stops emitting tokens (provider hung, entry
    //    reload, upstream proxy dropped the SSE).
    //
    //    The _streaming check uses the host flag rather than a closure
    //    over our myTurn because _stopStreaming() / a happier next turn
    //    will reset it; if our turn already finalised we still want to
    //    tear our watcher down here on the next tick.
    watchdog = setInterval(() => {
      if (!this._streaming || this._activeTurn !== myTurn) {
        teardown();
        return;
      }
      const grace = firstTokenSeen ? POST_TOKEN_GRACE_MS : PRE_TOKEN_GRACE_MS;
      if (Date.now() - lastActivityAt > grace) {
        teardown();
        cancelSubscription();
        _finaliseInterruption(
          this,
          assistantMsg,
          userMsgForSend,
          firstTokenSeen
            ? this._t(
                "chat_actions_interrupt_server_stopped",
                "The server stopped responding.",
              )
            : this._t(
                "chat_actions_interrupt_server_no_reply",
                "The server didn't reply in time.",
              ),
          myTurn,
        );
      }
    }, 5_000);

    localUnsub = await this.hass.connection.subscribeMessage((event) => {
      // Drop any event delivered after this turn was interrupted /
      // superseded. The bubble was already finalised (`server stopped
      // responding`) and a newer turn may own host state — late
      // tokens from this turn must not overwrite the new bubble's
      // content or unblock the composer for a turn it doesn't own.
      // Cancel here too: if a disconnect+reconnect re-armed the
      // subscription on the server side, we drop it locally so the
      // server stops rerunning this turn.
      if (assistantMsg._streaming === false) {
        cancelSubscription();
        return;
      }
      if (event.type === "token") {
        firstTokenSeen = true;
        lastActivityAt = Date.now();
        assistantMsg.content += event.text;
        this._messages = [...this._messages];
        if (this._activeTurn === myTurn) this._loading = false;
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
          if (event.session_id) {
            if (event.session_id !== this._activeSessionId) {
              this._activeSessionId = event.session_id;
            }
            this._loadSessions();
          }
          _finaliseInterruption(
            this,
            assistantMsg,
            userMsgForSend,
            this._t(
              "chat_actions_interrupt_truncated",
              "Response looks cut short — try again.",
            ),
            myTurn,
          );
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
        // Only release the composer if this is still the active turn.
        // A late ``done`` from a previously-finalised turn must not
        // touch the next turn's _loading / _streaming flags.
        if (this._activeTurn === myTurn) {
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

        // Update session tracking
        if (event.session_id) {
          if (event.session_id !== this._activeSessionId) {
            this._activeSessionId = event.session_id;
          }
          this._loadSessions();
        }
      } else if (event.type === "error") {
        teardown();
        cancelSubscription();
        // Use the same finalisation path as interruptions so the user
        // gets a Retry affordance instead of a dead-end error bubble.
        _finaliseInterruption(
          this,
          assistantMsg,
          userMsgForSend,
          event.message ||
            this._t(
              "chat_actions_interrupt_llm_unreachable",
              "Couldn't reach the LLM provider.",
            ),
          myTurn,
        );
      }
    }, subscribePayload);
    // Surface the unsub on the host too, so _stopStreaming() can reach
    // it. cancelSubscription() guards with `if (this._streamUnsub === u)`
    // so a late callback can never null out the NEXT turn's pointer.
    this._streamUnsub = localUnsub;
  } catch (err) {
    teardown();
    cancelSubscription();
    _finaliseInterruption(
      this,
      assistantMsg,
      userMsgForSend,
      err.message ||
        this._t(
          "chat_actions_interrupt_session_start_failed",
          "Couldn't start the chat session.",
        ),
      myTurn,
    );
  }
}

// Re-send a message that was previously interrupted. Called from the
// Retry control rendered under interrupted bubbles.
export function _retryMessage(text) {
  if (!text || this._loading) return;
  this._input = text;
  this._sendMessage();
}

export function _stopStreaming() {
  if (this._streamTeardown) {
    this._streamTeardown();
  }
  if (this._streamUnsub) {
    this._streamUnsub();
    this._streamUnsub = null;
  }
  this._streaming = false;
  this._loading = false;
  const note =
    "\n\n" + this._t("chat_actions_cancelled_by_user", "_Cancelled by user_");
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

// Record an anonymous thumbs up/down on an assistant reply. Only wired
// up in the UI when telemetry is enabled (the toolbar hides the thumbs
// otherwise). Clicking the active rating again clears it. The backend
// only ever sees the direction, never the message text.
export async function _recordChatFeedback(msg, rating, btn) {
  if (!this._config?.telemetry_enabled) return;
  _pulse(btn);
  const next = msg._feedback === rating ? null : rating;
  msg._feedback = next;
  this._messages = [...this._messages];
  if (!next) return;
  // Tell telemetry which kind of reply was rated so automation/scene
  // feedback can be tracked apart from plain prose answers.
  const subject = msg.automation ? "automation" : msg.scene ? "scene" : "prose";
  try {
    await this.hass.callWS({
      type: "selora_ai/record_chat_feedback",
      rating: next,
      subject,
    });
  } catch (_) {
    // Telemetry is best-effort — a failed counter never surfaces an
    // error. Leave the thumb selected so the click still feels handled.
  }
}

// Copy text to the clipboard with a fallback for insecure contexts.
// HA is often served over plain http on the LAN, where
// `navigator.clipboard` is undefined — without the execCommand path the
// copy buttons would silently do nothing.
async function _writeClipboard(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (_) {
    /* fall through to the legacy path */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-9999px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (_) {
    return false;
  }
}

// Brief press animation shared by the icon action buttons.
function _pulse(btn) {
  if (!btn) return;
  btn.classList.remove("pulse");
  // Force reflow so re-adding the class restarts the animation.
  void btn.offsetWidth;
  btn.classList.add("pulse");
  setTimeout(() => btn.classList.remove("pulse"), 300);
}

// Delegated handler for the per-fence copy buttons injected into rendered
// markdown (see renderMarkdown). The buttons live inside a `.innerHTML`
// blob so they can't carry lit listeners — clicks bubble up to the
// message span, where this picks them off by class and copies the raw
// code stashed on `data-code`.
export async function _onCodeCopyClick(e) {
  const btn = e.target.closest?.(".selora-code-copy");
  if (!btn) return;
  const code = btn.dataset.code || "";
  _pulse(btn);
  const ok = await _writeClipboard(code);
  if (!ok || btn.classList.contains("copied")) return;
  const original = btn.innerHTML;
  btn.classList.add("copied");
  btn.innerHTML =
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
  setTimeout(() => {
    btn.classList.remove("copied");
    btn.innerHTML = original;
  }, 1500);
}

export async function _copyMessageText(msg, btn, text) {
  _pulse(btn);
  // Prefer the display text (automation/scene JSON stripped) when the
  // caller has it; fall back to the raw content.
  const toCopy = (text ?? msg.content) || "";
  const ok = await _writeClipboard(toCopy);
  if (!ok) return;
  btn.classList.add("copied");
  const icon = btn.querySelector("ha-icon");
  if (icon) icon.setAttribute("icon", "mdi:check");
  setTimeout(() => {
    btn.classList.remove("copied");
    if (icon) icon.setAttribute("icon", "mdi:content-copy");
  }, 1500);
}
