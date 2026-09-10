import { describe, it, expect } from "vitest";
import {
  automationSearchFields,
  buildSearchRegistry,
  collectConfigRefs,
  matchesSearchFields,
  normalizeSearch,
  sceneSearchFields,
  searchTerms,
} from "../entity-search.js";

const HASS = {
  states: {
    "binary_sensor.myggspray": {
      attributes: { friendly_name: "MYGGSPRAY wrlss mtn sensor Basement" },
    },
    "light.game_area": {
      attributes: { friendly_name: "Game Area Main Lights" },
    },
    "light.porch": { attributes: { friendly_name: "Porch Light" } },
    "cover.store_lumieres": { attributes: { friendly_name: "Store Lumières" } },
  },
  entities: {
    "binary_sensor.myggspray": { area_id: "basement" },
    "light.game_area": { device_id: "dev_game", area_id: null },
    "light.porch": { area_id: "porch" },
    "cover.store_lumieres": { area_id: "porch" },
  },
  devices: {
    dev_game: { name: "Game Area Hub", area_id: "basement" },
  },
  areas: {
    basement: { name: "Basement" },
    porch: { name: "Porch" },
  },
};

const reg = () => buildSearchRegistry(HASS);

const AUTOMATION = {
  alias: "Game Area Auto Lights",
  description: "Turn on the main lights when motion is detected",
  entity_id: "automation.game_area_auto_lights",
  triggers: [
    { platform: "state", entity_id: "binary_sensor.myggspray", to: "on" },
  ],
  conditions: [],
  actions: [
    {
      choose: [
        {
          conditions: [{ condition: "sun", after: "sunset" }],
          sequence: [
            {
              action: "light.turn_on",
              target: { entity_id: ["light.game_area"] },
            },
          ],
        },
      ],
    },
  ],
};

// ---------------------------------------------------------------------------
// normalizeSearch / searchTerms
// ---------------------------------------------------------------------------
describe("normalizeSearch", () => {
  it("strips diacritics and casefolds", () => {
    expect(normalizeSearch("Store Lumières")).toBe("store lumieres");
  });

  it("splits entity_ids on the dot and underscores", () => {
    expect(normalizeSearch("light.kitchen_light")).toBe("light kitchen light");
  });

  it("survives null", () => {
    expect(normalizeSearch(null)).toBe("");
  });
});

describe("searchTerms", () => {
  it("splits on whitespace and normalizes each term", () => {
    expect(searchTerms("  Basement   Lumières ")).toEqual([
      "basement",
      "lumieres",
    ]);
  });

  it("keeps a quoted phrase whole", () => {
    expect(searchTerms('"game area" lights')).toEqual(["game area", "lights"]);
  });

  it("treats an unclosed quote as running to the end", () => {
    // Every keystroke between the two quotes passes through this state; a
    // literal quote left in the term would empty the list until it is closed.
    expect(searchTerms('"game area')).toEqual(["game area"]);
  });

  it("drops a stray closing quote instead of poisoning the term", () => {
    expect(searchTerms('game" area')).toEqual(["game", "area"]);
  });

  it("returns nothing for an empty query", () => {
    expect(searchTerms("   ")).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// collectConfigRefs
// ---------------------------------------------------------------------------
describe("collectConfigRefs", () => {
  it("reaches ids nested inside choose branches and targets", () => {
    const refs = collectConfigRefs(AUTOMATION.actions);
    expect([...refs.entities]).toEqual(["light.game_area"]);
  });

  it("collects all five target kinds", () => {
    const refs = collectConfigRefs([
      { platform: "device", device_id: "dev_game" },
      { action: "light.turn_off", target: { area_id: ["porch"] } },
      { action: "light.turn_off", target: { floor_id: "ground_floor" } },
      { action: "light.turn_off", target: { label_id: ["holiday"] } },
    ]);
    expect([...refs.devices]).toEqual(["dev_game"]);
    expect([...refs.areas]).toEqual(["porch"]);
    expect([...refs.floors]).toEqual(["ground_floor"]);
    expect([...refs.labels]).toEqual(["holiday"]);
  });

  it("splits a legacy comma-separated entity_id string", () => {
    const refs = collectConfigRefs([
      { entity_id: "light.porch, light.game_area" },
    ]);
    expect([...refs.entities]).toEqual(["light.porch", "light.game_area"]);
  });

  it("collects service-specific entity_id spellings", () => {
    // tts.speak addresses its speaker as `media_player_entity_id` — the one
    // reference that automation makes to the device.
    const refs = collectConfigRefs([
      { action: "tts.speak", data: { media_player_entity_id: "light.porch" } },
    ]);
    expect([...refs.entities]).toEqual(["light.porch"]);
  });

  it("ignores templated targets and the `all` sentinel", () => {
    const refs = collectConfigRefs([
      { entity_id: "{{ trigger.entity_id }}" },
      { entity_id: "all" },
    ]);
    expect(refs.entities.size).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Automation search
// ---------------------------------------------------------------------------
describe("automation search", () => {
  const match = (query, automation = AUTOMATION) =>
    matchesSearchFields(
      automationSearchFields(automation, reg()),
      searchTerms(query),
    );

  it("matches the alias", () => {
    expect(match("game area").match).toBe(true);
  });

  it("matches the description", () => {
    expect(match("motion").match).toBe(true);
  });

  it("finds the automation by a device it is triggered from", () => {
    const res = match("myggspray");
    expect(res.match).toBe(true);
    expect(res.reasons).toEqual(["MYGGSPRAY wrlss mtn sensor Basement"]);
  });

  it("finds it by the area a targeted entity inherits from its device", () => {
    const res = match("basement");
    expect(res.match).toBe(true);
    // The target light has no area of its own and inherits Basement from Game
    // Area Hub. The AREA is what matched, so the area is what gets named —
    // reporting "Game Area Main Lights" would point at a light whose own name
    // says nothing about the basement.
    expect(res.reasons).toContain("Basement");
    expect(res.reasons).not.toContain("Game Area Main Lights");
  });

  it("names the device, not the entity, when the device matched", () => {
    const res = match("hub");
    expect(res.match).toBe(true);
    expect(res.reasons).toEqual(["Game Area Hub"]);
  });

  it("finds an automation targeting a whole floor", () => {
    const res = match("ground floor", {
      alias: "Evening Sweep",
      actions: [
        { action: "light.turn_off", target: { floor_id: "ground_floor" } },
      ],
    });
    expect(res.match).toBe(true);
    // No floor registry loaded here, so the slug is the label — the id is
    // still searchable, which is the point of carrying it.
    expect(res.reasons).toEqual(["ground_floor"]);
  });

  it("resolves a floor and a label to their names once the registry lands", () => {
    const withTargets = buildSearchRegistry(HASS, {
      floors: { ground_floor: { name: "Ground Floor" } },
      labels: { holiday: { name: "Holiday" } },
    });
    const fields = automationSearchFields(
      {
        alias: "Evening Sweep",
        actions: [
          {
            action: "light.turn_off",
            target: { floor_id: "ground_floor", label_id: ["holiday"] },
          },
        ],
      },
      withTargets,
    );
    expect(matchesSearchFields(fields, searchTerms("ground floor"))).toEqual({
      match: true,
      reasons: ["Ground Floor"],
    });
    expect(matchesSearchFields(fields, searchTerms("holiday")).reasons).toEqual(
      ["Holiday"],
    );
  });

  it("reports no reason when the row's own text already explains the hit", () => {
    expect(match("game area").reasons).toEqual([]);
  });

  it("requires every term to hit (AND)", () => {
    expect(match("myggspray porch").match).toBe(false);
    expect(match("myggspray lights").match).toBe(true);
  });

  it("does not match an unrelated automation", () => {
    expect(match("porch").match).toBe(false);
  });

  it("matches an entity_id typed with its dot", () => {
    expect(match("light.game_area").match).toBe(true);
  });

  it("matches everything on an empty query", () => {
    expect(match("").match).toBe(true);
  });

  it("survives an automation with no config loaded", () => {
    const bare = { alias: "Bare", entity_id: "automation.bare" };
    expect(match("bare", bare).match).toBe(true);
    expect(match("myggspray", bare).match).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Scene search
// ---------------------------------------------------------------------------
describe("scene search", () => {
  const SCENE = {
    name: "Début film",
    scene_id: "selora_ai_abc",
    entity_id: "scene.debut_film",
    entities: {
      "light.porch": { state: "off" },
      "cover.store_lumieres": { state: "closed" },
    },
  };

  const match = (query, scene = SCENE) =>
    matchesSearchFields(sceneSearchFields(scene, reg()), searchTerms(query));

  it("matches the scene name, accents ignored", () => {
    expect(match("debut").match).toBe(true);
  });

  it("finds a scene by a member's friendly name", () => {
    const res = match("porch light");
    expect(res.match).toBe(true);
    expect(res.reasons).toContain("Porch Light");
  });

  it("finds a scene by the area its members are in", () => {
    const res = match("porch");
    expect(res.match).toBe(true);
    expect(res.reasons.length).toBeGreaterThan(0);
  });

  it("does not match a device the scene does not set", () => {
    expect(match("myggspray").match).toBe(false);
  });

  it("survives a scene whose members were not loaded", () => {
    expect(match("film", { name: "Début film", entities: {} }).match).toBe(
      true,
    );
  });
});

// ---------------------------------------------------------------------------
// Registry degradation
// ---------------------------------------------------------------------------
describe("buildSearchRegistry", () => {
  it("reads hass through a getter so the wrapper survives hass swaps", () => {
    let hass = { states: {}, entities: {}, devices: {}, areas: {} };
    const r = buildSearchRegistry(() => hass);
    expect(r.entity("light.porch")).toBe(null);
    hass = HASS;
    expect(r.entity("light.porch").name).toBe("Porch Light");
  });

  it("prefers the full entity registry for the device link", () => {
    const displayOnly = { ...HASS, entities: { "light.game_area": {} } };
    const withoutFull = buildSearchRegistry(displayOnly);
    expect(withoutFull.entity("light.game_area").device).toBe("");

    const withFull = buildSearchRegistry(displayOnly, {
      entities: { "light.game_area": { device_id: "dev_game" } },
      devices: HASS.devices,
      areas: HASS.areas,
    });
    expect(withFull.entity("light.game_area").device).toBe("Game Area Hub");
    expect(withFull.entity("light.game_area").area).toBe("Basement");
  });

  it("prefers the live registry over the once-fetched full one", () => {
    // `full` is loaded on the first keystroke and never refreshed. If it won,
    // a device renamed later would stay findable only under its old name for
    // the rest of the panel's lifetime.
    const r = buildSearchRegistry(HASS, {
      entities: { "light.game_area": { device_id: "dev_game" } },
      devices: { dev_game: { name: "Old Hub Name", area_id: "porch" } },
      areas: { porch: { name: "Stale Porch" } },
    });
    expect(r.device("dev_game").name).toBe("Game Area Hub");
    expect(r.area("porch")).toBe("Porch");
  });

  it("falls back to the entity_id when nothing resolves", () => {
    const r = buildSearchRegistry({ states: {}, entities: {} });
    const fields = automationSearchFields(
      { alias: "X", triggers: [{ entity_id: "light.unknown_thing" }] },
      r,
    );
    expect(
      matchesSearchFields(fields, searchTerms("unknown thing")).match,
    ).toBe(true);
  });
});
