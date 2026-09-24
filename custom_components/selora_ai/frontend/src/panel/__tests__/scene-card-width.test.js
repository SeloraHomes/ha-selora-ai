import { readFileSync, readdirSync } from "fs";

import { describe, expect, it } from "vitest";

import { renderMessage } from "../render-chat.js";

// Templates are serialized rather than mounted, as scene-entity-list.test.js
// does. Attribute values arrive as template values, so the class attribute is
// reassembled here exactly as it would be in the DOM.
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

function host() {
  return {
    _t: (_key, fallback) => fallback,
    _renderSceneCard: () => "",
    _renderProposalCard: () => "",
    _onCodeCopyClick: () => {},
    hass: { entities: {}, devices: {}, areas: {} },
  };
}

const scene = (entities, extra = {}) => ({
  role: "assistant",
  content: "Updated.",
  scene: { name: "Cozy Living", entities },
  scene_status: "pending",
  ...extra,
});

const TWO = {
  "light.a": { state: "on" },
  "light.b": { state: "on" },
};

function wrapClasses(msg) {
  const markup = ser(renderMessage(host(), msg, 0));
  return markup.match(/class="(assistant-wrap[^"]*)"/)?.[1] || "";
}

describe("a scene card is sized by its tiles, not by the prose above it", () => {
  // The wrap is inline-flex, so it takes the width of its widest content.
  // With nothing else asking for room that was the sentence — the same scene
  // laid out in two columns under a long line and one under a short one, a
  // message apart in the same conversation.
  it("asks for the tile width on every scene message", () => {
    expect(wrapClasses(scene(TWO))).toContain("assistant-wrap--scene");
  });

  it("asks for it on the refine source as much as on the proposal", () => {
    // These are the two cards the user sees side by side, and the sentence
    // above them is written by different code paths and different lengths.
    const refining = wrapClasses(
      scene(TWO, { scene_status: "refining", content: "I've loaded it." }),
    );
    expect(refining).toContain("assistant-wrap--scene");
  });

  it("narrows to one track when the scene holds one entity", () => {
    // Two tracks' worth of bubble around a lone tile is dead space.
    const classes = wrapClasses(scene({ "light.a": { state: "on" } }));
    expect(classes).toContain("assistant-wrap--scene-single");
  });

  it("keeps the second track for anything larger", () => {
    expect(wrapClasses(scene(TWO))).not.toContain(
      "assistant-wrap--scene-single",
    );
  });

  it("leaves a message with no scene alone", () => {
    const plain = wrapClasses({ role: "assistant", content: "Hello." });
    expect(plain).not.toContain("assistant-wrap--scene");
  });
});

describe("the scene wrap classes are defined", () => {
  const STYLES = (function collect(dir, out = []) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = new URL(
        `${entry.name}${entry.isDirectory() ? "/" : ""}`,
        dir,
      );
      if (entry.isDirectory()) collect(path, out);
      else if (entry.name.endsWith(".css.js"))
        out.push(readFileSync(path, "utf8"));
    }
    return out;
  })(new URL("../../", import.meta.url)).join("\n");

  it("has a rule for each", () => {
    // Markup and stylesheet drift silently — the page still renders, just at
    // the prose's width again.
    expect(STYLES).toMatch(/\.assistant-wrap--scene\b/);
    expect(STYLES).toMatch(/\.assistant-wrap--scene-single\b/);
  });

  it("orders the scene rule after the narrow-viewport override", () => {
    // Same specificity, so the later rule wins the width. Ahead of it the
    // ≤870px block's width:100% would take the width back on a phone.
    expect(STYLES.indexOf(".assistant-wrap--scene {")).toBeGreaterThan(
      STYLES.indexOf(".assistant-wrap--approval {"),
    );
  });
});
