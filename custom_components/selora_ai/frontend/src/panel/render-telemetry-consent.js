import { html } from "lit";

/**
 * One-time telemetry consent banner shown atop the panel until the user
 * accepts or declines. Visibility + persistence are driven by
 * `host._config.telemetry_prompt_seen` (set via `host._setTelemetryConsent`);
 * once seen it never shows again. Default is off, so accepting is the only
 * way telemetry turns on.
 */
export function renderTelemetryConsent(host) {
  const cfg = host._config;
  // Wait for config to load; don't nag if already decided or already on.
  if (!cfg || cfg.telemetry_prompt_seen || cfg.telemetry_enabled) return "";
  return html`
    <div
      class="telemetry-consent"
      role="region"
      aria-label=${host._t("telemetry_consent_title", "Help improve Selora AI")}
    >
      <ha-icon icon="mdi:chart-box-outline"></ha-icon>
      <div class="telemetry-consent-text">
        <strong
          >${host._t(
            "telemetry_consent_title",
            "Help improve Selora AI",
          )}</strong
        >
        <span
          >${host._t(
            "telemetry_consent_body",
            "Share anonymous counts about your setup (devices, integrations, automations…) and how often Selora repairs model output. Never your entity names, prompts, or responses.",
          )}</span
        >
      </div>
      <div class="telemetry-consent-actions">
        <button
          class="btn btn-primary"
          @click=${() => host._setTelemetryConsent(true)}
        >
          ${host._t("telemetry_consent_enable", "Enable")}
        </button>
        <button class="btn" @click=${() => host._setTelemetryConsent(false)}>
          ${host._t("telemetry_consent_decline", "No thanks")}
        </button>
      </div>
    </div>
  `;
}
