import {
  LitElement,
  html,
  css,
} from "https://unpkg.com/lit-element@2.4.0/lit-element.js?module";

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
function renderMarkdown(text) {
  if (!text) return "";
  let escaped = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  // Code blocks (```)
  escaped = escaped.replace(/```([\s\S]*?)```/g, '<pre style="background:#2d2d2d;color:#f8f8f2;padding:10px;border-radius:6px;font-size:12px;overflow-x:auto;margin:8px 0;">$1</pre>');
  // Inline code
  escaped = escaped.replace(/`([^`]+)`/g, '<code style="background:rgba(255,255,255,0.1);padding:2px 5px;border-radius:3px;font-size:13px;">$1</code>');
  // Bold
  escaped = escaped.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  // Italic
  escaped = escaped.replace(/\*(.+?)\*/g, "<em>$1</em>");
  // Numbered lists: lines starting with "1. ", "2. ", etc.
  escaped = escaped.replace(/^(\d+)\.\s+(.+)$/gm, '<div style="margin:4px 0 4px 8px;"><strong>$1.</strong> $2</div>');
  // Bullet lists: lines starting with "- "
  escaped = escaped.replace(/^[-•]\s+(.+)$/gm, '<div style="margin:4px 0 4px 8px;padding-left:12px;border-left:2px solid rgba(255,255,255,0.15);">$1</div>');
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
    };
  }

  constructor() {
    super();
    this._sessions = [];
    this._activeSessionId = null;
    this._messages = [];
    this._input = "";
    this._loading = false;
    this._showSidebar = true;
    this._activeTab = "chat";
    this._suggestions = [];
    this._automations = [];
    this._expandedAutomations = {};
    this._editedYaml = {};
    this._savingYaml = {};
    this._config = null;
    this._savingConfig = false;
    this._newApiKey = "";
  }

  connectedCallback() {
    super.connectedCallback();
    this._loadSessions();
    this._loadSuggestions();
    this._loadAutomations();
  }

  // -------------------------------------------------------------------------
  // Data loaders
  // -------------------------------------------------------------------------

  async _loadSessions() {
    try {
      const sessions = await this.hass.callWS({ type: "selora_ai/get_sessions" });
      this._sessions = sessions || [];
      // Auto-open most recent session if no active session
      if (!this._activeSessionId && this._sessions.length > 0) {
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
      const { session_id } = await this.hass.callWS({ type: "selora_ai/new_session" });
      this._activeSessionId = session_id;
      this._messages = [];
      this._activeTab = "chat";
      await this._loadSessions();
      if (this.narrow) this._showSidebar = false;
    } catch (err) {
      console.error("Failed to create session", err);
    }
  }

  async _deleteSession(sessionId, evt) {
    evt.stopPropagation();
    if (!confirm("Delete this conversation?")) return;
    try {
      await this.hass.callWS({ type: "selora_ai/delete_session", session_id: sessionId });
      if (this._activeSessionId === sessionId) {
        this._activeSessionId = null;
        this._messages = [];
      }
      await this._loadSessions();
    } catch (err) {
      console.error("Failed to delete session", err);
    }
  }

  async _loadSuggestions() {
    try {
      const suggestions = await this.hass.callWS({ type: "selora_ai/get_suggestions" });
      this._suggestions = suggestions || [];
    } catch (err) {
      console.error("Failed to load suggestions", err);
    }
  }

  async _loadAutomations() {
    try {
      const automations = await this.hass.callWS({ type: "selora_ai/get_automations" });
      this._automations = automations || [];
    } catch (err) {
      console.error("Failed to load automations", err);
    }
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

      await this.hass.callWS({ type: "selora_ai/update_config", config: payload });
      this._newApiKey = "";
      await this._loadConfig();
      alert("Configuration saved successfully!");
    } catch (err) {
      alert("Failed to save configuration: " + err.message);
    } finally {
      this._savingConfig = false;
    }
  }

  // -------------------------------------------------------------------------
  // Messaging
  // -------------------------------------------------------------------------

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

      const unsub = await this.hass.connection.subscribeMessage(
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
            assistantMsg.automation_status = event.automation ? "pending" : null;
            assistantMsg.automation_message_index = event.automation_message_index ?? null;
            assistantMsg._streaming = false;
            this._messages = [...this._messages];
            this._loading = false;

            // Update session tracking
            if (event.session_id) {
              if (event.session_id !== this._activeSessionId) {
                this._activeSessionId = event.session_id;
              }
              this._loadSessions();
            }

            unsub();
          } else if (event.type === "error") {
            assistantMsg.content = "Sorry, I encountered an error: " + event.message;
            assistantMsg._streaming = false;
            this._messages = [...this._messages];
            this._loading = false;
            unsub();
          }
        },
        subscribePayload
      );
    } catch (err) {
      assistantMsg.content = "Sorry, I encountered an error: " + err.message;
      assistantMsg._streaming = false;
      this._messages = [...this._messages];
      this._loading = false;
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

  async _acceptAutomation(msgIndex, automation) {
    try {
      await this.hass.callWS({
        type: "selora_ai/create_automation",
        automation: automation,
      });
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

      this._messages = [
        ...this._messages,
        {
          role: "assistant",
          content: `Automation "${automation.alias}" created and added to your system (disabled by default for review).`,
          timestamp: new Date().toISOString(),
        },
      ];
    } catch (err) {
      alert("Failed to create automation: " + err.message);
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
      await this.hass.callWS({ type: "selora_ai/create_automation", automation });
      await this._loadAutomations();
      alert(`Automation "${automation.alias}" created.`);
    } catch (err) {
      alert("Failed to create automation: " + err.message);
    }
  }

  _discardSuggestion(suggestion) {
    this._suggestions = this._suggestions.filter((s) => s !== suggestion);
  }

  // Accept automation — if the user edited the YAML, send the edited version
  async _acceptAutomationWithEdits(msgIndex, automation, yamlKey) {
    const edited = this._editedYaml[yamlKey];
    if (edited && edited !== (this._originalYaml?.[yamlKey] ?? "")) {
      // Use edited YAML text
      try {
        this._savingYaml = { ...this._savingYaml, [yamlKey]: true };
        this.requestUpdate();
        await this.hass.callWS({ type: "selora_ai/apply_automation_yaml", yaml_text: edited });
        await this.hass.callWS({
          type: "selora_ai/set_automation_status",
          session_id: this._activeSessionId,
          message_index: msgIndex,
          status: "saved",
        });
        const session = await this.hass.callWS({ type: "selora_ai/get_session", session_id: this._activeSessionId });
        this._messages = session.messages || [];
        await this._loadAutomations();
        this._messages = [...this._messages, {
          role: "assistant",
          content: `Automation "${automation.alias}" created with your edits (disabled by default for review).`,
          timestamp: new Date().toISOString(),
        }];
      } catch (err) {
        alert("Failed to create automation from edited YAML: " + err.message);
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
        await this.hass.callWS({ type: "selora_ai/apply_automation_yaml", yaml_text: edited });
      } else {
        await this.hass.callWS({ type: "selora_ai/create_automation", automation: auto });
      }
      await this._loadAutomations();
      alert(`Automation "${auto.alias}" created.`);
    } catch (err) {
      alert("Failed to create automation: " + err.message);
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
    } catch (err) {
      alert("Failed to save changes: " + err.message);
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

  // -------------------------------------------------------------------------
  // Scroll to bottom on new messages
  // -------------------------------------------------------------------------

  updated(changedProps) {
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
        overflow: hidden;
      }

      /* ---- Sidebar (session list) ---- */
      .sidebar {
        width: 260px;
        min-width: 260px;
        display: flex;
        flex-direction: column;
        background: var(--sidebar-background-color, var(--card-background-color));
        border-right: 1px solid var(--divider-color);
        overflow: hidden;
      }
      .sidebar-header {
        padding: 16px;
        font-size: 13px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.08em;
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
        background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.12);
        border-left: 3px solid var(--primary-color);
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
        display: none;
      }
      :host([narrow]) .header-top ha-icon-button {
        display: inline-flex;
      }
      .tabs {
        display: flex;
        padding: 0 8px;
        border-top: 1px solid rgba(255,255,255,0.1);
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
      .tab:hover { opacity: 1; }
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
      }
      .empty-state ha-icon {
        --mdc-icon-size: 56px;
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
        background: var(--primary-color);
        color: white;
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
        margin-top: 3px;
      }
      .bubble.user + .bubble-meta { align-self: flex-end; }
      .bubble.assistant + .bubble-meta { align-self: flex-start; }

      /* ---- Automation proposal card ---- */
      .proposal-card {
        margin-top: 12px;
        border: 1px solid var(--primary-color);
        border-radius: 10px;
        overflow: hidden;
        background: var(--primary-background-color);
      }
      .proposal-header {
        background: rgba(var(--rgb-primary-color, 3,169,244), 0.1);
        padding: 10px 14px;
        font-size: 12px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        display: flex;
        align-items: center;
        gap: 6px;
        color: var(--primary-color);
      }
      .proposal-body {
        padding: 14px;
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
        letter-spacing: 0.08em;
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
      .yaml-toggle:hover { opacity: 1; }
      textarea.yaml-editor {
        width: 100%;
        box-sizing: border-box;
        background: #1e1e2e;
        color: #cdd6f4;
        padding: 10px 12px;
        border-radius: 6px;
        font-size: 11px;
        font-family: 'Fira Code', 'Cascadia Code', monospace;
        line-height: 1.5;
        border: 1px solid rgba(255,255,255,0.12);
        resize: vertical;
        min-height: 140px;
        outline: none;
        transition: border-color 0.15s;
      }
      textarea.yaml-editor:focus {
        border-color: var(--primary-color);
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
        font-family: 'Fira Code', 'Cascadia Code', monospace;
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
        background: rgba(76,175,80,0.12);
        color: var(--success-color, #4caf50);
      }
      .proposal-status.declined {
        background: rgba(158,158,158,0.12);
        color: var(--secondary-text-color);
      }

      /* ---- Automation flowchart ---- */
      .flow-chart {
        display: flex;
        flex-direction: column;
        align-items: flex-start;
        margin: 10px 0 12px;
        font-size: 12px;
      }
      .flow-section { width: 100%; }
      .flow-label {
        font-size: 9px;
        font-weight: 800;
        letter-spacing: 0.1em;
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
      .flow-node + .flow-node { margin-top: 3px; }
      .trigger-node {
        background: rgba(var(--rgb-primary-color, 3,169,244), 0.12);
        border: 1px solid rgba(var(--rgb-primary-color, 3,169,244), 0.35);
        color: var(--primary-color);
      }
      .condition-node {
        background: rgba(255,152,0,0.1);
        border: 1px solid rgba(255,152,0,0.35);
        color: var(--warning-color, #ff9800);
      }
      .action-node {
        background: rgba(76,175,80,0.1);
        border: 1px solid rgba(76,175,80,0.35);
        color: var(--success-color, #4caf50);
      }
      .flow-arrow {
        font-size: 16px;
        line-height: 1;
        opacity: 0.35;
        padding: 3px 0 3px 4px;
      }
      .flow-arrow-sm {
        font-size: 13px;
        line-height: 1;
        opacity: 0.3;
        padding: 2px 0 2px 4px;
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
      .toggle-switch input { opacity: 0; width: 0; height: 0; }
      .toggle-track {
        position: absolute;
        inset: 0;
        border-radius: 11px;
        background: var(--divider-color);
        border: 1px solid rgba(0,0,0,0.15);
        transition: background 0.2s;
      }
      .toggle-track.on { background: var(--success-color, #4caf50); }
      .toggle-thumb {
        position: absolute;
        top: 3px;
        left: 3px;
        width: 16px;
        height: 16px;
        border-radius: 50%;
        background: white;
        box-shadow: 0 1px 3px rgba(0,0,0,0.3);
        transition: left 0.2s;
      }
      .toggle-track.on .toggle-thumb { left: 21px; }
      .toggle-label {
        font-size: 12px;
        font-weight: 600;
        color: var(--secondary-text-color);
      }
      .toggle-label.on { color: var(--success-color, #4caf50); }

      /* ---- Card action buttons ---- */
      .card-actions {
        display: flex;
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
        transition: background 0.15s, opacity 0.15s;
        user-select: none;
        letter-spacing: 0.02em;
      }
      .btn:hover { opacity: 0.85; }
      .btn-primary {
        background: var(--primary-color);
        border-color: var(--primary-color);
        color: white;
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
      .btn-outline:hover { border-color: var(--primary-color); color: var(--primary-color); }
      .btn-danger {
        border-color: var(--error-color, #f44336);
        color: var(--error-color, #f44336);
        background: transparent;
      }
      .btn-danger:hover { background: rgba(244,67,54,0.08); }
      .btn-ghost {
        border-color: transparent;
        color: var(--secondary-text-color);
        background: transparent;
        font-size: 11px;
        padding: 4px 8px;
      }
      .btn-ghost:hover { color: var(--primary-text-color); background: rgba(0,0,0,0.06); border-color: var(--divider-color); }
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
      .expand-toggle:hover { opacity: 1; }

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
      .typing-dot:nth-child(1) { animation-delay: 0s; }
      .typing-dot:nth-child(2) { animation-delay: 0.2s; }
      .typing-dot:nth-child(3) { animation-delay: 0.4s; }
      @keyframes typingBounce {
        0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
        40% { transform: scale(1); opacity: 1; }
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
        50% { opacity: 0; }
      }

      /* ---- Scroll view (automations / settings) ---- */
      .scroll-view {
        flex: 1;
        overflow-y: auto;
        padding: 16px;
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
      .card h3 { margin: 0; font-size: 16px; }
      .card p { margin: 6px 0; color: var(--secondary-text-color); font-size: 13px; }
      .chip {
        padding: 3px 9px;
        border-radius: 10px;
        font-size: 10px;
        font-weight: 700;
        color: white;
      }
      .chip.ai-managed { background: #4caf50; }
      .chip.user-managed { background: #9e9e9e; }
      .chip.suggestion { background: var(--primary-color); }
      pre { background: #1e1e2e; color: #cdd6f4; padding: 10px; border-radius: 6px; font-size: 11px; overflow-x: auto; }

      /* ---- Settings ---- */
      .settings-form { max-width: 600px; margin: 0 auto; }
      .form-group { margin-bottom: 22px; }
      .form-group label { display: block; margin-bottom: 6px; font-weight: 500; font-size: 14px; }
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
      .key-not-set { font-size: 12px; opacity: 0.5; font-style: italic; margin-top: 4px; }
      .save-bar { margin-top: 28px; display: flex; justify-content: flex-end; }

      /* Narrow overrides */
      :host([narrow]) .sidebar {
        position: absolute;
        left: 0; top: 0; bottom: 0;
        z-index: 10;
        transform: translateX(-100%);
        transition: transform 0.25s ease;
        box-shadow: 2px 0 8px rgba(0,0,0,0.2);
      }
      :host([narrow]) .sidebar.open {
        transform: translateX(0);
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
        </div>
        <mwc-button class="new-chat-btn" outlined @click=${this._newSession}>
          + New Chat
        </mwc-button>
        <div class="session-list">
          ${this._sessions.length === 0
            ? html`<div style="padding: 16px; font-size: 12px; opacity: 0.5;">No conversations yet.</div>`
            : this._sessions.map(
                (s) => html`
                  <div
                    class="session-item ${s.id === this._activeSessionId ? "active" : ""}"
                    @click=${() => this._openSession(s.id)}
                  >
                    <div style="flex:1; min-width:0;">
                      <div class="session-title">${s.title}</div>
                      <div class="session-meta">${this._formatDate(s.updated_at)}</div>
                    </div>
                    <ha-icon
                      class="session-delete"
                      icon="mdi:delete-outline"
                      @click=${(e) => this._deleteSession(s.id, e)}
                      title="Delete"
                    ></ha-icon>
                  </div>
                `
              )}
        </div>
      </div>

      <div class="main">
        <div class="header">
          <div class="header-top">
            <ha-icon-button
              title="Conversations"
              @click=${() => (this._showSidebar = !this._showSidebar)}
            >
              <ha-icon icon="mdi:menu"></ha-icon>
            </ha-icon-button>
            <ha-icon icon="mdi:robot-confetti"></ha-icon>
            Selora AI Architect
          </div>
          <div class="tabs">
            <div class="tab ${this._activeTab === "chat" ? "active" : ""}" @click=${() => (this._activeTab = "chat")}>Chat</div>
            <div class="tab ${this._activeTab === "automations" ? "active" : ""}" @click=${() => (this._activeTab = "automations")}>Automations</div>
            <div class="tab ${this._activeTab === "settings" ? "active" : ""}" @click=${() => { this._activeTab = "settings"; this._loadConfig(); }}>Settings</div>
          </div>
        </div>

        ${this._activeTab === "chat" ? this._renderChat() : ""}
        ${this._activeTab === "automations" ? this._renderAutomations() : ""}
        ${this._activeTab === "settings" ? this._renderSettings() : ""}
      </div>
    `;
  }

  // -------------------------------------------------------------------------
  // Chat pane
  // -------------------------------------------------------------------------

  _renderChat() {
    return html`
      <div class="chat-pane">
        <div class="chat-messages" id="chat-messages">
          ${!this._activeSessionId || this._messages.length === 0
            ? html`
                <div class="empty-state">
                  <ha-icon icon="mdi:robot-happy-outline"></ha-icon>
                  <div style="font-size:16px; font-weight:500;">Start a conversation</div>
                  <div style="font-size:13px;">Ask me to build an automation, control a device, or answer a question about your home.</div>
                </div>
              `
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
            @keydown=${(e) => e.key === "Enter" && !e.shiftKey && this._sendMessage()}
            placeholder="Describe an automation or ask a question…"
            ?disabled=${this._loading}
            style="flex:1;"
          ></ha-textfield>
          <ha-icon-button
            @click=${this._sendMessage}
            ?disabled=${this._loading || !this._input.trim()}
            title="Send"
          >
            <ha-icon icon="mdi:send"></ha-icon>
          </ha-icon-button>
        </div>
      </div>
    `;
  }

  _renderMessage(msg, idx) {
    const isUser = msg.role === "user";
    // Hide empty streaming messages (typing indicator shown separately)
    if (msg._streaming && !msg.content) return html``;
    return html`
      <div class="message-row">
        <div class="bubble ${isUser ? "user" : "assistant"}">
          <span class="msg-content ${msg._streaming ? "streaming-cursor" : ""}" .innerHTML=${isUser ? msg.content : renderMarkdown(msg.content)}></span>
          ${msg.config_issue
            ? html`
                <div style="margin-top: 10px;">
                  <mwc-button dense raised @click=${this._goToSettings}>Go to Settings</mwc-button>
                </div>
              `
            : ""}
          ${msg.automation ? this._renderProposalCard(msg, idx) : ""}
        </div>
        <div class="bubble-meta">${isUser ? "You" : "Selora AI"} · ${this._formatTime(msg.timestamp)}</div>
      </div>
    `;
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
        @input=${(e) => this._onYamlInput(key, e.target.value)}
        spellcheck="false"
        autocomplete="off"
        rows="8"
      ></textarea>
      ${isDirty || onSave ? html`
        <div class="yaml-edit-bar">
          ${isDirty ? html`
            <span class="yaml-unsaved">
              <ha-icon icon="mdi:circle-edit-outline" style="--mdc-icon-size:13px;"></ha-icon>
              Unsaved changes
            </span>
          ` : html`<span style="flex:1;"></span>`}
          ${onSave ? html`
            <button class="btn btn-primary" ?disabled=${saving || !isDirty} @click=${() => onSave(key)}>
              <ha-icon icon="mdi:content-save" style="--mdc-icon-size:13px;"></ha-icon>
              ${saving ? "Saving…" : "Save changes"}
            </button>
          ` : ""}
        </div>
      ` : ""}
    `;
  }

  // -------------------------------------------------------------------------
  // Automation flowchart helpers
  // -------------------------------------------------------------------------

  /** Strip the domain prefix and underscores: "light.kitchen_lamp" → "kitchen lamp" */
  _fmtEntity(id) {
    if (!id) return "";
    const parts = String(id).split(".");
    return (parts.length > 1 ? parts.slice(1).join(".") : parts[0]).replace(/_/g, " ");
  }

  _fmtEntities(val) {
    if (!val) return "";
    const arr = Array.isArray(val) ? val : [val];
    return arr.map((e) => this._fmtEntity(e)).join(", ");
  }

  _describeFlowItem(item) {
    if (!item || typeof item !== "object") return String(item ?? "");

    // HA supports both 'platform' (classic) and 'trigger' (new format) keys on trigger objects
    const p = item.platform || item.trigger;

    // ── Triggers ──────────────────────────────────────────────────────────────
    if (p === "time") {
      const at = Array.isArray(item.at) ? item.at.join(", ") : item.at;
      return `At ${at}`;
    }
    if (p === "sun") {
      const ev = (item.event || "").replace(/_/g, " ");
      const offset = item.offset ? ` (${item.offset})` : "";
      return `Sun ${ev}${offset}`;
    }
    if (p === "state") {
      const eid = this._fmtEntities(item.entity_id);
      const from = item.from != null ? ` from "${item.from}"` : "";
      const to = item.to != null ? ` becomes "${item.to}"` : " changes";
      const dur = item.for ? ` for ${item.for}` : "";
      return `${eid}${from}${to}${dur}`;
    }
    if (p === "numeric_state") {
      const eid = this._fmtEntities(item.entity_id);
      if (item.above != null && item.below != null) return `${eid} between ${item.above} and ${item.below}`;
      if (item.above != null) return `${eid} rises above ${item.above}`;
      if (item.below != null) return `${eid} drops below ${item.below}`;
      return `${eid} value changes`;
    }
    if (p === "homeassistant") {
      const ev = item.event === "start" ? "starts up" : item.event === "shutdown" ? "shuts down" : (item.event || "event");
      return `Home Assistant ${ev}`;
    }
    if (p === "time_pattern") {
      if (item.seconds != null) return `Every ${item.seconds} seconds`;
      if (item.minutes != null) return `Every ${item.minutes} minutes`;
      if (item.hours != null) return `Every ${item.hours} hours`;
      return "On a time pattern";
    }
    if (p === "template") return "When a template becomes true";
    if (p === "event") return item.event_type ? `Event: ${String(item.event_type).replace(/_/g, " ")}` : "On an event";
    if (p === "device") return item.type ? String(item.type).replace(/_/g, " ") : "Device trigger";
    if (p === "zone") {
      const eid = this._fmtEntities(item.entity_id);
      const zone = this._fmtEntity(item.zone);
      const ev = (item.event || "enters").replace(/_/g, " ");
      return `${eid} ${ev} ${zone}`.trim();
    }
    if (p === "mqtt") return `MQTT: ${item.topic || "message received"}`;
    if (p === "webhook") return "Webhook received";
    if (p === "tag") return `Tag scanned${item.tag_id ? `: ${item.tag_id}` : ""}`;
    if (p === "geo_location") return "Geo-location event";
    if (p === "calendar") return `Calendar: ${item.event || "event"}`;
    if (p) return String(p).replace(/_/g, " "); // generic platform — still readable

    // ── Conditions (use 'condition' key) ──────────────────────────────────────
    const cond = item.condition;
    if (cond === "state") {
      const eid = this._fmtEntities(item.entity_id);
      const st = item.state ?? item.to;
      return `${eid} is "${st}"`;
    }
    if (cond === "numeric_state") {
      const eid = this._fmtEntities(item.entity_id);
      if (item.above != null && item.below != null) return `${eid} between ${item.above} and ${item.below}`;
      if (item.above != null) return `${eid} above ${item.above}`;
      if (item.below != null) return `${eid} below ${item.below}`;
      return `${eid} numeric check`;
    }
    if (cond === "time") {
      const parts = [];
      if (item.after) parts.push(`after ${item.after}`);
      if (item.before) parts.push(`before ${item.before}`);
      if (item.weekday) {
        const days = Array.isArray(item.weekday) ? item.weekday.join(", ") : item.weekday;
        parts.push(`on ${days}`);
      }
      return parts.length ? parts.join(" · ") : "Time window";
    }
    if (cond === "template") return "Template evaluates to true";
    if (cond === "sun") {
      const parts = [];
      if (item.after) parts.push(`after ${String(item.after).replace(/_/g, " ")}`);
      if (item.before) parts.push(`before ${String(item.before).replace(/_/g, " ")}`);
      return parts.join(", ") || "Sun position";
    }
    if (cond === "and") return `All ${(item.conditions || []).length} conditions must be true`;
    if (cond === "or") return `Any of ${(item.conditions || []).length} conditions is true`;
    if (cond === "not") return "None of the conditions are true";
    if (cond === "zone") {
      const eid = this._fmtEntities(item.entity_id);
      return `${eid} is in ${this._fmtEntity(item.zone) || "zone"}`;
    }
    if (cond === "device") return item.type ? String(item.type).replace(/_/g, " ") : "Device condition";
    if (cond) return String(cond).replace(/_/g, " ");

    // ── Actions ───────────────────────────────────────────────────────────────
    const svc = item.service || item.action;
    if (svc) {
      const [, svcName = svc] = String(svc).split(".");
      const name = svcName.replace(/_/g, " ");
      const targets = item.target?.entity_id ?? item.data?.entity_id;
      const t = this._fmtEntities(targets);
      const extras = [];
      if (item.data?.brightness_pct != null) extras.push(`${item.data.brightness_pct}% brightness`);
      if (item.data?.temperature != null) extras.push(`${item.data.temperature}°`);
      if (item.data?.color_temp != null) extras.push(`colour temp ${item.data.color_temp}`);
      if (item.data?.message) extras.push(`"${item.data.message}"`);
      if (item.data?.title) extras.push(item.data.title);
      const detail = [t, ...extras].filter(Boolean).join(", ");
      return detail ? `${name}: ${detail}` : name;
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
    if (item.choose) return `Choose between ${item.choose.length} option${item.choose.length !== 1 ? "s" : ""}`;
    if (item.repeat) {
      const r = item.repeat;
      if (r.count != null) return `Repeat ${r.count} time${r.count !== 1 ? "s" : ""}`;
      if (r.while) return "Repeat while condition holds";
      if (r.until) return "Repeat until condition is met";
      return "Repeat";
    }
    if (item.parallel) return `Run ${(item.parallel || []).length} actions in parallel`;
    if (item.sequence) return `Run a sequence of ${(item.sequence || []).length} steps`;
    if (item.variables) return "Set variables";
    if (item.stop) return `Stop: ${item.stop}`;
    if (item.event) return `Fire event: ${String(item.event).replace(/_/g, " ")}`;

    // ── Human-readable fallback — never show raw JSON ─────────────────────────
    const SKIP = new Set(["id", "enabled", "mode", "alias", "description"]);
    const readable = Object.entries(item)
      .filter(([k, v]) => !SKIP.has(k) && v != null && v !== "")
      .map(([k, v]) => {
        const label = k.replace(/_/g, " ");
        const val = Array.isArray(v) ? v.map((x) => (typeof x === "object" ? "…" : x)).join(", ") : v;
        return `${label}: ${val}`;
      })
      .slice(0, 3);
    return readable.length ? readable.join(" · ") : "Step";
  }

  _renderAutomationFlowchart(auto) {
    if (!auto) return html``;
    const triggers = (() => { const t = auto.triggers ?? auto.trigger ?? []; return Array.isArray(t) ? t : [t]; })();
    const conditions = (() => { const c = auto.conditions ?? auto.condition ?? []; return Array.isArray(c) ? c : [c]; })().filter(Boolean);
    const actions = (() => { const a = auto.actions ?? auto.action ?? []; return Array.isArray(a) ? a : [a]; })();
    if (!triggers.length && !actions.length) return html``;
    return html`
      <div class="flow-chart">
        <div class="flow-section">
          <div class="flow-label">Trigger</div>
          ${triggers.map((t) => html`<div class="flow-node trigger-node">${this._describeFlowItem(t)}</div>`)}
        </div>
        ${conditions.length ? html`
          <div class="flow-arrow">↓</div>
          <div class="flow-section">
            <div class="flow-label">Condition</div>
            ${conditions.map((c) => html`<div class="flow-node condition-node">${this._describeFlowItem(c)}</div>`)}
          </div>
        ` : ""}
        <div class="flow-arrow">↓</div>
        <div class="flow-section">
          <div class="flow-label">Actions</div>
          ${actions.map((a, i) => html`
            ${i > 0 ? html`<div class="flow-arrow-sm">↓</div>` : ""}
            <div class="flow-node action-node">${this._describeFlowItem(a)}</div>
          `)}
        </div>
      </div>
    `;
  }

  _renderProposalCard(msg, msgIndex) {
    const status = msg.automation_status;
    const automation = msg.automation;
    const yaml = msg.automation_yaml || "";

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
              <ha-icon icon="mdi:check"></ha-icon> Saved to your system (disabled — enable in Automations)
            </div>
          </div>
        </div>
      `;
    }

    if (status === "declined") {
      return html`
        <div class="proposal-card" style="margin-top:12px; opacity:0.6;">
          <div class="proposal-header" style="color:var(--secondary-text-color);">
            <ha-icon icon="mdi:close-circle-outline"></ha-icon>
            Automation Declined
          </div>
          <div class="proposal-body">
            <div class="proposal-name">${automation.alias}</div>
            <div class="proposal-status declined">Dismissed. You can refine it by replying below.</div>
          </div>
        </div>
      `;
    }

    if (status === "refining") {
      return html`
        <div class="proposal-card" style="margin-top:12px; opacity:0.75;">
          <div class="proposal-header" style="color:var(--warning-color, #ff9800);">
            <ha-icon icon="mdi:pencil-circle-outline"></ha-icon>
            Being Refined
          </div>
          <div class="proposal-body">
            <div class="proposal-name">${automation.alias}</div>
            <div class="proposal-status" style="background:rgba(255,152,0,0.1); color:var(--warning-color,#ff9800);">
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
    const hasEdits = this._editedYaml[yamlKey] !== undefined && this._editedYaml[yamlKey] !== yaml;
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
                <div class="proposal-description-label">What this automation does</div>
                <div class="proposal-description">${msg.description}</div>
              `
            : ""}

          ${this._renderAutomationFlowchart(automation)}

          <div class="yaml-toggle" @click=${() => this._toggleYaml(msgIndex)}>
            <ha-icon icon="mdi:code-braces" style="--mdc-icon-size:14px;"></ha-icon>
            ${yamlOpen ? "Hide YAML" : "Edit YAML"}
          </div>
          ${yamlOpen ? this._renderYamlEditor(yamlKey, yaml) : ""}

          <div class="proposal-verify">
            ${hasEdits ? "Your YAML edits will be used when you accept." : "Does the flow above match what you intended?"}
          </div>

          <div class="proposal-actions">
            <button class="btn btn-success" @click=${() => this._acceptAutomationWithEdits(msgIndex, automation, yamlKey)}>
              <ha-icon icon="mdi:check" style="--mdc-icon-size:14px;"></ha-icon>
              Accept &amp; Save
            </button>
            <button class="btn btn-outline" @click=${() => this._refineAutomation(msgIndex, automation, msg.description)}>
              <ha-icon icon="mdi:pencil" style="--mdc-icon-size:14px;"></ha-icon>
              Refine
            </button>
            <button class="btn btn-danger" @click=${() => this._declineAutomation(msgIndex)}>
              <ha-icon icon="mdi:close" style="--mdc-icon-size:14px;"></ha-icon>
              Decline
            </button>
          </div>
        </div>
      </div>
    `;
  }

  _toggleYaml(msgIndex) {
    this._yamlOpen = { ...(this._yamlOpen || {}), [msgIndex]: !((this._yamlOpen || {})[msgIndex]) };
    this.requestUpdate();
  }

  // -------------------------------------------------------------------------
  // Automations tab
  // -------------------------------------------------------------------------

  _toggleExpandAutomation(key) {
    this._expandedAutomations = { ...this._expandedAutomations, [key]: !this._expandedAutomations[key] };
    this.requestUpdate();
  }

  async _toggleAutomation(entityId, currentState) {
    try {
      const service = currentState === "on" ? "turn_off" : "turn_on";
      await this.hass.callService("automation", service, { entity_id: entityId });
      await this._loadAutomations();
    } catch (err) {
      console.error("Failed to toggle automation", err);
    }
  }

  _renderAutomations() {
    return html`
      <div class="scroll-view">
        ${this._automations.length > 0
          ? html`
              <h2 style="margin-top:0;">Active Automations</h2>
              ${this._automations.map((a) => {
                const expanded = !!this._expandedAutomations[a.entity_id];
                const isOn = a.state === "on";
                return html`
                  <div class="card">
                    <div class="card-header">
                      <h3 style="flex:1;">${a.alias}</h3>
                      <div class="chip ${a.is_selora ? "ai-managed" : "user-managed"}" style="margin-right:8px;">
                        ${a.is_selora ? "SELORA" : "USER"}
                      </div>
                    </div>

                    ${a.description
                      ? html`<p>${a.description.replace("[Selora AI] ", "")}</p>`
                      : ""}

                    <div class="toggle-row">
                      <label class="toggle-switch" @click=${() => this._toggleAutomation(a.entity_id, a.state)}>
                        <div class="toggle-track ${isOn ? "on" : ""}">
                          <div class="toggle-thumb"></div>
                        </div>
                      </label>
                      <span class="toggle-label ${isOn ? "on" : ""}">${isOn ? "Enabled" : "Disabled"}</span>
                      ${a.last_triggered
                        ? html`<span style="font-size:11px; opacity:0.5; margin-left:auto;">Last run: ${new Date(a.last_triggered).toLocaleString()}</span>`
                        : ""}
                    </div>

                    ${(a.trigger?.length || a.action?.length)
                      ? html`
                          <div class="expand-toggle" @click=${() => this._toggleExpandAutomation(a.entity_id)}>
                            <ha-icon icon="mdi:chevron-${expanded ? "up" : "down"}" style="--mdc-icon-size:14px;"></ha-icon>
                            ${expanded ? "Hide flow" : "View flow"}
                          </div>
                          ${expanded ? this._renderAutomationFlowchart(a) : ""}
                        `
                      : ""}

                    ${a.yaml_text
                      ? html`
                          <div class="expand-toggle" @click=${() => this._toggleExpandAutomation(`yaml_${a.entity_id}`)}>
                            <ha-icon icon="mdi:code-braces" style="--mdc-icon-size:13px;"></ha-icon>
                            ${this._expandedAutomations[`yaml_${a.entity_id}`] ? "Hide YAML" : "Edit YAML"}
                          </div>
                          ${this._expandedAutomations[`yaml_${a.entity_id}`]
                            ? this._renderYamlEditor(
                                `yaml_${a.entity_id}`,
                                a.yaml_text,
                                (key) => this._saveActiveAutomationYaml(a.automation_id, key)
                              )
                            : ""}
                        `
                      : ""}

                    <div class="card-actions">
                      <button class="btn ${isOn ? "btn-outline btn-danger" : "btn-success"}"
                        @click=${() => this._toggleAutomation(a.entity_id, a.state)}>
                        <ha-icon icon="mdi:${isOn ? "pause" : "play"}" style="--mdc-icon-size:13px;"></ha-icon>
                        ${isOn ? "Disable" : "Enable"}
                      </button>
                    </div>
                  </div>
                `;
              })}
              <div style="border-top: 1px solid var(--divider-color); margin: 24px 0 16px;"></div>
            `
          : ""}

        <h2 style="margin-top:0;">AI Recommendations</h2>
        ${this._suggestions.length === 0
          ? html`
              <div style="display:flex; flex-direction:column; align-items:center; opacity:0.45; padding:32px 0; gap:12px;">
                <ha-icon icon="mdi:robot-vacuum-variant" style="--mdc-icon-size:56px;"></ha-icon>
                <p>No recommendations yet — background analysis runs hourly.</p>
              </div>
            `
          : this._suggestions.map((item) => {
              const auto = item.automation || item.automation_data;
              const key = `sug_${auto.alias}`;
              const expanded = !!this._expandedAutomations[key];
              const origYaml = item.automation_yaml || "";
              return html`
                <div class="card">
                  <div class="card-header">
                    <h3>${auto.alias}</h3>
                    <div class="chip suggestion">RECOMMENDED</div>
                  </div>
                  ${auto.description ? html`<p>${auto.description}</p>` : ""}

                  ${this._renderAutomationFlowchart(auto)}

                  <div class="expand-toggle" @click=${() => this._toggleExpandAutomation(key)}>
                    <ha-icon icon="mdi:code-braces" style="--mdc-icon-size:13px;"></ha-icon>
                    ${expanded ? "Hide YAML" : "Edit YAML"}
                  </div>
                  ${expanded ? this._renderYamlEditor(key, origYaml) : ""}

                  <div class="card-actions">
                    <button class="btn btn-outline" @click=${() => this._discardSuggestion(item)}>
                      <ha-icon icon="mdi:trash-can-outline" style="--mdc-icon-size:13px;"></ha-icon>
                      Discard
                    </button>
                    <button class="btn btn-primary" ?disabled=${!!this._savingYaml[key]}
                      @click=${() => this._createSuggestionWithEdits(auto, key, origYaml)}>
                      <ha-icon icon="mdi:plus" style="--mdc-icon-size:13px;"></ha-icon>
                      ${this._savingYaml[key] ? "Creating…" : "Create Automation"}
                    </button>
                  </div>
                </div>
              `;
            })}
      </div>
    `;
  }

  // -------------------------------------------------------------------------
  // Settings tab
  // -------------------------------------------------------------------------

  _renderSettings() {
    if (!this._config) {
      return html`
        <div class="scroll-view" style="display:flex; justify-content:center; padding-top:64px;">
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
              @change=${(e) => this._updateConfig("llm_provider", e.target.value)}
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
                    ? html`<div class="key-hint">Current key: ${this._config.anthropic_api_key_hint}</div>`
                    : html`<div class="key-not-set">No API key set.</div>`}
                  <ha-textfield
                    label="${this._config.anthropic_api_key_set ? "Enter new key to replace" : "Enter API key"}"
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
                    @input=${(e) => this._updateConfig("anthropic_model", e.target.value)}
                  ></ha-textfield>
                </div>
              `
            : isOpenAI ? html`
                <div class="form-group">
                  <label>OpenAI API Key</label>
                  ${this._config.openai_api_key_set
                    ? html`<div class="key-hint">Current key: ${this._config.openai_api_key_hint}</div>`
                    : html`<div class="key-not-set">No API key set.</div>`}
                  <ha-textfield
                    label="${this._config.openai_api_key_set ? "Enter new key to replace" : "Enter API key"}"
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
                    @input=${(e) => this._updateConfig("openai_model", e.target.value)}
                  ></ha-textfield>
                </div>
            ` : html`
                <div class="form-group">
                  <ha-textfield
                    label="Ollama Host"
                    .value=${this._config.ollama_host}
                    @input=${(e) => this._updateConfig("ollama_host", e.target.value)}
                  ></ha-textfield>
                </div>
                <div class="form-group">
                  <ha-textfield
                    label="Ollama Model"
                    .value=${this._config.ollama_model}
                    @input=${(e) => this._updateConfig("ollama_model", e.target.value)}
                  ></ha-textfield>
                </div>
              `}

          <h3 style="border-bottom:1px solid var(--divider-color); padding-bottom:8px;">Background Services</h3>

          <div style="margin-top:16px;">
            <div style="display:flex; align-items:center; gap:8px; margin-bottom:16px;">
              <ha-switch
                .checked=${this._config.collector_enabled}
                @change=${(e) => this._updateConfig("collector_enabled", e.target.checked)}
              ></ha-switch>
              <label>Data Collector (AI Analysis)</label>
            </div>

            ${this._config.collector_enabled
              ? html`
                  <div style="padding-left:20px; border-left:2px solid var(--divider-color); margin-bottom:20px;">
                    <div class="form-group">
                      <label>Mode</label>
                      <select
                        .value=${this._config.collector_mode}
                        @change=${(e) => this._updateConfig("collector_mode", e.target.value)}
                        style="padding:8px; border-radius:4px; background:var(--card-background-color); color:var(--primary-text-color); border:1px solid var(--divider-color); width:100%;"
                      >
                        <option value="continuous">Continuous</option>
                        <option value="scheduled">Scheduled Window</option>
                      </select>
                    </div>
                    <div class="form-group">
                      <ha-textfield label="Interval (seconds)" type="number"
                        .value=${this._config.collector_interval}
                        @input=${(e) => this._updateConfig("collector_interval", parseInt(e.target.value))}
                      ></ha-textfield>
                    </div>
                    ${this._config.collector_mode === "scheduled"
                      ? html`
                          <div style="display:flex; gap:12px;">
                            <ha-textfield label="Start (HH:MM)" .value=${this._config.collector_start_time} @input=${(e) => this._updateConfig("collector_start_time", e.target.value)} style="flex:1;"></ha-textfield>
                            <ha-textfield label="End (HH:MM)"   .value=${this._config.collector_end_time}   @input=${(e) => this._updateConfig("collector_end_time",   e.target.value)} style="flex:1;"></ha-textfield>
                          </div>
                        `
                      : ""}
                  </div>
                `
              : ""}

            <div style="display:flex; align-items:center; gap:8px; margin-bottom:16px;">
              <ha-switch
                .checked=${this._config.discovery_enabled}
                @change=${(e) => this._updateConfig("discovery_enabled", e.target.checked)}
              ></ha-switch>
              <label>Network Discovery</label>
            </div>

            ${this._config.discovery_enabled
              ? html`
                  <div style="padding-left:20px; border-left:2px solid var(--divider-color); margin-bottom:20px;">
                    <div class="form-group">
                      <label>Mode</label>
                      <select
                        .value=${this._config.discovery_mode}
                        @change=${(e) => this._updateConfig("discovery_mode", e.target.value)}
                        style="padding:8px; border-radius:4px; background:var(--card-background-color); color:var(--primary-text-color); border:1px solid var(--divider-color); width:100%;"
                      >
                        <option value="continuous">Continuous</option>
                        <option value="scheduled">Scheduled Window</option>
                      </select>
                    </div>
                    <div class="form-group">
                      <ha-textfield label="Interval (seconds)" type="number"
                        .value=${this._config.discovery_interval}
                        @input=${(e) => this._updateConfig("discovery_interval", parseInt(e.target.value))}
                      ></ha-textfield>
                    </div>
                    ${this._config.discovery_mode === "scheduled"
                      ? html`
                          <div style="display:flex; gap:12px;">
                            <ha-textfield label="Start (HH:MM)" .value=${this._config.discovery_start_time} @input=${(e) => this._updateConfig("discovery_start_time", e.target.value)} style="flex:1;"></ha-textfield>
                            <ha-textfield label="End (HH:MM)"   .value=${this._config.discovery_end_time}   @input=${(e) => this._updateConfig("discovery_end_time",   e.target.value)} style="flex:1;"></ha-textfield>
                          </div>
                        `
                      : ""}
                  </div>
                `
              : ""}
          </div>

          <div class="save-bar">
            <mwc-button raised @click=${this._saveConfig} ?disabled=${this._savingConfig}>
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
      return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch {
      return "";
    }
  }
}

customElements.define("selora-ai-architect", SeloraAIArchitectPanel);
