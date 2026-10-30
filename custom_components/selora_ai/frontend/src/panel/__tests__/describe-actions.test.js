import { describe, expect, it, vi } from "vitest";

import { answerCamera } from "../describe-actions.js";

const button = {
  label: "Leave it off",
  value: "Leave E1 Zoom off",
  inline: true,
  camera_answer: { camera: "e1_zoom", describe: false, zones: [] },
};

function host(callWS) {
  const asked = { role: "assistant", quick_actions: [button] };
  return {
    asked,
    _activeSessionId: "s1",
    _messages: [asked],
    hass: { language: "en", callWS },
    _showToast: vi.fn(),
  };
}

describe("answering a camera-descriptions question", () => {
  it("goes to the server, not the model, and shows both sides", async () => {
    const said = { role: "user", content: "Leave E1 Zoom off" };
    const next = { role: "assistant", content: "Should Selora AI describe…" };
    const h = host(vi.fn(async () => ({ messages: [said, next] })));

    await answerCamera(h, h.asked, button);

    expect(h.hass.callWS).toHaveBeenCalledWith({
      type: "selora_ai/frigate/describe_answer",
      session_id: "s1",
      camera: "e1_zoom",
      describe: false,
      zones: [],
      language: "en",
    });
    expect(h._messages.slice(1)).toEqual([said, next]);
    expect(h.asked._qa_used).toBe(true);
  });

  it("answers once, however fast the clicks", async () => {
    const h = host(vi.fn(async () => ({ messages: [] })));
    await Promise.all([
      answerCamera(h, h.asked, button),
      answerCamera(h, h.asked, button),
    ]);
    expect(h.hass.callWS).toHaveBeenCalledTimes(1);
  });

  it("gives the buttons back when the answer could not be saved", async () => {
    const h = host(
      vi.fn(async () => {
        throw new Error("Frigate no longer has that camera.");
      }),
    );

    await answerCamera(h, h.asked, button);

    expect(h.asked._qa_used).toBe(false);
    expect(h._showToast).toHaveBeenCalled();
  });
});
