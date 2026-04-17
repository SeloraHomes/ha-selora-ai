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
  const isGemini = host._config.llm_provider === "gemini";
  const isOpenAI = host._config.llm_provider === "openai";

  return html`
    <div class="scroll-view">
      <div class="settings-form">
        <a
          href="https://selorahomes.com/docs/selora-ai/configuration/"
          target="_blank"
          rel="noopener noreferrer"
          class="settings-doc-banner"
        >
          <div style="flex:1;">
            <strong>Configuration guide</strong>
            <span
              >Learn how to set up LLM providers, remote access, and MCP
              tokens.</span
            >
          </div>
          <ha-icon
            icon="mdi:open-in-new"
            style="--mdc-icon-size:16px;flex-shrink:0;opacity:0.4;"
          ></ha-icon>
        </a>
        <div class="section-card settings-section">
          <div class="section-card-header">
            <h3>LLM Provider</h3>
          </div>
          <div class="form-group">
            <label>Provider</label>
            <select
              class="form-select"
              .value=${host._config.llm_provider}
              @change=${(e) => {
                host._updateConfig("llm_provider", e.target.value);
                host._showApiKeyInput = false;
                host._newApiKey = "";
                host._llmSaveStatus = null;
              }}
            >
              <option value="anthropic">Anthropic (Claude)</option>
              <option value="gemini">Google Gemini</option>
              <option value="openai">OpenAI</option>
              <option value="ollama">Ollama (Local)</option>
              <option disabled>Selora AI Local (Coming soon)</option>
              <option disabled>Selora AI Cloud (Coming soon)</option>
            </select>
          </div>

          ${isGemini
            ? html`
                <div class="form-group">
                  <label>API Key</label>
                  ${host._config.gemini_api_key_set
                    ? html`<button
                        class="key-hint key-set key-hint-btn"
                        title="Click to replace key"
                        @click=${() => {
                          host._showApiKeyInput = !host._showApiKeyInput;
                          if (!host._showApiKeyInput) host._newApiKey = "";
                          host.requestUpdate();
                        }}
                      >
                        <ha-icon
                          icon="mdi:check-circle"
                          style="--mdc-icon-size:14px;color:var(--success-color, #22c55e);margin-right:6px;vertical-align:middle;"
                        ></ha-icon>
                        ${host._config.gemini_api_key_hint}
                        <ha-icon
                          icon="${host._showApiKeyInput
                            ? "mdi:close"
                            : "mdi:pencil"}"
                          class="key-hint-action"
                        ></ha-icon>
                      </button>`
                    : ""}
                  ${!host._config.gemini_api_key_set || host._showApiKeyInput
                    ? html`
                        <ha-textfield
                          label="${host._config.gemini_api_key_set
                            ? "Enter new key"
                            : "Enter API key"}"
                          type="password"
                          .value=${host._newApiKey}
                          @input=${(e) => (host._newApiKey = e.target.value)}
                          placeholder="AIza..."
                          style="margin-top:8px;width:100%;"
                        ></ha-textfield>
                      `
                    : ""}
                </div>
                <div class="form-group">
                  <ha-textfield
                    label="Model"
                    .value=${host._config.gemini_model}
                    @input=${(e) =>
                      host._updateConfig("gemini_model", e.target.value)}
                    style="width:100%;"
                  ></ha-textfield>
                </div>
              `
            : isAnthropic
              ? html`
                  <div class="form-group">
                    <label>API Key</label>
                    ${host._config.anthropic_api_key_set
                      ? html`<button
                          class="key-hint key-set key-hint-btn"
                          title="Click to replace key"
                          @click=${() => {
                            host._showApiKeyInput = !host._showApiKeyInput;
                            if (!host._showApiKeyInput) host._newApiKey = "";
                            host.requestUpdate();
                          }}
                        >
                          <ha-icon
                            icon="mdi:check-circle"
                            style="--mdc-icon-size:14px;color:var(--success-color, #22c55e);margin-right:6px;vertical-align:middle;"
                          ></ha-icon>
                          ${host._config.anthropic_api_key_hint}
                          <ha-icon
                            icon="${host._showApiKeyInput
                              ? "mdi:close"
                              : "mdi:pencil"}"
                            class="key-hint-action"
                          ></ha-icon>
                        </button>`
                      : ""}
                    ${!host._config.anthropic_api_key_set ||
                    host._showApiKeyInput
                      ? html`
                          <ha-textfield
                            label="${host._config.anthropic_api_key_set
                              ? "Enter new key"
                              : "Enter API key"}"
                            type="password"
                            .value=${host._newApiKey}
                            @input=${(e) => (host._newApiKey = e.target.value)}
                            placeholder="sk-ant-..."
                            style="margin-top:8px;width:100%;"
                          ></ha-textfield>
                        `
                      : ""}
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
                        ? html`<button
                            class="key-hint key-set key-hint-btn"
                            title="Click to replace key"
                            @click=${() => {
                              host._showApiKeyInput = !host._showApiKeyInput;
                              if (!host._showApiKeyInput) host._newApiKey = "";
                              host.requestUpdate();
                            }}
                          >
                            <ha-icon
                              icon="mdi:check-circle"
                              style="--mdc-icon-size:14px;color:var(--success-color, #22c55e);margin-right:6px;vertical-align:middle;"
                            ></ha-icon>
                            ${host._config.openai_api_key_hint}
                            <ha-icon
                              icon="${host._showApiKeyInput
                                ? "mdi:close"
                                : "mdi:pencil"}"
                              class="key-hint-action"
                            ></ha-icon>
                          </button>`
                        : ""}
                      ${!host._config.openai_api_key_set ||
                      host._showApiKeyInput
                        ? html`
                            <ha-textfield
                              label="${host._config.openai_api_key_set
                                ? "Enter new key"
                                : "Enter API key"}"
                              type="password"
                              .value=${host._newApiKey}
                              @input=${(e) =>
                                (host._newApiKey = e.target.value)}
                              placeholder="sk-..."
                              style="margin-top:8px;width:100%;"
                            ></ha-textfield>
                          `
                        : ""}
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

          <div class="card-save-bar">
            <button
              class="btn btn-primary"
              @click=${host._saveLlmConfig}
              ?disabled=${host._savingLlmConfig}
            >
              ${host._savingLlmConfig
                ? html`<span
                      class="spinner"
                      style="width:14px;height:14px;"
                    ></span>
                    Validating…`
                : "Save"}
            </button>
          </div>
          ${host._llmSaveStatus
            ? html`<div
                class="save-feedback save-feedback--${host._llmSaveStatus.type}"
              >
                <ha-icon
                  icon="${host._llmSaveStatus.type === "success"
                    ? "mdi:check-circle"
                    : "mdi:alert-circle"}"
                  style="--mdc-icon-size:14px;"
                ></ha-icon>
                ${host._llmSaveStatus.message}
              </div>`
            : ""}
        </div>

        <div class="section-card settings-section">
          <div style="margin-bottom:16px;">
            <h3 style="font-size:20px;font-weight:700;margin:0;">
              Remote access &amp; MCP authentication
            </h3>
            <p
              style="font-size:13px;color:var(--secondary-text-color);margin:4px 0 0;"
            >
              Manage external connectivity and API token access.
            </p>
          </div>

          <div class="settings-connect-block">
            <div
              class="service-row"
              style="border-bottom:none;padding-bottom:0;"
            >
              <div class="service-label-group">
                <label>Selora Connect</label>
                <span class="service-desc">Secure tunnel via Pangolin</span>
              </div>
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
                  style="color:var(--error-color,#d32f2f);font-size:13px;padding:4px 0 0;"
                >
                  ${host._connectError}
                </div>`
              : ""}
            ${host._config.selora_connect_enabled
              ? html`
                  <div
                    style="display:flex;align-items:center;gap:8px;padding:8px 0 0;"
                  >
                    <code
                      style="font-size:12px;word-break:break-all;flex:1;padding:8px 10px;background:var(--card-background-color);border-radius:6px;border:1px solid var(--divider-color);overflow:hidden;text-overflow:ellipsis;"
                      >${host._config.selora_mcp_url ||
                      `${location.origin}${location.pathname.split("/selora-ai")[0]}/api/selora_ai/mcp`}</code
                    >
                    <ha-icon-button
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
                    >
                      <ha-icon
                        icon="mdi:content-copy"
                        style="--mdc-icon-size:20px;"
                      ></ha-icon>
                    </ha-icon-button>
                  </div>
                `
              : ""}
            ${host._config.developer_mode &&
            !host._config.selora_connect_enabled
              ? html`
                  <div style="padding:8px 0 0;">
                    <ha-textfield
                      label="Connect Server URL"
                      .value=${host._config.selora_connect_url ||
                      "https://connect.selorahomes.com"}
                      @input=${(e) =>
                        host._updateConfig(
                          "selora_connect_url",
                          e.target.value,
                        )}
                      style="width:100%;"
                    ></ha-textfield>
                  </div>
                `
              : ""}
          </div>

          <div class="settings-section-title">MCP TOKENS</div>
          ${host._mcpTokens.length === 0
            ? html`<div
                style="font-size:13px;color:var(--secondary-text-color);padding:4px 0 8px;"
              >
                No tokens yet.
              </div>`
            : html`
                <div class="mcp-token-list">
                  ${host._mcpTokens.map(
                    (t) => html`
                      <div class="mcp-token-row">
                        <ha-icon
                          icon="mdi:key-variant"
                          style="--mdc-icon-size:20px;color:var(--selora-accent);flex-shrink:0;"
                        ></ha-icon>
                        <div class="mcp-token-info">
                          <div class="mcp-token-name">
                            ${t.name}
                            <span
                              class="mcp-token-badge mcp-token-badge--${t.permission_level}"
                              >${t.permission_level.replace("_", " ")}</span
                            >
                          </div>
                          <div class="mcp-token-meta">
                            <span>${t.token_prefix}${"*".repeat(8)}</span>
                            ${t.expires_at
                              ? html`<span
                                  >&middot; expires
                                  ${new Date(t.expires_at).toLocaleDateString(
                                    undefined,
                                    { month: "short", day: "numeric" },
                                  )}</span
                                >`
                              : ""}
                            ${t.last_used_at
                              ? html`<span
                                  >&middot; used
                                  ${_timeAgo(t.last_used_at)}</span
                                >`
                              : ""}
                          </div>
                        </div>
                        <ha-icon-button
                          ?disabled=${host._revokingTokenId === t.id}
                          @click=${() => host._revokeMcpToken(t.id)}
                        >
                          ${host._revokingTokenId === t.id
                            ? html`<span
                                class="spinner"
                                style="width:14px;height:14px;"
                              ></span>`
                            : html`<ha-icon
                                icon="mdi:delete-outline"
                                style="--mdc-icon-size:20px;"
                              ></ha-icon>`}
                        </ha-icon-button>
                      </div>
                    `,
                  )}
                </div>
              `}
          <button
            class="btn btn-outline"
            style="margin-top:8px;"
            @click=${() => host._openCreateTokenDialog()}
          >
            <ha-icon icon="mdi:plus" style="--mdc-icon-size:16px;"></ha-icon>
            Add token
          </button>
        </div>

        ${renderCreateTokenDialog(host)}

        <details class="section-card settings-section advanced-section" open>
          <summary class="advanced-toggle">
            Advanced settings
            <ha-icon
              icon="mdi:chevron-right"
              class="advanced-chevron"
              style="margin-left:auto;"
            ></ha-icon>
          </summary>

          <div class="settings-section-title" style="margin-top:16px;">
            BACKGROUND SERVICES
          </div>

          <div class="service-group">
            <div class="service-row">
              <div class="service-label-group">
                <label>Data collector (AI analysis)</label>
                <span class="service-desc"
                  >Feeds entity history to Selora AI</span
                >
              </div>
              <ha-switch
                .checked=${host._config.collector_enabled}
                @change=${(e) =>
                  host._updateConfig("collector_enabled", e.target.checked)}
              ></ha-switch>
            </div>
            ${host._config.collector_enabled
              ? html`
                  <div class="service-details">
                    <div style="display:flex;gap:12px;">
                      <div class="form-group" style="flex:1;margin-bottom:0;">
                        <label>Mode</label>
                        <select
                          class="form-select"
                          .value=${host._config.collector_mode}
                          @change=${(e) =>
                            host._updateConfig(
                              "collector_mode",
                              e.target.value,
                            )}
                        >
                          <option value="continuous">Continuous</option>
                          <option value="scheduled">Scheduled Window</option>
                        </select>
                      </div>
                      <div
                        class="form-group"
                        style="width:130px;margin-bottom:0;"
                      >
                        <label>Interval (s)</label>
                        <input
                          class="form-select"
                          type="number"
                          .value=${host._config.collector_interval}
                          @input=${(e) =>
                            host._updateConfig(
                              "collector_interval",
                              parseInt(e.target.value),
                            )}
                          style="width:100%;box-sizing:border-box;"
                        />
                      </div>
                    </div>
                    ${host._config.collector_mode === "scheduled"
                      ? html`
                          <div style="display:flex;gap:12px;margin-top:12px;">
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
                `
              : ""}
          </div>

          <div class="service-group">
            <div class="service-row">
              <div class="service-label-group">
                <label>Network discovery</label>
                <span class="service-desc"
                  >Scans local network for new devices</span
                >
              </div>
              <ha-switch
                .checked=${host._config.discovery_enabled}
                @change=${(e) =>
                  host._updateConfig("discovery_enabled", e.target.checked)}
              ></ha-switch>
            </div>
            ${host._config.discovery_enabled
              ? html`
                  <div class="service-details">
                    <div style="display:flex;gap:12px;">
                      <div class="form-group" style="flex:1;margin-bottom:0;">
                        <label>Mode</label>
                        <select
                          class="form-select"
                          .value=${host._config.discovery_mode}
                          @change=${(e) =>
                            host._updateConfig(
                              "discovery_mode",
                              e.target.value,
                            )}
                        >
                          <option value="continuous">Continuous</option>
                          <option value="scheduled">Scheduled Window</option>
                        </select>
                      </div>
                      <div
                        class="form-group"
                        style="width:130px;margin-bottom:0;"
                      >
                        <label>Interval (s)</label>
                        <input
                          class="form-select"
                          type="number"
                          .value=${host._config.discovery_interval}
                          @input=${(e) =>
                            host._updateConfig(
                              "discovery_interval",
                              parseInt(e.target.value),
                            )}
                          style="width:100%;box-sizing:border-box;"
                        />
                      </div>
                    </div>
                    ${host._config.discovery_mode === "scheduled"
                      ? html`
                          <div style="display:flex;gap:12px;margin-top:12px;">
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
          </div>

          <div class="service-group">
            <div class="service-row">
              <div class="service-label-group">
                <label>Auto-remove stale automations</label>
                <span class="service-desc"
                  >Deletes automations inactive for
                  ${host._config.stale_days || 5}+ days</span
                >
              </div>
              <ha-switch
                .checked=${host._config.auto_purge_stale || false}
                @change=${(e) =>
                  host._updateConfig("auto_purge_stale", e.target.checked)}
              ></ha-switch>
            </div>
          </div>

          <div class="service-group">
            <div class="service-row">
              <div class="service-label-group">
                <label>Developer mode</label>
                <span class="service-desc"
                  >Exposes raw entity payloads and debug logs</span
                >
              </div>
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
          </div>

          <div class="card-save-bar">
            <button
              class="btn btn-primary"
              @click=${host._saveAdvancedConfig}
              ?disabled=${host._savingAdvancedConfig}
            >
              ${host._savingAdvancedConfig ? "Saving…" : "Save"}
            </button>
          </div>
        </details>

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

// ── MCP Token helpers ───────────────────────────────────────────────────────

const MCP_TOOLS = [
  { name: "selora_list_automations", label: "List automations", admin: false },
  { name: "selora_get_automation", label: "Get automation", admin: false },
  {
    name: "selora_validate_automation",
    label: "Validate automation",
    admin: false,
  },
  {
    name: "selora_create_automation",
    label: "Create automation",
    admin: true,
  },
  {
    name: "selora_accept_automation",
    label: "Accept automation",
    admin: true,
  },
  {
    name: "selora_delete_automation",
    label: "Delete automation",
    admin: true,
  },
  {
    name: "selora_get_home_snapshot",
    label: "Get home snapshot",
    admin: false,
  },
  { name: "selora_chat", label: "Chat", admin: true },
  { name: "selora_list_sessions", label: "List sessions", admin: false },
  { name: "selora_list_patterns", label: "List patterns", admin: false },
  { name: "selora_get_pattern", label: "Get pattern", admin: false },
  { name: "selora_list_suggestions", label: "List suggestions", admin: false },
  {
    name: "selora_accept_suggestion",
    label: "Accept suggestion",
    admin: true,
  },
  {
    name: "selora_dismiss_suggestion",
    label: "Dismiss suggestion",
    admin: true,
  },
  { name: "selora_trigger_scan", label: "Trigger scan", admin: true },
  { name: "selora_list_devices", label: "List devices", admin: false },
  { name: "selora_get_device", label: "Get device", admin: false },
];

function _timeAgo(isoString) {
  if (!isoString) return "never";
  const seconds = Math.floor(
    (Date.now() - new Date(isoString).getTime()) / 1000,
  );
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function renderCreateTokenDialog(host) {
  if (!host._showCreateTokenDialog) return "";

  // Token was just created — show it for copy
  if (host._createdToken) {
    return html`
      <div class="modal-overlay" @click=${() => host._closeCreateTokenDialog()}>
        <div
          class="modal-content"
          style="max-width:480px;"
          @click=${(e) => e.stopPropagation()}
        >
          <h3 style="margin:0 0 12px;">Token Created</h3>
          <p
            style="font-size:13px;color:var(--secondary-text-color);margin:0 0 12px;"
          >
            Copy this token now — it won't be shown again.
          </p>
          <div
            style="display:flex;align-items:center;gap:8px;padding:10px 12px;background:var(--card-background-color);border:1px solid var(--selora-accent);border-radius:8px;font-family:monospace;font-size:13px;word-break:break-all;"
          >
            <span style="flex:1;user-select:all;">${host._createdToken}</span>
            <button
              style="background:none;border:none;color:var(--selora-accent);cursor:pointer;padding:8px;border-radius:50%;flex-shrink:0;"
              @click=${() => {
                navigator.clipboard.writeText(host._createdToken);
                host._showToast("Token copied to clipboard", "success");
              }}
            >
              <ha-icon
                icon="mdi:content-copy"
                style="--mdc-icon-size:20px;"
              ></ha-icon>
            </button>
          </div>
          <div style="display:flex;justify-content:flex-end;margin-top:16px;">
            <button
              class="btn btn-primary"
              @click=${() => host._closeCreateTokenDialog()}
            >
              Done
            </button>
          </div>
        </div>
      </div>
    `;
  }

  const permission = host._newTokenPermission;

  return html`
    <div class="modal-overlay" @click=${() => host._closeCreateTokenDialog()}>
      <div
        class="modal-content"
        style="max-width:480px;"
        @click=${(e) => e.stopPropagation()}
      >
        <h3 style="margin:0 0 16px;">Create MCP Token</h3>

        <div class="form-group">
          <label>Name</label>
          <input
            class="modal-input"
            type="text"
            placeholder="e.g. Claude Desktop"
            .value=${host._newTokenName}
            @input=${(e) => {
              host._newTokenName = e.target.value;
            }}
            style="width:100%;box-sizing:border-box;"
          />
        </div>

        <div class="form-group">
          <label>Permission Level</label>
          <select
            class="form-select"
            .value=${permission}
            @change=${(e) => {
              host._newTokenPermission = e.target.value;
              host.requestUpdate();
            }}
          >
            <option value="read_only">Read Only</option>
            <option value="admin">Admin (all tools)</option>
            <option value="custom">Custom (select tools)</option>
          </select>
        </div>

        ${permission === "custom"
          ? html`
              <div class="form-group">
                <label>Allowed Tools</label>
                <div class="mcp-tool-checklist">
                  ${MCP_TOOLS.map(
                    (tool) => html`
                      <label class="mcp-tool-check">
                        <input
                          type="checkbox"
                          .checked=${host._newTokenTools[tool.name] || false}
                          @change=${(e) => {
                            host._newTokenTools = {
                              ...host._newTokenTools,
                              [tool.name]: e.target.checked,
                            };
                            host.requestUpdate();
                          }}
                        />
                        <span>${tool.label}</span>
                        ${tool.admin
                          ? html`<span
                              class="mcp-token-badge mcp-token-badge--admin"
                              style="font-size:10px;padding:1px 5px;"
                              >admin</span
                            >`
                          : ""}
                      </label>
                    `,
                  )}
                </div>
              </div>
            `
          : ""}

        <div class="form-group">
          <label>Expiration (optional)</label>
          <select
            class="form-select"
            .value=${host._newTokenExpiry}
            @change=${(e) => {
              host._newTokenExpiry = e.target.value;
              host.requestUpdate();
            }}
          >
            <option value="">Never expires</option>
            <option value="7">7 days</option>
            <option value="30">30 days</option>
            <option value="90">90 days</option>
            <option value="365">1 year</option>
          </select>
        </div>

        <div
          style="display:flex;justify-content:flex-end;gap:8px;margin-top:16px;"
        >
          <button
            class="btn btn-outline"
            @click=${() => host._closeCreateTokenDialog()}
          >
            Cancel
          </button>
          <button
            class="btn btn-primary"
            ?disabled=${!host._newTokenName?.trim() || host._creatingToken}
            @click=${() => host._createMcpToken()}
          >
            ${host._creatingToken
              ? html`<span
                  class="spinner"
                  style="width:14px;height:14px;"
                ></span>`
              : "Create Token"}
          </button>
        </div>
      </div>
    </div>
  `;
}
