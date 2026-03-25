import { LitElement, html, css } from "lit";
import { keyed } from "lit/directives/keyed.js";

// ---------------------------------------------------------------------------
// Selora AI Architect Panel
// ---------------------------------------------------------------------------
// Layout: two-pane when wide, single-pane when narrow.
//   Left pane  — session list + "New Chat" button
//   Right pane — active session (messages + input)
//
// Tabs within right pane: Chat | Automations | Settings
// ---------------------------------------------------------------------------

/**
 * Lightweight markdown-to-HTML converter.
 * Handles: **bold**, *italic*, numbered lists, bullet lists, `code`, line breaks.
 */
/**
 * Strip ```automation ... ``` blocks from message text.
 * Returns { text, hasAutomationBlock, isPartialBlock }.
 */
function stripAutomationBlock(text) {
  if (!text)
    return { text: "", hasAutomationBlock: false, isPartialBlock: false };

  // Complete block: ```automation ... ```
  const completeRe = /```automation[\s\S]*?```/g;
  const hasComplete = completeRe.test(text);
  let cleaned = text.replace(completeRe, "").trim();

  // Partial block (still streaming): ```automation ... (no closing ```)
  const partialRe = /```automation[\s\S]*$/;
  const hasPartial = !hasComplete && partialRe.test(cleaned);
  if (hasPartial) {
    cleaned = cleaned.replace(partialRe, "").trim();
  }

  return {
    text: cleaned,
    hasAutomationBlock: hasComplete,
    isPartialBlock: hasPartial,
  };
}

function renderMarkdown(text) {
  if (!text) return "";
  let escaped = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  // Code blocks (```)
  escaped = escaped.replace(
    /```([\s\S]*?)```/g,
    '<pre style="background:#2d2d2d;color:#f8f8f2;padding:10px;border-radius:6px;font-size:12px;overflow-x:auto;margin:8px 0;">$1</pre>',
  );
  // Inline code
  escaped = escaped.replace(
    /`([^`]+)`/g,
    '<code style="background:rgba(255,255,255,0.1);padding:2px 5px;border-radius:3px;font-size:13px;">$1</code>',
  );
  // Headings (#### → h6, ### → h5, ## → h4, # → h3) — sized for chat bubbles
  escaped = escaped.replace(
    /^####\s+(.+)$/gm,
    '<div style="font-weight:700;font-size:14px;margin:10px 0 4px;">$1</div>',
  );
  escaped = escaped.replace(
    /^###\s+(.+)$/gm,
    '<div style="font-weight:700;font-size:15px;margin:12px 0 4px;">$1</div>',
  );
  escaped = escaped.replace(
    /^##\s+(.+)$/gm,
    '<div style="font-weight:700;font-size:16px;margin:14px 0 6px;">$1</div>',
  );
  escaped = escaped.replace(
    /^#\s+(.+)$/gm,
    '<div style="font-weight:700;font-size:17px;margin:16px 0 6px;">$1</div>',
  );
  // Bold
  escaped = escaped.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  // Italic
  escaped = escaped.replace(/\*(.+?)\*/g, "<em>$1</em>");
  // Numbered lists: lines starting with "1. ", "2. ", etc.
  escaped = escaped.replace(
    /^(\d+)\.\s+(.+)$/gm,
    '<div style="margin:4px 0 4px 8px;"><strong>$1.</strong> $2</div>',
  );
  // Bullet lists: lines starting with "- "
  escaped = escaped.replace(
    /^[-•]\s+(.+)$/gm,
    '<div style="margin:4px 0 4px 8px;padding-left:12px;border-left:2px solid rgba(255,255,255,0.15);">$1</div>',
  );
  // Line breaks
  escaped = escaped.replace(/\n/g, "<br>");

  return escaped;
}

class SeloraAIArchitectPanel extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      narrow: { type: Boolean, reflect: true },
      route: { type: Object },
      panel: { type: Object },

      // Session list
      _sessions: { type: Array },
      _activeSessionId: { type: String },

      // Message view
      _messages: { type: Array },
      _input: { type: String },
      _loading: { type: Boolean },
      _streaming: { type: Boolean },

      // Sidebar visibility (mobile)
      _showSidebar: { type: Boolean },

      // Tabs
      _activeTab: { type: String },

      // Automations tab
      _suggestions: { type: Array },
      _automations: { type: Array },
      _expandedAutomations: { type: Object },

      // Settings tab
      _config: { type: Object },
      _savingConfig: { type: Boolean },
      _newApiKey: { type: String },

      // Editable YAML state (keyed by msgIndex or suggestion key)
      _editedYaml: { type: Object },
      _savingYaml: { type: Object },

      // Version history drawer
      _versionHistoryOpen: { type: Object },
      _versions: { type: Object },
      _loadingVersions: { type: Object },
      _versionTab: { type: Object }, // keyed by automationId → "versions" | "lineage"
      _lineage: { type: Object }, // keyed by automationId → LineageEntry[]
      _loadingLineage: { type: Object },

      // Diff viewer
      _diffOpen: { type: Boolean },
      _diffAutomationId: { type: String },
      _diffVersionA: { type: String },
      _diffVersionB: { type: String },
      _diffResult: { type: Array },
      _loadingDiff: { type: Boolean },

      // Automation filter
      _automationFilter: { type: String },

      // Burger menu
      _openBurgerMenu: { type: String },

      // Recently deleted section
      _showDeleted: { type: Boolean },
      _deletedAutomations: { type: Array },
      _loadingDeleted: { type: Boolean },

      // Action loading states
      _deletingAutomation: { type: Object },
      _restoringAutomation: { type: Object },
      _hardDeletingAutomation: { type: Object },
      _restoringVersion: { type: Object },
      _loadingToChat: { type: Object },

      // Bulk automation actions
      _selectedAutomationIds: { type: Object },
      _bulkActionInProgress: { type: Boolean },
      _bulkActionLabel: { type: String },

      // Hard delete confirmation modal
      _hardDeleteTarget: { type: Object },
      _hardDeleteAliasInput: { type: String },

      // Toast notifications
      _toast: { type: String },
      _toastType: { type: String },

      // Detail drawer for compact grid
      _expandedDetailId: { type: String },

      // New automation dialog
      _showNewAutoDialog: { type: Boolean },
      _newAutoName: { type: String },
      _suggestingName: { type: Boolean },

      // Generate suggestions loading
      _generatingSuggestions: { type: Boolean },

      // Automations sub-tab
      _automationsSubTab: { type: String },

      // Inline card tabs (flow / yaml / history)
      _cardActiveTab: { type: Object },

      // Bulk edit mode
      _bulkEditMode: { type: Boolean },

      // Inline alias editing
      _editingAlias: { type: String }, // automation_id being renamed
      _editingAliasValue: { type: String },

      // Proactive suggestions (pattern-based)
      _proactiveSuggestions: { type: Array },
      _loadingProactive: { type: Boolean },
      _proactiveExpanded: { type: Object },
      _acceptingProactive: { type: Object },
      _dismissingProactive: { type: Object },
      _showProactive: { type: Boolean },

      // Delete session confirmation
      _deleteConfirmSessionId: { type: String },

      // Bulk session delete
      _selectChatsMode: { type: Boolean },
      _selectedSessionIds: { type: Object },

      // Pending "Create in Chat" from dashboard card
      _pendingNewAutomation: { type: String },

      // Pagination
      _automationsPage: { type: Number },
      _suggestionsPage: { type: Number },
      _autosPerPage: { type: Number },
      _suggestionsPerPage: { type: Number },
    };
  }

  constructor() {
    super();
    this._sessions = [];
    this._activeSessionId = null;
    this._messages = [];
    this._input = "";
    this._loading = false;
    this._streaming = false;
    this._streamUnsub = null;
    this._showSidebar = false;
    this._activeTab = "chat";
    this._suggestions = [];
    this._automations = [];
    this._expandedAutomations = {};
    this._automationsSubTab = "my_automations";
    this._editedYaml = {};
    this._savingYaml = {};
    this._config = null;
    this._savingConfig = false;
    this._newApiKey = "";
    // Version history
    this._versionHistoryOpen = {};
    this._versions = {};
    this._loadingVersions = {};
    this._versionTab = {};
    this._lineage = {};
    this._loadingLineage = {};
    // Diff viewer
    this._diffOpen = false;
    this._diffAutomationId = null;
    this._diffVersionA = null;
    this._diffVersionB = null;
    this._diffResult = [];
    this._loadingDiff = false;
    // Automation filter
    this._automationFilter = "";
    // Burger menu
    this._openBurgerMenu = null;
    // Recently deleted
    this._showDeleted = false;
    this._deletedAutomations = [];
    this._loadingDeleted = false;
    // Action loading states
    this._deletingAutomation = {};
    this._restoringAutomation = {};
    this._hardDeletingAutomation = {};
    this._restoringVersion = {};
    this._loadingToChat = {};
    this._selectedAutomationIds = {};
    this._bulkActionInProgress = false;
    this._bulkActionLabel = "";
    this._hardDeleteTarget = null;
    this._hardDeleteAliasInput = "";
    this._toast = "";
    this._toastType = "info";
    this._toastTimer = null;
    this._expandedDetailId = null;
    this._showNewAutoDialog = false;
    this._newAutoName = "";
    this._suggestingName = false;
    this._generatingSuggestions = false;
    // Inline card tabs
    this._cardActiveTab = {};
    this._bulkEditMode = false;
    this._editingAlias = null;
    this._editingAliasValue = "";
    // Proactive suggestions
    this._proactiveSuggestions = [];
    this._loadingProactive = false;
    this._proactiveExpanded = {};
    this._acceptingProactive = {};
    this._dismissingProactive = {};
    this._showProactive = true;
    // Delete session confirmation
    this._deleteConfirmSessionId = null;
    // Bulk session delete
    this._selectChatsMode = false;
    this._selectedSessionIds = {};
    // Pagination
    this._automationsPage = 1;
    this._suggestionsPage = 1;
    this._autosPerPage = 10;
    this._suggestionsPerPage = 10;
  }

  connectedCallback() {
    super.connectedCallback();
    this._checkTabParam();
    this._loadSessions();
    this._loadSuggestions();
    this._loadAutomations();
    this._locationHandler = () => this._checkTabParam();
    window.addEventListener("location-changed", this._locationHandler);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._locationHandler) {
      window.removeEventListener("location-changed", this._locationHandler);
    }
  }

  _checkTabParam() {
    const params = new URLSearchParams(window.location.search);
    const tab = params.get("tab");
    if (tab === "automations" || tab === "settings") {
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

  async _loadSessions() {
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

  async _openSession(sessionId) {
    try {
      const session = await this.hass.callWS({
        type: "selora_ai/get_session",
        session_id: sessionId,
      });
      this._activeSessionId = session.id;
      this._messages = session.messages || [];
      this._activeTab = "chat";
      if (this.narrow) this._showSidebar = false;
    } catch (err) {
      console.error("Failed to open session", err);
    }
  }

  async _newSession() {
    try {
      const { session_id } = await this.hass.callWS({
        type: "selora_ai/new_session",
      });
      this._activeSessionId = session_id;
      this._messages = [];
      this._activeTab = "chat";
      this._welcomeKey = (this._welcomeKey || 0) + 1;
      await this._loadSessions();
      if (this.narrow) this._showSidebar = false;
    } catch (err) {
      console.error("Failed to create session", err);
    }
  }

  async _newAutomationChat(name) {
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

  _renderNewAutomationDialog() {
    if (!this._showNewAutoDialog) return "";
    return html`
      <div
        class="modal-overlay"
        @click=${() => {
          this._showNewAutoDialog = false;
        }}
      >
        <div
          class="modal-content"
          style="max-width:420px;"
          @click=${(e) => e.stopPropagation()}
        >
          <h3 style="margin:0 0 16px;">New Automation</h3>
          <label
            style="font-size:13px;font-weight:500;display:block;margin-bottom:6px;"
            >Automation name</label
          >
          <div style="display:flex;gap:8px;align-items:center;">
            <input
              type="text"
              placeholder="e.g. Turn off lights at midnight"
              style="flex:1;padding:10px 12px;border:1px solid var(--divider-color);border-radius:8px;font-size:14px;background:var(--card-background-color);color:var(--primary-text-color);box-sizing:border-box;"
              .value=${this._newAutoName}
              @input=${(e) => {
                this._newAutoName = e.target.value;
              }}
              @keydown=${(e) => {
                if (e.key === "Enter")
                  this._newAutomationChat(this._newAutoName);
              }}
            />
            <button
              class="btn btn-outline"
              style="padding:8px 10px;flex-shrink:0;"
              title="AI Suggest"
              ?disabled=${this._suggestingName}
              @click=${() => this._suggestAutomationName()}
            >
              ${this._suggestingName
                ? html`<span class="spinner green"></span>`
                : html`<ha-icon
                    icon="mdi:auto-fix"
                    style="--mdc-icon-size:18px;"
                  ></ha-icon>`}
            </button>
          </div>
          ${this._suggestingName
            ? html`<div
                style="font-size:12px;color:var(--secondary-text-color);margin-top:6px;"
              >
                Asking AI for a suggestion…
              </div>`
            : ""}
          <div
            style="display:flex;justify-content:flex-end;gap:8px;margin-top:16px;"
          >
            <button
              class="btn btn-outline"
              @click=${() => {
                this._showNewAutoDialog = false;
              }}
            >
              Cancel
            </button>
            <button
              class="btn btn-primary"
              ?disabled=${!this._newAutoName?.trim()}
              @click=${() => this._newAutomationChat(this._newAutoName)}
            >
              <ha-icon
                icon="mdi:chat-processing-outline"
                style="--mdc-icon-size:14px;"
              ></ha-icon>
              Create in Chat
            </button>
          </div>
        </div>
      </div>
    `;
  }

  async _suggestAutomationName() {
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

  _deleteSession(sessionId, evt) {
    evt.stopPropagation();
    this._deleteConfirmSessionId = sessionId;
  }

  async _confirmDeleteSession() {
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

  _toggleSessionSelection(sessionId) {
    this._selectedSessionIds = {
      ...this._selectedSessionIds,
      [sessionId]: !this._selectedSessionIds[sessionId],
    };
  }

  _toggleSelectAllSessions() {
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

  _requestBulkDeleteSessions() {
    const count = Object.values(this._selectedSessionIds).filter(
      Boolean,
    ).length;
    if (count === 0) return;
    this._deleteConfirmSessionId = "__bulk__";
  }

  async _confirmBulkDeleteSessions() {
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

  async _loadSuggestions() {
    try {
      const suggestions = await this.hass.callWS({
        type: "selora_ai/get_suggestions",
      });
      this._suggestions = suggestions || [];
    } catch (err) {
      console.error("Failed to load suggestions", err);
    }
  }

  async _triggerGenerateSuggestions() {
    this._generatingSuggestions = true;
    try {
      const newSuggestions = await this.hass.callWS({
        type: "selora_ai/generate_suggestions",
      });
      // Merge new suggestions with existing — don't replace
      const existingAliases = new Set(
        (this._suggestions || []).map((s) => {
          const a = s.automation || s.automation_data || {};
          return (a.alias || "").toLowerCase();
        }),
      );
      const added = [];
      for (const s of newSuggestions || []) {
        const a = s.automation || s.automation_data || {};
        const alias = (a.alias || "").toLowerCase();
        if (!existingAliases.has(alias)) {
          added.push(s);
          existingAliases.add(alias);
        }
      }
      this._suggestions = [...added, ...this._suggestions];
      // Reload proactive suggestions to stay in sync
      await this._loadProactiveSuggestions();
      if (added.length > 0) {
        this._showToast(
          `Generated ${added.length} new recommendation(s)`,
          "success",
        );
      } else {
        this._showToast(
          "Analysis complete — no new suggestions at this time",
          "info",
        );
      }
    } catch (err) {
      console.error("Failed to generate suggestions", err);
      this._showToast(
        "Failed to generate suggestions: " + (err.message || "unknown error"),
        "error",
      );
    } finally {
      this._generatingSuggestions = false;
    }
  }

  async _loadAutomations() {
    try {
      const automations = await this.hass.callWS({
        type: "selora_ai/get_automations",
      });
      this._automations = (automations || []).reverse();
      const validIds = new Set(
        this._automations.map((a) => a.automation_id).filter(Boolean),
      );
      this._selectedAutomationIds = Object.fromEntries(
        Object.entries(this._selectedAutomationIds || {}).filter(
          ([id, selected]) => selected && validIds.has(id),
        ),
      );
    } catch (err) {
      console.error("Failed to load automations", err);
    }
    // Also load proactive suggestions
    this._loadProactiveSuggestions();
  }

  async _loadProactiveSuggestions() {
    this._loadingProactive = true;
    try {
      const suggestions = await this.hass.callWS({
        type: "selora_ai/get_proactive_suggestions",
        status: "pending",
      });
      // Deduplicate by description
      const seen = new Set();
      this._proactiveSuggestions = (suggestions || []).filter((s) => {
        const key = (s.description || "").toLowerCase().trim();
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
    } catch (err) {
      console.error("Failed to load proactive suggestions", err);
      this._proactiveSuggestions = [];
    }
    this._loadingProactive = false;
  }

  async _acceptProactiveSuggestion(suggestionId, editedYaml) {
    this._acceptingProactive = {
      ...this._acceptingProactive,
      [suggestionId]: true,
    };
    try {
      if (editedYaml) {
        await this.hass.callWS({
          type: "selora_ai/accept_suggestion_with_edits",
          suggestion_id: suggestionId,
          automation_yaml: editedYaml,
        });
      } else {
        await this.hass.callWS({
          type: "selora_ai/update_proactive_suggestion",
          suggestion_id: suggestionId,
          action: "accepted",
        });
      }
      this._showToast("Suggestion accepted — automation created", "success");
      await this._loadAutomations();
    } catch (err) {
      console.error("Failed to accept suggestion", err);
      this._showToast("Failed to accept suggestion", "error");
    }
    this._acceptingProactive = {
      ...this._acceptingProactive,
      [suggestionId]: false,
    };
  }

  async _dismissProactiveSuggestion(suggestionId) {
    this._dismissingProactive = {
      ...this._dismissingProactive,
      [suggestionId]: true,
    };
    try {
      await this.hass.callWS({
        type: "selora_ai/update_proactive_suggestion",
        suggestion_id: suggestionId,
        action: "dismissed",
      });
      this._proactiveSuggestions = this._proactiveSuggestions.filter(
        (s) => s.suggestion_id !== suggestionId,
      );
      this._showToast("Suggestion dismissed", "info");
    } catch (err) {
      console.error("Failed to dismiss suggestion", err);
    }
    this._dismissingProactive = {
      ...this._dismissingProactive,
      [suggestionId]: false,
    };
  }

  async _snoozeProactiveSuggestion(suggestionId) {
    try {
      await this.hass.callWS({
        type: "selora_ai/update_proactive_suggestion",
        suggestion_id: suggestionId,
        action: "snoozed",
      });
      this._proactiveSuggestions = this._proactiveSuggestions.filter(
        (s) => s.suggestion_id !== suggestionId,
      );
      this._showToast("Suggestion snoozed for 24h", "info");
    } catch (err) {
      console.error("Failed to snooze suggestion", err);
    }
  }

  async _triggerPatternScan() {
    this._loadingProactive = true;
    try {
      const result = await this.hass.callWS({
        type: "selora_ai/trigger_pattern_scan",
      });
      this._showToast(
        `Scan complete — ${result.patterns_found} patterns found`,
        "success",
      );
      await this._loadProactiveSuggestions();
    } catch (err) {
      console.error("Pattern scan failed", err);
      this._showToast("Pattern scan failed", "error");
    }
    this._loadingProactive = false;
  }

  async _loadConfig() {
    try {
      const config = await this.hass.callWS({ type: "selora_ai/get_config" });
      this._config = config;
      this._newApiKey = "";
    } catch (err) {
      console.error("Failed to load config", err);
    }
  }

  async _saveConfig() {
    if (!this._config || this._savingConfig) return;
    this._savingConfig = true;
    try {
      const payload = { ...this._config };
      const provider = this._config.llm_provider;
      if (provider === "openai") {
        if (this._newApiKey.trim()) {
          payload.openai_api_key = this._newApiKey.trim();
        } else {
          delete payload.openai_api_key;
        }
      } else {
        if (this._newApiKey.trim()) {
          payload.anthropic_api_key = this._newApiKey.trim();
        } else {
          delete payload.anthropic_api_key;
        }
      }
      delete payload.anthropic_api_key_hint;
      delete payload.anthropic_api_key_set;
      delete payload.openai_api_key_hint;
      delete payload.openai_api_key_set;

      await this.hass.callWS({
        type: "selora_ai/update_config",
        config: payload,
      });
      this._newApiKey = "";
      await this._loadConfig();
      this._showToast("Configuration saved.", "success");
    } catch (err) {
      this._showToast("Failed to save configuration: " + err.message, "error");
    } finally {
      this._savingConfig = false;
    }
  }

  // -------------------------------------------------------------------------
  // Messaging
  // -------------------------------------------------------------------------

  _quickStart(message) {
    this._input = message;
    this._sendMessage();
  }

  async _sendMessage() {
    if (!this._input.trim() || this._loading) return;
    const userMsg = this._input;
    this._messages = [...this._messages, { role: "user", content: userMsg }];
    this._input = "";
    this._loading = true;

    const assistantMsg = { role: "assistant", content: "", _streaming: true };
    this._messages = [...this._messages, assistantMsg];

    try {
      const subscribePayload = {
        type: "selora_ai/chat_stream",
        message: userMsg,
      };
      if (this._activeSessionId) {
        subscribePayload.session_id = this._activeSessionId;
      }

      this._streaming = true;
      this._streamUnsub = await this.hass.connection.subscribeMessage(
        (event) => {
          if (event.type === "token") {
            assistantMsg.content += event.text;
            this._messages = [...this._messages];
            this._loading = false;
            this._requestScrollChat();
          } else if (event.type === "done") {
            assistantMsg.content = event.response || assistantMsg.content;
            assistantMsg.automation = event.automation || null;
            assistantMsg.automation_yaml = event.automation_yaml || null;
            assistantMsg.automation_status = event.automation
              ? "pending"
              : null;
            assistantMsg.automation_message_index =
              event.automation_message_index ?? null;
            assistantMsg.refining_automation_id =
              event.refining_automation_id || null;
            assistantMsg._streaming = false;
            this._messages = [...this._messages];
            this._loading = false;
            this._streaming = false;
            this._streamUnsub = null;
            if (event.validation_error) {
              this._showToast(
                `Automation validation failed: ${event.validation_error}`,
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
            assistantMsg.content =
              "Sorry, I encountered an error: " + event.message;
            assistantMsg._streaming = false;
            this._messages = [...this._messages];
            this._loading = false;
            this._streaming = false;
            this._streamUnsub = null;
          }
        },
        subscribePayload,
      );
    } catch (err) {
      assistantMsg.content = "Sorry, I encountered an error: " + err.message;
      assistantMsg._streaming = false;
      this._messages = [...this._messages];
      this._loading = false;
      this._streaming = false;
      this._streamUnsub = null;
    }
  }

  _stopStreaming() {
    if (this._streamUnsub) {
      this._streamUnsub();
      this._streamUnsub = null;
    }
    this._streaming = false;
    this._loading = false;
    // Mark the last assistant message as done
    const lastMsg = this._messages[this._messages.length - 1];
    if (lastMsg && lastMsg._streaming) {
      lastMsg._streaming = false;
      this._messages = [...this._messages];
    }
  }

  _requestScrollChat() {
    if (!this._scrollPending) {
      this._scrollPending = true;
      requestAnimationFrame(() => {
        this._scrollPending = false;
        const container = this.shadowRoot.getElementById("chat-messages");
        if (container) container.scrollTop = container.scrollHeight;
      });
    }
  }

  // -------------------------------------------------------------------------
  // Automation actions
  // -------------------------------------------------------------------------

  // -------------------------------------------------------------------------
  // Refinement helpers
  // -------------------------------------------------------------------------

  _getRefiningAutomationId(msgIndex = null) {
    const msg = msgIndex == null ? null : this._messages[msgIndex];
    if (msg?.refining_automation_id) return msg.refining_automation_id;
    if (msg?.automation_id) return msg.automation_id;
    if (msg?.automation?.id) return msg.automation.id;

    for (const m of this._messages) {
      if (m.automation_status === "refining") {
        if (m.automation_id) return m.automation_id;
        if (m.automation?.id) return m.automation.id;
      }
    }
    return null;
  }

  async _loadLineage(automationId) {
    this._loadingLineage = { ...this._loadingLineage, [automationId]: true };
    this.requestUpdate();
    try {
      const result = await this.hass.callWS({
        type: "selora_ai/get_automation_lineage",
        automation_id: automationId,
      });
      this._lineage = { ...this._lineage, [automationId]: result };
    } catch (err) {
      console.error("Failed to load lineage", err);
      this._lineage = { ...this._lineage, [automationId]: [] };
    } finally {
      this._loadingLineage = { ...this._loadingLineage, [automationId]: false };
      this.requestUpdate();
    }
  }

  async _acceptAutomation(msgIndex, automation) {
    try {
      const refiningId = this._getRefiningAutomationId(msgIndex);
      if (refiningId) {
        const yamlText = this._messages[msgIndex]?.automation_yaml || "";
        if (yamlText) {
          await this.hass.callWS({
            type: "selora_ai/update_automation_yaml",
            automation_id: refiningId,
            yaml_text: yamlText,
            session_id: this._activeSessionId,
            version_message: "Refined via chat",
          });
        } else {
          await this.hass.callWS({
            type: "selora_ai/create_automation",
            automation: automation,
            session_id: this._activeSessionId,
          });
        }
      } else {
        await this.hass.callWS({
          type: "selora_ai/create_automation",
          automation: automation,
          session_id: this._activeSessionId,
        });
      }
      await this.hass.callWS({
        type: "selora_ai/set_automation_status",
        session_id: this._activeSessionId,
        message_index: msgIndex,
        status: "saved",
      });
      const session = await this.hass.callWS({
        type: "selora_ai/get_session",
        session_id: this._activeSessionId,
      });
      this._messages = session.messages || [];
      await this._removeDraftForSession(this._activeSessionId);
      await this._loadAutomations();

      this._showToast(
        `Automation "${automation.alias}" ${refiningId ? "updated" : "created and enabled"}.`,
        "success",
      );
      this._activeTab = "automations";
    } catch (err) {
      this._showToast("Failed to save automation: " + err.message, "error");
    }
  }

  async _removeDraftForSession(sessionId) {
    if (!sessionId) return;
    try {
      const draft = this._automations.find(
        (a) => a._draft && a._linked_session === sessionId,
      );
      if (draft && draft._draft_id) {
        await this.hass.callWS({
          type: "selora_ai/remove_draft",
          draft_id: draft._draft_id,
        });
      }
    } catch (err) {
      console.error("Failed to remove draft for session", err);
    }
  }

  async _dismissDraft(draftId) {
    if (!draftId) return;
    try {
      await this.hass.callWS({
        type: "selora_ai/remove_draft",
        draft_id: draftId,
      });
      await this._loadAutomations();
      this._showToast("Draft dismissed.", "info");
    } catch (err) {
      console.error("Failed to dismiss draft", err);
      this._showToast("Failed to dismiss draft: " + err.message, "error");
    }
  }

  async _declineAutomation(msgIndex) {
    try {
      await this.hass.callWS({
        type: "selora_ai/set_automation_status",
        session_id: this._activeSessionId,
        message_index: msgIndex,
        status: "declined",
      });
      const session = await this.hass.callWS({
        type: "selora_ai/get_session",
        session_id: this._activeSessionId,
      });
      this._messages = session.messages || [];
    } catch (err) {
      console.error("Failed to decline automation", err);
    }
  }

  async _refineAutomation(msgIndex, automation, description) {
    // Mark the original proposal as "refining" so the card shows it's superseded
    try {
      await this.hass.callWS({
        type: "selora_ai/set_automation_status",
        session_id: this._activeSessionId,
        message_index: msgIndex,
        status: "refining",
      });
      const session = await this.hass.callWS({
        type: "selora_ai/get_session",
        session_id: this._activeSessionId,
      });
      this._messages = session.messages || [];
    } catch (err) {
      console.error("Failed to mark automation as refining", err);
    }

    // Pre-fill with rich context so the user just needs to describe the change
    const ctx = description ? ` (${description})` : "";
    this._input = `Refine "${automation.alias}"${ctx}: `;
    this.shadowRoot.querySelector("ha-textfield")?.focus();
  }

  async _createAutomationFromSuggestion(automation) {
    try {
      await this.hass.callWS({
        type: "selora_ai/create_automation",
        automation,
      });
      await this._loadAutomations();
      this._showToast(`Automation "${automation.alias}" created.`, "success");
    } catch (err) {
      this._showToast("Failed to create automation: " + err.message, "error");
    }
  }

  _discardSuggestion(suggestion) {
    this._suggestions = this._suggestions.filter((s) => s !== suggestion);
  }

  // Accept automation — if the user edited the YAML, send the edited version
  async _acceptAutomationWithEdits(msgIndex, automation, yamlKey) {
    const edited = this._editedYaml[yamlKey];
    const msg = this._messages[msgIndex] || {};
    const originalYaml = msg.automation_yaml || "";
    const refiningId = this._getRefiningAutomationId(msgIndex);

    if (edited && edited !== (this._originalYaml?.[yamlKey] ?? originalYaml)) {
      try {
        this._savingYaml = { ...this._savingYaml, [yamlKey]: true };
        this.requestUpdate();

        if (refiningId) {
          await this.hass.callWS({
            type: "selora_ai/update_automation_yaml",
            automation_id: refiningId,
            yaml_text: edited,
            session_id: this._activeSessionId,
            version_message: "Refined via chat (with edits)",
          });
        } else {
          await this.hass.callWS({
            type: "selora_ai/apply_automation_yaml",
            yaml_text: edited,
            session_id: this._activeSessionId,
          });
        }

        await this.hass.callWS({
          type: "selora_ai/set_automation_status",
          session_id: this._activeSessionId,
          message_index: msgIndex,
          status: "saved",
        });
        const session = await this.hass.callWS({
          type: "selora_ai/get_session",
          session_id: this._activeSessionId,
        });
        this._messages = session.messages || [];
        await this._loadAutomations();

        this._showToast(
          `Automation "${automation.alias}" ${refiningId ? "updated" : "created and enabled"}.`,
          "success",
        );
        this._activeTab = "automations";
      } catch (err) {
        this._showToast(
          "Failed to save automation from edited YAML: " + err.message,
          "error",
        );
      } finally {
        this._savingYaml = { ...this._savingYaml, [yamlKey]: false };
        this.requestUpdate();
      }
    } else {
      await this._acceptAutomation(msgIndex, automation);
    }
  }

  async _createSuggestionWithEdits(auto, yamlKey, originalYaml) {
    const edited = this._editedYaml[yamlKey];
    try {
      this._savingYaml = { ...this._savingYaml, [yamlKey]: true };
      this.requestUpdate();
      if (edited && edited !== originalYaml) {
        await this.hass.callWS({
          type: "selora_ai/apply_automation_yaml",
          yaml_text: edited,
        });
      } else {
        await this.hass.callWS({
          type: "selora_ai/create_automation",
          automation: auto,
        });
      }
      await this._loadAutomations();
      this._showToast(`Automation "${auto.alias}" created.`, "success");
    } catch (err) {
      this._showToast("Failed to create automation: " + err.message, "error");
    } finally {
      this._savingYaml = { ...this._savingYaml, [yamlKey]: false };
      this.requestUpdate();
    }
  }

  async _saveActiveAutomationYaml(automationId, yamlKey) {
    const edited = this._editedYaml[yamlKey];
    if (!edited) return;
    try {
      this._savingYaml = { ...this._savingYaml, [yamlKey]: true };
      this.requestUpdate();
      await this.hass.callWS({
        type: "selora_ai/update_automation_yaml",
        automation_id: automationId,
        yaml_text: edited,
      });
      // Clear edits and refresh
      this._editedYaml = { ...this._editedYaml, [yamlKey]: undefined };
      await this._loadAutomations();
      this._showToast("Automation YAML saved.", "success");
    } catch (err) {
      this._showToast("Failed to save changes: " + err.message, "error");
    } finally {
      this._savingYaml = { ...this._savingYaml, [yamlKey]: false };
      this.requestUpdate();
    }
  }

  _initYamlEdit(key, originalYaml) {
    if (this._editedYaml[key] === undefined) {
      this._editedYaml = { ...this._editedYaml, [key]: originalYaml };
    }
  }

  _onYamlInput(key, value) {
    this._editedYaml = { ...this._editedYaml, [key]: value };
    this.requestUpdate();
  }

  _goToSettings() {
    this._activeTab = "settings";
    this._loadConfig();
  }

  _updateConfig(key, value) {
    this._config = { ...this._config, [key]: value };
    this.requestUpdate();
  }

  _showToast(message, type = "info") {
    if (this._toastTimer) {
      clearTimeout(this._toastTimer);
      this._toastTimer = null;
    }
    this._toast = message;
    this._toastType = type;
    this._toastTimer = setTimeout(() => {
      this._toast = "";
      this._toastType = "info";
      this._toastTimer = null;
      this.requestUpdate();
    }, 3500);
    this.requestUpdate();
  }

  _dismissToast() {
    if (this._toastTimer) {
      clearTimeout(this._toastTimer);
      this._toastTimer = null;
    }
    this._toast = "";
    this._toastType = "info";
    this.requestUpdate();
  }

  // -------------------------------------------------------------------------
  // Scroll to bottom on new messages
  // -------------------------------------------------------------------------

  updated(changedProps) {
    if (changedProps.has("hass")) {
      this._checkTabParam();
    }
    // Process deferred "Create in Chat" once hass becomes available
    if (this.hass && this._pendingNewAutomation) {
      const name = this._pendingNewAutomation;
      this._pendingNewAutomation = null;
      this._newAutomationChat(name);
    }
    if (changedProps.has("_messages") && this._activeTab === "chat") {
      const container = this.shadowRoot.getElementById("chat-messages");
      if (container) container.scrollTop = container.scrollHeight;
    }
  }

  // -------------------------------------------------------------------------
  // Styles
  // -------------------------------------------------------------------------

  static get styles() {
    return css`
      :host {
        display: flex;
        height: 100%;
        background: var(--primary-background-color);
        color: var(--primary-text-color);
        font-family: var(--paper-font-body1_-_font-family, roboto, sans-serif);
      }

      /* ---- Sidebar (session list) ---- */
      .sidebar {
        width: 0;
        min-width: 0;
        display: flex;
        flex-direction: column;
        background: var(
          --sidebar-background-color,
          var(--card-background-color)
        );
        border-right: 1px solid var(--divider-color);
        overflow: hidden;
        transition:
          width 0.25s ease,
          min-width 0.25s ease;
      }
      .sidebar.open {
        width: 260px;
        min-width: 260px;
      }
      .sidebar-header {
        padding: 16px;
        font-size: 13px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: normal;
        opacity: 0.6;
        border-bottom: 1px solid var(--divider-color);
        display: flex;
        align-items: center;
        justify-content: space-between;
      }
      .session-list {
        flex: 1;
        overflow-y: auto;
      }
      .session-item {
        padding: 12px 16px;
        cursor: pointer;
        border-bottom: 1px solid var(--divider-color);
        display: flex;
        align-items: flex-start;
        gap: 8px;
        position: relative;
        transition: background 0.15s;
      }
      .session-item:hover {
        background: var(--secondary-background-color);
      }
      .session-item.active {
        background: rgba(245, 158, 11, 0.12);
        border-left: 3px solid #f59e0b;
      }
      .session-title {
        font-size: 13px;
        font-weight: 500;
        flex: 1;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .session-meta {
        font-size: 11px;
        opacity: 0.5;
        margin-top: 2px;
      }
      .session-delete {
        opacity: 0;
        cursor: pointer;
        color: var(--error-color, #f44336);
        transition: opacity 0.15s;
        flex-shrink: 0;
        align-self: center;
      }
      .session-item:hover .session-delete {
        opacity: 0.6;
      }
      .session-delete:hover {
        opacity: 1 !important;
      }
      .sidebar-select-btn {
        background: transparent;
        border: 1px solid var(--divider-color);
        color: var(--primary-text-color);
        font-size: 11px;
        font-weight: 700;
        padding: 4px 12px;
        border-radius: 6px;
        cursor: pointer;
        transition:
          background 0.15s,
          border-color 0.15s;
      }
      .sidebar-select-btn:hover {
        background: rgba(245, 158, 11, 0.1);
        border-color: #f59e0b;
      }
      .select-actions-bar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 8px 16px;
        border-bottom: 1px solid var(--divider-color);
        background: rgba(245, 158, 11, 0.06);
      }
      .select-all-label {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 12px;
        cursor: pointer;
        user-select: none;
      }
      .select-all-label input[type="checkbox"] {
        accent-color: #f59e0b;
        cursor: pointer;
      }
      .btn-delete-selected {
        display: flex;
        align-items: center;
        gap: 4px;
        background: transparent;
        border: 1px solid var(--error-color, #ef4444);
        color: var(--error-color, #ef4444);
        font-size: 11px;
        font-weight: 500;
        padding: 4px 10px;
        border-radius: 6px;
        cursor: pointer;
        transition:
          background 0.15s,
          color 0.15s;
      }
      .btn-delete-selected:hover:not([disabled]) {
        background: var(--error-color, #ef4444);
        color: #fff;
      }
      .btn-delete-selected[disabled] {
        opacity: 0.35;
        cursor: not-allowed;
      }
      .session-checkbox {
        accent-color: #f59e0b;
        cursor: pointer;
        flex-shrink: 0;
        margin-top: 2px;
      }
      .new-chat-btn {
        margin: 12px;
        display: block;
      }

      /* ---- Main area ---- */
      .main {
        flex: 1;
        display: flex;
        flex-direction: column;
        overflow: hidden;
      }
      .header {
        background: var(--app-header-background-color);
        color: var(--app-header-text-color);
        box-shadow: var(--card-box-shadow);
        z-index: 2;
        flex-shrink: 0;
      }
      .header-top {
        padding: 14px 16px;
        font-size: 18px;
        font-weight: 500;
        display: flex;
        align-items: center;
        gap: 10px;
      }
      .header-top ha-icon-button {
        margin-right: 4px;
        display: inline-flex;
      }
      .tabs {
        display: flex;
        padding: 0 8px;
        border-top: 1px solid rgba(255, 255, 255, 0.1);
      }
      .tab {
        padding: 10px 20px;
        cursor: pointer;
        font-weight: 500;
        font-size: 13px;
        text-transform: uppercase;
        opacity: 0.65;
        border-bottom: 3px solid transparent;
        transition: all 0.2s;
      }
      .tab:hover {
        opacity: 1;
      }
      .tab.active {
        opacity: 1;
        border-bottom-color: var(--accent-color, #ff9800);
      }

      /* ---- Chat ---- */
      .chat-pane {
        flex: 1;
        display: flex;
        flex-direction: column;
        overflow: hidden;
      }
      .chat-messages {
        flex: 1;
        overflow-y: auto;
        padding: 20px 16px;
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      @keyframes fadeInUp {
        from {
          opacity: 0;
          transform: translateY(18px);
        }
        to {
          opacity: 1;
          transform: translateY(0);
        }
      }
      @keyframes logoEntrance {
        0% {
          opacity: 0;
          transform: scale(0.6) translateY(12px);
        }
        60% {
          opacity: 1;
          transform: scale(1.06) translateY(-2px);
        }
        100% {
          opacity: 1;
          transform: scale(1) translateY(0);
        }
      }
      .empty-state {
        flex: 1;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        opacity: 0.45;
        gap: 12px;
        padding: 32px;
        text-align: center;
        animation: fadeInUp 0.5s ease both;
      }
      .empty-state.welcome {
        opacity: 1;
        gap: 0;
      }
      .empty-state.welcome > * {
        animation: fadeInUp 0.5s ease both;
      }
      .empty-state.welcome > img:first-child {
        animation: logoEntrance 0.7s cubic-bezier(0.34, 1.56, 0.64, 1) both;
      }
      .empty-state.welcome > :nth-child(2) {
        animation-delay: 0.15s;
      }
      .empty-state.welcome > :nth-child(3) {
        animation-delay: 0.25s;
      }
      .empty-state.welcome > :nth-child(4) {
        animation-delay: 0.35s;
      }
      .empty-state.welcome > :nth-child(5) {
        animation-delay: 0.4s;
      }
      .empty-state.welcome > :nth-child(6) {
        animation-delay: 0.45s;
      }
      .empty-state.welcome > :nth-child(7) {
        animation-delay: 0.5s;
      }
      .empty-state ha-icon {
        --mdc-icon-size: 56px;
      }
      .welcome-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
      }
      .welcome-card:active {
        transform: translateY(0);
      }
      .message-row {
        display: flex;
        flex-direction: column;
      }
      .bubble {
        max-width: 82%;
        padding: 12px 16px;
        border-radius: 16px;
        font-size: 14px;
        line-height: 1.5;
        word-wrap: break-word;
      }
      .bubble.user {
        align-self: flex-end;
        background: #f59e0b !important;
        color: #1a1a1a !important;
        border-bottom-right-radius: 4px;
      }
      .bubble.assistant {
        align-self: flex-start;
        background: var(--card-background-color);
        box-shadow: var(--card-box-shadow);
        border-bottom-left-radius: 4px;
      }
      .bubble-meta {
        font-size: 10px;
        opacity: 0.5;
        margin-top: 2px;
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .bubble.user + .bubble-meta {
        align-self: flex-end;
      }
      .bubble.assistant + .bubble-meta {
        align-self: flex-start;
      }
      .copy-msg-row {
        display: flex;
        justify-content: flex-end;
        margin-top: 4px;
      }
      .copy-msg-btn {
        background: none;
        border: none;
        padding: 2px 4px;
        cursor: pointer;
        opacity: 0;
        transition:
          opacity 0.15s,
          color 0.15s;
        color: inherit;
        line-height: 1;
        border-radius: 4px;
      }
      .message-row:hover .copy-msg-btn {
        opacity: 0.7;
      }
      .copy-msg-btn:hover {
        opacity: 1 !important;
      }
      .copy-msg-btn.copied {
        opacity: 1 !important;
        color: var(--success-color, #4caf50);
      }
      .bubble.assistant strong {
        color: #f59e0b;
      }

      /* ---- Automation proposal card ---- */
      .proposal-card {
        margin-top: 12px;
        border: 1px solid #f59e0b;
        border-radius: 10px;
        overflow: hidden;
        background: var(--primary-background-color);
      }
      .proposal-header {
        background: rgba(245, 158, 11, 0.1);
        padding: 10px 14px;
        font-size: 12px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: normal;
        display: flex;
        align-items: center;
        gap: 6px;
        color: #f59e0b;
      }
      .proposal-body {
        padding: 14px;
      }
      .proposal-body .flow-chart {
        align-items: flex-start;
      }
      .proposal-body .flow-section {
        text-align: left;
      }
      .proposal-name {
        font-weight: 600;
        font-size: 15px;
        margin-bottom: 8px;
      }
      .proposal-description {
        font-size: 13px;
        color: var(--secondary-text-color);
        margin-bottom: 12px;
        line-height: 1.5;
        padding: 10px 12px;
        background: rgba(var(--rgb-accent-color, 255, 152, 0), 0.08);
        border-left: 3px solid var(--accent-color, #ff9800);
        border-radius: 0 6px 6px 0;
      }
      .proposal-description-label {
        font-size: 10px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: normal;
        opacity: 0.6;
        margin-bottom: 4px;
      }
      .yaml-toggle {
        font-size: 12px;
        cursor: pointer;
        opacity: 0.6;
        display: flex;
        align-items: center;
        gap: 4px;
        margin-bottom: 8px;
        user-select: none;
      }
      .yaml-toggle:hover {
        opacity: 1;
      }
      textarea.yaml-editor {
        width: 100%;
        box-sizing: border-box;
        background: #1e1e2e;
        color: #cdd6f4;
        padding: 10px 12px;
        border-radius: 6px;
        font-size: 11px;
        font-family: "Fira Code", "Cascadia Code", monospace;
        line-height: 1.5;
        border: 1px solid rgba(255, 255, 255, 0.12);
        resize: vertical;
        min-height: 140px;
        outline: none;
        transition: border-color 0.15s;
      }
      textarea.yaml-editor:focus {
        border-color: #f59e0b;
      }
      .yaml-edit-bar {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-top: 6px;
        flex-wrap: wrap;
      }
      .yaml-unsaved {
        font-size: 11px;
        color: var(--warning-color, #ff9800);
        display: flex;
        align-items: center;
        gap: 4px;
        flex: 1;
      }
      pre.yaml {
        background: #1e1e2e;
        color: #cdd6f4;
        padding: 10px 12px;
        border-radius: 6px;
        font-size: 11px;
        overflow-x: auto;
        font-family: "Fira Code", "Cascadia Code", monospace;
        margin: 0 0 12px;
        max-height: 200px;
        overflow-y: auto;
      }
      .proposal-verify {
        font-size: 12px;
        font-style: italic;
        opacity: 0.65;
        margin-bottom: 10px;
      }
      .proposal-actions {
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
      }
      .proposal-actions mwc-button[raised] {
        --mdc-theme-primary: var(--success-color, #4caf50);
      }

      /* Declined / saved states */
      .proposal-status {
        padding: 8px 12px;
        font-size: 12px;
        border-radius: 6px;
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .proposal-status.saved {
        background: rgba(76, 175, 80, 0.12);
        color: var(--success-color, #4caf50);
      }
      .proposal-status.declined {
        background: rgba(158, 158, 158, 0.12);
        color: var(--secondary-text-color);
      }

      /* ---- Automation flowchart ---- */
      .flow-chart {
        display: flex;
        flex-direction: column;
        align-items: center;
        margin: 10px 0 12px;
        font-size: 12px;
      }
      .flow-section {
        width: 100%;
        text-align: center;
      }
      .flow-label {
        font-size: 9px;
        font-weight: 800;
        letter-spacing: normal;
        text-transform: uppercase;
        opacity: 0.5;
        margin-bottom: 4px;
      }
      .flow-node {
        display: inline-block;
        padding: 6px 12px;
        border-radius: 8px;
        margin-bottom: 4px;
        max-width: 100%;
        word-break: break-word;
        font-size: 12px;
        line-height: 1.4;
      }
      .flow-node + .flow-node {
        margin-top: 3px;
      }
      .trigger-node,
      .condition-node,
      .action-node {
        background: rgba(var(--rgb-primary-text-color, 255, 255, 255), 0.06);
        border: 1px solid
          rgba(var(--rgb-primary-text-color, 255, 255, 255), 0.15);
        color: var(--primary-text-color);
      }
      .flow-arrow {
        font-size: 16px;
        line-height: 1;
        opacity: 0.35;
        padding: 3px 0;
        text-align: center;
      }
      .flow-arrow-sm {
        font-size: 13px;
        line-height: 1;
        opacity: 0.3;
        padding: 2px 0;
        text-align: center;
      }

      /* ---- Toggle switch ---- */
      .toggle-row {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-top: 10px;
      }
      .toggle-switch {
        position: relative;
        width: 40px;
        height: 22px;
        flex-shrink: 0;
        cursor: pointer;
      }
      .toggle-switch input {
        opacity: 0;
        width: 0;
        height: 0;
        position: absolute;
      }
      .toggle-track {
        position: absolute;
        inset: 0;
        border-radius: 11px;
        background: var(--divider-color);
        border: 1px solid rgba(0, 0, 0, 0.15);
        transition: background 0.2s;
      }
      .toggle-track.on {
        background: #f59e0b;
        border-color: #d97706;
      }
      .toggle-thumb {
        position: absolute;
        top: 2px;
        left: 2px;
        width: 16px;
        height: 16px;
        border-radius: 50%;
        background: white;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
        transition: left 0.2s;
      }
      .toggle-track.on .toggle-thumb {
        left: 20px;
      }
      .toggle-label {
        font-size: 12px;
        font-weight: 600;
        color: var(--secondary-text-color);
      }
      .toggle-label.on {
        color: #f59e0b;
      }

      /* ---- Card action buttons ---- */
      .card-actions {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
        margin-top: 12px;
        padding-top: 12px;
        border-top: 1px solid var(--divider-color);
      }
      .btn {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 6px 14px;
        border-radius: 6px;
        font-size: 12px;
        font-weight: 600;
        cursor: pointer;
        border: 1.5px solid transparent;
        background: transparent;
        transition:
          background 0.15s,
          opacity 0.15s;
        user-select: none;
      }
      .btn:hover {
        opacity: 0.85;
      }
      .btn-primary {
        background: #f59e0b;
        border-color: #f59e0b;
        color: #1a1a1a;
      }
      .btn-success {
        background: var(--success-color, #4caf50);
        border-color: var(--success-color, #4caf50);
        color: white;
      }
      .btn-outline {
        border-color: var(--divider-color);
        color: var(--primary-text-color);
        background: var(--card-background-color);
      }
      .btn-outline:hover {
        border-color: #f59e0b;
        color: #f59e0b;
      }
      .btn-danger {
        border-color: var(--error-color, #f44336);
        color: var(--error-color, #f44336);
        background: transparent;
      }
      .btn-danger:hover {
        background: rgba(244, 67, 54, 0.08);
      }
      .btn-warning {
        border-color: var(--warning-color, #ff9800);
        color: var(--warning-color, #ff9800);
        background: transparent;
      }
      .btn-warning:hover {
        background: rgba(255, 152, 0, 0.08);
      }

      /* ---- Burger menu ---- */
      .burger-menu-wrapper {
        position: relative;
      }
      .burger-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 28px;
        height: 28px;
        border-radius: 6px;
        border: 1px solid var(--divider-color);
        background: var(--card-background-color);
        cursor: pointer;
        color: var(--secondary-text-color);
        transition: background 0.15s;
      }
      .burger-btn:hover {
        background: rgba(0, 0, 0, 0.06);
        color: var(--primary-text-color);
      }
      .burger-dropdown {
        position: absolute;
        right: 0;
        top: 32px;
        background: var(--card-background-color);
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
        z-index: 100;
        min-width: 140px;
        overflow: hidden;
      }
      .burger-item {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 8px 14px;
        font-size: 12px;
        font-weight: 500;
        cursor: pointer;
        color: var(--primary-text-color);
        border: none;
        background: none;
        width: 100%;
        text-align: left;
      }
      .burger-item:hover {
        background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.08);
      }
      .burger-item.danger {
        color: var(--error-color, #f44336);
      }
      .burger-item.danger:hover {
        background: rgba(244, 67, 54, 0.08);
      }
      .rename-input {
        flex: 1;
        font-size: 14px;
        font-weight: 600;
        border: 1px solid #f59e0b;
        border-radius: 6px;
        padding: 4px 8px;
        outline: none;
        background: var(--card-background-color, #fff);
        color: var(--primary-text-color);
        min-width: 0;
      }
      .rename-save-btn {
        background: #f59e0b;
        border: none;
        border-radius: 6px;
        color: #fff;
        cursor: pointer;
        padding: 4px 6px;
        margin-left: 4px;
        line-height: 1;
        display: flex;
        align-items: center;
      }
      .rename-save-btn:hover {
        background: #d97706;
      }

      /* ---- Card inline tabs (Flow / YAML / History) ---- */
      .card-tabs {
        display: flex;
        align-items: center;
        gap: 0;
        margin: 8px 0 0;
        border-top: 1px solid var(--divider-color);
        padding-top: 8px;
        font-size: 12px;
      }
      .card-tabs .label {
        font-size: 11px;
        opacity: 0.5;
        margin-right: 8px;
        white-space: nowrap;
      }
      .card-tab {
        padding: 4px 10px;
        border: none;
        background: none;
        font-size: 12px;
        font-weight: 500;
        color: var(--secondary-text-color);
        cursor: pointer;
        border-bottom: 2px solid transparent;
        transition:
          color 0.15s,
          border-color 0.15s;
        display: inline-flex;
        align-items: center;
        gap: 4px;
      }
      .card-tab:hover {
        color: var(--primary-text-color);
      }
      .card-tab.active {
        color: #f59e0b;
        border-bottom-color: #f59e0b;
      }
      .card-chevron {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        transition: transform 0.25s ease;
        cursor: pointer;
        opacity: 0.5;
        --mdc-icon-size: 16px;
        flex-shrink: 0;
      }
      .card-chevron:hover {
        opacity: 0.8;
      }
      .card-chevron.open {
        transform: rotate(180deg);
      }
      .card-tab-sep {
        color: var(--divider-color);
        font-size: 12px;
        user-select: none;
      }

      /* ---- Filter input ---- */
      .sub-tabs {
        display: flex;
        gap: 0;
        margin-bottom: 12px;
        justify-content: center;
      }
      .sub-tab {
        padding: 8px 18px;
        border: none;
        background: none;
        font-size: 14px;
        font-weight: 500;
        color: var(--secondary-text-color);
        cursor: pointer;
        border-bottom: 2px solid transparent;
        transition:
          color 0.15s,
          border-color 0.15s;
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .sub-tab:hover {
        color: var(--primary-text-color);
      }
      .sub-tab.active {
        color: #f59e0b;
        border-bottom-color: #f59e0b;
      }
      .sub-tab .badge {
        background: #f59e0b;
        color: #000;
        border-radius: 10px;
        padding: 1px 7px;
        font-size: 11px;
        font-weight: 600;
        min-width: 16px;
        text-align: center;
      }
      .filter-row {
        display: flex;
        align-items: center;
        justify-content: center;
        margin-bottom: 12px;
        gap: 12px;
      }
      .filter-input-wrap {
        display: flex;
        align-items: center;
        gap: 6px;
        background: var(--card-background-color);
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        padding: 4px 10px;
        flex: 0 1 400px;
      }
      .filter-input-wrap input {
        border: none;
        background: transparent;
        color: var(--primary-text-color);
        font-size: 13px;
        outline: none;
        flex: 1;
        min-width: 0;
      }
      .filter-input-wrap ha-icon {
        --mdc-icon-size: 16px;
        color: var(--secondary-text-color);
        flex-shrink: 0;
      }
      .bulk-select-all {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        font-size: 12px;
        color: var(--secondary-text-color);
      }
      .bulk-select-all input {
        width: 14px;
        height: 14px;
        margin: 0;
        accent-color: var(--primary-color);
      }
      .bulk-actions-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        margin: -2px 0 12px;
        padding: 8px 10px;
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        background: var(--secondary-background-color);
      }
      .bulk-actions-row .left {
        font-size: 12px;
        font-weight: 600;
      }
      .bulk-actions-row .actions {
        display: flex;
        align-items: center;
        gap: 6px;
        flex-wrap: wrap;
      }
      .card-select {
        display: inline-flex;
        align-items: center;
        margin-right: 6px;
      }
      .card-select input {
        width: 14px;
        height: 14px;
        margin: 0;
        accent-color: var(--primary-color);
        cursor: pointer;
      }

      /* ---- Status indicator (inline) ---- */
      .status-indicator {
        font-size: 11px;
        font-weight: 600;
        padding: 2px 8px;
        border-radius: 10px;
        flex-shrink: 0;
      }
      .status-indicator.on {
        color: var(--success-color, #4caf50);
        background: rgba(76, 175, 80, 0.12);
      }
      .status-indicator.off {
        color: var(--secondary-text-color);
        background: rgba(158, 158, 158, 0.12);
      }
      .btn-ghost {
        border-color: transparent;
        color: var(--secondary-text-color);
        background: transparent;
        font-size: 11px;
        padding: 4px 8px;
      }
      .btn-ghost:hover {
        color: var(--primary-text-color);
        background: rgba(0, 0, 0, 0.06);
        border-color: var(--divider-color);
      }
      .btn-ghost.active {
        color: #b45309;
        border-color: rgba(245, 158, 11, 0.35);
        background: rgba(245, 158, 11, 0.05);
      }
      .expand-toggle {
        font-size: 11px;
        opacity: 0.55;
        cursor: pointer;
        display: flex;
        align-items: center;
        gap: 4px;
        user-select: none;
        padding: 4px 0;
      }
      .expand-toggle:hover {
        opacity: 1;
      }

      /* ---- Spinner ---- */
      .spinner {
        display: inline-block;
        width: 18px;
        height: 18px;
        border: 2.5px solid rgba(0, 0, 0, 0.1);
        border-top-color: #f59e0b;
        border-radius: 50%;
        animation: spin 0.7s linear infinite;
      }
      .spinner.green {
        border-color: rgba(76, 175, 80, 0.2);
        border-top-color: var(--success-color, #4caf50);
      }
      @keyframes spin {
        to {
          transform: rotate(360deg);
        }
      }

      /* ---- Modal overlay ---- */
      .modal-overlay {
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.5);
        z-index: 10001;
        display: flex;
        align-items: center;
        justify-content: center;
      }
      .modal-content {
        background: var(--card-background-color, #fff);
        border-radius: 12px;
        padding: 24px;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        width: 90%;
      }

      /* ---- Automations grid (flex columns for independent heights) ---- */
      .automations-grid {
        display: flex;
        gap: 10px;
        margin-bottom: 14px;
      }
      .automations-grid .masonry-col {
        flex: 1;
        display: flex;
        flex-direction: column;
        gap: 10px;
        min-width: 0;
      }
      @media (max-width: 600px) {
        .automations-grid {
          flex-direction: column;
        }
      }
      .pagination {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 12px;
        padding: 12px 0;
      }
      .page-info {
        font-size: 12px;
        opacity: 0.6;
      }
      .per-page-label {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 12px;
        font-weight: 600;
        white-space: nowrap;
      }
      .per-page-select {
        font-size: 12px;
        font-weight: 600;
        padding: 4px 8px;
        border-radius: 6px;
        border: 1px solid var(--divider-color);
        background: var(--primary-background-color);
        color: var(--primary-text-color);
        cursor: pointer;
      }
      .automations-grid .card {
        margin-bottom: 0;
        padding: 12px 14px;
        display: flex;
        flex-direction: column;
        min-width: 0;
      }
      .automations-grid .card-header {
        margin-bottom: 4px;
        align-items: center;
      }
      .automations-grid .card h3 {
        font-size: 13px;
        line-height: 1.3;
        flex: 1;
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .automations-grid .card-meta {
        font-size: 11px;
        color: var(--secondary-text-color);
        opacity: 0.7;
      }

      /* ---- Automation detail drawer (below grid) ---- */
      .automation-detail-drawer {
        background: var(--card-background-color);
        border: 1px solid var(--divider-color);
        border-radius: 10px;
        padding: 16px;
        margin-bottom: 14px;
        box-shadow: var(--card-box-shadow);
      }
      .automation-detail-drawer .detail-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 12px;
      }
      .automation-detail-drawer .detail-header h3 {
        margin: 0;
        font-size: 16px;
      }

      /* ---- Chat input ---- */
      .chat-input {
        padding: 12px 16px;
        background: var(--card-background-color);
        border-top: 1px solid var(--divider-color);
        display: flex;
        gap: 10px;
        align-items: center;
        flex-shrink: 0;
      }
      .typing-bubble {
        align-self: flex-start;
        background-color: var(--card-background-color);
        box-shadow: var(--card-box-shadow);
        border-radius: 18px;
        border-bottom-left-radius: 4px;
        padding: 16px 22px;
        display: flex;
        align-items: center;
        gap: 5px;
      }
      .typing-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background-color: var(--secondary-text-color);
        animation: typingBounce 1.4s infinite ease-in-out both;
      }
      .typing-dot:nth-child(1) {
        animation-delay: 0s;
      }
      .typing-dot:nth-child(2) {
        animation-delay: 0.2s;
      }
      .typing-dot:nth-child(3) {
        animation-delay: 0.4s;
      }
      @keyframes typingBounce {
        0%,
        80%,
        100% {
          transform: scale(0.6);
          opacity: 0.4;
        }
        40% {
          transform: scale(1);
          opacity: 1;
        }
      }
      .streaming-cursor::after {
        content: "";
        display: inline-block;
        width: 2px;
        height: 1em;
        background-color: var(--primary-text-color);
        margin-left: 2px;
        vertical-align: text-bottom;
        animation: blink 0.7s step-end infinite;
      }
      @keyframes blink {
        50% {
          opacity: 0;
        }
      }

      /* ---- Scroll view (automations / settings) ---- */
      .scroll-view {
        flex: 1;
        overflow-y: auto;
        padding: 16px 24px;
        max-width: 1200px;
        margin: 0 auto;
        width: 100%;
        box-sizing: border-box;
      }
      .card {
        background: var(--card-background-color);
        border-radius: 10px;
        padding: 16px;
        margin-bottom: 14px;
        box-shadow: var(--card-box-shadow);
        border: 1px solid var(--divider-color);
      }
      .card-header {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        margin-bottom: 10px;
      }
      .card h3 {
        margin: 0;
        font-size: 16px;
      }
      .card p {
        margin: 6px 0;
        color: var(--secondary-text-color);
        font-size: 13px;
      }
      .chip {
        padding: 3px 9px;
        border-radius: 10px;
        font-size: 10px;
        font-weight: 700;
        color: white;
      }
      .chip.ai-managed {
        background: #f59e0b;
      }
      .chip.user-managed {
        background: #9e9e9e;
      }
      .chip.suggestion {
        background: #f59e0b;
      }
      pre {
        background: #1e1e2e;
        color: #cdd6f4;
        padding: 10px;
        border-radius: 6px;
        font-size: 11px;
        overflow-x: auto;
      }

      /* ---- Settings ---- */
      .settings-form {
        max-width: 600px;
        margin: 0 auto;
      }
      .form-group {
        margin-bottom: 22px;
      }
      .form-group label {
        display: block;
        margin-bottom: 6px;
        font-weight: 500;
        font-size: 14px;
      }
      .key-hint {
        font-size: 12px;
        opacity: 0.6;
        font-family: monospace;
        padding: 4px 8px;
        background: var(--secondary-background-color);
        border-radius: 4px;
        display: inline-block;
        margin-top: 4px;
      }
      .key-not-set {
        font-size: 12px;
        opacity: 0.5;
        font-style: italic;
        margin-top: 4px;
      }
      .save-bar {
        margin-top: 28px;
        display: flex;
        justify-content: flex-end;
      }

      /* Narrow overrides — sidebar overlays on small screens */
      :host([narrow]) .sidebar {
        position: absolute;
        left: 0;
        top: 0;
        bottom: 0;
        z-index: 10;
        width: 0;
        min-width: 0;
        transform: translateX(-100%);
        transition:
          transform 0.25s ease,
          width 0.25s ease,
          min-width 0.25s ease;
        box-shadow: 2px 0 8px rgba(0, 0, 0, 0.2);
      }
      :host([narrow]) .sidebar.open {
        width: 260px;
        min-width: 260px;
        transform: translateX(0);
      }

      .toast {
        position: fixed;
        right: 16px;
        bottom: 16px;
        z-index: 10050;
        max-width: min(420px, calc(100vw - 32px));
        padding: 10px 12px;
        border-radius: 10px;
        color: #fff;
        font-size: 13px;
        line-height: 1.4;
        box-shadow: 0 10px 28px rgba(0, 0, 0, 0.35);
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .toast.info {
        background: #1f6feb;
      }
      .toast.success {
        background: #198754;
      }
      .toast.error {
        background: #dc3545;
      }
      .toast-close {
        margin-left: auto;
        cursor: pointer;
        opacity: 0.85;
      }
      .toast-close:hover {
        opacity: 1;
      }
    `;
  }

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  render() {
    return html`
      <div class="sidebar ${this._showSidebar ? "open" : ""}" part="sidebar">
        <div class="sidebar-header">
          <span>Conversations</span>
          ${this._sessions.length > 0
            ? html`
                ${this._selectChatsMode
                  ? html`
                      <button
                        class="sidebar-select-btn"
                        @click=${() => {
                          this._selectChatsMode = false;
                          this._selectedSessionIds = {};
                        }}
                      >
                        Done
                      </button>
                    `
                  : html`
                      <button
                        class="sidebar-select-btn"
                        @click=${() => {
                          this._selectChatsMode = true;
                        }}
                      >
                        Select
                      </button>
                    `}
              `
            : ""}
        </div>
        ${this._selectChatsMode
          ? html`
              <div class="select-actions-bar">
                <label
                  class="select-all-label"
                  @click=${() => this._toggleSelectAllSessions()}
                >
                  <input
                    type="checkbox"
                    .checked=${this._sessions.length > 0 &&
                    this._sessions.every((s) => this._selectedSessionIds[s.id])}
                  />
                  <span>Select all</span>
                </label>
                <button
                  class="btn-delete-selected"
                  ?disabled=${Object.values(this._selectedSessionIds).filter(
                    Boolean,
                  ).length === 0}
                  @click=${() => this._requestBulkDeleteSessions()}
                >
                  <ha-icon
                    icon="mdi:delete-outline"
                    style="--mdc-icon-size:14px;"
                  ></ha-icon>
                  Delete
                  (${Object.values(this._selectedSessionIds).filter(Boolean)
                    .length})
                </button>
              </div>
            `
          : html`
              <mwc-button
                class="new-chat-btn"
                outlined
                @click=${this._newSession}
              >
                + New Chat
              </mwc-button>
            `}
        <div class="session-list">
          ${this._sessions.length === 0
            ? html`<div style="padding: 16px; font-size: 12px; opacity: 0.5;">
                No conversations yet.
              </div>`
            : this._sessions.map(
                (s) => html`
                  <div
                    class="session-item ${s.id === this._activeSessionId
                      ? "active"
                      : ""}"
                    @click=${() =>
                      this._selectChatsMode
                        ? this._toggleSessionSelection(s.id)
                        : this._openSession(s.id)}
                  >
                    ${this._selectChatsMode
                      ? html`
                          <input
                            type="checkbox"
                            class="session-checkbox"
                            .checked=${!!this._selectedSessionIds[s.id]}
                            @click=${(e) => {
                              e.stopPropagation();
                              this._toggleSessionSelection(s.id);
                            }}
                          />
                        `
                      : ""}
                    <div style="flex:1; min-width:0;">
                      <div class="session-title">${s.title}</div>
                      <div class="session-meta">
                        ${this._formatDate(s.updated_at)}
                      </div>
                    </div>
                    ${!this._selectChatsMode
                      ? html`
                          <ha-icon
                            class="session-delete"
                            icon="mdi:delete-outline"
                            @click=${(e) => this._deleteSession(s.id, e)}
                            title="Delete"
                          ></ha-icon>
                        `
                      : ""}
                  </div>
                `,
              )}
        </div>
      </div>

      <div class="main">
        <div class="header">
          <div class="header-top">
            <ha-icon-button
              title=${this._showSidebar
                ? "Hide conversations"
                : "Show conversations"}
              @click=${() => (this._showSidebar = !this._showSidebar)}
            >
              <ha-icon
                icon=${this._showSidebar ? "mdi:menu-open" : "mdi:menu"}
              ></ha-icon>
            </ha-icon-button>
            <img
              src="/api/selora_ai/logo.png"
              alt="Selora"
              style="width:28px;height:28px;border-radius:6px;"
            />
            Selora AI
          </div>
          <div class="tabs">
            <div
              class="tab ${this._activeTab === "chat" ? "active" : ""}"
              @click=${() => {
                this._activeTab = "chat";
              }}
            >
              Chat
            </div>
            <div
              class="tab ${this._activeTab === "automations" ? "active" : ""}"
              @click=${() => {
                this._activeTab = "automations";
                this._showSidebar = false;
                this._loadAutomations();
              }}
            >
              Automations
            </div>
            <div
              class="tab ${this._activeTab === "settings" ? "active" : ""}"
              @click=${() => {
                this._activeTab = "settings";
                this._showSidebar = false;
                this._loadConfig();
              }}
            >
              Settings
            </div>
          </div>
        </div>

        ${this._activeTab === "chat" ? this._renderChat() : ""}
        ${this._activeTab === "automations" ? this._renderAutomations() : ""}
        ${this._activeTab === "settings" ? this._renderSettings() : ""}
      </div>

      ${this._renderHardDeleteDialog()}
      ${this._deleteConfirmSessionId
        ? html`
            <div
              class="modal-overlay"
              @click=${(e) => {
                if (e.target === e.currentTarget)
                  this._deleteConfirmSessionId = null;
              }}
            >
              <div
                class="modal-content"
                style="max-width:400px;text-align:center;"
              >
                ${this._deleteConfirmSessionId === "__bulk__"
                  ? html`
                      <div
                        style="font-size:17px;font-weight:600;margin-bottom:8px;"
                      >
                        Delete Conversations
                      </div>
                      <div
                        style="font-size:13px;opacity:0.7;margin-bottom:20px;"
                      >
                        Delete
                        ${Object.values(this._selectedSessionIds).filter(
                          Boolean,
                        ).length}
                        selected conversation(s)? This cannot be undone.
                      </div>
                      <div
                        style="display:flex;gap:10px;justify-content:center;"
                      >
                        <button
                          class="btn btn-outline"
                          @click=${() => {
                            this._deleteConfirmSessionId = null;
                          }}
                        >
                          Cancel
                        </button>
                        <button
                          class="btn"
                          style="background:#ef4444;color:#fff;border-color:#ef4444;"
                          @click=${() => this._confirmBulkDeleteSessions()}
                        >
                          Delete
                        </button>
                      </div>
                    `
                  : html`
                      <div
                        style="font-size:17px;font-weight:600;margin-bottom:8px;"
                      >
                        Delete Conversation
                      </div>
                      <div
                        style="font-size:13px;opacity:0.7;margin-bottom:20px;"
                      >
                        Are you sure you want to delete this conversation? This
                        cannot be undone.
                      </div>
                      <div
                        style="display:flex;gap:10px;justify-content:center;"
                      >
                        <button
                          class="btn btn-outline"
                          @click=${() => {
                            this._deleteConfirmSessionId = null;
                          }}
                        >
                          Cancel
                        </button>
                        <button
                          class="btn"
                          style="background:#ef4444;color:#fff;border-color:#ef4444;"
                          @click=${() => this._confirmDeleteSession()}
                        >
                          Delete
                        </button>
                      </div>
                    `}
              </div>
            </div>
          `
        : ""}
      ${this._toast
        ? html`
            <div class="toast ${this._toastType}">
              <span>${this._toast}</span>
              <ha-icon
                class="toast-close"
                icon="mdi:close"
                @click=${() => this._dismissToast()}
              ></ha-icon>
            </div>
          `
        : ""}
    `;
  }

  // -------------------------------------------------------------------------
  // Chat pane
  // -------------------------------------------------------------------------

  _renderChat() {
    return html`
      <div class="chat-pane">
        <div class="chat-messages" id="chat-messages">
          ${this._messages.length === 0
            ? keyed(
                this._welcomeKey || 0,
                html`
                  <div
                    class="empty-state welcome"
                    style="max-width:520px;margin:0 auto;text-align:center;"
                  >
                    <img
                      src="/api/selora_ai/logo.png"
                      alt="Selora AI"
                      style="width:56px;height:56px;border-radius:12px;margin-bottom:8px;"
                    />
                    <div
                      style="font-size:20px;font-weight:600;margin-bottom:4px;"
                    >
                      Welcome to Selora AI
                    </div>
                    <div
                      style="font-size:13px;opacity:0.7;margin-bottom:20px;line-height:1.5;"
                    >
                      Your intelligent home automation architect. I analyze your
                      devices, detect patterns, and help you build automations
                      using natural language.
                    </div>
                    <div
                      style="display:grid;grid-template-columns:1fr 1fr;gap:10px;text-align:left;margin-bottom:20px;"
                    >
                      <div
                        class="welcome-card"
                        style="display:flex;align-items:flex-start;gap:8px;padding:10px;border-radius:8px;background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.15);cursor:pointer;transition:transform 0.15s,box-shadow 0.15s;"
                        @click=${() =>
                          this._quickStart("Create an automation for my home")}
                      >
                        <ha-icon
                          icon="mdi:lightning-bolt"
                          style="--mdc-icon-size:18px;color:#f59e0b;flex-shrink:0;margin-top:1px;"
                        ></ha-icon>
                        <div>
                          <div style="font-size:12px;font-weight:600;">
                            Create Automations
                          </div>
                          <div style="font-size:11px;opacity:0.6;">
                            Describe what you want in plain English
                          </div>
                        </div>
                      </div>
                      <div
                        class="welcome-card"
                        style="display:flex;align-items:flex-start;gap:8px;padding:10px;border-radius:8px;background:rgba(59,130,246,0.08);border:1px solid rgba(59,130,246,0.15);cursor:pointer;transition:transform 0.15s,box-shadow 0.15s;"
                        @click=${() =>
                          this._quickStart(
                            "Analyze my device usage patterns and suggest automations",
                          )}
                      >
                        <ha-icon
                          icon="mdi:magnify-scan"
                          style="--mdc-icon-size:18px;color:#3b82f6;flex-shrink:0;margin-top:1px;"
                        ></ha-icon>
                        <div>
                          <div style="font-size:12px;font-weight:600;">
                            Detect Patterns
                          </div>
                          <div style="font-size:11px;opacity:0.6;">
                            AI spots your routines and suggests automations
                          </div>
                        </div>
                      </div>
                      <div
                        class="welcome-card"
                        style="display:flex;align-items:flex-start;gap:8px;padding:10px;border-radius:8px;background:rgba(34,197,94,0.08);border:1px solid rgba(34,197,94,0.15);cursor:pointer;transition:transform 0.15s,box-shadow 0.15s;"
                        @click=${() =>
                          this._quickStart(
                            "What devices do I have and how are they organized?",
                          )}
                      >
                        <ha-icon
                          icon="mdi:home-search-outline"
                          style="--mdc-icon-size:18px;color:#22c55e;flex-shrink:0;margin-top:1px;"
                        ></ha-icon>
                        <div>
                          <div style="font-size:12px;font-weight:600;">
                            Manage Devices
                          </div>
                          <div style="font-size:11px;opacity:0.6;">
                            Discover, organize, and control your smart home
                          </div>
                        </div>
                      </div>
                      <div
                        class="welcome-card"
                        style="display:flex;align-items:flex-start;gap:8px;padding:10px;border-radius:8px;background:rgba(168,85,247,0.08);border:1px solid rgba(168,85,247,0.15);cursor:pointer;transition:transform 0.15s,box-shadow 0.15s;"
                        @click=${() =>
                          this._quickStart("What can you help me with?")}
                      >
                        <ha-icon
                          icon="mdi:chat-question-outline"
                          style="--mdc-icon-size:18px;color:#a855f7;flex-shrink:0;margin-top:1px;"
                        ></ha-icon>
                        <div>
                          <div style="font-size:12px;font-weight:600;">
                            Ask Anything
                          </div>
                          <div style="font-size:11px;opacity:0.6;">
                            Get answers about your home setup
                          </div>
                        </div>
                      </div>
                    </div>
                    <div
                      style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:normal;opacity:0.4;margin-bottom:10px;"
                    >
                      Quick start
                    </div>
                    <div
                      style="display:flex;flex-direction:column;gap:8px;width:100%;"
                    >
                      <button
                        class="btn btn-outline"
                        style="width:100%;justify-content:flex-start;gap:8px;padding:10px 14px;font-size:13px;"
                        @click=${() =>
                          this._quickStart(
                            "Create an automation that turns off all lights at midnight",
                          )}
                      >
                        <ha-icon
                          icon="mdi:lightbulb-off-outline"
                          style="--mdc-icon-size:16px;"
                        ></ha-icon>
                        Turn off all lights at midnight
                      </button>
                      <button
                        class="btn btn-outline"
                        style="width:100%;justify-content:flex-start;gap:8px;padding:10px 14px;font-size:13px;"
                        @click=${() =>
                          this._quickStart(
                            "What devices do I have and which ones are currently on?",
                          )}
                      >
                        <ha-icon
                          icon="mdi:devices"
                          style="--mdc-icon-size:16px;"
                        ></ha-icon>
                        What devices do I have?
                      </button>
                      <button
                        class="btn btn-outline"
                        style="width:100%;justify-content:flex-start;gap:8px;padding:10px 14px;font-size:13px;"
                        @click=${() =>
                          this._quickStart(
                            "Suggest useful automations based on my devices and usage patterns",
                          )}
                      >
                        <ha-icon
                          icon="mdi:auto-fix"
                          style="--mdc-icon-size:16px;"
                        ></ha-icon>
                        Suggest automations for my home
                      </button>
                    </div>
                  </div>
                `,
              )
            : this._messages.map((msg, idx) => this._renderMessage(msg, idx))}
          ${this._loading
            ? html`
                <div class="typing-bubble">
                  <div class="typing-dot"></div>
                  <div class="typing-dot"></div>
                  <div class="typing-dot"></div>
                </div>
              `
            : ""}
        </div>

        <div class="chat-input">
          <ha-textfield
            .value=${this._input}
            @input=${(e) => (this._input = e.target.value)}
            @keydown=${(e) =>
              e.key === "Enter" && !e.shiftKey && this._sendMessage()}
            placeholder="Describe an automation or ask a question…"
            ?disabled=${this._loading || this._streaming}
            style="flex:1;"
          ></ha-textfield>
          ${this._streaming
            ? html` <ha-icon-button
                @click=${() => this._stopStreaming()}
                title="Stop generating"
                style="color:#f59e0b;"
              >
                <ha-icon icon="mdi:stop-circle"></ha-icon>
              </ha-icon-button>`
            : html` <ha-icon-button
                @click=${this._sendMessage}
                ?disabled=${this._loading || !this._input.trim()}
                title="Send"
              >
                <ha-icon icon="mdi:send"></ha-icon>
              </ha-icon-button>`}
        </div>
      </div>
    `;
  }

  _renderMessage(msg, idx) {
    const isUser = msg.role === "user";
    // Hide empty streaming messages (typing indicator shown separately)
    if (msg._streaming && !msg.content) return html``;

    // Strip automation JSON blocks from display, show spinner while generating
    let displayContent = msg.content;
    let showAutomationSpinner = false;
    if (!isUser) {
      const { text, isPartialBlock } = stripAutomationBlock(msg.content);
      displayContent = text;
      showAutomationSpinner = isPartialBlock && msg._streaming;
    }

    return html`
      <div class="message-row">
        ${isUser
          ? html`
              <div class="bubble user">
                <span class="msg-content" .innerHTML=${msg.content}></span>
              </div>
            `
          : html`
              <div
                style="display:inline-flex;flex-direction:column;max-width:82%;align-self:flex-start;"
              >
                <div
                  class="bubble assistant"
                  style="max-width:100%;align-self:auto;"
                >
                  <span
                    class="msg-content ${msg._streaming
                      ? "streaming-cursor"
                      : ""}"
                    .innerHTML=${renderMarkdown(displayContent)}
                  ></span>
                  ${showAutomationSpinner
                    ? html`
                        <div
                          style="display:flex;align-items:center;gap:10px;margin-top:12px;padding:12px;border-radius:8px;background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.15);"
                        >
                          <div
                            class="typing-dot"
                            style="animation:blink 1s infinite;width:8px;height:8px;border-radius:50%;background:#f59e0b;"
                          ></div>
                          <span
                            style="font-size:13px;font-weight:500;color:#f59e0b;"
                            >Building automation...</span
                          >
                        </div>
                      `
                    : ""}
                  ${msg.config_issue
                    ? html`
                        <div style="margin-top: 10px;">
                          <mwc-button dense raised @click=${this._goToSettings}
                            >Go to Settings</mwc-button
                          >
                        </div>
                      `
                    : ""}
                  ${msg.automation ? this._renderProposalCard(msg, idx) : ""}
                </div>
                <div
                  class="bubble-meta"
                  style="display:flex;justify-content:space-between;align-items:center;width:100%;"
                >
                  <span>Selora AI · ${this._formatTime(msg.timestamp)}</span>
                  <button
                    class="copy-msg-btn"
                    title="Copy message"
                    @click=${(e) => this._copyMessageText(msg, e.currentTarget)}
                  >
                    <ha-icon
                      icon="mdi:content-copy"
                      style="--mdc-icon-size:12px;"
                    ></ha-icon>
                  </button>
                </div>
              </div>
            `}
        ${isUser
          ? html` <div class="bubble-meta">
              You · ${this._formatTime(msg.timestamp)}
            </div>`
          : ""}
      </div>
    `;
  }

  async _copyMessageText(msg, btn) {
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

  // -------------------------------------------------------------------------
  // YAML editor
  // -------------------------------------------------------------------------

  /**
   * Render an editable YAML textarea with an optional save button.
   * @param {string} key         - unique key for tracking edits (_editedYaml)
   * @param {string} originalYaml - the original YAML string to initialise from
   * @param {Function|null} onSave - called with (key) when "Save changes" is clicked; null = no save button (caller handles via accept)
   */
  _renderYamlEditor(key, originalYaml, onSave = null) {
    this._initYamlEdit(key, originalYaml);
    const current = this._editedYaml[key] ?? originalYaml;
    const isDirty = current !== originalYaml;
    const saving = !!this._savingYaml[key];
    return html`
      <textarea
        class="yaml-editor"
        .value=${current}
        @input=${(e) => {
          this._onYamlInput(key, e.target.value);
          e.target.style.height = "auto";
          e.target.style.height = e.target.scrollHeight + "px";
        }}
        spellcheck="false"
        autocomplete="off"
        rows="${Math.max(8, (current || "").split("\n").length + 1)}"
      ></textarea>
      ${isDirty || onSave
        ? html`
            <div class="yaml-edit-bar">
              ${isDirty
                ? html`
                    <span class="yaml-unsaved">
                      <ha-icon
                        icon="mdi:circle-edit-outline"
                        style="--mdc-icon-size:13px;"
                      ></ha-icon>
                      Unsaved changes
                    </span>
                  `
                : html`<span style="flex:1;"></span>`}
              ${onSave
                ? html`
                    <button
                      class="btn btn-primary"
                      ?disabled=${saving || !isDirty}
                      @click=${() => onSave(key)}
                    >
                      <ha-icon
                        icon="mdi:content-save"
                        style="--mdc-icon-size:13px;"
                      ></ha-icon>
                      ${saving ? "Saving…" : "Save changes"}
                    </button>
                  `
                : ""}
            </div>
          `
        : ""}
    `;
  }

  // -------------------------------------------------------------------------
  // Automation flowchart helpers
  // -------------------------------------------------------------------------

  _humanizeToken(value) {
    if (value == null || value === "") return "";
    return String(value)
      .replace(/_/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }

  _fmtEntity(id) {
    if (!id) return "";
    const eid = String(id);
    const stateObj = this.hass?.states?.[eid];
    if (stateObj?.attributes?.friendly_name)
      return stateObj.attributes.friendly_name;
    const parts = eid.split(".");
    const raw = (
      parts.length > 1 ? parts.slice(1).join(".") : parts[0]
    ).replace(/_/g, " ");
    return raw.replace(/\b\w/g, (c) => c.toUpperCase());
  }

  _fmtEntities(val) {
    if (!val) return "";
    const arr = Array.isArray(val) ? val : [val];
    if (arr.length === 1) return this._fmtEntity(arr[0]);
    if (arr.length === 2)
      return `${this._fmtEntity(arr[0])} and ${this._fmtEntity(arr[1])}`;
    return (
      arr
        .slice(0, -1)
        .map((e) => this._fmtEntity(e))
        .join(", ") +
      ", and " +
      this._fmtEntity(arr[arr.length - 1])
    );
  }

  _fmtState(state) {
    if (state == null) return null;
    const s = String(state);
    const friendly = {
      on: "on",
      off: "off",
      home: "home",
      not_home: "away",
      open: "open",
      closed: "closed",
      locked: "locked",
      unlocked: "unlocked",
      playing: "playing",
      paused: "paused",
      idle: "idle",
      unavailable: "unavailable",
      unknown: "unknown",
    };
    return friendly[s] || s.replace(/_/g, " ");
  }

  _fmtDuration(value) {
    if (!value) return "";
    if (typeof value === "string") return value;
    if (typeof value !== "object") return String(value);
    const parts = [
      value.hours ? `${value.hours}h` : "",
      value.minutes ? `${value.minutes}m` : "",
      value.seconds ? `${value.seconds}s` : "",
    ].filter(Boolean);
    if (parts.length) return parts.join(" ");
    return String(value);
  }

  _fmtWeekdays(value) {
    if (!value) return "";
    const dayMap = {
      mon: "Mon",
      tue: "Tue",
      wed: "Wed",
      thu: "Thu",
      fri: "Fri",
      sat: "Sat",
      sun: "Sun",
    };
    const days = Array.isArray(value) ? value : [value];
    return days
      .map((d) => dayMap[String(d)] || this._humanizeToken(d))
      .join(", ");
  }

  _fmtNumericValue(entityId, value) {
    if (value == null || value === "") return "";
    const raw = String(value).trim();
    const batteryLike = String(entityId || "")
      .toLowerCase()
      .includes("battery");
    if (batteryLike && /^-?\d+(\.\d+)?$/.test(raw) && !raw.includes("%")) {
      return `${raw}%`;
    }
    return raw;
  }

  _fmtTime(val) {
    if (val == null) return String(val);
    const s = String(val).trim();
    // Jinja template — extract entity and show friendly name
    if (s.includes("{{") || s.includes("{%")) {
      const m = s.match(/states\(['"]([^'"]+)['"]\)/);
      if (m) return this._fmtEntity(m[1]);
      const m2 = s.match(/state_attr\(['"]([^'"]+)['"]/);
      if (m2) return this._fmtEntity(m2[1]);
      return "a calculated time";
    }
    // Handle raw seconds (e.g. 43200 => "12:00 PM")
    const num = Number(s);
    if (!isNaN(num) && num >= 0 && num <= 86400 && !s.includes(":")) {
      const h = Math.floor(num / 3600);
      const m = Math.floor((num % 3600) / 60);
      const ampm = h >= 12 ? "PM" : "AM";
      const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
      return `${h12}:${String(m).padStart(2, "0")} ${ampm}`;
    }
    // Handle HH:MM:SS or HH:MM (e.g. "12:00:00" => "12:00 PM")
    const parts = s.split(":");
    if (parts.length >= 2) {
      const h = parseInt(parts[0], 10);
      const m = parseInt(parts[1], 10);
      if (!isNaN(h) && !isNaN(m)) {
        const ampm = h >= 12 ? "PM" : "AM";
        const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
        return `${h12}:${String(m).padStart(2, "0")} ${ampm}`;
      }
    }
    // Entity reference like input_datetime.xxx
    if (s.startsWith("input_datetime.") || s.startsWith("sensor."))
      return this._fmtEntity(s);
    return s;
  }

  _describeFlowItem(item) {
    if (!item || typeof item !== "object") return String(item ?? "");

    // HA supports both 'platform' (classic) and 'trigger' (new format) keys on trigger objects
    const p = item.platform || item.trigger;

    // ── Triggers ──────────────────────────────────────────────────────────────
    if (p === "time") {
      const raw = item.at;
      if (Array.isArray(raw)) {
        return `When the time is ${raw.map((t) => this._fmtTime(t)).join(" or ")}`;
      }
      return `When the time is ${this._fmtTime(raw)}`;
    }
    if (p === "sun") {
      const ev =
        item.event === "sunset"
          ? "sunset"
          : item.event === "sunrise"
            ? "sunrise"
            : this._humanizeToken(item.event || "sun event").toLowerCase();
      const offset = item.offset ? ` (${item.offset})` : "";
      return `When it is ${ev}${offset}`;
    }
    if (p === "state") {
      const eid = this._fmtEntities(item.entity_id);
      const fromState = this._fmtState(item.from);
      const toState = this._fmtState(item.to);
      const duration = this._fmtDuration(item.for);
      const dur = duration ? ` for ${duration}` : "";
      if (toState === "on") return `When ${eid} turns on${dur}`;
      if (toState === "off") return `When ${eid} turns off${dur}`;
      if (toState && fromState)
        return `When ${eid} changes from ${fromState} to ${toState}${dur}`;
      if (toState) return `When ${eid} becomes ${toState}${dur}`;
      return `When ${eid} changes state${dur}`;
    }
    if (p === "numeric_state") {
      const eid = this._fmtEntities(item.entity_id);
      const above = this._fmtNumericValue(item.entity_id, item.above);
      const below = this._fmtNumericValue(item.entity_id, item.below);
      if (item.above != null && item.below != null)
        return `When ${eid} is between ${above} and ${below}`;
      if (item.above != null) return `When ${eid} rises above ${above}`;
      if (item.below != null) return `When ${eid} drops below ${below}`;
      return `When ${eid} value changes`;
    }
    if (p === "homeassistant") {
      const ev =
        item.event === "start"
          ? "starts"
          : item.event === "shutdown"
            ? "shuts down"
            : "changes state";
      return `When Home Assistant ${ev}`;
    }
    if (p === "time_pattern") {
      if (item.seconds != null)
        return `Every ${item.seconds} second${Number(item.seconds) === 1 ? "" : "s"}`;
      if (item.minutes != null)
        return `Every ${item.minutes} minute${Number(item.minutes) === 1 ? "" : "s"}`;
      if (item.hours != null)
        return `Every ${item.hours} hour${Number(item.hours) === 1 ? "" : "s"}`;
      return "On a time pattern";
    }
    if (p === "template") {
      const tmpl = item.value_template || "";
      const entityMatch = tmpl.match(/states\(['"]([^'"]+)['"]\)/);
      if (entityMatch)
        return `When ${this._fmtEntity(entityMatch[1])} condition is met`;
      return "When a template condition is met";
    }
    if (p === "event") {
      const name = item.event_type
        ? this._humanizeToken(item.event_type).toLowerCase()
        : "an event";
      return `When ${name} happens`;
    }
    if (p === "device") {
      const triggerType = item.type
        ? this._humanizeToken(item.type).toLowerCase()
        : "triggered";
      return item.device_id
        ? `When a device ${triggerType}`
        : `When a device is ${triggerType}`;
    }
    if (p === "zone") {
      const eid = this._fmtEntities(item.entity_id);
      const zone = this._fmtEntity(item.zone);
      const eventMap = {
        enter: "enters",
        leave: "leaves",
      };
      const rawEvent = String(item.event || "enter");
      const ev =
        eventMap[rawEvent] || this._humanizeToken(rawEvent).toLowerCase();
      return `${eid} ${ev} ${zone}`.trim();
    }
    if (p === "mqtt")
      return item.topic
        ? `When a device message arrives (${item.topic})`
        : "When a device message arrives";
    if (p === "webhook") return "When an outside service sends an update";
    if (p === "tag")
      return `When a tag is scanned${item.tag_id ? ` (${item.tag_id})` : ""}`;
    if (p === "geo_location") return "When a location update is received";
    if (p === "calendar") {
      const eventName = item.event
        ? this._humanizeToken(item.event).toLowerCase()
        : "event";
      const entity = item.entity_id
        ? ` on ${this._fmtEntity(item.entity_id)}`
        : "";
      return `When a calendar ${eventName} begins${entity}`;
    }
    if (p) return "When this trigger happens";

    // ── Conditions (use 'condition' key) ──────────────────────────────────────
    const cond = item.condition;
    if (cond === "state") {
      const eid = this._fmtEntities(item.entity_id);
      const st = this._fmtState(item.state ?? item.to);
      return `${eid} is ${st}`;
    }
    if (cond === "numeric_state") {
      const eid = this._fmtEntities(item.entity_id);
      if (item.above != null && item.below != null)
        return `${eid} between ${item.above} and ${item.below}`;
      if (item.above != null) return `${eid} above ${item.above}`;
      if (item.below != null) return `${eid} below ${item.below}`;
      return `${eid} numeric check`;
    }
    if (cond === "time") {
      const parts = [];
      if (item.after) parts.push(`after ${this._fmtTime(item.after)}`);
      if (item.before) parts.push(`before ${this._fmtTime(item.before)}`);
      if (item.weekday) {
        parts.push(`on ${this._fmtWeekdays(item.weekday)}`);
      }
      return parts.length ? parts.join(" · ") : "Time window";
    }
    if (cond === "template") return "Template evaluates to true";
    if (cond === "sun") {
      const parts = [];
      if (item.after)
        parts.push(`after ${String(item.after).replace(/_/g, " ")}`);
      if (item.before)
        parts.push(`before ${String(item.before).replace(/_/g, " ")}`);
      return parts.join(", ") || "Sun position";
    }
    if (cond === "and")
      return `All ${(item.conditions || []).length} conditions must be true`;
    if (cond === "or")
      return `Any of ${(item.conditions || []).length} conditions is true`;
    if (cond === "not") return "None of the conditions are true";
    if (cond === "zone") {
      const eid = this._fmtEntities(item.entity_id);
      return `${eid} is in ${this._fmtEntity(item.zone) || "zone"}`;
    }
    if (cond === "device")
      return item.type
        ? String(item.type).replace(/_/g, " ")
        : "Device condition";
    if (cond) return String(cond).replace(/_/g, " ");

    // ── Actions ───────────────────────────────────────────────────────────────
    const svc = item.service || item.action;
    if (svc) {
      const svcStr = String(svc);
      const [domain = "", svcName = svc] = svcStr.split(".");

      // Special handling for notification services
      if (
        svcStr === "notify.persistent_notification" ||
        domain === "persistent_notification"
      ) {
        const title = item.data?.title;
        const msg = item.data?.message;
        if (title && msg) return `Notify: "${title}"`;
        if (title) return `Notify: "${title}"`;
        if (msg) {
          const short = msg.length > 60 ? msg.slice(0, 57) + "…" : msg;
          return `Notify: "${short}"`;
        }
        return "Send a notification";
      }
      if (domain === "notify") {
        const target = svcName
          .replace(/_/g, " ")
          .replace(/\b\w/g, (c) => c.toUpperCase());
        const msg = item.data?.message;
        const title = item.data?.title;
        if (title) return `Notify ${target}: "${title}"`;
        if (msg) {
          const short = msg.length > 50 ? msg.slice(0, 47) + "…" : msg;
          return `Notify ${target}: "${short}"`;
        }
        return `Notify via ${target}`;
      }
      if (domain === "tts") {
        const msg = item.data?.message;
        if (msg) {
          const short = msg.length > 50 ? msg.slice(0, 47) + "…" : msg;
          return `Say: "${short}"`;
        }
        return "Text-to-speech";
      }

      const friendlyActions = {
        turn_on: "Turn on",
        turn_off: "Turn off",
        toggle: "Toggle",
        lock: "Lock",
        unlock: "Unlock",
        open_cover: "Open",
        close_cover: "Close",
        set_temperature: "Set temperature for",
        set_value: "Set value for",
        send_command: "Send command to",
        reload: "Reload",
      };
      const name =
        friendlyActions[svcName] ||
        svcName.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
      const targets = item.target?.entity_id ?? item.data?.entity_id;
      const t = this._fmtEntities(targets);
      const extras = [];
      if (item.data?.brightness_pct != null)
        extras.push(`at ${item.data.brightness_pct}%`);
      if (item.data?.temperature != null)
        extras.push(`to ${item.data.temperature}°`);
      if (item.data?.color_temp != null)
        extras.push(`color temp ${item.data.color_temp}`);
      // Don't show raw messages with Jinja templates — too noisy
      if (item.data?.message && !String(item.data.message).includes("{{")) {
        const short =
          item.data.message.length > 50
            ? item.data.message.slice(0, 47) + "…"
            : item.data.message;
        extras.push(`"${short}"`);
      }
      if (item.data?.title && !String(item.data.title).includes("{{"))
        extras.push(item.data.title);
      const detail = extras.length ? ` (${extras.join(", ")})` : "";
      return t ? `${name} ${t}${detail}` : `${name}${detail}`;
    }
    if (item.delay) {
      const d = item.delay;
      if (typeof d === "string") return `Wait ${d}`;
      const parts = [];
      if (d.hours) parts.push(`${d.hours}h`);
      if (d.minutes) parts.push(`${d.minutes}m`);
      if (d.seconds) parts.push(`${d.seconds}s`);
      return parts.length ? `Wait ${parts.join(" ")}` : "Wait";
    }
    if (item.wait_template) return "Wait until condition is met";
    if (item.wait_for_trigger) return "Wait for a trigger";
    if (item.scene) return `Activate scene: ${this._fmtEntity(item.scene)}`;
    if (item.choose)
      return `Choose between ${item.choose.length} option${item.choose.length !== 1 ? "s" : ""}`;
    if (item.repeat) {
      const r = item.repeat;
      if (r.count != null)
        return `Repeat ${r.count} time${r.count !== 1 ? "s" : ""}`;
      if (r.while) return "Repeat while condition holds";
      if (r.until) return "Repeat until condition is met";
      return "Repeat";
    }
    if (item.parallel)
      return `Run ${(item.parallel || []).length} actions in parallel`;
    if (item.sequence)
      return `Run a sequence of ${(item.sequence || []).length} steps`;
    if (item.variables) return "Set variables";
    if (item.stop) return `Stop: ${item.stop}`;
    if (item.event)
      return `Fire event: ${String(item.event).replace(/_/g, " ")}`;

    // ── Human-readable fallback — never show raw JSON or Jinja ────────────────
    const SKIP = new Set(["id", "enabled", "mode", "alias", "description"]);
    const readable = Object.entries(item)
      .filter(([k, v]) => !SKIP.has(k) && v != null && v !== "")
      .map(([k, v]) => {
        const label = k.replace(/_/g, " ");
        const strVal =
          typeof v === "string"
            ? v
            : Array.isArray(v)
              ? v.map((x) => (typeof x === "object" ? "…" : x)).join(", ")
              : String(v);
        // Hide Jinja templates
        if (strVal.includes("{{") || strVal.includes("{%")) return null;
        return `${label}: ${strVal}`;
      })
      .filter(Boolean)
      .slice(0, 3);
    return readable.length ? readable.join(" · ") : "Automation step";
  }

  _renderAutomationFlowchart(auto) {
    if (!auto) return html``;
    const triggers = (() => {
      const t = auto.triggers ?? auto.trigger ?? [];
      return Array.isArray(t) ? t : [t];
    })();
    const conditions = (() => {
      const c = auto.conditions ?? auto.condition ?? [];
      return Array.isArray(c) ? c : [c];
    })().filter(Boolean);
    const actions = (() => {
      const a = auto.actions ?? auto.action ?? [];
      return Array.isArray(a) ? a : [a];
    })();
    if (!triggers.length && !actions.length) return html``;
    return html`
      <div class="flow-chart">
        <div class="flow-section">
          <div class="flow-label">Trigger</div>
          ${triggers.map(
            (t) =>
              html`<div class="flow-node trigger-node">
                ${this._describeFlowItem(t)}
              </div>`,
          )}
        </div>
        ${conditions.length
          ? html`
              <div class="flow-arrow">↓</div>
              <div class="flow-section">
                <div class="flow-label">Condition</div>
                ${conditions.map(
                  (c) =>
                    html`<div class="flow-node condition-node">
                      ${this._describeFlowItem(c)}
                    </div>`,
                )}
              </div>
            `
          : ""}
        <div class="flow-arrow">↓</div>
        <div class="flow-section">
          <div class="flow-label">Actions</div>
          ${actions.map(
            (a, i) => html`
              ${i > 0 ? html`<div class="flow-arrow-sm">↓</div>` : ""}
              <div class="flow-node action-node">
                ${this._describeFlowItem(a)}
              </div>
            `,
          )}
        </div>
      </div>
    `;
  }

  _renderProposalCard(msg, msgIndex) {
    const status = msg.automation_status;
    const automation = msg.automation;
    const yaml = msg.automation_yaml || "";
    const risk = msg.risk_assessment || automation?.risk_assessment || null;
    const scrutinyTags = risk?.scrutiny_tags || [];

    if (status === "saved") {
      return html`
        <div class="proposal-card" style="margin-top:12px;">
          <div class="proposal-header">
            <ha-icon icon="mdi:check-circle"></ha-icon>
            Automation Created
          </div>
          <div class="proposal-body">
            <div class="proposal-name">${automation.alias}</div>
            <div class="proposal-status saved">
              <ha-icon icon="mdi:check"></ha-icon> Saved and enabled
            </div>
          </div>
        </div>
      `;
    }

    if (status === "declined") {
      return html`
        <div class="proposal-card" style="margin-top:12px; opacity:0.6;">
          <div
            class="proposal-header"
            style="color:var(--secondary-text-color);"
          >
            <ha-icon icon="mdi:close-circle-outline"></ha-icon>
            Automation Declined
          </div>
          <div class="proposal-body">
            <div class="proposal-name">${automation.alias}</div>
            <div class="proposal-status declined">
              Dismissed. You can refine it by replying below.
            </div>
          </div>
        </div>
      `;
    }

    if (status === "refining") {
      return html`
        <div class="proposal-card" style="margin-top:12px; opacity:0.75;">
          <div
            class="proposal-header"
            style="color:var(--warning-color, #ff9800);"
          >
            <ha-icon icon="mdi:pencil-circle-outline"></ha-icon>
            Being Refined
          </div>
          <div class="proposal-body">
            <div class="proposal-name">${automation.alias}</div>
            <div
              class="proposal-status"
              style="background:rgba(255,152,0,0.1); color:var(--warning-color,#ff9800);"
            >
              <ha-icon icon="mdi:arrow-down"></ha-icon>
              Refinement requested — see the updated proposal below.
            </div>
          </div>
        </div>
      `;
    }

    // Pending proposal — full review UI
    const yamlOpen = this._yamlOpen && this._yamlOpen[msgIndex];
    const yamlKey = `proposal_${msgIndex}`;
    const hasEdits =
      this._editedYaml[yamlKey] !== undefined &&
      this._editedYaml[yamlKey] !== yaml;
    return html`
      <div class="proposal-card">
        <div class="proposal-header">
          <ha-icon icon="mdi:lightning-bolt"></ha-icon>
          Automation Proposal
        </div>
        <div class="proposal-body">
          <div class="proposal-name">${automation.alias}</div>

          ${msg.description
            ? html`
                <div class="proposal-description-label">
                  What this automation does
                </div>
                <div class="proposal-description">${msg.description}</div>
              `
            : ""}
          ${risk?.level === "elevated"
            ? html`
                <div
                  class="proposal-status"
                  style="background:rgba(255,152,0,0.12); color:var(--warning-color,#ff9800); border:1px solid rgba(255,152,0,0.25);"
                >
                  <ha-icon icon="mdi:alert-outline"></ha-icon>
                  <div>
                    <strong>Elevated risk review recommended.</strong>
                    <div style="margin-top:4px;">${risk.summary}</div>
                    ${risk.reasons?.length
                      ? html`<div style="margin-top:6px; font-size:12px;">
                          ${risk.reasons.join(" ")}
                        </div>`
                      : ""}
                  </div>
                </div>
              `
            : ""}
          ${this._renderAutomationFlowchart(automation)}

          <div class="yaml-toggle" @click=${() => this._toggleYaml(msgIndex)}>
            <ha-icon
              icon="mdi:code-braces"
              style="--mdc-icon-size:14px;"
            ></ha-icon>
            ${yamlOpen ? "Hide YAML" : "Edit YAML"}
          </div>
          ${yamlOpen ? this._renderYamlEditor(yamlKey, yaml) : ""}

          <div class="proposal-verify">
            ${hasEdits
              ? "Your YAML edits will be used when you accept."
              : "Does the flow above match what you intended?"}
          </div>

          <div class="proposal-actions">
            <button
              class="btn btn-success"
              @click=${() =>
                this._acceptAutomationWithEdits(msgIndex, automation, yamlKey)}
            >
              <ha-icon icon="mdi:check" style="--mdc-icon-size:14px;"></ha-icon>
              Accept &amp; Save
            </button>
            <button
              class="btn btn-outline"
              @click=${() =>
                this._refineAutomation(msgIndex, automation, msg.description)}
            >
              <ha-icon
                icon="mdi:pencil"
                style="--mdc-icon-size:14px;"
              ></ha-icon>
              Refine
            </button>
            <button
              class="btn btn-danger"
              @click=${() => this._declineAutomation(msgIndex)}
            >
              <ha-icon icon="mdi:close" style="--mdc-icon-size:14px;"></ha-icon>
              Decline
            </button>
          </div>
        </div>
      </div>
    `;
  }

  _toggleYaml(msgIndex) {
    this._yamlOpen = {
      ...(this._yamlOpen || {}),
      [msgIndex]: !(this._yamlOpen || {})[msgIndex],
    };
    this.requestUpdate();
  }

  // -------------------------------------------------------------------------
  // Automations tab
  // -------------------------------------------------------------------------

  _toggleExpandAutomation(key) {
    this._expandedAutomations = {
      ...this._expandedAutomations,
      [key]: !this._expandedAutomations[key],
    };
    this.requestUpdate();
  }

  _getSelectedAutomationIds() {
    return Object.keys(this._selectedAutomationIds || {}).filter(
      (id) => this._selectedAutomationIds[id],
    );
  }

  _automationIsEnabled(automation) {
    if (!automation) return false;
    if (automation.state === "on") return true;
    if (automation.state === "off") return false;
    if (
      automation.state === "unavailable" &&
      typeof automation.persisted_enabled === "boolean"
    ) {
      return automation.persisted_enabled;
    }
    return false;
  }

  _toggleAutomationSelection(automationId, evt) {
    evt.stopPropagation();
    if (!automationId) return;
    const checked = !!evt.target.checked;
    this._selectedAutomationIds = {
      ...this._selectedAutomationIds,
      [automationId]: checked,
    };
    this.requestUpdate();
  }

  _toggleSelectAllFiltered(filteredAutomations, checked) {
    const selectable = (filteredAutomations || []).filter(
      (a) => !a._draft && a.automation_id,
    );
    const next = { ...this._selectedAutomationIds };
    for (const auto of selectable) {
      next[auto.automation_id] = checked;
    }
    this._selectedAutomationIds = next;
    this.requestUpdate();
  }

  _clearAutomationSelection() {
    this._selectedAutomationIds = {};
    this.requestUpdate();
  }

  async _bulkToggleSelected(enable) {
    if (this._bulkActionInProgress) return;
    const selectedIds = this._getSelectedAutomationIds();
    if (!selectedIds.length) return;

    const byId = new Map(this._automations.map((a) => [a.automation_id, a]));
    const targets = selectedIds
      .map((id) => byId.get(id))
      .filter((a) => a && !a._draft && a.automation_id)
      .filter((a) =>
        enable ? !this._automationIsEnabled(a) : this._automationIsEnabled(a),
      );
    const skippedCount = selectedIds.length - targets.length;

    if (!targets.length) {
      this._showToast(
        `Selected automations are already ${enable ? "enabled" : "disabled"}.`,
        "info",
      );
      return;
    }

    this._bulkActionInProgress = true;
    this._bulkActionLabel = `${enable ? "Enabling" : "Disabling"} ${targets.length} automation(s)…`;
    let successCount = 0;
    try {
      for (const auto of targets) {
        try {
          await this.hass.callWS({
            type: "selora_ai/toggle_automation",
            automation_id: auto.automation_id,
            entity_id: auto.entity_id,
            enabled: enable,
          });
          successCount += 1;
        } catch (err) {
          console.error("Bulk toggle failed", auto.automation_id, err);
        }
      }
      await this._loadAutomations();
      const failedCount = targets.length - successCount;
      if (failedCount === 0) {
        const skippedNote =
          skippedCount > 0 ? ` (${skippedCount} already in target state)` : "";
        this._showToast(
          `${enable ? "Enabled" : "Disabled"} ${successCount} automation(s)${skippedNote}.`,
          "success",
        );
      } else {
        this._showToast(
          `${enable ? "Enable" : "Disable"} completed: ${successCount} succeeded, ${failedCount} failed.`,
          "error",
        );
      }
    } finally {
      this._bulkActionInProgress = false;
      this._bulkActionLabel = "";
      this.requestUpdate();
    }
  }

  async _bulkSoftDeleteSelected() {
    if (this._bulkActionInProgress) return;
    const selectedIds = this._getSelectedAutomationIds();
    if (!selectedIds.length) return;

    const byId = new Map(this._automations.map((a) => [a.automation_id, a]));
    const targets = selectedIds
      .map((id) => byId.get(id))
      .filter((a) => a && !a._draft && a.automation_id);

    if (!targets.length) return;
    if (!confirm(`Soft-delete ${targets.length} selected automation(s)?`))
      return;

    this._bulkActionInProgress = true;
    this._bulkActionLabel = `Soft-deleting ${targets.length} automation(s)…`;
    let successCount = 0;
    try {
      for (const auto of targets) {
        try {
          await this.hass.callWS({
            type: "selora_ai/soft_delete_automation",
            automation_id: auto.automation_id,
          });
          successCount += 1;
        } catch (err) {
          console.error("Bulk soft-delete failed", auto.automation_id, err);
        }
      }
      this._selectedAutomationIds = {};
      await this._loadAutomations();
      const failedCount = targets.length - successCount;
      if (failedCount === 0) {
        this._showToast(
          `Soft-deleted ${successCount} automation(s).`,
          "success",
        );
      } else {
        this._showToast(
          `Soft-delete completed: ${successCount} succeeded, ${failedCount} failed.`,
          "error",
        );
      }
    } finally {
      this._bulkActionInProgress = false;
      this._bulkActionLabel = "";
      this.requestUpdate();
    }
  }

  async _toggleAutomation(entityId, automationId, enabled) {
    try {
      await this.hass.callWS({
        type: "selora_ai/toggle_automation",
        automation_id: automationId,
        entity_id: entityId,
        enabled: !!enabled,
      });
      await this._loadAutomations();
    } catch (err) {
      console.error("Failed to toggle automation", err);
      const message = err?.message || "unknown error";
      this._showToast(`Failed to toggle automation: ${message}`, "error");
    }
  }

  _toggleBurgerMenu(automationId, evt) {
    evt.stopPropagation();
    this._openBurgerMenu =
      this._openBurgerMenu === automationId ? null : automationId;
    this.requestUpdate();
  }

  _closeBurgerMenus() {
    if (this._openBurgerMenu) {
      this._openBurgerMenu = null;
      this.requestUpdate();
    }
  }

  // -------------------------------------------------------------------------
  // Rename automation
  // -------------------------------------------------------------------------

  _startRenameAutomation(automationId, currentAlias) {
    this._editingAlias = automationId;
    this._editingAliasValue = currentAlias || "";
    this._openBurgerMenu = null;
    this.requestUpdate();
    // Focus the input after render
    this.updateComplete.then(() => {
      const input = this.shadowRoot.querySelector(
        `.rename-input[data-id="${automationId}"]`,
      );
      if (input) {
        input.focus();
        input.select();
      }
    });
  }

  async _saveRenameAutomation(automationId) {
    const newAlias = (this._editingAliasValue || "").trim();
    if (!newAlias) {
      this._editingAlias = null;
      return;
    }
    try {
      await this.hass.callWS({
        type: "selora_ai/rename_automation",
        automation_id: automationId,
        alias: newAlias,
      });
      this._editingAlias = null;
      this._showToast("Automation renamed", "success");
      await this._loadAutomations();
    } catch (err) {
      console.error("Failed to rename automation", err);
      this._showToast("Failed to rename: " + err.message, "error");
    }
  }

  _cancelRenameAutomation() {
    this._editingAlias = null;
    this._editingAliasValue = "";
  }

  // -------------------------------------------------------------------------
  // Version history methods
  // -------------------------------------------------------------------------

  async _openVersionHistory(automationId) {
    const isOpen = !!this._versionHistoryOpen[automationId];
    this._versionHistoryOpen = {
      ...this._versionHistoryOpen,
      [automationId]: !isOpen,
    };
    if (!isOpen && !this._versions[automationId]) {
      await this._loadVersionHistory(automationId);
    }
    this.requestUpdate();
  }

  async _loadVersionHistory(automationId) {
    this._loadingVersions = { ...this._loadingVersions, [automationId]: true };
    try {
      const result = await this.hass.callWS({
        type: "selora_ai/get_automation_versions",
        automation_id: automationId,
      });
      const ordered = Array.isArray(result) ? [...result].reverse() : [];
      this._versions = { ...this._versions, [automationId]: ordered };
    } catch (err) {
      console.error("Failed to load version history", err);
      this._showToast(
        "Failed to load version history: " + err.message,
        "error",
      );
    } finally {
      this._loadingVersions = {
        ...this._loadingVersions,
        [automationId]: false,
      };
    }
    this.requestUpdate();
  }

  async _openDiffViewer(automationId) {
    const versions = this._versions[automationId];
    if (!versions || versions.length < 2)
      await this._loadVersionHistory(automationId);
    const v = this._versions[automationId] || [];
    this._diffAutomationId = automationId;
    this._diffVersionA = v[0]?.version_id || null;
    this._diffVersionB = v[1]?.version_id || null;
    this._diffResult = [];
    this._diffOpen = true;
    if (this._diffVersionA && this._diffVersionB) {
      await this._loadDiff(
        automationId,
        this._diffVersionA,
        this._diffVersionB,
      );
    }
    this.requestUpdate();
  }

  async _loadDiff(automationId, versionAId, versionBId) {
    if (!versionAId || !versionBId) return;
    this._loadingDiff = true;
    this._diffResult = [];
    try {
      const result = await this.hass.callWS({
        type: "selora_ai/get_automation_diff",
        automation_id: automationId,
        version_id_a: versionAId,
        version_id_b: versionBId,
      });
      const diffText = result?.diff || "";
      this._diffResult = diffText ? diffText.split("\n") : [];
    } catch (err) {
      console.error("Failed to load diff", err);
      this._showToast("Failed to load diff: " + err.message, "error");
    } finally {
      this._loadingDiff = false;
    }
    this.requestUpdate();
  }

  async _restoreVersion(automationId, versionId, yamlText) {
    const key = `${automationId}_${versionId}`;
    this._restoringVersion = { ...this._restoringVersion, [key]: true };
    try {
      await this.hass.callWS({
        type: "selora_ai/update_automation_yaml",
        automation_id: automationId,
        yaml_text: yamlText,
        version_message: `Restored from version ${versionId}`,
      });
      this._versionHistoryOpen = {
        ...this._versionHistoryOpen,
        [automationId]: false,
      };
      this._versions = { ...this._versions, [automationId]: null };
      await this._loadAutomations();
      this._showToast("Version restored.", "success");
    } catch (err) {
      console.error("Failed to restore version", err);
      this._showToast("Failed to restore version: " + err.message, "error");
    } finally {
      this._restoringVersion = { ...this._restoringVersion, [key]: false };
    }
    this.requestUpdate();
  }

  // -------------------------------------------------------------------------
  // Soft delete / restore methods
  // -------------------------------------------------------------------------

  async _softDeleteAutomation(automationId) {
    this._deletingAutomation = {
      ...this._deletingAutomation,
      [automationId]: true,
    };
    try {
      await this.hass.callWS({
        type: "selora_ai/soft_delete_automation",
        automation_id: automationId,
      });
      await this._loadAutomations();
      this._showToast("Automation moved to Recently Deleted.", "success");
    } catch (err) {
      console.error("Failed to delete automation", err);
      this._showToast("Failed to delete automation: " + err.message, "error");
    } finally {
      this._deletingAutomation = {
        ...this._deletingAutomation,
        [automationId]: false,
      };
    }
    this.requestUpdate();
  }

  async _restoreDeletedAutomation(automationId) {
    this._restoringAutomation = {
      ...this._restoringAutomation,
      [automationId]: true,
    };
    try {
      await this.hass.callWS({
        type: "selora_ai/restore_automation",
        automation_id: automationId,
      });
      await this._loadDeletedAutomations();
      await this._loadAutomations();
      this._showToast("Automation restored.", "success");
    } catch (err) {
      console.error("Failed to restore automation", err);
      this._showToast("Failed to restore automation: " + err.message, "error");
    } finally {
      this._restoringAutomation = {
        ...this._restoringAutomation,
        [automationId]: false,
      };
    }
    this.requestUpdate();
  }

  _openHardDeleteDialog(automationId, alias) {
    this._hardDeleteTarget = { automationId, alias };
    this._hardDeleteAliasInput = "";
    this.requestUpdate();
  }

  _closeHardDeleteDialog() {
    this._hardDeleteTarget = null;
    this._hardDeleteAliasInput = "";
    this.requestUpdate();
  }

  async _confirmHardDelete() {
    const target = this._hardDeleteTarget;
    if (!target) return;

    const { automationId, alias } = target;
    if (this._hardDeleteAliasInput !== alias) return;

    this._hardDeletingAutomation = {
      ...this._hardDeletingAutomation,
      [automationId]: true,
    };
    try {
      await this.hass.callWS({
        type: "selora_ai/hard_delete_automation",
        automation_id: automationId,
      });
      this._closeHardDeleteDialog();
      await this._loadDeletedAutomations();
      await this._loadAutomations();
      this._showToast("Automation permanently deleted.", "success");
    } catch (err) {
      console.error("Failed to hard delete automation", err);
      this._showToast(
        "Failed to permanently delete automation: " + err.message,
        "error",
      );
    } finally {
      this._hardDeletingAutomation = {
        ...this._hardDeletingAutomation,
        [automationId]: false,
      };
    }
    this.requestUpdate();
  }

  async _toggleDeletedSection() {
    this._showDeleted = !this._showDeleted;
    if (this._showDeleted && this._deletedAutomations.length === 0) {
      await this._loadDeletedAutomations();
    }
    this.requestUpdate();
  }

  async _loadDeletedAutomations() {
    this._loadingDeleted = true;
    try {
      const result = await this.hass.callWS({
        type: "selora_ai/get_automations",
        include_deleted: true,
      });
      this._deletedAutomations = (result || []).filter((a) => a.is_deleted);
    } catch (err) {
      console.error("Failed to load deleted automations", err);
      this._showToast(
        "Failed to load deleted automations: " + err.message,
        "error",
      );
    } finally {
      this._loadingDeleted = false;
    }
    this.requestUpdate();
  }

  // -------------------------------------------------------------------------
  // Refine in chat
  // -------------------------------------------------------------------------

  async _loadAutomationToChat(automationId) {
    if (!automationId) {
      this._showToast(
        "This automation cannot be refined because it has no automation ID.",
        "error",
      );
      return;
    }
    this._loadingToChat = { ...this._loadingToChat, [automationId]: true };
    try {
      const result = await this.hass.callWS({
        type: "selora_ai/load_automation_to_session",
        automation_id: automationId,
      });
      const sessionId = result?.session_id;
      if (sessionId) {
        this._activeSessionId = sessionId;
        this._activeTab = "chat";
        this._showSidebar = false;
        await this._openSession(sessionId);
        this._showToast("Automation loaded into chat.", "success");
      }
    } catch (err) {
      console.error("Failed to load automation to chat", err);
      this._showToast(
        "Failed to load automation into chat: " + err.message,
        "error",
      );
    } finally {
      this._loadingToChat = { ...this._loadingToChat, [automationId]: false };
    }
    this.requestUpdate();
  }

  // -------------------------------------------------------------------------
  // Render: version history drawer
  // -------------------------------------------------------------------------

  _renderVersionHistoryDrawer(a) {
    const automationId = a.automation_id || a.entity_id;
    const versions = this._versions[automationId] || [];
    const loading = this._loadingVersions[automationId];

    return html`
      <div
        style="border:1px solid var(--divider-color);border-radius:8px;margin:8px 0 4px;padding:12px;background:var(--secondary-background-color);"
      >
        ${loading
          ? html`<div style="opacity:0.5;font-size:12px;">Loading…</div>`
          : versions.length === 0
            ? html`<div style="opacity:0.5;font-size:12px;">
                No version history yet.
              </div>`
            : html`
                <div style="position:relative;padding-left:20px;">
                  <div
                    style="position:absolute;left:7px;top:0;bottom:0;width:2px;background:var(--divider-color);border-radius:2px;"
                  ></div>
                  ${versions.map((v, i) => {
                    const key = `${automationId}_${v.version_id}`;
                    const restoring = this._restoringVersion[key];
                    const date = new Date(v.created_at);
                    const relativeTime = this._relativeTime(date);
                    const isCurrent = i === 0;
                    return html`
                      <div
                        style="position:relative;margin-bottom:${i <
                        versions.length - 1
                          ? "14px"
                          : "0"};padding-left:14px;"
                      >
                        <div
                          style="position:absolute;left:-6px;top:3px;width:10px;height:10px;border-radius:50%;background:${isCurrent
                            ? "#f59e0b"
                            : "var(--divider-color)"};border:2px solid var(--secondary-background-color);"
                        ></div>
                        <div
                          style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;"
                        >
                          <span style="font-size:12px;font-weight:600;"
                            >v${versions.length - i}</span
                          >
                          <span
                            style="font-size:11px;opacity:0.6;"
                            title=${date.toISOString()}
                            >${relativeTime}</span
                          >
                          ${isCurrent
                            ? html`<span
                                style="font-size:10px;background:#f59e0b;color:#fff;border-radius:4px;padding:1px 6px;font-weight:600;"
                                >current</span
                              >`
                            : ""}
                        </div>
                        ${v.message || v.version_message
                          ? html`<div
                              style="font-size:11px;opacity:0.6;margin-top:2px;"
                            >
                              ${v.message || v.version_message}
                            </div>`
                          : ""}
                        <div style="display:flex;gap:6px;margin-top:6px;">
                          <button
                            class="btn btn-outline"
                            style="font-size:10px;padding:2px 7px;"
                            @click=${() =>
                              this._toggleExpandAutomation(`ver_${key}`)}
                          >
                            <ha-icon
                              icon="mdi:code-braces"
                              style="--mdc-icon-size:11px;"
                            ></ha-icon>
                            ${this._expandedAutomations[`ver_${key}`]
                              ? "Hide"
                              : "YAML"}
                          </button>
                          ${!isCurrent
                            ? html`
                                <button
                                  class="btn btn-outline"
                                  style="font-size:10px;padding:2px 7px;"
                                  ?disabled=${restoring ||
                                  !(v.yaml || v.yaml_content)}
                                  @click=${() =>
                                    this._restoreVersion(
                                      automationId,
                                      v.version_id,
                                      v.yaml || v.yaml_content || "",
                                    )}
                                >
                                  <ha-icon
                                    icon="mdi:restore"
                                    style="--mdc-icon-size:11px;"
                                  ></ha-icon>
                                  ${restoring ? "Restoring…" : "Restore"}
                                </button>
                              `
                            : ""}
                        </div>
                        ${this._expandedAutomations[`ver_${key}`]
                          ? html`<pre
                              style="font-size:11px;background:#1a1a2e;color:#e0e0e0;padding:8px;border-radius:6px;overflow-x:auto;margin:6px 0 0;white-space:pre-wrap;"
                            >
${v.yaml || v.yaml_content || "(no YAML stored)"}</pre
                            >`
                          : ""}
                      </div>
                    `;
                  })}
                </div>
              `}
      </div>
    `;
  }

  // -------------------------------------------------------------------------
  // Render: diff viewer modal
  // -------------------------------------------------------------------------

  _renderDiffViewer() {
    if (!this._diffOpen) return "";
    const automationId = this._diffAutomationId;
    const versions = this._versions[automationId] || [];
    return html`
      <div
        style="position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center;"
        @click=${(e) => {
          if (e.target === e.currentTarget) {
            this._diffOpen = false;
            this.requestUpdate();
          }
        }}
      >
        <div
          style="background:var(--card-background-color);border-radius:12px;width:90%;max-width:760px;max-height:85vh;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 8px 32px rgba(0,0,0,0.4);"
        >
          <div
            style="display:flex;align-items:center;justify-content:space-between;padding:16px 20px;border-bottom:1px solid var(--divider-color);"
          >
            <span style="font-weight:700;font-size:15px;">
              <ha-icon
                icon="mdi:compare"
                style="--mdc-icon-size:17px;vertical-align:middle;margin-right:6px;"
              ></ha-icon>
              Compare Versions
            </span>
            <ha-icon
              icon="mdi:close"
              style="cursor:pointer;--mdc-icon-size:20px;"
              @click=${() => {
                this._diffOpen = false;
                this.requestUpdate();
              }}
            ></ha-icon>
          </div>
          <div
            style="padding:12px 20px;border-bottom:1px solid var(--divider-color);display:flex;gap:12px;align-items:center;flex-wrap:wrap;"
          >
            <div style="display:flex;align-items:center;gap:8px;">
              <span style="font-size:12px;opacity:0.7;"
                >Version A (newer):</span
              >
              <select
                style="font-size:12px;padding:4px 8px;border-radius:6px;background:var(--input-fill-color);border:1px solid var(--divider-color);color:var(--primary-text-color);"
                .value=${this._diffVersionA || ""}
                @change=${async (e) => {
                  this._diffVersionA = e.target.value;
                  await this._loadDiff(
                    automationId,
                    this._diffVersionA,
                    this._diffVersionB,
                  );
                }}
              >
                ${versions.map(
                  (v, i) =>
                    html`<option value=${v.version_id}>
                      v${versions.length - i} —
                      ${v.message ||
                      v.version_message ||
                      new Date(v.created_at).toLocaleDateString()}
                    </option>`,
                )}
              </select>
            </div>
            <div style="display:flex;align-items:center;gap:8px;">
              <span style="font-size:12px;opacity:0.7;"
                >Version B (older):</span
              >
              <select
                style="font-size:12px;padding:4px 8px;border-radius:6px;background:var(--input-fill-color);border:1px solid var(--divider-color);color:var(--primary-text-color);"
                .value=${this._diffVersionB || ""}
                @change=${async (e) => {
                  this._diffVersionB = e.target.value;
                  await this._loadDiff(
                    automationId,
                    this._diffVersionA,
                    this._diffVersionB,
                  );
                }}
              >
                ${versions.map(
                  (v, i) =>
                    html`<option value=${v.version_id}>
                      v${versions.length - i} —
                      ${v.message ||
                      v.version_message ||
                      new Date(v.created_at).toLocaleDateString()}
                    </option>`,
                )}
              </select>
            </div>
          </div>
          <div style="flex:1;overflow-y:auto;padding:12px 20px;">
            ${this._loadingDiff
              ? html`<div style="opacity:0.5;text-align:center;padding:24px;">
                  Loading diff…
                </div>`
              : this._diffResult.length === 0
                ? html`<div style="opacity:0.5;text-align:center;padding:24px;">
                    No differences found.
                  </div>`
                : html`<pre
                    style="font-size:12px;margin:0;font-family:monospace;white-space:pre-wrap;"
                  >
${this._diffResult.map((line) => {
                      const bg = line.startsWith("+")
                        ? "rgba(40,167,69,0.15)"
                        : line.startsWith("-")
                          ? "rgba(220,53,69,0.15)"
                          : "transparent";
                      const color = line.startsWith("+")
                        ? "#40c057"
                        : line.startsWith("-")
                          ? "#fa5252"
                          : "var(--primary-text-color)";
                      return html`<span
                        style="display:block;background:${bg};color:${color};padding:1px 4px;"
                        >${line}</span
                      >`;
                    })}</pre
                  >`}
          </div>
        </div>
      </div>
    `;
  }

  // -------------------------------------------------------------------------
  // Render: recently deleted section
  // -------------------------------------------------------------------------

  _renderDeletedSection() {
    const daysRemaining = (deletedAt) => {
      const elapsed =
        (Date.now() - new Date(deletedAt).getTime()) / (1000 * 60 * 60 * 24);
      return Math.max(0, Math.round(30 - elapsed));
    };
    return html`
      <div style="margin-top:16px;">
        <div
          class="expand-toggle"
          style="display:flex;align-items:center;gap:6px;"
          @click=${() => this._toggleDeletedSection()}
        >
          <ha-icon
            icon="mdi:trash-can-outline"
            style="--mdc-icon-size:14px;opacity:0.6;"
          ></ha-icon>
          <span>Recently Deleted</span>
          <ha-icon
            icon="mdi:chevron-${this._showDeleted ? "up" : "down"}"
            style="--mdc-icon-size:14px;margin-left:auto;"
          ></ha-icon>
        </div>
        ${this._showDeleted
          ? html`
              <div style="margin-top:8px;">
                ${this._loadingDeleted
                  ? html`<div style="opacity:0.5;font-size:12px;padding:8px 0;">
                      Loading…
                    </div>`
                  : this._deletedAutomations.length === 0
                    ? html`<div
                        style="opacity:0.45;font-size:12px;padding:8px 0;"
                      >
                        No recently deleted automations.
                      </div>`
                    : this._deletedAutomations.map((a) => {
                        const automationId = a.automation_id || a.entity_id;
                        const days = daysRemaining(a.deleted_at);
                        const restoring =
                          this._restoringAutomation[automationId];
                        const hardDeleting =
                          this._hardDeletingAutomation[automationId];
                        return html`
                          <div
                            class="card"
                            style="opacity:0.8;border-left:3px solid var(--error-color);"
                          >
                            <div class="card-header">
                              <h3 style="flex:1;">${a.alias}</h3>
                              ${days <= 3
                                ? html`<span
                                    style="font-size:10px;background:var(--error-color);color:#fff;border-radius:4px;padding:2px 6px;"
                                    >⚠ ${days}d left</span
                                  >`
                                : html`<span style="font-size:11px;opacity:0.6;"
                                    >${days} days until purge</span
                                  >`}
                            </div>
                            <p style="font-size:11px;opacity:0.6;margin:4px 0;">
                              Deleted
                              ${this._relativeTime(new Date(a.deleted_at))}
                            </p>
                            <div class="card-actions">
                              <button
                                class="btn btn-outline"
                                ?disabled=${restoring || hardDeleting}
                                @click=${() =>
                                  this._restoreDeletedAutomation(automationId)}
                              >
                                <ha-icon
                                  icon="mdi:restore"
                                  style="--mdc-icon-size:13px;"
                                ></ha-icon>
                                ${restoring ? "Restoring…" : "Restore"}
                              </button>
                              <button
                                class="btn btn-outline btn-danger"
                                ?disabled=${restoring || hardDeleting}
                                @click=${() =>
                                  this._openHardDeleteDialog(
                                    automationId,
                                    a.alias,
                                  )}
                              >
                                <ha-icon
                                  icon="mdi:trash-can"
                                  style="--mdc-icon-size:13px;"
                                ></ha-icon>
                                ${hardDeleting
                                  ? "Deleting…"
                                  : "Permanently Delete"}
                              </button>
                            </div>
                          </div>
                        `;
                      })}
              </div>
            `
          : ""}
      </div>
    `;
  }

  _renderHardDeleteDialog() {
    if (!this._hardDeleteTarget) return "";

    const { automationId, alias } = this._hardDeleteTarget;
    const hardDeleting = !!this._hardDeletingAutomation[automationId];
    const canConfirm = this._hardDeleteAliasInput === alias;

    return html`
      <div
        style="position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:10000;display:flex;align-items:center;justify-content:center;"
        @click=${(e) => {
          if (e.target === e.currentTarget && !hardDeleting) {
            this._closeHardDeleteDialog();
          }
        }}
      >
        <div
          style="background:var(--card-background-color);border-radius:12px;width:90%;max-width:520px;padding:18px;box-shadow:0 8px 32px rgba(0,0,0,0.4);border:1px solid var(--divider-color);"
        >
          <div
            style="font-size:16px;font-weight:700;margin-bottom:8px;display:flex;align-items:center;gap:8px;color:var(--error-color);"
          >
            <ha-icon icon="mdi:alert-octagon"></ha-icon>
            Permanently Delete Automation
          </div>
          <p
            style="font-size:13px;opacity:0.85;margin:0 0 10px;line-height:1.45;"
          >
            This action cannot be undone. Type the automation alias to confirm
            permanent deletion.
          </p>
          <p style="font-size:12px;opacity:0.75;margin:0 0 8px;">
            Alias: <strong>${alias}</strong>
          </p>
          <ha-textfield
            .value=${this._hardDeleteAliasInput}
            @input=${(e) => (this._hardDeleteAliasInput = e.target.value)}
            placeholder="Type alias exactly"
            ?disabled=${hardDeleting}
            style="width:100%;"
          ></ha-textfield>
          <div
            style="display:flex;justify-content:flex-end;gap:10px;margin-top:14px;"
          >
            <button
              class="btn btn-outline"
              ?disabled=${hardDeleting}
              @click=${() => this._closeHardDeleteDialog()}
            >
              Cancel
            </button>
            <button
              class="btn btn-danger"
              ?disabled=${hardDeleting || !canConfirm}
              @click=${() => this._confirmHardDelete()}
            >
              ${hardDeleting ? "Deleting…" : "Permanently Delete"}
            </button>
          </div>
        </div>
      </div>
    `;
  }

  // -------------------------------------------------------------------------
  // Helper: relative time
  // -------------------------------------------------------------------------

  _relativeTime(date) {
    const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
    if (seconds < 60) return "just now";
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
    return date.toLocaleDateString();
  }

  /** Distribute card templates into masonry columns (round-robin, left-to-right order).
   *  Optional firstColFooter appends extra content at the bottom of the first column. */
  _masonryColumns(cards, cols = 3, firstColFooter = null) {
    const w = window.innerWidth;
    const numCols = w <= 600 ? 1 : w <= 1000 ? 2 : cols;
    const buckets = Array.from({ length: numCols }, () => []);
    cards.forEach((c, i) => buckets[i % numCols].push(c));
    return buckets.map(
      (col, i) =>
        html`<div class="masonry-col">
          ${col}${i === 0 && firstColFooter ? firstColFooter : ""}
        </div>`,
    );
  }

  _renderAutomations() {
    const filterText = (this._automationFilter || "").toLowerCase();
    const filteredAutomations = filterText
      ? this._automations.filter((a) =>
          (a.alias || "").toLowerCase().includes(filterText),
        )
      : this._automations;
    const perPage = this._autosPerPage || 10;
    const totalAutoPages = Math.max(
      1,
      Math.ceil(filteredAutomations.length / perPage),
    );
    const safeAutoPage = Math.min(this._automationsPage, totalAutoPages);
    const pagedAutomations = filteredAutomations.slice(
      (safeAutoPage - 1) * perPage,
      safeAutoPage * perPage,
    );
    const selectableAutomations = filteredAutomations.filter(
      (a) => !a._draft && a.automation_id,
    );
    const selectableIds = selectableAutomations.map((a) => a.automation_id);
    const selectedIds = this._getSelectedAutomationIds();
    const selectedVisibleCount = selectableIds.filter(
      (id) => this._selectedAutomationIds[id],
    ).length;
    const allVisibleSelected =
      selectableIds.length > 0 && selectedVisibleCount === selectableIds.length;
    const partiallyVisibleSelected =
      selectedVisibleCount > 0 && !allVisibleSelected;
    const hiddenSelectedCount = Math.max(
      0,
      selectedIds.length - selectedVisibleCount,
    );
    const bulkDisabled = selectedIds.length === 0 || this._bulkActionInProgress;

    return html`
      <div class="scroll-view" @click=${() => this._closeBurgerMenus()}>
        <div class="sub-tabs">
          <button
            class="sub-tab ${this._automationsSubTab === "my_automations"
              ? "active"
              : ""}"
            @click=${() => {
              this._automationsSubTab = "my_automations";
            }}
          >
            My Automations
          </button>
          <button
            class="sub-tab ${this._automationsSubTab === "suggestions"
              ? "active"
              : ""}"
            @click=${() => {
              this._automationsSubTab = "suggestions";
            }}
          >
            Suggestions
            ${(() => {
              const qualCount =
                (this._proactiveSuggestions || []).filter(
                  (s) => (s.confidence || 0) >= 0.8,
                ).length + (this._suggestions || []).length;
              return qualCount > 0
                ? html`<span class="badge">${qualCount}</span>`
                : "";
            })()}
          </button>
        </div>
        ${this._automationsSubTab === "my_automations"
          ? html`
              ${this._automations.length > 0
                ? html`
                    <div class="filter-row">
                      <div
                        style="display:flex;align-items:center;gap:8px;justify-content:center;"
                      >
                        ${this._bulkEditMode
                          ? html`
                              <label class="bulk-select-all">
                                <input
                                  type="checkbox"
                                  ?checked=${allVisibleSelected}
                                  .indeterminate=${partiallyVisibleSelected}
                                  ?disabled=${selectableIds.length === 0 ||
                                  this._bulkActionInProgress}
                                  @change=${(e) =>
                                    this._toggleSelectAllFiltered(
                                      filteredAutomations,
                                      e.target.checked,
                                    )}
                                />
                                <span>Select all</span>
                              </label>
                            `
                          : ""}
                        <div class="filter-input-wrap">
                          <ha-icon icon="mdi:magnify"></ha-icon>
                          <input
                            type="text"
                            placeholder="Filter automations…"
                            .value=${this._automationFilter}
                            @input=${(e) => {
                              this._automationFilter = e.target.value;
                              this._automationsPage = 1;
                            }}
                          />
                        </div>
                        <button
                          class="btn btn-primary"
                          style="white-space:nowrap;"
                          @click=${() => {
                            this._newAutoName = "";
                            this._showNewAutoDialog = true;
                          }}
                        >
                          <ha-icon
                            icon="mdi:plus"
                            style="--mdc-icon-size:14px;"
                          ></ha-icon>
                          New Automation
                        </button>
                        ${this._bulkEditMode
                          ? html`
                              <button
                                class="btn btn-outline"
                                style="white-space:nowrap;"
                                @click=${() => {
                                  this._bulkEditMode = false;
                                  this._clearAutomationSelection();
                                }}
                              >
                                Done
                              </button>
                            `
                          : html`
                              <button
                                class="btn btn-outline"
                                style="white-space:nowrap;"
                                @click=${() => {
                                  this._bulkEditMode = true;
                                }}
                              >
                                <ha-icon
                                  icon="mdi:checkbox-multiple-outline"
                                  style="--mdc-icon-size:14px;"
                                ></ha-icon>
                                Bulk edit
                              </button>
                            `}
                        <label class="per-page-label"
                          >Show Per Page:
                          <select
                            class="per-page-select"
                            .value=${String(this._autosPerPage)}
                            @change=${(e) => {
                              this._autosPerPage = Number(e.target.value);
                              this._automationsPage = 1;
                            }}
                          >
                            <option value="10">10</option>
                            <option value="20">20</option>
                            <option value="50">50</option>
                          </select>
                        </label>
                      </div>
                    </div>
                    ${this._bulkEditMode && selectedIds.length > 0
                      ? html`
                          <div class="bulk-actions-row">
                            <div class="left">
                              ${selectedIds.length}
                              selected${hiddenSelectedCount > 0
                                ? html` <span
                                    style="opacity:0.65;font-weight:500;"
                                    >(${hiddenSelectedCount} hidden by
                                    filter)</span
                                  >`
                                : ""}
                              ${this._bulkActionInProgress
                                ? html`<span
                                    style="opacity:0.75;font-weight:500;"
                                  >
                                    · ${this._bulkActionLabel}</span
                                  >`
                                : ""}
                            </div>
                            <div class="actions">
                              <button
                                class="btn btn-outline"
                                ?disabled=${bulkDisabled}
                                @click=${() => this._bulkToggleSelected(true)}
                              >
                                ${this._bulkActionInProgress
                                  ? "Working…"
                                  : "Enable all"}
                              </button>
                              <button
                                class="btn btn-outline"
                                ?disabled=${bulkDisabled}
                                @click=${() => this._bulkToggleSelected(false)}
                              >
                                ${this._bulkActionInProgress
                                  ? "Working…"
                                  : "Disable all"}
                              </button>
                              <button
                                class="btn btn-outline btn-danger"
                                ?disabled=${bulkDisabled}
                                @click=${() => this._bulkSoftDeleteSelected()}
                              >
                                ${this._bulkActionInProgress
                                  ? "Working…"
                                  : "Soft-delete selected"}
                              </button>
                              <button
                                class="btn btn-ghost"
                                ?disabled=${this._bulkActionInProgress}
                                @click=${() => this._clearAutomationSelection()}
                              >
                                Clear
                              </button>
                            </div>
                          </div>
                        `
                      : ""}
                    <div class="automations-grid">
                      ${this._masonryColumns(
                        pagedAutomations.map((a) => {
                          const isDraft = !!a._draft;
                          const expanded =
                            !!this._expandedAutomations[a.entity_id];
                          const isOn = this._automationIsEnabled(a);
                          const automationId = a.automation_id || "";
                          const hasAutomationId = !!automationId;
                          const canToggle =
                            hasAutomationId && !this._bulkActionInProgress;
                          const versionCount = a.version_count || null;
                          const deleting =
                            this._deletingAutomation[automationId];
                          const loadingChat = this._loadingToChat[automationId];
                          const burgerOpen =
                            this._openBurgerMenu === automationId;
                          return html`
                            <div
                              class="card"
                              style="padding:12px 14px;${isDraft ||
                              a.state === "on"
                                ? "border-color:#f59e0b;box-shadow:0 0 0 1px #f59e0b;"
                                : ""}"
                            >
                              <div
                                class="card-header"
                                style="margin-bottom:6px;"
                              >
                                ${this._bulkEditMode && hasAutomationId
                                  ? html`
                                      <label class="card-select">
                                        <input
                                          type="checkbox"
                                          .checked=${!!this
                                            ._selectedAutomationIds[
                                            automationId
                                          ]}
                                          ?disabled=${this
                                            ._bulkActionInProgress}
                                          @click=${(e) => e.stopPropagation()}
                                          @change=${(e) =>
                                            this._toggleAutomationSelection(
                                              automationId,
                                              e,
                                            )}
                                        />
                                      </label>
                                    `
                                  : ""}
                                ${this._editingAlias === automationId
                                  ? html`
                                      <input
                                        class="rename-input"
                                        data-id="${automationId}"
                                        .value=${this._editingAliasValue}
                                        @input=${(e) => {
                                          this._editingAliasValue =
                                            e.target.value;
                                        }}
                                        @keydown=${(e) => {
                                          if (e.key === "Enter")
                                            this._saveRenameAutomation(
                                              automationId,
                                            );
                                          if (e.key === "Escape")
                                            this._cancelRenameAutomation();
                                        }}
                                      />
                                      <button
                                        class="rename-save-btn"
                                        title="Save"
                                        @click=${() =>
                                          this._saveRenameAutomation(
                                            automationId,
                                          )}
                                      >
                                        <ha-icon
                                          icon="mdi:check"
                                          style="--mdc-icon-size:16px;"
                                        ></ha-icon>
                                      </button>
                                    `
                                  : html`
                                      <h3
                                        style="flex:1;font-size:14px;margin:0;"
                                      >
                                        ${a.alias}
                                      </h3>
                                    `}
                                ${hasAutomationId
                                  ? html`
                                      <div
                                        class="burger-menu-wrapper"
                                        style="margin-left:6px;"
                                      >
                                        <button
                                          class="burger-btn"
                                          @click=${(e) =>
                                            this._toggleBurgerMenu(
                                              automationId,
                                              e,
                                            )}
                                          ?disabled=${this
                                            ._bulkActionInProgress}
                                          title="More actions"
                                        >
                                          <ha-icon
                                            icon="mdi:dots-vertical"
                                            style="--mdc-icon-size:16px;"
                                          ></ha-icon>
                                        </button>
                                        ${burgerOpen
                                          ? html`
                                              <div class="burger-dropdown">
                                                <button
                                                  class="burger-item"
                                                  @click=${(e) => {
                                                    e.stopPropagation();
                                                    this._startRenameAutomation(
                                                      automationId,
                                                      a.alias,
                                                    );
                                                  }}
                                                >
                                                  <ha-icon
                                                    icon="mdi:pencil-outline"
                                                    style="--mdc-icon-size:14px;"
                                                  ></ha-icon>
                                                  Rename
                                                </button>
                                                <button
                                                  class="burger-item danger"
                                                  ?disabled=${deleting}
                                                  @click=${(e) => {
                                                    e.stopPropagation();
                                                    this._openBurgerMenu = null;
                                                    this._softDeleteAutomation(
                                                      automationId,
                                                    );
                                                  }}
                                                >
                                                  <ha-icon
                                                    icon="mdi:trash-can-outline"
                                                    style="--mdc-icon-size:14px;"
                                                  ></ha-icon>
                                                  ${deleting
                                                    ? "Deleting…"
                                                    : "Delete"}
                                                </button>
                                              </div>
                                            `
                                          : ""}
                                      </div>
                                    `
                                  : ""}
                              </div>

                              <div
                                style="display:flex;align-items:center;gap:6px;margin-bottom:2px;flex-wrap:wrap;"
                              >
                                <label
                                  class="toggle-switch"
                                  title="${canToggle
                                    ? isOn
                                      ? "Enabled"
                                      : "Disabled"
                                    : "Unavailable — automation id not resolved"}"
                                  style="${canToggle
                                    ? ""
                                    : "opacity:0.45;cursor:not-allowed;"}"
                                  @click=${() => {
                                    if (!canToggle) {
                                      this._showToast(
                                        "Unable to toggle: automation id was not resolved. Reload and try again.",
                                        "error",
                                      );
                                    }
                                  }}
                                >
                                  <input
                                    type="checkbox"
                                    .checked=${isOn}
                                    ?disabled=${!canToggle}
                                    @click=${(e) => e.stopPropagation()}
                                    @change=${(e) => {
                                      if (!canToggle) return;
                                      this._toggleAutomation(
                                        a.entity_id,
                                        automationId,
                                        e.target.checked,
                                      );
                                    }}
                                  />
                                  <div class="toggle-track ${isOn ? "on" : ""}">
                                    <div class="toggle-thumb"></div>
                                  </div>
                                </label>
                                ${isDraft
                                  ? html` <button
                                        class="btn btn-primary"
                                        style="font-size:11px;padding:3px 8px;"
                                        @click=${() => {
                                          this._activeSessionId =
                                            a._linked_session;
                                          this._activeTab = "chat";
                                          this._openSession(a._linked_session);
                                        }}
                                      >
                                        <ha-icon
                                          icon="mdi:chat-processing-outline"
                                          style="--mdc-icon-size:13px;"
                                        ></ha-icon>
                                        Define in Chat
                                      </button>
                                      <button
                                        class="btn btn-outline"
                                        style="font-size:11px;padding:3px 8px;margin-left:auto;"
                                        @click=${() =>
                                          this._dismissDraft(a._draft_id)}
                                      >
                                        <ha-icon
                                          icon="mdi:close"
                                          style="--mdc-icon-size:13px;"
                                        ></ha-icon>
                                        Dismiss
                                      </button>`
                                  : html` <button
                                      class="btn btn-outline"
                                      style="font-size:11px;padding:3px 8px;"
                                      ?disabled=${!hasAutomationId ||
                                      loadingChat ||
                                      this._bulkActionInProgress}
                                      @click=${() =>
                                        this._loadAutomationToChat(
                                          automationId,
                                        )}
                                    >
                                      <ha-icon
                                        icon="mdi:chat-processing-outline"
                                        style="--mdc-icon-size:13px;"
                                      ></ha-icon>
                                      ${loadingChat
                                        ? "Loading…"
                                        : "Refine in chat"}
                                    </button>`}
                              </div>

                              <div class="card-tabs">
                                <span class="label">View:</span>
                                ${a.trigger?.length || a.action?.length
                                  ? html`
                                      <button
                                        class="card-tab ${this._cardActiveTab[
                                          a.entity_id
                                        ] === "flow"
                                          ? "active"
                                          : ""}"
                                        @click=${() => {
                                          this._cardActiveTab = {
                                            ...this._cardActiveTab,
                                            [a.entity_id]:
                                              this._cardActiveTab[
                                                a.entity_id
                                              ] === "flow"
                                                ? null
                                                : "flow",
                                          };
                                        }}
                                      >
                                        <ha-icon
                                          icon="mdi:sitemap-outline"
                                          style="--mdc-icon-size:14px;"
                                        ></ha-icon>
                                        Flow
                                      </button>
                                      <span class="card-tab-sep">|</span>
                                    `
                                  : ""}
                                ${a.yaml_text
                                  ? html`
                                      <button
                                        class="card-tab ${this._cardActiveTab[
                                          a.entity_id
                                        ] === "yaml"
                                          ? "active"
                                          : ""}"
                                        @click=${() => {
                                          this._cardActiveTab = {
                                            ...this._cardActiveTab,
                                            [a.entity_id]:
                                              this._cardActiveTab[
                                                a.entity_id
                                              ] === "yaml"
                                                ? null
                                                : "yaml",
                                          };
                                        }}
                                      >
                                        <ha-icon
                                          icon="mdi:code-braces"
                                          style="--mdc-icon-size:14px;"
                                        ></ha-icon>
                                        YAML
                                      </button>
                                      <span class="card-tab-sep">|</span>
                                    `
                                  : ""}
                                ${hasAutomationId
                                  ? html`
                                      <button
                                        class="card-tab ${this._cardActiveTab[
                                          a.entity_id
                                        ] === "history"
                                          ? "active"
                                          : ""}"
                                        @click=${() => {
                                          const isActive =
                                            this._cardActiveTab[a.entity_id] ===
                                            "history";
                                          this._cardActiveTab = {
                                            ...this._cardActiveTab,
                                            [a.entity_id]: isActive
                                              ? null
                                              : "history",
                                          };
                                          if (
                                            !isActive &&
                                            !this._versions[automationId]
                                          ) {
                                            this._versionHistoryOpen = {
                                              ...this._versionHistoryOpen,
                                              [automationId]: true,
                                            };
                                            this._loadVersionHistory(
                                              automationId,
                                            );
                                          }
                                        }}
                                      >
                                        History
                                      </button>
                                    `
                                  : ""}
                                <ha-icon
                                  icon="mdi:chevron-down"
                                  class="card-chevron ${this._cardActiveTab[
                                    a.entity_id
                                  ]
                                    ? "open"
                                    : ""}"
                                  style="margin-left:auto;"
                                  title="Expand details"
                                  @click=${(e) => {
                                    e.stopPropagation();
                                    const current =
                                      this._cardActiveTab[a.entity_id];
                                    if (current) {
                                      this._cardActiveTab = {
                                        ...this._cardActiveTab,
                                        [a.entity_id]: null,
                                      };
                                    } else {
                                      // Open to first available tab to avoid blank expand
                                      const defaultTab =
                                        a.trigger?.length || a.action?.length
                                          ? "flow"
                                          : a.yaml_text
                                            ? "yaml"
                                            : hasAutomationId
                                              ? "history"
                                              : null;
                                      this._cardActiveTab = {
                                        ...this._cardActiveTab,
                                        [a.entity_id]: defaultTab,
                                      };
                                    }
                                  }}
                                ></ha-icon>
                              </div>

                              ${this._cardActiveTab[a.entity_id] === "flow" &&
                              (a.trigger?.length || a.action?.length)
                                ? this._renderAutomationFlowchart(a)
                                : ""}
                              ${this._cardActiveTab[a.entity_id] === "yaml" &&
                              a.yaml_text
                                ? this._renderYamlEditor(
                                    `yaml_${a.entity_id}`,
                                    a.yaml_text,
                                    (key) =>
                                      this._saveActiveAutomationYaml(
                                        a.automation_id,
                                        key,
                                      ),
                                  )
                                : ""}
                              ${this._cardActiveTab[a.entity_id] ===
                                "history" && hasAutomationId
                                ? this._renderVersionHistoryDrawer(a)
                                : ""}
                            </div>
                          `;
                        }),
                        3,
                        this._renderDeletedSection(),
                      )}
                    </div>
                    ${totalAutoPages > 1
                      ? html`
                          <div class="pagination">
                            <button
                              class="btn btn-outline"
                              ?disabled=${safeAutoPage <= 1}
                              @click=${() => {
                                this._automationsPage = safeAutoPage - 1;
                              }}
                            >
                              ‹ Prev
                            </button>
                            <span class="page-info"
                              >Page ${safeAutoPage} of ${totalAutoPages} ·
                              ${filteredAutomations.length} automations</span
                            >
                            <button
                              class="btn btn-outline"
                              ?disabled=${safeAutoPage >= totalAutoPages}
                              @click=${() => {
                                this._automationsPage = safeAutoPage + 1;
                              }}
                            >
                              Next ›
                            </button>
                          </div>
                        `
                      : ""}
                    ${filteredAutomations.length === 0 &&
                    this._automations.length > 0
                      ? html`<div
                          style="text-align:center;opacity:0.45;padding:24px 0;"
                        >
                          No automations match "${this._automationFilter}"
                        </div>`
                      : ""}
                  `
                : html`<div style="text-align:center;padding:32px 0;">
                    <ha-icon
                      icon="mdi:robot-vacuum-variant"
                      style="--mdc-icon-size:40px;display:block;margin-bottom:8px;opacity:0.35;"
                    ></ha-icon>
                    <p style="opacity:0.45;margin:0 0 12px;">
                      No automations yet.
                    </p>
                    <button
                      class="btn btn-primary"
                      @click=${() => {
                        this._newAutoName = "";
                        this._showNewAutoDialog = true;
                      }}
                    >
                      <ha-icon
                        icon="mdi:plus"
                        style="--mdc-icon-size:14px;"
                      ></ha-icon>
                      New Automation
                    </button>
                  </div>`}
            `
          : ""}
        ${this._automationsSubTab === "suggestions"
          ? html`
              ${this._renderUnifiedSuggestions()}
              ${this._suggestions.length > 0
                ? html`
                    <div class="automations-grid">
                      ${this._masonryColumns(
                        this._suggestions.map((item) => {
                          const auto = item.automation || item.automation_data;
                          const risk =
                            item.risk_assessment ||
                            auto?.risk_assessment ||
                            null;
                          const cardKey = `sug_${auto.alias}`;
                          const origYaml = item.automation_yaml || "";
                          const editedYaml = this._editedYaml[cardKey];
                          const displayYaml =
                            editedYaml !== undefined ? editedYaml : origYaml;
                          const hasFlow =
                            auto &&
                            (auto.trigger?.length ||
                              auto.triggers?.length ||
                              auto.action?.length ||
                              auto.actions?.length);
                          const defaultTab = hasFlow ? "flow" : "yaml";
                          const activeTab =
                            this._cardActiveTab[cardKey] !== undefined
                              ? this._cardActiveTab[cardKey]
                              : defaultTab;
                          return html`
                            <div
                              class="card"
                              style="padding:12px 14px;text-align:center;"
                            >
                              <div
                                class="card-header"
                                style="margin-bottom:6px;justify-content:center;"
                              >
                                <h3 style="font-size:14px;margin:0;">
                                  ${auto.alias}
                                </h3>
                              </div>
                              ${auto.description
                                ? html`<div
                                    style="font-size:11px;opacity:0.55;margin-bottom:6px;"
                                  >
                                    ${auto.description}
                                  </div>`
                                : ""}
                              ${risk?.level === "elevated"
                                ? html`
                                    <div
                                      class="proposal-status"
                                      style="background:rgba(255,152,0,0.12); color:var(--warning-color,#ff9800); border:1px solid rgba(255,152,0,0.25); margin-bottom:8px;font-size:12px;"
                                    >
                                      <ha-icon
                                        icon="mdi:alert-outline"
                                      ></ha-icon>
                                      <span>${risk.summary}</span>
                                    </div>
                                  `
                                : ""}

                              <div
                                style="display:flex;align-items:center;gap:6px;margin-bottom:2px;"
                              >
                                <button
                                  class="btn btn-primary"
                                  style="flex:1;font-size:11px;padding:5px 8px;justify-content:center;"
                                  ?disabled=${!!this._savingYaml[cardKey]}
                                  @click=${() =>
                                    this._createSuggestionWithEdits(
                                      auto,
                                      cardKey,
                                      origYaml,
                                    )}
                                >
                                  <ha-icon
                                    icon="mdi:check"
                                    style="--mdc-icon-size:13px;"
                                  ></ha-icon>
                                  ${this._savingYaml[cardKey]
                                    ? "Creating…"
                                    : "Accept"}
                                </button>
                                <button
                                  class="btn btn-outline"
                                  style="flex:1;font-size:11px;padding:5px 8px;justify-content:center;"
                                  @click=${() => this._discardSuggestion(item)}
                                >
                                  <ha-icon
                                    icon="mdi:close"
                                    style="--mdc-icon-size:13px;"
                                  ></ha-icon>
                                  Discard
                                </button>
                              </div>

                              <div class="card-tabs">
                                <span class="label">View:</span>
                                ${hasFlow
                                  ? html`
                                      <button
                                        class="card-tab ${activeTab === "flow"
                                          ? "active"
                                          : ""}"
                                        @click=${() => {
                                          this._cardActiveTab = {
                                            ...this._cardActiveTab,
                                            [cardKey]:
                                              activeTab === "flow"
                                                ? null
                                                : "flow",
                                          };
                                        }}
                                      >
                                        <ha-icon
                                          icon="mdi:sitemap-outline"
                                          style="--mdc-icon-size:14px;"
                                        ></ha-icon>
                                        Flow
                                      </button>
                                      <span class="card-tab-sep">|</span>
                                    `
                                  : ""}
                                <button
                                  class="card-tab ${activeTab === "yaml"
                                    ? "active"
                                    : ""}"
                                  @click=${() => {
                                    this._cardActiveTab = {
                                      ...this._cardActiveTab,
                                      [cardKey]:
                                        activeTab === "yaml" ? null : "yaml",
                                    };
                                  }}
                                >
                                  <ha-icon
                                    icon="mdi:code-braces"
                                    style="--mdc-icon-size:14px;"
                                  ></ha-icon>
                                  YAML
                                </button>
                                <ha-icon
                                  icon="mdi:chevron-down"
                                  class="card-chevron ${activeTab
                                    ? "open"
                                    : ""}"
                                  style="margin-left:auto;"
                                  @click=${() => {
                                    this._cardActiveTab = {
                                      ...this._cardActiveTab,
                                      [cardKey]: activeTab
                                        ? null
                                        : hasFlow
                                          ? "flow"
                                          : "yaml",
                                    };
                                  }}
                                ></ha-icon>
                              </div>

                              ${activeTab === "flow" && hasFlow
                                ? this._renderAutomationFlowchart(auto)
                                : ""}
                              ${activeTab === "yaml"
                                ? html`
                                    <div style="margin-top:6px;">
                                      <textarea
                                        class="yaml-editor"
                                        style="width:100%;font-family:monospace;font-size:12px;background:var(--primary-background-color);color:var(--primary-text-color);border:1px solid var(--divider-color);border-radius:6px;padding:8px;resize:none;overflow:hidden;field-sizing:content;"
                                        .value=${displayYaml}
                                        @input=${(e) => {
                                          this._editedYaml = {
                                            ...this._editedYaml,
                                            [cardKey]: e.target.value,
                                          };
                                          e.target.style.height = "auto";
                                          e.target.style.height =
                                            e.target.scrollHeight + "px";
                                        }}
                                        @focus=${(e) => {
                                          e.target.style.height = "auto";
                                          e.target.style.height =
                                            e.target.scrollHeight + "px";
                                        }}
                                      >
                                      </textarea>
                                    </div>
                                  `
                                : ""}
                            </div>
                          `;
                        }),
                      )}
                    </div>
                  `
                : ""}
            `
          : ""}
        ${this._renderDiffViewer()} ${this._renderNewAutomationDialog()}
      </div>
    `;
  }

  // -------------------------------------------------------------------------
  // Proactive suggestions section (automations tab)
  // -------------------------------------------------------------------------

  _renderUnifiedSuggestions() {
    const MIN_CONF = 0.8;
    const SPAGE_SIZE = this._suggestionsPerPage || 10;
    // Deduplicate proactive suggestions by description
    const seenDescs = new Set();
    const qualified = (this._proactiveSuggestions || []).filter((s) => {
      if ((s.confidence || 0) < MIN_CONF) return false;
      const key = (s.description || "").toLowerCase().trim();
      if (seenDescs.has(key)) return false;
      seenDescs.add(key);
      return true;
    });
    const totalItems = qualified.length + (this._suggestions || []).length;

    return html`
      <div style="margin-bottom:16px;">
        <div class="filter-row" style="margin-bottom:12px;">
          <div
            style="display:flex;align-items:center;gap:8px;justify-content:center;"
          >
            <button
              class="btn"
              style="font-size:11px;"
              ?disabled=${this._loadingProactive}
              @click=${() => this._triggerPatternScan()}
            >
              <ha-icon
                icon="mdi:refresh"
                style="--mdc-icon-size:13px;"
              ></ha-icon>
              ${this._loadingProactive ? "Scanning…" : "Scan Now"}
            </button>
            <button
              class="btn btn-primary"
              style="font-size:11px;"
              ?disabled=${this._generatingSuggestions}
              @click=${() => this._triggerGenerateSuggestions()}
            >
              ${this._generatingSuggestions
                ? html`<span
                    class="spinner"
                    style="width:14px;height:14px;border-width:2px;vertical-align:middle;"
                  ></span>`
                : html`<ha-icon
                    icon="mdi:auto-fix"
                    style="--mdc-icon-size:13px;"
                  ></ha-icon>`}
              ${this._generatingSuggestions ? "Analyzing…" : "Generate"}
            </button>
            <label class="per-page-label"
              >Show Per Page:
              <select
                class="per-page-select"
                .value=${String(this._suggestionsPerPage)}
                @change=${(e) => {
                  this._suggestionsPerPage = Number(e.target.value);
                  this._suggestionsPage = 1;
                }}
              >
                <option value="10">10</option>
                <option value="20">20</option>
                <option value="50">50</option>
              </select>
            </label>
          </div>
        </div>

        ${totalItems === 0
          ? html`
              <div
                style="display:flex;flex-direction:column;align-items:center;padding:32px 0;gap:12px;"
              >
                <ha-icon
                  icon="mdi:lightbulb-auto-outline"
                  style="--mdc-icon-size:48px;opacity:0.3;"
                ></ha-icon>
                <p style="opacity:0.45;margin:0;font-size:13px;">
                  No suggestions yet. Tap "Generate" to analyze your home.
                </p>
              </div>
            `
          : ""}
        ${qualified.length > 0
          ? html`
              ${(() => {
                const sTotalPages = Math.max(
                  1,
                  Math.ceil(qualified.length / SPAGE_SIZE),
                );
                const sSafePage = Math.min(this._suggestionsPage, sTotalPages);
                const sPaged = qualified.slice(
                  (sSafePage - 1) * SPAGE_SIZE,
                  sSafePage * SPAGE_SIZE,
                );

                return html`
                  <div class="automations-grid">
                    ${this._masonryColumns(
                      sPaged.map((s) => {
                        const accepting =
                          !!this._acceptingProactive[s.suggestion_id];
                        const dismissing =
                          !!this._dismissingProactive[s.suggestion_id];
                        const cardKey = `proactive_${s.suggestion_id}`;
                        const editedYaml = this._editedYaml[cardKey];
                        const displayYaml =
                          editedYaml !== undefined
                            ? editedYaml
                            : s.automation_yaml;
                        const parsedAuto = s.automation_data || null;
                        const hasFlow =
                          parsedAuto &&
                          (parsedAuto.trigger?.length ||
                            parsedAuto.triggers?.length ||
                            parsedAuto.action?.length ||
                            parsedAuto.actions?.length);
                        const defaultTab = hasFlow ? "flow" : "yaml";
                        const activeTab =
                          this._cardActiveTab[cardKey] !== undefined
                            ? this._cardActiveTab[cardKey]
                            : defaultTab;
                        return html`
                          <div
                            class="card"
                            style="padding:12px 14px;text-align:center;"
                          >
                            <div
                              class="card-header"
                              style="margin-bottom:6px;justify-content:center;"
                            >
                              <h3 style="font-size:14px;margin:0;">
                                ${s.description}
                              </h3>
                            </div>

                            ${s.evidence_summary
                              ? html`
                                  <div
                                    style="font-size:11px;opacity:0.55;margin-bottom:6px;"
                                  >
                                    ${s.evidence_summary}
                                  </div>
                                `
                              : ""}

                            <div
                              style="display:flex;align-items:center;gap:6px;margin-bottom:2px;"
                            >
                              <button
                                class="btn btn-primary"
                                style="flex:1;font-size:11px;padding:5px 8px;justify-content:center;"
                                ?disabled=${accepting}
                                @click=${() =>
                                  this._acceptProactiveSuggestion(
                                    s.suggestion_id,
                                    editedYaml,
                                  )}
                              >
                                <ha-icon
                                  icon="mdi:check"
                                  style="--mdc-icon-size:13px;"
                                ></ha-icon>
                                ${accepting ? "Creating…" : "Accept"}
                              </button>
                              <button
                                class="btn btn-outline"
                                style="flex:1;font-size:11px;padding:5px 8px;justify-content:center;"
                                ?disabled=${dismissing}
                                @click=${() =>
                                  this._dismissProactiveSuggestion(
                                    s.suggestion_id,
                                  )}
                              >
                                <ha-icon
                                  icon="mdi:close"
                                  style="--mdc-icon-size:13px;"
                                ></ha-icon>
                                ${dismissing ? "Dismissing…" : "Dismiss"}
                              </button>
                              <button
                                class="btn btn-outline"
                                style="flex:1;font-size:11px;padding:5px 8px;justify-content:center;"
                                ?disabled=${dismissing}
                                @click=${() =>
                                  this._snoozeProactiveSuggestion(
                                    s.suggestion_id,
                                  )}
                              >
                                <ha-icon
                                  icon="mdi:clock-outline"
                                  style="--mdc-icon-size:13px;"
                                ></ha-icon>
                                Snooze
                              </button>
                            </div>

                            <div class="card-tabs">
                              <span class="label">View:</span>
                              ${hasFlow
                                ? html`
                                    <button
                                      class="card-tab ${activeTab === "flow"
                                        ? "active"
                                        : ""}"
                                      @click=${() => {
                                        this._cardActiveTab = {
                                          ...this._cardActiveTab,
                                          [cardKey]:
                                            activeTab === "flow"
                                              ? null
                                              : "flow",
                                        };
                                      }}
                                    >
                                      <ha-icon
                                        icon="mdi:sitemap-outline"
                                        style="--mdc-icon-size:14px;"
                                      ></ha-icon>
                                      Flow
                                    </button>
                                    <span class="card-tab-sep">|</span>
                                  `
                                : ""}
                              <button
                                class="card-tab ${activeTab === "yaml"
                                  ? "active"
                                  : ""}"
                                @click=${() => {
                                  this._cardActiveTab = {
                                    ...this._cardActiveTab,
                                    [cardKey]:
                                      activeTab === "yaml" ? null : "yaml",
                                  };
                                }}
                              >
                                <ha-icon
                                  icon="mdi:code-braces"
                                  style="--mdc-icon-size:14px;"
                                ></ha-icon>
                                YAML
                              </button>
                              <ha-icon
                                icon="mdi:chevron-down"
                                class="card-chevron ${activeTab ? "open" : ""}"
                                style="margin-left:auto;"
                                @click=${() => {
                                  this._cardActiveTab = {
                                    ...this._cardActiveTab,
                                    [cardKey]: activeTab ? null : "yaml",
                                  };
                                }}
                              ></ha-icon>
                            </div>

                            ${activeTab === "flow" && hasFlow
                              ? this._renderAutomationFlowchart(parsedAuto)
                              : ""}
                            ${activeTab === "yaml"
                              ? html`
                                  <div style="margin-top:6px;">
                                    <textarea
                                      class="yaml-editor"
                                      style="width:100%;font-family:monospace;font-size:12px;background:var(--primary-background-color);color:var(--primary-text-color);border:1px solid var(--divider-color);border-radius:6px;padding:8px;resize:none;overflow:hidden;field-sizing:content;"
                                      .value=${displayYaml}
                                      @input=${(e) => {
                                        this._editedYaml = {
                                          ...this._editedYaml,
                                          [cardKey]: e.target.value,
                                        };
                                        e.target.style.height = "auto";
                                        e.target.style.height =
                                          e.target.scrollHeight + "px";
                                      }}
                                      @focus=${(e) => {
                                        e.target.style.height = "auto";
                                        e.target.style.height =
                                          e.target.scrollHeight + "px";
                                      }}
                                    >
                                    </textarea>
                                  </div>
                                `
                              : ""}
                          </div>
                        `;
                      }),
                    )}
                  </div>
                  ${sTotalPages > 1
                    ? html`
                        <div class="pagination">
                          <button
                            class="btn btn-outline"
                            ?disabled=${sSafePage <= 1}
                            @click=${() => {
                              this._suggestionsPage = sSafePage - 1;
                            }}
                          >
                            ‹ Prev
                          </button>
                          <span class="page-info"
                            >Page ${sSafePage} of ${sTotalPages} ·
                            ${qualified.length} suggestions</span
                          >
                          <button
                            class="btn btn-outline"
                            ?disabled=${sSafePage >= sTotalPages}
                            @click=${() => {
                              this._suggestionsPage = sSafePage + 1;
                            }}
                          >
                            Next ›
                          </button>
                        </div>
                      `
                    : ""}
                `;
              })()}
            `
          : ""}
      </div>
    `;
  }

  // -------------------------------------------------------------------------
  // Settings tab
  // -------------------------------------------------------------------------

  _renderSettings() {
    if (!this._config) {
      return html`
        <div
          class="scroll-view"
          style="display:flex; justify-content:center; padding-top:64px;"
        >
          <ha-circular-progress active></ha-circular-progress>
        </div>
      `;
    }

    const isAnthropic = this._config.llm_provider === "anthropic";
    const isOpenAI = this._config.llm_provider === "openai";

    return html`
      <div class="scroll-view">
        <div class="settings-form">
          <h2>Integration Settings</h2>

          <div class="form-group">
            <label>LLM Provider</label>
            <select
              .value=${this._config.llm_provider}
              @change=${(e) =>
                this._updateConfig("llm_provider", e.target.value)}
              style="padding:8px; border-radius:4px; background:var(--card-background-color); color:var(--primary-text-color); border:1px solid var(--divider-color); width:100%;"
            >
              <option value="anthropic">Anthropic (Claude)</option>
              <option value="openai">OpenAI</option>
              <option value="ollama">Ollama (Local)</option>
            </select>
          </div>

          ${isAnthropic
            ? html`
                <div class="form-group">
                  <label>Anthropic API Key</label>
                  ${this._config.anthropic_api_key_set
                    ? html`<div class="key-hint">
                        Current key: ${this._config.anthropic_api_key_hint}
                      </div>`
                    : html`<div class="key-not-set">No API key set.</div>`}
                  <ha-textfield
                    label="${this._config.anthropic_api_key_set
                      ? "Enter new key to replace"
                      : "Enter API key"}"
                    type="password"
                    .value=${this._newApiKey}
                    @input=${(e) => (this._newApiKey = e.target.value)}
                    placeholder="sk-ant-..."
                    style="margin-top:8px;"
                  ></ha-textfield>
                </div>
                <div class="form-group">
                  <ha-textfield
                    label="Anthropic Model"
                    .value=${this._config.anthropic_model}
                    @input=${(e) =>
                      this._updateConfig("anthropic_model", e.target.value)}
                  ></ha-textfield>
                </div>
              `
            : isOpenAI
              ? html`
                  <div class="form-group">
                    <label>OpenAI API Key</label>
                    ${this._config.openai_api_key_set
                      ? html`<div class="key-hint">
                          Current key: ${this._config.openai_api_key_hint}
                        </div>`
                      : html`<div class="key-not-set">No API key set.</div>`}
                    <ha-textfield
                      label="${this._config.openai_api_key_set
                        ? "Enter new key to replace"
                        : "Enter API key"}"
                      type="password"
                      .value=${this._newApiKey}
                      @input=${(e) => (this._newApiKey = e.target.value)}
                      placeholder="sk-..."
                      style="margin-top:8px;"
                    ></ha-textfield>
                  </div>
                  <div class="form-group">
                    <ha-textfield
                      label="OpenAI Model"
                      .value=${this._config.openai_model}
                      @input=${(e) =>
                        this._updateConfig("openai_model", e.target.value)}
                    ></ha-textfield>
                  </div>
                `
              : html`
                  <div class="form-group">
                    <ha-textfield
                      label="Ollama Host"
                      .value=${this._config.ollama_host}
                      @input=${(e) =>
                        this._updateConfig("ollama_host", e.target.value)}
                    ></ha-textfield>
                  </div>
                  <div class="form-group">
                    <ha-textfield
                      label="Ollama Model"
                      .value=${this._config.ollama_model}
                      @input=${(e) =>
                        this._updateConfig("ollama_model", e.target.value)}
                    ></ha-textfield>
                  </div>
                `}

          <h3
            style="border-bottom:1px solid var(--divider-color); padding-bottom:8px;"
          >
            Background Services
          </h3>

          <div style="margin-top:16px;">
            <div
              style="display:flex; align-items:center; gap:8px; margin-bottom:16px;"
            >
              <ha-switch
                .checked=${this._config.collector_enabled}
                @change=${(e) =>
                  this._updateConfig("collector_enabled", e.target.checked)}
              ></ha-switch>
              <label>Data Collector (AI Analysis)</label>
            </div>

            ${this._config.collector_enabled
              ? html`
                  <div
                    style="padding-left:20px; border-left:2px solid var(--divider-color); margin-bottom:20px;"
                  >
                    <div class="form-group">
                      <label>Mode</label>
                      <select
                        .value=${this._config.collector_mode}
                        @change=${(e) =>
                          this._updateConfig("collector_mode", e.target.value)}
                        style="padding:8px; border-radius:4px; background:var(--card-background-color); color:var(--primary-text-color); border:1px solid var(--divider-color); width:100%;"
                      >
                        <option value="continuous">Continuous</option>
                        <option value="scheduled">Scheduled Window</option>
                      </select>
                    </div>
                    <div class="form-group">
                      <ha-textfield
                        label="Interval (seconds)"
                        type="number"
                        .value=${this._config.collector_interval}
                        @input=${(e) =>
                          this._updateConfig(
                            "collector_interval",
                            parseInt(e.target.value),
                          )}
                      ></ha-textfield>
                    </div>
                    ${this._config.collector_mode === "scheduled"
                      ? html`
                          <div style="display:flex; gap:12px;">
                            <ha-textfield
                              label="Start (HH:MM)"
                              .value=${this._config.collector_start_time}
                              @input=${(e) =>
                                this._updateConfig(
                                  "collector_start_time",
                                  e.target.value,
                                )}
                              style="flex:1;"
                            ></ha-textfield>
                            <ha-textfield
                              label="End (HH:MM)"
                              .value=${this._config.collector_end_time}
                              @input=${(e) =>
                                this._updateConfig(
                                  "collector_end_time",
                                  e.target.value,
                                )}
                              style="flex:1;"
                            ></ha-textfield>
                          </div>
                        `
                      : ""}
                  </div>
                `
              : ""}

            <div
              style="display:flex; align-items:center; gap:8px; margin-bottom:16px;"
            >
              <ha-switch
                .checked=${this._config.discovery_enabled}
                @change=${(e) =>
                  this._updateConfig("discovery_enabled", e.target.checked)}
              ></ha-switch>
              <label>Network Discovery</label>
            </div>

            ${this._config.discovery_enabled
              ? html`
                  <div
                    style="padding-left:20px; border-left:2px solid var(--divider-color); margin-bottom:20px;"
                  >
                    <div class="form-group">
                      <label>Mode</label>
                      <select
                        .value=${this._config.discovery_mode}
                        @change=${(e) =>
                          this._updateConfig("discovery_mode", e.target.value)}
                        style="padding:8px; border-radius:4px; background:var(--card-background-color); color:var(--primary-text-color); border:1px solid var(--divider-color); width:100%;"
                      >
                        <option value="continuous">Continuous</option>
                        <option value="scheduled">Scheduled Window</option>
                      </select>
                    </div>
                    <div class="form-group">
                      <ha-textfield
                        label="Interval (seconds)"
                        type="number"
                        .value=${this._config.discovery_interval}
                        @input=${(e) =>
                          this._updateConfig(
                            "discovery_interval",
                            parseInt(e.target.value),
                          )}
                      ></ha-textfield>
                    </div>
                    ${this._config.discovery_mode === "scheduled"
                      ? html`
                          <div style="display:flex; gap:12px;">
                            <ha-textfield
                              label="Start (HH:MM)"
                              .value=${this._config.discovery_start_time}
                              @input=${(e) =>
                                this._updateConfig(
                                  "discovery_start_time",
                                  e.target.value,
                                )}
                              style="flex:1;"
                            ></ha-textfield>
                            <ha-textfield
                              label="End (HH:MM)"
                              .value=${this._config.discovery_end_time}
                              @input=${(e) =>
                                this._updateConfig(
                                  "discovery_end_time",
                                  e.target.value,
                                )}
                              style="flex:1;"
                            ></ha-textfield>
                          </div>
                        `
                      : ""}
                  </div>
                `
              : ""}
          </div>

          <div class="save-bar">
            <mwc-button
              raised
              @click=${this._saveConfig}
              ?disabled=${this._savingConfig}
            >
              ${this._savingConfig ? "Saving…" : "Save Settings"}
            </mwc-button>
          </div>
        </div>
      </div>
    `;
  }

  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------

  _formatDate(iso) {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      const now = new Date();
      const diffMs = now - d;
      if (diffMs < 60000) return "just now";
      if (diffMs < 3600000) return `${Math.floor(diffMs / 60000)}m ago`;
      if (diffMs < 86400000) return `${Math.floor(diffMs / 3600000)}h ago`;
      return d.toLocaleDateString();
    } catch {
      return "";
    }
  }

  _formatTime(iso) {
    if (!iso) return "";
    try {
      return new Date(iso).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return "";
    }
  }
}

customElements.define("selora-ai-architect", SeloraAIArchitectPanel);
