// The outcome of adding or removing Frigate cameras, as a card in place of the
// message text (`msg.frigate_result`, written by the backend from its own plan).
//
// An added camera exists in Home Assistant only once Frigate has restarted and
// its integration reloaded, about a minute later. The card waits on `hass` —
// the panel re-renders on every state change — so it turns from a spinner into
// the camera's snapshot and recording switch without polling anything.
import { html } from "lit";

import { renderCreatedCheck } from "../shared/created-check.js";

// After this long the wait is not a normal restart any more.
const SLOW_AFTER_MS = 3 * 60 * 1000;
// A result this fresh draws its tick; reopening the session later does not.
const FRESH_MS = 60 * 1000;

function age(msg) {
  const at = Date.parse(msg.timestamp || "");
  return Number.isNaN(at) ? Infinity : Date.now() - at;
}

// The Frigate integration's entity for an id the backend predicted. HA
// suffixes an id already taken ("camera.porch" belongs to the camera Frigate
// was given, so Frigate's becomes "camera.porch_2"), and the expected id could
// then be the original camera, not Frigate's. Only an entity of the frigate
// platform counts; without registry data the expected id is taken as is.
function frigateEntity(host, expectedId) {
  const entities = host.hass?.entities;
  if (!entities) return host.hass?.states?.[expectedId] ? expectedId : null;
  if (entities[expectedId]?.platform === "frigate") return expectedId;
  const prefix = `${expectedId}_`;
  return (
    Object.keys(entities).find(
      (id) =>
        id.startsWith(prefix) &&
        /^\d+$/.test(id.slice(prefix.length)) &&
        entities[id]?.platform === "frigate",
    ) || null
  );
}

function isLive(host, entityId) {
  const state = entityId ? host.hass?.states?.[entityId] : null;
  return !!state && state.state !== "unavailable";
}

function openMoreInfo(host, entityId) {
  host.dispatchEvent(
    new CustomEvent("hass-more-info", {
      detail: { entityId },
      bubbles: true,
      composed: true,
    }),
  );
}

// The camera's own controls and sensors, as live tiles: recording and
// detection switches, and an occupancy sensor per tracked object, which shows
// what is detected better than a sentence could. Only the ones the Frigate
// integration has created yet. HA names them after the camera, which the card
// already shows, and draws a house for every occupancy sensor, so each gets a
// short name and an icon that says what it is.
const OBJECT_ICONS = {
  person: "mdi:account",
  car: "mdi:car",
  dog: "mdi:dog",
  cat: "mdi:cat",
  bird: "mdi:bird",
  bicycle: "mdi:bicycle",
  motorcycle: "mdi:motorbike",
};

function capitalize(text) {
  return text.charAt(0).toUpperCase() + text.slice(1).replace(/_/g, " ");
}

function controlTiles(host, result, camera) {
  const name = camera.frigate_name;
  const tiles = [
    [
      camera.recordings_entity,
      "mdi:filmstrip-box",
      host._t("frigate_result_recording", "Recording"),
    ],
    [
      `switch.${name}_detect`,
      "mdi:motion-sensor",
      host._t("frigate_result_detection", "Detection"),
    ],
    ...(result.objects || []).map((object) => [
      `binary_sensor.${name}_${object}_occupancy`,
      OBJECT_ICONS[object] || "mdi:eye-outline",
      capitalize(object),
    ]),
  ]
    .map(([id, icon, label]) => [frigateEntity(host, id), icon, label])
    .filter(([id]) => id && host.hass?.states?.[id]);
  return {
    ids: tiles.map(([id]) => id),
    options: Object.fromEntries(
      tiles.map(([id, icon, label]) => [id, { icon, name: label }]),
    ),
  };
}

function renderWaiting(host, msg) {
  const slow = age(msg) > SLOW_AFTER_MS;
  return html`
    <div class="frigate-result-wait">
      <span class="spinner" style="width:14px;height:14px;"></span>
      <span
        >${
          slow
            ? host._t(
                "frigate_result_slow",
                "Frigate is taking longer than usual to come back.",
              )
            : host._t(
                "frigate_result_waiting",
                "Frigate is restarting; the camera appears here in about a minute.",
              )
        }</span
      >
    </div>
  `;
}

// The camera's own snapshot, signed by HA (`entity_picture`), refreshed at
// most every few seconds as state updates re-render the card. HA's live camera
// card is lazy-loaded and often not there in a custom panel; a plain image is.
const SNAPSHOT_EVERY_MS = 5000;

function snapshotUrl(host, entityId) {
  const picture = host.hass?.states?.[entityId]?.attributes?.entity_picture;
  if (!picture) return "";
  const bucket = Math.floor(Date.now() / SNAPSHOT_EVERY_MS);
  return `${picture}${picture.includes("?") ? "&" : "?"}t=${bucket}`;
}

function renderAdded(host, msg, result, camera) {
  const cameraId = frigateEntity(host, camera.camera_entity);
  if (!isLive(host, cameraId)) return renderWaiting(host, msg);
  const snapshot = snapshotUrl(host, cameraId);
  return html`
    <div class="frigate-result-live pop-in">
      ${
        snapshot
          ? html`<button
              class="frigate-result-preview"
              title=${host._t("frigate_result_open", "Open the camera")}
              @click=${() => openMoreInfo(host, cameraId)}
            >
              <img src=${snapshot} alt=${camera.name} loading="lazy" />
            </button>`
          : ""
      }
      ${(() => {
        const { ids, options } = controlTiles(host, result, camera);
        return ids.length
          ? html`<div
              class="selora-entity-grid"
              data-entity-ids=${ids.join(",")}
              data-tile-options=${JSON.stringify(options)}
            ></div>`
          : "";
      })()}
    </div>
  `;
}

function renderCamera(host, msg, result, camera) {
  const added = result.action === "added";
  const cameraId = frigateEntity(host, camera.camera_entity);
  const live = added && isLive(host, cameraId);
  return html`
    <div class="frigate-result-camera">
      <div class="frigate-result-name">
        <ha-icon icon="mdi:cctv"></ha-icon>
        ${
          live
            ? html`<a
                href="#"
                title=${host._t("frigate_result_open", "Open the camera")}
                @click=${(e) => {
                  e.preventDefault();
                  openMoreInfo(host, cameraId);
                }}
                >${camera.name}</a
              >`
            : html`<span>${camera.name}</span>`
        }
      </div>
      ${
        result.action === "saved"
          ? html`<div class="frigate-result-wait">
              <ha-icon icon="mdi:alert-outline"></ha-icon>
              <span
                >${host._t(
                  "frigate_result_saved",
                  "Saved in Frigate; it starts with Frigate's next restart.",
                )}</span
              >
            </div>`
          : added
            ? renderAdded(host, msg, result, camera)
            : ""
      }
    </div>
  `;
}

const TAGS = {
  added: ["frigate_result_added", "Added to Frigate"],
  saved: ["frigate_result_saved_tag", "Saved in Frigate"],
  removed: ["frigate_result_removed", "Removed from Frigate"],
};

/** The result card, or "" for a message without one. */
export function renderFrigateResult(host, msg) {
  const result = msg.frigate_result;
  if (!result || !Array.isArray(result.cameras)) return "";
  const fresh = age(msg) < FRESH_MS;
  const [tagKey, tagFallback] = TAGS[result.action] || TAGS.added;
  return html`
    <div class="frigate-result${fresh ? " pop-in" : ""}">
      <div class="scene-saved-head">
        <span class="scene-saved-icon"
          >${
            result.action === "saved"
              ? html`<ha-icon icon="mdi:content-save-outline"></ha-icon>`
              : renderCreatedCheck({ animate: fresh, size: 20 })
          }</span
        >
        <span class="scene-saved-tag">${host._t(tagKey, tagFallback)}</span>
      </div>
      ${result.cameras.map((camera) => renderCamera(host, msg, result, camera))}
    </div>
  `;
}
