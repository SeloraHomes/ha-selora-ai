// What an `add_integration` turn added, as a card under the reply: the tick a
// Frigate camera gets, the new entry's name and integration, and its entities
// as live tiles, so the device added can be used right there. The name opens
// the device in Home Assistant, or the entry's page when it has none (a media
// server) or several (a bridge). The model's
// sentence alone does not say whether the setup is done or still waiting on
// the user.
//
// Rendered from the reply's stored tool calls, so a reopened conversation
// shows it too. An entry Home Assistant could not start says so instead of
// drawing the tick.
import { html } from "lit";

import { renderCreatedCheck } from "../shared/created-check.js";
import { interpolate } from "../shared/i18n.js";

// A card this fresh draws its tick; reopening the session later does not.
const FRESH_MS = 60 * 1000;

function addedOf(msg) {
  return (msg.tool_calls || [])
    .filter(
      (call) =>
        call?.tool === "add_integration" && call.result?.status === "added",
    )
    .map((call) => call.result);
}

// Only tiles Home Assistant has a state for: an entity is registered before
// its platform first writes one.
function tiles(host, entry) {
  return (entry.entity_ids || []).filter((id) => host.hass?.states?.[id]);
}

// Home Assistant's own pages, opened in place as its UI does.
function open(path) {
  window.history.pushState(null, "", path);
  window.dispatchEvent(new Event("location-changed"));
}

function pageOf(entry) {
  if (entry.device_id) {
    return `/config/devices/device/${encodeURIComponent(entry.device_id)}`;
  }
  const domain = encodeURIComponent(entry.domain || "");
  return entry.entry_id
    ? `/config/integrations/integration/${domain}#config_entry=${encodeURIComponent(entry.entry_id)}`
    : "";
}

function renderName(entry) {
  const name = entry.title || entry.name || entry.domain;
  const page = pageOf(entry);
  if (!page) {
    return html`<ha-icon icon="mdi:puzzle-outline"></ha-icon
      ><span>${name}</span>`;
  }
  return html`<button
    type="button"
    class="flow-entity-link"
    title=${name}
    @click=${(e) => {
      e.stopPropagation();
      open(page);
    }}
  >
    <ha-icon
      icon=${entry.device_id ? "mdi:devices" : "mdi:puzzle-outline"}
    ></ha-icon
    ><span>${name}</span>
  </button>`;
}

function renderEntry(host, entry) {
  const working = entry.state === "loaded";
  const ids = working ? tiles(host, entry) : [];
  const counts = interpolate(
    host._t(
      "integration_added_counts",
      "Devices: {devices} · Entities: {entities}",
    ),
    { devices: entry.devices ?? 0, entities: entry.entities ?? 0 },
  );
  return html`
    <div class="frigate-result-camera">
      <div class="frigate-result-name">${renderName(entry)}</div>
      <div class="frigate-result-wait">
        <span
          >${entry.name || entry.domain}${
            working && !ids.length ? html` · ${counts}` : ""
          }</span
        >
      </div>
      ${
        ids.length
          ? html`<div class="frigate-result-live">
              <div
                class="selora-entity-grid"
                data-entity-ids=${ids.join(",")}
              ></div>
            </div>`
          : ""
      }
      ${
        working
          ? ""
          : html`<div class="frigate-result-wait">
              <ha-icon icon="mdi:alert-outline"></ha-icon>
              <span
                >${
                  entry.reason ||
                  host._t(
                    "integration_added_not_running",
                    "Added, but Home Assistant has not started it yet.",
                  )
                }</span
              >
            </div>`
      }
    </div>
  `;
}

/** The card, or "" for a reply that added nothing. */
export function renderIntegrationAddedCard(host, msg) {
  const added = addedOf(msg);
  if (!added.length) return "";
  const at = Date.parse(msg.timestamp || "");
  const fresh = !Number.isNaN(at) && Date.now() - at < FRESH_MS;
  const working = added.every((entry) => entry.state === "loaded");
  return html`
    <div class="frigate-result${fresh ? " pop-in" : ""}">
      <div class="scene-saved-head">
        <span class="scene-saved-icon"
          >${
            working
              ? renderCreatedCheck({ animate: fresh, size: 20 })
              : html`<ha-icon icon="mdi:alert-circle-outline"></ha-icon>`
          }</span
        >
        <span class="scene-saved-tag"
          >${host._t("integration_added_tag", "Added to Home Assistant")}</span
        >
      </div>
      ${added.map((entry) => renderEntry(host, entry))}
    </div>
  `;
}
