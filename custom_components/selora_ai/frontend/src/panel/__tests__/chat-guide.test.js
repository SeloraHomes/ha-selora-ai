import { describe, expect, it } from "vitest";

import { renderGuide } from "../render-chat.js";

function ser(value) {
  if (value == null || typeof value === "boolean") return "";
  if (typeof value === "string" || typeof value === "number")
    return String(value);
  if (Array.isArray(value)) return value.map(ser).join("");
  if (value.strings) {
    let out = "";
    for (let i = 0; i < value.strings.length; i++) {
      out += value.strings[i];
      if (i < value.values.length) out += ser(value.values[i]);
    }
    return out;
  }
  return "";
}

const guide = {
  icon: "mdi:face-recognition",
  title: "Recognize the people you know on your cameras",
  subtitle: "Frigate can tell who is at the door once it knows their faces.",
  banner: "frigate",
  sessionId: "s1",
};

describe("a guided conversation's heading", () => {
  it("opens its own conversation, over the Frigate banner", () => {
    const markup = ser(renderGuide({ _guide: guide, _activeSessionId: "s1" }));
    expect(markup).toContain("chat-guide");
    expect(markup).toContain("card-heading--banner");
    expect(markup).toContain("/api/selora_ai/images/frigate-banner.webp");
    expect(markup).toContain("Recognize the people you know on your cameras");
  });

  it("stays out of any other conversation", () => {
    expect(renderGuide({ _guide: guide, _activeSessionId: "s2" })).toBe("");
    expect(renderGuide({ _guide: null, _activeSessionId: "s1" })).toBe("");
  });

  it("draws no picture for a name the panel does not know", () => {
    const markup = ser(
      renderGuide({
        _guide: { ...guide, banner: "elsewhere" },
        _activeSessionId: "s1",
      }),
    );
    expect(markup).not.toContain("card-heading--banner");
  });
});
