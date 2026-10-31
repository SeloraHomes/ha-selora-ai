// What the cameras saw (`find_camera_events`, backend: frigate/events.py), as
// snapshots under the reply. Rendered from the reply's stored tool calls, so a
// reopened conversation shows it too.
//
// The tool result holds ids, never media URLs. The images come from the
// Frigate integration's own views, the clips from Selora AI's (see clipPath);
// both need Home Assistant's login: each
// path is signed with `auth/sign_path` once and reused until shortly before it
// expires, since an <img> cannot send the panel's token.
import { html } from "lit";
import { interpolate } from "../shared/i18n.js";
import {
  eventPage,
  frigateApp,
  openFrigatePage,
  zonesPage,
} from "./frigate-ui.js";

const SIGN_FOR_S = 3600;
const RESIGN_AFTER_MS = 50 * 60 * 1000;
const RETRY_AFTER_MS = 30 * 1000;
const OBJECTS = {
  person: ["camera_events_person", "Person"],
  car: ["camera_events_car", "Car"],
  dog: ["camera_events_dog", "Dog"],
  cat: ["camera_events_cat", "Cat"],
  bird: ["camera_events_bird", "Bird"],
  package: ["camera_events_package", "Package"],
};

// path → {url, at, failed}
const SIGNED = new Map();
// "<message timestamp>|<event id>" of the event shown large, per message.
const OPEN = new Map();

// One group per search the reply made, each with what it left unlisted. An
// event a previous search already showed is not shown twice.
function searchesOf(msg) {
  const seen = new Set();
  const searches = [];
  for (const call of msg.tool_calls || []) {
    if (call?.tool !== "find_camera_events") continue;
    const listed = call.result?.events || [];
    const instance = call.result?.instance;
    const events = listed
      .filter((e) => e?.id && !seen.has(e.id))
      .map((e) => ({ ...e, frigate_instance: instance }));
    events.forEach((e) => seen.add(e.id));
    const more = (call.result?.total || 0) - listed.length;
    if (events.length || more > 0) searches.push({ events, more });
  }
  return searches;
}

// Mirrors _INSTANCE in frigate/events.py.
const INSTANCE = /^(?!\.{1,2}$)[^/\\?#%\s\x00-\x1f\x7f]{1,128}$/;

// The event's picture: its snapshot, or, on a camera with snapshots off, the
// thumbnail Frigate keeps for every event. The Frigate integration serves
// thumbnails only under its instance id (Frigate's MQTT client id), which the
// tool result carries; not through its notification views, which can be
// switched off. A reply stored without one keeps the placeholder.
function picturePath(event, height) {
  const id = encodeURIComponent(event.id);
  if (!event.has_snapshot) {
    const instance = event.frigate_instance;
    return typeof instance === "string" && INSTANCE.test(instance)
      ? `/api/frigate/${encodeURIComponent(instance)}/thumbnail/${id}`
      : null;
  }
  return height
    ? `/api/frigate/snapshot/${id}?h=${height}&crop=1`
    : `/api/frigate/snapshot/${id}`;
}

// The clip comes from Selora AI's own view (frigate/media.py), not the Frigate
// integration's: Frigate sends a clip chunked and ignores Range, and WebKit
// (Safari, the iOS and macOS apps) plays no video without ranges. The view
// keeps the clip on the hub and answers them.
function clipPath(event) {
  if (event.in_progress || !event.has_clip) return null;
  return `/api/selora_ai/frigate/events/${encodeURIComponent(event.id)}/clip.mp4`;
}

async function sign(host, path) {
  const entry = SIGNED.get(path) || {};
  SIGNED.set(path, { ...entry, at: Date.now(), pending: true });
  try {
    const result = await host.hass.callWS({
      type: "auth/sign_path",
      path,
      expires: SIGN_FOR_S,
    });
    SIGNED.set(path, { url: result?.path || null, at: Date.now() });
  } catch (err) {
    console.error("Failed to sign a Frigate media path", err);
    SIGNED.set(path, { ...entry, at: Date.now(), failed: true });
  }
  host.requestUpdate?.();
}

/** The signed URL for a path, or null while it is being signed. */
function signed(host, path) {
  const entry = SIGNED.get(path);
  const age = entry ? Date.now() - entry.at : Infinity;
  const stale = entry?.failed ? age > RETRY_AFTER_MS : age > RESIGN_AFTER_MS;
  if (!entry || (stale && !entry.pending)) sign(host, path);
  return entry?.url || null;
}

function formatTime(host, iso) {
  try {
    return new Date(iso).toLocaleString(
      host.hass?.locale?.language || host.hass?.language || undefined,
      { weekday: "short", hour: "numeric", minute: "2-digit" },
    );
  } catch {
    return iso || "";
  }
}

function objectName(host, label) {
  const known = OBJECTS[label];
  if (known) return host._t(known[0], known[1]);
  const text = String(label || "").replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function caption(host, event) {
  const what = event.sub_label
    ? `${objectName(host, event.label)} · ${event.sub_label}`
    : objectName(host, event.label);
  return `${what} · ${event.camera_name || event.camera}`;
}

function renderOpen(host, event, key) {
  const picture = picturePath(event, 0);
  const snapshot = picture ? signed(host, picture) : null;
  const clip = clipPath(event) ? signed(host, clipPath(event)) : null;
  return html`
    <div class="camera-event-open">
      ${
        clip
          ? html`<video
              controls
              autoplay
              playsinline
              preload="metadata"
              poster=${snapshot || ""}
              src=${clip}
            ></video>`
          : snapshot
            ? html`<img src=${snapshot} alt=${caption(host, event)} />`
            : ""
      }
      <div class="camera-event-open-meta">
        <span class="camera-event-open-caption">
          <span class="camera-event-open-what">${caption(host, event)}</span>
          <span class="camera-event-open-when"
            >${formatTime(host, event.start)}</span
          >
        </span>
        ${renderFrigateLinks(host, event)}
        <button
          class="camera-event-close"
          title=${host._t("camera_events_close", "Close")}
          aria-label=${host._t("camera_events_close", "Close")}
          @click=${() => {
            OPEN.delete(key);
            host.requestUpdate?.();
          }}
        >
          <ha-icon icon="mdi:close"></ha-icon>
        </button>
      </div>
    </div>
  `;
}

// Frigate's own pages for this event: the event, and the camera's zones,
// where a parked car or a busy pavement is kept out of what counts.
function renderFrigateLinks(host, event) {
  if (!frigateApp(host.hass)) return "";
  const link = (page, icon, label) =>
    html`<button
      class="camera-event-link"
      @click=${() => openFrigatePage(host, page)}
    >
      <ha-icon icon=${icon}></ha-icon>
      <span>${label}</span>
    </button>`;
  return html`<span class="camera-event-links">
    ${link(
      eventPage(event.id),
      "mdi:open-in-new",
      host._t("camera_events_open_in_frigate", "Open in Frigate"),
    )}
    ${
      event.camera
        ? link(
            zonesPage(event.camera),
            "mdi:vector-square",
            host._t("camera_events_zones", "Zones"),
          )
        : ""
    }
  </span>`;
}

function renderTile(host, event, key) {
  const picture = picturePath(event, 240);
  const thumb = picture ? signed(host, picture) : null;
  return html`
    <button
      class="camera-event"
      title=${caption(host, event)}
      @click=${() => {
        OPEN.set(key, event.id);
        host.requestUpdate?.();
      }}
    >
      <div class="camera-event-thumb">
        ${
          thumb
            ? html`<img src=${thumb} alt="" loading="lazy" />`
            : html`<ha-icon icon="mdi:cctv"></ha-icon>`
        }
        ${
          clipPath(event)
            ? html`<ha-icon
                class="camera-event-play"
                icon="mdi:play-circle"
              ></ha-icon>`
            : ""
        }
      </div>
      <span class="camera-event-what">${caption(host, event)}</span>
      <span class="camera-event-when"
        >${
          event.in_progress
            ? host._t("camera_events_now", "Still going")
            : formatTime(host, event.start)
        }</span
      >
    </button>
  `;
}

function renderMore(host, more) {
  return more > 0
    ? html`<div class="camera-events-more">
        ${interpolate(
          host._t("camera_events_more", "{count} more in this time range."),
          { count: more },
        )}
      </div>`
    : "";
}

/** The events card for a reply that looked up camera events, or "". */
export function renderCameraEventsCard(host, msg) {
  const searches = searchesOf(msg);
  if (!searches.length) return "";
  const key = msg.timestamp || "";
  const openId = OPEN.get(key);
  const open = openId
    ? searches.flatMap((s) => s.events).find((e) => e.id === openId)
    : null;
  return html`
    <div class="camera-events">
      ${open ? renderOpen(host, open, key) : ""}
      ${searches.map(
        ({ events, more }) => html`
          ${
            events.length
              ? html`<div class="camera-events-grid">
                  ${events.map((e) => renderTile(host, e, key))}
                </div>`
              : ""
          }
          ${renderMore(host, more)}
        `,
      )}
    </div>
  `;
}
