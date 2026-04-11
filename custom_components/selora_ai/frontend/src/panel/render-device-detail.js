import { html } from "lit";

// ── Helpers ──────────────────────────────────────────────────────────

function _formatTimestamp(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function _stateColor(state) {
  if (!state) return "var(--selora-zinc-400)";
  const s = String(state).toLowerCase();
  if (["on", "open", "home", "playing", "active"].includes(s))
    return "var(--selora-accent, #fbbf24)";
  if (["off", "closed", "not_home", "idle", "standby"].includes(s))
    return "var(--selora-zinc-400)";
  if (["unavailable", "unknown"].includes(s)) return "#ef4444";
  return "var(--selora-zinc-200)";
}

function _deviceIcon(domains) {
  if (!domains || !domains.length) return "mdi:devices";
  const d = domains[0];
  const map = {
    light: "mdi:lightbulb",
    switch: "mdi:toggle-switch",
    sensor: "mdi:eye",
    binary_sensor: "mdi:motion-sensor",
    climate: "mdi:thermostat",
    cover: "mdi:window-shutter",
    media_player: "mdi:speaker",
    camera: "mdi:cctv",
    lock: "mdi:lock",
    fan: "mdi:fan",
  };
  return map[d] || "mdi:devices";
}

// ── Device detail drawer ─────────────────────────────────────────────

export function renderDeviceDetail(host) {
  const detail = host._deviceDetail;
  if (!detail) return "";
  const loading = host._deviceDetailLoading;

  return html`
    <div
      class="device-detail-drawer"
      style="
      margin-top:12px;padding:14px;
      border:1px solid var(--selora-inner-card-border, var(--divider-color, #3f3f46));
      border-radius:12px;
      background:var(--selora-inner-card-bg, var(--primary-background-color, #18181b));
    "
    >
      ${loading
        ? html`<span style="font-size:13px;color:var(--selora-zinc-400);"
            >Loading device detail...</span
          >`
        : html`
            <!-- Header -->
            <div
              style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;"
            >
              <div style="display:flex;align-items:center;gap:8px;">
                <ha-icon
                  icon=${_deviceIcon(detail.entities?.map((e) => e.domain))}
                  style="--mdc-icon-size:22px;color:var(--selora-accent);"
                ></ha-icon>
                <div>
                  <div
                    style="font-weight:700;font-size:15px;color:var(--selora-zinc-200);"
                  >
                    ${detail.name}
                  </div>
                  <div style="font-size:12px;color:var(--selora-zinc-400);">
                    ${[detail.area, detail.manufacturer, detail.model]
                      .filter(Boolean)
                      .join(" · ")}
                    ${detail.integration
                      ? html` ·
                          <span style="opacity:0.7"
                            >${detail.integration}</span
                          >`
                      : ""}
                  </div>
                </div>
              </div>
              <button
                style="background:none;border:none;cursor:pointer;color:var(--selora-zinc-400);padding:4px;"
                @click=${() => {
                  host._deviceDetail = null;
                }}
                title="Close"
              >
                <ha-icon
                  icon="mdi:close"
                  style="--mdc-icon-size:18px;"
                ></ha-icon>
              </button>
            </div>

            <!-- Entities -->
            ${detail.entities?.length
              ? html`
                  <div style="margin-bottom:12px;">
                    <div
                      style="font-size:11px;font-weight:600;text-transform:uppercase;color:var(--selora-zinc-400);margin-bottom:6px;"
                    >
                      Entities
                    </div>
                    ${detail.entities.map(
                      (e) => html`
                        <div
                          style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid var(--selora-inner-card-border, var(--divider-color, #3f3f46));"
                        >
                          <span
                            style="font-size:12px;color:var(--selora-zinc-200);"
                            >${e.name || e.entity_id}</span
                          >
                          <span
                            style="font-size:12px;font-weight:600;color:${_stateColor(
                              e.state,
                            )};"
                            >${e.state}</span
                          >
                        </div>
                      `,
                    )}
                  </div>
                `
              : ""}

            <!-- State History -->
            ${detail.state_history?.length
              ? html`
                  <div style="margin-bottom:12px;">
                    <div
                      style="font-size:11px;font-weight:600;text-transform:uppercase;color:var(--selora-zinc-400);margin-bottom:6px;"
                    >
                      State History (24h)
                    </div>
                    <div style="max-height:150px;overflow-y:auto;">
                      ${detail.state_history.slice(0, 30).map(
                        (h) => html`
                          <div
                            style="display:flex;justify-content:space-between;padding:3px 0;font-size:11px;"
                          >
                            <span style="color:var(--selora-zinc-400);"
                              >${h.entity_id.split(".")[1]}</span
                            >
                            <span style="color:${_stateColor(h.state)};"
                              >${h.state}</span
                            >
                            <span style="color:var(--selora-zinc-400);"
                              >${_formatTimestamp(h.last_changed)}</span
                            >
                          </div>
                        `,
                      )}
                    </div>
                  </div>
                `
              : ""}

            <!-- Linked Automations -->
            ${detail.linked_automations?.length
              ? html`
                  <div style="margin-bottom:12px;">
                    <div
                      style="font-size:11px;font-weight:600;text-transform:uppercase;color:var(--selora-zinc-400);margin-bottom:6px;"
                    >
                      Linked Automations
                    </div>
                    ${detail.linked_automations.map(
                      (a) => html`
                        <div
                          style="padding:4px 0;border-bottom:1px solid var(--selora-inner-card-border, var(--divider-color, #3f3f46));"
                        >
                          <span
                            style="font-size:12px;color:var(--selora-zinc-200);"
                            >${a.alias || a.id}</span
                          >
                        </div>
                      `,
                    )}
                  </div>
                `
              : ""}

            <!-- Related Patterns -->
            ${detail.related_patterns?.length
              ? html`
                  <div>
                    <div
                      style="font-size:11px;font-weight:600;text-transform:uppercase;color:var(--selora-zinc-400);margin-bottom:6px;"
                    >
                      Detected Patterns
                    </div>
                    ${detail.related_patterns.map(
                      (p) => html`
                        <div
                          style="padding:4px 0;border-bottom:1px solid var(--selora-inner-card-border, var(--divider-color, #3f3f46));"
                        >
                          <div
                            style="font-size:12px;color:var(--selora-zinc-200);"
                          >
                            ${p.description}
                          </div>
                          <div
                            style="font-size:10px;color:var(--selora-zinc-400);margin-top:2px;"
                          >
                            ${p.type} · ${Math.round(p.confidence * 100)}%
                            confidence
                          </div>
                        </div>
                      `,
                    )}
                  </div>
                `
              : ""}
          `}
    </div>
  `;
}
