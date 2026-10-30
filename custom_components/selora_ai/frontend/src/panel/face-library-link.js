// The way into Frigate's Face Library, under a reply about face recognition
// (`get_face_recognition`, backend: frigate/faces.py). People are added there,
// in Frigate's own UI; the panel only opens the page. Drawn from the reply's
// stored tool calls, so a reopened conversation keeps it.
import { html } from "lit";
import { frigateApp, openFrigatePage } from "./frigate-ui.js";

export const FACES_PAGE = "faces";

export function renderFaceLibraryLink(host, msg) {
  const shown = (msg.tool_calls || []).some(
    (call) =>
      call?.tool === "get_face_recognition" && call.result?.face_library,
  );
  if (!shown || !frigateApp(host.hass)) return "";
  return html`<div class="face-library camera-event-links">
    <button
      class="camera-event-link"
      @click=${() => openFrigatePage(host, FACES_PAGE)}
    >
      <ha-icon icon="mdi:face-recognition"></ha-icon>
      <span
        >${host._t("face_library_open", "Open the Face Library in Frigate")}</span
      >
    </button>
  </div>`;
}
