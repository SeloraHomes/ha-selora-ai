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
// Self-heal HA's <ha-panel-custom> when navigating back to this panel.
//
// HA's panel-resolver occasionally re-mounts <ha-panel-custom> with no
// child element after a view-transition (e.g. tab returning from background).
// The result is an empty wrapper and a black panel area until the user
// hard-reloads. We install ONE MutationObserver, ONCE per page, on
// partial-panel-resolver's direct children — no subtree, no per-instance
// state, no accumulating refs. If we see an empty <ha-panel-custom> whose
// config points at our element, we create <selora-ai> with the right
// hass/panel/narrow/route props and append it. Idempotent across reloads
// of this module thanks to the window-scoped guard.
// ---------------------------------------------------------------------------
(() => {
  const PANEL_NAME = "selora-ai";
  const GUARD = "__seloraAiPanelMountGuard";
  if (window[GUARD]) return;
  window[GUARD] = true;

  const fix = (panelCustom) => {
    if (!panelCustom || panelCustom.tagName !== "HA-PANEL-CUSTOM") return;
    const cfg = panelCustom.panel?.config?._panel_custom;
    if (!cfg || cfg.name !== PANEL_NAME) return;
    if (panelCustom.querySelector(PANEL_NAME)) return;
    // HA's _loadElement is async (awaits import + microtasks). Wait long
    // enough for the happy path to finish, then only inject if it didn't.
    // 400ms is generous on every device we care about.
    setTimeout(() => {
      if (!panelCustom.isConnected) return;
      if (panelCustom.querySelector(PANEL_NAME)) return;
      const el = document.createElement(PANEL_NAME);
      el.hass = panelCustom.hass;
      el.narrow = panelCustom.narrow;
      el.route = panelCustom.route;
      el.panel = panelCustom.panel;
      panelCustom.appendChild(el);
    }, 400);
  };

  let attempts = 0;
  const start = () => {
    const ha = document.querySelector("home-assistant");
    const main = ha?.shadowRoot?.querySelector("home-assistant-main");
    const resolver =
      main?.shadowRoot?.querySelector("partial-panel-resolver") ||
      main?.querySelector("partial-panel-resolver");
    if (!resolver) {
      if (++attempts < 30) setTimeout(start, 500);
      return;
    }
    for (const pc of resolver.querySelectorAll("ha-panel-custom")) fix(pc);
    new MutationObserver((muts) => {
      for (const m of muts) {
        for (const n of m.addedNodes) if (n.nodeType === 1) fix(n);
      }
    }).observe(resolver, { childList: true });
  };
  start();
})();

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
  // HA's recent panel resolver wraps each panel in a scoped custom-element
  // registry (via @webcomponents/scoped-custom-element-registry). With the
  // default attachShadow options, our shadow root gets a fresh per-panel
  // registry that doesn't see globally-registered HA components, so
  // <ha-textfield>, <ha-switch>, etc. silently fail to upgrade — the
  // textfield renders as an empty unknown element (invisible) and the
  // switch falls back to undecorated mwc-switch (HA-default blue, ignoring
  // our --switch-checked-color overrides). Pass customElements explicitly
  // so attachShadow uses the global registry. Lit reads this static for
  // its default createRenderRoot, which keeps style adoption intact.
  static shadowRootOptions = {
    ...LitElement.shadowRootOptions,
    customElements: window.customElements,
  };

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
    // Quota / 429 alert state. Populated by the selora_ai_quota_exceeded
    // event subscription in connectedCallback. Auto-clears when the
    // backend's retry_after window elapses.
    this._quotaAlert = null;
    this._quotaUnsub = null;
    this._quotaSubPending = false;
    this._quotaClearTimer = null;
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
        return;
      }
      if (
        e.key === "Escape" &&
        this._activeTab === "chat" &&
        (this._streaming || this._loading)
      ) {
        e.preventDefault();
        this._stopStreaming();
      }
    };
    window.addEventListener("keydown", this._keyDownHandler);
    this._closeOverflowHandler = () => {
      if (this._showOverflowMenu) this._showOverflowMenu = false;
    };
    document.addEventListener("click", this._closeOverflowHandler);

    // Mobile keyboard: use visualViewport to keep the chat input visible.
    // The pinning logic only runs when an input/textarea inside the panel is
    // focused — otherwise transient viewport changes (tab returning from
    // background, address-bar show/hide, HA view-transitions) could leave the
    // host stuck at `position: fixed` with bogus dimensions, causing a black
    // panel area on return.
    this._keyboardOpen = false;
    this._resetHostKeyboardStyles = () => {
      const host = this.shadowRoot?.host;
      if (!host) return;
      host.style.height = "";
      host.style.position = "";
      host.style.top = "";
      host.style.left = "";
      host.style.right = "";
      this._keyboardOpen = false;
    };
    if (window.visualViewport) {
      this._viewportHandler = () => {
        if (!this.isConnected || document.hidden) return;
        const host = this.shadowRoot?.host;
        if (!host) return;
        // Only treat shrinking viewport as a keyboard if a text input inside
        // the panel currently has focus.
        const active = this.shadowRoot?.activeElement;
        const editing =
          active &&
          (active.tagName === "INPUT" || active.tagName === "TEXTAREA");
        // Desktop Chromium (notably Windows) fires visualViewport `scroll`
        // on every wheel tick. With nothing focused we have no business
        // touching host layout — skip entirely so we don't reflow during
        // user scrolling. We still need to fall through when `_keyboardOpen`
        // is true so we can reset styles when the input loses focus.
        if (!editing && !this._keyboardOpen) return;
        const vp = window.visualViewport;
        const keyboardHeight = window.innerHeight - vp.height;
        const isOpen = !!editing && keyboardHeight > 80;
        if (isOpen) {
          host.style.height = `${vp.height}px`;
          host.style.position = "fixed";
          host.style.top = `${vp.offsetTop}px`;
          host.style.left = "0";
          host.style.right = "0";
        } else {
          this._resetHostKeyboardStyles();
        }
        if (isOpen !== this._keyboardOpen) {
          this._keyboardOpen = isOpen;
          // Only scroll on keyboard *open* — the latest message would
          // otherwise be hidden behind it. On close (e.g. textarea
          // blurred because the user tapped a tile to open a more-info
          // dialog), leave the scroll position alone.
          if (isOpen) {
            this._requestScrollChat();
          }
        }
      };
      window.visualViewport.addEventListener("resize", this._viewportHandler);
      window.visualViewport.addEventListener("scroll", this._viewportHandler);
    }
    // When the page becomes visible again (tab switch, mobile return from
    // background) or is restored from bfcache, force-reset host styles so a
    // stale "keyboard open" state can't leave the panel pinned off-screen.
    this._visibilityHandler = () => {
      if (!document.hidden) this._resetHostKeyboardStyles();
    };
    document.addEventListener("visibilitychange", this._visibilityHandler);
    this._pageShowHandler = () => this._resetHostKeyboardStyles();
    window.addEventListener("pageshow", this._pageShowHandler);
    // On reconnect HA may already have set `hass` before connectedCallback
    // fires (no further `hass` change in `updated()`), so we miss future
    // events unless we kick the subscription right here. The helper is
    // a no-op when `hass` isn't ready or a subscription is already in
    // flight, so it's safe to call from both lifecycle hooks.
    this._ensureQuotaSubscription();
    // If a quota alert was active when the panel detached, recompute
    // the remaining cool-down and re-arm the auto-dismiss timer (or
    // dismiss now if the window has already elapsed).
    this._reconcileQuotaAlertOnReconnect();
    // Listen for WebSocket reconnection so we can reload data and
    // re-establish subscriptions that the disconnect killed.
    this._wsReadyHandler = () => this._handleWsReconnect();
    this._wsReadyConn = null;
    this._attachWsReadyListener();
  }

  _attachWsReadyListener() {
    const conn = this.hass?.connection;
    if (!conn || conn === this._wsReadyConn) return;
    if (this._wsReadyConn) {
      this._wsReadyConn.removeEventListener("ready", this._wsReadyHandler);
    }
    this._wsReadyConn = conn;
    conn.addEventListener("ready", this._wsReadyHandler);
  }

  _handleWsReconnect() {
    // The WebSocket reconnected. Drop stale subscriptions so they get
    // re-established, then reload all panel data.
    if (this._quotaUnsub) {
      try {
        this._quotaUnsub();
      } catch (_e) {
        /* best-effort */
      }
      this._quotaUnsub = null;
    }
    this._quotaSubPending = false;
    this._ensureQuotaSubscription();
    this._loadSessions();
    this._loadAutomations();
    this._loadScenes();
    this._loadConfig();
    this._loadSuggestions();
  }

  _ensureQuotaSubscription() {
    if (this._quotaUnsub || this._quotaSubPending) return;
    if (!this.hass?.connection) return;
    this._quotaSubPending = true;
    this.hass.connection
      .subscribeEvents((evt) => {
        const data = evt?.data || {};
        // Preserve a valid 0 (provider says "retry now") — only fall
        // back to the default when the value is missing or non-numeric.
        const raw = Number(data.retry_after);
        const retryAfter = Number.isFinite(raw) && raw >= 0 ? raw : 60;
        this._setQuotaAlert({
          provider: data.provider || "unknown",
          model: data.model || "",
          retryAfter,
          message: data.message || "",
        });
      }, "selora_ai_quota_exceeded")
      .then((unsub) => {
        this._quotaUnsub = unsub;
        this._quotaSubPending = false;
        // If the panel was disconnected while the subscription was
        // pending, drop it immediately to avoid a leaked listener.
        if (!this.isConnected) {
          try {
            unsub();
          } catch (_e) {
            // best-effort
          }
          this._quotaUnsub = null;
        }
      })
      .catch((err) => {
        this._quotaSubPending = false;
        console.warn("Failed to subscribe to quota events", err);
      });
  }

  _setQuotaAlert(alert) {
    this._quotaAlert = {
      ...alert,
      until: Date.now() + alert.retryAfter * 1000,
    };
    if (this._quotaClearTimer) clearTimeout(this._quotaClearTimer);
    this._quotaClearTimer = setTimeout(() => {
      this._dismissQuotaAlert();
    }, alert.retryAfter * 1000);
    // Tick once per second so the countdown in the banner stays current.
    if (this._quotaTickTimer) clearInterval(this._quotaTickTimer);
    this._quotaTickTimer = setInterval(() => this.requestUpdate(), 1000);
    this.requestUpdate();
  }

  _dismissQuotaAlert() {
    this._quotaAlert = null;
    if (this._quotaClearTimer) {
      clearTimeout(this._quotaClearTimer);
      this._quotaClearTimer = null;
    }
    if (this._quotaTickTimer) {
      clearInterval(this._quotaTickTimer);
      this._quotaTickTimer = null;
    }
    this.requestUpdate();
  }

  _reconcileQuotaAlertOnReconnect() {
    // disconnectedCallback tears down the auto-dismiss + tick timers
    // but keeps `_quotaAlert` so the same instance reattaching mid-window
    // continues to show the banner. On reconnect, recompute how much
    // of the original retry_after is left and either dismiss the alert
    // (window elapsed while detached) or rearm both timers.
    if (!this._quotaAlert) return;
    const remainingMs = this._quotaAlert.until - Date.now();
    if (remainingMs <= 0) {
      this._dismissQuotaAlert();
      return;
    }
    if (this._quotaClearTimer) clearTimeout(this._quotaClearTimer);
    this._quotaClearTimer = setTimeout(
      () => this._dismissQuotaAlert(),
      remainingMs,
    );
    if (this._quotaTickTimer) clearInterval(this._quotaTickTimer);
    this._quotaTickTimer = setInterval(() => this.requestUpdate(), 1000);
    this.requestUpdate();
  }

  _quotaProviderLabel() {
    const p = this._quotaAlert?.provider;
    if (p === "selora_cloud") return "Selora Cloud";
    if (p === "anthropic") return "Anthropic";
    if (p === "openai") return "OpenAI";
    if (p === "openrouter") return "OpenRouter";
    if (p === "gemini") return "Gemini";
    if (p === "ollama") return "Ollama";
    return "your LLM provider";
  }

  _renderQuotaBanner() {
    if (!this._quotaAlert) return "";
    const remaining = Math.max(
      0,
      Math.ceil((this._quotaAlert.until - Date.now()) / 1000),
    );
    return html`
      <div class="quota-banner" role="alert">
        <ha-icon icon="mdi:speedometer-slow"></ha-icon>
        <div class="quota-banner-text">
          <strong>${this._quotaProviderLabel()} quota reached.</strong>
          ${remaining > 0
            ? html` Try again in ${remaining}s.`
            : " Retrying now…"}
        </div>
        <button
          class="quota-banner-close"
          aria-label="Dismiss"
          @click=${() => this._dismissQuotaAlert()}
        >
          <ha-icon icon="mdi:close"></ha-icon>
        </button>
      </div>
    `;
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
    }
    if (this._visibilityHandler) {
      document.removeEventListener("visibilitychange", this._visibilityHandler);
      this._visibilityHandler = null;
    }
    if (this._pageShowHandler) {
      window.removeEventListener("pageshow", this._pageShowHandler);
      this._pageShowHandler = null;
    }
    if (this._resetHostKeyboardStyles) {
      this._resetHostKeyboardStyles();
      this._resetHostKeyboardStyles = null;
    }
    if (this._oauthPollTimer) {
      clearInterval(this._oauthPollTimer);
      this._oauthPollTimer = null;
    }
    if (this._aigatewayPollTimer) {
      clearInterval(this._aigatewayPollTimer);
      this._aigatewayPollTimer = null;
    }
    if (this._wsReadyConn && this._wsReadyHandler) {
      this._wsReadyConn.removeEventListener("ready", this._wsReadyHandler);
      this._wsReadyConn = null;
      this._wsReadyHandler = null;
    }
    if (this._quotaUnsub) {
      try {
        this._quotaUnsub();
      } catch (_e) {
        // best-effort cleanup
      }
      this._quotaUnsub = null;
    }
    if (this._quotaClearTimer) {
      clearTimeout(this._quotaClearTimer);
      this._quotaClearTimer = null;
    }
    if (this._quotaTickTimer) {
      clearInterval(this._quotaTickTimer);
      this._quotaTickTimer = null;
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

      // Selora Cloud while NOT linked: linking is the activation step.
      // Once linked, Save still has a job — persisting a provider switch
      // back to selora_cloud after the user picked another provider in
      // the dropdown. Without this path, switching back is silent: the
      // dropdown updates _config locally but never reaches the backend,
      // so the integration keeps using the previous provider.
      if (provider === "selora_cloud") {
        if (!this._config.aigateway_linked) return;
        const seloraPayload = { llm_provider: "selora_cloud" };
        if (this._config.selora_connect_url) {
          seloraPayload.selora_connect_url = this._config.selora_connect_url;
        }
        await this.hass.callWS({
          type: "selora_ai/update_config",
          config: seloraPayload,
        });
        await this._loadConfig();
        this._llmSaveStatus = {
          type: "success",
          message: "Switched to Selora Cloud.",
        };
        setTimeout(() => {
          this._llmSaveStatus = null;
          this.requestUpdate();
        }, 4000);
        return;
      }

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
        pattern_detection_enabled:
          this._config.pattern_detection_enabled !== false,
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
    if (provider === "selora_cloud") return !this._config.aigateway_linked;
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
  //
  // HA-mediated linking: PKCE state lives on the backend; we ask HA to
  // start a session and hand us an authorize URL. The user clicks a
  // real `<a target="_blank">` rendered with that URL — programmatic
  // clicks after `await` lose the user-gesture context and get blocked
  // in browsers and Companion app alike, so the URL has to be on a
  // genuinely-clicked anchor. Two clicks total: "Link" prepares the
  // URL, the resulting anchor opens the system browser. HA's callback
  // view finishes the exchange and fires `selora_ai_oauth_linked`.

  _OAUTH_LINK_TIMEOUT_MS = 10 * 60 * 1000;

  _resetOAuthState(flow) {
    if (flow === "aigateway") {
      this._aigwAuthorizeUrl = "";
    } else {
      this._connectAuthorizeUrl = "";
    }
  }

  async _runOAuthLink({ flow, wsType, beforeStart, onSuccess, onError }) {
    let unsub = null;
    let timeout = null;
    const cleanup = () => {
      if (typeof unsub === "function") {
        try {
          unsub();
        } catch (_e) {
          /* best-effort */
        }
        unsub = null;
      }
      if (timeout) {
        clearTimeout(timeout);
        timeout = null;
      }
      this._resetOAuthState(flow);
    };

    try {
      if (typeof beforeStart === "function") await beforeStart();

      // Subscribe BEFORE returning the URL so a fast callback can't
      // race the panel.
      unsub = await this.hass.connection.subscribeEvents((evt) => {
        const data = evt.data || {};
        if (data.flow !== flow) return;
        cleanup();
        if (data.ok) {
          onSuccess();
        } else {
          onError(data.error || "Linking failed.");
        }
      }, "selora_ai_oauth_linked");

      const result = await this.hass.callWS({
        type: wsType,
        connect_url: this._config?.selora_connect_url || "",
      });
      const authorizeUrl = result?.authorize_url;
      if (!authorizeUrl) throw new Error("No authorize URL returned.");

      // Render the URL as a real anchor (see _resetOAuthState + the
      // settings template). The user clicks it; the Companion app
      // routes target=_blank to the system browser.
      if (flow === "aigateway") {
        this._aigwAuthorizeUrl = authorizeUrl;
      } else {
        this._connectAuthorizeUrl = authorizeUrl;
      }
      this.requestUpdate();

      timeout = setTimeout(() => {
        cleanup();
        onError(
          "Linking timed out. Please try again — make sure you finish " +
            "signing in within 10 minutes.",
        );
      }, this._OAUTH_LINK_TIMEOUT_MS);
    } catch (err) {
      cleanup();
      onError(err.message || "Failed to start linking.");
    }
  }

  async _startOAuthLink() {
    if (this._linkingConnect) return;
    this._linkingConnect = true;
    this._connectError = "";
    this.requestUpdate();
    await this._runOAuthLink({
      flow: "connect",
      wsType: "selora_ai/start_connect_link",
      onSuccess: async () => {
        await this._loadConfig();
        this._linkingConnect = false;
        this._showToast("Selora Connect linked successfully.", "success");
        this.requestUpdate();
      },
      onError: (msg) => {
        this._connectError = msg;
        this._linkingConnect = false;
        this.requestUpdate();
      },
    });
  }

  async _unlinkConnect() {
    const ok = window.confirm(
      "Unlink Selora Connect?\n\nExternal MCP tools (Openclaw, Claude Desktop, Cursor, Windsurf) will lose access until you re-link.",
    );
    if (!ok) {
      // The toggle has already flipped to "off" in the DOM — refresh
      // from the backend so the switch snaps back to its true state.
      this.requestUpdate();
      await this._loadConfig();
      return;
    }
    try {
      await this.hass.callWS({ type: "selora_ai/unlink_connect" });
      await this._loadConfig();
      this._showToast("Selora Connect unlinked.", "success");
    } catch (err) {
      this._showToast("Failed to unlink: " + err.message, "error");
    }
  }

  // ── AI Gateway OAuth Link flow ────────────────────────────────────

  async _startAIGatewayLink() {
    if (this._linkingAIGateway) return;
    this._linkingAIGateway = true;
    this._aigatewayError = "";
    this.requestUpdate();

    await this._runOAuthLink({
      flow: "aigateway",
      wsType: "selora_ai/start_aigw_link",
      beforeStart: async () => {
        // Persist the developer-mode Selora Cloud URL before linking so
        // the LLM provider rebuilt on entry reload uses it. Otherwise the
        // unsaved field reverts after the post-link reload and chat
        // completions go to the default host.
        if (this._config?.developer_mode && this._config?.selora_connect_url) {
          await this.hass.callWS({
            type: "selora_ai/update_config",
            config: { selora_connect_url: this._config.selora_connect_url },
          });
        }
      },
      onSuccess: async () => {
        await this._loadConfig();
        this._linkingAIGateway = false;
        this._showToast("Selora Cloud linked successfully.", "success");
        this.requestUpdate();
      },
      onError: (msg) => {
        this._aigatewayError = msg;
        this._linkingAIGateway = false;
        this.requestUpdate();
      },
    });
  }

  async _unlinkAIGateway() {
    const ok = window.confirm(
      "Unlink Selora Cloud?\n\nChat and automation suggestions will stop until you re-link your account in Settings.",
    );
    if (!ok) return;
    try {
      await this.hass.callWS({ type: "selora_ai/unlink_aigateway" });
      await this._loadConfig();
      this._showToast("Selora Cloud unlinked.", "success");
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
    // Warning toasts carry actionable text the user must read (e.g.
    // forced-disabled risk-gated automation). Keep them up longer.
    const duration = type === "warning" ? 8000 : 3500;
    this._toastTimer = setTimeout(() => {
      this._toast = "";
      this._toastType = "info";
      this._toastTimer = null;
      this.requestUpdate();
    }, duration);
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
    // Reflect "no LLM configured" as a host attribute so CSS can suppress
    // decorative effects (header glow, particles) until the user finishes
    // setup. Cheap toggle, fine to run every update.
    if (this._config) {
      this.toggleAttribute("needs-setup", this._llmNeedsSetup);
    }
    // Reflect the active quota alert as a host attribute so CSS / status
    // chrome can react. Cheap toggle, fine to run every update.
    this.toggleAttribute("quota-exceeded", !!this._quotaAlert);
    if (changedProps.has("hass")) {
      // hass can land after connectedCallback (depends on the panel-mount
      // path) — kick the quota subscription as soon as it's available.
      this._attachWsReadyListener();
      this._ensureQuotaSubscription();
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
      this._requestScrollChat();
    }
    // Hydrate entity chips emitted by the markdown renderer. Re-runs when
    // messages change (new chips appeared), hass changes (live state updates),
    // or the chat tab becomes active again (tab switch destroys and recreates
    // the chat DOM, so grids need re-wiring even if _messages didn't change).
    if (
      this.hass &&
      (changedProps.has("_messages") ||
        changedProps.has("hass") ||
        (changedProps.has("_activeTab") && this._activeTab === "chat"))
    ) {
      this._hydrateEntityChips();
    }
    // Auto-focus the composer when the chat tab activates or when the user
    // switches to a different conversation, so they can start typing
    // immediately.
    if (
      this._activeTab === "chat" &&
      (changedProps.has("_activeTab") || changedProps.has("_activeSessionId"))
    ) {
      this._focusComposerSoon();
    }
  }

  _focusComposerSoon() {
    // Wait one frame so newly-rendered welcome/docked composer is in the DOM.
    requestAnimationFrame(() => {
      const ta = this.shadowRoot?.querySelector(".composer-textarea");
      if (!ta) return;
      const active = this.shadowRoot.activeElement;
      if (
        active &&
        active !== ta &&
        (active.tagName === "INPUT" || active.tagName === "TEXTAREA")
      ) {
        return; // user is already typing somewhere — don't steal focus
      }
      ta.focus();
    });
  }

  // Hydrate `[[entity:<id>|…]]` and `[[entities:id1,id2,…]]` placeholders
  // with real HA tile cards. We try two construction paths so the
  // panel works across HA frontend variants:
  //   1. `window.loadCardHelpers().createCardElement({type:"tile",…})`
  //      — the documented API; also lazy-loads the card chunk.
  //   2. `document.createElement("hui-tile-card") + setConfig(…)` —
  //      direct construction once Lovelace has registered the
  //      element. We wait briefly via `customElements.whenDefined`
  //      so we don't race the registration.
  // Cards self-update when we keep their `.hass` property current, so
  // we just refresh that on every pass.
  async _hydrateEntityChips() {
    const root = this.shadowRoot;
    if (!root) return;
    const grids = root.querySelectorAll(".selora-entity-grid");
    if (grids.length === 0) return;

    const createTile = await this._getTileCardCreator();
    const registries = await this._ensureFullRegistries();

    let cardsAppended = false;

    for (const grid of grids) {
      const wired = grid.dataset.wired === "true";

      if (!wired) {
        const ids = (grid.dataset.entityIds || "")
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
        let appended = 0;
        if (createTile) {
          // Build (areaName, ids[]) groups so multi-area lists render
          // as a single grid with full-width section headers between
          // sub-runs. Single-area lists render flat (no header). The
          // `null` key collects entities without an assigned area.
          const groups = new Map();
          for (const id of ids) {
            if (!this.hass.states?.[id]) continue;
            const reg = registries.entities?.[id];
            const dev = reg?.device_id
              ? registries.devices?.[reg.device_id]
              : null;
            const areaId = reg?.area_id || dev?.area_id || null;
            const areaName = areaId
              ? registries.areas?.[areaId]?.name || null
              : null;
            if (!groups.has(areaName)) groups.set(areaName, []);
            groups.get(areaName).push(id);
          }
          // Stable order: named areas alphabetically first, "no area"
          // last so unassigned devices don't push named ones around.
          const sortedGroups = [...groups.entries()].sort((a, b) => {
            if (!a[0]) return 1;
            if (!b[0]) return -1;
            return a[0].localeCompare(b[0]);
          });
          const showHeaders = groups.size > 1;
          const buildTile = (id) => {
            const card = createTile(id);
            if (!card) return null;
            card.hass = this.hass;
            // Hover tooltip with manufacturer / model — see the
            // _ensureFullRegistries comment for why we can't rely on
            // hass.entities directly.
            const reg = registries.entities?.[id];
            const dev = reg?.device_id
              ? registries.devices?.[reg.device_id]
              : null;
            if (dev) {
              const parts = [];
              if (dev.manufacturer) parts.push(dev.manufacturer);
              if (dev.model) parts.push(dev.model);
              if (parts.length) card.title = parts.join(" · ");
            }
            return card;
          };
          // Resolve area_id alongside the name so the header can show
          // the area's own configured icon (HA stores `icon` on the
          // area registry entry — a string like "mdi:bed").
          const areaIdByName = new Map();
          for (const a of Object.values(registries.areas || {})) {
            if (a.name) areaIdByName.set(a.name, a.area_id);
          }
          for (const [areaName, areaIds] of sortedGroups) {
            if (showHeaders) {
              const header = document.createElement("div");
              header.className = "selora-area-header";
              const icon = document.createElement("ha-icon");
              icon.icon = areaName
                ? registries.areas?.[areaIdByName.get(areaName)]?.icon ||
                  "mdi:floor-plan"
                : "mdi:help-circle-outline";
              icon.className = "selora-area-icon";
              const label = document.createElement("span");
              label.textContent = areaName || "Unassigned";
              header.append(icon, label);
              grid.appendChild(header);
            }
            for (const id of areaIds) {
              try {
                const card = buildTile(id);
                if (!card) continue;
                grid.appendChild(card);
                appended += 1;
              } catch (e) {
                console.warn("Selora: tile card create failed for", id, e);
              }
            }
          }
        }
        if (appended === 0) {
          // Last-resort fallback so the message is still readable.
          // Hits when neither construction path resolved or all ids
          // were unknown/missing in hass.states.
          grid.textContent = ids.join(", ");
        } else {
          cardsAppended = true;
        }
        grid.dataset.wired = "true";
      }

      // Keep cards' hass current so brightness, on/off, etc. stay live.
      // hui-entities-card (and its inner rows) compare incoming hass by
      // reference and skip when unchanged. HA sometimes mutates the same
      // hass object instead of creating a new one, so a plain reassign
      // looks like "no change" and the card never re-renders. A shallow
      // copy guarantees a fresh reference; methods like callService
      // survive because they're own properties on hass.
      for (const card of grid.children) {
        if (card.hass !== undefined) {
          card.hass = { ...this.hass };
        }
      }
    }
    // Tile cards expand the message height after the synchronous Lit
    // render, so the initial scroll in updated() lands short. Re-scroll
    // only when new cards were just appended — every subsequent
    // hass-driven hydration (live state from a service call, e.g. the
    // brightness slider in a more-info dialog firing light.turn_on)
    // would otherwise yank the chat to the bottom even when no layout
    // change happened. Force the scroll: the layout just grew and the
    // user has no scroll position to preserve (the previous explicit
    // scroll-to-bottom in _openSession landed before the cards added
    // their height, so the respect-position guard would otherwise see
    // distance > 80 and leave the user above the new bottom).
    if (cardsAppended) {
      this._requestScrollChat({ force: true });
    }
  }

  // Lazily fetch the full entity + device registries via WS. The
  // `hass.entities` object exposed to panels is the *display* registry
  // (no device_id), so we can't get from entity_id to manufacturer
  // through it. Cached on `this` for the panel's lifetime — registry
  // changes mid-session won't refresh until the panel reloads, which
  // is fine for a tooltip.
  async _ensureFullRegistries() {
    // If a previous load resolved to empty maps (transient WS failure)
    // we want the next call to try again rather than be stuck with the
    // empty cache for the rest of the panel's lifetime. The retry
    // condition is "not just an empty object" — a populated load
    // is final.
    if (this._fullRegistriesPromise) {
      const cached = await this._fullRegistriesPromise;
      const populated =
        Object.keys(cached.entities).length > 0 ||
        Object.keys(cached.devices).length > 0 ||
        Object.keys(cached.areas).length > 0;
      if (populated) return cached;
      this._fullRegistriesPromise = null;
    }
    this._fullRegistriesPromise = (async () => {
      try {
        const [entityList, deviceList, areaList] = await Promise.all([
          this.hass.callWS({ type: "config/entity_registry/list" }),
          this.hass.callWS({ type: "config/device_registry/list" }),
          this.hass.callWS({ type: "config/area_registry/list" }),
        ]);
        const entities = {};
        for (const e of entityList) entities[e.entity_id] = e;
        const devices = {};
        for (const d of deviceList) devices[d.id] = d;
        const areas = {};
        for (const a of areaList) areas[a.area_id] = a;
        return { entities, devices, areas };
      } catch (e) {
        console.warn("Selora: registry list failed", e);
        return { entities: {}, devices: {}, areas: {} };
      }
    })();
    return this._fullRegistriesPromise;
  }

  // Lazily resolve a single function `(entityId) => HTMLElement` that
  // builds an HA card for one entity. Uses the `entities` card type —
  // it renders the domain-appropriate native control inline (toggle
  // for switches, slider for volume, climate readout for HVAC, cover
  // arrows for blinds, etc.) instead of the bare tap-target shown by
  // `tile`. Tries `window.loadCardHelpers` first, then falls back to
  // `document.createElement("hui-entities-card")` once Lovelace has
  // registered the element. Cached on `this` so the chunk-load only
  // happens once per panel lifetime.
  async _getTileCardCreator() {
    if (this._tileCardCreator !== undefined) return this._tileCardCreator;

    // Map each entity domain to the most useful inline tile feature so
    // chat cards visually match HA's default dashboard tile and keep
    // domain-appropriate controls (brightness slider, volume slider,
    // cover arrows, climate target temp, etc.). Domains without a
    // standard feature (sensor, binary_sensor, switch, person, …) get
    // no extras — the tile's tap target is already the right action.
    const featuresForDomain = (entityId) => {
      const domain = entityId.split(".")[0];
      switch (domain) {
        case "light":
          return [{ type: "light-brightness" }];
        case "cover":
          return [{ type: "cover-open-close" }];
        case "fan":
          return [{ type: "fan-speed" }];
        case "media_player":
          return [{ type: "media-player-volume-slider" }];
        case "climate":
          return [{ type: "target-temperature" }];
        case "vacuum":
          return [{ type: "vacuum-commands" }];
        case "lock":
          return [{ type: "lock-commands" }];
        case "alarm_control_panel":
          return [{ type: "alarm-modes" }];
        case "water_heater":
          return [{ type: "water-heater-operation-modes" }];
        case "humidifier":
          return [{ type: "humidifier-toggle" }];
        case "lawn_mower":
          return [{ type: "lawn-mower-commands" }];
        default:
          return [];
      }
    };
    const buildConfig = (id) => ({
      type: "tile",
      entity: id,
      features: featuresForDomain(id),
    });

    // Path 1: HA's documented helper.
    if (typeof window.loadCardHelpers === "function") {
      try {
        const helpers = await window.loadCardHelpers();
        if (helpers && typeof helpers.createCardElement === "function") {
          this._tileCardCreator = (id) =>
            helpers.createCardElement(buildConfig(id));
          return this._tileCardCreator;
        }
      } catch (e) {
        console.warn("Selora: loadCardHelpers() failed", e);
      }
    }

    // Path 2: direct construction. Race a short timeout against
    // whenDefined so we don't hang forever on HA builds where the
    // element never registers on a custom-panel page.
    try {
      const ready = await Promise.race([
        customElements.whenDefined("hui-tile-card").then(() => true),
        new Promise((resolve) => setTimeout(() => resolve(false), 3000)),
      ]);
      if (ready) {
        this._tileCardCreator = (id) => {
          const el = document.createElement("hui-tile-card");
          el.setConfig(buildConfig(id));
          return el;
        };
        return this._tileCardCreator;
      }
    } catch (e) {
      console.warn("Selora: hui-tile-card whenDefined failed", e);
    }

    this._tileCardCreator = null;
    return null;
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
    // Only add scene_id context for Selora-managed scenes. Using a
    // non-Selora scene_id as existing_scene_id causes async_create_scene
    // to reject it; for HA scenes we create a new Selora scene instead.
    const ctx =
      known || scene.source !== "selora"
        ? ""
        : ` (scene_id: ${scene.scene_id})`;
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
          <img
            src="/api/selora_ai/${this._isDark ? "logo" : "logo-light"}.png"
            alt=""
            class="header-logo"
            @click=${() => {
              this._activeTab = "chat";
            }}
            style="cursor:pointer;"
          />
          <span
            class="header-title ${this._isDark ? "gold-text" : ""}"
            @click=${() => {
              this._activeTab = "chat";
            }}
            style="cursor:pointer;"
            >Selora AI</span
          >
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
                >Conversations</span
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
          ${this._activeTab !== "chat" || this._messages.length > 0
            ? html`<button
                class="header-new-chat"
                title="New chat"
                aria-label="New chat"
                @click=${() => {
                  this._showOverflowMenu = false;
                  if (this._messages.length === 0) {
                    this._activeTab = "chat";
                    if (this.narrow) this._showSidebar = false;
                  } else {
                    this._newSession();
                  }
                }}
              >
                <ha-icon icon="mdi:square-edit-outline"></ha-icon>
                <span class="header-new-chat-label">New chat</span>
              </button>`
            : ""}
          <div class="overflow-btn-wrap">
            <button
              class="overflow-btn selora-menu-btn"
              aria-label="Selora menu"
              @click=${(e) => {
                e.stopPropagation();
                const opening = !this._showOverflowMenu;
                this._showOverflowMenu = opening;
                // On mobile the conversations drawer overlays the body
                // and would compete for the same space as the dropdown;
                // collapse it whenever the menu opens.
                if (opening && this.narrow) this._showSidebar = false;
              }}
            >
              <ha-icon icon="mdi:dots-grid"></ha-icon>
            </button>
            ${this._showOverflowMenu
              ? html`
                  <div class="overflow-menu selora-menu">
                    <div class="overflow-section narrow-only">
                      <button
                        class="overflow-item"
                        @click=${() => {
                          this._showOverflowMenu = false;
                          this._activeTab = "chat";
                          this._showSidebar = true;
                        }}
                      >
                        <ha-icon icon="mdi:chat-outline"></ha-icon>
                        Conversations
                      </button>
                      <button
                        class="overflow-item ${this._activeTab === "automations"
                          ? "active"
                          : ""}"
                        @click=${() => {
                          this._showOverflowMenu = false;
                          this._activeTab = "automations";
                          this._showSidebar = false;
                          this._loadAutomations();
                        }}
                      >
                        <ha-icon icon="mdi:robot-outline"></ha-icon>
                        Automations
                      </button>
                      <button
                        class="overflow-item ${this._activeTab === "scenes"
                          ? "active"
                          : ""}"
                        @click=${() => {
                          this._showOverflowMenu = false;
                          this._activeTab = "scenes";
                          this._showSidebar = false;
                          this._loadScenes();
                        }}
                      >
                        <ha-icon icon="mdi:palette-outline"></ha-icon>
                        Scenes
                      </button>
                      <div class="overflow-divider"></div>
                    </div>
                    <button
                      class="overflow-item ${this._activeTab === "settings"
                        ? "active"
                        : ""}"
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
                      <span class="overflow-item-label">Documentation</span>
                      <ha-icon
                        icon="mdi:open-in-new"
                        class="overflow-item-external"
                      ></ha-icon>
                    </a>
                    <button
                      class="overflow-item"
                      @click=${() => {
                        this._showOverflowMenu = false;
                        this._openFeedback();
                      }}
                    >
                      <ha-icon icon="mdi:message-alert-outline"></ha-icon>
                      <span class="overflow-item-label">Give Feedback</span>
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
                      <span class="overflow-item-label">GitHub Issues</span>
                      <ha-icon
                        icon="mdi:open-in-new"
                        class="overflow-item-external"
                      ></ha-icon>
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
                      <span class="overflow-item-label">GitLab Repository</span>
                      <ha-icon
                        icon="mdi:open-in-new"
                        class="overflow-item-external"
                      ></ha-icon>
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
                      ${this._deleteConfirmSessionId === s.id
                        ? html`
                            <div class="session-item session-delete-confirm">
                              <span class="session-delete-confirm-label"
                                >Delete?</span
                              >
                              <div
                                style="display:flex;gap:6px;margin-left:auto;"
                              >
                                <button
                                  class="btn btn-sm"
                                  style="background:#ef4444;color:#fff;border-color:#ef4444;padding:3px 10px;font-size:12px;"
                                  @click=${(e) => {
                                    e.stopPropagation();
                                    this._confirmDeleteSession();
                                  }}
                                >
                                  Delete
                                </button>
                                <button
                                  class="btn btn-outline btn-sm"
                                  style="padding:3px 10px;font-size:12px;"
                                  @click=${(e) => {
                                    e.stopPropagation();
                                    this._deleteConfirmSessionId = null;
                                  }}
                                >
                                  Cancel
                                </button>
                              </div>
                            </div>
                          `
                        : html`
                            <div
                              class="session-item ${s.id ===
                              this._activeSessionId
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
                              @touchstart=${(e) =>
                                this._onSessionTouchStart(e, s.id)}
                              @touchmove=${(e) =>
                                this._onSessionTouchMove(e, s.id)}
                              @touchend=${(e) =>
                                this._onSessionTouchEnd(e, s.id)}
                            >
                              ${this._selectChatsMode
                                ? html`
                                    <input
                                      type="checkbox"
                                      class="session-checkbox"
                                      .checked=${!!this._selectedSessionIds[
                                        s.id
                                      ]}
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
                                      @click=${(e) =>
                                        this._deleteSession(s.id, e)}
                                      title="Delete"
                                    ></ha-icon>
                                  `
                                : ""}
                            </div>
                          `}
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
            .count=${this._quotaAlert
              ? this._isDark
                ? 1600
                : 600
              : this._isDark
                ? 1200
                : 400}
            .color=${this._quotaAlert
              ? "#ef4444"
              : this._isDark
                ? "#C7AE6A"
                : this._primaryColor || "#03a9f4"}
            .maxOpacity=${this._quotaAlert ? 1.0 : this._isDark ? 1.0 : 0.5}
          ></selora-particles>
          ${this._renderQuotaBanner()}
          ${this._activeTab === "chat" ? this._renderChat() : ""}
          ${this._activeTab === "automations" ? this._renderAutomations() : ""}
          ${this._activeTab === "scenes" ? this._renderScenes() : ""}
          ${this._activeTab === "settings" ? this._renderSettings() : ""}
          ${this._activeTab === "usage" ? this._renderUsage() : ""}
        </div>
      </div>

      ${this._renderFeedbackModal()}
      ${this._deleteConfirmSessionId === "__bulk__"
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
                <div style="font-size:17px;font-weight:600;margin-bottom:8px;">
                  Delete Conversations
                </div>
                <div style="font-size:13px;opacity:0.7;margin-bottom:20px;">
                  Delete
                  ${Object.values(this._selectedSessionIds).filter(Boolean)
                    .length}
                  selected conversation(s)? This cannot be undone.
                </div>
                <div style="display:flex;gap:10px;justify-content:center;">
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
