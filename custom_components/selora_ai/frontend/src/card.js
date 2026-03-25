import { LitElement, html, css } from "lit";

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
          trigger: suggestion.trigger || suggestion.triggers || [],
          action: suggestion.action || suggestion.actions || [],
          condition: suggestion.condition || suggestion.conditions || [],
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
        type: "selora_ai/soft_delete_automation",
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
        const idx =
          crypto.getRandomValues(new Uint32Array(1))[0] % suggestions.length;
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
    history.pushState(null, "", "/selora-ai-architect?tab=automations");
    window.dispatchEvent(new Event("location-changed"));
  }

  // -------------------------------------------------------------------------
  // Formatting helpers
  // -------------------------------------------------------------------------

  _formatRelativeTime(dateStr) {
    if (!dateStr) return "Never";
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    if (diffMins < 1) return "Just now";
    if (diffMins < 60) return `${diffMins}m ago`;
    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toLocaleDateString();
  }

  _humanizeToken(value) {
    if (value == null || value === "") return "";
    return String(value)
      .replace(/_/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }

  _fmtEntity(eid) {
    if (!eid) return "";
    const id = String(eid);
    if (this.hass?.states?.[id]) {
      return (
        this.hass.states[id].attributes?.friendly_name ||
        id
          .split(".")
          .pop()
          .replace(/_/g, " ")
          .replace(/\b\w/g, (c) => c.toUpperCase())
      );
    }
    return id
      .split(".")
      .pop()
      .replace(/_/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase());
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
    if (val == null) return "";
    const s = String(val).trim();
    if (s.includes("{{")) {
      const m = s.match(/states\(['"]([^'"]+)['"]\)/);
      if (m) return this._fmtEntity(m[1]);
      return "a calculated time";
    }
    const num = Number(s);
    if (!isNaN(num) && num >= 0 && num <= 86400 && !s.includes(":")) {
      const h = Math.floor(num / 3600),
        m = Math.floor((num % 3600) / 60);
      const ampm = h >= 12 ? "PM" : "AM";
      const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
      return `${h12}:${String(m).padStart(2, "0")} ${ampm}`;
    }
    const parts = s.split(":");
    if (parts.length >= 2) {
      const h = parseInt(parts[0], 10),
        m = parseInt(parts[1], 10);
      if (!isNaN(h) && !isNaN(m)) {
        const ampm = h >= 12 ? "PM" : "AM";
        const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
        return `${h12}:${String(m).padStart(2, "0")} ${ampm}`;
      }
    }
    if (s.startsWith("input_datetime.") || s.startsWith("sensor."))
      return this._fmtEntity(s);
    return s;
  }

  _formatTrigger(t) {
    if (!t) return "Unknown trigger";
    const p = t.platform || t.trigger;
    if (p === "time") {
      const raw = t.at;
      if (Array.isArray(raw))
        return `When the time is ${raw.map((v) => this._fmtTime(v)).join(" or ")}`;
      return `When the time is ${this._fmtTime(raw)}`;
    }
    if (p === "sun") {
      const ev =
        t.event === "sunset"
          ? "sunset"
          : t.event === "sunrise"
            ? "sunrise"
            : this._humanizeToken(t.event || "sun event").toLowerCase();
      return `When it is ${ev}${t.offset ? ` (${t.offset})` : ""}`;
    }
    if (p === "state") {
      const eid = this._fmtEntity(t.entity_id);
      const fromState = this._fmtState(t.from);
      const toState = this._fmtState(t.to);
      const duration = this._fmtDuration(t.for);
      const dur = duration ? ` for ${duration}` : "";
      if (toState === "on") return `When ${eid} turns on${dur}`;
      if (toState === "off") return `When ${eid} turns off${dur}`;
      if (toState && fromState)
        return `When ${eid} changes from ${fromState} to ${toState}${dur}`;
      if (toState) return `When ${eid} becomes ${toState}${dur}`;
      return `When ${eid} changes state${dur}`;
    }
    if (p === "numeric_state") {
      const eid = this._fmtEntity(t.entity_id);
      const above = this._fmtNumericValue(t.entity_id, t.above);
      const below = this._fmtNumericValue(t.entity_id, t.below);
      if (t.above != null && t.below != null)
        return `When ${eid} is between ${above} and ${below}`;
      if (t.above != null) return `When ${eid} rises above ${above}`;
      if (t.below != null) return `When ${eid} drops below ${below}`;
      return `When ${eid} value changes`;
    }
    if (p === "homeassistant")
      return `When Home Assistant ${t.event === "start" ? "starts" : t.event === "shutdown" ? "shuts down" : "changes state"}`;
    if (p === "template") {
      const tmpl = t.value_template || "";
      const m = tmpl.match(/states\(['"]([^'"]+)['"]\)/);
      if (m) return `When ${this._fmtEntity(m[1])} condition is met`;
      return "When a template condition is met";
    }
    if (p === "time_pattern") {
      if (t.seconds != null)
        return `Every ${t.seconds} second${Number(t.seconds) === 1 ? "" : "s"}`;
      if (t.minutes != null)
        return `Every ${t.minutes} minute${Number(t.minutes) === 1 ? "" : "s"}`;
      if (t.hours != null)
        return `Every ${t.hours} hour${Number(t.hours) === 1 ? "" : "s"}`;
      return "On a time pattern";
    }
    if (p === "event")
      return `When ${t.event_type ? this._humanizeToken(t.event_type).toLowerCase() : "an event"} happens`;
    if (p === "device") {
      const triggerType = t.type
        ? this._humanizeToken(t.type).toLowerCase()
        : "triggered";
      return t.device_id
        ? `When a device ${triggerType}`
        : `When a device is ${triggerType}`;
    }
    if (p === "zone") {
      const evMap = { enter: "enters", leave: "leaves" };
      const ev =
        evMap[String(t.event || "enter")] ||
        this._humanizeToken(t.event || "enter").toLowerCase();
      const who = this._fmtEntity(t.entity_id);
      const zone = this._fmtEntity(t.zone);
      return `When ${who} ${ev} ${zone}`;
    }
    if (p === "mqtt")
      return t.topic
        ? `When a device message arrives (${t.topic})`
        : "When a device message arrives";
    if (p === "webhook") return "When an outside service sends an update";
    if (p === "tag")
      return `When a tag is scanned${t.tag_id ? ` (${t.tag_id})` : ""}`;
    if (p === "calendar") {
      const eventName = t.event
        ? this._humanizeToken(t.event).toLowerCase()
        : "event";
      return `When a calendar ${eventName} begins`;
    }
    if (p) return "When this trigger happens";
    return "Trigger";
  }

  _formatAction(a) {
    if (!a) return "Unknown action";
    const svc = a.service || a.action;
    if (svc) {
      const str = String(svc);
      const [domain = "", name = svc] = str.split(".");
      if (
        str === "notify.persistent_notification" ||
        domain === "persistent_notification"
      ) {
        const title = a.data?.title,
          msg = a.data?.message;
        if (title) return `Notify: "${title}"`;
        if (msg)
          return `Notify: "${msg.length > 50 ? msg.slice(0, 47) + "…" : msg}"`;
        return "Send a notification";
      }
      if (domain === "notify") {
        const target = name
          .replace(/_/g, " ")
          .replace(/\b\w/g, (c) => c.toUpperCase());
        const title = a.data?.title,
          msg = a.data?.message;
        if (title) return `Notify: "${title}"`;
        if (msg && !msg.includes("{{"))
          return `Notify: "${msg.length > 50 ? msg.slice(0, 47) + "…" : msg}"`;
        return `Notify via ${target}`;
      }
      if (domain === "tts") {
        const msg = a.data?.message;
        if (msg && !msg.includes("{{"))
          return `Say: "${msg.length > 50 ? msg.slice(0, 47) + "…" : msg}"`;
        return "Text-to-speech";
      }
      const friendly = {
        turn_on: "Turn on",
        turn_off: "Turn off",
        toggle: "Toggle",
        lock: "Lock",
        unlock: "Unlock",
      };
      const label =
        friendly[name] ||
        name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
      const targets = a.target?.entity_id ?? a.data?.entity_id;
      const t = targets
        ? Array.isArray(targets)
          ? targets.map((e) => this._fmtEntity(e)).join(", ")
          : this._fmtEntity(targets)
        : "";
      return t ? `${label} ${t}` : label;
    }
    if (a.delay) return `Wait ${typeof a.delay === "string" ? a.delay : ""}`;
    if (a.scene) return `Activate scene: ${this._fmtEntity(a.scene)}`;
    return "Action";
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
    const triggers = Array.isArray(a.trigger) ? a.trigger : [];
    const actions = Array.isArray(a.action) ? a.action : [];

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
                ? html` · Ran ${this._formatRelativeTime(a.last_triggered)}`
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
                              ${this._formatTrigger(t)}
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
                              ${this._formatAction(act)}
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
                      history.pushState(
                        null,
                        "",
                        "/selora-ai-architect?tab=automations",
                      );
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

  // -------------------------------------------------------------------------
  // Styles
  // -------------------------------------------------------------------------

  static get styles() {
    return css`
      :host {
        --selora-accent: #f59e0b;
      }

      ha-card {
        overflow: hidden;
      }

      /* ---- Header ---- */
      .card-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 16px;
        cursor: pointer;
        border-bottom: 1px solid var(--divider-color);
        transition: background 0.15s;
      }
      .card-header:hover {
        background: var(--secondary-background-color);
      }
      .header-left {
        display: flex;
        align-items: center;
        gap: 10px;
      }
      .header-logo {
        width: 26px;
        height: 26px;
        border-radius: 6px;
      }
      .header-title {
        font-size: 16px;
        font-weight: 600;
      }
      .header-action {
        --mdc-icon-size: 18px;
        opacity: 0.4;
        transition: opacity 0.15s;
      }
      .card-header:hover .header-action {
        opacity: 0.8;
      }

      /* ---- Content ---- */
      .card-content {
        padding: 12px 16px 16px;
      }

      /* ---- Quick Actions ---- */
      .quick-actions {
        display: flex;
        gap: 8px;
        margin-bottom: 16px;
      }
      .action-btn {
        flex: 1;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 6px;
        padding: 10px 12px;
        border: 1px solid var(--divider-color);
        border-radius: 10px;
        background: var(--card-background-color);
        color: var(--primary-text-color);
        font-size: 13px;
        font-weight: 500;
        cursor: pointer;
        transition: all 0.15s;
        font-family: inherit;
      }
      .action-btn:hover {
        background: rgba(245, 158, 11, 0.06);
        border-color: var(--selora-accent);
      }
      .action-btn:disabled {
        opacity: 0.6;
        cursor: not-allowed;
      }
      .action-btn ha-icon {
        --mdc-icon-size: 18px;
      }
      .new-btn {
        background: var(--selora-accent);
        border-color: var(--selora-accent);
        color: #1a1a1a;
        font-weight: 600;
      }
      .new-btn:hover {
        background: #f7b731;
        border-color: #f7b731;
      }

      /* ---- Sections ---- */
      .section {
        margin-bottom: 12px;
      }
      .section:last-child {
        margin-bottom: 0;
      }
      .section-header {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 12px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: normal;
        opacity: 0.7;
        margin-bottom: 8px;
        padding-bottom: 6px;
        border-bottom: 1px solid var(--divider-color);
      }
      .section-icon {
        --mdc-icon-size: 16px;
      }
      .badge {
        background: var(--selora-accent);
        color: white;
        font-size: 10px;
        font-weight: 700;
        padding: 1px 6px;
        border-radius: 10px;
        margin-left: auto;
      }

      /* ---- Suggestion Items ---- */
      .suggestion-item {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 10px 0;
        border-bottom: 1px solid var(--divider-color);
      }
      .suggestion-item:last-child {
        border-bottom: none;
      }
      .suggestion-info {
        flex: 1;
        min-width: 0;
      }
      .suggestion-name {
        font-size: 13px;
        font-weight: 500;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .suggestion-desc {
        font-size: 11px;
        opacity: 0.6;
        margin-top: 2px;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
      }
      .accept-btn {
        flex-shrink: 0;
        width: 32px;
        height: 32px;
        border-radius: 50%;
        border: 1px solid var(--selora-accent);
        background: transparent;
        color: var(--selora-accent);
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: all 0.15s;
      }
      .accept-btn:hover {
        background: var(--selora-accent);
        color: white;
      }
      .accept-btn ha-icon {
        --mdc-icon-size: 18px;
      }

      /* ---- Automation Items ---- */
      .automation-item {
        border-bottom: 1px solid var(--divider-color);
      }
      .automation-item:last-child {
        border-bottom: none;
      }
      .automation-item.expanded {
        background: rgba(245, 158, 11, 0.04);
        border-radius: 8px;
        margin: 4px -8px;
        padding: 0 8px;
        border-bottom: none;
      }
      .automation-row {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 8px 0;
        cursor: pointer;
      }
      .activity-indicator {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        flex-shrink: 0;
      }
      .activity-indicator.active {
        background: var(--selora-accent);
        box-shadow: 0 0 6px rgba(245, 158, 11, 0.5);
      }
      .activity-indicator.inactive {
        background: var(--disabled-text-color, #999);
      }
      .activity-info {
        flex: 1;
        min-width: 0;
      }
      .activity-name {
        font-size: 13px;
        font-weight: 500;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .activity-meta {
        font-size: 11px;
        opacity: 0.5;
        margin-top: 1px;
      }
      .activity-toggle-wrap {
        cursor: pointer;
        flex-shrink: 0;
        padding: 4px;
      }
      .activity-toggle {
        --mdc-icon-size: 24px;
        transition: color 0.15s;
      }
      .activity-toggle.on {
        color: var(--selora-accent);
      }
      .activity-toggle.off {
        color: var(--disabled-text-color, #999);
      }

      /* ---- Expanded Details ---- */
      .automation-details {
        padding: 4px 0 10px 18px;
      }
      .detail-desc {
        font-size: 12px;
        opacity: 0.6;
        margin-bottom: 8px;
        font-style: italic;
      }
      .detail-section {
        margin-bottom: 6px;
      }
      .detail-label {
        font-size: 10px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: normal;
        opacity: 0.5;
        margin-bottom: 3px;
      }
      .detail-chip {
        display: inline-block;
        font-size: 11px;
        padding: 2px 8px;
        border-radius: 4px;
        margin: 2px 4px 2px 0;
      }
      .detail-chip.trigger {
        background: rgba(245, 158, 11, 0.12);
        border: 1px solid var(--selora-accent);
        color: var(--primary-text-color);
      }
      .detail-chip.action {
        background: var(--secondary-background-color, rgba(0, 0, 0, 0.06));
        border: 1px solid var(--divider-color);
        color: var(--primary-text-color);
      }
      .detail-actions {
        display: flex;
        gap: 8px;
        margin-top: 8px;
      }
      .detail-btn {
        display: flex;
        align-items: center;
        gap: 4px;
        padding: 5px 10px;
        border-radius: 6px;
        font-size: 11px;
        font-weight: 500;
        cursor: pointer;
        border: 1px solid var(--divider-color);
        background: var(--card-background-color);
        color: var(--primary-text-color);
        font-family: inherit;
        transition: all 0.15s;
      }
      .detail-btn ha-icon {
        --mdc-icon-size: 14px;
      }
      .open-btn:hover {
        border-color: var(--selora-accent);
        color: var(--selora-accent);
      }
      .delete-btn:hover {
        border-color: var(--error-color, #f44336);
        color: var(--error-color, #f44336);
      }

      /* ---- Error banner ---- */
      .error-banner {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        padding: 8px 12px;
        margin-bottom: 12px;
        border-radius: 8px;
        background: rgba(244, 67, 54, 0.1);
        border: 1px solid var(--error-color, #f44336);
        color: var(--error-color, #f44336);
        font-size: 12px;
        font-weight: 500;
      }
      .error-dismiss {
        --mdc-icon-size: 16px;
        cursor: pointer;
        opacity: 0.7;
        flex-shrink: 0;
      }
      .error-dismiss:hover {
        opacity: 1;
      }

      /* ---- Common ---- */
      .loading-row {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 12px 0;
        font-size: 12px;
        opacity: 0.6;
      }
      .empty-row {
        padding: 12px 0;
        font-size: 12px;
        opacity: 0.5;
        font-style: italic;
      }
      .more-link {
        text-align: center;
        font-size: 12px;
        color: var(--selora-accent);
        cursor: pointer;
        padding: 8px 0 4px;
        font-weight: 500;
      }
      .more-link:hover {
        text-decoration: underline;
      }

      /* ---- Bouncing dots loader ---- */
      .dots-loader {
        display: inline-flex;
        gap: 4px;
        align-items: center;
      }
      .dots-loader span {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: var(--selora-accent);
        animation: bounce 1.2s ease-in-out infinite;
      }
      .dots-loader span:nth-child(2) {
        animation-delay: 0.2s;
      }
      .dots-loader span:nth-child(3) {
        animation-delay: 0.4s;
      }
      @keyframes bounce {
        0%,
        60%,
        100% {
          transform: translateY(0);
          opacity: 0.4;
        }
        30% {
          transform: translateY(-6px);
          opacity: 1;
        }
      }

      /* ---- Spinner (fallback) ---- */
      .spinner {
        display: inline-block;
        width: 16px;
        height: 16px;
        border: 2px solid transparent;
        border-top-color: var(--selora-accent);
        border-left-color: var(--selora-accent);
        border-radius: 50%;
        animation: spin 0.6s linear infinite;
      }
      @keyframes spin {
        to {
          transform: rotate(360deg);
        }
      }

      /* ---- Generating row ---- */
      .generating-row {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 14px 0;
        font-size: 12px;
        opacity: 0.7;
      }

      /* ---- Modal overlay (matches panel) ---- */
      .modal-overlay {
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.5);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 10001;
      }
      .modal {
        background: var(--card-background-color, #fff);
        border-radius: 12px;
        padding: 24px;
        max-width: 420px;
        width: 90%;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
      }
      .modal-title {
        font-size: 18px;
        font-weight: 700;
        margin: 0 0 16px;
      }
      .modal-label {
        font-size: 13px;
        font-weight: 500;
        display: block;
        margin-bottom: 6px;
      }
      .modal-row {
        display: flex;
        gap: 8px;
        align-items: center;
      }
      .modal-input {
        flex: 1;
        padding: 10px 12px;
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        background: var(--card-background-color);
        color: var(--primary-text-color);
        font-size: 14px;
        font-family: inherit;
        outline: none;
        transition: border-color 0.15s;
      }
      .modal-input:focus {
        border-color: var(--selora-accent);
      }
      .modal-input::placeholder {
        opacity: 0.35;
      }
      .modal-input.generating-placeholder {
        display: flex;
        align-items: center;
        gap: 8px;
        border-color: var(--selora-accent);
      }
      .modal-row.generating .modal-magic-btn {
        border-color: var(--selora-accent);
      }
      .modal-magic-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 8px 10px;
        flex-shrink: 0;
        border-radius: 6px;
        border: 1.5px solid var(--divider-color);
        background: var(--card-background-color);
        color: var(--primary-text-color);
        cursor: pointer;
        font-weight: 600;
        transition: opacity 0.15s;
      }
      .modal-magic-btn:hover {
        opacity: 0.85;
        border-color: #f59e0b;
        color: #f59e0b;
      }
      .modal-magic-btn ha-icon {
        --mdc-icon-size: 20px;
      }
      .modal-actions {
        display: flex;
        gap: 8px;
        margin-top: 16px;
        justify-content: flex-end;
      }
      .modal-btn {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 6px 14px;
        border-radius: 6px;
        font-size: 12px;
        font-weight: 600;
        cursor: pointer;
        font-family: inherit;
        border: 1.5px solid transparent;
        background: transparent;
        transition:
          background 0.15s,
          opacity 0.15s;
        user-select: none;
        letter-spacing: normal;
      }
      .modal-btn:hover {
        opacity: 0.85;
      }
      .modal-btn ha-icon {
        --mdc-icon-size: 14px;
      }
      .modal-cancel {
        border-color: var(--divider-color);
        color: var(--primary-text-color);
        background: var(--card-background-color);
      }
      .modal-cancel:hover {
        border-color: #f59e0b;
        color: #f59e0b;
      }
      .modal-create {
        background: #f59e0b;
        border-color: #f59e0b;
        color: #1a1a1a;
      }
      .modal-create:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }
    `;
  }

  getCardSize() {
    return 4;
  }
}

// ---------------------------------------------------------------------------
// Card Editor (minimal config UI)
// ---------------------------------------------------------------------------

class SeloraAICardEditor extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      _config: { type: Object },
    };
  }

  setConfig(config) {
    this._config = config;
  }

  _valueChanged(key, value) {
    const newConfig = { ...this._config, [key]: value };
    this._config = newConfig;
    const event = new CustomEvent("config-changed", {
      detail: { config: newConfig },
    });
    this.dispatchEvent(event);
  }

  render() {
    if (!this._config) return html``;
    return html`
      <div style="padding: 16px;">
        <ha-textfield
          label="Title"
          .value=${this._config.title || "Selora AI"}
          @change=${(e) => this._valueChanged("title", e.target.value)}
        ></ha-textfield>
        <ha-formfield label="Show Suggestions">
          <ha-switch
            .checked=${this._config.show_suggestions !== false}
            @change=${(e) =>
              this._valueChanged("show_suggestions", e.target.checked)}
          ></ha-switch>
        </ha-formfield>
        <ha-formfield label="Show Automations">
          <ha-switch
            .checked=${this._config.show_automations !== false}
            @change=${(e) =>
              this._valueChanged("show_automations", e.target.checked)}
          ></ha-switch>
        </ha-formfield>
        <ha-textfield
          label="Max Suggestions"
          type="number"
          .value=${String(this._config.max_suggestions || 3)}
          @change=${(e) =>
            this._valueChanged("max_suggestions", parseInt(e.target.value, 10))}
        ></ha-textfield>
        <ha-textfield
          label="Max Automations"
          type="number"
          .value=${String(this._config.max_automations || 10)}
          @change=${(e) =>
            this._valueChanged("max_automations", parseInt(e.target.value, 10))}
        ></ha-textfield>
      </div>
    `;
  }
}

// ---------------------------------------------------------------------------
// Register
// ---------------------------------------------------------------------------

customElements.define("selora-ai-card", SeloraAIDashboardCard);
customElements.define("selora-ai-card-editor", SeloraAICardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "selora-ai-card",
  name: "Selora AI",
  description:
    "Dashboard card for Selora AI automation suggestions, quick chat, and activity feed.",
  preview: true,
});
