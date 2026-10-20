// Composer draft persistence: the unsent message (and the entity chips picked
// into it) survives a page reload.
//
// One draft per HA user, not per session: switching conversations already
// carries the composer text along, and on reload the panel reopens the most
// recent session, which need not be the one the draft was typed in. Keyed by
// user because HA accounts share a browser's storage, and one user's unsent
// text must not appear in another's composer. That ties the restore to
// ``hass`` arriving rather than to the constructor.

import { pruneStaleSelections } from "./chat-autocomplete.js";

const DRAFT_KEY_PREFIX = "selora_ai_composer_draft:";

function _storage() {
  try {
    return globalThis.localStorage || null;
  } catch {
    return null;
  }
}

function _draftKey(host) {
  const userId = host.hass?.user?.id;
  return userId ? DRAFT_KEY_PREFIX + userId : null;
}

// Returns ``{ text, selections }`` or null when there is nothing to restore.
export function loadComposerDraft(host) {
  const storage = _storage();
  const key = _draftKey(host);
  if (!storage || !key) return null;
  try {
    const draft = JSON.parse(storage.getItem(key) || "null");
    if (!draft || typeof draft.text !== "string" || !draft.text) return null;
    // A chip whose label no longer appears in the text would emit an
    // [[entity:…]] marker for a device the message doesn't name.
    const selections = Array.isArray(draft.selections)
      ? pruneStaleSelections(
          draft.text,
          draft.selections.filter((s) => s && typeof s.label === "string"),
        )
      : [];
    return { text: draft.text, selections };
  } catch {
    return null;
  }
}

// Once per panel instance, as soon as the user is known. Anything typed
// before then wins over the stored draft. Run after every render.
export function restoreComposerDraft(host) {
  if (host._draftRestored || !_draftKey(host)) return;
  host._draftRestored = true;
  if (host._input) {
    // Typed while saves were held back: store it now, or a reload before the
    // next keystroke brings back the older draft.
    saveComposerDraft(host);
    return;
  }
  const draft = loadComposerDraft(host);
  if (!draft) return;
  host._input = draft.text;
  host._autocompleteSelections = draft.selections;
  host._fitDraft = true;
}

export function saveComposerDraft(host) {
  // Until the restore has run, an empty composer means "not loaded yet", not
  // "cleared" — saving it would delete the draft about to be restored.
  if (!host._draftRestored) return;
  // While Up/Down browses sent messages, ``_input`` holds a recalled message,
  // not the draft — which is parked in ``_historyDraft`` and was stored
  // before browsing started. Once the recalled message is edited, it is the
  // newest unsent text and is stored like any draft.
  if (host._historyIndex != null && host._input === host._historyRecalled) {
    return;
  }
  const storage = _storage();
  const key = _draftKey(host);
  if (!storage || !key) return;
  try {
    const text = host._input || "";
    if (!text) {
      storage.removeItem(key);
      return;
    }
    storage.setItem(
      key,
      JSON.stringify({ text, selections: host._autocompleteSelections || [] }),
    );
  } catch {
    // Quota exceeded or storage disabled: the draft just won't survive a
    // reload — not worth surfacing.
  }
}

// The textarea only grows in its @input handler, so a restored multi-line
// draft would show one row with the rest cropped. Size it once the textarea
// is rendered with the restored text (the chat tab may mount it later).
// Run after every render.
export function fitComposerToDraft(host) {
  if (!host._fitDraft) return;
  const ta = host.shadowRoot?.querySelector(".composer-textarea");
  if (!ta || ta.value !== host._input) return;
  host._fitDraft = false;
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 200) + "px";
}
