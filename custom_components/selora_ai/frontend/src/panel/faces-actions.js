// Face recognition's guide (backend: frigate/faces.py). The yes or no is a
// fixed answer, so a click goes straight to the server instead of through the
// model. The people card sends each photo straight to Frigate's Face Library
// through the server: a photo never reaches the model or the conversation.
import { _processImageFile } from "./chat-attachments.js";

// Frigate takes photos only once it runs face recognition, after its restart.
const POLL_MS = 5000;
// Past this the card stops waiting and offers to check again: a Frigate that
// cannot restart itself starts it only on its next restart.
const WAIT_MS = 5 * 60 * 1000;
// Mirrors websocket/frigate.py _MAX_PHOTO_B64: HA closes the websocket on a
// frame past 4 MiB.
const MAX_PHOTO_B64 = 3 * 1024 * 1024;
const PHOTO_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

export async function answerFaces(host, msg, action) {
  const sessionId = host._activeSessionId;
  if (!sessionId || !msg || msg._qa_used) return;
  msg._qa_used = true;
  msg._qa_picked = action.value;
  host._messages = [...host._messages];
  try {
    const result = await host.hass.callWS({
      type: "selora_ai/frigate/faces_answer",
      session_id: sessionId,
      enable: action.face_answer.enable,
      ...(host.hass?.language ? { language: host.hass.language } : {}),
    });
    // The server saved both sides, so a reopened session reads the same.
    if (host._activeSessionId === sessionId) {
      host._messages = [...host._messages, ...(result.messages || [])];
    }
  } catch (err) {
    msg._qa_used = false;
    msg._qa_picked = undefined;
    host._messages = [...host._messages];
    host._showToast?.(err.message || String(err), "error");
  }
}

// One state per panel: every people card shows the same library.
const STATES = new WeakMap();

export function peopleState(host) {
  let state = STATES.get(host);
  if (!state) {
    state = {
      loaded: false,
      loading: false,
      running: false,
      people: [],
      waitingSince: 0,
      timer: null,
      name: "",
      busy: false,
      progress: null,
      result: null,
    };
    STATES.set(host, state);
  }
  return state;
}

function changed(host) {
  host.requestUpdate?.();
}

/** Read the library, and while Frigate is not running it yet, read again. */
export async function loadPeople(host) {
  const state = peopleState(host);
  clearTimeout(state.timer);
  state.timer = null;
  state.loading = true;
  try {
    const result = await host.hass.callWS({
      type: "selora_ai/frigate/faces_people",
    });
    state.running = !!result.running;
    state.people = result.people || [];
  } catch (err) {
    state.running = false;
    console.error("Selora AI: could not read the Face Library", err);
  }
  state.loaded = true;
  state.loading = false;
  if (state.running) {
    state.waitingSince = 0;
  } else {
    state.waitingSince ||= Date.now();
    if (Date.now() - state.waitingSince < WAIT_MS) {
      state.timer = setTimeout(() => loadPeople(host), POLL_MS);
    }
  }
  changed(host);
}

/** Check again after the card stopped waiting. */
export function waitAgain(host) {
  const state = peopleState(host);
  state.waitingSince = 0;
  state.loaded = false;
  changed(host);
  loadPeople(host);
}

export function stoppedWaiting(state) {
  return (
    state.loaded &&
    !state.running &&
    !state.loading &&
    !state.timer &&
    state.waitingSince > 0
  );
}

async function sendPhoto(host, name, file) {
  if (!PHOTO_TYPES.has(file.type)) {
    throw new Error(
      host._t("faces_photo_unsupported", "Not a JPEG, PNG or WebP photo."),
    );
  }
  const photo = await _processImageFile(file);
  const image = photo.dataUrl.slice(photo.dataUrl.indexOf(",") + 1);
  if (image.length > MAX_PHOTO_B64) {
    throw new Error(
      host._t("faces_photo_too_large", "The photo is too large."),
    );
  }
  await host.hass.callWS({
    type: "selora_ai/frigate/faces_add_photo",
    name,
    content_type: photo.mimeType,
    image,
  });
}

/** Add each photo to *name*, one call each, then read the library again. */
export async function addPhotos(host, files) {
  const state = peopleState(host);
  const name = state.name.trim();
  const photos = Array.from(files || []);
  if (!name || !photos.length || state.busy) return;
  state.busy = true;
  state.result = null;
  state.progress = { done: 0, total: photos.length };
  changed(host);
  const failed = [];
  for (const file of photos) {
    try {
      await sendPhoto(host, name, file);
    } catch (err) {
      failed.push({ file: file.name, error: err?.message || String(err) });
    }
    state.progress = { ...state.progress, done: state.progress.done + 1 };
    changed(host);
  }
  state.result = { name, added: photos.length - failed.length, failed };
  // Once every photo is in, the form is ready for the next person; a person's
  // name stays while a photo still has to be tried again.
  if (!failed.length) state.name = "";
  state.busy = false;
  state.progress = null;
  await loadPeople(host);
}
