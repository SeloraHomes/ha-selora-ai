// ---------------------------------------------------------------------------
// Card Editor (minimal config UI)
// ---------------------------------------------------------------------------

import { LitElement, html } from "lit";

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

customElements.define("selora-ai-card-editor", SeloraAICardEditor);
