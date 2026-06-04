// Session management actions (prototype-assigned to SeloraAIArchitectPanel)

import { stripEntityMarkers } from "./chat-autocomplete.js";

const PANEL_PREFIX = "/selora-ai";
const VALID_TABS = ["chat", "automations", "scenes", "settings", "usage"];

function _tabFromPath(pathname) {
  if (!pathname.startsWith(PANEL_PREFIX)) return null;
  const rest = pathname.slice(PANEL_PREFIX.length).replace(/^\/+|\/+$/g, "");
  if (!rest) return "chat";
  return VALID_TABS.includes(rest) ? rest : null;
}

export function _setActiveTab(tab) {
  if (!VALID_TABS.includes(tab)) return;
  this._activeTab = tab;
  const target = tab === "chat" ? PANEL_PREFIX : `${PANEL_PREFIX}/${tab}`;
  if (window.location.pathname !== target) {
    const url = new URL(window.location);
    url.pathname = target;
    window.history.replaceState({}, "", url);
  }
}

export function _checkTabParam() {
  const tab = _tabFromPath(window.location.pathname);
  if (tab && tab !== this._activeTab) {
    this._activeTab = tab;
    if (tab !== "chat") this._showSidebar = false;
  }
  // Deep-linking to /selora-ai/usage skips the Settings → Usage click that
  // normally triggers the stats load, so kick it off here.
  if (tab === "usage") this._loadUsageStats?.();
  // Same for /selora-ai/settings — the grants and MCP token lists are
  // mutable from elsewhere (the approval flow records Always grants
  // server-side; revokes happen on this tab) so we refresh on every
  // activation rather than serving the connectedCallback snapshot.
  if (tab === "settings") {
    this._loadApprovalGrants?.();
    this._loadMcpTokens?.();
  }

  // Handle "Create in Chat" from dashboard card
  const params = new URLSearchParams(window.location.search);
  const newAuto = params.get("new_automation");
  if (newAuto) {
    if (this.hass) {
      // hass is ready — create the chat immediately
      this._newAutomationChat(newAuto);
    } else {
      // First panel load — hass not set yet, defer until updated()
      this._pendingNewAutomation = newAuto;
    }
    const url = new URL(window.location);
    url.searchParams.delete("new_automation");
    window.history.replaceState({}, "", url);
  }
}

// -------------------------------------------------------------------------
// Data loaders
// -------------------------------------------------------------------------

export async function _loadSessions() {
  try {
    const sessions = await this.hass.callWS({
      type: "selora_ai/get_sessions",
    });
    this._sessions = sessions || [];
    // Auto-open most recent session if no active session and on chat tab
    if (
      !this._activeSessionId &&
      this._sessions.length > 0 &&
      this._activeTab === "chat"
    ) {
      await this._openSession(this._sessions[0].id);
    }
  } catch (err) {
    console.error("Failed to load sessions", err);
  }
}

export async function _openSession(sessionId) {
  try {
    const session = await this.hass.callWS({
      type: "selora_ai/get_session",
      session_id: sessionId,
    });
    this._activeSessionId = session.id;
    this._messages = session.messages || [];
    this._deviceDetail = null;
    this._deviceDetailLoading = false;
    this._newAutomationMode = false;
    // Restore in-flight OR interrupted background turns that the
    // backend hasn't persisted. session.messages only contains pairs
    // that reached ``done``, so anything still streaming (or that
    // stalled / errored / disconnected while the user was looking at
    // another conversation) is missing from the server snapshot.
    //
    // Splice every matching entry's user + assistant bubbles back
    // onto _messages in the order they were sent. Re-attaching the
    // SAME object references means a still-live stream's token
    // mutations keep landing on bubbles the UI is now rendering, and
    // an interrupted bubble keeps its Retry affordance.
    //
    // Re-raise the busy flags only if a stream is genuinely still
    // running; interrupted entries leave the composer idle so the
    // user can retry. _loading is only true while the live stream
    // hasn't emitted its first token (no content yet).
    const sessionEntries = [...(this._streams || [])].filter(
      (e) =>
        e.sessionId === session.id &&
        (e.assistantMsg?._streaming || e.assistantMsg?._interrupted),
    );
    if (sessionEntries.length > 0) {
      const tail = [];
      for (const e of sessionEntries) {
        if (e.userMsg) tail.push(e.userMsg);
        tail.push(e.assistantMsg);
      }
      this._messages = [...this._messages, ...tail];
      const liveEntry = sessionEntries.find((e) => e.assistantMsg?._streaming);
      if (liveEntry) {
        this._loading = !liveEntry.assistantMsg.content;
        this._streaming = true;
      } else {
        this._loading = false;
        this._streaming = false;
      }
    } else {
      // Any in-flight turn from a previous session keeps streaming in
      // the background; its event handlers check session match before
      // touching these flags again, so resetting here is safe and
      // prevents the Stop button / disabled composer from carrying
      // over into this view.
      this._loading = false;
      this._streaming = false;
    }
    this._setActiveTab("chat");
    if (this.narrow) this._showSidebar = false;
  } catch (err) {
    console.error("Failed to open session", err);
  }
}

export async function _newSession() {
  try {
    const { session_id } = await this.hass.callWS({
      type: "selora_ai/new_session",
    });
    this._activeSessionId = session_id;
    this._messages = [];
    this._deviceDetail = null;
    this._deviceDetailLoading = false;
    this._newAutomationMode = false;
    this._loading = false;
    this._streaming = false;
    this._setActiveTab("chat");
    this._welcomeKey = (this._welcomeKey || 0) + 1;
    await this._loadSessions();
    if (this.narrow) this._showSidebar = false;
  } catch (err) {
    console.error("Failed to create session", err);
  }
}

// Opens a fresh chat session pre-tuned for "I want to create an
// automation" — sets a transient mode flag so the welcome copy and
// composer placeholder invite the user to describe what they want,
// while still benefiting from entity autocomplete. Cleared on send
// in _sendMessage.
export async function _startNewAutomationChat() {
  try {
    const { session_id } = await this.hass.callWS({
      type: "selora_ai/new_session",
    });
    this._activeSessionId = session_id;
    this._messages = [];
    this._input = "";
    this._autocompleteSelections = [];
    this._newAutomationMode = true;
    this._welcomeKey = (this._welcomeKey || 0) + 1;
    this._setActiveTab("chat");
    if (this.narrow) this._showSidebar = false;
    this._loadSessions();

    // Focus the composer so the user can type immediately.
    this.requestUpdate();
    await this.updateComplete;
    const ta = this.shadowRoot?.querySelector(".composer-textarea");
    if (ta) ta.focus();
  } catch (err) {
    console.error("Failed to start new automation chat", err);
    this._showToast(
      "Failed to start a new automation chat: " + (err?.message || err),
      "error",
    );
  }
}

// Legacy deep-link path: `?new_automation=<name>` on the panel URL or
// the dashboard "Create in Chat" card. When a name is provided we
// pre-fill and auto-send so the existing entry point keeps working.
export async function _newAutomationChat(name) {
  if (!name || !name.trim()) return;
  const trimmed = name.trim();
  try {
    // Create session + draft in parallel
    const { session_id } = await this.hass.callWS({
      type: "selora_ai/new_session",
    });
    await Promise.all([
      this.hass
        .callWS({
          type: "selora_ai/rename_session",
          session_id,
          title: trimmed,
        })
        .catch(() => {}),
      this.hass
        .callWS({
          type: "selora_ai/create_draft",
          alias: trimmed,
          session_id,
        })
        .catch(() => {}),
    ]);

    // Switch to chat and fire the message immediately — the user
    // already validated their intent in the dialog, so a second
    // "press send" step is just friction. Set _input then call
    // _sendMessage synchronously: _sendMessage clears _input before
    // the next render, so the composer never paints the prefilled
    // text (avoids a flash of the prompt being cropped in the
    // single-line textarea).
    this._activeSessionId = session_id;
    this._messages = [];
    this._input = `Create a new automation called "${trimmed}".`;
    this._setActiveTab("chat");
    if (this.narrow) this._showSidebar = false;

    // Load updated data in parallel with the streamed reply.
    this._loadAutomations();
    this._loadSessions();

    await this._sendMessage();
  } catch (err) {
    console.error("Failed to create automation chat session", err);
  }
}

// Companion to _startNewAutomationChat: ask the LLM to invent a
// concrete automation idea tailored to the user's home, then drop it
// into the composer so the user can tweak entities (with
// autocomplete) before sending. We deliberately stop short of
// auto-sending — an AI-conjured suggestion deserves a quick human
// glance, and editing it is the whole reason we sit in the composer
// instead of firing the chat outright.
export async function _suggestAutomationIdea() {
  if (this._suggestingAutomation) return;
  this._suggestingAutomation = true;
  try {
    const result = await this.hass.callWS({
      type: "selora_ai/chat",
      message:
        "Suggest one specific, useful automation for my smart home based on the devices I actually have. " +
        "Reply with ONE plain-English sentence describing the automation as an instruction I could send back to you — " +
        "something like 'Turn off the kitchen lights when nobody is in the kitchen for 10 minutes.' " +
        "Use the human-friendly device names only. " +
        "Do not include quotes, lists, explanations, YAML, or any [[entity:…]] / [[entities:…]] markers — just the instruction.",
    });
    // The LLM sometimes appends `[[entities:…]]` markers (it's been
    // prompted to use them in normal chat). Strip them so the user
    // sees a clean instruction in the composer — when they hit send,
    // the regular send path will re-attach markers for any entities
    // they confirmed via autocomplete.
    const suggestion = stripEntityMarkers(result?.response || "")
      .trim()
      .replace(/^["']|["']$/g, "")
      .replace(/\s+/g, " ");
    if (suggestion) {
      this._input = suggestion;
      // Push the new value through the rendered textarea so the
      // auto-grow logic picks it up (the @input handler is the only
      // place that resizes; setting host._input alone leaves the
      // textarea at its single-row default and crops longer ideas).
      this.requestUpdate();
      await this.updateComplete;
      const ta = this.shadowRoot?.querySelector(".composer-textarea");
      if (ta) {
        ta.value = suggestion;
        ta.style.height = "auto";
        ta.style.height = Math.min(ta.scrollHeight, 200) + "px";
        ta.focus();
        ta.setSelectionRange(suggestion.length, suggestion.length);
      }
    } else {
      this._showToast("AI didn't return a suggestion — try again.", "warning");
    }
    // The /chat WS call spins up a throwaway session for the
    // one-shot prompt; clean it up so the sidebar doesn't fill with
    // empty stubs.
    if (result?.session_id && result.session_id !== this._activeSessionId) {
      this.hass
        .callWS({
          type: "selora_ai/delete_session",
          session_id: result.session_id,
        })
        .catch(() => {});
      this._loadSessions();
    }
  } catch (err) {
    console.error("Failed to suggest automation", err);
    this._showToast(
      "Failed to generate a suggestion — check LLM config.",
      "error",
    );
  } finally {
    this._suggestingAutomation = false;
  }
}

export function _deleteSession(sessionId, evt) {
  evt.stopPropagation();
  this._swipedSessionId = null;
  this._deleteConfirmSessionId = sessionId;
}

export function _onSessionTouchStart(e, id) {
  const touch = e.touches[0];
  this._touchStartX = touch.clientX;
  this._touchStartY = touch.clientY;
  this._touchSessionId = id;
  this._touchSwiping = false;
}

export function _onSessionTouchMove(e, id) {
  if (!this._touchStartX) return;
  const dx = this._touchStartX - e.touches[0].clientX;
  const dy = Math.abs(e.touches[0].clientY - this._touchStartY);
  if (!this._touchSwiping && dy > 10 && dy > Math.abs(dx)) {
    this._touchStartX = null;
    return;
  }
  if (dx > 10) {
    this._touchSwiping = true;
    e.preventDefault();
    const el = e.currentTarget;
    el.parentElement.classList.add("reveal-delete");
    const clamped = Math.min(Math.max(dx, 0), 80);
    el.style.transform = `translateX(-${clamped}px)`;
    el.style.transition = "none";
  }
}

export function _onSessionTouchEnd(e, id) {
  if (!this._touchSwiping) {
    this._touchStartX = null;
    return;
  }
  e.preventDefault();
  const el = e.currentTarget;
  el.style.transition = "";
  el.style.transform = "";
  const dx = this._touchStartX - e.changedTouches[0].clientX;
  this._touchStartX = null;
  this._touchSwiping = false;
  if (dx > 40) {
    this._swipedSessionId = this._swipedSessionId === id ? null : id;
  } else {
    this._swipedSessionId =
      this._swipedSessionId === id ? null : this._swipedSessionId;
  }
}

export async function _confirmDeleteSession() {
  const sessionId = this._deleteConfirmSessionId;
  if (!sessionId) return;
  this._deleteConfirmSessionId = null;
  try {
    await this.hass.callWS({
      type: "selora_ai/delete_session",
      session_id: sessionId,
    });
    _pruneStreamsForSession.call(this, sessionId);
    if (this._activeSessionId === sessionId) {
      this._activeSessionId = null;
      this._messages = [];
    }
    await this._loadSessions();
  } catch (err) {
    console.error("Failed to delete session", err);
  }
}

// Cancel any live subscription for a deleted session and drop its
// entries from _streams. Without this, an interrupted or still-
// streaming background turn would survive the delete: _openSession
// for the now-deleted id couldn't re-render it (no such session) but
// the entry would keep consuming memory, and a still-live stream
// would keep emitting events nothing in the UI listens for.
function _pruneStreamsForSession(sessionId) {
  if (!this._streams) return;
  for (const e of [...this._streams]) {
    if (e.sessionId !== sessionId) continue;
    try {
      e.teardown();
    } catch (_) {
      /* best-effort */
    }
    try {
      e.cancel();
    } catch (_) {
      /* best-effort */
    }
    this._streams.delete(e);
  }
}

export function _toggleSessionSelection(sessionId) {
  this._selectedSessionIds = {
    ...this._selectedSessionIds,
    [sessionId]: !this._selectedSessionIds[sessionId],
  };
}

export function _toggleSelectAllSessions() {
  const allSelected = this._sessions.every(
    (s) => this._selectedSessionIds[s.id],
  );
  if (allSelected) {
    this._selectedSessionIds = {};
  } else {
    const selected = {};
    this._sessions.forEach((s) => {
      selected[s.id] = true;
    });
    this._selectedSessionIds = selected;
  }
}

export function _requestBulkDeleteSessions() {
  const count = Object.values(this._selectedSessionIds).filter(Boolean).length;
  if (count === 0) return;
  this._deleteConfirmSessionId = "__bulk__";
}

export async function _confirmBulkDeleteSessions() {
  this._deleteConfirmSessionId = null;
  const ids = Object.entries(this._selectedSessionIds)
    .filter(([, v]) => v)
    .map(([id]) => id);
  for (const id of ids) {
    try {
      await this.hass.callWS({
        type: "selora_ai/delete_session",
        session_id: id,
      });
      _pruneStreamsForSession.call(this, id);
      if (this._activeSessionId === id) {
        this._activeSessionId = null;
        this._messages = [];
      }
    } catch (err) {
      console.error("Failed to delete session", id, err);
    }
  }
  this._selectedSessionIds = {};
  this._selectChatsMode = false;
  await this._loadSessions();
}
