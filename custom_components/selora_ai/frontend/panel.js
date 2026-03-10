
import {
  LitElement,
  html,
  css,
} from "https://unpkg.com/lit-element@2.4.0/lit-element.js?module";

class SeloraAIArchitectPanel extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      narrow: { type: Boolean },
      route: { type: Object },
      panel: { type: Object },
      _messages: { type: Array },
      _input: { type: String },
      _loading: { type: Boolean },
      _suggestions: { type: Array },
      _automations: { type: Array },
      _activeTab: { type: String },
      _config: { type: Object },
      _savingConfig: { type: Boolean },
      _flashApiKey: { type: Boolean },
    };
  }

  constructor() {
    super();
    this._messages = [
      {
        role: "assistant",
        content: "Hello! I am your Smart Home Architect. You can use me to build complex automations, or just give simple commands like \"turn off the kitchen lights\" which I'll pass directly to Home Assistant.",
      },
    ];
    this._input = "";
    this._loading = false;
    this._suggestions = [];
    this._automations = [];
    this._activeTab = "chat";
    this._config = null;
    this._savingConfig = false;
    this._flashApiKey = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this._loadSuggestions();
    this._loadAutomations();
  }

  async _loadAutomations() {
    try {
      const automations = await this.hass.callWS({
        type: "selora_ai/get_automations",
      });
      this._automations = automations || [];
    } catch (err) {
      console.error("Failed to load automations", err);
    }
  }

  async _loadSuggestions() {
    try {
      const suggestions = await this.hass.callWS({
        type: "selora_ai/get_suggestions",
      });
      this._suggestions = suggestions || [];
      
      if (this._suggestions.length > 0 && this._messages.length === 1) {
        this._messages = [
          ...this._messages,
          {
            role: "assistant",
            content: `I've analyzed your home patterns and have ${this._suggestions.length} automated recommendations for you. Check the "Automations" tab or ask me to refine them here!`,
          }
        ];
      }
    } catch (err) {
      console.error("Failed to load suggestions", err);
    }
  }

  async _loadConfig() {
    try {
      const config = await this.hass.callWS({
        type: "selora_ai/get_config",
      });
      this._config = config;
    } catch (err) {
      console.error("Failed to load config", err);
    }
  }

  async _saveConfig() {
    if (!this._config || this._savingConfig) return;
    this._savingConfig = true;
    try {
      await this.hass.callWS({
        type: "selora_ai/update_config",
        config: this._config,
      });
      // Show success toast/message if possible in HA
      alert("Configuration saved successfully!");
    } catch (err) {
      alert("Failed to save configuration: " + err.message);
    } finally {
      this._savingConfig = false;
    }
  }

  static get styles() {
    return css`
      :host {
        display: flex;
        flex-direction: column;
        height: 100%;
        background-color: var(--primary-background-color);
        color: var(--primary-text-color);
        font-family: var(--paper-font-body1_-_font-family, roboto, sans-serif);
      }
      .header {
        background-color: var(--app-header-background-color);
        color: var(--app-header-text-color);
        box-shadow: var(--card-box-shadow);
        z-index: 2;
      }
      .header-top {
        padding: 16px;
        font-size: 20px;
        font-weight: 500;
        display: flex;
        align-items: center;
      }
      .header-top ha-icon {
        margin-right: 12px;
      }
      .tabs {
        display: flex;
        padding: 0 8px;
        border-top: 1px solid rgba(255, 255, 255, 0.1);
      }
      .tab {
        padding: 12px 24px;
        cursor: pointer;
        font-weight: 500;
        opacity: 0.7;
        border-bottom: 3px solid transparent;
        transition: all 0.2s ease;
        text-transform: uppercase;
        font-size: 14px;
      }
      .tab:hover {
        opacity: 1;
      }
      .tab.active {
        opacity: 1;
        border-bottom-color: var(--accent-color, #ff9800);
      }
      
      .content {
        flex: 1;
        overflow: hidden;
        display: flex;
        flex-direction: column;
      }

      /* Chat Styles */
      .chat-container {
        flex: 1;
        overflow-y: auto;
        padding: 20px;
        display: flex;
        flex-direction: column;
        gap: 16px;
      }
      .message-wrapper {
        display: flex;
        flex-direction: column;
      }
      .message {
        max-width: 85%;
        padding: 14px 18px;
        border-radius: 18px;
        line-height: 1.5;
        font-size: 15px;
        position: relative;
        word-wrap: break-word;
      }
      .message.user {
        align-self: flex-end;
        background-color: var(--primary-color);
        color: white;
        border-bottom-right-radius: 4px;
      }
      .message.assistant {
        align-self: flex-start;
        background-color: var(--card-background-color);
        color: var(--primary-text-color);
        border-bottom-left-radius: 4px;
        box-shadow: var(--card-box-shadow);
      }
      .message-info {
        font-size: 11px;
        margin-top: 4px;
        opacity: 0.6;
      }
      .message.user + .message-info {
        align-self: flex-end;
        margin-right: 4px;
      }
      .message.assistant + .message-info {
        align-self: flex-start;
        margin-left: 4px;
      }

      .input-container {
        padding: 16px;
        background-color: var(--card-background-color);
        display: flex;
        gap: 12px;
        align-items: center;
        border-top: 1px solid var(--divider-color);
      }
      paper-input {
        flex: 1;
        --paper-input-container-focus-color: var(--primary-color);
      }
      
      /* Automations Styles */
      .scroll-view {
        flex: 1;
        overflow-y: auto;
        padding: 16px;
      }
      .card {
        background-color: var(--card-background-color);
        border-radius: 12px;
        padding: 16px;
        margin-bottom: 16px;
        box-shadow: var(--card-box-shadow);
        border: 1px solid var(--divider-color);
      }
      .card-header {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        margin-bottom: 12px;
      }
      .chip {
        padding: 4px 10px;
        background-color: var(--primary-color);
        color: white;
        border-radius: 12px;
        font-size: 10px;
        font-weight: bold;
      }
      .chip.ai-managed {
        background-color: #4caf50;
      }
      .chip.user-managed {
        background-color: #9e9e9e;
      }
      .chip.suggestion {
        background-color: var(--primary-color);
      }
      .card h3 {
        margin: 0;
        font-size: 18px;
      }
      .card p {
        margin: 8px 0;
        color: var(--secondary-text-color);
      }
      pre {
        background-color: #2d2d2d;
        color: #f8f8f2;
        padding: 12px;
        border-radius: 8px;
        font-size: 12px;
        overflow-x: auto;
        font-family: 'Fira Code', 'Consolas', monospace;
      }
      
      /* Settings Styles */
      .settings-form {
        max-width: 600px;
        margin: 0 auto;
        width: 100%;
      }
      .form-group {
        margin-bottom: 24px;
      }
      .form-group label {
        display: block;
        margin-bottom: 8px;
        font-weight: 500;
      }
      select, paper-input {
        width: 100%;
      }
      .save-bar {
        margin-top: 32px;
        display: flex;
        justify-content: flex-end;
      }

      .loading-indicator {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 10px;
        color: var(--secondary-text-color);
        font-style: italic;
      }
      
      @keyframes flash {
        0% { background-color: transparent; }
        50% { background-color: var(--accent-color, #ff9800); }
        100% { background-color: transparent; }
      }
      .flash {
        animation: flash 1s ease-in-out 2;
        border-radius: 4px;
      }
    `;
  }

  render() {
    return html`
      <div class="header">
        <div class="header-top">
          <ha-icon icon="mdi:robot-confetti"></ha-icon>
          Selora AI Architect
        </div>
        <div class="tabs">
          <div 
            class="tab ${this._activeTab === "chat" ? "active" : ""}" 
            @click=${() => this._activeTab = "chat"}
          >
            Chat
          </div>
          <div 
            class="tab ${this._activeTab === "automations" ? "active" : ""}" 
            @click=${() => this._activeTab = "automations"}
          >
            Automations
          </div>
          <div 
            class="tab ${this._activeTab === "settings" ? "active" : ""}" 
            @click=${() => { this._activeTab = "settings"; this._loadConfig(); }}
          >
            Settings
          </div>
        </div>
      </div>

      <div class="content">
        ${this._activeTab === "chat" ? this.renderChat() : ""}
        ${this._activeTab === "automations" ? this.renderAutomations() : ""}
        ${this._activeTab === "settings" ? this.renderSettings() : ""}
      </div>
    `;
  }

  renderChat() {
    return html`
      <div class="chat-container" id="chat">
        ${this._messages.map(
          (msg) => html`
            <div class="message-wrapper">
              <div class="message ${msg.role}">
                ${msg.content}
                ${msg.automation ? this.renderInlineAutomation(msg.automation, msg.automation_yaml) : ""}
                ${msg.config_issue ? html`
                  <div style="margin-top: 12px;">
                    <mwc-button dense raised @click=${this._goToSettings}>
                      Go to Settings
                    </mwc-button>
                  </div>
                ` : ""}
              </div>
              <div class="message-info">
                ${msg.role === "assistant" ? "Selora AI" : "You"}
              </div>
            </div>
          `
        )}
        ${this._loading ? html`
          <div class="loading-indicator">
            <ha-circular-progress active size="small"></ha-circular-progress>
            Architect is thinking...
          </div>
        ` : ""}
      </div>
      <div class="input-container">
        <ha-textfield
          .value=${this._input}
          @input=${(e) => (this._input = e.target.value)}
          @keydown=${(e) => e.key === "Enter" && this._sendMessage()}
          placeholder="Describe an automation you want..."
          ?disabled=${this._loading}
          style="flex: 1;"
        >
        </ha-textfield>
        <ha-icon-button
          @click=${this._sendMessage}
          ?disabled=${this._loading || !this._input.trim()}
          title="Send"
        >
          <ha-icon icon="mdi:send"></ha-icon>
        </ha-icon-button>
      </div>
    `;
  }

  renderInlineAutomation(automation, yaml) {
    return html`
      <div style="margin-top: 12px; padding: 10px; background: rgba(0,0,0,0.05); border-radius: 8px; border-left: 3px solid var(--primary-color);">
        <div style="font-weight: bold; font-size: 13px;">${automation.alias}</div>
        <pre style="margin: 8px 0; font-size: 11px;">${yaml}</pre>
        <mwc-button dense raised @click=${() => this._createAutomation(automation)}>
          Create This Automation
        </mwc-button>
      </div>
    `;
  }

  renderAutomations() {
    return html`
      <div class="scroll-view">
        ${this._automations.length > 0 ? html`
          <h2 style="margin-top: 0;">Active Automations</h2>
          ${this._automations.map(automation => html`
            <div class="card">
              <div class="card-header">
                <h3>${automation.alias}</h3>
                <div class="chip ${automation.is_selora ? "ai-managed" : "user-managed"}">
                  ${automation.is_selora ? "SELORA MANAGED" : "USER MANAGED"}
                </div>
              </div>
              <p>${automation.description || "No description provided."}</p>
              <div style="display: flex; align-items: center; gap: 8px; margin-top: 8px; font-size: 12px; opacity: 0.7;">
                <ha-icon icon="mdi:power" style="--mdc-icon-size: 14px; color: ${automation.state === "on" ? "var(--success-color, #4caf50)" : "var(--error-color, #f44336)"}"></ha-icon>
                <span>Status: ${automation.state.toUpperCase()}</span>
                ${automation.last_triggered ? html`
                  <span style="margin-left: 12px;">Last Run: ${new Date(automation.last_triggered).toLocaleString()}</span>
                ` : ""}
              </div>
            </div>
          `)}
          <div style="height: 32px;"></div>
        ` : ""}

        <h2 style="${this._automations.length > 0 ? "border-top: 1px solid var(--divider-color); padding-top: 24px;" : "margin-top: 0;"}">
          Architect Recommendations
        </h2>
        ${this._suggestions.length === 0 ? html`
          <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; opacity: 0.5; padding: 32px 0;">
            <ha-icon icon="mdi:robot-vacuum-variant" style="--mdc-icon-size: 64px; margin-bottom: 16px;"></ha-icon>
            <p>No recommendations yet. Chat with me or wait for background analysis!</p>
          </div>
        ` : html`
          <p>Based on your home's usage patterns, I suggest these automations:</p>
          ${this._suggestions.map(item => {
            const automation = item.automation || item.automation_data;
            return html`
              <div class="card">
                <div class="card-header">
                  <h3>${automation.alias}</h3>
                  <div class="chip suggestion">AI RECOMMENDED</div>
                </div>
                <p>${automation.description}</p>
                <pre>${item.automation_yaml}</pre>
                <div style="display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px;">
                  <mwc-button dense outlined @click=${() => this._discardSuggestion(item)}>Discard</mwc-button>
                  <mwc-button dense raised @click=${() => this._createAutomation(automation)}>Create Automation</mwc-button>
                </div>
              </div>
            `;
          })}
        `}
      </div>
    `;
  }

  renderSettings() {
    if (!this._config) {
      return html`
        <div class="scroll-view" style="display: flex; justify-content: center; padding-top: 64px;">
          <ha-circular-progress active></ha-circular-progress>
        </div>
      `;
    }

    return html`
      <div class="scroll-view">
        <div class="settings-form">
          <h2>Integration Settings</h2>
          <p style="margin-bottom: 32px; color: var(--secondary-text-color);">
            Configure your AI provider and API keys. Changes require saving.
          </p>

          <div class="form-group">
            <label>LLM Provider</label>
            <select 
              .value=${this._config.llm_provider}
              @change=${(e) => this._updateConfig("llm_provider", e.target.value)}
              style="padding: 8px; border-radius: 4px; background: var(--card-background-color); color: var(--primary-text-color); border: 1px solid var(--divider-color);"
            >
              <option value="anthropic">Anthropic (Claude)</option>
              <option value="ollama">Ollama (Local)</option>
            </select>
          </div>

          ${this._config.llm_provider === "anthropic" ? html`
            <div class="form-group ${this._flashApiKey ? "flash" : ""}">
              <ha-textfield
                id="api-key-field"
                label="Anthropic API Key"
                type="password"
                .value=${this._config.anthropic_api_key}
                @input=${(e) => this._updateConfig("anthropic_api_key", e.target.value)}
              ></ha-textfield>
            </div>
            <div class="form-group">
              <ha-textfield
                label="Anthropic Model"
                .value=${this._config.anthropic_model}
                @input=${(e) => this._updateConfig("anthropic_model", e.target.value)}
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

          <div class="save-bar">
            <mwc-button raised @click=${this._saveConfig} ?disabled=${this._savingConfig}>
              ${this._savingConfig ? "Saving..." : "Save Settings"}
            </mwc-button>
          </div>
        </div>
      </div>
    `;
  }

  _updateConfig(key, value) {
    this._config = { ...this._config, [key]: value };
    this.requestUpdate();
  }

  updated(changedProps) {
    if (changedProps.has("_messages") && this._activeTab === "chat") {
      const container = this.shadowRoot.getElementById("chat");
      if (container) {
        container.scrollTop = container.scrollHeight;
      }
    }
  }

  async _sendMessage() {
    if (!this._input.trim() || this._loading) return;

    const userMsg = this._input;
    this._messages = [...this._messages, { role: "user", content: userMsg }];
    this._input = "";
    this._loading = true;

    try {
      const response = await this.hass.callWS({
        type: "selora_ai/chat",
        message: userMsg,
      });

      this._messages = [
        ...this._messages,
        {
          role: "assistant",
          content: response ? response.response : "No response from architect.",
          automation: response ? response.automation : null,
          automation_yaml: response ? response.automation_yaml : null,
          config_issue: response ? response.config_issue : false,
        },
      ];
    } catch (err) {
      this._messages = [
        ...this._messages,
        {
          role: "assistant",
          content: "Sorry, I encountered an error: " + err.message,
        },
      ];
    } finally {
      this._loading = false;
    }
  }

  async _createAutomation(automation) {
    this._loading = true;
    try {
      await this.hass.callWS({
        type: "selora_ai/create_automation",
        automation: automation,
      });
      
      this._loadAutomations();
      
      this._messages = [
        ...this._messages,
        {
          role: "assistant",
          content: `Automation "${automation.alias}" created successfully!`,
        },
      ];
    } catch (err) {
      this._messages = [
        ...this._messages,
        {
          role: "assistant",
          content: "Failed to create automation: " + err.message,
        },
      ];
    } finally {
      this._loading = false;
    }
  }

  _discardSuggestion(suggestion) {
    this._suggestions = this._suggestions.filter(s => s !== suggestion);
  }

  _goToSettings() {
    this._activeTab = "settings";
    this._loadConfig().then(() => {
      if (this._config && this._config.llm_provider !== "anthropic") {
        this._updateConfig("llm_provider", "anthropic");
      }
      this._flashApiKey = true;
      setTimeout(() => {
        this._flashApiKey = false;
      }, 3000);
    });
  }
}

customElements.define("selora-ai-architect", SeloraAIArchitectPanel);
