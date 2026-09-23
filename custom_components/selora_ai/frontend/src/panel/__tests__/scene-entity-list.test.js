import { readFileSync, readdirSync } from "fs";

import { describe, expect, it } from "vitest";

import { renderSceneCard } from "../render-scenes.js";

// Templates are serialized rather than mounted — these tests run in node, the
// same way render-suggestions.test.js reads its markup.
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
    _yamlOpen: {},
    _justCreatedId: null,
    _sceneEditedEntities: () => ({}),
    _sceneIsDirty: () => false,
    hass: { entities: {}, devices: {}, areas: {} },
  };
}

const PROPOSAL = {
  scene: {
    name: "Movie Night",
    entities: {
      "light.lounge": { state: "on", brightness: 120 },
      "light.hall": { state: "off" },
    },
  },
  scene_yaml: "name: Movie Night\n",
};

describe("the scene entity list shows one tile per entity", () => {
  const markup = ser(renderSceneCard(host(), PROPOSAL, 0));

  it("renders a single tile per entity, not a before/after pair", () => {
    // Two tiles for the same entity read as one control duplicated: both
    // carry the same name and icon, and an unchanged entity makes them
    // identical. One tile per entity, showing what the scene sets.
    const tiles = [...markup.matchAll(/selora-entity-grid/g)];
    expect(tiles).toHaveLength(2);
  });

  it("drops the two-column scaffolding with it", () => {
    // The arrow and the Now / Scene sets captions only mean anything with a
    // second column to point at.
    expect(markup).not.toMatch(/scene-ent-arrow/);
    expect(markup).not.toMatch(/scene-ent-head/);
    expect(markup).not.toMatch(/scene-ent-row/);
    expect(markup).not.toMatch(/>Now</);
  });

  it("keeps a proposal's tiles read-only", () => {
    // Nothing is saved yet, so the tile must not drive the real device. In
    // the editor the same tile IS the control — its service calls are
    // rerouted — which is why the modifier is conditional.
    expect(markup).toMatch(/scene-ent-tile--forced/);
  });
});

describe("the scene list names no class that no stylesheet defines", () => {
  // The two-column layout left `.scene-ent-row`, `.scene-ent-head`,
  // `.scene-ent-arrow` and a `.scene-ent-tile--edit` that never had a rule at
  // all. Markup and stylesheet drift silently — the page still renders — so
  // this is the check that notices.
  const SOURCE = readFileSync(
    new URL("../render-scenes.js", import.meta.url),
    "utf8",
  );
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

  it("defines every scene-list class it uses", () => {
    const used = new Set();
    for (const [, attr] of SOURCE.matchAll(/class="([^"$]+)"/g)) {
      for (const name of attr.split(/\s+/).filter(Boolean)) {
        if (name.startsWith("scene-ent") || name.startsWith("scene-edit"))
          used.add(name);
      }
    }
    expect(used.size).toBeGreaterThan(0);
    const missing = [...used].filter(
      (name) => !new RegExp(`\\.${name}\\b`).test(STYLES),
    );
    expect(missing).toEqual([]);
  });

  it("leaves no rule behind for a layout that is gone", () => {
    for (const dead of [
      "scene-ent-row",
      "scene-ent-head",
      "scene-ent-arrow",
      "scene-ent-cap--target",
    ]) {
      expect(STYLES).not.toMatch(new RegExp(`\\.${dead}\\b`));
    }
  });
});
