import { html } from "lit";
import { formatTimeAgo } from "../shared/date-utils.js";
import { renderAutomationFlowchart } from "./render-automations.js";

// ---------------------------------------------------------------------------
// Stale automations helpers
// ---------------------------------------------------------------------------

const STALE_KEPT_KEY = "selora_stale_kept";

function _staleDays(host) {
  return host._config?.stale_days || 5;
}

function _staleMs(host) {
  return _staleDays(host) * 24 * 60 * 60 * 1000;
}

function _loadKept() {
  try {
    return JSON.parse(localStorage.getItem(STALE_KEPT_KEY) || "{}");
  } catch {
    return {};
  }
}

function _saveKept(kept) {
  localStorage.setItem(STALE_KEPT_KEY, JSON.stringify(kept));
}

function keepAutomation(host, automationId) {
  const kept = _loadKept();
  kept[automationId] = Date.now();
  _saveKept(kept);
  host.requestUpdate();
}

export function getStaleAutomations(host) {
  if (!host._automations?.length) return [];
  const now = Date.now();
  const staleMs = _staleMs(host);
  const cutoff = now - staleMs;
  const kept = _loadKept();

  // Clean up expired kept entries
  let dirty = false;
  for (const [id, ts] of Object.entries(kept)) {
    if (now - ts > staleMs) {
      delete kept[id];
      dirty = true;
    }
  }
  if (dirty) _saveKept(kept);

  return host._automations.filter((a) => {
    if (!host._automationIsEnabled(a)) return false;
    if (!a.automation_id?.startsWith("selora_ai_")) return false;
    // Skip if user chose to keep this automation (re-checks after STALE_DAYS)
    if (kept[a.automation_id]) return false;
    if (!a.last_triggered) {
      // Grace period: don't flag never-triggered automations that were
      // recently created (use last_updated as a proxy for creation time,
      // matches backend find_stale_automations which uses state.last_updated)
      if (a.last_updated) {
        const created = new Date(a.last_updated).getTime();
        if (created >= cutoff) return false;
      }
      return true;
    }
    return new Date(a.last_triggered).getTime() < cutoff;
  });
}

export function renderStaleModal(host) {
  if (!host._staleModalOpen) return "";
  const stale = getStaleAutomations(host);
  if (!stale.length) {
    host._staleModalOpen = false;
    return "";
  }

  const staleDays = _staleDays(host);
  const selected = host._staleSelected || {};
  const selectedCount = stale.filter((a) => selected[a.automation_id]).length;
  const allSelected = selectedCount === stale.length;
  const someSelected = selectedCount > 0 && !allSelected;

  return html`
    <div
      class="modal-overlay"
      @click=${() => {
        host._staleModalOpen = false;
        host._staleSelected = {};
      }}
    >
      <div
        class="modal-content"
        style="max-width:560px;max-height:80vh;display:flex;flex-direction:column;border:1px solid var(--selora-accent);"
        @click=${(e) => e.stopPropagation()}
      >
        <h3 class="modal-title" style="flex-shrink:0;">
          <ha-icon
            icon="mdi:clock-alert-outline"
            style="--mdc-icon-size:22px;color:#f59e0b;vertical-align:middle;margin-right:6px;"
          ></ha-icon>
          Stale Automations
          <span
            style="font-size:13px;font-weight:400;color:var(--secondary-text-color);margin-left:8px;"
            >${stale.length} automation${stale.length !== 1 ? "s" : ""}</span
          >
        </h3>
        <p
          style="font-size:14px;line-height:1.6;margin:0 0 4px;color:var(--primary-text-color);flex-shrink:0;"
        >
          The following Selora automations haven't triggered in ${staleDays}
          day${staleDays !== 1 ? "s" : ""}. You can remove ones you no longer
          need to free up space for new suggestions.
        </p>

        <!-- Select all + bulk actions -->
        <div
          style="display:flex;align-items:center;justify-content:space-between;margin:12px 0 4px;padding:0 2px;flex-shrink:0;"
        >
          <label
            style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--secondary-text-color);cursor:pointer;user-select:none;"
          >
            <input
              type="checkbox"
              .checked=${allSelected}
              .indeterminate=${someSelected}
              @change=${(e) => {
                const next = {};
                if (e.target.checked) {
                  stale.forEach((a) => {
                    next[a.automation_id] = true;
                  });
                }
                host._staleSelected = next;
              }}
            />
            Select all
          </label>
          ${selectedCount > 0
            ? html`<button
                class="modal-btn modal-cancel"
                style="font-size:11px;padding:4px 10px;color:#ef4444;border-color:#ef4444;"
                ?disabled=${host._staleBulkDeleting}
                @click=${async () => {
                  const toDelete = stale.filter(
                    (a) => selected[a.automation_id],
                  );
                  if (
                    !confirm(
                      `Remove ${toDelete.length} automation${toDelete.length !== 1 ? "s" : ""} permanently?`,
                    )
                  )
                    return;
                  host._staleBulkDeleting = true;
                  for (const a of toDelete) {
                    try {
                      await host.hass.callWS({
                        type: "selora_ai/delete_automation",
                        automation_id: a.automation_id,
                      });
                    } catch (err) {
                      console.error("Failed to delete", a.alias, err);
                    }
                  }
                  await host._loadAutomations();
                  host._staleSelected = {};
                  host._staleBulkDeleting = false;
                  host._showToast(
                    `Removed ${toDelete.length} automation${toDelete.length !== 1 ? "s" : ""}.`,
                    "success",
                  );
                  host.requestUpdate();
                }}
              >
                <ha-icon
                  icon="mdi:trash-can-outline"
                  style="--mdc-icon-size:13px;"
                ></ha-icon>
                Remove ${selectedCount} selected
              </button>`
            : ""}
        </div>

        <!-- Scrollable list -->
        <div
          style="flex:1;min-height:0;overflow-y:auto;border:1px solid var(--divider-color);border-radius:8px;"
        >
          ${stale.map(
            (a) => html`
              <div
                style="display:flex;align-items:center;padding:10px 14px;border-bottom:1px solid var(--divider-color);gap:10px;"
              >
                <input
                  type="checkbox"
                  .checked=${!!selected[a.automation_id]}
                  @change=${(e) => {
                    const next = { ...host._staleSelected };
                    if (e.target.checked) {
                      next[a.automation_id] = true;
                    } else {
                      delete next[a.automation_id];
                    }
                    host._staleSelected = next;
                  }}
                  @click=${(e) => e.stopPropagation()}
                  style="flex-shrink:0;cursor:pointer;"
                />
                <div
                  style="flex:1;min-width:0;cursor:pointer;"
                  @click=${() => {
                    host._staleDetailAuto = a;
                  }}
                >
                  <div
                    style="font-size:13px;font-weight:600;color:var(--primary-text-color);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"
                  >
                    ${a.alias || a.entity_id}
                  </div>
                  <div
                    style="font-size:11px;color:var(--secondary-text-color);margin-top:2px;"
                  >
                    Last triggered:
                    ${a.last_triggered
                      ? formatTimeAgo(a.last_triggered)
                      : "Never"}
                  </div>
                </div>
                <button
                  class="modal-btn modal-cancel"
                  style="flex-shrink:0;font-size:11px;padding:4px 10px;color:var(--selora-accent);border-color:var(--selora-accent);"
                  @click=${(e) => {
                    e.stopPropagation();
                    keepAutomation(host, a.automation_id);
                    host._showToast(
                      `"${a.alias || "Automation"}" kept for ${staleDays} days.`,
                      "info",
                    );
                  }}
                >
                  <ha-icon
                    icon="mdi:check"
                    style="--mdc-icon-size:13px;"
                  ></ha-icon>
                  Keep
                </button>
                <ha-icon
                  icon="mdi:chevron-right"
                  style="--mdc-icon-size:18px;color:var(--secondary-text-color);flex-shrink:0;cursor:pointer;"
                  @click=${() => {
                    host._staleDetailAuto = a;
                  }}
                ></ha-icon>
              </div>
            `,
          )}
        </div>
        <div
          class="modal-actions"
          style="justify-content:center;gap:12px;margin-top:16px;flex-shrink:0;"
        >
          <button
            class="modal-btn modal-cancel"
            @click=${() => {
              host._staleModalOpen = false;
              host._staleSelected = {};
            }}
          >
            Close
          </button>
        </div>
      </div>
    </div>
    ${_renderStaleDetailModal(host)}
  `;
}

function _renderStaleDetailModal(host) {
  const a = host._staleDetailAuto;
  if (!a) return "";
  const staleDays = _staleDays(host);
  return html`
    <div
      class="modal-overlay"
      style="z-index:10002;"
      @click=${() => {
        host._staleDetailAuto = null;
      }}
    >
      <div
        class="modal-content"
        style="max-width:520px;border:1px solid var(--selora-accent);"
        @click=${(e) => e.stopPropagation()}
      >
        <h3 class="modal-title">
          <ha-icon
            icon="mdi:robot"
            style="--mdc-icon-size:22px;color:var(--selora-accent);vertical-align:middle;margin-right:6px;"
          ></ha-icon>
          ${a.alias || a.entity_id}
        </h3>
        <div
          style="font-size:12px;color:var(--secondary-text-color);margin-bottom:12px;"
        >
          Last triggered:
          ${a.last_triggered ? formatTimeAgo(a.last_triggered) : "Never"} ·
          State: ${a.state || "unknown"}
        </div>

        ${a.description
          ? html`<p
              style="font-size:13px;margin:0 0 12px;color:var(--primary-text-color);"
            >
              ${a.description}
            </p>`
          : ""}
        ${renderAutomationFlowchart(host, a)}

        <div
          class="modal-actions"
          style="justify-content:center;gap:12px;margin-top:16px;"
        >
          <button
            class="modal-btn modal-cancel"
            @click=${() => {
              host._staleDetailAuto = null;
            }}
          >
            Back
          </button>
          <button
            class="modal-btn modal-cancel"
            style="color:var(--selora-accent);border-color:var(--selora-accent);"
            @click=${() => {
              keepAutomation(host, a.automation_id);
              host._staleDetailAuto = null;
              host._showToast(
                `"${a.alias || "Automation"}" kept for ${staleDays} days.`,
                "info",
              );
            }}
          >
            <ha-icon icon="mdi:check" style="--mdc-icon-size:14px;"></ha-icon>
            Keep
          </button>
          <button
            class="modal-btn modal-cancel"
            style="color:#ef4444;border-color:#ef4444;"
            @click=${async () => {
              if (!a.automation_id) return;
              if (!confirm("Remove this automation permanently?")) return;
              try {
                await host.hass.callWS({
                  type: "selora_ai/delete_automation",
                  automation_id: a.automation_id,
                });
                await host._loadAutomations();
                host._showToast("Automation removed.", "success");
              } catch (err) {
                host._showToast("Failed to remove: " + err.message, "error");
              }
              host._staleDetailAuto = null;
              host.requestUpdate();
            }}
          >
            <ha-icon
              icon="mdi:trash-can-outline"
              style="--mdc-icon-size:14px;"
            ></ha-icon>
            Remove
          </button>
        </div>
      </div>
    </div>
  `;
}
