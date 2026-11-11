import { describe, expect, it, vi } from "vitest";

import { _ghostSuffix, _onComposerKeydown } from "../render-chat.js";

// "light" typed: the vocabulary ghosts "lights", the list highlights a row.
function composer(label) {
  return {
    _input: "light",
    _ghost: { suffix: "s", anchor: { left: 0, top: 0, height: 20 }, caret: 5 },
    _autocomplete: {
      open: true,
      items: [{ entity_id: "light.x", label }],
      activeIndex: 0,
      trigger: { start: 0, end: 5, query: "light" },
    },
    _messages: [],
  };
}

describe("the ghost text while the suggestion list is open", () => {
  it("shows the highlighted row's tail, which is what Tab inserts", () => {
    expect(_ghostSuffix(composer("Light Strip"))).toBe(" Strip");
  });

  it("shows nothing for a row that does not continue the word", () => {
    expect(_ghostSuffix(composer("Kitchen Light"))).toBeNull();
  });

  it("does not accept the hidden vocabulary word on →", () => {
    const host = composer("Kitchen Light");
    const e = {
      key: "ArrowRight",
      preventDefault: vi.fn(),
      target: { value: "light", selectionStart: 5, selectionEnd: 5 },
    };
    _onComposerKeydown(host, e);
    expect(host._input).toBe("light");
    expect(e.preventDefault).not.toHaveBeenCalled();
  });

  it("shows the vocabulary word once the list is closed", () => {
    const host = composer("Kitchen Light");
    host._autocomplete = null;
    expect(_ghostSuffix(host)).toBe("s");
  });
});
