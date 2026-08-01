import { html } from "lit";

/**
 * Banner shown when the code the panel talks to isn't the code that was
 * deployed. Two distinct states, from `host._versionStatus`
 * (`selora_ai/version_status`):
 *
 * - `restart_required` — the integration's Python on disk is newer than the
 *   modules in memory. A reload can't fix it (modules are cached in
 *   `sys.modules`), so the ask is a Home Assistant restart. Until then the
 *   panel may call websocket commands whose shipped schema the backend
 *   doesn't know yet.
 * - `panel_reload_required` — this browser is running a cached bundle older
 *   than the one on disk. A page reload picks up the new cache-buster.
 *
 * Dismissal is per page view: the condition is transient, and persisting a
 * dismissal would hide a real "your update isn't live yet" state.
 */
export function renderStaleCodeNotice(host) {
  const status = host._versionStatus;
  if (!status || host._staleCodeDismissed) return "";
  const restart = status.restart_required;
  const reload = status.panel_reload_required;
  if (!restart && !reload) return "";
  const title = restart
    ? host._t("stale_code_restart_title", "Restart to finish updating")
    : host._t("stale_code_reload_title", "Reload to finish updating");
  const body = restart
    ? host._t(
        "stale_code_restart_body",
        "Selora AI's files changed on disk, but Home Assistant is still running the previously loaded code. Restart Home Assistant to finish the update.",
      )
    : host._t(
        "stale_code_reload_body",
        "This page is running an older Selora AI panel. Reload to pick up the deployed version.",
      );
  return html`
    <div
      class="telemetry-consent stale-code-notice"
      role="alert"
      aria-label=${title}
    >
      <ha-icon icon="mdi:update"></ha-icon>
      <div class="telemetry-consent-text">
        <strong>${title}</strong>
        <span>${body}</span>
      </div>
      <div class="telemetry-consent-actions">
        ${
          reload && !restart
            ? html`
                <button
                  class="btn btn-primary"
                  @click=${() => window.location.reload()}
                >
                  ${host._t("stale_code_reload_action", "Reload")}
                </button>
              `
            : ""
        }
        <button class="btn" @click=${() => host._dismissStaleCodeNotice()}>
          ${host._t("stale_code_dismiss", "Dismiss")}
        </button>
      </div>
    </div>
  `;
}
