// Session management actions (prototype-assigned to SeloraAIArchitectPanel)

export function _checkTabParam() {
  const params = new URLSearchParams(window.location.search);
  const tab = params.get("tab");
  if (tab === "automations" || tab === "scenes" || tab === "settings") {
    this._activeTab = tab;
    this._showSidebar = false;
  }

  // Handle "Create in Chat" from dashboard card
  const newAuto = params.get("new_automation");
  if (newAuto) {
    if (this.hass) {
      // hass is ready — create the chat immediately
      this._newAutomationChat(newAuto);
    } else {
      // First panel load — hass not set yet, defer until updated()
      this._pendingNewAutomation = newAuto;
    }
  }

  // Clean query params so they don't stick on subsequent visits
  if (tab || newAuto) {
    const url = new URL(window.location);
    url.searchParams.delete("tab");
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
    this._activeTab = "chat";
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
    this._activeTab = "chat";
    this._welcomeKey = (this._welcomeKey || 0) + 1;
    await this._loadSessions();
    if (this.narrow) this._showSidebar = false;
  } catch (err) {
    console.error("Failed to create session", err);
  }
}

export async function _newAutomationChat(name) {
  if (!name || !name.trim()) return;
  const trimmed = name.trim();
  this._showNewAutoDialog = false;
  this.requestUpdate();
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

    // Switch to chat
    this._activeSessionId = session_id;
    this._messages = [];
    this._input = `Create a new automation called "${trimmed}".`;
    this._activeTab = "chat";
    if (this.narrow) this._showSidebar = false;

    // Force render then focus
    this.requestUpdate();
    await this.updateComplete;
    const textfield = this.shadowRoot?.querySelector("ha-textfield");
    if (textfield) textfield.focus();

    // Load updated data
    this._loadAutomations();
    this._loadSessions();
  } catch (err) {
    console.error("Failed to create automation chat session", err);
  }
}

export async function _suggestAutomationName() {
  this._suggestingName = true;
  try {
    const result = await this.hass.callWS({
      type: "selora_ai/chat",
      message:
        "Suggest one short, descriptive automation name for my smart home based on my devices and current setup. Reply with ONLY the automation name, nothing else. No quotes, no explanation.",
    });
    const name = (result?.response || "").trim().replace(/^["']|["']$/g, "");
    if (name) this._newAutoName = name;
    // Clean up the throwaway session created by the chat call
    if (result?.session_id) {
      this.hass
        .callWS({
          type: "selora_ai/delete_session",
          session_id: result.session_id,
        })
        .catch(() => {});
      this._loadSessions();
    }
  } catch (err) {
    console.error("Failed to suggest name", err);
    this._showToast(
      "Failed to generate suggestion — check LLM config",
      "error",
    );
  } finally {
    this._suggestingName = false;
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
    if (this._activeSessionId === sessionId) {
      this._activeSessionId = null;
      this._messages = [];
    }
    await this._loadSessions();
  } catch (err) {
    console.error("Failed to delete session", err);
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
