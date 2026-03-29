import { LitElement, html } from "lit";
import { seloraTokens } from "./shared/design-tokens.css.js";
import { panelStyles } from "./panel/styles.css.js";
import { formatDate } from "./shared/date-utils.js";
import {
  renderChat,
  renderMessage,
  renderYamlEditor,
  renderNewAutomationDialog,
} from "./panel/render-chat.js";
import {
  renderAutomations,
  renderAutomationFlowchart,
  renderProposalCard,
  toggleYaml,
  masonryColumns,
} from "./panel/render-automations.js";
import { renderSuggestionsSection } from "./panel/render-suggestions.js";
import { renderSettings } from "./panel/render-settings.js";
import {
  renderVersionHistoryDrawer,
  renderDiffViewer,
  renderDeletedSection,
  renderHardDeleteDialog,
} from "./panel/render-version-history.js";
import * as sessionActions from "./panel/session-actions.js";
import * as suggestionActions from "./panel/suggestion-actions.js";
import * as chatActions from "./panel/chat-actions.js";
import * as automationCrud from "./panel/automation-crud.js";
import * as automationManagement from "./panel/automation-management.js";

// ---------------------------------------------------------------------------
// Selora AI Architect Panel
// ---------------------------------------------------------------------------
// Layout: two-pane when wide, single-pane when narrow.
//   Left pane  — session list + "New Chat" button
//   Right pane — active session (messages + input)
//
// Tabs within right pane: Chat | Automations | Settings
// ---------------------------------------------------------------------------

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
      _statusFilter: { type: String },
      _sortBy: { type: String },

      // Suggestion filter
      _suggestionFilter: { type: String },
      _suggestionSourceFilter: { type: String },
      _suggestionSortBy: { type: String },

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

      // Suggestions visible count (incremental load)
      _suggestionsVisibleCount: { type: Number },
      // Suggestions bulk edit
      _suggestionBulkMode: { type: Boolean },
      _selectedSuggestionKeys: { type: Object },

      // Highlight newly accepted automation
      _highlightedAutomation: { type: String },
      // Fading out suggestion card keys
      _fadingOutSuggestions: { type: Object },

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
      _swipedSessionId: { type: String },
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
    this._suggestionsVisibleCount = 3;
    this._suggestionBulkMode = false;
    this._highlightedAutomation = null;
    this._fadingOutSuggestions = {};
    this._selectedSuggestionKeys = {};
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
    this._statusFilter = "all";
    this._sortBy = "recent";
    this._suggestionFilter = "";
    this._suggestionSourceFilter = "all";
    this._suggestionSortBy = "recent";
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
    this._autosPerPage = 20;
    this._suggestionsPerPage = 10;
  }

  connectedCallback() {
    super.connectedCallback();
    // Inject Inter font into document head (Shadow DOM can't @import fonts)
    if (!document.querySelector("link[data-selora-font]")) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href =
        "https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap";
      link.dataset.seloraFont = "1";
      document.head.appendChild(link);
    }
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

  // -------------------------------------------------------------------------
  // Config
  // -------------------------------------------------------------------------

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

  _goToSettings() {
    this._activeTab = "settings";
    this._loadConfig();
  }

  _updateConfig(key, value) {
    this._config = { ...this._config, [key]: value };
    this.requestUpdate();
  }

  _highlightAndScrollToNew() {
    // Find the newest automation (last in list after sort by recent)
    const newest = this._automations[0];
    if (!newest) return;
    this._highlightedAutomation = newest.entity_id;
    this.requestUpdate();
    requestAnimationFrame(() => {
      const row = this.shadowRoot.querySelector(
        `.auto-row[data-entity-id="${newest.entity_id}"]`,
      );
      if (row) {
        row.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    });
    setTimeout(() => {
      this._highlightedAutomation = null;
    }, 3000);
  }

  // -------------------------------------------------------------------------
  // Toast notifications
  // -------------------------------------------------------------------------

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
    return [seloraTokens, panelStyles];
  }

  // -------------------------------------------------------------------------
  // Render delegation wrappers
  // -------------------------------------------------------------------------

  _renderNewAutomationDialog() {
    return renderNewAutomationDialog(this);
  }

  _renderChat() {
    return renderChat(this);
  }

  _renderMessage(msg, idx) {
    return renderMessage(this, msg, idx);
  }

  _renderYamlEditor(key, originalYaml, onSave) {
    return renderYamlEditor(this, key, originalYaml, onSave);
  }

  _renderAutomationFlowchart(auto) {
    return renderAutomationFlowchart(this, auto);
  }

  _renderProposalCard(msg, msgIndex) {
    return renderProposalCard(this, msg, msgIndex);
  }

  _toggleYaml(msgIndex) {
    return toggleYaml(this, msgIndex);
  }

  _masonryColumns(cards, cols, firstColFooter) {
    return masonryColumns(cards, cols, firstColFooter);
  }

  _renderAutomations() {
    return renderAutomations(this);
  }

  _renderSuggestionsSection() {
    return renderSuggestionsSection(this);
  }

  _renderSettings() {
    return renderSettings(this);
  }

  _renderVersionHistoryDrawer(a) {
    return renderVersionHistoryDrawer(this, a);
  }

  _renderDiffViewer() {
    return renderDiffViewer(this);
  }

  _renderDeletedSection() {
    return renderDeletedSection(this);
  }

  _renderHardDeleteDialog() {
    return renderHardDeleteDialog(this);
  }

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  render() {
    return html`
      <div class="header">
        <div class="header-top">
          <img
            src="/api/selora_ai/logo.png"
            alt="Selora"
            style="width:28px;height:28px;border-radius:6px;"
          />
          <span class="gold-text">Selora AI</span>
        </div>
        <div class="tabs">
          <div
            class="tab ${this._activeTab === "chat" ? "active" : ""}"
            @click=${() => {
              if (this._activeTab === "chat") {
                this._showSidebar = !this._showSidebar;
              } else {
                this._activeTab = "chat";
                this._showSidebar = true;
              }
            }}
          >
            <span class="tab-inner"
              ><ha-icon icon="mdi:chat-outline" class="tab-icon"></ha-icon
              ><span class="tab-text">Chat</span></span
            >
          </div>
          <div
            class="tab ${this._activeTab === "automations" ? "active" : ""}"
            @click=${() => {
              this._activeTab = "automations";
              this._showSidebar = false;
              this._loadAutomations();
            }}
          >
            <span class="tab-inner"
              ><ha-icon icon="mdi:robot-outline" class="tab-icon"></ha-icon
              ><span class="tab-text">Automations</span></span
            >
          </div>
          <div
            class="tab ${this._activeTab === "settings" ? "active" : ""}"
            @click=${() => {
              this._activeTab = "settings";
              this._showSidebar = false;
              this._loadConfig();
            }}
          >
            <span class="tab-inner"
              ><ha-icon icon="mdi:cog-outline" class="tab-icon"></ha-icon
              ><span class="tab-text">Settings</span></span
            >
          </div>
        </div>
      </div>

      <div class="body">
        <div class="sidebar ${this._showSidebar ? "open" : ""}" part="sidebar">
          <div class="sidebar-header">
            <span>Conversations</span>
            <div
              style="display:flex;align-items:center;gap:6px;margin-left:auto;"
            >
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
              <ha-icon
                icon="mdi:close"
                style="--mdc-icon-size:18px;cursor:pointer;opacity:0.6;"
                @click=${() => (this._showSidebar = false)}
              ></ha-icon>
            </div>
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
                      this._sessions.every(
                        (s) => this._selectedSessionIds[s.id],
                      )}
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
                <button
                  class="btn btn-primary new-chat-btn"
                  style="width:calc(100% - 24px);"
                  @click=${this._newSession}
                >
                  <ha-icon
                    icon="mdi:plus"
                    style="--mdc-icon-size:16px;"
                  ></ha-icon>
                  New Chat
                </button>
              `}
          <div class="session-list">
            ${this._sessions.length === 0
              ? html`<div style="padding: 16px; font-size: 12px; opacity: 0.5;">
                  No conversations yet.
                </div>`
              : this._sessions.map(
                  (s) => html`
                    <div
                      class="session-item-wrapper ${this._swipedSessionId ===
                      s.id
                        ? "reveal-delete"
                        : ""}"
                    >
                      <div
                        class="session-item-delete-bg"
                        @click=${(e) => this._deleteSession(s.id, e)}
                      >
                        <ha-icon icon="mdi:delete-outline"></ha-icon>
                      </div>
                      <div
                        class="session-item ${s.id === this._activeSessionId
                          ? "active"
                          : ""} ${this._swipedSessionId === s.id
                          ? "swiped"
                          : ""}"
                        @click=${() => {
                          if (this._swipedSessionId === s.id) {
                            this._swipedSessionId = null;
                            return;
                          }
                          this._selectChatsMode
                            ? this._toggleSessionSelection(s.id)
                            : this._openSession(s.id);
                        }}
                        @touchstart=${(e) => this._onSessionTouchStart(e, s.id)}
                        @touchmove=${(e) => this._onSessionTouchMove(e, s.id)}
                        @touchend=${(e) => this._onSessionTouchEnd(e, s.id)}
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
                            ${formatDate(s.updated_at)}
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
                    </div>
                  `,
                )}
          </div>
        </div>

        <div
          class="main"
          @click=${() => {
            if (this.narrow && this._showSidebar) this._showSidebar = false;
          }}
        >
          ${this._activeTab === "chat" ? this._renderChat() : ""}
          ${this._activeTab === "automations" ? this._renderAutomations() : ""}
          ${this._activeTab === "settings" ? this._renderSettings() : ""}
        </div>
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
}

// Attach extracted business logic to prototype
Object.assign(SeloraAIArchitectPanel.prototype, sessionActions);
Object.assign(SeloraAIArchitectPanel.prototype, suggestionActions);
Object.assign(SeloraAIArchitectPanel.prototype, chatActions);
Object.assign(SeloraAIArchitectPanel.prototype, automationCrud);
Object.assign(SeloraAIArchitectPanel.prototype, automationManagement);

customElements.define("selora-ai-architect", SeloraAIArchitectPanel);
