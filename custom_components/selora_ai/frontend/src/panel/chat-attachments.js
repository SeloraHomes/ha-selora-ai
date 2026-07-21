import { html } from "lit";

// Image attachments for the chat composer (dropped or pasted screenshots).
// Gated on host._config.supports_vision — the backend reports whether the
// active provider/model can analyze images (selora_ai/get_config), and the
// chat handlers enforce the same gate server-side.
//
// Caps mirror the backend (const.py CHAT_ATTACHMENT_*): images are
// downscaled client-side to MAX_EDGE_PX and re-encoded as JPEG so a 4K
// screenshot doesn't push megabytes of base64 through the websocket.
export const MAX_CHAT_ATTACHMENTS = 4;
// Byte budgets mirror const.py CHAT_ATTACHMENT_MAX_(TOTAL_)B64_BYTES. HA's
// websocket closes the whole CONNECTION on frames past aiohttp's 4 MiB
// default — the backend never even sees the request — so the panel must
// keep the combined base64 payload under budget before sending.
const MAX_B64_PER_IMAGE = 2 * 1024 * 1024;
const MAX_B64_TOTAL = 3 * 1024 * 1024;
const MAX_EDGE_PX = 1568;
const JPEG_QUALITY = 0.85;
// Below this size an un-resized original is kept as-is — re-encoding a
// small PNG to JPEG only loses text crispness without saving anything.
const KEEP_ORIGINAL_MAX_BYTES = 300 * 1024;
const ACCEPTED_MIME = new Set([
  "image/jpeg",
  "image/png",
  "image/webp",
  "image/gif",
]);

export function supportsImageAttachments(host) {
  return !!host._config?.supports_vision;
}

function _b64Length(attachment) {
  return Math.max(
    0,
    attachment.dataUrl.length - attachment.dataUrl.indexOf(",") - 1,
  );
}

function _readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function _loadImage(dataUrl) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("decode failed"));
    img.src = dataUrl;
  });
}

// Downscale to MAX_EDGE_PX and re-encode as JPEG. Small originals that
// need no resize are kept verbatim (better text fidelity for screenshots).
async function _processImageFile(file) {
  const originalDataUrl = await _readAsDataUrl(file);
  const img = await _loadImage(originalDataUrl);
  const maxEdge = Math.max(img.naturalWidth, img.naturalHeight);
  if (maxEdge <= MAX_EDGE_PX && file.size <= KEEP_ORIGINAL_MAX_BYTES) {
    return { name: file.name, mimeType: file.type, dataUrl: originalDataUrl };
  }
  const scale = Math.min(1, MAX_EDGE_PX / maxEdge);
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
  canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
  const ctx = canvas.getContext("2d");
  // JPEG has no alpha — flatten transparency onto white instead of black.
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  return {
    name: file.name,
    mimeType: "image/jpeg",
    dataUrl: canvas.toDataURL("image/jpeg", JPEG_QUALITY),
  };
}

// Add dropped/pasted files to the pending strip. Non-image files are
// ignored silently (a text drop still means "insert text"); capability
// and cap violations surface as a transient notice under the composer.
export async function addImageAttachments(host, files) {
  const images = Array.from(files || []).filter((f) =>
    ACCEPTED_MIME.has(f.type),
  );
  if (!images.length) return;
  if (!supportsImageAttachments(host)) {
    host._attachmentNotice = host._t(
      "chat_attachment_unsupported",
      "Your current AI model can't analyze images.",
    );
    return;
  }
  // Room accounts for slots RESERVED by batches still decoding — two
  // overlapping calls (paste during a drop) would otherwise both measure
  // room against the same unchanged list and together overshoot the cap
  // the backend enforces.
  const reserved = host._attachmentSlotsReserved || 0;
  const room =
    MAX_CHAT_ATTACHMENTS - (host._chatAttachments || []).length - reserved;
  if (room <= 0) {
    host._attachmentNotice = host._t(
      "chat_attachment_limit",
      "You can attach up to 4 images per message.",
    );
    return;
  }
  const accepted = images.slice(0, room);
  const results = [];
  // Decode/resize can take noticeable time on a large image while the
  // composer stays enabled. The counter blocks Send until every in-flight
  // batch lands, so a mid-processing click can't snapshot an empty
  // attachment list and strand the image on the NEXT turn. A counter (not
  // a flag) because a paste can overlap a drop.
  host._attachmentsBusy = (host._attachmentsBusy || 0) + 1;
  host._attachmentSlotsReserved = reserved + accepted.length;
  // Running byte budget: already-attached images plus this batch. Even a
  // downscaled image is refused when it would push the websocket frame
  // past HA's 4 MiB cap (see MAX_B64_TOTAL above).
  let totalB64 = (host._chatAttachments || []).reduce(
    (sum, a) => sum + _b64Length(a),
    0,
  );
  try {
    for (const file of accepted) {
      try {
        const processed = await _processImageFile(file);
        const size = _b64Length(processed);
        if (size > MAX_B64_PER_IMAGE || totalB64 + size > MAX_B64_TOTAL) {
          host._attachmentNotice = host._t(
            "chat_attachment_too_large",
            "That image is too large to send.",
          );
          continue;
        }
        totalB64 += size;
        results.push(processed);
      } catch (_) {
        host._attachmentNotice = host._t(
          "chat_attachment_read_error",
          "Couldn't read that image.",
        );
      }
    }
  } finally {
    host._attachmentsBusy = Math.max(0, (host._attachmentsBusy || 1) - 1);
    host._attachmentSlotsReserved = Math.max(
      0,
      (host._attachmentSlotsReserved || accepted.length) - accepted.length,
    );
  }
  if (!results.length) return;
  // Belt over the reservation: never merge past the cap.
  host._chatAttachments = [...(host._chatAttachments || []), ...results].slice(
    0,
    MAX_CHAT_ATTACHMENTS,
  );
  host._attachmentNotice =
    images.length > room
      ? host._t(
          "chat_attachment_limit",
          "You can attach up to 4 images per message.",
        )
      : "";
}

export function removeChatAttachment(host, idx) {
  host._chatAttachments = (host._chatAttachments || []).filter(
    (_, i) => i !== idx,
  );
  host._attachmentNotice = "";
}

// Wire shape for selora_ai/chat_stream: [{mime_type, data}] with the
// data: URL prefix stripped (the backend re-wraps per provider format).
export function attachmentsForSend(host) {
  return (host._chatAttachments || [])
    .map((a) => {
      const comma = a.dataUrl.indexOf(",");
      if (comma < 0) return null;
      return { mime_type: a.mimeType, data: a.dataUrl.slice(comma + 1) };
    })
    .filter(Boolean);
}

// Window-level guard against the browser's default drop behavior: a file
// dropped anywhere outside a handled target NAVIGATES the page to that
// file, replacing the whole panel with the image. Swallow stray file
// drops everywhere, and be generous on the chat tab — a drop that missed
// the composer still lands in the attachment strip. Drops already handled
// by a real target (the composer, the recipe uploader) arrive here with
// defaultPrevented set and are left alone. Returns a teardown function;
// wired in panel.js connectedCallback/disconnectedCallback.
export function createGlobalDropGuard(host) {
  const isFileDrag = (e) => e.dataTransfer?.types?.includes?.("Files");
  // dragenter/dragleave fire on every element transition while dragging,
  // so a depth counter is the only reliable "is a file drag in flight"
  // signal: enter++ / leave--, zero means the drag left the window.
  let depth = 0;
  const setDropActive = (value) => {
    if (host._chatDropActive !== value) host._chatDropActive = value;
  };
  const onDragEnter = (e) => {
    if (!isFileDrag(e)) return;
    depth += 1;
    // Full-pane "Drop images to attach" overlay — only where a drop can
    // actually do something (chat tab + vision-capable model).
    if (host._activeTab === "chat" && supportsImageAttachments(host)) {
      setDropActive(true);
    }
  };
  const onDragLeave = (e) => {
    if (!isFileDrag(e)) return;
    depth = Math.max(0, depth - 1);
    if (depth === 0) setDropActive(false);
  };
  const onDragOver = (e) => {
    if (isFileDrag(e)) e.preventDefault();
  };
  const onDrop = (e) => {
    depth = 0;
    setDropActive(false);
    if (!isFileDrag(e) || e.defaultPrevented) return;
    e.preventDefault();
    if (host._activeTab === "chat" && e.dataTransfer.files?.length) {
      addImageAttachments(host, e.dataTransfer.files);
    }
  };
  window.addEventListener("dragenter", onDragEnter);
  window.addEventListener("dragleave", onDragLeave);
  window.addEventListener("dragover", onDragOver);
  window.addEventListener("drop", onDrop);
  return () => {
    window.removeEventListener("dragenter", onDragEnter);
    window.removeEventListener("dragleave", onDragLeave);
    window.removeEventListener("dragover", onDragOver);
    window.removeEventListener("drop", onDrop);
  };
}

// Full-pane drop target shown while a file drag is in flight over the
// panel. Pointer-events pass through so the drop itself lands on the
// composer or the global guard as usual — this is purely visual.
export function renderDropOverlay(host) {
  if (!host._chatDropActive) return html``;
  return html`
    <div class="chat-drop-overlay">
      <div class="chat-drop-overlay-inner">
        <ha-icon icon="mdi:image-plus-outline"></ha-icon>
        <span
          >${host._t("chat_attachment_drop_here", "Drop images to attach")}</span
        >
      </div>
    </div>
  `;
}

export function renderAttachmentStrip(host) {
  const attachments = host._chatAttachments || [];
  if (!attachments.length && !host._attachmentNotice) return html``;
  return html`
    <div class="composer-attachments">
      ${attachments.map(
        (a, idx) => html`
          <span class="composer-attachment">
            <img src=${a.dataUrl} alt=${a.name || "image"} />
            <button
              type="button"
              class="composer-attachment-remove"
              title=${host._t("chat_attachment_remove", "Remove image")}
              @click=${() => removeChatAttachment(host, idx)}
            >
              ×
            </button>
          </span>
        `,
      )}
      ${
        host._attachmentNotice
          ? html`<span class="composer-attachment-notice"
              >${host._attachmentNotice}</span
            >`
          : html``
      }
    </div>
  `;
}
