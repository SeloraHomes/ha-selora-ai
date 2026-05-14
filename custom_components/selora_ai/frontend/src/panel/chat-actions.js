// Chat and messaging actions (prototype-assigned to SeloraAIArchitectPanel)

export function _quickStart(message) {
  this._input = message;
  this._sendMessage();
}

export function _selectQuickAction(action) {
  const text = action.value || action.label;
  // Mark the originating message's actions as used
  for (const msg of this._messages) {
    if (msg.quick_actions && msg.quick_actions.includes(action)) {
      msg._qa_used = true;
      break;
    }
  }
  this._quickStart(text);
}

// If a streaming response stalls or the WS drops, finalise the active
// assistant bubble with an explanatory notice and a Retry affordance.
// `reason` is shown verbatim under the bubble; `userMsg` is what Retry
// will resend.
function _finaliseInterruption(host, assistantMsg, userMsg, reason) {
  if (!assistantMsg || assistantMsg._streaming === false) return;
  assistantMsg._streaming = false;
  assistantMsg._interrupted = true;
  assistantMsg._interruptReason = reason;
  assistantMsg._retryWith = userMsg;
  host._messages = [...host._messages];
  host._loading = false;
  host._streaming = false;
}

export async function _sendMessage() {
  if (!this._input.trim() || this._loading) return;
  const userMsg = this._input;
  this._messages = [...this._messages, { role: "user", content: userMsg }];
  this._input = "";
  this._loading = true;
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
  // User sent a message — they expect to see it land regardless of
  // where they were scrolled.
  this._requestScrollChat({ force: true });

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

  const cancelSubscription = () => {
    if (this._streamUnsub) {
      try {
        this._streamUnsub();
      } catch (_) {
        /* unsub may already be torn down */
      }
      this._streamUnsub = null;
    }
  };

  try {
    const subscribePayload = {
      type: "selora_ai/chat_stream",
      message: userMsg,
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
      _finaliseInterruption(
        this,
        assistantMsg,
        userMsg,
        "Connection to Home Assistant was lost mid-response.",
      );
    };
    this.hass.connection.addEventListener("disconnected", onDisconnect);

    // 2) Stall watchdog: covers cases where the WS stays up but the
    //    integration stops emitting tokens (provider hung, entry
    //    reload, upstream proxy dropped the SSE).
    watchdog = setInterval(() => {
      if (!this._streaming) {
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
          userMsg,
          firstTokenSeen
            ? "The server stopped responding."
            : "The server didn't reply in time.",
        );
      }
    }, 5_000);

    this._streamUnsub = await this.hass.connection.subscribeMessage((event) => {
      if (event.type === "token") {
        firstTokenSeen = true;
        lastActivityAt = Date.now();
        assistantMsg.content += event.text;
        this._messages = [...this._messages];
        this._loading = false;
        this._requestScrollChat();
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
        const trimmed = responseText.trim();
        const looksTruncated =
          !hasStructured &&
          trimmed.length > 0 &&
          trimmed.length < 400 &&
          (/[:,\-]\s*$/.test(trimmed) || // dangling colon / comma / bullet dash
            /\*\*[^*\n]*$/.test(trimmed) || // unterminated bold
            /^\s*-\s*$/.test(trimmed.split(/\n/).pop() || "") || // last line is just a bullet
            /\b(the|an?)\s*$/i.test(trimmed)); // ends on a bare article — never a valid sentence end
        if (looksTruncated) {
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
            userMsg,
            "Response looks cut short — try again.",
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
        assistantMsg.tool_calls = event.tool_calls || null;
        assistantMsg._replyMs = Date.now() - sendStartedAt;
        assistantMsg._streaming = false;
        this._messages = [...this._messages];
        this._loading = false;
        this._streaming = false;
        this._streamUnsub = null;
        if (event.validation_error) {
          const label =
            event.validation_target === "scene" ? "Scene" : "Automation";
          this._showToast(
            `${label} validation failed: ${event.validation_error}`,
            "error",
          );
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
          userMsg,
          event.message || "Couldn't reach the LLM provider.",
        );
      }
    }, subscribePayload);
  } catch (err) {
    teardown();
    cancelSubscription();
    _finaliseInterruption(
      this,
      assistantMsg,
      userMsg,
      err.message || "Couldn't start the chat session.",
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
  const note = "\n\n_Cancelled by user_";
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

export function _requestScrollChat(opts) {
  // Standard chat-app behaviour: only auto-scroll if the user is
  // already near the bottom. If they scrolled up to read history,
  // don't yank them down — covers every path that ends up here
  // (keyboard show/hide, hydrate, focus, hass updates, …). Pass
  // `{ force: true }` for explicit user actions where the user
  // expects to land at the latest reply (opening a session, sending
  // a message).
  //
  // Coalesce repeated calls within the same frame: a non-force call
  // from `updated()` (triggered by the _messages change) often races
  // ahead of the explicit force call from `_openSession`, and we'd
  // otherwise drop the force on the floor. Track the strongest mode
  // requested while the RAF is pending.
  if (opts && opts.force) this._scrollForce = true;
  if (this._scrollPending) return;
  this._scrollPending = true;
  requestAnimationFrame(() => {
    const force = !!this._scrollForce;
    this._scrollPending = false;
    this._scrollForce = false;
    const container = this.shadowRoot.getElementById("chat-messages");
    if (!container) return;
    if (!force) {
      const distance =
        container.scrollHeight - container.scrollTop - container.clientHeight;
      if (distance > 80) return;
    }
    container.scrollTop = container.scrollHeight;
  });
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
