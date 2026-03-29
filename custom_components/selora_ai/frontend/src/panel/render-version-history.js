import { html } from "lit";
import { relativeTime } from "../shared/date-utils.js";

export function renderVersionHistoryDrawer(host, a) {
  const automationId = a.automation_id || a.entity_id;
  const versions = host._versions[automationId] || [];
  const loading = host._loadingVersions[automationId];

  return html`
    <div
      style="border:1px solid var(--divider-color);border-radius:8px;margin:8px 0 4px;padding:12px;background:var(--secondary-background-color);"
    >
      ${loading
        ? html`<div style="opacity:0.5;font-size:12px;">Loading…</div>`
        : versions.length === 0
          ? html`<div style="opacity:0.5;font-size:12px;">
              No version history yet.
            </div>`
          : html`
              <div style="position:relative;padding-left:20px;">
                <div
                  style="position:absolute;left:7px;top:0;bottom:0;width:2px;background:var(--divider-color);border-radius:2px;"
                ></div>
                ${versions.map((v, i) => {
                  const key = `${automationId}_${v.version_id}`;
                  const restoring = host._restoringVersion[key];
                  const date = new Date(v.created_at);
                  const timeAgo = relativeTime(date);
                  const isCurrent = i === 0;
                  return html`
                    <div
                      style="position:relative;margin-bottom:${i <
                      versions.length - 1
                        ? "14px"
                        : "0"};padding-left:14px;"
                    >
                      <div
                        style="position:absolute;left:-6px;top:3px;width:10px;height:10px;border-radius:50%;background:${isCurrent
                          ? "#fbbf24"
                          : "var(--divider-color)"};border:2px solid var(--secondary-background-color);"
                      ></div>
                      <div
                        style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;"
                      >
                        <span style="font-size:12px;font-weight:600;"
                          >v${versions.length - i}</span
                        >
                        <span
                          style="font-size:11px;opacity:0.6;"
                          title=${date.toISOString()}
                          >${timeAgo}</span
                        >
                        ${isCurrent
                          ? html`<span
                              style="font-size:10px;background:#fbbf24;color:#000;border-radius:4px;padding:1px 6px;font-weight:600;"
                              >current</span
                            >`
                          : ""}
                      </div>
                      ${v.message || v.version_message
                        ? html`<div
                            style="font-size:11px;opacity:0.6;margin-top:2px;"
                          >
                            ${v.message || v.version_message}
                          </div>`
                        : ""}
                      <div style="display:flex;gap:6px;margin-top:6px;">
                        <button
                          class="btn btn-outline"
                          style="font-size:10px;padding:2px 7px;"
                          @click=${() =>
                            host._toggleExpandAutomation(`ver_${key}`)}
                        >
                          <ha-icon
                            icon="mdi:code-braces"
                            style="--mdc-icon-size:11px;"
                          ></ha-icon>
                          ${host._expandedAutomations[`ver_${key}`]
                            ? "Hide"
                            : "YAML"}
                        </button>
                        ${!isCurrent
                          ? html`
                              <button
                                class="btn btn-outline"
                                style="font-size:10px;padding:2px 7px;"
                                ?disabled=${restoring ||
                                !(v.yaml || v.yaml_content)}
                                @click=${() =>
                                  host._restoreVersion(
                                    automationId,
                                    v.version_id,
                                    v.yaml || v.yaml_content || "",
                                  )}
                              >
                                <ha-icon
                                  icon="mdi:restore"
                                  style="--mdc-icon-size:11px;"
                                ></ha-icon>
                                ${restoring ? "Restoring…" : "Restore"}
                              </button>
                            `
                          : ""}
                      </div>
                      ${host._expandedAutomations[`ver_${key}`]
                        ? html`<ha-code-editor
                            mode="yaml"
                            .value=${v.yaml ||
                            v.yaml_content ||
                            "(no YAML stored)"}
                            read-only
                            style="--code-mirror-font-size:12px;margin-top:6px;"
                          ></ha-code-editor>`
                        : ""}
                    </div>
                  `;
                })}
              </div>
            `}
    </div>
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
            Compare Versions
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
            <span style="font-size:12px;opacity:0.7;">Version A (newer):</span>
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
                    ${v.message ||
                    v.version_message ||
                    new Date(v.created_at).toLocaleDateString()}
                  </option>`,
              )}
            </select>
          </div>
          <div style="display:flex;align-items:center;gap:8px;">
            <span style="font-size:12px;opacity:0.7;">Version B (older):</span>
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
                    ${v.message ||
                    v.version_message ||
                    new Date(v.created_at).toLocaleDateString()}
                  </option>`,
              )}
            </select>
          </div>
        </div>
        <div style="flex:1;overflow-y:auto;padding:12px 20px;">
          ${host._loadingDiff
            ? html`<div style="opacity:0.5;text-align:center;padding:24px;">
                Loading diff…
              </div>`
            : host._diffResult.length === 0
              ? html`<div style="opacity:0.5;text-align:center;padding:24px;">
                  No differences found.
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
                  })}</pre
                >`}
        </div>
      </div>
    </div>
  `;
}

export function renderDeletedSection(host) {
  const daysRemaining = (deletedAt) => {
    const elapsed =
      (Date.now() - new Date(deletedAt).getTime()) / (1000 * 60 * 60 * 24);
    return Math.max(0, Math.round(30 - elapsed));
  };
  return html`
    <div style="margin-top:16px;">
      <div
        class="expand-toggle"
        style="display:flex;align-items:center;gap:6px;"
        @click=${() => host._toggleDeletedSection()}
      >
        <ha-icon
          icon="mdi:trash-can-outline"
          style="--mdc-icon-size:14px;opacity:0.6;"
        ></ha-icon>
        <span>Recently Deleted</span>
        <ha-icon
          icon="mdi:chevron-${host._showDeleted ? "up" : "down"}"
          style="--mdc-icon-size:14px;margin-left:auto;"
        ></ha-icon>
      </div>
      ${host._showDeleted
        ? html`
            <div style="margin-top:8px;">
              ${host._loadingDeleted
                ? html`<div style="opacity:0.5;font-size:12px;padding:8px 0;">
                    Loading…
                  </div>`
                : host._deletedAutomations.length === 0
                  ? html`<div
                      style="opacity:0.45;font-size:12px;padding:8px 0;"
                    >
                      No recently deleted automations.
                    </div>`
                  : host._deletedAutomations.map((a) => {
                      const automationId = a.automation_id || a.entity_id;
                      const days = daysRemaining(a.deleted_at);
                      const restoring = host._restoringAutomation[automationId];
                      const hardDeleting =
                        host._hardDeletingAutomation[automationId];
                      return html`
                        <div
                          class="card"
                          style="opacity:0.8;border-left:3px solid var(--error-color);"
                        >
                          <div class="card-header">
                            <h3 style="flex:1;">${a.alias}</h3>
                            ${days <= 3
                              ? html`<span
                                  style="font-size:10px;background:var(--error-color);color:#fff;border-radius:4px;padding:2px 6px;"
                                  >⚠ ${days}d left</span
                                >`
                              : html`<span style="font-size:11px;opacity:0.6;"
                                  >${days} days until purge</span
                                >`}
                          </div>
                          <p style="font-size:11px;opacity:0.6;margin:4px 0;">
                            Deleted ${relativeTime(new Date(a.deleted_at))}
                          </p>
                          <div class="card-actions">
                            <button
                              class="btn btn-outline"
                              ?disabled=${restoring || hardDeleting}
                              @click=${() =>
                                host._restoreDeletedAutomation(automationId)}
                            >
                              <ha-icon
                                icon="mdi:restore"
                                style="--mdc-icon-size:13px;"
                              ></ha-icon>
                              ${restoring ? "Restoring…" : "Restore"}
                            </button>
                            <button
                              class="btn btn-outline btn-danger"
                              ?disabled=${restoring || hardDeleting}
                              @click=${() =>
                                host._openHardDeleteDialog(
                                  automationId,
                                  a.alias,
                                )}
                            >
                              <ha-icon
                                icon="mdi:trash-can"
                                style="--mdc-icon-size:13px;"
                              ></ha-icon>
                              ${hardDeleting
                                ? "Deleting…"
                                : "Permanently Delete"}
                            </button>
                          </div>
                        </div>
                      `;
                    })}
            </div>
          `
        : ""}
    </div>
  `;
}

export function renderHardDeleteDialog(host) {
  if (!host._hardDeleteTarget) return "";

  const { automationId, alias } = host._hardDeleteTarget;
  const hardDeleting = !!host._hardDeletingAutomation[automationId];
  const canConfirm = host._hardDeleteAliasInput === alias;

  return html`
    <div
      style="position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:10000;display:flex;align-items:center;justify-content:center;"
      @click=${(e) => {
        if (e.target === e.currentTarget && !hardDeleting) {
          host._closeHardDeleteDialog();
        }
      }}
    >
      <div
        style="background:var(--card-background-color);border-radius:12px;width:90%;max-width:520px;padding:18px;box-shadow:0 8px 32px rgba(0,0,0,0.4);border:1px solid var(--divider-color);"
      >
        <div
          style="font-size:16px;font-weight:700;margin-bottom:8px;display:flex;align-items:center;gap:8px;color:var(--error-color);"
        >
          <ha-icon icon="mdi:alert-octagon"></ha-icon>
          Permanently Delete Automation
        </div>
        <p
          style="font-size:13px;opacity:0.85;margin:0 0 10px;line-height:1.45;"
        >
          This action cannot be undone. Type the automation alias to confirm
          permanent deletion.
        </p>
        <p style="font-size:12px;opacity:0.75;margin:0 0 8px;">
          Alias: <strong>${alias}</strong>
        </p>
        <ha-textfield
          .value=${host._hardDeleteAliasInput}
          @input=${(e) => (host._hardDeleteAliasInput = e.target.value)}
          placeholder="Type alias exactly"
          ?disabled=${hardDeleting}
          style="width:100%;"
        ></ha-textfield>
        <div
          style="display:flex;justify-content:flex-end;gap:10px;margin-top:14px;"
        >
          <button
            class="btn btn-outline"
            ?disabled=${hardDeleting}
            @click=${() => host._closeHardDeleteDialog()}
          >
            Cancel
          </button>
          <button
            class="btn btn-danger"
            ?disabled=${hardDeleting || !canConfirm}
            @click=${() => host._confirmHardDelete()}
          >
            ${hardDeleting ? "Deleting…" : "Permanently Delete"}
          </button>
        </div>
      </div>
    </div>
  `;
}
