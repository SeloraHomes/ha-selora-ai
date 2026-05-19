// Session management actions (prototype-assigned to SeloraAIArchitectPanel)

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
    this._setActiveTab("chat");
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
    this._setActiveTab("chat");
    if (this.narrow) this._showSidebar = false;

    // Force render then focus
    this.requestUpdate();
    await this.updateComplete;
    const textarea = this.shadowRoot?.querySelector(".composer-textarea");
    if (textarea) textarea.focus();

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
