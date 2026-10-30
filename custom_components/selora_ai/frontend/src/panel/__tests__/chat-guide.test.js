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

const messages = [
  { role: "user", content: "Add 3 cameras to Frigate" },
  { role: "assistant", content: "" },
  { role: "user", content: "yes" },
  { role: "assistant", content: "Done." },
];

function host(overrides = {}) {
  return {
    _guide: guide,
    _activeSessionId: "s1",
    _messages: messages,
    ...overrides,
  };
}

describe("a guided conversation's heading", () => {
  it("tops Selora's first reply, over the Frigate banner", () => {
    const markup = ser(renderGuide(host(), 1));
    expect(markup).toContain("chat-guide");
    expect(markup).toContain("card-heading--banner");
    expect(markup).toContain("/api/selora_ai/images/frigate-banner.webp");
    expect(markup).toContain("Recognize the people you know on your cameras");
  });

  it("stays off the request and every later reply", () => {
    expect(renderGuide(host(), 0)).toBe("");
    expect(renderGuide(host(), 3)).toBe("");
  });

  it("stays out of any other conversation", () => {
    expect(renderGuide(host({ _activeSessionId: "s2" }), 1)).toBe("");
    expect(renderGuide(host({ _guide: null }), 1)).toBe("");
  });

  it("draws no picture for a name the panel does not know", () => {
    const markup = ser(
      renderGuide(host({ _guide: { ...guide, banner: "elsewhere" } }), 1),
    );
    expect(markup).not.toContain("card-heading--banner");
  });
});
