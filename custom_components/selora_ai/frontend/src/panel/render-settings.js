import { html } from "lit";

export function renderSettings(host) {
  if (!host._config) {
    return html`
      <div
        class="scroll-view"
        style="display:flex; justify-content:center; padding-top:64px;"
      >
        <ha-circular-progress active></ha-circular-progress>
      </div>
    `;
  }

  const isAnthropic = host._config.llm_provider === "anthropic";
  const isOpenAI = host._config.llm_provider === "openai";

  return html`
    <div class="scroll-view">
      <div class="settings-form">
        <div class="section-card settings-section">
          <div
            class="section-card-header"
            style="display:flex;align-items:center;justify-content:space-between;"
          >
            <h3>LLM Provider</h3>
            <a
              href="https://selorahomes.com/docs/selora-ai/configuration/"
              target="_blank"
              rel="noopener noreferrer"
              style="display:inline-flex;align-items:center;gap:4px;font-size:12px;color:var(--secondary-text-color);text-decoration:none;"
            >
              <ha-icon
                icon="mdi:book-open-variant"
                style="--mdc-icon-size:14px;"
              ></ha-icon>
              Documentation
            </a>
          </div>
          <div class="form-group">
            <label>Provider</label>
            <select
              class="form-select"
              .value=${host._config.llm_provider}
              @change=${(e) =>
                host._updateConfig("llm_provider", e.target.value)}
            >
              <option value="anthropic">Anthropic (Claude)</option>
              <option value="openai">OpenAI</option>
              <option value="ollama">Ollama (Local)</option>
              <option disabled>Selora AI Local (Coming soon)</option>
              <option disabled>Selora AI Cloud (Coming soon)</option>
            </select>
          </div>

          ${isAnthropic
            ? html`
                <div class="form-group">
                  <label>API Key</label>
                  ${host._config.anthropic_api_key_set
                    ? html`<div class="key-hint">
                        ${host._config.anthropic_api_key_hint}
                      </div>`
                    : ""}
                  <ha-textfield
                    label="${host._config.anthropic_api_key_set
                      ? "Enter new key to replace"
                      : "Enter API key"}"
                    type="password"
                    .value=${host._newApiKey}
                    @input=${(e) => (host._newApiKey = e.target.value)}
                    placeholder="sk-ant-..."
                    style="margin-top:8px;width:100%;"
                  ></ha-textfield>
                </div>
                <div class="form-group">
                  <ha-textfield
                    label="Model"
                    .value=${host._config.anthropic_model}
                    @input=${(e) =>
                      host._updateConfig("anthropic_model", e.target.value)}
                    style="width:100%;"
                  ></ha-textfield>
                </div>
              `
            : isOpenAI
              ? html`
                  <div class="form-group">
                    <label>API Key</label>
                    ${host._config.openai_api_key_set
                      ? html`<div class="key-hint">
                          ${host._config.openai_api_key_hint}
                        </div>`
                      : ""}
                    <ha-textfield
                      label="${host._config.openai_api_key_set
                        ? "Enter new key to replace"
                        : "Enter API key"}"
                      type="password"
                      .value=${host._newApiKey}
                      @input=${(e) => (host._newApiKey = e.target.value)}
                      placeholder="sk-..."
                      style="margin-top:8px;width:100%;"
                    ></ha-textfield>
                  </div>
                  <div class="form-group">
                    <ha-textfield
                      label="Model"
                      .value=${host._config.openai_model}
                      @input=${(e) =>
                        host._updateConfig("openai_model", e.target.value)}
                      style="width:100%;"
                    ></ha-textfield>
                  </div>
                `
              : html`
                  <div class="form-group">
                    <ha-textfield
                      label="Host"
                      .value=${host._config.ollama_host}
                      @input=${(e) =>
                        host._updateConfig("ollama_host", e.target.value)}
                      style="width:100%;"
                    ></ha-textfield>
                  </div>
                  <div class="form-group">
                    <ha-textfield
                      label="Model"
                      .value=${host._config.ollama_model}
                      @input=${(e) =>
                        host._updateConfig("ollama_model", e.target.value)}
                      style="width:100%;"
                    ></ha-textfield>
                  </div>
                `}
        </div>

        <div class="section-card settings-section">
          <div class="section-card-header">
            <h3>Remote Access &amp; MCP Authentication</h3>
          </div>
          <div class="service-row">
            <label
              >Selora Connect
              <span class="setting-help">
                <ha-icon icon="mdi:help-circle-outline"></ha-icon>
                <span class="setting-tooltip"
                  >Link your Selora Connect account to enable OAuth
                  authentication for MCP clients instead of HA long-lived
                  tokens.</span
                >
              </span>
            </label>
            <ha-switch
              .checked=${host._config.selora_connect_enabled}
              @change=${(e) => {
                if (e.target.checked) {
                  host._startOAuthLink();
                } else {
                  host._unlinkConnect();
                }
              }}
              ?disabled=${host._linkingConnect}
            ></ha-switch>
          </div>
          ${host._connectError
            ? html`<div
                style="color:var(--error-color,#d32f2f);font-size:13px;margin-top:4px;padding:0 0 8px;"
              >
                ${host._connectError}
              </div>`
            : ""}
          ${host._config.selora_connect_enabled
            ? html`
                <div class="service-details" style="margin-top:8px;">
                  <div
                    style="font-size:13px;color:var(--secondary-text-color);"
                  >
                    MCP Server URL
                  </div>
                  <div
                    style="display:flex;align-items:center;gap:8px;margin-top:4px;"
                  >
                    <code
                      style="font-size:12px;word-break:break-all;flex:1;padding:6px 8px;background:var(--card-background-color);border-radius:4px;border:1px solid var(--divider-color);"
                      >${host._config.selora_mcp_url ||
                      `${location.origin}${location.pathname.split("/selora-ai")[0]}/api/selora_ai/mcp`}</code
                    >
                    <ha-icon-button
                      .path=${"M19,21H8V7H19M19,5H8A2,2 0 0,0 6,7V21A2,2 0 0,0 8,23H19A2,2 0 0,0 21,21V7A2,2 0 0,0 19,5M16,1H4A2,2 0 0,0 2,3V17H4V3H16V1Z"}
                      @click=${() => {
                        const mcpUrl =
                          host._config.selora_mcp_url ||
                          `${location.origin}${location.pathname.split("/selora-ai")[0]}/api/selora_ai/mcp`;
                        navigator.clipboard.writeText(mcpUrl);
                        host._showToast(
                          "MCP URL copied to clipboard",
                          "success",
                        );
                      }}
                    ></ha-icon-button>
                  </div>
                </div>
              `
            : ""}
          ${host._config.developer_mode && !host._config.selora_connect_enabled
            ? html`
                <div class="service-details" style="margin-top:8px;">
                  <ha-textfield
                    label="Connect Server URL"
                    .value=${host._config.selora_connect_url ||
                    "https://connect.selorahomes.com"}
                    @input=${(e) =>
                      host._updateConfig("selora_connect_url", e.target.value)}
                    style="width:100%;"
                  ></ha-textfield>
                </div>
              `
            : ""}
        </div>

        <details class="section-card settings-section advanced-section">
          <summary class="advanced-toggle">
            Advanced Settings
            <ha-icon
              icon="mdi:chevron-right"
              class="advanced-chevron"
              style="margin-left:auto;"
            ></ha-icon>
          </summary>
          <div class="settings-section-title" style="margin-top:20px;">
            Background Services
          </div>

          <div class="service-row">
            <label
              >Data Collector (AI Analysis)
              <span class="setting-help">
                <ha-icon icon="mdi:help-circle-outline"></ha-icon>
                <span class="setting-tooltip"
                  >Periodically sends a snapshot of your home state to the
                  configured LLM to generate automation suggestions.</span
                >
              </span>
            </label>
            <ha-switch
              .checked=${host._config.collector_enabled}
              @change=${(e) =>
                host._updateConfig("collector_enabled", e.target.checked)}
            ></ha-switch>
          </div>

          ${host._config.collector_enabled
            ? html`
                <div class="service-details">
                  <div class="form-group">
                    <label>Mode</label>
                    <select
                      class="form-select"
                      .value=${host._config.collector_mode}
                      @change=${(e) =>
                        host._updateConfig("collector_mode", e.target.value)}
                    >
                      <option value="continuous">Continuous</option>
                      <option value="scheduled">Scheduled Window</option>
                    </select>
                  </div>
                  <div class="form-group">
                    <ha-textfield
                      label="Interval (seconds)"
                      type="number"
                      .value=${host._config.collector_interval}
                      @input=${(e) =>
                        host._updateConfig(
                          "collector_interval",
                          parseInt(e.target.value),
                        )}
                      style="width:100%;"
                    ></ha-textfield>
                  </div>
                  ${host._config.collector_mode === "scheduled"
                    ? html`
                        <div style="display:flex;gap:12px;">
                          <ha-textfield
                            label="Start (HH:MM)"
                            .value=${host._config.collector_start_time}
                            @input=${(e) =>
                              host._updateConfig(
                                "collector_start_time",
                                e.target.value,
                              )}
                            style="flex:1;"
                          ></ha-textfield>
                          <ha-textfield
                            label="End (HH:MM)"
                            .value=${host._config.collector_end_time}
                            @input=${(e) =>
                              host._updateConfig(
                                "collector_end_time",
                                e.target.value,
                              )}
                            style="flex:1;"
                          ></ha-textfield>
                        </div>
                      `
                    : ""}
                </div>
                <div class="service-row" style="margin-top:12px;">
                  <label
                    >Auto-remove stale automations
                    <span class="setting-help">
                      <ha-icon icon="mdi:help-circle-outline"></ha-icon>
                      <span class="setting-tooltip"
                        >Automatically remove Selora automations that haven't
                        triggered in ${host._config.stale_days || 5} days when
                        the automation cap is reached. A notification lists what
                        was removed.</span
                      >
                    </span>
                  </label>
                  <ha-switch
                    .checked=${host._config.auto_purge_stale || false}
                    @change=${(e) =>
                      host._updateConfig("auto_purge_stale", e.target.checked)}
                  ></ha-switch>
                </div>
              `
            : ""}

          <div class="service-row">
            <label
              >Network Discovery
              <span class="setting-help">
                <ha-icon icon="mdi:help-circle-outline"></ha-icon>
                <span class="setting-tooltip"
                  >Scans your network for new devices and suggests adding them
                  to Home Assistant.</span
                >
              </span>
            </label>
            <ha-switch
              .checked=${host._config.discovery_enabled}
              @change=${(e) =>
                host._updateConfig("discovery_enabled", e.target.checked)}
            ></ha-switch>
          </div>

          ${host._config.discovery_enabled
            ? html`
                <div class="service-details">
                  <div class="form-group">
                    <label>Mode</label>
                    <select
                      class="form-select"
                      .value=${host._config.discovery_mode}
                      @change=${(e) =>
                        host._updateConfig("discovery_mode", e.target.value)}
                    >
                      <option value="continuous">Continuous</option>
                      <option value="scheduled">Scheduled Window</option>
                    </select>
                  </div>
                  <div class="form-group">
                    <ha-textfield
                      label="Interval (seconds)"
                      type="number"
                      .value=${host._config.discovery_interval}
                      @input=${(e) =>
                        host._updateConfig(
                          "discovery_interval",
                          parseInt(e.target.value),
                        )}
                      style="width:100%;"
                    ></ha-textfield>
                  </div>
                  ${host._config.discovery_mode === "scheduled"
                    ? html`
                        <div style="display:flex;gap:12px;">
                          <ha-textfield
                            label="Start (HH:MM)"
                            .value=${host._config.discovery_start_time}
                            @input=${(e) =>
                              host._updateConfig(
                                "discovery_start_time",
                                e.target.value,
                              )}
                            style="flex:1;"
                          ></ha-textfield>
                          <ha-textfield
                            label="End (HH:MM)"
                            .value=${host._config.discovery_end_time}
                            @input=${(e) =>
                              host._updateConfig(
                                "discovery_end_time",
                                e.target.value,
                              )}
                            style="flex:1;"
                          ></ha-textfield>
                        </div>
                      `
                    : ""}
                </div>
              `
            : ""}
          <hr class="settings-separator" />

          <div class="service-row">
            <label
              >Developer Mode
              <span class="setting-help">
                <ha-icon icon="mdi:help-circle-outline"></ha-icon>
                <span class="setting-tooltip"
                  >Shows advanced controls like manual pattern scanning in the
                  Suggestions tab. Useful for debugging and development.</span
                >
              </span>
            </label>
            <ha-switch
              .checked=${host._config.developer_mode}
              @change=${async (e) => {
                const val = e.target.checked;
                host._updateConfig("developer_mode", val);
                try {
                  await host.hass.callWS({
                    type: "selora_ai/update_config",
                    config: { developer_mode: val },
                  });
                } catch (err) {
                  host._showToast("Failed to save developer mode.", "error");
                }
              }}
            ></ha-switch>
          </div>
        </details>

        <div class="save-bar">
          <button
            class="btn btn-primary"
            @click=${host._saveConfig}
            ?disabled=${host._savingConfig}
          >
            ${host._savingConfig ? "Saving…" : "Save Settings"}
          </button>
        </div>

        <div
          style="text-align:center;font-size:11px;opacity:0.35;margin-top:24px;"
        >
          <a
            href="https://github.com/SeloraHomes/ha-selora-ai/releases/tag/v${__SELORA_VERSION__}"
            target="_blank"
            rel="noopener noreferrer"
            style="color:inherit;text-decoration:none;"
          >
            Selora AI v${__SELORA_VERSION__}
          </a>
        </div>
      </div>
    </div>
  `;
}
