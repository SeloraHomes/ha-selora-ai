import { html } from "lit";
import { relativeTime } from "../shared/date-utils.js";

export function renderVersionHistoryDrawer(host, a) {
  const automationId = a.automation_id || a.entity_id;
  const versions = host._versions[automationId] || [];
  const loading = host._loadingVersions[automationId];

  return html`
    <div class="version-history">
      ${
        loading
          ? html`<div class="version-history-empty">
              ${host._t("version_history_loading", "Loading…")}
            </div>`
          : versions.length === 0
            ? html`<div class="version-history-empty">
                ${host._t("version_history_empty", "No version history yet.")}
              </div>`
            : html`
                <ol class="version-list">
                  ${versions.map((v, i) =>
                    renderVersionEntry(
                      host,
                      automationId,
                      v,
                      i,
                      versions.length,
                    ),
                  )}
                </ol>
              `
      }
    </div>
  `;
}

function renderVersionEntry(host, automationId, v, i, total) {
  const key = `${automationId}_${v.version_id}`;
  const restoring = host._restoringVersion[key];
  const date = new Date(v.created_at);
  const timeAgo = relativeTime(date);
  const isCurrent = i === 0;
  const message = v.message || v.version_message;
  const yamlOpen = !!host._expandedAutomations[`ver_${key}`];
  const versionNumber = total - i;
  return html`
    <li class="version-entry ${isCurrent ? "current" : ""}">
      <span class="version-entry-dot" aria-hidden="true"></span>
      <div class="version-entry-card">
        <header class="version-entry-head">
          <div class="version-entry-title">
            <span class="version-entry-num">v${versionNumber}</span>
            ${
              isCurrent
                ? html`<span class="version-entry-badge"
                    >${host._t("version_history_current_badge", "Current")}</span
                  >`
                : ""
            }
          </div>
          <time class="version-entry-time" title=${date.toISOString()}
            >${timeAgo}</time
          >
        </header>
        ${message ? html`<p class="version-entry-message">${message}</p>` : ""}
        <div class="version-entry-actions">
          <button
            class="btn btn-outline version-entry-btn"
            @click=${() => host._toggleExpandAutomation(`ver_${key}`)}
          >
            <ha-icon
              icon=${yamlOpen ? "mdi:eye-off-outline" : "mdi:code-braces"}
              style="--mdc-icon-size:14px;"
            ></ha-icon>
            ${
              yamlOpen
                ? host._t("version_history_hide_yaml", "Hide YAML")
                : host._t("version_history_view_yaml", "View YAML")
            }
          </button>
          ${
            !isCurrent
              ? html`
                  <button
                    class="btn btn-outline version-entry-btn"
                    ?disabled=${restoring || !(v.yaml || v.yaml_content)}
                    @click=${() =>
                      host._restoreVersion(
                        automationId,
                        v.version_id,
                        v.yaml || v.yaml_content || "",
                      )}
                  >
                    <ha-icon
                      icon="mdi:restore"
                      style="--mdc-icon-size:14px;"
                    ></ha-icon>
                    ${
                      restoring
                        ? host._t("version_history_restoring", "Restoring…")
                        : host._t(
                            "version_history_restore_button",
                            "Restore this version",
                          )
                    }
                  </button>
                `
              : ""
          }
        </div>
        ${
          yamlOpen
            ? html`<div class="version-entry-yaml">
                <ha-code-editor
                  mode="yaml"
                  .value=${
                    v.yaml ||
                    v.yaml_content ||
                    host._t(
                      "version_history_no_yaml_stored",
                      "(no YAML stored)",
                    )
                  }
                  read-only
                  style="--code-mirror-font-size:13px;"
                ></ha-code-editor>
              </div>`
            : ""
        }
      </div>
    </li>
  `;
}

export function renderDiffViewer(host) {
  if (!host._diffOpen) return "";
  const automationId = host._diffAutomationId;
  const versions = host._versions[automationId] || [];
  return html`
    <div
      style="position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center;"
      @click=${(e) => {
        if (e.target === e.currentTarget) {
          host._diffOpen = false;
          host.requestUpdate();
        }
      }}
    >
      <div
        style="background:var(--card-background-color);border-radius:12px;width:90%;max-width:760px;max-height:85vh;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 8px 32px rgba(0,0,0,0.4);"
      >
        <div
          style="display:flex;align-items:center;justify-content:space-between;padding:16px 20px;border-bottom:1px solid var(--divider-color);"
        >
          <span style="font-weight:700;font-size:15px;">
            <ha-icon
              icon="mdi:compare"
              style="--mdc-icon-size:17px;vertical-align:middle;margin-right:6px;"
            ></ha-icon>
            ${host._t("version_history_compare_title", "Compare Versions")}
          </span>
          <ha-icon
            icon="mdi:close"
            style="cursor:pointer;--mdc-icon-size:20px;"
            @click=${() => {
              host._diffOpen = false;
              host.requestUpdate();
            }}
          ></ha-icon>
        </div>
        <div
          style="padding:12px 20px;border-bottom:1px solid var(--divider-color);display:flex;gap:12px;align-items:center;flex-wrap:wrap;"
        >
          <div style="display:flex;align-items:center;gap:8px;">
            <span style="font-size:12px;opacity:0.7;"
              >${host._t(
                "version_history_version_a_label",
                "Version A (newer):",
              )}</span
            >
            <select
              style="font-size:12px;padding:4px 8px;border-radius:6px;background:var(--input-fill-color);border:1px solid var(--divider-color);color:var(--primary-text-color);"
              .value=${host._diffVersionA || ""}
              @change=${async (e) => {
                host._diffVersionA = e.target.value;
                await host._loadDiff(
                  automationId,
                  host._diffVersionA,
                  host._diffVersionB,
                );
              }}
            >
              ${versions.map(
                (v, i) =>
                  html`<option value=${v.version_id}>
                    v${versions.length - i} —
                    ${
                      v.message ||
                      v.version_message ||
                      new Date(v.created_at).toLocaleDateString()
                    }
                  </option>`,
              )}
            </select>
          </div>
          <div style="display:flex;align-items:center;gap:8px;">
            <span style="font-size:12px;opacity:0.7;"
              >${host._t(
                "version_history_version_b_label",
                "Version B (older):",
              )}</span
            >
            <select
              style="font-size:12px;padding:4px 8px;border-radius:6px;background:var(--input-fill-color);border:1px solid var(--divider-color);color:var(--primary-text-color);"
              .value=${host._diffVersionB || ""}
              @change=${async (e) => {
                host._diffVersionB = e.target.value;
                await host._loadDiff(
                  automationId,
                  host._diffVersionA,
                  host._diffVersionB,
                );
              }}
            >
              ${versions.map(
                (v, i) =>
                  html`<option value=${v.version_id}>
                    v${versions.length - i} —
                    ${
                      v.message ||
                      v.version_message ||
                      new Date(v.created_at).toLocaleDateString()
                    }
                  </option>`,
              )}
            </select>
          </div>
        </div>
        <div style="flex:1;overflow-y:auto;padding:12px 20px;">
          ${
            host._loadingDiff
              ? html`<div style="opacity:0.5;text-align:center;padding:24px;">
                  ${host._t("version_history_loading_diff", "Loading diff…")}
                </div>`
              : host._diffResult.length === 0
                ? html`<div style="opacity:0.5;text-align:center;padding:24px;">
                    ${host._t("version_history_no_diff", "No differences found.")}
                  </div>`
                : html`<pre
                    style="font-size:12px;margin:0;font-family:monospace;white-space:pre-wrap;"
                  >
${host._diffResult.map((line) => {
  const bg = line.startsWith("+")
    ? "rgba(40,167,69,0.15)"
    : line.startsWith("-")
      ? "rgba(220,53,69,0.15)"
      : "transparent";
  const color = line.startsWith("+")
    ? "#40c057"
    : line.startsWith("-")
      ? "#fa5252"
      : "var(--primary-text-color)";
  return html`<span
    style="display:block;background:${bg};color:${color};padding:1px 4px;"
    >${line}</span
  >`;
})}</pre>`
          }
        </div>
      </div>
    </div>
  `;
}
