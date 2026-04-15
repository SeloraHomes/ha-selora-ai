import { LitElement, html } from "lit";
import { describeFlowItem } from "./shared/flow-description.js";
import { formatRelativeTime } from "./shared/date-utils.js";
import { seloraTokens } from "./shared/design-tokens.css.js";
import { sharedAnimations } from "./shared/styles/animations.css.js";
import { sharedModals } from "./shared/styles/modals.css.js";
import { sharedBadges } from "./shared/styles/badges.css.js";
import { sharedLoaders } from "./shared/styles/loaders.css.js";
import { cardStyles } from "./card/styles.css.js";
import "./card/editor.js";

// ---------------------------------------------------------------------------
// Selora AI Dashboard Card
// ---------------------------------------------------------------------------
// A Lovelace custom card for the HA dashboard that surfaces:
//   - Automation suggestions from the AI
//   - Quick "Start Chat" action
//   - Automation management (toggle, delete, view details)
// ---------------------------------------------------------------------------

class SeloraAIDashboardCard extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      config: { type: Object },

      // Automation suggestions
      _suggestions: { type: Array },
      _loadingSuggestions: { type: Boolean },
      _generatingSuggestions: { type: Boolean },

      // Automations list
      _automations: { type: Array },
      _loadingAutomations: { type: Boolean },

      // Expanded automation details
      _expandedId: { type: String },

      // New automation form
      _showNewAutomation: { type: Boolean },
      _newAutomationName: { type: String },
      _generatingName: { type: Boolean },
      _creatingAutomation: { type: Boolean },

      // Error feedback
      _errorMessage: { type: String },
    };
  }

  constructor() {
    super();
    this.config = {};
    this._suggestions = [];
    this._loadingSuggestions = true;
    this._generatingSuggestions = false;
    this._automations = [];
    this._loadingAutomations = true;
    this._expandedId = null;
    this._showNewAutomation = false;
    this._newAutomationName = "";
    this._generatingName = false;
    this._creatingAutomation = false;
    this._errorMessage = "";
  }

  setConfig(config) {
    this.config = config;
  }

  static getConfigElement() {
    return document.createElement("selora-ai-card-editor");
  }

  static getStubConfig() {
    return {
      title: "Selora AI",
      show_suggestions: true,
      show_automations: true,
      max_suggestions: 3,
      max_automations: 10,
    };
  }

  connectedCallback() {
    super.connectedCallback();
    // Don't call _loadData() here — hass is set before DOM insertion,
    // so updated() will fire with hass already available. Calling here
    // too causes duplicate WS requests on every mount.
  }

  updated(changedProps) {
    if (changedProps.has("hass") && this.hass) {
      // Set accent text color based on HA dark mode (gold on dark, black on light)
      const dark = this.hass.themes?.darkMode;
      if (dark !== undefined) {
        this.style.setProperty(
          "--selora-accent-text",
          dark ? "#fbbf24" : "#18181b",
        );
      }
      if (!this._initialLoaded) {
        this._initialLoaded = true;
        this._loadData();
      }
    }
  }

  async _loadData() {
    if (!this.hass) return;
    await Promise.all([this._loadSuggestions(), this._loadAutomations()]);
  }

  // -------------------------------------------------------------------------
  // Data loaders
  // -------------------------------------------------------------------------

  async _loadSuggestions() {
    this._loadingSuggestions = true;
    try {
      const suggestions = await this.hass.callWS({
        type: "selora_ai/get_suggestions",
      });
      this._suggestions = suggestions || [];
    } catch (err) {
      console.error("Selora AI Card: Failed to load suggestions", err);
      this._suggestions = [];
    } finally {
      this._loadingSuggestions = false;
    }
  }

  async _loadAutomations() {
    this._loadingAutomations = true;
    try {
      const automations = await this.hass.callWS({
        type: "selora_ai/get_automations",
      });
      const max = this.config.max_automations || 10;
      this._automations = (automations || [])
        .filter((a) => a.is_selora)
        .reverse()
        .slice(0, max);
    } catch (err) {
      console.error("Selora AI Card: Failed to load automations", err);
      this._automations = [];
    } finally {
      this._loadingAutomations = false;
    }
  }

  // -------------------------------------------------------------------------
  // Actions
  // -------------------------------------------------------------------------

  _showError(msg) {
    this._errorMessage = msg;
    setTimeout(() => {
      this._errorMessage = "";
    }, 5000);
  }

  async _generateSuggestions() {
    this._generatingSuggestions = true;
    try {
      const suggestions = await this.hass.callWS({
        type: "selora_ai/generate_suggestions",
      });
      this._suggestions = suggestions || [];
    } catch (err) {
      console.error("Selora AI Card: Failed to generate suggestions", err);
      this._showError("Failed to generate suggestions");
    } finally {
      this._generatingSuggestions = false;
    }
  }

  async _acceptSuggestion(suggestion) {
    try {
      // Suggestions have automation_data (validated) or fall back to the raw fields
      const automationPayload = suggestion.automation_data ||
        suggestion.automation || {
          alias: suggestion.alias,
          description: suggestion.description || "",
          triggers: suggestion.triggers || suggestion.trigger || [],
          actions: suggestion.actions || suggestion.action || [],
          conditions: suggestion.conditions || suggestion.condition || [],
        };
      // Respect the LLM's suggested initial_state; default to enabled
      automationPayload.initial_state = automationPayload.initial_state ?? true;
      await this.hass.callWS({
        type: "selora_ai/create_automation",
        automation: automationPayload,
      });
      // Remove accepted suggestion from the list immediately
      this._suggestions = this._suggestions.filter((s) => s !== suggestion);
      await this._loadAutomations();
    } catch (err) {
      console.error("Selora AI Card: Failed to accept suggestion", err);
      this._showError("Failed to accept suggestion");
    }
  }

  async _toggleAutomation(automation) {
    if (!automation.automation_id || !automation.entity_id) {
      this._showError("Cannot toggle: automation ID not resolved");
      return;
    }
    try {
      await this.hass.callWS({
        type: "selora_ai/toggle_automation",
        automation_id: automation.automation_id,
        entity_id: automation.entity_id,
      });
      await this._loadAutomations();
    } catch (err) {
      console.error("Selora AI Card: Failed to toggle automation", err);
      this._showError("Failed to toggle automation");
    }
  }

  async _deleteAutomation(automation) {
    try {
      await this.hass.callWS({
        type: "selora_ai/delete_automation",
        automation_id: automation.automation_id,
      });
      await this._loadAutomations();
    } catch (err) {
      console.error("Selora AI Card: Failed to delete automation", err);
      this._showError("Failed to delete automation");
    }
  }

  _toggleExpanded(automationId) {
    this._expandedId = this._expandedId === automationId ? null : automationId;
  }

  async _createAutomation() {
    const name = this._newAutomationName.trim();
    if (!name) return;
    this._creatingAutomation = true;
    try {
      // Ask the LLM to generate an automation, then save it directly
      const result = await this.hass.callWS({
        type: "selora_ai/quick_create_automation",
        name: name,
      });
      if (result && result.automation_id) {
        this._showNewAutomation = false;
        this._newAutomationName = "";
        await this._loadAutomations();
      } else {
        this._showError("Failed to create automation. Try again.");
      }
    } catch (err) {
      console.error("Selora AI Card: Failed to create automation", err);
      this._showError("Failed to create automation: " + err.message);
    } finally {
      this._creatingAutomation = false;
    }
  }

  async _letAIDecide() {
    this._generatingName = true;
    try {
      const suggestions = await this.hass.callWS({
        type: "selora_ai/generate_suggestions",
      });
      if (suggestions && suggestions.length > 0) {
        // Pick a random suggestion name to keep it fresh
        const idx = Math.floor(Math.random() * suggestions.length);
        this._newAutomationName =
          suggestions[idx].alias ||
          suggestions[idx].description ||
          "New Automation";
      } else {
        this._showError(
          "No suggestions available. Try adding more devices first.",
        );
      }
    } catch (err) {
      console.error("Selora AI Card: Failed to generate name", err);
      this._showError("Failed to generate suggestion");
    } finally {
      this._generatingName = false;
    }
  }

  _openPanel() {
    history.pushState(null, "", "/selora-ai?tab=automations");
    window.dispatchEvent(new Event("location-changed"));
  }

  // -------------------------------------------------------------------------
  // Styles
  // -------------------------------------------------------------------------

  static get styles() {
    return [
      seloraTokens,
      sharedAnimations,
      sharedModals,
      sharedBadges,
      sharedLoaders,
      cardStyles,
    ];
  }

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  render() {
    const title = this.config.title || "Selora AI";
    const showSuggestions = this.config.show_suggestions !== false;
    const showAutomations = this.config.show_automations !== false;

    return html`
      <ha-card>
        <!-- Header -->
        <div class="card-header" @click=${this._openPanel}>
          <div class="header-left">
            <img
              src="/api/selora_ai/logo.png"
              alt="Selora"
              class="header-logo"
            />
            <span class="header-title">${title}</span>
          </div>
          <ha-icon icon="mdi:open-in-new" class="header-action"></ha-icon>
        </div>

        <div class="card-content">
          <!-- Error banner -->
          ${this._errorMessage
            ? html`
                <div class="error-banner">
                  <span>${this._errorMessage}</span>
                  <ha-icon
                    icon="mdi:close"
                    class="error-dismiss"
                    @click=${() => {
                      this._errorMessage = "";
                    }}
                  ></ha-icon>
                </div>
              `
            : ""}

          <!-- Quick Actions -->
          <div class="section quick-actions">
            <button
              class="action-btn new-btn"
              @click=${() => {
                this._showNewAutomation = true;
              }}
            >
              <ha-icon icon="mdi:plus"></ha-icon>
              <span>New Automation</span>
            </button>
            <button
              class="action-btn suggest-btn"
              ?disabled=${this._generatingSuggestions}
              @click=${this._generateSuggestions}
            >
              ${this._generatingSuggestions
                ? html`<span class="spinner"></span>`
                : html`<ha-icon icon="mdi:auto-fix"></ha-icon>`}
              <span
                >${this._generatingSuggestions
                  ? "Analyzing..."
                  : "Generate Suggestions"}</span
              >
            </button>
          </div>

          <!-- Automation Suggestions -->
          ${showSuggestions ? this._renderSuggestions() : ""}

          <!-- Automations -->
          ${showAutomations ? this._renderAutomations() : ""}
        </div>
      </ha-card>

      <!-- Modal overlay for New Automation -->
      ${this._showNewAutomation
        ? html`
            <div
              class="modal-overlay"
              @click=${(e) => {
                if (e.target === e.currentTarget)
                  this._showNewAutomation = false;
              }}
            >
              <div class="modal">
                <div class="modal-title">New Automation</div>
                <div class="modal-label">Automation name</div>
                <div
                  class="modal-row ${this._generatingName ? "generating" : ""}"
                >
                  ${this._generatingName
                    ? html`
                        <div class="modal-input generating-placeholder">
                          <span class="dots-loader"
                            ><span></span><span></span><span></span
                          ></span>
                          <span style="opacity:0.5;font-size:13px;"
                            >Generating suggestion...</span
                          >
                        </div>
                      `
                    : html`
                        <input
                          class="modal-input"
                          type="text"
                          placeholder="e.g. Turn off lights at midnight"
                          .value=${this._newAutomationName}
                          @input=${(e) => {
                            this._newAutomationName = e.target.value;
                          }}
                          @keydown=${(e) => {
                            if (e.key === "Enter") this._createAutomation();
                          }}
                        />
                      `}
                  <button
                    class="modal-magic-btn"
                    title="Let AI decide"
                    ?disabled=${this._generatingName ||
                    this._creatingAutomation}
                    @click=${this._letAIDecide}
                  >
                    ${this._generatingName
                      ? html`<span class="spinner"></span>`
                      : html`<ha-icon icon="mdi:auto-fix"></ha-icon>`}
                  </button>
                </div>
                <div class="modal-actions">
                  <button
                    class="modal-btn modal-cancel"
                    @click=${() => {
                      this._showNewAutomation = false;
                    }}
                    ?disabled=${this._creatingAutomation}
                  >
                    Cancel
                  </button>
                  <button
                    class="modal-btn modal-create"
                    @click=${this._createAutomation}
                    ?disabled=${!this._newAutomationName.trim() ||
                    this._creatingAutomation}
                  >
                    ${this._creatingAutomation
                      ? html`<span class="spinner"></span> Creating...`
                      : html`<ha-icon icon="mdi:plus-circle-outline"></ha-icon>
                          Create`}
                  </button>
                </div>
              </div>
            </div>
          `
        : ""}
    `;
  }

  _renderSuggestions() {
    const maxSuggestions = this.config.max_suggestions || 3;
    const suggestions = this._suggestions.slice(0, maxSuggestions);

    return html`
      <div class="section">
        <div class="section-header">
          <ha-icon
            icon="mdi:lightbulb-on-outline"
            class="section-icon"
          ></ha-icon>
          <span>Suggestions</span>
          ${this._suggestions.length > 0
            ? html`<span class="badge">${this._suggestions.length}</span>`
            : ""}
        </div>

        ${this._generatingSuggestions
          ? html`<div class="generating-row">
              <span class="dots-loader"
                ><span></span><span></span><span></span
              ></span>
              Generating suggestions...
            </div>`
          : this._loadingSuggestions
            ? html`<div class="loading-row">
                <span class="spinner"></span> Loading suggestions...
              </div>`
            : suggestions.length === 0
              ? html`<div class="empty-row">
                  No suggestions yet. Tap "Generate Suggestions" to analyze your
                  home.
                </div>`
              : suggestions.map(
                  (s) => html`
                    <div class="suggestion-item">
                      <div class="suggestion-info">
                        <div class="suggestion-name">
                          ${s.automation?.alias || s.alias || "Untitled"}
                        </div>
                        <div class="suggestion-desc">
                          ${s.automation?.description || s.description || ""}
                        </div>
                      </div>
                      <button
                        class="accept-btn"
                        @click=${() => this._acceptSuggestion(s)}
                        title="Accept"
                      >
                        <ha-icon icon="mdi:check"></ha-icon>
                      </button>
                    </div>
                  `,
                )}
        ${!this._loadingSuggestions &&
        !this._generatingSuggestions &&
        this._suggestions.length > maxSuggestions
          ? html`<div class="more-link" @click=${this._openPanel}>
              View all ${this._suggestions.length} suggestions
            </div>`
          : ""}
      </div>
    `;
  }

  _renderAutomations() {
    return html`
      <div class="section">
        <div class="section-header">
          <ha-icon icon="mdi:lightning-bolt" class="section-icon"></ha-icon>
          <span>Automations</span>
          ${this._automations.length > 0
            ? html`<span class="badge">${this._automations.length}</span>`
            : ""}
        </div>

        ${this._loadingAutomations
          ? html`<div class="loading-row">
              <span class="spinner"></span> Loading automations...
            </div>`
          : this._automations.length === 0
            ? html`<div class="empty-row">No Selora AI automations yet.</div>`
            : this._automations.map((a) => this._renderAutomationItem(a))}
      </div>
    `;
  }

  _renderAutomationItem(a) {
    const isOn = a.state === "on";
    const isExpanded = this._expandedId === a.automation_id;
    const triggers = Array.isArray(a.triggers ?? a.trigger)
      ? (a.triggers ?? a.trigger)
      : [];
    const actions = Array.isArray(a.actions ?? a.action)
      ? (a.actions ?? a.action)
      : [];

    return html`
      <div class="automation-item ${isExpanded ? "expanded" : ""}">
        <div
          class="automation-row"
          @click=${() => this._toggleExpanded(a.automation_id)}
        >
          <div class="activity-indicator ${isOn ? "active" : "inactive"}"></div>
          <div class="activity-info">
            <div class="activity-name">${a.alias || a.entity_id}</div>
            <div class="activity-meta">
              ${isOn ? "Enabled" : "Disabled"}
              ${a.last_triggered
                ? html` · Ran ${formatRelativeTime(a.last_triggered)}`
                : ""}
            </div>
          </div>
          <div
            class="activity-toggle-wrap"
            @click=${(e) => {
              e.stopPropagation();
              e.preventDefault();
              this._toggleAutomation(a);
            }}
          >
            <ha-icon
              icon=${isOn
                ? "mdi:toggle-switch"
                : "mdi:toggle-switch-off-outline"}
              class="activity-toggle ${isOn ? "on" : "off"}"
            ></ha-icon>
          </div>
        </div>

        ${isExpanded
          ? html`
              <div class="automation-details">
                ${a.description
                  ? html`<div class="detail-desc">${a.description}</div>`
                  : ""}
                ${triggers.length > 0
                  ? html`
                      <div class="detail-section">
                        <div class="detail-label">Triggers</div>
                        ${triggers.map(
                          (t) => html`
                            <div class="detail-chip trigger">
                              ${describeFlowItem(this.hass, t)}
                            </div>
                          `,
                        )}
                      </div>
                    `
                  : ""}
                ${actions.length > 0
                  ? html`
                      <div class="detail-section">
                        <div class="detail-label">Actions</div>
                        ${actions.map(
                          (act) => html`
                            <div class="detail-chip action">
                              ${describeFlowItem(this.hass, act)}
                            </div>
                          `,
                        )}
                      </div>
                    `
                  : ""}

                <div class="detail-actions">
                  <button
                    class="detail-btn open-btn"
                    @click=${() => {
                      history.pushState(null, "", "/selora-ai?tab=automations");
                      window.dispatchEvent(new Event("location-changed"));
                    }}
                  >
                    <ha-icon icon="mdi:pencil-outline"></ha-icon> Edit in Panel
                  </button>
                  <button
                    class="detail-btn delete-btn"
                    @click=${() => this._deleteAutomation(a)}
                  >
                    <ha-icon icon="mdi:trash-can-outline"></ha-icon> Delete
                  </button>
                </div>
              </div>
            `
          : ""}
      </div>
    `;
  }

  getCardSize() {
    return 4;
  }
}

// ---------------------------------------------------------------------------
// Register
// ---------------------------------------------------------------------------

customElements.define("selora-ai-card", SeloraAIDashboardCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "selora-ai-card",
  name: "Selora AI",
  description:
    "Dashboard card for Selora AI automation suggestions, quick chat, and activity feed.",
  preview: true,
});
