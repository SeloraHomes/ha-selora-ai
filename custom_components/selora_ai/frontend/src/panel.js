import { LitElement, html } from "lit";
import { seloraTokens } from "./shared/design-tokens.css.js";
import { sharedAnimations } from "./shared/styles/animations.css.js";
import { sharedButtons } from "./shared/styles/buttons.css.js";
import { sharedModals } from "./shared/styles/modals.css.js";
import { sharedBadges } from "./shared/styles/badges.css.js";
import { sharedLoaders } from "./shared/styles/loaders.css.js";
import { sharedScrollbar } from "./shared/styles/scrollbar.css.js";
import { allPanelStyles } from "./panel/styles/index.css.js";
import "./shared/particles.js";
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
import { renderSceneCard, renderScenes } from "./panel/render-scenes.js";
import { renderSuggestionsSection } from "./panel/render-suggestions.js";
import { renderSettings } from "./panel/render-settings.js";
import { renderUsage, loadUsageStats } from "./panel/render-usage.js";
import {
  renderVersionHistoryDrawer,
  renderDiffViewer,
} from "./panel/render-version-history.js";
import * as sessionActions from "./panel/session-actions.js";
import * as suggestionActions from "./panel/suggestion-actions.js";
import * as chatActions from "./panel/chat-actions.js";
import * as automationCrud from "./panel/automation-crud.js";
import * as automationManagement from "./panel/automation-management.js";
import * as sceneActions from "./panel/scene-actions.js";

// ---------------------------------------------------------------------------
// Pure JS SHA-256 (RFC 6234) — fallback when crypto.subtle is unavailable
// (HA panels served over HTTP lack SubtleCrypto / secure context).
// ---------------------------------------------------------------------------
const _SHA256_K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
  0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
  0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
  0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
  0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
  0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
  0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

function _sha256(msgBytes) {
  const rotr = (x, n) => (x >>> n) | (x << (32 - n));
  const len = msgBytes.length;
  const bitLen = len * 8;
  const blocks = Math.ceil((len + 9) / 64);
  const padded = new Uint8Array(blocks * 64);
  padded.set(msgBytes);
  padded[len] = 0x80;
  const dv = new DataView(padded.buffer);
  dv.setUint32(padded.length - 4, bitLen, false);
  let [h0, h1, h2, h3, h4, h5, h6, h7] = [
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c,
    0x1f83d9ab, 0x5be0cd19,
  ];
  const w = new Uint32Array(64);
  for (let i = 0; i < padded.length; i += 64) {
    for (let t = 0; t < 16; t++) w[t] = dv.getUint32(i + t * 4, false);
    for (let t = 16; t < 64; t++) {
      const s0 = rotr(w[t - 15], 7) ^ rotr(w[t - 15], 18) ^ (w[t - 15] >>> 3);
      const s1 = rotr(w[t - 2], 17) ^ rotr(w[t - 2], 19) ^ (w[t - 2] >>> 10);
      w[t] = (w[t - 16] + s0 + w[t - 7] + s1) | 0;
    }
    let [a, b, c, d, e, f, g, h] = [h0, h1, h2, h3, h4, h5, h6, h7];
    for (let t = 0; t < 64; t++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (h + S1 + ch + _SHA256_K[t] + w[t]) | 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) | 0;
      h = g;
      g = f;
      f = e;
      e = (d + t1) | 0;
      d = c;
      c = b;
      b = a;
      a = (t1 + t2) | 0;
    }
    h0 = (h0 + a) | 0;
    h1 = (h1 + b) | 0;
    h2 = (h2 + c) | 0;
    h3 = (h3 + d) | 0;
    h4 = (h4 + e) | 0;
    h5 = (h5 + f) | 0;
    h6 = (h6 + g) | 0;
    h7 = (h7 + h) | 0;
  }
  const out = new Uint8Array(32);
  const ov = new DataView(out.buffer);
  [h0, h1, h2, h3, h4, h5, h6, h7].forEach((v, i) =>
    ov.setUint32(i * 4, v, false),
  );
  return out;
}

// ---------------------------------------------------------------------------
// Selora AI Architect Panel
// ---------------------------------------------------------------------------
// Layout: two-pane when wide, single-pane when narrow.
//   Left pane  — session list + "New Chat" button
//   Right pane — active session (messages + input)
//
// Tabs within right pane: Chat | Automations | Settings
// ---------------------------------------------------------------------------

class SeloraAIPanel extends LitElement {
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
      _savingLlmConfig: { type: Boolean },
      _savingAdvancedConfig: { type: Boolean },
      _llmSaveStatus: { type: Object },
      _showApiKeyInput: { type: Boolean },
      _newApiKey: { type: String },

      // Usage tab (linked from Settings → LLM Provider)
      _usageStats: { type: Object },
      _usageRecent: { type: Array },
      _pricingDefaults: { type: Object },
      _pricingEdit: { type: Object },

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

      // Stale automations modal
      _staleModalOpen: { type: Boolean },
      _staleSelected: { type: Object },
      _staleDetailAuto: { type: Object },
      _staleBulkDeleting: { type: Boolean },

      // Feedback modal
      _showFeedbackModal: { type: Boolean },
      _feedbackText: { type: String },
      _feedbackRating: { type: String },
      _feedbackCategory: { type: String },
      _feedbackEmail: { type: String },
      _submittingFeedback: { type: Boolean },

      // MCP tokens
      _mcpTokens: { type: Array },
      _showCreateTokenDialog: { type: Boolean },
      _newTokenName: { type: String },
      _newTokenPermission: { type: String },
      _newTokenTools: { type: Object },
      _newTokenExpiry: { type: String },
      _createdToken: { type: String },
      _creatingToken: { type: Boolean },
      _revokingTokenId: { type: String },

      // Device detail drawer
      _deviceDetail: { type: Object },
      _deviceDetailLoading: { type: Boolean },

      // Scenes tab
      _scenes: { type: Array },
      _sceneFilter: { type: String },
      _sceneSortBy: { type: String },
      _expandedScenes: { type: Object },
      _sceneYamlOpen: { type: Object },
      _openSceneBurger: { type: String },
      _deletingScene: { type: Object },
      _deleteSceneConfirmId: { type: String },
      _deleteSceneConfirmName: { type: String },

      // Theme
      _isDark: { type: Boolean },
      _primaryColor: { type: String },

      // Overflow menu
      _showOverflowMenu: { type: Boolean },
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
    this._savingLlmConfig = false;
    this._savingAdvancedConfig = false;
    this._llmSaveStatus = null;
    this._showApiKeyInput = false;
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
    this._staleModalOpen = false;
    this._staleSelected = {};
    this._staleDetailAuto = null;
    this._staleBulkDeleting = false;
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
    // MCP tokens
    this._mcpTokens = [];
    this._showCreateTokenDialog = false;
    this._newTokenName = "";
    this._newTokenPermission = "read_only";
    this._newTokenTools = {};
    this._newTokenExpiry = "";
    this._createdToken = "";
    this._creatingToken = false;
    this._revokingTokenId = null;
    // Device detail drawer
    this._deviceDetail = null;
    this._deviceDetailLoading = false;
    // Scenes tab
    this._scenes = [];
    this._sceneFilter = "";
    this._sceneSortBy = "recent";
    this._expandedScenes = {};
    this._sceneYamlOpen = {};
    this._openSceneBurger = null;
    this._deletingScene = {};
    this._deleteSceneConfirmId = null;
    this._deleteSceneConfirmName = null;
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
    this._loadScenes();
    this._loadConfig();
    this._loadMcpTokens();
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
    this._closeOverflowHandler = () => {
      if (this._showOverflowMenu) this._showOverflowMenu = false;
    };
    document.addEventListener("click", this._closeOverflowHandler);

    // Mobile keyboard: use visualViewport to keep the chat input visible
    this._keyboardOpen = false;
    if (window.visualViewport) {
      this._viewportHandler = () => {
        if (!this.isConnected) return;
        const vp = window.visualViewport;
        const keyboardHeight = window.innerHeight - vp.height;
        const host = this.shadowRoot?.host;
        if (!host) return;
        const isOpen = keyboardHeight > 80;
        if (isOpen) {
          host.style.height = `${vp.height}px`;
          host.style.position = "fixed";
          host.style.top = `${vp.offsetTop}px`;
          host.style.left = "0";
          host.style.right = "0";
        } else {
          host.style.height = "";
          host.style.position = "";
          host.style.top = "";
          host.style.left = "";
          host.style.right = "";
        }
        if (isOpen !== this._keyboardOpen) {
          this._keyboardOpen = isOpen;
          this._requestScrollChat();
        }
      };
      window.visualViewport.addEventListener("resize", this._viewportHandler);
      window.visualViewport.addEventListener("scroll", this._viewportHandler);
    }
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._locationHandler) {
      window.removeEventListener("location-changed", this._locationHandler);
    }
    if (this._closeOverflowHandler) {
      document.removeEventListener("click", this._closeOverflowHandler);
    }
    if (this._keyDownHandler) {
      window.removeEventListener("keydown", this._keyDownHandler);
      this._keyDownHandler = null;
    }
    const vpHandler = this._viewportHandler;
    this._viewportHandler = null;
    if (vpHandler && window.visualViewport) {
      window.visualViewport.removeEventListener("resize", vpHandler);
      window.visualViewport.removeEventListener("scroll", vpHandler);
      const host = this.shadowRoot?.host;
      if (host) {
        host.style.height = "";
        host.style.position = "";
        host.style.top = "";
        host.style.left = "";
        host.style.right = "";
      }
    }
    if (this._oauthPollTimer) {
      clearInterval(this._oauthPollTimer);
      this._oauthPollTimer = null;
    }
    // Tear down an in-flight chat stream. Use the same cleanup path as
    // the manual stop button so streaming/loading flags and the last
    // message's _streaming marker are cleared — otherwise a
    // detach/reattach of the same instance would leave the UI stuck in
    // a loading state with no subscription left to receive done/error.
    if (this._streamUnsub) {
      try {
        this._stopStreaming();
      } catch (_e) {
        // Already detached or websocket gone — flags must still be reset
        // so a reattach starts clean.
        this._streamUnsub = null;
        this._streaming = false;
        this._loading = false;
        const lastMsg = this._messages[this._messages.length - 1];
        if (lastMsg && lastMsg._streaming) {
          lastMsg._streaming = false;
        }
      }
    }
    // Clear pending toast timer so setTimeout doesn't fire requestUpdate()
    // on a detached element.
    if (this._toastTimer) {
      clearTimeout(this._toastTimer);
      this._toastTimer = null;
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

  async _saveLlmConfig() {
    if (!this._config || this._savingLlmConfig) return;
    this._savingLlmConfig = true;
    this._llmSaveStatus = null;
    try {
      const provider = this._config.llm_provider;
      const newKey = this._newApiKey.trim();

      // Build LLM-only payload
      const payload = { llm_provider: provider };
      if (provider === "anthropic") {
        payload.anthropic_model = this._config.anthropic_model;
        if (newKey) payload.anthropic_api_key = newKey;
      } else if (provider === "gemini") {
        payload.gemini_model = this._config.gemini_model;
        if (newKey) payload.gemini_api_key = newKey;
      } else if (provider === "openai") {
        payload.openai_model = this._config.openai_model;
        if (newKey) payload.openai_api_key = newKey;
      } else if (provider === "openrouter") {
        payload.openrouter_model = this._config.openrouter_model;
        if (newKey) payload.openrouter_api_key = newKey;
      } else {
        payload.ollama_host = this._config.ollama_host;
        payload.ollama_model = this._config.ollama_model;
      }

      // Validate if a new key was entered or for Ollama (always validate connectivity)
      const needsValidation = newKey || provider === "ollama";
      if (needsValidation) {
        const validatePayload = {
          type: "selora_ai/validate_llm_key",
          provider,
        };
        if (provider === "ollama") {
          validatePayload.host = this._config.ollama_host;
          validatePayload.model = this._config.ollama_model;
        } else {
          validatePayload.api_key = newKey;
          validatePayload.model = this._config[`${provider}_model`];
        }
        const result = await this.hass.callWS(validatePayload);
        if (!result.valid) {
          this._llmSaveStatus = {
            type: "error",
            message: result.error || "Invalid API key or provider unreachable.",
          };
          return;
        }
      }

      await this.hass.callWS({
        type: "selora_ai/update_config",
        config: payload,
      });
      this._newApiKey = "";
      this._showApiKeyInput = false;
      await this._loadConfig();
      this._llmSaveStatus = { type: "success", message: "LLM settings saved." };
      setTimeout(() => {
        this._llmSaveStatus = null;
        this.requestUpdate();
      }, 4000);
    } catch (err) {
      this._llmSaveStatus = {
        type: "error",
        message: "Failed to save: " + err.message,
      };
    } finally {
      this._savingLlmConfig = false;
    }
  }

  async _saveAdvancedConfig() {
    if (!this._config || this._savingAdvancedConfig) return;
    this._savingAdvancedConfig = true;
    try {
      const payload = {
        collector_enabled: this._config.collector_enabled,
        collector_mode: this._config.collector_mode,
        collector_interval: this._config.collector_interval,
        collector_start_time: this._config.collector_start_time,
        collector_end_time: this._config.collector_end_time,
        discovery_enabled: this._config.discovery_enabled,
        discovery_mode: this._config.discovery_mode,
        discovery_interval: this._config.discovery_interval,
        discovery_start_time: this._config.discovery_start_time,
        discovery_end_time: this._config.discovery_end_time,
        auto_purge_stale: this._config.auto_purge_stale || false,
        // Developer-only: Connect Server URL (editable when Connect is unlinked)
        selora_connect_url: this._config.selora_connect_url,
      };
      await this.hass.callWS({
        type: "selora_ai/update_config",
        config: payload,
      });
      await this._loadConfig();
      this._showToast("Advanced settings saved.", "success");
    } catch (err) {
      this._showToast("Failed to save: " + err.message, "error");
    } finally {
      this._savingAdvancedConfig = false;
    }
  }

  _goToSettings() {
    this._activeTab = "settings";
    this._loadConfig();
    this._loadMcpTokens();
  }

  get _llmNeedsSetup() {
    if (!this._config) return false;
    const provider = this._config.llm_provider;
    if (!provider) return true; // No provider selected at all
    if (provider === "anthropic") return !this._config.anthropic_api_key_set;
    if (provider === "gemini") return !this._config.gemini_api_key_set;
    if (provider === "openai") return !this._config.openai_api_key_set;
    if (provider === "openrouter") return !this._config.openrouter_api_key_set;
    return false; // Ollama doesn't require an API key
  }

  _updateConfig(key, value) {
    this._config = { ...this._config, [key]: value };
    this.requestUpdate();
  }

  // ── OAuth PKCE helpers ──────────────────────────────────────────────

  _generateRandomString(length) {
    const chars =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~";
    const limit = 256 - (256 % chars.length);
    const result = [];
    while (result.length < length) {
      const arr = new Uint8Array(length - result.length);
      crypto.getRandomValues(arr);
      for (const b of arr) {
        if (b < limit && result.length < length) {
          result.push(chars[b % chars.length]);
        }
      }
    }
    return result.join("");
  }

  async _generateCodeChallenge(verifier) {
    const data = new TextEncoder().encode(verifier);
    let digest;
    if (typeof crypto !== "undefined" && crypto.subtle) {
      digest = new Uint8Array(await crypto.subtle.digest("SHA-256", data));
    } else {
      // HTTP context — crypto.subtle unavailable, use pure JS SHA-256
      digest = _sha256(data);
    }
    return btoa(String.fromCharCode(...digest))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
  }

  // ── OAuth Link flow ───────────────────────────────────────────────

  async _startOAuthLink() {
    if (this._linkingConnect) return;
    this._linkingConnect = true;
    this._connectError = "";
    this.requestUpdate();

    // Open popup synchronously in the user-gesture call stack to avoid
    // browser popup blockers (the async PKCE hash would break the chain).
    const popup = window.open(
      "about:blank",
      "selora_connect_oauth",
      "width=500,height=700,menubar=no,toolbar=no",
    );
    if (!popup) {
      this._connectError = "Popup blocked. Please allow popups for this site.";
      this._linkingConnect = false;
      this.requestUpdate();
      return;
    }

    try {
      const connectUrl = (
        this._config.selora_connect_url || "https://connect.selorahomes.com"
      ).replace(/\/+$/, "");

      // PKCE: generate code_verifier and S256 challenge
      const codeVerifier = this._generateRandomString(64);
      const codeChallenge = await this._generateCodeChallenge(codeVerifier);
      const state = this._generateRandomString(32);

      // redirect_uri = client_id per MCP spec
      // Use the current panel URL to handle reverse proxy path prefixes
      const redirectUri = `${location.origin}${location.pathname}`;

      // Build authorize URL
      const params = new URLSearchParams({
        response_type: "code",
        client_id: redirectUri,
        redirect_uri: redirectUri,
        code_challenge: codeChallenge,
        code_challenge_method: "S256",
        state,
        scope: "mcp:provision",
        device_name: this.hass?.config?.location_name || "Home Assistant",
      });

      // Navigate the already-open popup to the authorize URL
      popup.location.href = `${connectUrl}/oauth/authorize?${params}`;

      // Store PKCE state for callback
      this._oauthState = { codeVerifier, state, connectUrl, redirectUri };

      // Poll for popup redirect back to our origin
      let polling = false;
      this._oauthPollTimer = setInterval(async () => {
        if (polling) return; // previous tick still in-flight
        polling = true;
        try {
          if (popup.closed) {
            clearInterval(this._oauthPollTimer);
            this._oauthPollTimer = null;
            if (this._linkingConnect) {
              this._linkingConnect = false;
              this._connectError = "Authorization cancelled.";
              this.requestUpdate();
            }
            return;
          }
          // Check if popup navigated back to our redirect_uri
          const popupUrl = popup.location.href;
          if (!popupUrl.startsWith(this._oauthState.redirectUri)) return;

          // Parse the auth code from the URL
          clearInterval(this._oauthPollTimer);
          this._oauthPollTimer = null;
          popup.close();

          const callbackParams = new URLSearchParams(new URL(popupUrl).search);
          const code = callbackParams.get("code");
          const returnedState = callbackParams.get("state");
          const error = callbackParams.get("error");

          if (error) {
            this._connectError = `Authorization failed: ${error}`;
            this._linkingConnect = false;
            this.requestUpdate();
            return;
          }

          if (!code || returnedState !== this._oauthState.state) {
            this._connectError = "Invalid authorization response.";
            this._linkingConnect = false;
            this.requestUpdate();
            return;
          }

          // Exchange code for credentials via backend
          await this.hass.callWS({
            type: "selora_ai/exchange_connect_code",
            code,
            code_verifier: this._oauthState.codeVerifier,
            redirect_uri: this._oauthState.redirectUri,
            connect_url: this._oauthState.connectUrl,
          });

          this._oauthState = null;
          await this._loadConfig();
          this._showToast("Selora Connect linked successfully.", "success");
        } catch (err) {
          // Cross-origin access to popup.location throws — that's expected
          // while the popup is still on Connect's domain. Ignore silently.
          if (err.name !== "SecurityError" && err.name !== "DOMException") {
            clearInterval(this._oauthPollTimer);
            this._oauthPollTimer = null;
            popup.close();
            this._connectError = err.message || "Failed to link.";
          }
        } finally {
          polling = false;
          if (!this._oauthPollTimer) {
            this._linkingConnect = false;
            this.requestUpdate();
          }
        }
      }, 500);
    } catch (err) {
      this._connectError = err.message || "Failed to start OAuth flow.";
      this._linkingConnect = false;
      this.requestUpdate();
    }
  }

  async _unlinkConnect() {
    try {
      await this.hass.callWS({ type: "selora_ai/unlink_connect" });
      await this._loadConfig();
      this._showToast("Selora Connect unlinked.", "success");
    } catch (err) {
      this._showToast("Failed to unlink: " + err.message, "error");
    }
  }

  // -------------------------------------------------------------------------
  // MCP Token Management
  // -------------------------------------------------------------------------

  async _loadMcpTokens() {
    try {
      const result = await this.hass.callWS({
        type: "selora_ai/list_mcp_tokens",
      });
      this._mcpTokens = result.tokens || [];
    } catch (err) {
      console.error("Failed to load MCP tokens", err);
    }
  }

  async _createMcpToken() {
    if (this._creatingToken) return;
    this._creatingToken = true;
    this.requestUpdate();
    try {
      const payload = {
        type: "selora_ai/create_mcp_token",
        name: this._newTokenName,
        permission_level: this._newTokenPermission,
      };
      if (this._newTokenPermission === "custom") {
        payload.allowed_tools = Object.keys(this._newTokenTools).filter(
          (t) => this._newTokenTools[t],
        );
      }
      if (this._newTokenExpiry) {
        payload.expires_in_days = parseInt(this._newTokenExpiry, 10);
      }
      const result = await this.hass.callWS(payload);
      this._createdToken = result.token;
      await this._loadMcpTokens();
      this._showToast("MCP token created.", "success");
    } catch (err) {
      this._showToast("Failed to create token: " + err.message, "error");
      this._showCreateTokenDialog = false;
    } finally {
      this._creatingToken = false;
      this.requestUpdate();
    }
  }

  async _revokeMcpToken(tokenId) {
    this._revokingTokenId = tokenId;
    this.requestUpdate();
    try {
      await this.hass.callWS({
        type: "selora_ai/revoke_mcp_token",
        token_id: tokenId,
      });
      await this._loadMcpTokens();
      this._showToast("Token revoked.", "success");
    } catch (err) {
      this._showToast("Failed to revoke token: " + err.message, "error");
    } finally {
      this._revokingTokenId = null;
      this.requestUpdate();
    }
  }

  _openCreateTokenDialog() {
    this._newTokenName = "";
    this._newTokenPermission = "read_only";
    this._newTokenTools = {};
    this._newTokenExpiry = "";
    this._createdToken = "";
    this._showCreateTokenDialog = true;
    this.requestUpdate();
  }

  _closeCreateTokenDialog() {
    this._showCreateTokenDialog = false;
    this._createdToken = "";
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
      // Track dark mode for conditional rendering (gold branding only in dark)
      const dark = this.hass?.themes?.darkMode;
      if (dark !== undefined) {
        this._isDark = dark;
        this.toggleAttribute("dark", dark);
      }
      // Resolve HA's primary color so canvas-based effects (particles) can
      // match the theme accent rather than hard-coding gold in light mode.
      const probe = document.createElement("div");
      probe.style.color = "var(--primary-color)";
      probe.style.display = "none";
      this.shadowRoot?.appendChild(probe);
      const resolved = getComputedStyle(probe).color;
      probe.remove();
      const m = resolved.match(/\d+/g);
      if (m && m.length >= 3) {
        this._primaryColor =
          "#" +
          [m[0], m[1], m[2]]
            .map((v) => parseInt(v, 10).toString(16).padStart(2, "0"))
            .join("");
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

  _renderSceneCard(msg, msgIndex) {
    return renderSceneCard(this, msg, msgIndex);
  }

  async _activateScene(sceneId, sceneName) {
    if (!sceneId) return;
    try {
      await this.hass.callService("scene", "turn_on", {
        entity_id: `scene.${sceneId}`,
      });
      this._showToast(`Scene "${sceneName || sceneId}" activated.`, "success");
    } catch (err) {
      this._showToast("Failed to activate scene: " + err.message, "error");
    }
  }

  _renderScenes() {
    return renderScenes(this);
  }

  async _loadScenes() {
    try {
      const result = await this.hass.callWS({
        type: "selora_ai/get_scenes",
      });
      this._scenes = result?.scenes || [];
    } catch (err) {
      console.error("Failed to load scenes", err);
      this._scenes = [];
    }
  }

  async _refineSceneInChat(scene) {
    if (!scene) return;
    const sessionId = scene.session_id;
    const known = sessionId
      ? this._sessions.find((s) => s.id === sessionId)
      : null;
    try {
      if (known) {
        await this._openSession(sessionId);
      } else {
        await this._newSession();
      }
    } catch (err) {
      console.error("Failed to switch session for scene refine", err);
    }
    const ctx = known ? "" : ` (scene_id: ${scene.scene_id})`;
    this._input = `Refine "${scene.name}"${ctx}: `;
    this._activeTab = "chat";
    this.requestUpdate();
    await this.updateComplete;
    const textarea = this.shadowRoot?.querySelector(".composer-textarea");
    if (textarea) textarea.focus();
  }

  async _confirmDeleteScene() {
    const sceneId = this._deleteSceneConfirmId;
    const name = this._deleteSceneConfirmName;
    if (!sceneId) return;
    this._deleteSceneConfirmId = null;
    this._deleteSceneConfirmName = null;
    this._deletingScene = { ...this._deletingScene, [sceneId]: true };
    try {
      await this.hass.callWS({
        type: "selora_ai/delete_scene",
        scene_id: sceneId,
      });
      this._showToast(`Scene "${name || sceneId}" deleted.`, "success");
      await this._loadScenes();
    } catch (err) {
      this._showToast("Failed to delete scene: " + err.message, "error");
    } finally {
      this._deletingScene = { ...this._deletingScene, [sceneId]: false };
    }
  }

  async _newSceneChat() {
    try {
      const { session_id } = await this.hass.callWS({
        type: "selora_ai/new_session",
      });
      this._activeSessionId = session_id;
      this._messages = [];
      this._input = "Create a scene that ";
      this._activeTab = "chat";
      this._welcomeKey = (this._welcomeKey || 0) + 1;
      await this._loadSessions();
      if (this.narrow) this._showSidebar = false;
      this.requestUpdate();
      await this.updateComplete;
      const textarea = this.shadowRoot?.querySelector(".composer-textarea");
      if (textarea) {
        textarea.focus();
        const len = this._input.length;
        textarea.setSelectionRange(len, len);
      }
    } catch (err) {
      console.error("Failed to start new scene chat", err);
      this._showToast("Failed to start new chat: " + err.message, "error");
    }
  }

  async _openDeviceDetail(deviceId) {
    if (!deviceId || !this.hass) return;
    this._deviceDetail = { name: "Loading..." };
    this._deviceDetailLoading = true;
    try {
      const result = await this.hass.connection.sendMessagePromise({
        type: "selora_ai/get_device_detail",
        device_id: deviceId,
      });
      this._deviceDetail = result;
    } catch (err) {
      this._deviceDetail = { name: "Error loading device", error: err.message };
    }
    this._deviceDetailLoading = false;
    await this.updateComplete;
    const detail = this.shadowRoot?.querySelector(".device-detail-drawer");
    if (detail) detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
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

  _renderUsage() {
    return renderUsage(this);
  }

  async _loadUsageStats() {
    await loadUsageStats(this);
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
        <div class="header-toolbar">
          ${this.narrow
            ? html`<button
                class="menu-btn"
                @click=${() =>
                  this.dispatchEvent(
                    new Event("hass-toggle-menu", {
                      bubbles: true,
                      composed: true,
                    }),
                  )}
              >
                <ha-icon icon="mdi:menu"></ha-icon>
              </button>`
            : ""}
          <span
            class="header-title ${this._isDark ? "gold-text" : ""}"
            @click=${() => {
              this._activeTab = "chat";
              if (this._messages.length > 0) this._newSession();
            }}
            style="cursor:pointer;"
            >Selora AI</span
          >
          <img
            src="/api/selora_ai/${this._isDark ? "logo" : "logo-light"}.png"
            alt=""
            class="header-logo"
            @click=${() => {
              this._activeTab = "chat";
              if (this._messages.length > 0) this._newSession();
            }}
            style="cursor:pointer;"
          />
          <div class="tabs-center">
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
                >Chat</span
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
                >Automations</span
              >
            </div>
            <div
              class="tab ${this._activeTab === "scenes" ? "active" : ""}"
              @click=${() => {
                this._activeTab = "scenes";
                this._showSidebar = false;
                this._loadScenes();
              }}
            >
              <span class="tab-inner"
                ><ha-icon icon="mdi:palette-outline" class="tab-icon"></ha-icon
                >Scenes</span
              >
            </div>
          </div>
          <span class="header-spacer"></span>
          <div class="overflow-btn-wrap">
            <button
              class="overflow-btn"
              @click=${(e) => {
                e.stopPropagation();
                this._showOverflowMenu = !this._showOverflowMenu;
              }}
            >
              <ha-icon icon="mdi:dots-vertical"></ha-icon>
            </button>
            ${this._showOverflowMenu
              ? html`
                  <div class="overflow-menu">
                    <button
                      class="overflow-item"
                      @click=${() => {
                        this._showOverflowMenu = false;
                        this._activeTab = "settings";
                        this._showSidebar = false;
                        this._loadConfig();
                      }}
                    >
                      <ha-icon icon="mdi:cog-outline"></ha-icon>
                      Settings
                    </button>
                    <div class="overflow-divider"></div>
                    <a
                      class="overflow-item"
                      href="https://selorahomes.com/docs/selora-ai/"
                      target="_blank"
                      rel="noopener noreferrer"
                      @click=${() => {
                        this._showOverflowMenu = false;
                      }}
                    >
                      <ha-icon icon="mdi:book-open-variant"></ha-icon>
                      Documentation
                    </a>
                    <button
                      class="overflow-item"
                      @click=${() => {
                        this._showOverflowMenu = false;
                        this._openFeedback();
                      }}
                    >
                      <ha-icon icon="mdi:message-alert-outline"></ha-icon>
                      Give Feedback
                    </button>
                    <a
                      class="overflow-item"
                      href="https://github.com/SeloraHomes/ha-selora-ai/issues"
                      target="_blank"
                      rel="noopener noreferrer"
                      @click=${() => {
                        this._showOverflowMenu = false;
                      }}
                    >
                      <ha-icon icon="mdi:github"></ha-icon>
                      GitHub Issues
                    </a>
                    <a
                      class="overflow-item"
                      href="https://gitlab.com/selorahomes/products/selora-ai/ha-integration/"
                      target="_blank"
                      rel="noopener noreferrer"
                      @click=${() => {
                        this._showOverflowMenu = false;
                      }}
                    >
                      <ha-icon icon="mdi:gitlab"></ha-icon>
                      GitLab Repository
                    </a>
                  </div>
                `
              : ""}
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
          <selora-particles
            .count=${this._isDark ? 1200 : 400}
            .color=${this._isDark ? "#C7AE6A" : this._primaryColor || "#03a9f4"}
            .maxOpacity=${this._isDark ? 1.0 : 0.5}
          ></selora-particles>
          ${this._activeTab === "chat" ? this._renderChat() : ""}
          ${this._activeTab === "automations" ? this._renderAutomations() : ""}
          ${this._activeTab === "scenes" ? this._renderScenes() : ""}
          ${this._activeTab === "settings" ? this._renderSettings() : ""}
          ${this._activeTab === "usage" ? this._renderUsage() : ""}
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
Object.assign(SeloraAIPanel.prototype, sessionActions);
Object.assign(SeloraAIPanel.prototype, suggestionActions);
Object.assign(SeloraAIPanel.prototype, chatActions);
Object.assign(SeloraAIPanel.prototype, automationCrud);
Object.assign(SeloraAIPanel.prototype, automationManagement);
Object.assign(SeloraAIPanel.prototype, sceneActions);

customElements.define("selora-ai", SeloraAIPanel);
