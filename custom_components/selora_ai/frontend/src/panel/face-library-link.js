// The way into Frigate's Face Library, under a reply about face recognition
// (`get_face_recognition`, backend: frigate/faces.py) and on the guide's people
// card. People are added on that card; the library is where Frigate lists the
// faces it saw, to assign to the right person. The button opens Frigate inside
// HA, a tap away from its Face Library: HA's app panel cannot land on a page.
// Drawn from the reply's stored tool calls, so a reopened conversation keeps it.
import { html } from "lit";
import { frigateApp, openFrigateInHA } from "./frigate-ui.js";

/** Whether a reply read face recognition as on, with a library to open. */
export function faceLibraryShown(msg) {
  return (msg.tool_calls || []).some(
    (call) =>
      call?.tool === "get_face_recognition" && call.result?.face_library,
  );
}

export function renderFaceLibraryButton(host) {
  if (!frigateApp(host.hass)) return "";
  return html`<div class="face-library camera-event-links">
    <button class="camera-event-link" @click=${() => openFrigateInHA(host)}>
      <ha-icon icon="mdi:face-recognition"></ha-icon>
      <span
        >${host._t("camera_events_open_in_frigate", "Open in Frigate")}</span
      >
    </button>
  </div>`;
}

export function renderFaceLibraryLink(host, msg) {
  return faceLibraryShown(msg) ? renderFaceLibraryButton(host) : "";
}
