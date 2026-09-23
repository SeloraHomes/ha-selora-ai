import { describe, expect, it } from "vitest";

import { renderScenes } from "../render-scenes.js";

// Booleans are printed rather than dropped: `?disabled=${false}` is exactly
// what these tests are about, and a serializer that renders it as "" cannot
// tell the two states apart.
function ser(value) {
  if (value == null) return "";
  if (typeof value === "boolean") return String(value);
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
  return "";
}

function menuFor(scene) {
  const host = {
    _t: (_key, fallback) => fallback,
    _scenes: [scene],
    _openSceneBurger: scene.scene_id,
    _openBurgerMenuStyle: "",
    _expandedScenes: {},
    _deletingScene: {},
    _loadingToChat: {},
    _savingScene: {},
    _testingScene: {},
    _yamlOpen: {},
    _sceneYamlOpen: {},
    _sceneFilter: "",
    _sceneStatusFilter: "all",
    _sceneSortBy: "name",
    _sceneSortDir: "asc",
    _searchRegistry: {},
    _ensureSearchRegistries: () => {},
    _sceneEditedEntities: () => ({}),
    _sceneIsDirty: () => false,
    _llmNeedsSetup: false,
    _justCreatedId: null,
    _highlightedScene: null,
    hass: { states: {}, entities: {}, devices: {}, areas: {} },
  };
  const markup = ser(renderScenes(host));
  // The Rename item is the one carrying the pencil; read the attributes that
  // precede it rather than guessing at offsets from the top of the menu.
  const head = markup.slice(0, markup.indexOf("mdi:pencil-outline"));
  return {
    markup,
    disabled: head
      .slice(head.lastIndexOf("?disabled="))
      .startsWith("?disabled=true"),
    title: head
      .slice(head.lastIndexOf("title="))
      .replace(/^title=/, "")
      .trim(),
  };
}

const row = (extra) => ({
  scene_id: "s1",
  name: "External lights",
  entity_id: "scene.external_lights",
  entities: {},
  entity_count: 0,
  yaml: "",
  source: "home_assistant",
  deletable: true,
  ...extra,
});

describe("the scenes menu offers Rename by capability, not by owner", () => {
  it("offers it for a Home Assistant scene the backend says is renamable", () => {
    // The row this whole change is about: written by HA's own scene editor,
    // an ordinary yaml entry carrying an id.
    const { markup, disabled } = menuFor(
      row({ renamable: true, rename_blocked: "" }),
    );
    expect(markup).toContain("Rename");
    expect(disabled).toBe(false);
  });

  it("offers it for a Selora scene", () => {
    const { disabled } = menuFor(
      row({ source: "selora", renamable: true, rename_blocked: "" }),
    );
    expect(disabled).toBe(false);
  });

  it("says why when an id-less yaml scene cannot be renamed", () => {
    // Hiding the item is what left the user asking why it was missing.
    const { markup, disabled, title } = menuFor(
      row({ renamable: false, rename_blocked: "no_yaml_id" }),
    );
    expect(markup).toContain("Rename");
    expect(disabled).toBe(true);
    expect(title).toContain("no id in scenes.yaml");
  });

  it("says why when the scene belongs to another integration", () => {
    const { disabled, title } = menuFor(
      row({
        renamable: false,
        rename_blocked: "integration",
        deletable: false,
      }),
    );
    expect(disabled).toBe(true);
    expect(title).toContain("another integration");
  });

  it("falls back to the old Selora-only rule when the backend is older", () => {
    // A panel newer than the running Python sends no flags at all; the item
    // must not become available for rows the old backend would refuse.
    expect(menuFor(row({})).disabled).toBe(true);
    expect(menuFor(row({ source: "selora" })).disabled).toBe(false);
  });
});
