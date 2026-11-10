import { describe, expect, it } from "vitest";

import { renderMessage } from "../render-chat.js";

function ser(value) {
  if (value == null || typeof value === "boolean") return "";
  if (typeof value === "string" || typeof value === "number")
    return String(value);
  if (typeof value === "function") return "[fn]";
  if (Array.isArray(value)) return value.map(ser).join("");
  if (value.strings) {
    let out = "";
    for (let i = 0; i < value.strings.length; i++) {
      out += value.strings[i];
      if (i < value.values.length) out += ser(value.values[i]);
    }
    return out;
  }
  return "[obj]";
}

const host = {
  _t: (_key, fallback) => fallback,
  _onCodeCopyClick: () => {},
  hass: { entities: {}, devices: {}, areas: {} },
  _messages: [{}],
};

const ANSWER = "I can't tell from the cameras: none of them looks for dogs.";

const reply = (answerFirst) => ({
  role: "assistant",
  content: ANSWER,
  approval_status: "pending",
  command_approval: {
    proposal_id: "p1",
    approval_kind: "destructive",
    calls: [],
    deletes: [],
    removes: false,
    actions: [
      {
        kind: "frigate_objects",
        verb: "set_objects",
        target_id: "e1_zoom",
        name: "E1 Zoom",
        label: "E1 Zoom starts looking for dog (Selora Vision restarts)",
      },
    ],
    why: answerFirst
      ? "Have the cameras look for dog from now on?"
      : "No camera looks for dog yet.",
    ...(answerFirst ? { answer_first: true } : {}),
  },
});

describe("the card that has the cameras look for something", () => {
  it("follows the answer to a question that was not about the cameras", () => {
    const markup = ser(renderMessage(host, reply(true), 0));
    expect(markup).toContain(ANSWER);
    expect(markup).not.toContain("bubble--approval");
    expect(markup.indexOf(ANSWER)).toBeLessThan(
      markup.indexOf("Have the cameras look for dog from now on?"),
    );
  });

  it("stands in for the reply to a question about the cameras", () => {
    const markup = ser(renderMessage(host, reply(false), 0));
    expect(markup).not.toContain(ANSWER);
    expect(markup).toContain("bubble--approval");
  });
});
