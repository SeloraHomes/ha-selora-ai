// What an `add_integration` turn added, as a card under the reply: the tick a
// Frigate camera gets, the new entry's name, its integration and what it
// brought in. The model's sentence alone left the user unsure whether the
// setup was done or still waiting on them.
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

function renderEntry(host, entry) {
  const working = entry.state === "loaded";
  const counts = interpolate(
    host._t(
      "integration_added_counts",
      "Devices: {devices} · Entities: {entities}",
    ),
    { devices: entry.devices ?? 0, entities: entry.entities ?? 0 },
  );
  return html`
    <div class="frigate-result-camera">
      <div class="frigate-result-name">
        <ha-icon icon="mdi:puzzle-outline"></ha-icon>
        <span>${entry.title || entry.name || entry.domain}</span>
      </div>
      <div class="frigate-result-wait">
        <span
          >${entry.name || entry.domain}${working ? html` · ${counts}` : ""}</span
        >
      </div>
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
