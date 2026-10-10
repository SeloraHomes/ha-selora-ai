// The predicted next message, shown as the empty composer's placeholder once
// a turn ends (backend: next_prompt.py). It belongs to the reply it followed:
// it shows only while that reply is the last message on screen, so a new
// turn, an appended approval result or a session switch retires it without
// anyone having to clear it. Never stored — a reopened session shows none.

// A provider that hangs must not hold the composer: past this the turn is
// released with no prediction.
const PREDICT_TIMEOUT_MS = 10000;

// Ask for a prediction after `assistantMsg` finished. The composer stays
// disabled while that session is on screen (`isPredicting`) until the answer
// is in, so the suggestion is there before the user can start typing. `refocus` is set at the end of a
// turn, where the composer had focus before it was disabled.
export async function requestNextPrompt(
  host,
  assistantMsg,
  sessionId,
  { refocus = false } = {},
) {
  host._nextPrompt = null;
  if (!sessionId || !host._config?.next_prompt_enabled) return;
  const token = (host._predictToken = (host._predictToken || 0) + 1);
  host._predictingSession = sessionId;
  let result = null;
  try {
    result = await Promise.race([
      host.hass.callWS({
        type: "selora_ai/predict_next_prompt",
        session_id: sessionId,
        ...(host.hass?.language ? { language: host.hass.language } : {}),
      }),
      new Promise((resolve) => setTimeout(resolve, PREDICT_TIMEOUT_MS, null)),
    ]);
  } catch (err) {
    // A prediction is a nicety; an older backend without the command, or a
    // provider error, just means there is none to show.
    result = null;
  }
  // A newer request owns the flag and the result.
  if (host._predictToken !== token) return;
  host._predictingSession = null;
  const text = typeof result?.prompt === "string" ? result.prompt.trim() : "";
  if (text) host._nextPrompt = { message: assistantMsg, sessionId, text };
  if (refocus || text) _focusComposer(host, refocus);
}

// Tab only reaches the prediction from the composer, and after a click on a
// card button focus sits on that button — Tab then just moves focus along.
// Return it to the composer unless the user is in another text field. Not on
// touch screens unless the composer had it, since focusing opens the keyboard
// over the conversation.
function _focusComposer(host, hadFocus) {
  if (!hadFocus && !globalThis.matchMedia?.("(pointer: fine)").matches) {
    return;
  }
  host.updateComplete?.then(() => {
    const active = host.shadowRoot?.activeElement;
    if (
      active &&
      (active.isContentEditable ||
        /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName))
    ) {
      return;
    }
    const ta = host.shadowRoot?.querySelector(".composer-textarea");
    if (ta && !ta.disabled) ta.focus();
  });
}

// Predict again after the user acted on the last reply's card. Accepting
// changes what is likely next — the work is saved, so a follow-up edit is
// the next move rather than the save itself.
export function refreshNextPrompt(host) {
  const messages = host._messages || [];
  const last = messages[messages.length - 1];
  if (last?.role !== "assistant") return;
  requestNextPrompt(host, last, host._activeSessionId);
}

// Whether the composer on screen waits on a prediction. Scoped to the
// session it was asked for: another conversation's composer is not held.
export function isPredicting(host) {
  return (
    !!host._predictingSession &&
    host._predictingSession === host._activeSessionId
  );
}

// The prediction to show now, or "" when there is none.
export function activeNextPrompt(host) {
  const np = host._nextPrompt;
  if (!np || host._input || host._loading || host._streaming) return "";
  if (np.sessionId !== host._activeSessionId) return "";
  const messages = host._messages || [];
  return messages[messages.length - 1] === np.message ? np.text : "";
}

// Put the prediction in the composer for the user to send or edit. Returns
// false when there was nothing to accept, so key handlers can fall through.
export function acceptNextPrompt(host, textarea) {
  const text = activeNextPrompt(host);
  if (!text) return false;
  host._input = text;
  host._nextPrompt = null;
  requestAnimationFrame(() => {
    if (!textarea) return;
    textarea.value = text;
    textarea.setSelectionRange(text.length, text.length);
    textarea.focus();
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
  return true;
}
