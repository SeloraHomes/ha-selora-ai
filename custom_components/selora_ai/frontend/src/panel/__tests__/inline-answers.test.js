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

const asked = (inline) => ({
  role: "assistant",
  content: "Should Selora AI describe what E1 Zoom sees?",
  quick_actions: [
    {
      label: "Turn descriptions on",
      value: "Turn descriptions on for E1 Zoom",
      mode: "choice",
      ...(inline ? { inline: true } : {}),
    },
    {
      label: "Leave it off",
      value: "Leave E1 Zoom off",
      mode: "choice",
      ...(inline ? { inline: true } : {}),
    },
  ],
});

describe("answers to a message's own question", () => {
  it("sit under the question, in its bubble", () => {
    // Above the composer they were a screen away from what they answer.
    const markup = ser(renderMessage(host, asked(true), 0));
    expect(markup).toContain("msg-quick");
    // The suggestion's "Ask Selora to do it" pill, with the answer's own mark.
    expect(markup).toContain("qa-group--answers");
    expect(markup).toMatch(
      /action-btn action-btn--primary\s[^]*action-btn-tick/,
    );
    expect(markup).toContain("action-btn--quiet");
    expect(markup).toContain("Turn descriptions on");
    expect(markup).toContain("Leave it off");
  });

  it("stay in place once answered, disabled, with the answer given lit", () => {
    const reopened = {
      ...host,
      _messages: [
        asked(true),
        { role: "user", content: "Leave E1 Zoom off" },
        { role: "assistant", content: "Next camera" },
      ],
    };
    const markup = ser(renderMessage(reopened, asked(true), 0));
    expect(markup).toContain("qa-group--used qa-group--picked");
    expect(markup).toMatch(/action-btn--picked\s[^]*Leave it off/);
    expect(markup.match(/action-btn--picked/g)).toHaveLength(1);
  });

  it("disable the buttons while a click is on its way", () => {
    const sending = { ...asked(true), _qa_used: true };
    sending._qa_picked = "Turn descriptions on for E1 Zoom";
    const markup = ser(renderMessage(host, sending, 0));
    expect(markup).toContain("qa-group--used qa-group--picked");
  });

  it("stay pressable on the question still waiting for an answer", () => {
    expect(ser(renderMessage(host, asked(true), 0))).not.toContain(
      "qa-group--used",
    );
  });

  it("leave other quick actions to the composer row", () => {
    expect(ser(renderMessage(host, asked(false), 0))).not.toContain(
      "Turn descriptions on",
    );
  });
});
