import { html } from "lit";

import { renderIgnoreList } from "./render-ignore-list.js";

// Native <input> wrapper used in place of <ha-textfield>. Recent HA builds
// wrap each panel in a scoped custom-element registry, which prevents
// globally-registered HA components like <ha-textfield> from upgrading
// inside our shadow root — they render as empty unknown elements. Plain
// <input> works regardless of the registry, and the .form-select class
// already styles it to match the rest of the settings form.
function _textInput({
  label,
  value,
  oninput,
  type = "text",
  placeholder = "",
  style = "",
}) {
  return html`
    ${
      label
        ? html`<label
            style="font-size:13px;color:var(--secondary-text-color);display:block;margin-bottom:6px;"
            >${label}</label
          >`
        : ""
    }
    <input
      class="form-select"
      type=${type}
      .value=${value || ""}
      @input=${oninput}
      placeholder=${placeholder}
      style="width:100%;box-sizing:border-box;${style}"
    />
  `;
}

// Find the LLM cost sensor and return today's spend, or null if either
// the sensor isn't present or the value is non-numeric. Used to surface
// a live preview in the Settings → LLM Provider header.
function _todayCostHint(host) {
  const states = host.hass?.states || {};
  for (const [entityId, state] of Object.entries(states)) {
    if (
      entityId.startsWith("sensor.") &&
      entityId.includes("selora") &&
      entityId.endsWith("llm_cost")
    ) {
      const v = Number(state?.state);
      if (Number.isFinite(v) && v > 0) {
        // Lifetime total — we don't have today's split here, but the
        // panel will show the period breakdown after navigation. The
        // header just teases that there *is* data.
        return v;
      }
      return 0;
    }
  }
  return null;
}

function _renderUsageHeaderLink(host) {
  const cost = _todayCostHint(host);
  const hasData = cost !== null && cost > 0;
  return html`
    <button
      class="section-card-action"
      title=${host._t("settings_view_token_usage_title", "View token usage")}
      @click=${() => {
        host._setActiveTab("usage");
        host._loadUsageStats?.();
        host.requestUpdate();
      }}
    >
      <ha-icon icon="mdi:chart-line-variant"></ha-icon>
      <span
        >${
          hasData
            ? host._t("settings_usage_label", "Usage")
            : host._t("settings_view_usage_label", "View usage")
        }</span
      >
      <ha-icon
        icon="mdi:chevron-right"
        class="section-card-action-chevron"
      ></ha-icon>
    </button>
  `;
}

const _PROVIDERS = [
  { value: "selora_cloud", label: "Selora AI Cloud" },
  { value: "selora_local", label: "Selora AI Local" },
  { value: "anthropic", label: "Anthropic (Claude)" },
  { value: "gemini", label: "Google Gemini" },
  { value: "openai", label: "OpenAI (ChatGPT)" },
  { value: "openrouter", label: "OpenRouter" },
  { value: "ollama", label: "Ollama (Local)" },
];

function _renderProviderPicker(host) {
  const providers = _PROVIDERS;
  const current = providers.find((p) => p.value === host._config.llm_provider);
  const open = host._providerDropdownOpen || false;
  return html`
    <div style="position:relative;">
      <button
        class="form-select"
        style="text-align:left;width:100%;display:flex;align-items:center;justify-content:space-between;"
        @click=${() => {
          host._providerDropdownOpen = !open;
          host.requestUpdate();
        }}
      >
        <span
          >${
            current
              ? current.label
              : host._t("settings_provider_select_placeholder", "Select...")
          }</span
        >
        <ha-icon
          icon="mdi:chevron-down"
          style="--mdc-icon-size:18px;opacity:0.6;"
        ></ha-icon>
      </button>
      ${
        open
          ? html`
              <div
                style="position:fixed;inset:0;z-index:9;"
                @click=${() => {
                  host._providerDropdownOpen = false;
                  host.requestUpdate();
                }}
              ></div>
              <div
                style="position:absolute;top:100%;left:0;right:0;z-index:10;margin-top:4px;border-radius:10px;border:1px solid var(--divider-color);background:var(--card-background-color);box-shadow:0 4px 12px rgba(0,0,0,0.15);overflow:hidden;"
              >
                ${providers.map(
                  (p) => html`
                    <button
                      style="display:block;width:100%;text-align:left;padding:10px 14px;border:none;background:${
                        p.value === host._config.llm_provider
                          ? "var(--selora-accent)"
                          : "transparent"
                      };color:${
                        p.disabled
                          ? "var(--disabled-text-color, #999)"
                          : p.value === host._config.llm_provider
                            ? "#000"
                            : "var(--primary-text-color)"
                      };font-size:14px;cursor:${
                        p.disabled ? "default" : "pointer"
                      };opacity:${p.disabled ? "0.5" : "1"};"
                      @click=${() => {
                        if (p.disabled) return;
                        host._providerDropdownOpen = false;
                        host._updateConfig("llm_provider", p.value);
                        // Switching to Selora Local: if the backend was
                        // detected at a non-default host (typical on HA OS
                        // where the add-on lives on the Supervisor bridge),
                        // prefill that host so saving the form doesn't
                        // immediately fail validation against localhost.
                        if (
                          p.value === "selora_local" &&
                          host._config?.selora_local_discovered_host
                        ) {
                          host._updateConfig(
                            "selora_local_host",
                            host._config.selora_local_discovered_host,
                          );
                        }
                        host._showApiKeyInput = false;
                        host._newApiKey = "";
                        host._llmSaveStatus = null;
                      }}
                    >
                      ${p.label}
                    </button>
                  `,
                )}
              </div>
            `
          : ""
      }
    </div>
  `;
}

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

  const isSeloraCloud = host._config.llm_provider === "selora_cloud";
  const isAnthropic = host._config.llm_provider === "anthropic";
  const isGemini = host._config.llm_provider === "gemini";
  const isOpenAI = host._config.llm_provider === "openai";
  const isOpenRouter = host._config.llm_provider === "openrouter";
  const isSeloraLocal = host._config.llm_provider === "selora_local";

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
            <strong
              >${host._t(
                "settings_doc_banner_title",
                "Configuration guide",
              )}</strong
            >
            <span
              >${host._t(
                "settings_doc_banner_desc",
                "Learn how to set up LLM providers, remote access, and MCP tokens.",
              )}</span
            >
          </div>
          <ha-icon
            icon="mdi:open-in-new"
            style="--mdc-icon-size:16px;flex-shrink:0;opacity:0.4;"
          ></ha-icon>
        </a>
        <div class="section-card settings-section">
          <div class="section-card-header section-card-header--with-action">
            <h3>${host._t("settings_llm_provider_heading", "LLM Provider")}</h3>
            ${_renderUsageHeaderLink(host)}
          </div>
          <div class="form-group">
            <label>${host._t("settings_provider_label", "Provider")}</label>
            ${_renderProviderPicker(host)}
          </div>

          ${
            isSeloraCloud
              ? html`
                  <div class="form-group">
                    <label
                      >${host._t(
                        "settings_selora_account_label",
                        "Selora account",
                      )}</label
                    >
                    ${
                      host._config.aigateway_linked
                        ? html`
                            <div
                              style="display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid var(--divider-color);border-radius:8px;background:var(--card-background-color);"
                            >
                              <ha-icon
                                icon="mdi:check-circle"
                                style="--mdc-icon-size:18px;color:var(--success-color, #22c55e);flex-shrink:0;"
                              ></ha-icon>
                              <div style="flex:1;min-width:0;">
                                <div
                                  style="font-size:14px;color:var(--primary-text-color);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
                                >
                                  Linked${
                                    host._config.aigateway_user_email
                                      ? html` as
                                          <strong
                                            >${
                                              host._config.aigateway_user_email
                                            }</strong
                                          >`
                                      : ""
                                  }
                                </div>
                                <div
                                  style="font-size:12px;color:var(--secondary-text-color);"
                                >
                                  ${host._t(
                                    "settings_selora_cloud_backend_desc",
                                    "Selora Cloud is providing your LLM backend.",
                                  )}
                                </div>
                              </div>
                              <button
                                class="btn btn-outline"
                                style="flex-shrink:0;"
                                @click=${() => host._unlinkAIGateway()}
                              >
                                ${host._t("settings_unlink_button", "Unlink")}
                              </button>
                            </div>
                          `
                        : html`
                            <div
                              style="display:flex;flex-direction:column;gap:10px;"
                            >
                              <p
                                style="font-size:13px;color:var(--secondary-text-color);margin:0;"
                              >
                                ${host._t(
                                  "settings_signin_selora_desc",
                                  "Sign in with your Selora account to use the hosted LLM backend. No API key required.",
                                )}
                              </p>
                              ${
                                host._config.developer_mode
                                  ? html`
                                      ${_textInput({
                                        label: host._t(
                                          "settings_selora_cloud_url_label",
                                          "Selora Cloud URL",
                                        ),
                                        value:
                                          host._config.selora_connect_url ||
                                          "https://connect.selorahomes.com",
                                        oninput: (e) =>
                                          host._updateConfig(
                                            "selora_connect_url",
                                            e.target.value,
                                          ),
                                      })}
                                      <div
                                        style="font-size:12px;color:var(--secondary-text-color);margin-top:-2px;"
                                      >
                                        ${host._t(
                                          "settings_selora_cloud_url_hint",
                                          "OAuth and chat completions both use this URL. Saved automatically when you link.",
                                        )}
                                      </div>
                                    `
                                  : ""
                              }
                              ${
                                host._aigwAuthorizeUrl
                                  ? html`<a
                                      class="btn btn-primary"
                                      href=${host._aigwAuthorizeUrl}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      style="align-self:flex-start;text-decoration:none;display:inline-flex;align-items:center;gap:6px;"
                                    >
                                      ${host._t(
                                        "settings_open_signin_page",
                                        "Open sign-in page →",
                                      )}
                                    </a>`
                                  : html`<button
                                      class="btn btn-primary"
                                      ?disabled=${host._linkingAIGateway}
                                      @click=${() => host._startAIGatewayLink()}
                                      style="align-self:flex-start;"
                                    >
                                      ${
                                        host._linkingAIGateway
                                          ? html`<span
                                                class="spinner"
                                                style="width:14px;height:14px;"
                                              ></span>
                                              ${host._t(
                                                "settings_preparing_label",
                                                "Preparing…",
                                              )}`
                                          : host._t(
                                              "settings_link_selora_account_button",
                                              "Link Selora account",
                                            )
                                      }
                                    </button>`
                              }
                              ${
                                host._aigwAuthorizeUrl
                                  ? html`<div
                                      style="font-size:12px;color:var(--secondary-text-color);margin-top:4px;"
                                    >
                                      ${host._t(
                                        "settings_signin_new_tab_hint",
                                        "Opens in a new tab. After signing in, return to this page — the panel updates automatically.",
                                      )}
                                    </div>`
                                  : ""
                              }
                            </div>
                          `
                    }
                    ${
                      host._aigatewayError
                        ? html`<div
                            style="color:var(--error-color,#d32f2f);font-size:13px;padding:6px 0 0;"
                          >
                            ${host._aigatewayError}
                          </div>`
                        : ""
                    }
                  </div>
                  ${
                    host._config.aigateway_linked && host._config.developer_mode
                      ? html`
                          <div class="form-group">
                            ${_textInput({
                              label: host._t(
                                "settings_selora_cloud_url_label",
                                "Selora Cloud URL",
                              ),
                              value:
                                host._config.selora_connect_url ||
                                "https://connect.selorahomes.com",
                              oninput: (e) =>
                                host._updateConfig(
                                  "selora_connect_url",
                                  e.target.value,
                                ),
                            })}
                          </div>
                        `
                      : ""
                  }
                `
              : isGemini
                ? html`
                    <div class="form-group">
                      <label
                        >${host._t("settings_api_key_label", "API Key")}</label
                      >
                      ${
                        host._config.gemini_api_key_set
                          ? html`<button
                              class="key-hint key-set key-hint-btn"
                              title=${host._t(
                                "settings_click_replace_key_title",
                                "Click to replace key",
                              )}
                              @click=${() => {
                                host._showApiKeyInput = !host._showApiKeyInput;
                                if (!host._showApiKeyInput)
                                  host._newApiKey = "";
                                host.requestUpdate();
                              }}
                            >
                              <ha-icon
                                icon="mdi:check-circle"
                                style="--mdc-icon-size:14px;color:var(--success-color, #22c55e);margin-right:6px;vertical-align:middle;"
                              ></ha-icon>
                              ${host._config.gemini_api_key_hint}
                              <ha-icon
                                icon="${
                                  host._showApiKeyInput
                                    ? "mdi:close"
                                    : "mdi:pencil"
                                }"
                                class="key-hint-action"
                              ></ha-icon>
                            </button>`
                          : ""
                      }
                      ${
                        !host._config.gemini_api_key_set ||
                        host._showApiKeyInput
                          ? _textInput({
                              label: host._config.gemini_api_key_set
                                ? host._t(
                                    "settings_enter_new_key_label",
                                    "Enter new key",
                                  )
                                : host._t(
                                    "settings_enter_api_key_label",
                                    "Enter API key",
                                  ),
                              type: "password",
                              value: host._newApiKey,
                              oninput: (e) =>
                                (host._newApiKey = e.target.value),
                              placeholder: "AIza...",
                              style: "margin-top:8px;",
                            })
                          : ""
                      }
                    </div>
                    <div class="form-group">
                      ${_textInput({
                        label: host._t("settings_model_label", "Model"),
                        value: host._config.gemini_model,
                        oninput: (e) =>
                          host._updateConfig("gemini_model", e.target.value),
                      })}
                    </div>
                  `
                : isAnthropic
                  ? html`
                      <div class="form-group">
                        <label
                          >${host._t("settings_api_key_label", "API Key")}</label
                        >
                        ${
                          host._config.anthropic_api_key_set
                            ? html`<button
                                class="key-hint key-set key-hint-btn"
                                title=${host._t(
                                  "settings_click_replace_key_title",
                                  "Click to replace key",
                                )}
                                @click=${() => {
                                  host._showApiKeyInput =
                                    !host._showApiKeyInput;
                                  if (!host._showApiKeyInput)
                                    host._newApiKey = "";
                                  host.requestUpdate();
                                }}
                              >
                                <ha-icon
                                  icon="mdi:check-circle"
                                  style="--mdc-icon-size:14px;color:var(--success-color, #22c55e);margin-right:6px;vertical-align:middle;"
                                ></ha-icon>
                                ${host._config.anthropic_api_key_hint}
                                <ha-icon
                                  icon="${
                                    host._showApiKeyInput
                                      ? "mdi:close"
                                      : "mdi:pencil"
                                  }"
                                  class="key-hint-action"
                                ></ha-icon>
                              </button>`
                            : ""
                        }
                        ${
                          !host._config.anthropic_api_key_set ||
                          host._showApiKeyInput
                            ? _textInput({
                                label: host._config.anthropic_api_key_set
                                  ? host._t(
                                      "settings_enter_new_key_label",
                                      "Enter new key",
                                    )
                                  : host._t(
                                      "settings_enter_api_key_label",
                                      "Enter API key",
                                    ),
                                type: "password",
                                value: host._newApiKey,
                                oninput: (e) =>
                                  (host._newApiKey = e.target.value),
                                placeholder: "sk-ant-...",
                                style: "margin-top:8px;",
                              })
                            : ""
                        }
                      </div>
                      <div class="form-group">
                        ${_textInput({
                          label: host._t("settings_model_label", "Model"),
                          value: host._config.anthropic_model,
                          oninput: (e) =>
                            host._updateConfig(
                              "anthropic_model",
                              e.target.value,
                            ),
                        })}
                      </div>
                    `
                  : isOpenAI
                    ? html`
                        <div class="form-group">
                          <label
                            >${host._t(
                              "settings_api_key_label",
                              "API Key",
                            )}</label
                          >
                          ${
                            host._config.openai_api_key_set
                              ? html`<button
                                  class="key-hint key-set key-hint-btn"
                                  title=${host._t(
                                    "settings_click_replace_key_title",
                                    "Click to replace key",
                                  )}
                                  @click=${() => {
                                    host._showApiKeyInput =
                                      !host._showApiKeyInput;
                                    if (!host._showApiKeyInput)
                                      host._newApiKey = "";
                                    host.requestUpdate();
                                  }}
                                >
                                  <ha-icon
                                    icon="mdi:check-circle"
                                    style="--mdc-icon-size:14px;color:var(--success-color, #22c55e);margin-right:6px;vertical-align:middle;"
                                  ></ha-icon>
                                  ${host._config.openai_api_key_hint}
                                  <ha-icon
                                    icon="${
                                      host._showApiKeyInput
                                        ? "mdi:close"
                                        : "mdi:pencil"
                                    }"
                                    class="key-hint-action"
                                  ></ha-icon>
                                </button>`
                              : ""
                          }
                          ${
                            !host._config.openai_api_key_set ||
                            host._showApiKeyInput
                              ? _textInput({
                                  label: host._config.openai_api_key_set
                                    ? host._t(
                                        "settings_enter_new_key_label",
                                        "Enter new key",
                                      )
                                    : host._t(
                                        "settings_enter_api_key_label",
                                        "Enter API key",
                                      ),
                                  type: "password",
                                  value: host._newApiKey,
                                  oninput: (e) =>
                                    (host._newApiKey = e.target.value),
                                  placeholder: "sk-...",
                                  style: "margin-top:8px;",
                                })
                              : ""
                          }
                        </div>
                        <div class="form-group">
                          ${_textInput({
                            label: host._t("settings_model_label", "Model"),
                            value: host._config.openai_model,
                            oninput: (e) =>
                              host._updateConfig(
                                "openai_model",
                                e.target.value,
                              ),
                          })}
                        </div>
                      `
                    : isOpenRouter
                      ? html`
                          <div class="form-group">
                            <label
                              >${host._t(
                                "settings_api_key_label",
                                "API Key",
                              )}</label
                            >
                            ${
                              host._config.openrouter_api_key_set
                                ? html`<button
                                    class="key-hint key-set key-hint-btn"
                                    title=${host._t(
                                      "settings_click_replace_key_title",
                                      "Click to replace key",
                                    )}
                                    @click=${() => {
                                      host._showApiKeyInput =
                                        !host._showApiKeyInput;
                                      if (!host._showApiKeyInput)
                                        host._newApiKey = "";
                                      host.requestUpdate();
                                    }}
                                  >
                                    <ha-icon
                                      icon="mdi:check-circle"
                                      style="--mdc-icon-size:14px;color:var(--success-color, #22c55e);margin-right:6px;vertical-align:middle;"
                                    ></ha-icon>
                                    ${host._config.openrouter_api_key_hint}
                                    <ha-icon
                                      icon="${
                                        host._showApiKeyInput
                                          ? "mdi:close"
                                          : "mdi:pencil"
                                      }"
                                      class="key-hint-action"
                                    ></ha-icon>
                                  </button>`
                                : ""
                            }
                            ${
                              !host._config.openrouter_api_key_set ||
                              host._showApiKeyInput
                                ? _textInput({
                                    label: host._config.openrouter_api_key_set
                                      ? host._t(
                                          "settings_enter_new_key_label",
                                          "Enter new key",
                                        )
                                      : host._t(
                                          "settings_enter_api_key_label",
                                          "Enter API key",
                                        ),
                                    type: "password",
                                    value: host._newApiKey,
                                    oninput: (e) =>
                                      (host._newApiKey = e.target.value),
                                    placeholder: "sk-or-...",
                                    style: "margin-top:8px;",
                                  })
                                : ""
                            }
                          </div>
                          <div class="form-group">
                            ${_textInput({
                              label: host._t("settings_model_label", "Model"),
                              value: host._config.openrouter_model,
                              oninput: (e) =>
                                host._updateConfig(
                                  "openrouter_model",
                                  e.target.value,
                                ),
                              placeholder: "anthropic/claude-sonnet-4.5",
                            })}
                          </div>
                        `
                      : isSeloraLocal
                        ? html`
                            <button
                              class="btn-link"
                              style="background:none;border:none;padding:0;color:var(--primary-color);font-size:12px;cursor:pointer;"
                              @click=${() => {
                                host._seloraLocalAdvanced =
                                  !host._seloraLocalAdvanced;
                                host.requestUpdate();
                              }}
                            >
                              ${
                                host._seloraLocalAdvanced
                                  ? host._t(
                                      "settings_selora_local_hide_advanced",
                                      "Hide advanced options",
                                    )
                                  : host._t(
                                      "settings_selora_local_show_advanced",
                                      "Show advanced options",
                                    )
                              }
                            </button>
                            ${
                              host._seloraLocalAdvanced
                                ? html`
                                    <p
                                      style="font-size:12px;color:var(--secondary-text-color);margin:8px 0;"
                                    >
                                      ${host._t(
                                        "settings_selora_local_advanced_desc",
                                        "Selora Hubs come pre-configured. To use a self-hosted llama-server running the Selora AI model, enter its address below.",
                                      )}
                                    </p>
                                    <div
                                      class="form-group"
                                      style="margin-top:8px;"
                                    >
                                      ${_textInput({
                                        label: host._t(
                                          "settings_selora_local_host_label",
                                          "Host",
                                        ),
                                        value:
                                          host._config.selora_local_host || "",
                                        oninput: (e) =>
                                          host._updateConfig(
                                            "selora_local_host",
                                            e.target.value,
                                          ),
                                        placeholder: "http://localhost:8080",
                                      })}
                                      <p
                                        style="font-size:12px;color:var(--secondary-text-color);margin-top:4px;"
                                      >
                                        ${host._t(
                                          "settings_selora_local_auto_detected_prefix",
                                          "Auto-detected:",
                                        )}
                                        ${
                                          host._config
                                            .selora_local_discovered_host ||
                                          host._t(
                                            "settings_selora_local_auto_detected_none",
                                            "none",
                                          )
                                        }.
                                      </p>
                                    </div>
                                  `
                                : ""
                            }
                          `
                        : html`
                            <div class="form-group">
                              ${_textInput({
                                label: host._t(
                                  "settings_ollama_host_label",
                                  "Host",
                                ),
                                value: host._config.ollama_host,
                                oninput: (e) =>
                                  host._updateConfig(
                                    "ollama_host",
                                    e.target.value,
                                  ),
                              })}
                            </div>
                            <div class="form-group">
                              ${_textInput({
                                label: host._t("settings_model_label", "Model"),
                                value: host._config.ollama_model,
                                oninput: (e) =>
                                  host._updateConfig(
                                    "ollama_model",
                                    e.target.value,
                                  ),
                              })}
                            </div>
                          `
          }
          ${
            isSeloraCloud && !host._config.aigateway_linked
              ? ""
              : html`
                  <div class="card-save-bar">
                    <button
                      class="btn btn-primary"
                      @click=${host._saveLlmConfig}
                      ?disabled=${host._savingLlmConfig}
                    >
                      ${
                        host._savingLlmConfig
                          ? html`<span
                                class="spinner"
                                style="width:14px;height:14px;"
                              ></span>
                              ${host._t("settings_validating_label", "Validating…")}`
                          : host._t("settings_save_button", "Save")
                      }
                    </button>
                  </div>
                `
          }
          ${
            host._llmSaveStatus
              ? html`<div
                  class="save-feedback save-feedback--${host._llmSaveStatus.type}"
                >
                  <ha-icon
                    icon="${
                      host._llmSaveStatus.type === "success"
                        ? "mdi:check-circle"
                        : "mdi:alert-circle"
                    }"
                    style="--mdc-icon-size:14px;"
                  ></ha-icon>
                  ${host._llmSaveStatus.message}
                </div>`
              : ""
          }
        </div>

        <div class="section-card settings-section">
          <div class="section-card-header">
            <h3>${host._t("settings_mcp_server_heading", "MCP Server")}</h3>
          </div>
          <p class="section-card-subtitle">
            ${host._t(
              "settings_mcp_server_subtitle",
              "Expose your home to external AI tools like Openclaw, Claude Desktop, Cursor, or Windsurf.",
            )}
          </p>

          <div class="settings-connect-block">
            <div
              class="service-row"
              style="border-bottom:none;padding-bottom:0;"
            >
              <div class="service-label-group">
                <label
                  >${host._t(
                    "settings_connect_via_selora_label",
                    "Connect via Selora account",
                  )}</label
                >
                <span class="service-desc"
                  >${host._t(
                    "settings_connect_via_selora_desc",
                    "Makes your MCP server reachable by external tools",
                  )}</span
                >
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
            ${
              host._connectError
                ? html`<div
                    style="color:var(--error-color,#d32f2f);font-size:13px;padding:4px 0 0;"
                  >
                    ${host._connectError}
                  </div>`
                : ""
            }
            ${
              host._connectAuthorizeUrl
                ? html`<div
                    style="display:flex;flex-direction:column;gap:6px;padding:8px 0 0;"
                  >
                    <a
                      class="btn btn-primary"
                      href=${host._connectAuthorizeUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      style="align-self:flex-start;text-decoration:none;"
                    >
                      ${host._t(
                        "settings_open_signin_page",
                        "Open sign-in page →",
                      )}
                    </a>
                    <div
                      style="font-size:12px;color:var(--secondary-text-color);"
                    >
                      ${host._t(
                        "settings_signin_new_tab_hint",
                        "Opens in a new tab. After signing in, return to this page — the panel updates automatically.",
                      )}
                    </div>
                  </div>`
                : ""
            }
            ${
              host._config.selora_connect_enabled
                ? html`
                    <div
                      style="display:flex;align-items:center;gap:8px;padding:8px 0 0;"
                    >
                      <code
                        style="font-size:12px;word-break:break-all;flex:1;padding:8px 10px;background:var(--card-background-color);border-radius:6px;border:1px solid var(--divider-color);overflow:hidden;text-overflow:ellipsis;"
                        >${
                          host._config.selora_mcp_url ||
                          `${location.origin}${location.pathname.split("/selora-ai")[0]}/api/selora_ai/mcp`
                        }</code
                      >
                      <ha-icon-button
                        @click=${() => {
                          const mcpUrl =
                            host._config.selora_mcp_url ||
                            `${location.origin}${location.pathname.split("/selora-ai")[0]}/api/selora_ai/mcp`;
                          navigator.clipboard.writeText(mcpUrl);
                          host._showToast(
                            host._t(
                              "settings_mcp_url_copied_toast",
                              "MCP URL copied to clipboard",
                            ),
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
                : ""
            }
            ${
              host._config.developer_mode &&
              !host._config.selora_connect_enabled
                ? html`
                    <div style="padding:8px 0 0;">
                      ${_textInput({
                        label: host._t(
                          "settings_connect_server_url_label",
                          "Connect Server URL",
                        ),
                        value:
                          host._config.selora_connect_url ||
                          "https://connect.selorahomes.com",
                        oninput: (e) =>
                          host._updateConfig(
                            "selora_connect_url",
                            e.target.value,
                          ),
                      })}
                    </div>
                  `
                : ""
            }
          </div>

          <div class="settings-section-title">
            ${host._t("settings_mcp_tokens_section_title", "MCP TOKENS")}
          </div>
          <p
            style="font-size:13px;color:var(--secondary-text-color);margin:0 0 8px;"
          >
            ${host._t(
              "settings_mcp_tokens_desc",
              "MCP tokens are an alternative to Selora Connect. Use them for tools that don't support OAuth or when you prefer token-based authentication.",
            )}
          </p>
          ${
            host._mcpTokens.length === 0
              ? html`<div
                  style="font-size:13px;color:var(--secondary-text-color);padding:4px 0 8px;"
                >
                  ${host._t("settings_no_tokens_yet", "No tokens yet.")}
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
                              ${
                                t.expires_at
                                  ? html`<span
                                      >&middot; expires
                                      ${new Date(
                                        t.expires_at,
                                      ).toLocaleDateString(undefined, {
                                        month: "short",
                                        day: "numeric",
                                      })}</span
                                    >`
                                  : ""
                              }
                              ${
                                t.last_used_at
                                  ? html`<span
                                      >&middot; used
                                      ${_timeAgo(t.last_used_at)}</span
                                    >`
                                  : ""
                              }
                            </div>
                          </div>
                          <ha-icon-button
                            ?disabled=${host._revokingTokenId === t.id}
                            @click=${() => host._revokeMcpToken(t.id)}
                          >
                            ${
                              host._revokingTokenId === t.id
                                ? html`<span
                                    class="spinner"
                                    style="width:14px;height:14px;"
                                  ></span>`
                                : html`<ha-icon
                                    icon="mdi:delete-outline"
                                    style="--mdc-icon-size:20px;"
                                  ></ha-icon>`
                            }
                          </ha-icon-button>
                        </div>
                      `,
                    )}
                  </div>
                `
          }
          <button
            class="btn btn-outline"
            style="margin-top:8px;"
            @click=${() => host._openCreateTokenDialog()}
          >
            <ha-icon icon="mdi:plus" style="--mdc-icon-size:16px;"></ha-icon>
            ${host._t("settings_add_token_button", "Add token")}
          </button>
        </div>

        ${renderCreateTokenDialog(host)} ${renderIgnoreList(host)}

        <div class="section-card settings-section">
          <div class="section-card-header">
            <h3>
              ${host._t(
                "settings_command_approvals_heading",
                "Command Approvals",
              )}
            </h3>
          </div>
          <p
            style="font-size:13px;color:var(--secondary-text-color);margin:0 0 12px;"
          >
            Services Selora AI is allowed to run on your behalf without
            prompting. Granted by clicking <em>Always</em> on an approval card
            in chat. Revoke any you no longer want auto-approved.
          </p>
          ${renderApprovalGrants(host)}
        </div>

        <details class="section-card settings-section advanced-section" open>
          <summary class="advanced-toggle">
            ${host._t(
              "settings_advanced_settings_heading",
              "Advanced settings",
            )}
            <ha-icon
              icon="mdi:chevron-right"
              class="advanced-chevron"
              style="margin-left:auto;"
            ></ha-icon>
          </summary>

          <div class="settings-section-title" style="margin-top:16px;">
            ${host._t(
              "settings_background_services_title",
              "BACKGROUND SERVICES",
            )}
          </div>

          <div class="service-group">
            <div class="service-row">
              <div class="service-label-group">
                <label
                  >${host._t(
                    "settings_data_collector_label",
                    "Data collector (AI analysis)",
                  )}</label
                >
                <span class="service-desc"
                  >${host._t(
                    "settings_data_collector_desc",
                    "Feeds entity history to Selora AI",
                  )}</span
                >
              </div>
              <ha-switch
                .checked=${host._config.collector_enabled}
                @change=${(e) =>
                  host._updateConfig("collector_enabled", e.target.checked)}
              ></ha-switch>
            </div>
            ${
              host._config.collector_enabled
                ? html`
                    <div class="service-details">
                      <div style="display:flex;gap:12px;">
                        <div class="form-group" style="flex:1;margin-bottom:0;">
                          <label
                            >${host._t("settings_mode_label", "Mode")}</label
                          >
                          <select
                            class="form-select"
                            .value=${host._config.collector_mode}
                            @change=${(e) =>
                              host._updateConfig(
                                "collector_mode",
                                e.target.value,
                              )}
                          >
                            <option value="continuous">
                              ${host._t("settings_mode_continuous", "Continuous")}
                            </option>
                            <option value="scheduled">
                              ${host._t(
                                "settings_mode_scheduled_window",
                                "Scheduled Window",
                              )}
                            </option>
                          </select>
                        </div>
                        <div
                          class="form-group"
                          style="width:130px;margin-bottom:0;"
                        >
                          <label
                            >${host._t(
                              "settings_interval_seconds_label",
                              "Interval (s)",
                            )}</label
                          >
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
                      ${
                        host._config.collector_mode === "scheduled"
                          ? html`
                              <div
                                style="display:flex;gap:12px;margin-top:12px;"
                              >
                                <div style="flex:1;">
                                  ${_textInput({
                                    label: host._t(
                                      "settings_start_hhmm_label",
                                      "Start (HH:MM)",
                                    ),
                                    value: host._config.collector_start_time,
                                    oninput: (e) =>
                                      host._updateConfig(
                                        "collector_start_time",
                                        e.target.value,
                                      ),
                                  })}
                                </div>
                                <div style="flex:1;">
                                  ${_textInput({
                                    label: host._t(
                                      "settings_end_hhmm_label",
                                      "End (HH:MM)",
                                    ),
                                    value: host._config.collector_end_time,
                                    oninput: (e) =>
                                      host._updateConfig(
                                        "collector_end_time",
                                        e.target.value,
                                      ),
                                  })}
                                </div>
                              </div>
                            `
                          : ""
                      }
                    </div>
                  `
                : ""
            }
          </div>

          <div class="service-group">
            <div class="service-row">
              <div class="service-label-group">
                <label
                  >${host._t(
                    "settings_network_discovery_label",
                    "Network discovery",
                  )}</label
                >
                <span class="service-desc"
                  >${host._t(
                    "settings_network_discovery_desc",
                    "Scans local network for new devices",
                  )}</span
                >
              </div>
              <ha-switch
                .checked=${host._config.discovery_enabled}
                @change=${(e) =>
                  host._updateConfig("discovery_enabled", e.target.checked)}
              ></ha-switch>
            </div>
            ${
              host._config.discovery_enabled
                ? html`
                    <div class="service-details">
                      <div style="display:flex;gap:12px;">
                        <div class="form-group" style="flex:1;margin-bottom:0;">
                          <label
                            >${host._t("settings_mode_label", "Mode")}</label
                          >
                          <select
                            class="form-select"
                            .value=${host._config.discovery_mode}
                            @change=${(e) =>
                              host._updateConfig(
                                "discovery_mode",
                                e.target.value,
                              )}
                          >
                            <option value="continuous">
                              ${host._t("settings_mode_continuous", "Continuous")}
                            </option>
                            <option value="scheduled">
                              ${host._t(
                                "settings_mode_scheduled_window",
                                "Scheduled Window",
                              )}
                            </option>
                          </select>
                        </div>
                        <div
                          class="form-group"
                          style="width:130px;margin-bottom:0;"
                        >
                          <label
                            >${host._t(
                              "settings_interval_seconds_label",
                              "Interval (s)",
                            )}</label
                          >
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
                      ${
                        host._config.discovery_mode === "scheduled"
                          ? html`
                              <div
                                style="display:flex;gap:12px;margin-top:12px;"
                              >
                                <div style="flex:1;">
                                  ${_textInput({
                                    label: host._t(
                                      "settings_start_hhmm_label",
                                      "Start (HH:MM)",
                                    ),
                                    value: host._config.discovery_start_time,
                                    oninput: (e) =>
                                      host._updateConfig(
                                        "discovery_start_time",
                                        e.target.value,
                                      ),
                                  })}
                                </div>
                                <div style="flex:1;">
                                  ${_textInput({
                                    label: host._t(
                                      "settings_end_hhmm_label",
                                      "End (HH:MM)",
                                    ),
                                    value: host._config.discovery_end_time,
                                    oninput: (e) =>
                                      host._updateConfig(
                                        "discovery_end_time",
                                        e.target.value,
                                      ),
                                  })}
                                </div>
                              </div>
                            `
                          : ""
                      }
                    </div>
                  `
                : ""
            }
          </div>

          <div class="service-group">
            <div class="service-row">
              <div class="service-label-group">
                <label
                  >${host._t(
                    "settings_pattern_detection_label",
                    "Pattern detection",
                  )}</label
                >
                <span class="service-desc"
                  >${host._t(
                    "settings_pattern_detection_desc",
                    "Detects recurring usage patterns and proposes automations",
                  )}</span
                >
              </div>
              <ha-switch
                .checked=${host._config.pattern_detection_enabled !== false}
                @change=${(e) =>
                  host._updateConfig(
                    "pattern_detection_enabled",
                    e.target.checked,
                  )}
              ></ha-switch>
            </div>
          </div>

          <div class="service-group">
            <div class="service-row">
              <div class="service-label-group">
                <label
                  >${host._t(
                    "settings_health_monitoring_label",
                    "Health monitoring",
                  )}</label
                >
                <span class="service-desc"
                  >${host._t(
                    "settings_health_monitoring_desc",
                    "Deterministic checks for offline devices, low batteries, integration errors and automation issues — powers the Health page.",
                  )}</span
                >
              </div>
              <ha-switch
                .checked=${host._config.insights_enabled !== false}
                @change=${(e) =>
                  host._updateConfig("insights_enabled", e.target.checked)}
              ></ha-switch>
            </div>
            ${
              host._config.insights_enabled !== false
                ? html`
                    <div class="service-details">
                      <div
                        class="form-group"
                        style="width:150px;margin-bottom:0;"
                      >
                        <label
                          >${host._t(
                            "settings_scan_interval_seconds_label",
                            "Scan interval (s)",
                          )}</label
                        >
                        <input
                          class="form-select"
                          type="number"
                          min="60"
                          step="60"
                          .value=${host._config.insights_interval}
                          @input=${(e) =>
                            host._updateConfig(
                              "insights_interval",
                              parseInt(e.target.value),
                            )}
                          style="width:100%;box-sizing:border-box;"
                        />
                      </div>
                    </div>
                  `
                : ""
            }
          </div>

          <div class="service-group">
            <div class="service-row">
              <div class="service-label-group">
                <label
                  >${host._t(
                    "settings_auto_remove_stale_label",
                    "Auto-remove stale automations",
                  )}</label
                >
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
                <label
                  >${host._t(
                    "settings_telemetry_label",
                    "Anonymous telemetry",
                  )}</label
                >
                <span class="service-desc"
                  >${host._t(
                    "settings_telemetry_desc",
                    "Off by default. When on, sends anonymous counts about your setup (devices, integrations, automations, scenes, scripts, blueprints, areas) and how often Selora repairs model output, so we can improve the product. Never sends entity names, prompts, or responses.",
                  )}</span
                >
              </div>
              <ha-switch
                .checked=${host._config.telemetry_enabled === true}
                @change=${(e) =>
                  host._updateConfig("telemetry_enabled", e.target.checked)}
              ></ha-switch>
            </div>
          </div>

          <div class="service-group">
            <div class="service-row">
              <div class="service-label-group">
                <label
                  >${host._t(
                    "settings_developer_mode_label",
                    "Developer mode",
                  )}</label
                >
                <span class="service-desc"
                  >${host._t(
                    "settings_developer_mode_desc",
                    "Exposes raw entity payloads and debug logs",
                  )}</span
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
                    host._showToast(
                      host._t(
                        "settings_dev_mode_save_failed_toast",
                        "Failed to save developer mode.",
                      ),
                      "error",
                    );
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
              ${
                host._savingAdvancedConfig
                  ? host._t("settings_saving_label", "Saving…")
                  : host._t("settings_save_button", "Save")
              }
            </button>
          </div>
        </details>

        <div class="section-card settings-section">
          <div class="section-card-header">
            <h3>
              ${host._t("settings_clear_cache_label", "Clear learned data")}
            </h3>
          </div>
          <div class="settings-maintenance">
            <span class="service-desc"
              >${host._t(
                "settings_clear_cache_desc",
                "Wipes stored usage history, detected patterns, and pending suggestions. Use this if suggestions reference devices you've removed. Selora relearns over time; your saved automations are not affected.",
              )}</span
            >
            <button
              class="btn btn-danger"
              @click=${host._clearLearnedCache}
              ?disabled=${host._clearingCache}
            >
              ${
                host._clearingCache
                  ? host._t("settings_clear_cache_clearing", "Clearing…")
                  : host._t("settings_clear_cache_button", "Clear")
              }
            </button>
          </div>
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

function renderApprovalGrants(host) {
  const grants = host._approvalGrants || [];
  if (!grants.length) {
    return html`<div
      style="font-size:13px;color:var(--secondary-text-color);padding:4px 0 8px;"
    >
      ${host._t(
        "settings_no_approvals_yet_prefix",
        "No saved approvals yet. The next time Selora asks before running something risky, click",
      )}
      <em>${host._t("settings_no_approvals_always_word", "Always")}</em>
      ${host._t("settings_no_approvals_yet_suffix", "to remember it here.")}
    </div>`;
  }
  const riskColor = {
    low: "#3b82f6",
    medium: "#f59e0b",
    high: "#ef4444",
  };
  return html`
    <div class="mcp-token-list">
      ${grants.map((g) => {
        // ``key`` is the full grant identifier ("service" for a
        // wildcard, "service:entity_id" for a per-entity row).
        // Legacy rows that predate the entity_id field may have only
        // ``service`` — fall back so the row is still revocable.
        const grantKey = g.key || g.service;
        // Resolve the entity's friendly name (when keyed). Falls back
        // to the bare entity_id so the row stays informative even if
        // the entity has been deleted from HA.
        const entityFriendly = g.entity_id
          ? host?.hass?.states?.[g.entity_id]?.attributes?.friendly_name ||
            g.entity_id
          : null;
        return html`
          <div class="mcp-token-row">
            <ha-icon
              icon=${
                entityFriendly
                  ? "mdi:shield-account-outline"
                  : "mdi:shield-check-outline"
              }
              style="--mdc-icon-size:20px;color:${
                riskColor[g.risk_level] || "var(--selora-accent)"
              };flex-shrink:0;"
              title=${
                entityFriendly
                  ? host._t(
                      "settings_per_entity_approval_title",
                      "Per-entity approval",
                    )
                  : host._t(
                      "settings_wildcard_approval_title",
                      "Wildcard — applies to every entity of this service",
                    )
              }
            ></ha-icon>
            <div class="mcp-token-info">
              <div class="mcp-token-name">
                ${g.service}${
                  entityFriendly
                    ? html` <span
                        style="color:var(--secondary-text-color);font-weight:400;"
                        >→ ${entityFriendly}</span
                      >`
                    : html` <span
                        style="color:var(--secondary-text-color);font-weight:400;font-style:italic;"
                        >→
                        ${host._t("settings_approval_all_label", "all")}</span
                      >`
                }
                <span
                  class="mcp-token-badge"
                  style="background:${
                    riskColor[g.risk_level] || "#3b82f6"
                  };color:#fff;text-transform:uppercase;"
                  title=${_riskTooltip(g.risk_level)}
                  >${g.risk_level || "low"}</span
                >
              </div>
              <div class="mcp-token-meta">
                <span
                  >granted
                  ${_timeAgo(g.granted_at)}${
                    g.granted_by_name
                      ? html` by <strong>${g.granted_by_name}</strong>`
                      : ""
                  }</span
                >
              </div>
            </div>
            <ha-icon-button
              ?disabled=${host._revokingApprovalKey === grantKey}
              @click=${() => host._revokeApproval(grantKey)}
            >
              ${
                host._revokingApprovalKey === grantKey
                  ? html`<span
                      class="spinner"
                      style="width:14px;height:14px;"
                    ></span>`
                  : html`<ha-icon
                      icon="mdi:delete-outline"
                      style="--mdc-icon-size:20px;"
                    ></ha-icon>`
              }
            </ha-icon-button>
          </div>
        `;
      })}
    </div>
  `;
}

function _riskTooltip(level) {
  switch (level) {
    case "high":
      return "High risk — irreversible or safety-impacting action (locks, alarms, gates). Always asks for confirmation unless approved here.";
    case "medium":
      return "Medium risk — affects shared state or comfort (climate, scenes, media). Asks for confirmation unless approved here.";
    case "low":
      return "Low risk — easily reversible action (lights, switches). Runs without prompting.";
    default:
      return "Risk level unknown.";
  }
}

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
          <h3 style="margin:0 0 12px;">
            ${host._t("settings_token_created_heading", "Token Created")}
          </h3>
          <p
            style="font-size:13px;color:var(--secondary-text-color);margin:0 0 12px;"
          >
            ${host._t(
              "settings_token_created_desc",
              "Copy this token now — it won't be shown again.",
            )}
          </p>
          <div
            style="display:flex;align-items:center;gap:8px;padding:10px 12px;background:var(--card-background-color);border:1px solid var(--selora-accent);border-radius:8px;font-family:monospace;font-size:13px;word-break:break-all;"
          >
            <span style="flex:1;user-select:all;">${host._createdToken}</span>
            <button
              style="background:none;border:none;color:var(--selora-accent);cursor:pointer;padding:8px;border-radius:50%;flex-shrink:0;"
              @click=${() => {
                navigator.clipboard.writeText(host._createdToken);
                host._showToast(
                  host._t(
                    "settings_token_copied_toast",
                    "Token copied to clipboard",
                  ),
                  "success",
                );
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
              ${host._t("settings_done_button", "Done")}
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
        <h3 style="margin:0 0 16px;">
          ${host._t("settings_create_mcp_token_heading", "Create MCP Token")}
        </h3>

        <div class="form-group">
          <label>${host._t("settings_token_name_label", "Name")}</label>
          <input
            class="modal-input"
            type="text"
            placeholder=${host._t(
              "settings_token_name_placeholder",
              "e.g. Claude Desktop",
            )}
            .value=${host._newTokenName}
            @input=${(e) => {
              host._newTokenName = e.target.value;
            }}
            style="width:100%;box-sizing:border-box;"
          />
        </div>

        <div class="form-group">
          <label
            >${host._t(
              "settings_permission_level_label",
              "Permission Level",
            )}</label
          >
          <select
            class="form-select"
            .value=${permission}
            @change=${(e) => {
              host._newTokenPermission = e.target.value;
              host.requestUpdate();
            }}
          >
            <option value="read_only">
              ${host._t("settings_perm_read_only", "Read Only")}
            </option>
            <option value="admin">
              ${host._t("settings_perm_admin_all", "Admin (all tools)")}
            </option>
            <option value="custom">
              ${host._t("settings_perm_custom", "Custom (select tools)")}
            </option>
          </select>
        </div>

        ${
          permission === "custom"
            ? html`
                <div class="form-group">
                  <label
                    >${host._t(
                      "settings_allowed_tools_label",
                      "Allowed Tools",
                    )}</label
                  >
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
                          ${
                            tool.admin
                              ? html`<span
                                  class="mcp-token-badge mcp-token-badge--admin"
                                  style="font-size:10px;padding:1px 5px;"
                                  >${host._t("settings_admin_badge", "admin")}</span
                                >`
                              : ""
                          }
                        </label>
                      `,
                    )}
                  </div>
                </div>
              `
            : ""
        }

        <div class="form-group">
          <label
            >${host._t(
              "settings_expiration_label",
              "Expiration (optional)",
            )}</label
          >
          <select
            class="form-select"
            .value=${host._newTokenExpiry}
            @change=${(e) => {
              host._newTokenExpiry = e.target.value;
              host.requestUpdate();
            }}
          >
            <option value="">
              ${host._t("settings_expiry_never", "Never expires")}
            </option>
            <option value="7">
              ${host._t("settings_expiry_7_days", "7 days")}
            </option>
            <option value="30">
              ${host._t("settings_expiry_30_days", "30 days")}
            </option>
            <option value="90">
              ${host._t("settings_expiry_90_days", "90 days")}
            </option>
            <option value="365">
              ${host._t("settings_expiry_1_year", "1 year")}
            </option>
          </select>
        </div>

        <div
          style="display:flex;justify-content:flex-end;gap:8px;margin-top:16px;"
        >
          <button
            class="btn btn-outline"
            @click=${() => host._closeCreateTokenDialog()}
          >
            ${host._t("settings_cancel_button", "Cancel")}
          </button>
          <button
            class="btn btn-primary"
            ?disabled=${!host._newTokenName?.trim() || host._creatingToken}
            @click=${() => host._createMcpToken()}
          >
            ${
              host._creatingToken
                ? html`<span
                    class="spinner"
                    style="width:14px;height:14px;"
                  ></span>`
                : host._t("settings_create_token_button", "Create Token")
            }
          </button>
        </div>
      </div>
    </div>
  `;
}
