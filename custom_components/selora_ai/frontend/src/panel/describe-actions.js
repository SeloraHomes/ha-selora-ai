// A camera-descriptions answer is a fixed choice with a fixed next question,
// so a click goes straight to the server instead of through the model: no
// round trip per camera, and Frigate restarts once, after the last one.
export async function answerCamera(host, msg, action) {
  const sessionId = host._activeSessionId;
  if (!sessionId || !msg || msg._qa_used) return;
  msg._qa_used = true;
  msg._qa_picked = action.value;
  host._messages = [...host._messages];
  const answer = action.camera_answer;
  try {
    const result = await host.hass.callWS({
      type: "selora_ai/frigate/describe_answer",
      session_id: sessionId,
      camera: answer.camera,
      describe: answer.describe,
      zones: answer.zones || [],
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
