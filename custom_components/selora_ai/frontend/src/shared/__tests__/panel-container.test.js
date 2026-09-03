import { describe, it, expect } from "vitest";
import {
  sizePanelContainer,
  releasePanelContainer,
} from "../panel-container.js";

const container = (props = {}) => ({
  localName: "ha-panel-custom",
  style: {},
  ...props,
});

describe("sizePanelContainer", () => {
  it("gives HA's panel container a definite height", () => {
    // Without it `:host { height: 100% }` resolves against an auto-height
    // block box, the shell is sized by the active tab's content, and every
    // overlay positioned against the shell is clipped at that edge.
    const parent = container();
    expect(sizePanelContainer(parent)).toBe(true);
    expect(parent.style.height).toBe("100%");
  });

  it("leaves a height HA set itself alone", () => {
    const parent = container({ style: { height: "100dvh" } });
    expect(sizePanelContainer(parent)).toBe(false);
    expect(parent.style.height).toBe("100dvh");
  });

  it("touches nothing but HA's panel container", () => {
    // The panel is also mountable outside `ha-panel-custom` (a dev harness, a
    // dialog); sizing an arbitrary ancestor to 100% is not ours to do.
    const parent = container({ localName: "div" });
    expect(sizePanelContainer(parent)).toBe(false);
    expect(parent.style.height).toBeUndefined();
  });

  it("survives no parent at all", () => {
    expect(sizePanelContainer(null)).toBe(false);
    expect(sizePanelContainer(undefined)).toBe(false);
  });
});

describe("releasePanelContainer", () => {
  it("hands the container back on disconnect", () => {
    // HA keeps one `ha-panel-custom` for every custom panel and swaps its
    // child, so a height left behind follows the next panel in.
    const parent = container();
    sizePanelContainer(parent);
    expect(releasePanelContainer(parent)).toBe(true);
    expect(parent.style.height).toBe("");
  });

  it("re-applies across a suspend/resume cycle", () => {
    const parent = container();
    sizePanelContainer(parent);
    releasePanelContainer(parent);
    expect(sizePanelContainer(parent)).toBe(true);
    expect(parent.style.height).toBe("100%");
  });

  it("never clears a height it did not set", () => {
    const parent = container({ style: { height: "100dvh" } });
    sizePanelContainer(parent);
    expect(releasePanelContainer(parent)).toBe(false);
    expect(parent.style.height).toBe("100dvh");
  });

  it("leaves a height something else has since written", () => {
    const parent = container();
    sizePanelContainer(parent);
    parent.style.height = "480px";
    expect(releasePanelContainer(parent)).toBe(false);
    expect(parent.style.height).toBe("480px");
  });

  it("survives no container at all", () => {
    expect(releasePanelContainer(null)).toBe(false);
    expect(releasePanelContainer(undefined)).toBe(false);
  });
});
