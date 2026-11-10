import { describe, expect, it, vi } from "vitest";

import { _onComposerKeydown } from "../render-chat.js";

function composer(overrides = {}) {
  return {
    _autocomplete: {
      open: true,
      items: [{ entity_id: "light.kitchen", name: "Kitchen Light" }],
      activeIndex: 0,
      picked: true,
    },
    _messages: [],
    _sendMessage: vi.fn(),
    ...overrides,
  };
}

function key(name, extra = {}) {
  return {
    key: name,
    preventDefault: vi.fn(),
    target: { value: "かんじ", selectionStart: 3, selectionEnd: 3 },
    ...extra,
  };
}

describe("the composer while an input method composes", () => {
  it("leaves Enter to confirm the conversion, sending nothing", () => {
    const host = composer({ _autocomplete: null });
    const e = key("Enter", { isComposing: true });
    _onComposerKeydown(host, e);
    expect(host._sendMessage).not.toHaveBeenCalled();
    expect(e.preventDefault).not.toHaveBeenCalled();
  });

  it("does not pick the highlighted suggestion (Safari's keyCode 229)", () => {
    const host = composer();
    const before = host._autocomplete;
    _onComposerKeydown(host, key("Enter", { keyCode: 229 }));
    expect(host._autocomplete).toBe(before);
    expect(host._sendMessage).not.toHaveBeenCalled();
  });

  it("sends on Enter once the text is composed", () => {
    const host = composer({ _autocomplete: null });
    _onComposerKeydown(host, key("Enter"));
    expect(host._sendMessage).toHaveBeenCalledOnce();
  });
});
