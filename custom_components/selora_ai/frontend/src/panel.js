import { LitElement, html } from "lit";
import { seloraTokens } from "./shared/design-tokens.css.js";
import { sharedAnimations } from "./shared/styles/animations.css.js";
import { sharedButtons } from "./shared/styles/buttons.css.js";
import { sharedModals } from "./shared/styles/modals.css.js";
import { sharedBadges } from "./shared/styles/badges.css.js";
import { sharedLoaders } from "./shared/styles/loaders.css.js";
import { sharedScrollbar } from "./shared/styles/scrollbar.css.js";
import { allPanelStyles } from "./panel/styles/index.css.js";
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

      // Action loading states
      _deletingAutomation: { type: Object },
      _restoringVersion: { type: Object },
      _loadingToChat: { type: Object },

      // Bulk automation actions
      _selectedAutomationIds: { type: Object },
      _bulkActionInProgress: { type: Boolean },
      _bulkActionLabel: { type: String },

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

      // Unavailable automation modal
      _unavailableAutoId: { type: String },
      _unavailableAutoName: { type: String },

      // Feedback modal
      _showFeedbackModal: { type: Boolean },
      _feedbackText: { type: String },
      _feedbackRating: { type: String },
      _feedbackCategory: { type: String },
      _feedbackEmail: { type: String },
      _submittingFeedback: { type: Boolean },
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
    // Action loading states
    this._deletingAutomation = {};
    this._restoringVersion = {};
    this._loadingToChat = {};
    this._selectedAutomationIds = {};
    this._bulkActionInProgress = false;
    this._bulkActionLabel = "";
    this._toast = "";
    this._toastType = "info";
    this._toastTimer = null;
    this._expandedDetailId = null;
    this._showNewAutoDialog = false;
    this._newAutoName = "";
    this._suggestingName = false;
    this._unavailableAutoId = null;
    this._unavailableAutoName = null;
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
    // Feedback modal
    this._showFeedbackModal = false;
    this._feedbackText = "";
    this._feedbackRating = "";
    this._feedbackCategory = "";
    this._feedbackEmail = "";
    this._submittingFeedback = false;
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
    this._loadConfig();
    this._locationHandler = () => this._checkTabParam();
    window.addEventListener("location-changed", this._locationHandler);
    this._keyDownHandler = (e) => {
      if (
        e.key === "Escape" &&
        this._showFeedbackModal &&
        !this._submittingFeedback
      ) {
        this._closeFeedback();
      }
    };
    window.addEventListener("keydown", this._keyDownHandler);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._locationHandler) {
      window.removeEventListener("location-changed", this._locationHandler);
    }
    if (this._keyDownHandler) {
      window.removeEventListener("keydown", this._keyDownHandler);
      this._keyDownHandler = null;
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

  get _llmNeedsSetup() {
    if (!this._config) return false;
    const provider = this._config.llm_provider;
    if (!provider) return true; // No provider selected at all
    if (provider === "anthropic") return !this._config.anthropic_api_key_set;
    if (provider === "openai") return !this._config.openai_api_key_set;
    return false; // Ollama doesn't require an API key
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

  _t(key, fallback) {
    return (
      this.hass?.localize?.(`component.selora_ai.common.${key}`) || fallback
    );
  }

  _openFeedback() {
    this._showFeedbackModal = true;
  }

  _closeFeedback() {
    if (this._submittingFeedback) return;
    this._showFeedbackModal = false;
    this._feedbackText = "";
    this._feedbackRating = "";
    this._feedbackCategory = "";
    this._feedbackEmail = "";
  }

  async _submitFeedback() {
    if (this._submittingFeedback) return;
    const text = (this._feedbackText || "").trim();
    if (text.length < 10) {
      this._showToast(
        this._t(
          "feedback_min_length_error",
          "Please enter at least 10 characters.",
        ),
        "error",
      );
      return;
    }

    this._submittingFeedback = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const payload = {
        message: text,
        ha_version: this.hass?.config?.version || "unknown",
        integration_version:
          typeof __SELORA_VERSION__ !== "undefined"
            ? __SELORA_VERSION__
            : "unknown",
      };
      if (this._feedbackRating) payload.rating = this._feedbackRating;
      if (this._feedbackCategory) payload.category = this._feedbackCategory;
      const email = (this._feedbackEmail || "").trim();
      if (email) payload.email = email;
      const res = await fetch(
        "https://qiob98god6.execute-api.us-east-1.amazonaws.com/api/feedback",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          signal: controller.signal,
        },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      this._showToast(
        this._t("feedback_success", "Thanks for your feedback!"),
        "success",
      );
      this._showFeedbackModal = false;
      this._feedbackText = "";
      this._feedbackRating = "";
      this._feedbackCategory = "";
      this._feedbackEmail = "";
    } catch (err) {
      this._showToast(
        err?.message ||
          this._t(
            "feedback_error",
            "Couldn’t send feedback — please try again.",
          ),
        "error",
      );
    } finally {
      clearTimeout(timeout);
      this._submittingFeedback = false;
    }
  }

  // -------------------------------------------------------------------------
  // Scroll to bottom on new messages
  // -------------------------------------------------------------------------

  updated(changedProps) {
    if (changedProps.has("hass")) {
      this._checkTabParam();
      // Set accent text color based on HA dark mode (gold on dark, black on light)
      const dark = this.hass?.themes?.darkMode;
      if (dark !== undefined) {
        this.style.setProperty(
          "--selora-accent-text",
          dark ? "#fbbf24" : "#18181b",
        );
      }
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
    return [
      seloraTokens,
      sharedAnimations,
      sharedButtons,
      sharedModals,
      sharedBadges,
      sharedLoaders,
      sharedScrollbar,
      ...allPanelStyles,
    ];
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

  _renderFeedbackModal() {
    if (!this._showFeedbackModal) return "";
    const textLength = (this._feedbackText || "").length;
    const tooShort = (this._feedbackText || "").trim().length < 10;
    const ratingOptions = [
      {
        value: "thumbsup",
        icon: "mdi:thumb-up-outline",
        label: this._t("feedback_rating_thumbsup", "Thumbs up"),
      },
      {
        value: "thumbsdown",
        icon: "mdi:thumb-down-outline",
        label: this._t("feedback_rating_thumbsdown", "Thumbs down"),
      },
    ];
    const categoryOptions = [
      {
        value: "bug",
        label: this._t("feedback_category_bug", "Bug"),
      },
      {
        value: "feature",
        label: this._t("feedback_category_feature", "Feature Request"),
      },
      {
        value: "general",
        label: this._t("feedback_category_general", "General"),
      },
    ];

    return html`
      <div
        class="modal-overlay"
        @click=${(e) => {
          if (e.target === e.currentTarget) this._closeFeedback();
        }}
      >
        <div
          class="modal-content"
          role="dialog"
          aria-modal="true"
          @keydown=${(e) => {
            if (e.key === "Enter" && e.target.tagName !== "TEXTAREA") {
              e.preventDefault();
              this._submitFeedback();
            }
          }}
          aria-labelledby="selora-feedback-title"
          style="max-width:520px;"
        >
          <div
            id="selora-feedback-title"
            style="font-size:18px;font-weight:600;margin-bottom:8px;"
          >
            ${this._t("feedback_modal_title", "Share Feedback")}
          </div>
          <div style="font-size:12px;opacity:0.7;margin-bottom:14px;">
            ${this._t(
              "feedback_privacy_notice",
              "Feedback is anonymous and contains no personal data.",
            )}
          </div>

          <textarea
            maxlength="2000"
            style="width:100%;min-height:120px;resize:vertical;padding:10px 12px;border-radius:8px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color);font:inherit;box-sizing:border-box;margin-bottom:6px;"
            placeholder=${this._t(
              "feedback_textarea_placeholder",
              "What's on your mind? (10 characters minimum)",
            )}
            .value=${this._feedbackText}
            @input=${(e) => {
              this._feedbackText = e.target.value;
            }}
          ></textarea>
          <div
            style="font-size:11px;opacity:0.6;text-align:right;margin-bottom:12px;"
          >
            ${textLength}/2000
          </div>

          <div style="font-size:12px;opacity:0.7;margin-bottom:6px;">
            ${this._t("feedback_rating_label", "Rating:")}
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;">
            ${ratingOptions.map(
              (opt) => html`
                <button
                  class="btn btn-outline"
                  style="padding:6px 10px;${this._feedbackRating === opt.value
                    ? "border-color:var(--selora-accent);color:var(--selora-accent);background:rgba(251,191,36,0.08);"
                    : ""}"
                  aria-pressed=${this._feedbackRating === opt.value
                    ? "true"
                    : "false"}
                  title=${opt.label}
                  @click=${() => {
                    this._feedbackRating =
                      this._feedbackRating === opt.value ? "" : opt.value;
                  }}
                >
                  <ha-icon
                    icon=${opt.icon}
                    style="--mdc-icon-size:18px;"
                  ></ha-icon>
                </button>
              `,
            )}
          </div>

          <div style="font-size:12px;opacity:0.7;margin-bottom:6px;">
            ${this._t("feedback_category_label", "Category (optional):")}
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;">
            ${categoryOptions.map(
              (opt) => html`
                <button
                  class="btn btn-outline"
                  style="padding:6px 10px;${this._feedbackCategory === opt.value
                    ? "border-color:var(--selora-accent);color:var(--selora-accent);background:rgba(251,191,36,0.08);"
                    : ""}"
                  aria-pressed=${this._feedbackCategory === opt.value
                    ? "true"
                    : "false"}
                  @click=${() => {
                    this._feedbackCategory =
                      this._feedbackCategory === opt.value ? "" : opt.value;
                  }}
                >
                  ${opt.label}
                </button>
              `,
            )}
          </div>

          <div style="margin-bottom:14px;">
            <div style="font-size:12px;opacity:0.7;margin-bottom:6px;">
              ${this._t("feedback_email_label", "Email (optional):")}
            </div>
            <input
              type="email"
              style="width:100%;box-sizing:border-box;padding:8px 12px;border-radius:8px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color);font:inherit;font-size:13px;"
              placeholder=${this._t(
                "feedback_email_placeholder",
                "your@email.com — only if you'd like a reply",
              )}
              .value=${this._feedbackEmail}
              @input=${(e) => {
                this._feedbackEmail = e.target.value;
              }}
            />
          </div>

          <div style="display:flex;justify-content:flex-end;gap:8px;">
            <button
              class="btn btn-outline"
              ?disabled=${this._submittingFeedback}
              @click=${() => this._closeFeedback()}
            >
              ${this._t("feedback_cancel", "Cancel")}
            </button>
            <button
              class="btn btn-primary"
              ?disabled=${this._submittingFeedback || tooShort}
              @click=${() => this._submitFeedback()}
            >
              ${this._submittingFeedback
                ? this._t("feedback_submitting", "Sending…")
                : this._t("feedback_submit", "Send Feedback")}
            </button>
          </div>
        </div>
      </div>
    `;
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
          <button class="feedback-link" @click=${() => this._openFeedback()}>
            ${this._t("feedback_button_label", "Give Feedback")}
          </button>
          <a
            href="https://github.com/SeloraHomes/ha-selora-ai/issues"
            target="_blank"
            rel="noopener noreferrer"
            title="GitHub Issues"
            class="header-icon-link"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
              <path
                d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"
              />
            </svg>
          </a>
          <a
            href="https://gitlab.com/selorahomes/products/selora-ai/ha-integration/"
            target="_blank"
            rel="noopener noreferrer"
            title="GitLab Repository"
            class="header-icon-link"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
              <path
                d="m23.6 9.593-.033-.086L20.3.98a.851.851 0 0 0-.336-.405.87.87 0 0 0-.52-.155.86.86 0 0 0-.52.164.86.86 0 0 0-.324.413L16.6 6.544H7.4L5.4 1.003A.86.86 0 0 0 5.07.583a.87.87 0 0 0-.52-.164.86.86 0 0 0-.52.155.85.85 0 0 0-.336.405L.428 9.5l-.033.09a6.07 6.07 0 0 0 2.012 7.01l.01.008.028.02 4.97 3.722 2.458 1.86 1.496 1.13a1.01 1.01 0 0 0 1.22 0l1.497-1.13 2.457-1.86 5-3.743.012-.01a6.07 6.07 0 0 0 2.005-7.003"
              />
            </svg>
          </a>
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

      ${this._renderFeedbackModal()}
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
