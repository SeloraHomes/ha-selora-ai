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
import { localize as i18nLocalize } from "./shared/i18n.js";
import {
  renderChat,
  renderMessage,
  renderYamlEditor,
} from "./panel/render-chat.js";
import {
  renderAutomations,
  renderAutomationFlowchart,
  renderProposalCard,
  renderProposalActions,
  toggleYaml,
  masonryColumns,
} from "./panel/render-automations.js";
import { renderSceneCard, renderScenes } from "./panel/render-scenes.js";
import { renderSuggestionsSection } from "./panel/render-suggestions.js";
import { renderSettings } from "./panel/render-settings.js";
import { renderTelemetryConsent } from "./panel/render-telemetry-consent.js";
import { renderUsage, loadUsageStats } from "./panel/render-usage.js";
import { renderRecipesV2 } from "./panel/render-recipes.js";
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
import * as sceneEdit from "./panel/scene-edit.js";

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
      // Shell-style ArrowUp/ArrowDown history navigation cursor. null
      // means "not browsing history; _input is the live draft".
      _historyIndex: { type: Number },
      _historyDraft: { type: String },
      _loading: { type: Boolean },
      _streaming: { type: Boolean },
      _chatScrolledAway: { type: Boolean },

      // Chat composer autocomplete (devices / areas / scenes / automations)
      _autocomplete: { type: Object },
      _autocompleteSelections: { type: Array },
      // Ghost-text completion of common chat vocabulary
      _ghost: { type: Object },

      // Sidebar visibility (mobile)
      _showSidebar: { type: Boolean },

      // Tabs
      _activeTab: { type: String },

      // Recipes tab (v2 pipeline)
      // ``_recipesView`` is one of "list" | "wizard" | "result".
      _recipesView: { type: String },
      _recipesList: { type: Object },
      // Lazily-fetched package contents per slug: { [slug]: { yaml, counts } }.
      // Populated when an installed recipe's Details panel is expanded.
      _recipePackages: { type: Object },
      _recipesBusy: { type: Boolean },
      // Catalog fetched from selorahomes.com (or local dev). null
      // until first fetch resolves; null + non-empty error means the
      // fetch failed and we render a fallback message.
      _recipesCatalog: { type: Object },
      _recipesCatalogBusy: { type: Boolean },
      _recipesCatalogError: { type: String },
      _recipesCatalogSearch: { type: String },
      // 1-based page for the paginated catalog list (below the featured strip).
      _catalogPage: { type: Number },
      _recipeWizardSlug: { type: String },
      _recipeWizardDetail: { type: Object },
      _recipeWizardInputs: { type: Object },
      // Per-role pick for ``selection: required`` roles —
      // ``{role_id: [entity_id, ...]}``. ``selection: auto`` roles
      // are left out of this dict and the resolver fills them in
      // automatically.
      _recipeWizardSelections: { type: Object },
      _recipeWizardPreview: { type: Object },
      _recipeWizardResult: { type: Object },
      // Install-source card (URL fetch + drag-and-drop upload).
      _recipesUrl: { type: String },
      _recipesUrlBusy: { type: Boolean },
      _recipesUploadBusy: { type: Boolean },
      _recipesDragOver: { type: Boolean },
      _recipesInstallError: { type: String },
      // Slug awaiting uninstall confirmation. ``null`` when no
      // confirmation is in flight; otherwise the recipe's slug.
      _recipeUninstallPending: { type: String },
      // Set of HA config_entry ids the user opted to remove alongside
      // the recipe. Populated via checkboxes in the uninstall modal.
      _recipeUninstallEntries: { type: Object },
      // Pipeline view: id of the item the action panel is focused on.
      // ``null`` lets ``_activeItem`` pick the best default.
      _recipeActiveItemId: { type: String },
      // Role-selection panel UI state, keyed by role id. Transient (not
      // persisted): a name filter string and a "show all candidates"
      // toggle so broad roles (e.g. every ``sensor``) don't dump dozens
      // of chips at once.
      _recipeRoleFilters: { type: Object },
      _recipeRoleExpanded: { type: Object },
      // Dashboard-card picker state. ``_recipeDashboards`` is the list of
      // writable dashboards from the backend; ``_recipeDashboardTarget``
      // is the user's pick — ``undefined`` (manifest default), a
      // url_path, or "__skip__" (don't add a card).
      _recipeDashboards: { type: Array },
      _recipeDashboardTarget: { type: String },
      // Inline HA config-flow state, keyed by integration domain.
      // ``{ [domain]: { flow_id, step, values, state, error } }``
      // ``state`` ∈ ``"form" | "running" | "complete" | "error"``.
      _recipeFlows: { type: Object },
      // 4-step linear wizard: 1=Overview, 2=Match, 3=Resolve, 4=Activate.
      // Each step gates the next via _canAdvanceFromStep.
      _recipeWizardStep: { type: Number },
      // v3 prototype — "Manage devices" modal state. ``slug`` indicates
      // the modal is open for that recipe; ``detail`` carries the
      // loaded manifest + installed record; ``selections`` mirrors the
      // current role bindings the user is editing.
      _recipeManageSlug: { type: String },
      _recipeManageDetail: { type: Object },
      _recipeManageSelections: { type: Object },
      _recipeManageBusy: { type: Boolean },
      _recipeManageError: { type: String },

      // Automations tab
      _suggestions: { type: Array },
      _automations: { type: Array },
      _expandedAutomations: { type: Object },

      // Settings tab
      _config: { type: Object },
      _savingLlmConfig: { type: Boolean },
      _savingAdvancedConfig: { type: Boolean },
      _clearingCache: { type: Boolean },
      _llmSaveStatus: { type: Object },
      _showApiKeyInput: { type: Boolean },
      _newApiKey: { type: String },
      _seloraLocalAdvanced: { type: Boolean },

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
      _sortDir: { type: String },

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

      // True when the user kicked off "New Automation" from the
      // automations tab. Tweaks the empty-chat welcome copy and the
      // composer placeholder so it's obvious the next message will
      // start a fresh automation. Cleared on send.
      _newAutomationMode: { type: Boolean },
      // In-flight flag for the "Suggest one for me" button shown in
      // new-automation mode — keeps the button disabled / spinning
      // while the LLM is composing an idea.
      _suggestingAutomation: { type: Boolean },

      // Per-message-index flag set true the instant the user clicks
      // Accept & Save. Drives a brief exit animation on the proposal's
      // Accept button before the chat state flips to "saved" and the
      // workflow row mounts — without it the swap would look like a
      // sudden jump between two different UIs.
      _acceptAnimating: { type: Object },

      // Per-automation "Run now" in-flight flag — keeps the inline
      // Run button disabled / spinning while the
      // `automation.trigger` service call is round-tripping.
      _runningAutomation: { type: Object },

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

      // Per-card expand toggle for clamped suggestion title/subtitle
      _expandedSuggestions: { type: Object },

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

      // Command approvals (Always-scope grants surfaced in settings)
      _approvalGrants: { type: Array },
      _revokingApprovalKey: { type: String },

      // Device detail drawer
      _deviceDetail: { type: Object },
      _deviceDetailLoading: { type: Boolean },

      // Scenes tab
      _scenes: { type: Array },
      _sceneFilter: { type: String },
      _sceneSortBy: { type: String },
      _sceneStatusFilter: { type: String },
      _sceneSortDir: { type: String },
      _expandedScenes: { type: Object },
      _sceneEdits: { type: Object },
      _savingScene: { type: Object },
      _testingScene: { type: Object },
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
    this._historyIndex = null;
    this._historyDraft = "";
    this._loading = false;
    this._streaming = false;
    this._chatScrolledAway = false;
    this._autocomplete = {
      open: false,
      items: [],
      activeIndex: 0,
      trigger: null,
    };
    this._autocompleteSelections = [];
    this._ghost = null;
    this._streams = new Set();
    this._showSidebar = false;
    this._activeTab = "chat";
    this._recipesView = "list";
    this._recipesList = { available: [], installed: [] };
    this._recipePackages = {};
    this._recipesBusy = false;
    // A native <select> popup collapses if the host re-renders while open.
    // ``shouldUpdate`` defers re-renders while one is open; wired in
    // ``firstUpdated``.
    this._nativeSelectOpen = false;
    this._nativeSelectTimer = null;
    this._recipesCatalog = null;
    this._recipesCatalogBusy = false;
    this._recipesCatalogError = null;
    this._recipesCatalogSearch = "";
    this._catalogPage = 1;
    this._recipeWizardSlug = null;
    this._recipeWizardDetail = null;
    this._recipeWizardInputs = {};
    this._recipeWizardSelections = {};
    this._recipeWizardPreview = null;
    this._recipeWizardResult = null;
    this._recipesUrl = "";
    this._recipesUrlBusy = false;
    this._recipesUploadBusy = false;
    this._recipesDragOver = false;
    this._recipesInstallError = null;
    this._recipeUninstallPending = null;
    this._recipeUninstallEntries = {};
    this._recipeActiveItemId = null;
    this._recipeFlows = {};
    this._recipeRoleFilters = {};
    this._recipeRoleExpanded = {};
    this._recipeDashboards = [];
    this._recipeDashboardTarget = undefined;
    this._recipeEntityRegistryUnsub = null;
    this._recipeWizardStep = 1;
    this._recipeManageSlug = null;
    this._recipeManageDetail = null;
    this._recipeManageSelections = {};
    this._recipeManageBusy = false;
    this._recipeManageError = null;
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
    this._clearingCache = false;
    this._llmSaveStatus = null;
    this._showApiKeyInput = false;
    this._newApiKey = "";
    this._seloraLocalAdvanced = false;
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
    this._sortDir = "desc";
    this._sceneStatusFilter = "all";
    this._sceneSortDir = "desc";
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
    this._newAutomationMode = false;
    this._suggestingAutomation = false;
    this._acceptAnimating = {};
    this._runningAutomation = {};
    this._unavailableAutoId = null;
    this._unavailableAutoName = null;
    this._generatingSuggestions = false;
    // Inline card tabs
    this._cardActiveTab = {};
    this._expandedSuggestions = {};
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
    // Command approvals
    this._approvalGrants = [];
    this._revokingApprovalKey = null;
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
    this._sceneEdits = {};
    this._savingScene = {};
    this._testingScene = {};
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
    this._loadApprovalGrants();
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
    return this._t("panel_quota_provider_default", "your LLM provider");
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
          <strong
            >${this._quotaProviderLabel()}
            ${this._t("panel_quota_reached", "quota reached.")}</strong
          >
          ${remaining > 0
            ? html` ${this._t("panel_quota_try_again_prefix", "Try again in")}
              ${remaining}s.`
            : ` ${this._t("panel_quota_retrying_now", "Retrying now…")}`}
        </div>
        <button
          class="quota-banner-close"
          aria-label=${this._t("panel_quota_dismiss", "Dismiss")}
          @click=${() => this._dismissQuotaAlert()}
        >
          <ha-icon icon="mdi:close"></ha-icon>
        </button>
      </div>
    `;
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._unsubscribeRecipeEntityRegistry) {
      this._unsubscribeRecipeEntityRegistry();
    }
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
    if (this._chatPinDeadline) {
      this._chatPinDeadline = 0;
    }
    if (this._oauthPollTimer) {
      clearInterval(this._oauthPollTimer);
      this._oauthPollTimer = null;
    }
    clearTimeout(this._nativeSelectTimer);
    this._nativeSelectOpen = false;
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
    // Tear down EVERY in-flight chat stream. With background streams
    // allowed across session switches, this._streams can hold more
    // than one entry; passing { all: true } makes _stopStreaming walk
    // the whole set instead of just the current session's stream.
    // Otherwise a detach/reattach would leave background subscriptions
    // mutating a detached panel.
    if (this._streams && this._streams.size > 0) {
      try {
        this._stopStreaming({ all: true });
      } catch (_e) {
        // Already detached or websocket gone — flags must still be reset
        // so a reattach starts clean.
        this._streams.clear();
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
          message: this._t(
            "panel_llm_switched_selora_cloud",
            "Switched to Selora Cloud.",
          ),
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
      } else if (provider === "selora_local") {
        payload.selora_local_host = this._config.selora_local_host;
      } else {
        payload.ollama_host = this._config.ollama_host;
        payload.ollama_model = this._config.ollama_model;
      }

      // Validate if a new key was entered or for local providers
      // (Ollama / Selora Local — always validate connectivity).
      const needsValidation =
        newKey || provider === "ollama" || provider === "selora_local";
      if (needsValidation) {
        const validatePayload = {
          type: "selora_ai/validate_llm_key",
          provider,
        };
        if (provider === "ollama") {
          validatePayload.host = this._config.ollama_host;
          validatePayload.model = this._config.ollama_model;
        } else if (provider === "selora_local") {
          validatePayload.host = this._config.selora_local_host;
        } else {
          validatePayload.api_key = newKey;
          validatePayload.model = this._config[`${provider}_model`];
        }
        const result = await this.hass.callWS(validatePayload);
        if (!result.valid) {
          this._llmSaveStatus = {
            type: "error",
            message:
              result.error ||
              this._t(
                "panel_llm_invalid_key",
                "Invalid API key or provider unreachable.",
              ),
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
      this._llmSaveStatus = {
        type: "success",
        message: this._t("panel_llm_settings_saved", "LLM settings saved."),
      };
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

  async _setTelemetryConsent(enabled) {
    // One-time consent banner choice. Either way the prompt is marked seen
    // so it never shows again; "Enable" additionally turns telemetry on.
    try {
      await this.hass.callWS({
        type: "selora_ai/update_config",
        config: {
          telemetry_enabled: enabled === true,
          telemetry_prompt_seen: true,
        },
      });
      // Reflect locally only after the backend persisted the choice, so a
      // failed save never hides the banner or shows telemetry as enabled
      // when it isn't.
      this._updateConfig("telemetry_enabled", enabled === true);
      this._updateConfig("telemetry_prompt_seen", true);
    } catch (err) {
      this._showToast("Failed to save: " + err.message, "error");
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
        telemetry_enabled: this._config.telemetry_enabled === true,
        // Deciding the toggle here counts as seeing the prompt — mark it
        // so the one-time consent banner never reappears afterwards.
        telemetry_prompt_seen: true,
        // Developer-only: Connect Server URL (editable when Connect is unlinked)
        selora_connect_url: this._config.selora_connect_url,
      };
      await this.hass.callWS({
        type: "selora_ai/update_config",
        config: payload,
      });
      await this._loadConfig();
      this._showToast(
        this._t("panel_advanced_settings_saved", "Advanced settings saved."),
        "success",
      );
    } catch (err) {
      this._showToast("Failed to save: " + err.message, "error");
    } finally {
      this._savingAdvancedConfig = false;
    }
  }

  _goToSettings() {
    this._setActiveTab("settings");
    this._loadConfig();
    this._loadMcpTokens();
    this._loadApprovalGrants();
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
          onError(
            data.error || this._t("panel_linking_failed", "Linking failed."),
          );
        }
      }, "selora_ai_oauth_linked");

      const result = await this.hass.callWS({
        type: wsType,
        connect_url: this._config?.selora_connect_url || "",
        // Hand HA the panel's CURRENT origin so the OAuth callback
        // lands on the same URL the user is browsing from. Backend
        // validates against HA's known internal+external URLs and
        // falls back to the legacy external-preferred behaviour if
        // we send something it doesn't recognise. Without this, a
        // user opening the panel locally got an OAuth flow that
        // bounced through their external URL — which breaks when the
        // external endpoint blocks non-HA traffic.
        panel_origin: window.location.origin,
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
          this._t(
            "panel_linking_timed_out",
            "Linking timed out. Please try again — make sure you finish signing in within 10 minutes.",
          ),
        );
      }, this._OAUTH_LINK_TIMEOUT_MS);
    } catch (err) {
      cleanup();
      onError(
        err.message ||
          this._t("panel_linking_start_failed", "Failed to start linking."),
      );
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
        this._showToast(
          this._t(
            "panel_connect_linked_success",
            "Selora Connect linked successfully.",
          ),
          "success",
        );
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
      this._t(
        "panel_unlink_connect_confirm",
        "Unlink Selora Connect?\n\nExternal MCP tools (Openclaw, Claude Desktop, Cursor, Windsurf) will lose access until you re-link.",
      ),
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
      this._showToast(
        this._t("panel_connect_unlinked", "Selora Connect unlinked."),
        "success",
      );
    } catch (err) {
      this._showToast("Failed to unlink: " + err.message, "error");
    }
  }

  async _clearLearnedCache() {
    const ok = window.confirm(
      this._t(
        "panel_clear_cache_confirm",
        "Clear all learned data?\n\nStored usage history, detected patterns, and pending suggestions will be deleted. This is safe — Selora relearns over time and your saved automations are untouched.",
      ),
    );
    if (!ok) return;
    this._clearingCache = true;
    this.requestUpdate();
    try {
      await this.hass.callWS({ type: "selora_ai/clear_cache" });
      this._showToast(
        this._t("panel_clear_cache_done", "Learned data cleared."),
        "success",
      );
    } catch (err) {
      this._showToast(
        this._t("panel_clear_cache_failed", "Failed to clear learned data:") +
          " " +
          err.message,
        "error",
      );
    } finally {
      this._clearingCache = false;
      this.requestUpdate();
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
        this._showToast(
          this._t(
            "panel_cloud_linked_success",
            "Selora Cloud linked successfully.",
          ),
          "success",
        );
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
      this._t(
        "panel_unlink_cloud_confirm",
        "Unlink Selora Cloud?\n\nChat and automation suggestions will stop until you re-link your account in Settings.",
      ),
    );
    if (!ok) return;
    try {
      await this.hass.callWS({ type: "selora_ai/unlink_aigateway" });
      await this._loadConfig();
      this._showToast(
        this._t("panel_cloud_unlinked", "Selora Cloud unlinked."),
        "success",
      );
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
      this._showToast(
        this._t("panel_mcp_token_created", "MCP token created."),
        "success",
      );
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
      this._showToast(
        this._t("panel_mcp_token_revoked", "Token revoked."),
        "success",
      );
    } catch (err) {
      this._showToast("Failed to revoke token: " + err.message, "error");
    } finally {
      this._revokingTokenId = null;
      this.requestUpdate();
    }
  }

  // -------------------------------------------------------------------------
  // Command Approval Management
  // -------------------------------------------------------------------------

  async _loadApprovalGrants() {
    try {
      const result = await this.hass.callWS({
        type: "selora_ai/list_approvals",
      });
      this._approvalGrants = result.grants || [];
    } catch (err) {
      console.error("Failed to load approval grants", err);
    }
  }

  async _revokeApproval(grantKey) {
    // ``grantKey`` is the full identifier from list_approvals
    // ("service" or "service:entity_id"). Pass it back as ``key`` so
    // the server can target per-entity grants precisely without
    // tearing down a wildcard at the same time.
    this._revokingApprovalKey = grantKey;
    this.requestUpdate();
    try {
      await this.hass.callWS({
        type: "selora_ai/revoke_approval",
        key: grantKey,
      });
      await this._loadApprovalGrants();
      this._showToast(
        this._t("panel_approval_revoked", "Approval revoked."),
        "success",
      );
    } catch (err) {
      this._showToast("Failed to revoke approval: " + err.message, "error");
    } finally {
      this._revokingApprovalKey = null;
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
    return i18nLocalize(this.hass, key, fallback);
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

  firstUpdated() {
    // Native <select> dropdowns close if the element (or an ancestor) is
    // re-rendered while the popup is open. The panel re-renders on every
    // `hass` push and on the 1s quota tick, which intermittently collapsed
    // an open dropdown mid-click (e.g. the recipe wizard's dashboard picker).
    // Track open state via delegated, capture-phase listeners on the shadow
    // root and defer host updates (see ``shouldUpdate``) until it closes.
    // These live on this element's own shadow root, so they're GC'd with the
    // element — no teardown needed.
    const root = this.renderRoot;
    root.addEventListener(
      "mousedown",
      (e) => {
        if (e.target instanceof HTMLSelectElement && !e.target.disabled) {
          this._nativeSelectOpen = true;
          // Safety net: a missed close event must never freeze live updates.
          clearTimeout(this._nativeSelectTimer);
          this._nativeSelectTimer = setTimeout(
            () => this._closeNativeSelect(),
            8000,
          );
        }
      },
      true,
    );
    const onClose = (e) => {
      if (e.target instanceof HTMLSelectElement) this._closeNativeSelect();
    };
    root.addEventListener("change", onClose, true);
    root.addEventListener("focusout", onClose, true);
  }

  _closeNativeSelect() {
    clearTimeout(this._nativeSelectTimer);
    if (!this._nativeSelectOpen) return;
    this._nativeSelectOpen = false;
    // Flush any host update deferred while the dropdown was open.
    this.requestUpdate();
  }

  shouldUpdate(changedProps) {
    // Don't re-render while a native <select> dropdown is open — it would
    // collapse the popup. ``_closeNativeSelect`` calls requestUpdate() so the
    // latest state renders once the dropdown closes.
    if (this._nativeSelectOpen) return false;
    return super.shouldUpdate(changedProps);
  }

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
    // Reflect active LLM activity so CSS can pulse the header glow line.
    this.toggleAttribute("processing", !!(this._streaming || this._loading));
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
    // Hydrate entity chips emitted by the markdown renderer. Re-runs when
    // messages change (new chips appeared), hass changes (live state updates),
    // or the chat tab becomes active again (tab switch destroys and recreates
    // the chat DOM, so grids need re-wiring even if _messages didn't change).
    if (
      this.hass &&
      (changedProps.has("_messages") ||
        changedProps.has("hass") ||
        changedProps.has("_scenes") ||
        changedProps.has("_expandedScenes") ||
        changedProps.has("_sceneEdits") ||
        changedProps.has("_activeTab"))
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
    if (this._activeTab === "chat") {
      // Auto-stick to the latest message: when the user enters the chat tab,
      // switches sessions, or new content arrives (including each streaming
      // token, since assistant content grows in place), pin the scroll to the
      // bottom. Suppressed when the user has scrolled up to read earlier
      // messages — that's tracked by _chatScrolledAway via the scroll handler.
      const tabJustOpened =
        changedProps.has("_activeTab") && this._activeTab === "chat";
      // Only treat this as a session switch when the previous id was already
      // set — the backend fills in event.session_id on the first response of
      // a brand-new chat (null → assigned id), and that path must not clear
      // _chatScrolledAway out from under a user who scrolled up to read.
      const sessionChanged =
        changedProps.has("_activeSessionId") &&
        changedProps.get("_activeSessionId") != null;
      const messagesChanged = changedProps.has("_messages");
      const loadingChanged = changedProps.has("_loading");
      if (tabJustOpened || sessionChanged) {
        this._chatScrolledAway = false;
        this._pinChatToBottom();
      } else if (
        (messagesChanged || loadingChanged) &&
        !this._chatScrolledAway
      ) {
        this._pinChatToBottom();
      } else {
        this._refreshChatScrollState();
      }
    } else if (this._chatPinDeadline) {
      this._chatPinDeadline = 0;
    }
  }

  // Hold the chat scroll at the bottom for a short window after any change.
  // A single scroll-to-bottom in updated() is not enough: _hydrateEntityChips
  // appends HA tile cards asynchronously, those cards then settle their own
  // shadow DOM over several frames, markdown can lazy-render, etc. — all of
  // which push the latest content below the viewport after the initial
  // scroll has already happened. A rAF loop covering ~1.5s catches the
  // final layout reliably without depending on ResizeObserver firing for
  // every nested growth. Negligibly cheap: assigning scrollTop when it's
  // already at scrollHeight is a no-op in the browser.
  _pinChatToBottom(durationMs = 1500) {
    if (this._chatScrolledAway) return;
    const newDeadline = Date.now() + durationMs;
    if (this._chatPinDeadline) {
      if (newDeadline > this._chatPinDeadline) {
        this._chatPinDeadline = newDeadline;
      }
      return;
    }
    this._chatPinDeadline = newDeadline;
    const container = this.shadowRoot?.getElementById("chat-messages");
    let lastHeight = container ? container.scrollHeight : 0;
    let lastTop = container ? container.scrollTop : 0;
    const tick = () => {
      if (!this._chatPinDeadline) return;
      const c = this.shadowRoot?.getElementById("chat-messages");
      if (!c) {
        this._chatPinDeadline = 0;
        return;
      }
      // Distinguish user scroll from content reflow: if scrollTop dropped
      // but scrollHeight didn't grow (and didn't shrink — typing bubble
      // removal clamps scrollTop without user input), the user moved the
      // viewport themselves. Honour it and release the pin.
      const userScrolled =
        c.scrollTop < lastTop - 2 && c.scrollHeight === lastHeight;
      if (userScrolled) {
        this._chatScrolledAway = true;
        this._chatPinDeadline = 0;
        return;
      }
      c.scrollTop = c.scrollHeight;
      lastHeight = c.scrollHeight;
      lastTop = c.scrollTop;
      if (Date.now() >= this._chatPinDeadline) {
        this._chatPinDeadline = 0;
        return;
      }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  _refreshChatScrollState() {
    const container = this.shadowRoot?.getElementById("chat-messages");
    if (!container) {
      if (this._chatScrolledAway) this._chatScrolledAway = false;
      return;
    }
    const distance =
      container.scrollHeight - container.scrollTop - container.clientHeight;
    const next = distance > 80;
    if (this._chatScrolledAway !== next) {
      this._chatScrolledAway = next;
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

    for (const grid of grids) {
      // Lit may reuse a wired grid DOM node for a different scene/entity
      // (reorder, filter) and just swap data-entity-ids. Treat the grid
      // as un-wired when its ids no longer match what we built, so the
      // stale tile is rebuilt for the new entity.
      const wired =
        grid.dataset.wired === "true" &&
        grid.dataset.wiredIds === (grid.dataset.entityIds || "");

      // Scene grids carry ``data-scene-states`` — a map of entity_id to
      // the scene's *target* state. Feed the tiles a hass whose matching
      // states are overridden with those targets so each widget previews
      // the scene's desired values (brightness, position, volume, …) and
      // matches the scene YAML, rather than showing live device state.
      let gridHass = this.hass;
      if (grid.dataset.sceneStates) {
        try {
          const overrides = this._mergeSceneStates(
            JSON.parse(grid.dataset.sceneStates),
          );
          gridHass = {
            ...this.hass,
            states: { ...this.hass.states, ...overrides },
          };
        } catch (e) {
          console.warn("Selora: bad scene-states payload", e);
        }
      }

      // Editable scene-target tile: intercept service calls so adjusting
      // the tile mutates the scene's desired state (and prompts a save)
      // instead of driving the real device.
      const sceneEditId = grid.dataset.sceneEditId;
      if (sceneEditId) {
        // The grid is single-entity, so route by its own id rather than
        // digging entity_id out of the call: HA tile features pass the
        // entity in the 4th ``target`` arg (not ``data``), and some calls
        // carry no entity at all. data still holds service params
        // (brightness_pct, position, …) used to derive the new state.
        const editEntityId = grid.dataset.entityIds;
        gridHass = {
          ...gridHass,
          callService: (domain, service, data = {}) => {
            this._applySceneTileEdit(
              sceneEditId,
              editEntityId,
              domain,
              service,
              data,
            );
            return Promise.resolve();
          },
        };
      }

      if (!wired) {
        const ids = (grid.dataset.entityIds || "")
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
        // ``data-no-features="true"`` lets specific call sites
        // (currently the approval card) drop the tile's default
        // action row so users can't tap "unlock" inside an
        // "approve unlocking?" card and bypass the approval flow.
        const noFeatures = grid.dataset.noFeatures === "true";
        // Clear any prior fallback text so a retry (after the tile
        // creator becomes available) rebuilds cleanly instead of
        // appending duplicates.
        grid.replaceChildren();
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
            // Editable scene-target tiles disable the body tap so it
            // can't open more-info (which would control the real device).
            const card = createTile(id, {
              noFeatures,
              noActions: !!sceneEditId,
            });
            if (!card) return null;
            card.hass = gridHass;
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
              label.textContent =
                areaName || this._t("area_unassigned", "Unassigned");
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
        if (appended > 0) {
          // Only mark wired on success. Wiring on failure (creator not
          // ready, or all ids briefly missing from hass.states) would
          // lock the grid to the text fallback forever — the next pass
          // must be free to retry.
          grid.dataset.wired = "true";
          grid.dataset.wiredIds = grid.dataset.entityIds || "";
        } else {
          // Last-resort fallback so the message is still readable.
          grid.textContent = ids.join(", ");
        }
      }

      // Keep cards' hass current so brightness, on/off, etc. stay live.
      // hui-entities-card (and its inner rows) compare incoming hass by
      // reference and skip when unchanged. HA sometimes mutates the same
      // hass object instead of creating a new one, so a plain reassign
      // looks like "no change" and the card never re-renders. A shallow
      // copy guarantees a fresh reference; methods like callService
      // survive because they're own properties on hass.
      //
      // Scene-target grids are the exception: they pin the tile to the
      // scene's desired state, so they must NOT be re-fed live hass on
      // every tick. Doing so churns the tile and (for editable tiles)
      // lets it re-issue callService, an unbounded loop that freezes the
      // UI. Update them only when the target itself changes.
      if (grid.dataset.sceneStates) {
        // Include the edit-scene id in the signature: when Lit reuses a
        // tile for a different scene with the same entity and identical
        // target, the states JSON alone is unchanged, so without this
        // the card keeps the previous scene's intercepted callService
        // closure and edits would land on the wrong scene.
        const sig = `${grid.dataset.sceneEditId || ""}|${grid.dataset.sceneStates}`;
        if (grid.dataset.sceneSig !== sig) {
          grid.dataset.sceneSig = sig;
          for (const card of grid.children) {
            if (card.hass !== undefined) card.hass = { ...gridHass };
          }
        }
      } else {
        for (const card of grid.children) {
          if (card.hass !== undefined) card.hass = { ...gridHass };
        }
      }
    }
  }

  // Build a {entity_id: state} override map from a scene's target states.
  // Each override merges the entity's live state object (so the tile has
  // friendly_name, supported_features, etc.) with the scene's desired
  // state + attributes. Scene-only shorthands are normalised to the
  // attribute names HA tile features actually read (brightness 0-255,
  // current_position) so the rendered slider matches the scene YAML.
  _mergeSceneStates(sceneStates) {
    const overrides = {};
    for (const [id, target] of Object.entries(sceneStates || {})) {
      if (!target || typeof target !== "object") continue;
      const live = this.hass.states?.[id];
      if (!live) continue;
      const attrs = { ...(live.attributes || {}) };
      for (const [k, v] of Object.entries(target)) {
        if (k === "state") continue;
        attrs[k] = v;
      }
      if (target.brightness_pct != null && target.brightness == null) {
        attrs.brightness = Math.round(
          (Number(target.brightness_pct) / 100) * 255,
        );
        delete attrs.brightness_pct;
      }
      if (target.position != null && target.current_position == null) {
        attrs.current_position = Number(target.position);
      }
      // When the scene turns the entity OFF/closed/locked, the live
      // brightness/color/position attributes carried over above are
      // stale — an off light has no brightness. Strip them (unless the
      // scene explicitly set one) so the target tile renders fully off
      // instead of off-icon + the live "71%" bar.
      const stateStr = String(target.state).toLowerCase();
      const INACTIVE = [
        "off",
        "closed",
        "locked",
        "not_home",
        "standby",
        "idle",
      ];
      if (INACTIVE.includes(stateStr)) {
        for (const k of [
          "brightness",
          "brightness_pct",
          "color_temp",
          "color_temp_kelvin",
          "rgb_color",
          "rgbw_color",
          "rgbww_color",
          "hs_color",
          "xy_color",
          "effect",
          "current_position",
          "current_tilt_position",
        ]) {
          if (target[k] == null) delete attrs[k];
        }
      }
      overrides[id] = {
        ...live,
        state: String(target.state),
        attributes: attrs,
      };
    }
    return overrides;
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
    // Cache the creator only on success. A previous version cached
    // ``null`` on failure (helpers not ready / whenDefined timeout),
    // which permanently disabled tiles for the panel's lifetime — the
    // root cause of "sometimes the tiles never load". Now a failed load
    // leaves the cache empty so the next hydration pass retries. The
    // in-flight promise dedups concurrent callers during a single load.
    if (this._tileCardCreator) return this._tileCardCreator;
    if (this._tileCardCreatorPromise) return this._tileCardCreatorPromise;
    this._tileCardCreatorPromise = (async () => {
      const creator = await this._buildTileCardCreator();
      if (creator) this._tileCardCreator = creator;
      this._tileCardCreatorPromise = null;
      return creator;
    })();
    return this._tileCardCreatorPromise;
  }

  async _buildTileCardCreator() {
    // Map each entity domain to the most useful inline tile feature so
    // chat cards visually match HA's default dashboard tile and keep
    // domain-appropriate controls (brightness slider, volume slider,
    // cover arrows, climate target temp, etc.). Domains without a
    // standard feature (sensor, binary_sensor, switch, person, …) get
    // no extras — the tile's tap target is already the right action.
    // The grid CSS forces uniform row heights so mixed-feature tiles
    // don't render at different sizes side-by-side.
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
    // ``noFeatures`` suppresses the tile's built-in action row
    // (lock/unlock buttons on lock entities, open/close on covers, …).
    // The approval card uses this — embedding live action buttons
    // inside an "approve unlocking?" card would let the user bypass
    // the approval flow entirely by tapping the tile's own button.
    // ``noActions`` disables the tile body's tap/hold/double-tap (which
    // default to more-info). The scene editor uses this for the target
    // tile: more-info would drive the real device via the live hass,
    // breaking the promise that edits don't touch devices until Test or
    // activate. Feature controls (slider, arrows) stay live — they're
    // intercepted separately.
    const buildConfig = (
      id,
      { noFeatures = false, noActions = false } = {},
    ) => {
      const config = {
        type: "tile",
        entity: id,
        features: noFeatures ? [] : featuresForDomain(id),
      };
      if (noActions) {
        config.tap_action = { action: "none" };
        config.hold_action = { action: "none" };
        config.double_tap_action = { action: "none" };
      }
      return config;
    };

    // Path 1: HA's documented helper.
    if (typeof window.loadCardHelpers === "function") {
      try {
        const helpers = await window.loadCardHelpers();
        if (helpers && typeof helpers.createCardElement === "function") {
          this._tileCardCreator = (id, opts) =>
            helpers.createCardElement(buildConfig(id, opts));
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
        this._tileCardCreator = (id, opts) => {
          const el = document.createElement("hui-tile-card");
          el.setConfig(buildConfig(id, opts));
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

  _renderChat() {
    return renderChat(this);
  }

  _renderMessage(msg, idx) {
    return renderMessage(this, msg, idx);
  }

  _renderYamlEditor(key, originalYaml, onSave, opts) {
    return renderYamlEditor(this, key, originalYaml, onSave, opts);
  }

  _renderAutomationFlowchart(auto) {
    return renderAutomationFlowchart(this, auto);
  }

  _renderProposalCard(msg, msgIndex) {
    return renderProposalCard(this, msg, msgIndex);
  }

  _renderProposalActions(msg, msgIndex) {
    return renderProposalActions(this, msg, msgIndex);
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
    this._setActiveTab("chat");
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
      this._setActiveTab("chat");
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
    this._deviceDetail = {
      name: this._t("panel_device_loading", "Loading..."),
    };
    this._deviceDetailLoading = true;
    try {
      const result = await this.hass.connection.sendMessagePromise({
        type: "selora_ai/get_device_detail",
        device_id: deviceId,
      });
      this._deviceDetail = result;
    } catch (err) {
      this._deviceDetail = {
        name: this._t("panel_device_error_loading", "Error loading device"),
        error: err.message,
      };
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

  _renderRecipesV2() {
    return renderRecipesV2(this);
  }

  // ── Recipes (v2 pipeline) actions ──────────────────────────────
  //
  // All actions go directly to the deterministic pipeline's WS surface
  // — no chat session, no LLM. The wizard's three views (list / wizard
  // / result) are pure functions of the state these mutators maintain.

  async _loadRecipesList() {
    this._recipesBusy = true;
    try {
      const result = await this.hass.callWS({
        type: "selora_ai/recipes/list",
      });
      this._recipesList = {
        available: result.available || [],
        installed: result.installed || [],
      };
    } catch (err) {
      console.error("Failed to load recipes list", err);
      this._recipesList = { available: [], installed: [] };
    } finally {
      this._recipesBusy = false;
    }
    // Fire-and-forget the catalog fetch too. We don't gate the list
    // view on it — if the CDN is down or slow the local bundles
    // still render. Catalog appears under its own section once the
    // promise resolves.
    this._loadRecipesCatalog();
  }

  // Lazily read an installed recipe's package file (YAML + section counts) so
  // the Details panel can show what it created and let the user view the file.
  // Cached per slug; only refetched with force=true (e.g. after a rebind).
  async _loadRecipePackage(slug, force = false) {
    if (!slug) return;
    if (!force && this._recipePackages[slug]) return;
    try {
      const result = await this.hass.callWS({
        type: "selora_ai/recipes/package",
        slug,
      });
      this._recipePackages = {
        ...this._recipePackages,
        [slug]: { yaml: result.yaml || "", counts: result.counts || {} },
      };
    } catch (err) {
      console.error("Failed to read recipe package", slug, err);
    }
  }

  async _loadRecipesCatalog(force = false) {
    this._recipesCatalogError = null;
    if (!force && this._recipesCatalog?.recipes?.length) return;
    this._recipesCatalogBusy = true;
    // Honour a per-browser catalog URL override stored in localStorage.
    // Dev workflow: set this once to ``http://localhost:1313/api/recipes.json``
    // (or any staging URL) and the catalog fetches against it without
    // touching the HA env or restarting the integration.
    const override = this._catalogUrlOverride();
    try {
      const result = await this.hass.callWS({
        type: "selora_ai/recipes/catalog",
        force_refresh: !!force,
        ...(override ? { url: override } : {}),
      });
      this._recipesCatalog = {
        recipes: result.recipes || [],
        installed_slugs: new Set(result.installed_slugs || []),
        generated_at: result.generated_at || "",
      };
    } catch (err) {
      this._recipesCatalogError =
        err?.message || err?.error || String(err) || "Catalog unavailable";
      this._recipesCatalog = null;
    } finally {
      this._recipesCatalogBusy = false;
    }
  }

  _catalogUrlOverride() {
    try {
      return localStorage.getItem("selora-ai-catalog-url") || "";
    } catch {
      return "";
    }
  }

  _setCatalogUrlOverride(url) {
    try {
      if (url) {
        localStorage.setItem("selora-ai-catalog-url", url);
      } else {
        localStorage.removeItem("selora-ai-catalog-url");
      }
    } catch (err) {
      console.debug("Failed to persist catalog URL override", err);
    }
    this._loadRecipesCatalog(true);
  }

  // Substring search across the catalog (title + tags + description).
  // Pure render-time filter so we don't re-fetch on every keystroke.
  _filteredCatalog() {
    const cat = this._recipesCatalog;
    if (!cat) return [];
    const q = (this._recipesCatalogSearch || "").trim().toLowerCase();
    if (!q) return cat.recipes;
    return cat.recipes.filter((r) => {
      const hay = [
        r.title,
        r.description,
        r.category,
        r.category_title,
        ...(r.tags || []),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
  }

  _onRecipesCatalogSearch(value) {
    this._recipesCatalogSearch = value || "";
    // A new query changes the result set — jump back to the first page.
    this._catalogPage = 1;
  }

  _setCatalogPage(n) {
    this._catalogPage = Math.max(1, n);
  }

  // Install a recipe from a catalog entry — same backend path as
  // the "paste a URL" install card, just pre-filled.
  async _installFromCatalogEntry(entry) {
    if (!entry?.package_url || this._recipesBusy) return;
    this._recipesUrl = entry.package_url;
    await this._installRecipeFromUrl();
  }

  // Deep-link entry point (marketing site → /selora-ai/recipes/<slug>).
  // The bundle may not be on disk yet — if the slug isn't staged
  // locally, fetch it from the catalog first so the wizard opens on
  // the Overview instead of failing to load the manifest.
  async _openRecipeFromDeepLink(slug) {
    const staged = (this._recipesList?.available || []).some(
      (r) => r.slug === slug,
    );
    if (staged) {
      this._openRecipeWizard(slug);
      return;
    }
    // Not on disk — stage it from the catalog by slug.
    await this._loadRecipesCatalog();
    const entry = (this._recipesCatalog?.recipes || []).find(
      (r) => r.slug === slug,
    );
    if (entry?.package_url) {
      // _installFromCatalogEntry stages the tarball then opens the
      // wizard for the staged slug on success.
      await this._installFromCatalogEntry(entry);
      return;
    }
    // Unknown slug — open anyway so the wizard surfaces the load error
    // rather than silently doing nothing.
    this._openRecipeWizard(slug);
  }

  async _openRecipeWizard(slug) {
    this._recipesView = "wizard";
    this._recipeWizardSlug = slug;
    // Reflect the wizard's slug in the URL so reloading mid-install
    // restores the same wizard instead of bouncing back to the list.
    this._setRecipeWizardUrl?.(slug);
    this._recipeWizardDetail = null;
    this._recipeWizardPreview = null;
    this._recipeWizardResult = null;
    this._recipeWizardInputs = {};
    this._recipeWizardSelections = {};
    this._recipeActiveItemId = null;
    this._recipeFlows = {};
    this._recipeWizardStep = 1;
    this._recipeWizardResult = null;
    // Reset the dashboard picker to the manifest default for this recipe.
    this._recipeDashboardTarget = undefined;
    this._recipeDashboards = [];
    // Auto-advance hook: when a device pairs (new entity appears in
    // the registry) we re-run preview without the user clicking
    // anything. Cheap subscription, lives for as long as the wizard
    // is open.
    this._subscribeRecipeEntityRegistry();
    this._recipesBusy = true;
    try {
      const detail = await this.hass.callWS({
        type: "selora_ai/recipes/get",
        slug,
      });
      this._recipeWizardDetail = detail;
      // Recipes that declare a dashboard card need the writable-dashboard
      // list for the "which dashboard?" picker. Fetch once, lazily.
      if (detail.manifest?.dashboard) {
        this._fetchRecipeDashboards();
      }
      // Seed wizard inputs with manifest defaults so the form has
      // sensible starting values and the preview WS sees concrete data
      // on the first call. The user can override before installing.
      const seeded = {};
      for (const input of detail.manifest?.inputs || []) {
        if (input.default !== undefined && input.default !== null) {
          seeded[input.id] = input.default;
        }
      }
      this._recipeWizardInputs = seeded;
      // Seed selections: every ``selection: required`` role starts
      // with an empty pick list so the role row shows toggles but
      // the install button stays disabled until the user picks.
      // ``selection: auto`` roles are intentionally absent — the
      // backend resolver fills them in for us.
      const seededSelections = {};
      for (const role of detail.manifest?.roles || []) {
        if (role.selection === "required") {
          seededSelections[role.id] = [];
        }
      }
      this._recipeWizardSelections = seededSelections;
      // Restore any persisted state for this slug — picks the user
      // made before navigating away (e.g. to pair a device in HA
      // settings) come back exactly where they left off. Seeded
      // defaults above are overwritten only for fields the user has
      // actually touched.
      const persisted = this._restoreWizardState(slug);
      if (persisted) {
        if (persisted.inputs && typeof persisted.inputs === "object") {
          this._recipeWizardInputs = {
            ...this._recipeWizardInputs,
            ...persisted.inputs,
          };
        }
        if (persisted.selections && typeof persisted.selections === "object") {
          this._recipeWizardSelections = {
            ...this._recipeWizardSelections,
            ...persisted.selections,
          };
        }
        if (
          typeof persisted.step === "number" &&
          persisted.step >= 1 &&
          persisted.step <= 3
        ) {
          // Restore only to safe pre-install steps. Steps 4 (live
          // install) and 5 (post-install activate) depend on
          // ephemeral state (stream, result) that we don't persist,
          // so any value above 3 maps back to 3.
          this._recipeWizardStep = persisted.step;
        }
      }
    } catch (err) {
      console.error("Failed to load recipe detail", err);
    } finally {
      this._recipesBusy = false;
    }
    // First preview pass — kicks off role resolution against the
    // live home so the wizard renders bindings + any punch list
    // immediately.
    await this._refreshRecipePreview();
  }

  // Toggle one entity in/out of a role's selection. Called from the
  // wizard's per-role chip row. Triggers a fresh preview so role
  // status + YAML stay in sync with the new pick.
  //
  // ``maxCount`` enforces the manifest's cap client-side. Without it
  // the chip stays visually "on" but the backend silently drops the
  // overflow when capping at max_count, which looks like a bug to
  // the user. With it, adding past the cap rolls the oldest pick out
  // of the selection (so max_count=1 behaves like radio buttons).
  _toggleRecipeRoleEntity(roleId, entityId, maxCount) {
    const current = (this._recipeWizardSelections || {})[roleId] || [];
    const idx = current.indexOf(entityId);
    let next;
    if (idx >= 0) {
      next = current.filter((e) => e !== entityId);
    } else if (maxCount && current.length >= maxCount) {
      // Rolling window: drop oldest picks to make room for the new
      // one. Keeps the last (maxCount - 1) picks + the new id.
      const keep = Math.max(0, maxCount - 1);
      next = [...current.slice(current.length - keep), entityId];
    } else {
      next = [...current, entityId];
    }
    this._recipeWizardSelections = {
      ...this._recipeWizardSelections,
      [roleId]: next,
    };
    // Anchor the action panel to this role while the user is picking.
    // Without it, _activeItem's fallback can drift to a different
    // role once this one flips to ok (e.g. a multi-select role where
    // the user wants to pick a second device for the SAME role).
    this._recipeActiveItemId = `configure/role:${roleId}`;
    this._persistWizardState();
    // Re-run preview so role .ok status + YAML reflect the pick.
    // Cheap WS call; skip if a request is already in flight to avoid
    // pile-up while the user clicks several chips in a row.
    if (!this._recipesBusy) this._refreshRecipePreview();
  }

  _setRecipeRoleFilter(roleId, value) {
    this._recipeRoleFilters = {
      ...this._recipeRoleFilters,
      [roleId]: value,
    };
  }

  _toggleRecipeRoleExpanded(roleId) {
    this._recipeRoleExpanded = {
      ...this._recipeRoleExpanded,
      [roleId]: !this._recipeRoleExpanded?.[roleId],
    };
  }

  _closeRecipeWizard() {
    // Clearing on explicit close — the user chose to abandon the
    // wizard, not navigate away temporarily. Reloads / device-pair
    // detours keep the saved state intact; only Cancel + finished
    // install + uninstall purge it.
    this._clearWizardState(this._recipeWizardSlug);
    this._recipesView = "list";
    this._recipeWizardSlug = null;
    this._setRecipeWizardUrl?.(null);
    this._recipeWizardDetail = null;
    this._recipeWizardInputs = {};
    this._recipeWizardSelections = {};
    this._recipeWizardPreview = null;
    this._recipeWizardResult = null;
    this._recipeActiveItemId = null;
    this._recipeFlows = {};
    this._recipeWizardStep = 1;
    this._unsubscribeRecipeEntityRegistry();
  }

  // ── Wizard state persistence (localStorage) ─────────────────────
  // Saves the user's in-progress wizard state (step, typed inputs,
  // role selections) so reload / navigating away to pair a device
  // doesn't force them to start over. Per-slug keys so different
  // recipes have independent state. We don't persist:
  // - ``_recipeWizardPreview``: fetched fresh on every open
  // - ``_recipeWizardResult``: only meaningful during/after install
  // - ``_recipeFlows``: inline config-flow state is per-session
  //
  // We DO persist step but cap restoration at 3 (pre-install). Steps
  // 4 (live install stream) and 5 (post-install summary) carry
  // ephemeral state we don't try to reconstruct.

  _wizardStorageKey(slug) {
    return `selora-ai-wizard:${slug}`;
  }

  // Update a single recipe input by id and persist. Called from the
  // Step 2 settings form so input changes survive a page reload.
  _updateRecipeInput(id, value) {
    this._recipeWizardInputs = {
      ...this._recipeWizardInputs,
      [id]: value,
    };
    this._persistWizardState();
  }

  _persistWizardState() {
    const slug = this._recipeWizardSlug;
    if (!slug) return;
    try {
      const payload = JSON.stringify({
        slug,
        step: this._recipeWizardStep,
        inputs: this._recipeWizardInputs,
        selections: this._recipeWizardSelections,
        savedAt: Date.now(),
      });
      localStorage.setItem(this._wizardStorageKey(slug), payload);
    } catch (err) {
      // localStorage can throw (quota, private mode). Best-effort —
      // a missed persist just means the next reload starts fresh.
      console.debug("Failed to persist wizard state", err);
    }
  }

  _restoreWizardState(slug) {
    if (!slug) return null;
    try {
      const raw = localStorage.getItem(this._wizardStorageKey(slug));
      if (!raw) return null;
      const state = JSON.parse(raw);
      if (!state || state.slug !== slug) return null;
      // Stale state (older than 24h) gets purged. Long enough for the
      // homeowner to leave and pair a device; short enough that
      // "yesterday's abandoned wizard" doesn't haunt them.
      const TTL_MS = 24 * 60 * 60 * 1000;
      if (state.savedAt && Date.now() - state.savedAt > TTL_MS) {
        localStorage.removeItem(this._wizardStorageKey(slug));
        return null;
      }
      return state;
    } catch (err) {
      console.debug("Failed to restore wizard state", err);
      return null;
    }
  }

  _clearWizardState(slug) {
    const target = slug || this._recipeWizardSlug;
    if (!target) return;
    try {
      localStorage.removeItem(this._wizardStorageKey(target));
    } catch (err) {
      console.debug("Failed to clear wizard state", err);
    }
  }

  // Lightweight check used by the recipes list to flag cards with an
  // in-progress wizard. Returns the saved step (1–5) if one exists,
  // or 0 if nothing is saved. We expose the step rather than just a
  // boolean so the card UI can render "Resume on Step N" hints.
  _wizardDraftStep(slug) {
    const state = this._restoreWizardState(slug);
    return state?.step || 0;
  }

  // "Start over" affordance on a draft card. Wipes the saved state
  // and opens the wizard from Step 1. Bumps the panel to trigger a
  // re-render so the card reflects the new (no-draft) state if the
  // user immediately closes the wizard.
  _discardRecipeDraft(slug) {
    if (!slug) return;
    this._clearWizardState(slug);
    // Force a re-render so the card flips from Resume → Install.
    // ``requestUpdate`` is the Lit-native nudge for non-reactive
    // changes (localStorage isn't tracked by Lit).
    this.requestUpdate?.();
  }

  // ── 4-step wizard navigation ─────────────────────────────────────
  // Linear flow: Overview → Match → Resolve → Activate. Each step
  // gates the next via ``_canAdvanceFromStep``. Step 3 kicks off the
  // install stream automatically on entry; on stream completion the
  // wizard auto-advances to step 4.

  _canAdvanceFromStep(step) {
    const preview = this._recipeWizardPreview;
    if (step === 1) {
      // Overview is read-only — always advanceable as long as we
      // have a detail/preview to show.
      return !!this._recipeWizardDetail;
    }
    if (step === 2) {
      // Settings step: only input validation blocks. Don't gate on
      // ``preview.ok`` here — that flips false whenever ANY other
      // item still needs attention (e.g. a pending device pair),
      // even though those are Step 3 / Step 4 concerns. Step 2 only
      // cares about whether the recipe's own inputs validate.
      const items = preview?.items || [];
      const badInputs = items.some(
        (it) => it.kind === "inputs" && it.status === "needs_input",
      );
      // Also block on input_invalid punch items in case the preview
      // hasn't produced an items list yet (race on first open).
      const inputPunch = (preview?.punch_list || []).some(
        (p) => p.code === "input_invalid",
      );
      return !badInputs && !inputPunch;
    }
    if (step === 3) {
      // Match step requires every non-pin item to be resolved. Pins
      // (device pairs) can remain pending — they show as interrupts
      // in step 4 with auto-advance via entity_registry events.
      // We REQUIRE a loaded preview here: if it never arrived (initial
      // race, WS error, etc.) the gating used to fail-open because
      // every check looked at an empty items list. Fail-closed now.
      if (!preview) return false;
      if (preview.ok === false) return false;
      const items = preview.items || [];
      const blockingNeedsInput = items.some(
        (it) =>
          it.status === "needs_input" &&
          it.kind !== "pin" &&
          it.kind !== "inputs" &&
          it.stage === "configure",
      );
      // Also block on definition/resolve/validate punch items.
      const earlyPunch = (preview.punch_list || []).some(
        (p) => p.stage !== "resolve" || p.code !== "binding_pending",
      );
      return !blockingNeedsInput && !earlyPunch;
    }
    if (step === 4) {
      // Resolve step advances when install finishes successfully.
      return !!this._recipeWizardResult?.ok;
    }
    return false;
  }

  _recipeHasMatchStep() {
    // True when Step 3 has anything to do — a role to pick, an
    // integration to set up, or a device to pair. Recipes that create
    // only helpers (no device roles) have nothing here.
    const items = this._recipeWizardPreview?.items || [];
    return items.some(
      (it) =>
        it.stage === "configure" &&
        (it.kind === "role_selection" ||
          it.kind === "integration" ||
          it.kind === "pin"),
    );
  }

  async _advanceRecipeStep() {
    const current = this._recipeWizardStep || 1;
    if (!this._canAdvanceFromStep(current)) return;
    let next = current + 1;
    // Skip the Match step entirely when there's nothing to match — but
    // only if the preview is healthy. If it isn't (e.g. a render error),
    // we still land on Step 3 so it can show the actual blocker rather
    // than silently dropping the user into a doomed install.
    if (
      next === 3 &&
      !this._recipeHasMatchStep() &&
      this._canAdvanceFromStep(3)
    ) {
      next = 4;
    }
    this._recipeWizardStep = next;
    this._recipeActiveItemId = null;
    this._persistWizardState();
    // Step is intentionally NOT written to the URL — per-step state
    // (typed inputs, picked devices, install stream result) lives
    // only in memory, so restoring to Step 3+ on reload would show a
    // half-empty form. Reloads always land at Step 1.
    // Entering Step 4 (Set up) auto-runs the install stream so the
    // user sees the buckets fill in real time. We deliberately do NOT
    // auto-advance to Step 5 — the install finishes faster than the
    // user can read the "Completed" rows, and a silent jump to Step 5
    // makes it feel like nothing happened. The user clicks Continue
    // on Step 4 themselves when they've seen the result.
    if (next === 4) {
      await this._runRecipeInstall();
    }
  }

  _retreatRecipeStep() {
    const current = this._recipeWizardStep || 1;
    if (current <= 1) return;
    // Don't allow going back from Step 4 mid-install or from Step 5
    // (install already on disk; can't be un-applied without uninstall).
    if (current === 4 && this._recipesBusy) return;
    if (current === 5) return;
    let prev = current - 1;
    // Mirror the forward skip: don't strand the user on an empty Match
    // step when going back from Set up.
    if (prev === 3 && !this._recipeHasMatchStep()) prev = 2;
    this._recipeWizardStep = prev;
    this._recipeActiveItemId = null;
    this._persistWizardState();
  }

  _jumpToRecipeStep(step) {
    // Only allow jumping to a completed step (lower number) or the
    // current one. Forward jumps must go through _advanceRecipeStep so
    // gating is honoured.
    if (step < 1 || step > 5) return;
    if (step > (this._recipeWizardStep || 1)) return;
    if (step === 5 && !this._recipeWizardResult?.ok) return;
    this._recipeWizardStep = step;
    this._recipeActiveItemId = null;
    this._persistWizardState();
  }

  // ── v3 prototype: Manage Devices modal ───────────────────────────
  // Lets the user swap which entities back a v3 recipe's roles without
  // re-running the install wizard. Backend call is
  // ``selora_ai/recipes/rebind`` which rewrites only the ``group:``
  // block in the installed package YAML and reloads core config.

  async _openManageDevices(slug) {
    this._recipeManageSlug = slug;
    this._recipeManageBusy = true;
    this._recipeManageError = null;
    this._recipeManageDetail = null;
    this._recipeManageSelections = {};
    try {
      const detail = await this.hass.callWS({
        type: "selora_ai/recipes/get",
        slug,
      });
      this._recipeManageDetail = detail;
      // Seed selections from the installed record's bindings so the
      // user sees the current state as the starting point.
      const current = detail?.installed?.bindings || {};
      const seeded = {};
      for (const role of detail.manifest?.roles || []) {
        seeded[role.id] = [...(current[role.id] || [])];
      }
      this._recipeManageSelections = seeded;
    } catch (err) {
      this._recipeManageError =
        err?.message || err?.error || String(err) || "Failed to load recipe";
    } finally {
      this._recipeManageBusy = false;
    }
  }

  _closeManageDevices() {
    this._recipeManageSlug = null;
    this._recipeManageDetail = null;
    this._recipeManageSelections = {};
    this._recipeManageError = null;
  }

  _toggleManageEntity(roleId, entityId) {
    // Mirror the wizard's max_count rolling-window behaviour so the
    // user can't pick more than the manifest allows.
    const role = (this._recipeManageDetail?.manifest?.roles || []).find(
      (r) => r.id === roleId,
    );
    const current = this._recipeManageSelections[roleId] || [];
    const idx = current.indexOf(entityId);
    let next;
    if (idx >= 0) {
      next = current.filter((e) => e !== entityId);
    } else if (role?.max_count && current.length >= role.max_count) {
      const keep = Math.max(0, role.max_count - 1);
      next = [...current.slice(current.length - keep), entityId];
    } else {
      next = [...current, entityId];
    }
    this._recipeManageSelections = {
      ...this._recipeManageSelections,
      [roleId]: next,
    };
  }

  async _saveManageDevices() {
    if (!this._recipeManageSlug) return;
    this._recipeManageBusy = true;
    this._recipeManageError = null;
    try {
      await this.hass.callWS({
        type: "selora_ai/recipes/rebind",
        slug: this._recipeManageSlug,
        selections: this._recipeManageSelections,
      });
      await this._loadRecipesList();
      this._closeManageDevices();
    } catch (err) {
      this._recipeManageError =
        err?.message ||
        err?.error ||
        String(err) ||
        "Failed to update bindings";
    } finally {
      this._recipeManageBusy = false;
    }
  }

  // ── Inline HA config-flow integration ────────────────────────────
  // HA's config-flow endpoints are REST, NOT WebSocket (despite the
  // similar ``config_entries/...`` naming used for some other commands).
  // We use ``hass.callApi`` — the same helper HA's own frontend uses
  // for config-flow UI — so auth and CORS are handled the same way.
  // The flow lives inside the wizard so the user never leaves the
  // page: kick it off, walk through each ``form`` step, and on
  // ``create_entry`` refresh the preview so the integration row
  // flips green.

  async _startIntegrationFlow(domain) {
    if (!domain) return;
    this._recipesBusy = true;
    try {
      const step = await this.hass.callApi(
        "POST",
        "config/config_entries/flow",
        { handler: domain, show_advanced_options: false },
      );
      this._recipeFlows = {
        ...(this._recipeFlows || {}),
        [domain]: {
          flow_id: step.flow_id,
          step,
          values: {},
          state: this._flowStateFromStep(step),
          error: step.type === "abort" ? step.reason || "Flow aborted" : null,
        },
      };
      if (step.type === "create_entry") {
        // Some integrations create the entry on init (no form needed).
        await this._refreshRecipePreview();
      }
    } catch (err) {
      this._recipeFlows = {
        ...(this._recipeFlows || {}),
        [domain]: {
          state: "error",
          error:
            err?.body?.message || err?.message || err?.error || String(err),
        },
      };
    } finally {
      this._recipesBusy = false;
    }
  }

  async _submitIntegrationFlow(domain) {
    const flow = (this._recipeFlows || {})[domain];
    if (!flow?.flow_id) return;
    this._recipesBusy = true;
    try {
      const step = await this.hass.callApi(
        "POST",
        `config/config_entries/flow/${flow.flow_id}`,
        flow.values || {},
      );
      this._recipeFlows = {
        ...(this._recipeFlows || {}),
        [domain]: {
          ...flow,
          step,
          flow_id: step.flow_id || flow.flow_id,
          state: this._flowStateFromStep(step),
          error: step.type === "abort" ? step.reason || "Flow aborted" : null,
          // Keep values when the same form re-renders with errors;
          // clear them when stepping forward to a new form so the
          // next step starts blank.
          values: step.type === "form" && step.errors ? flow.values : {},
        },
      };
      if (step.type === "create_entry") {
        await this._refreshRecipePreview();
      }
    } catch (err) {
      this._recipeFlows = {
        ...(this._recipeFlows || {}),
        [domain]: {
          ...flow,
          state: "error",
          error:
            err?.body?.message || err?.message || err?.error || String(err),
        },
      };
    } finally {
      this._recipesBusy = false;
    }
  }

  // One-click setup for integrations whose manifest declares
  // ``auto_setup`` — backend orchestrates the entire config flow
  // using values the recipe knows + values resolvable from HA
  // state (lat/lon, METAR from coordinates, etc.). No form rendered;
  // the homeowner just sees a brief "working…" state and then the
  // integration row flips to Configured.
  async _autoSetupIntegration(domain) {
    if (!domain || !this._recipeWizardSlug) return;
    this._recipesBusy = true;
    try {
      await this.hass.callWS({
        type: "selora_ai/recipes/auto_setup_integration",
        slug: this._recipeWizardSlug,
        domain,
      });
      // Refresh preview so the integration row flips status.
      await this._refreshRecipePreview();
    } catch (err) {
      // Surface as if the flow had errored — same UI state the user
      // would see for a manual flow failure.
      this._recipeFlows = {
        ...(this._recipeFlows || {}),
        [domain]: {
          state: "error",
          error:
            err?.message || err?.error || err?.body?.message || String(err),
        },
      };
    } finally {
      this._recipesBusy = false;
    }
  }

  async _abortIntegrationFlow(domain) {
    const flow = (this._recipeFlows || {})[domain];
    if (flow?.flow_id) {
      try {
        await this.hass.callApi(
          "DELETE",
          `config/config_entries/flow/${flow.flow_id}`,
        );
      } catch (err) {
        // ``abort`` is best-effort — if HA already finalised the flow
        // there's nothing to undo, so silently drop the error.
        console.debug("Flow abort ignored", err);
      }
    }
    this._resetIntegrationFlow(domain);
  }

  _resetIntegrationFlow(domain) {
    const next = { ...(this._recipeFlows || {}) };
    delete next[domain];
    this._recipeFlows = next;
  }

  _flowStateFromStep(step) {
    if (!step) return "error";
    if (step.type === "form") return "form";
    if (step.type === "create_entry") return "complete";
    if (step.type === "abort") return "error";
    // ``external_step``, ``progress``, ``menu`` — surface as running.
    return "running";
  }

  // ── Auto-advance on entity-registry events ───────────────────────
  // When a pending pin's ``entity_id`` appears in the registry while
  // the wizard is open, just re-run preview — the pin row flips from
  // ``needs_input`` to ``ok`` without the user clicking anything.

  async _subscribeRecipeEntityRegistry() {
    this._unsubscribeRecipeEntityRegistry();
    if (!this.hass?.connection) return;
    try {
      this._recipeEntityRegistryUnsub =
        await this.hass.connection.subscribeEvents(() => {
          // Debounce a touch — pairing flows often fire 3-4 events
          // back-to-back as device + entities materialise.
          if (this._recipeEntityRegistryTimer) {
            clearTimeout(this._recipeEntityRegistryTimer);
          }
          this._recipeEntityRegistryTimer = setTimeout(() => {
            this._recipeEntityRegistryTimer = null;
            if (this._recipesView === "wizard" && !this._recipesBusy) {
              this._refreshRecipePreview();
            }
          }, 350);
        }, "entity_registry_updated");
    } catch (err) {
      console.debug("entity_registry subscribe failed", err);
    }
  }

  _unsubscribeRecipeEntityRegistry() {
    if (this._recipeEntityRegistryTimer) {
      clearTimeout(this._recipeEntityRegistryTimer);
      this._recipeEntityRegistryTimer = null;
    }
    if (this._recipeEntityRegistryUnsub) {
      try {
        this._recipeEntityRegistryUnsub();
      } catch (err) {
        console.debug("entity_registry unsub failed", err);
      }
      this._recipeEntityRegistryUnsub = null;
    }
  }

  async _refreshRecipePreview() {
    if (!this._recipeWizardSlug) return;
    this._recipesBusy = true;
    try {
      const preview = await this.hass.callWS({
        type: "selora_ai/recipes/preview",
        slug: this._recipeWizardSlug,
        inputs: this._recipeWizardInputs || {},
        selections: this._recipeWizardSelections || {},
      });
      this._recipeWizardPreview = preview;
    } catch (err) {
      console.error("Failed to preview recipe", err);
      this._recipeWizardPreview = {
        ok: false,
        stage_reached: "definition",
        punch_list: [
          {
            stage: "definition",
            code: "preview_failed",
            message: err?.message || String(err),
          },
        ],
        bindings: {},
      };
    } finally {
      this._recipesBusy = false;
    }
  }

  async _fetchRecipeDashboards() {
    try {
      const res = await this.hass.callWS({
        type: "selora_ai/recipes/list_dashboards",
      });
      this._recipeDashboards = res?.dashboards || [];
    } catch (err) {
      console.debug("list_dashboards failed", err);
      // Don't clobber a previously-fetched list on a transient failure
      // (e.g. the WS call racing an HA reload). Only seed an empty array
      // when we have nothing at all.
      if (!Array.isArray(this._recipeDashboards)) this._recipeDashboards = [];
    }
  }

  _setRecipeDashboardTarget(value) {
    // "" from the picker maps to the default dashboard (url_path null).
    this._recipeDashboardTarget = value === "" ? null : value;
  }

  // Step 5 "Add card" action: place the recipe's manifest dashboard card
  // onto the chosen dashboard after the install already ran. Resolves the
  // target to the first writable dashboard when the user hasn't picked
  // one, so the visibly-selected option is what actually gets written.
  async _insertRecipeDashboardCard() {
    const slug = this._recipeWizardSlug;
    if (!slug || this._recipesBusy) return;
    const dashboards = this._recipeDashboards || [];
    let target = this._recipeDashboardTarget;
    if (target === undefined) {
      target = dashboards.length ? (dashboards[0].url_path ?? null) : null;
    }
    this._recipesBusy = true;
    try {
      const res = await this.hass.callWS({
        type: "selora_ai/recipes/insert_dashboard_card",
        slug,
        target,
      });
      // Reflect the placement in the wizard result so the outcome card
      // flips to the "added" state on the next render.
      if (this._recipeWizardResult?.record) {
        this._recipeWizardResult = {
          ...this._recipeWizardResult,
          record: {
            ...this._recipeWizardResult.record,
            dashboard_card: res,
          },
        };
      }
      if (!res?.ok) {
        this._showToast(
          this._t(
            "recipes_dashboard_add_failed",
            "Couldn't add the card to that dashboard.",
          ),
          "error",
        );
      }
    } catch (err) {
      console.error("insert_dashboard_card failed", err);
      this._showToast(
        this._t(
          "recipes_dashboard_add_failed",
          "Couldn't add the card to that dashboard.",
        ),
        "error",
      );
    } finally {
      this._recipesBusy = false;
    }
  }

  async _runRecipeInstall() {
    if (!this._recipeWizardSlug) return;
    this._recipesBusy = true;
    // Live-flip Apply rows as the backend reports each step. We do
    // this by mutating the preview's ``items`` list in place — the
    // pipeline view re-reads it on every render so the column repaints
    // with the new statuses without any extra plumbing.
    const updateApplyItem = (step, status, detail) => {
      const items = this._recipeWizardPreview?.items;
      if (!items) return;
      const idx = items.findIndex((it) => it.id === `apply/${step}`);
      if (idx < 0) return;
      const next = [...items];
      next[idx] = {
        ...next[idx],
        status,
        ...(detail !== undefined ? { detail } : {}),
      };
      this._recipeWizardPreview = {
        ...this._recipeWizardPreview,
        items: next,
      };
      // Focus the running step so the action panel narrates progress.
      if (status === "running") {
        this._recipeActiveItemId = `apply/${step}`;
      }
    };

    let finalResult = null;
    let unsub = null;
    try {
      await new Promise((resolve, reject) => {
        // ``subscribeMessage`` matches HA's WS connection API: each
        // event from the server triggers the callback; the returned
        // promise resolves with the unsubscribe fn. The "result"
        // event ends the stream — we close the subscription and let
        // the outer await resolve.
        this.hass.connection
          .subscribeMessage(
            (evt) => {
              const payload = evt?.event;
              if (!payload) return;
              if (payload.type === "apply") {
                updateApplyItem(payload.step, payload.status, payload.detail);
              } else if (payload.type === "result") {
                finalResult = payload.result;
                resolve();
              }
            },
            {
              type: "selora_ai/recipes/install_stream",
              slug: this._recipeWizardSlug,
              inputs: this._recipeWizardInputs || {},
              selections: this._recipeWizardSelections || {},
              // The dashboard-card offer moved to Step 5 (post-install),
              // so the install never auto-inserts — "__skip__" tells the
              // pipeline to leave the card to the Step 5 "Add card" action.
              dashboard_target: "__skip__",
            },
          )
          .then((u) => {
            unsub = u;
          })
          .catch(reject);
      });
      // Stay inside the wizard. The 4-step flow renders the result
      // as Step 5 (Activate); the legacy ``result`` view is reached
      // only when the user clicks [Activate recipe] in Step 5.
      this._recipeWizardResult = finalResult;
      // Install landed — the saved wizard state has done its job.
      // Clear it so a re-install of the same recipe starts clean.
      if (finalResult?.ok) {
        this._clearWizardState(this._recipeWizardSlug);
        // Reset the picker selection for Step 5. We deliberately do NOT
        // re-fetch dashboards here: a recipe install never changes the
        // dashboard set, and a refetch racing the HA reload can fail and
        // wipe the good list captured at wizard open.
        this._recipeDashboardTarget = undefined;
      }
    } catch (err) {
      console.error("Recipe install failed", err);
      this._recipeWizardResult = {
        ok: false,
        stage_reached: "definition",
        punch_list: [
          {
            stage: "definition",
            code: "install_failed",
            message: err?.message || String(err),
          },
        ],
      };
    } finally {
      if (unsub) {
        try {
          unsub();
        } catch (e) {
          console.debug("install_stream unsub failed", e);
        }
      }
      this._recipesBusy = false;
    }
  }

  async _installRecipeFromUrl() {
    // Fetch + extract + validate manifest. The bundle lands in
    // <config>/selora_ai_recipes/<slug>/ but the install pipeline
    // doesn't run until the user opens the wizard. Auto-opens the
    // wizard for the just-fetched recipe so the workflow continues
    // without an extra click.
    const url = (this._recipesUrl || "").trim();
    if (!url || this._recipesUrlBusy) return;
    this._recipesUrlBusy = true;
    this._recipesInstallError = null;
    try {
      const staged = await this.hass.callWS({
        type: "selora_ai/recipes/install_from_url",
        url,
      });
      this._recipesUrl = "";
      await this._loadRecipesList();
      if (staged?.slug) this._openRecipeWizard(staged.slug);
    } catch (err) {
      this._recipesInstallError =
        err?.message || err?.error || String(err) || "Unknown fetch error";
    } finally {
      this._recipesUrlBusy = false;
    }
  }

  async _uploadRecipeArchive(file) {
    // POSTs the file to the integration's upload endpoint. The endpoint
    // does the same extract + validate + stage as the URL path; on
    // success we open the wizard for the staged recipe.
    if (!file || this._recipesUploadBusy) return;
    this._recipesUploadBusy = true;
    this._recipesInstallError = null;
    try {
      const form = new FormData();
      form.append("file", file, file.name);
      const auth = this.hass?.auth?.accessToken
        ? { Authorization: `Bearer ${this.hass.auth.accessToken}` }
        : {};
      const resp = await fetch("/api/selora_ai/recipes/upload", {
        method: "POST",
        headers: auth,
        body: form,
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        this._recipesInstallError =
          payload?.message || `Upload failed (HTTP ${resp.status})`;
        return;
      }
      await this._loadRecipesList();
      if (payload?.slug) this._openRecipeWizard(payload.slug);
    } catch (err) {
      this._recipesInstallError =
        err?.message || String(err) || "Upload failed";
    } finally {
      this._recipesUploadBusy = false;
    }
  }

  // Two-step uninstall: the button on the recipe row sets the pending
  // slug, which surfaces a confirmation modal. The destructive action
  // only runs after the user confirms (clicks Uninstall or hits Enter).
  // Without the gate the click goes straight to delete-and-reload,
  // which is too easy to do accidentally for a destructive operation.
  _uninstallRecipe(slug) {
    if (!slug || this._recipesBusy) return;
    this._recipeUninstallPending = slug;
    // Reset entry selections — default UNCHECKED for every integration
    // the recipe installed (user explicitly opts in to removal).
    this._recipeUninstallEntries = {};
  }

  _cancelRecipeUninstall() {
    this._recipeUninstallPending = null;
    this._recipeUninstallEntries = {};
  }

  // Toggle one integration entry's "also remove" checkbox in the
  // uninstall confirm modal.
  _toggleUninstallEntry(entryId) {
    const next = { ...(this._recipeUninstallEntries || {}) };
    if (next[entryId]) {
      delete next[entryId];
    } else {
      next[entryId] = true;
    }
    this._recipeUninstallEntries = next;
  }

  // For each domain the recipe installed, list OTHER installed recipes
  // that also declare that domain. The uninstall modal uses this to
  // show "still used by X" warnings so the user doesn't accidentally
  // break another recipe by removing a shared integration.
  _otherUsersOfDomain(domain, exceptSlug) {
    const installed = this._recipesList?.installed || [];
    const available = this._recipesList?.available || [];
    const titleBySlug = Object.fromEntries(
      available.map((r) => [r.slug, r.title || r.slug]),
    );
    const usingDomain = (manifest) =>
      (manifest?.integrations || []).some((i) => i.domain === domain);
    return installed
      .filter((rec) => rec.slug !== exceptSlug)
      .filter((rec) => {
        const m = available.find((a) => a.slug === rec.slug);
        return usingDomain(m);
      })
      .map((rec) => titleBySlug[rec.slug] || rec.title || rec.slug);
  }

  async _confirmRecipeUninstall() {
    const slug = this._recipeUninstallPending;
    if (!slug) return;
    const entries = Object.keys(this._recipeUninstallEntries || {});
    this._recipeUninstallPending = null;
    this._recipeUninstallEntries = {};
    this._recipesBusy = true;
    try {
      await this.hass.callWS({
        type: "selora_ai/recipes/uninstall",
        slug,
        remove_entries: entries,
      });
      // Clear any persisted wizard state for the slug so a future
      // re-install starts from a clean slate.
      this._clearWizardState(slug);
      // Return to the list and drop any ``/recipes/<slug>`` deep-link.
      // The uninstall reloads HA, which fires a ``location-changed``;
      // a stale slug URL would route through ``_openRecipeFromDeepLink``
      // and reopen the wizard for the just-removed recipe (its bundle is
      // still staged on disk, so the handler happily reopens it).
      this._recipesView = "list";
      this._recipeWizardSlug = null;
      this._setRecipeWizardUrl?.(null);
      await this._loadRecipesList();
    } catch (err) {
      console.error("Recipe uninstall failed", err);
    } finally {
      this._recipesBusy = false;
    }
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
              this._setActiveTab("chat");
            }}
            style="cursor:pointer;"
          />
          <span
            class="header-title ${this._isDark ? "gold-text" : ""}"
            @click=${() => {
              this._setActiveTab("chat");
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
                  this._setActiveTab("chat");
                  this._showSidebar = true;
                }
              }}
            >
              <span class="tab-inner"
                ><ha-icon icon="mdi:chat-outline" class="tab-icon"></ha-icon
                >${this._t("panel_tab_conversations", "Conversations")}</span
              >
            </div>
            <div
              class="tab ${this._activeTab === "automations" ? "active" : ""}"
              @click=${() => {
                this._setActiveTab("automations");
                this._showSidebar = false;
                this._loadAutomations();
              }}
            >
              <span class="tab-inner"
                ><ha-icon icon="mdi:robot-outline" class="tab-icon"></ha-icon
                >${this._t("panel_tab_automations", "Automations")}</span
              >
            </div>
            <div
              class="tab ${this._activeTab === "scenes" ? "active" : ""}"
              @click=${() => {
                this._setActiveTab("scenes");
                this._showSidebar = false;
                this._loadScenes();
              }}
            >
              <span class="tab-inner"
                ><ha-icon icon="mdi:palette-outline" class="tab-icon"></ha-icon
                >${this._t("panel_tab_scenes", "Scenes")}</span
              >
            </div>
            <div
              class="tab ${this._activeTab === "recipes" ? "active" : ""}"
              @click=${() => {
                this._setActiveTab("recipes");
                this._showSidebar = false;
                this._recipesView = "list";
                this._loadRecipesList();
              }}
            >
              <span class="tab-inner"
                ><ha-icon
                  icon="mdi:book-open-variant"
                  class="tab-icon"
                ></ha-icon
                >Recipes</span
              >
            </div>
          </div>
          <span class="header-spacer"></span>
          ${this._activeTab !== "chat" || this._messages.length > 0
            ? html`<button
                class="header-new-chat"
                title=${this._t("nav_new_chat", "New chat")}
                aria-label=${this._t("nav_new_chat", "New chat")}
                @click=${() => {
                  this._showOverflowMenu = false;
                  if (this._messages.length === 0) {
                    this._setActiveTab("chat");
                    if (this.narrow) this._showSidebar = false;
                  } else {
                    this._newSession();
                  }
                }}
              >
                <ha-icon icon="mdi:square-edit-outline"></ha-icon>
                <span class="header-new-chat-label"
                  >${this._t("nav_new_chat", "New chat")}</span
                >
              </button>`
            : ""}
          <div class="overflow-btn-wrap">
            <button
              class="overflow-btn selora-menu-btn"
              aria-label=${this._t("nav_selora_menu", "Selora menu")}
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
                          this._setActiveTab("chat");
                          this._showSidebar = true;
                        }}
                      >
                        <ha-icon icon="mdi:chat-outline"></ha-icon>
                        ${this._t("nav_conversations", "Conversations")}
                      </button>
                      <button
                        class="overflow-item ${this._activeTab === "automations"
                          ? "active"
                          : ""}"
                        @click=${() => {
                          this._showOverflowMenu = false;
                          this._setActiveTab("automations");
                          this._showSidebar = false;
                          this._loadAutomations();
                        }}
                      >
                        <ha-icon icon="mdi:robot-outline"></ha-icon>
                        ${this._t("nav_automations", "Automations")}
                      </button>
                      <button
                        class="overflow-item ${this._activeTab === "scenes"
                          ? "active"
                          : ""}"
                        @click=${() => {
                          this._showOverflowMenu = false;
                          this._setActiveTab("scenes");
                          this._showSidebar = false;
                          this._loadScenes();
                        }}
                      >
                        <ha-icon icon="mdi:palette-outline"></ha-icon>
                        ${this._t("nav_scenes", "Scenes")}
                      </button>
                      <button
                        class="overflow-item ${this._activeTab === "recipes"
                          ? "active"
                          : ""}"
                        @click=${() => {
                          this._showOverflowMenu = false;
                          this._setActiveTab("recipes");
                          this._showSidebar = false;
                          this._recipesView = "list";
                          this._loadRecipesList();
                        }}
                      >
                        <ha-icon icon="mdi:book-open-variant"></ha-icon>
                        Recipes
                      </button>
                      <div class="overflow-divider"></div>
                    </div>
                    <button
                      class="overflow-item ${this._activeTab === "settings"
                        ? "active"
                        : ""}"
                      @click=${() => {
                        this._showOverflowMenu = false;
                        this._setActiveTab("settings");
                        this._showSidebar = false;
                        this._loadConfig();
                      }}
                    >
                      <ha-icon icon="mdi:cog-outline"></ha-icon>
                      ${this._t("nav_settings", "Settings")}
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
                      <span class="overflow-item-label"
                        >${this._t("nav_documentation", "Documentation")}</span
                      >
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
                      <span class="overflow-item-label"
                        >${this._t(
                          "feedback_button_label",
                          "Give Feedback",
                        )}</span
                      >
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
                      <span class="overflow-item-label"
                        >${this._t("nav_github_issues", "GitHub Issues")}</span
                      >
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
                      <span class="overflow-item-label"
                        >${this._t(
                          "nav_gitlab_repo",
                          "GitLab Repository",
                        )}</span
                      >
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
            <span
              >${this._t("panel_sidebar_conversations", "Conversations")}</span
            >
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
                            ${this._t("panel_sidebar_done", "Done")}
                          </button>
                        `
                      : html`
                          <button
                            class="sidebar-select-btn"
                            @click=${() => {
                              this._selectChatsMode = true;
                            }}
                          >
                            ${this._t("panel_sidebar_select", "Select")}
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
                    <span
                      >${this._t(
                        "panel_sidebar_select_all",
                        "Select all",
                      )}</span
                    >
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
                    ${this._t("panel_sidebar_delete", "Delete")}
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
                  ${this._t("panel_sidebar_new_chat", "New Chat")}
                </button>
              `}
          <div class="session-list">
            ${this._sessions.length === 0
              ? html`<div style="padding: 16px; font-size: 12px; opacity: 0.5;">
                  ${this._t(
                    "panel_sidebar_no_conversations",
                    "No conversations yet.",
                  )}
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
                                >${this._t(
                                  "panel_session_delete_confirm",
                                  "Delete?",
                                )}</span
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
                                  ${this._t("panel_session_delete", "Delete")}
                                </button>
                                <button
                                  class="btn btn-outline btn-sm"
                                  style="padding:3px 10px;font-size:12px;"
                                  @click=${(e) => {
                                    e.stopPropagation();
                                    this._deleteConfirmSessionId = null;
                                  }}
                                >
                                  ${this._t("panel_session_cancel", "Cancel")}
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
                                      title=${this._t(
                                        "panel_session_delete_title",
                                        "Delete",
                                      )}
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
            .speed=${this._streaming || this._loading ? 2.2 : 1}
          ></selora-particles>
          ${this._renderQuotaBanner()} ${renderTelemetryConsent(this)}
          ${this._activeTab === "chat" ? this._renderChat() : ""}
          ${this._activeTab === "automations" ? this._renderAutomations() : ""}
          ${this._activeTab === "scenes" ? this._renderScenes() : ""}
          ${this._activeTab === "recipes" ? this._renderRecipesV2() : ""}
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
                  ${this._t("panel_bulk_delete_title", "Delete Conversations")}
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
                    ${this._t("panel_bulk_delete_cancel", "Cancel")}
                  </button>
                  <button
                    class="btn"
                    style="background:#ef4444;color:#fff;border-color:#ef4444;"
                    @click=${() => this._confirmBulkDeleteSessions()}
                  >
                    ${this._t("panel_bulk_delete_confirm", "Delete")}
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
Object.assign(SeloraAIPanel.prototype, sceneEdit);

if (!customElements.get("selora-ai")) {
  customElements.define("selora-ai", SeloraAIPanel);
}
