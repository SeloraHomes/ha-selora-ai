import { html } from "lit";

// Confirmation for deleting a long-offline device from the Health tab. Opened
// by _promptDeleteDevice (insights-actions.js) off the offline finding's Delete
// button, which the backend only marks `removable` once the device has been
// down past HEALTH_OFFLINE_REMOVABLE_SECS.
//
// The copy has to carry the blast radius: deletion drops the device AND its
// entities, so anything referencing them (automations, scenes, dashboard cards)
// breaks, and only a rediscovery by the owning integration brings it back.

// Coarse duration for the prompt — days once there's at least one, hours below
// that. Deliberately not relativeTime(): this reads as a span ("offline for 9
// days"), not a point in the past ("9 days ago").
function _offlineFor(host, seconds) {
  const secs = Number(seconds) || 0;
  const days = Math.floor(secs / 86400);
  if (days >= 1) {
    return days === 1
      ? host._t("insights_offline_one_day", "1 day")
      : `${days} ${host._t("insights_offline_days", "days")}`;
  }
  const hours = Math.max(1, Math.floor(secs / 3600));
  return hours === 1
    ? host._t("insights_offline_one_hour", "1 hour")
    : `${hours} ${host._t("insights_offline_hours", "hours")}`;
}

export function renderDeleteDeviceModal(host) {
  const target = host._deleteDeviceTarget;
  if (!target) return "";
  const name =
    target.name || host._t("insights_delete_device_fallback", "this device");
  const close = () => {
    if (host._deletingDevice) return;
    host._deleteDeviceTarget = null;
  };
  return html`
    <div
      class="modal-overlay"
      @click=${(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div class="modal-content" style="max-width:440px;text-align:center;">
        <div style="font-size:17px;font-weight:600;margin-bottom:8px;">
          ${host._t("insights_delete_device_title", "Delete device")}
        </div>
        <div style="font-size:13px;opacity:0.7;margin-bottom:20px;">
          <strong>${name}</strong>
          ${host._t("insights_delete_device_offline", "has been offline for")}
          ${_offlineFor(host, target.offline_seconds)}.
          ${host._t(
            "insights_delete_device_body",
            "Deleting removes it and its entities from Home Assistant — " +
              "automations, scenes, and dashboard cards that use them will stop " +
              "working. It only comes back if its integration rediscovers it.",
          )}
        </div>
        <div style="display:flex;gap:10px;justify-content:center;">
          <button
            class="btn btn-outline"
            ?disabled=${host._deletingDevice}
            @click=${close}
          >
            ${host._t("insights_delete_device_cancel", "Cancel")}
          </button>
          <button
            class="btn"
            style="background:#ef4444;color:#fff;border-color:#ef4444;"
            ?disabled=${host._deletingDevice}
            @click=${() => host._confirmDeleteDevice()}
          >
            ${
              host._deletingDevice
                ? html`<span class="spinner"></span>`
                : html`<ha-icon icon="mdi:trash-can-outline"></ha-icon>`
            }
            ${host._t("insights_delete_device_confirm", "Delete")}
          </button>
        </div>
      </div>
    </div>
  `;
}
