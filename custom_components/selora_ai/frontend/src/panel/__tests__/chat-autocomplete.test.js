import { describe, it, expect } from "vitest";
import {
  detectTrigger,
  buildSuggestionIndex,
  rankSuggestions,
  findExactMatches,
  listByDomain,
  applySelection,
  buildEntityMarker,
  pruneStaleSelections,
  stripEntityMarkers,
  findGhostSuggestion,
} from "../chat-autocomplete.js";

describe("detectTrigger", () => {
  it("returns null when there's no trigger phrase", () => {
    expect(detectTrigger("hello world", 11)).toBeNull();
  });

  it("returns null for empty input", () => {
    expect(detectTrigger("", 0)).toBeNull();
  });

  it("detects 'turn on the ' as a device trigger", () => {
    const text = "turn on the kit";
    const got = detectTrigger(text, text.length);
    expect(got).not.toBeNull();
    expect(got.kind).toBe("device");
    expect(got.query).toBe("kit");
    expect(text.slice(got.start, got.end)).toBe("kit");
  });

  it("bare 'the '/'my ' do detect, but the 3-char minimum + match filter keep them quiet in pure prose", () => {
    // detectTrigger fires; the dropdown only opens if a device/area
    // actually matches the partial query — handled by _updateAutocomplete.
    const got = detectTrigger("tell me the weather", 19);
    expect(got).not.toBeNull();
    expect(got.kind).toBe("device");
    expect(got.includeAreas).toBe(true);
    expect(got.query).toBe("weather");
  });

  it("bare 'the ' lets verb-less phrasings address devices/areas", () => {
    const text = "Create an automation with the kitchen's ceil";
    const got = detectTrigger(text, text.length);
    expect(got).not.toBeNull();
    expect(got.query).toBe("kitchen's ceil");
  });

  it("triggers on the explicit @ shortcut", () => {
    const text = "look at @kit";
    const got = detectTrigger(text, text.length);
    expect(got.kind).toBe("device");
    expect(got.query).toBe("kit");
  });

  it("does NOT trigger when the user has only typed the article", () => {
    // The verb-led pattern matches "turn on " too, with "the" becoming
    // the query — we suppress until the user types a real device name.
    expect(detectTrigger("turn on the", 11)).toBeNull();
    expect(detectTrigger("dim my", 6)).toBeNull();
    expect(detectTrigger("turn on the ", 12)).toBeNull();
  });

  it("triggers as soon as the device name starts after the article", () => {
    const text = "turn on the kit";
    const got = detectTrigger(text, text.length);
    expect(got).not.toBeNull();
    expect(got.query).toBe("kit");
  });

  it("triggers on other actuating verbs", () => {
    for (const verb of ["dim ", "open ", "close ", "lock the ", "play "]) {
      const text = `please ${verb}kit`;
      const got = detectTrigger(text, text.length);
      expect(got, `verb=${verb}`).not.toBeNull();
      expect(got.kind).toBe("device");
      expect(got.query).toBe("kit");
    }
  });

  it("detects 'in the ' as an area trigger (more specific wins)", () => {
    const text = "turn lights off in the kit";
    const got = detectTrigger(text, text.length);
    expect(got.kind).toBe("area");
    expect(got.query).toBe("kit");
  });

  it("detects 'of the ' as an area trigger", () => {
    const text = "the lights of the bedr";
    const got = detectTrigger(text, text.length);
    expect(got.kind).toBe("area");
    expect(got.query).toBe("bedr");
  });

  it("detects 'in ' as an area trigger without 'the'", () => {
    const text = "lights off in kitc";
    const got = detectTrigger(text, text.length);
    expect(got.kind).toBe("area");
    expect(got.query).toBe("kitc");
  });

  it("detects 'activate ' as a scene trigger", () => {
    const text = "please activate movi";
    const got = detectTrigger(text, text.length);
    expect(got.kind).toBe("scene");
    expect(got.query).toBe("movi");
  });

  it("detects 'run ' as an automation trigger", () => {
    const text = "run morn";
    const got = detectTrigger(text, text.length);
    expect(got.kind).toBe("automation");
    expect(got.query).toBe("morn");
  });

  it("allows multi-word partial names", () => {
    const text = "turn on the living roo";
    const got = detectTrigger(text, text.length);
    expect(got.kind).toBe("device");
    expect(got.query).toBe("living roo");
  });

  it("stops the query at sentence terminators", () => {
    const text = "Hello there. run morn";
    const got = detectTrigger(text, text.length);
    expect(got.kind).toBe("automation");
    expect(got.query).toBe("morn");
  });

  it("returns null when the query is only whitespace", () => {
    const text = "turn on the ";
    expect(detectTrigger(text, text.length)).toBeNull();
  });

  it("uses the caret position, not the end of the text", () => {
    const text = "turn on the kit and turn on the lamp";
    // caret right after "kit"
    const caret = "turn on the kit".length;
    const got = detectTrigger(text, caret);
    expect(got.kind).toBe("device");
    expect(got.query).toBe("kit");
  });
});

describe("buildSuggestionIndex", () => {
  const hass = {
    states: {
      "light.kitchen_lamp": {
        attributes: { friendly_name: "Kitchen Lamp" },
        state: "on",
      },
      "switch.coffee_maker": {
        attributes: { friendly_name: "Coffee Maker" },
        state: "off",
      },
      "scene.movie_night": {
        attributes: { friendly_name: "Movie Night" },
        state: "scening",
      },
      "automation.morning_routine": {
        attributes: { friendly_name: "Morning Routine" },
        state: "on",
      },
      "script.bedtime": {
        attributes: { friendly_name: "Bedtime" },
        state: "off",
      },
      "sensor.temperature": {
        attributes: { friendly_name: "Temperature" },
        state: "21",
      },
    },
    entities: {
      "light.kitchen_lamp": { area_id: "kitchen" },
    },
  };
  const areas = {
    kitchen: { name: "Kitchen" },
    bedroom: { name: "Bedroom" },
  };

  it("includes devices, scenes, automations, scripts, and areas", () => {
    const items = buildSuggestionIndex(hass, areas);
    const kinds = new Set(items.map((i) => i.kind));
    expect(kinds.has("device")).toBe(true);
    expect(kinds.has("scene")).toBe(true);
    expect(kinds.has("automation")).toBe(true);
    expect(kinds.has("area")).toBe(true);
  });

  it("excludes sensors", () => {
    const items = buildSuggestionIndex(hass, areas);
    expect(
      items.find((i) => i.entity_id === "sensor.temperature"),
    ).toBeUndefined();
  });

  it("attaches area name to device when known", () => {
    const items = buildSuggestionIndex(hass, areas);
    const lamp = items.find((i) => i.entity_id === "light.kitchen_lamp");
    expect(lamp.area).toBe("Kitchen");
  });

  it("inherits area from the device when entity has no direct area_id", () => {
    const hassMixed = {
      states: {
        "light.bed_light_a": {
          attributes: { friendly_name: "Bed Light" },
          state: "off",
        },
        "light.bed_light_b": {
          attributes: { friendly_name: "Bed Light" },
          state: "off",
        },
      },
      entities: {
        // No area_id on either entity — both inherit via device_id
        "light.bed_light_a": { device_id: "dev_master" },
        "light.bed_light_b": { device_id: "dev_guest" },
      },
    };
    const devices = {
      dev_master: { area_id: "master_bedroom" },
      dev_guest: { area_id: "guest_bedroom" },
    };
    const areasFull = {
      master_bedroom: { name: "Master Bedroom" },
      guest_bedroom: { name: "Guest Bedroom" },
    };
    const items = buildSuggestionIndex(hassMixed, areasFull, devices);
    const a = items.find((i) => i.entity_id === "light.bed_light_a");
    const b = items.find((i) => i.entity_id === "light.bed_light_b");
    expect(a.area).toBe("Master Bedroom");
    expect(b.area).toBe("Guest Bedroom");
  });

  it("surfaces scripts under the automation kind", () => {
    const items = buildSuggestionIndex(hass, areas);
    const bedtime = items.find((i) => i.entity_id === "script.bedtime");
    expect(bedtime.kind).toBe("automation");
  });

  it("returns empty array for empty hass", () => {
    expect(buildSuggestionIndex(null, null)).toEqual([]);
    expect(buildSuggestionIndex({}, null)).toEqual([]);
  });

  it("drops a device's remote when it also exposes a media_player", () => {
    // A TV device exposes a media_player + its IR/CEC remote (same device_id).
    // The remote is an accessory of the media_player, so it's dropped.
    const hassTv = {
      states: {
        "media_player.tv": {
          attributes: { friendly_name: "Samsung Q6 Series" },
          state: "off",
        },
        "remote.tv": {
          attributes: { friendly_name: "Samsung Q6 Series" },
          state: "off",
        },
      },
      entities: {
        "media_player.tv": { area_id: "game", device_id: "dev_tv" },
        "remote.tv": { area_id: "game", device_id: "dev_tv" },
      },
    };
    const items = buildSuggestionIndex(hassTv, { game: { name: "Game Area" } });
    const tvRows = items.filter((i) => i.kind === "device");
    expect(tvRows).toHaveLength(1);
    expect(tvRows[0].entity_id).toBe("media_player.tv");
  });

  it("sources device_id from the full entity registry (real path)", () => {
    // Real installs: hass.entities (display registry) OMITS device_id — it
    // arrives only via config/entity_registry/list, passed as the 4th arg.
    // The accessory dedupe must work off that, not hass.entities.
    const hassTv = {
      states: {
        "media_player.tv": {
          attributes: { friendly_name: "Samsung Q6 Series" },
          state: "off",
        },
        "remote.tv": {
          attributes: { friendly_name: "Samsung Q6 Series" },
          state: "off",
        },
      },
      // Display registry: area only, NO device_id (mirrors real HA).
      entities: {
        "media_player.tv": { area_id: "game" },
        "remote.tv": { area_id: "game" },
      },
    };
    // Full entity registry (4th arg) carries device_id.
    const fullEntities = {
      "media_player.tv": { area_id: "game", device_id: "dev_tv" },
      "remote.tv": { area_id: "game", device_id: "dev_tv" },
    };
    const items = buildSuggestionIndex(
      hassTv,
      { game: { name: "Game Area" } },
      null,
      fullEntities,
    );
    const tvRows = items.filter((i) => i.kind === "device");
    expect(tvRows).toHaveLength(1);
    expect(tvRows[0].entity_id).toBe("media_player.tv");
  });

  it("falls back to hass.entities area when full registry is empty", () => {
    // Transient WS failure: _ensureFullRegistries resolves to empty maps and
    // passes {} as the 4th arg. Per-entity fallback must still recover area
    // metadata from the display registry rather than losing all area chips.
    const hass = {
      states: {
        "light.kitchen": {
          attributes: { friendly_name: "Kitchen Light" },
          state: "on",
        },
      },
      entities: {
        "light.kitchen": { area_id: "kitchen" },
      },
    };
    const items = buildSuggestionIndex(
      hass,
      { kitchen: { name: "Kitchen" } },
      null,
      {}, // empty full registry (WS failure)
    );
    const row = items.find((i) => i.entity_id === "light.kitchen");
    expect(row.area).toBe("Kitchen");
  });

  it("keeps a garage door's cover AND lock (complementary controls)", () => {
    // One garage-door device exposes cover + lock under the same name. Both
    // are addressable ("open" vs "lock the garage door"), so neither is
    // dropped — the accessory rule is scoped to remote→media_player only.
    const hassGarage = {
      states: {
        "cover.garage_door": {
          attributes: { friendly_name: "Garage Door" },
          state: "closed",
        },
        "lock.garage_door": {
          attributes: { friendly_name: "Garage Door" },
          state: "locked",
        },
      },
      entities: {
        "cover.garage_door": { area_id: "garage", device_id: "dev_garage" },
        "lock.garage_door": { area_id: "garage", device_id: "dev_garage" },
      },
    };
    const items = buildSuggestionIndex(hassGarage, {
      garage: { name: "Garage" },
    });
    const domains = items
      .filter((i) => i.kind === "device")
      .map((i) => i.domain)
      .sort();
    expect(domains).toEqual(["cover", "lock"]);
  });

  it("drops the area-less shadow of the SAME device's area-tagged row", () => {
    // Two entities of ONE device (same device_id), same name/domain, one with
    // an area and one without — the area-less one is a redundant shadow.
    const hassDup = {
      states: {
        "media_player.tv_a": {
          attributes: { friendly_name: "Living TV" },
          state: "off",
        },
        "media_player.tv_b": {
          attributes: { friendly_name: "Living TV" },
          state: "off",
        },
      },
      entities: {
        "media_player.tv_a": { device_id: "dev_tv" }, // no area
        "media_player.tv_b": { area_id: "living", device_id: "dev_tv" },
      },
    };
    const items = buildSuggestionIndex(hassDup, {
      living: { name: "Living Room" },
    });
    const rows = items.filter((i) => i.kind === "device");
    expect(rows).toHaveLength(1);
    expect(rows[0].entity_id).toBe("media_player.tv_b");
  });

  it("keeps distinct device-less helpers with the same name", () => {
    // Helpers (input_boolean, …) have no device_id. A global "Guest Mode" and
    // a room-specific one share a name; the area-tagged one must NOT evict the
    // global area-less one — they're distinct entities, not one device.
    const hassHelpers = {
      states: {
        "input_boolean.guest_mode": {
          attributes: { friendly_name: "Guest Mode" },
          state: "off",
        },
        "input_boolean.guest_mode_kitchen": {
          attributes: { friendly_name: "Guest Mode" },
          state: "off",
        },
      },
      entities: {
        "input_boolean.guest_mode": {}, // global, no device, no area
        "input_boolean.guest_mode_kitchen": { area_id: "kitchen" },
      },
    };
    const items = buildSuggestionIndex(hassHelpers, {
      kitchen: { name: "Kitchen" },
    });
    const ids = items
      .filter((i) => i.kind === "device")
      .map((i) => i.entity_id)
      .sort();
    expect(ids).toEqual([
      "input_boolean.guest_mode",
      "input_boolean.guest_mode_kitchen",
    ]);
  });

  it("collapses a TV split across two integrations to one row", () => {
    // Real case: Music Assistant exposes "Samsung Q6 Series (82)" (no area);
    // Samsung Smart TV exposes "Samsung Q6 Series (82) (QN82Q6FNA)" as a
    // media_player + remote in an area — one physical TV. The names form a
    // pure parenthetical chain (not a fork), so the area-less Music Assistant
    // row is shadowed by the area-tagged Smart TV one, and the accessory
    // remote is dropped: a single row survives.
    const hassTv = {
      states: {
        "media_player.samsung_ma": {
          attributes: { friendly_name: "Samsung Q6 Series (82)" },
          state: "off",
        },
        "media_player.samsung_tv": {
          attributes: { friendly_name: "Samsung Q6 Series (82) (QN82Q6FNA)" },
          state: "off",
        },
        "remote.samsung_tv": {
          attributes: { friendly_name: "Samsung Q6 Series (82) (QN82Q6FNA)" },
          state: "off",
        },
      },
      entities: {
        "media_player.samsung_ma": { device_id: "dev_ma" }, // no area
        "media_player.samsung_tv": { area_id: "game", device_id: "dev_tv" },
        "remote.samsung_tv": { area_id: "game", device_id: "dev_tv" },
      },
    };
    const items = buildSuggestionIndex(hassTv, { game: { name: "Game Area" } });
    const ids = items
      .filter((i) => i.kind === "device")
      .map((i) => i.entity_id);
    expect(ids).toEqual(["media_player.samsung_tv"]);
  });

  it("keeps devices disambiguated by differing parentheticals", () => {
    // "(Left)"/"(Right)" are deliberate disambiguators for two real devices
    // in one area — the parenthetical VALUES differ, so they must not merge.
    const hassLamps = {
      states: {
        "light.lamp_left": {
          attributes: { friendly_name: "Lamp (Left)" },
          state: "off",
        },
        "light.lamp_right": {
          attributes: { friendly_name: "Lamp (Right)" },
          state: "off",
        },
        "sensor_climate.indoor": {
          attributes: { friendly_name: "Sensor (Indoor)" },
          state: "on",
        },
      },
      entities: {
        "light.lamp_left": { area_id: "living" },
        "light.lamp_right": { area_id: "living" },
      },
    };
    const items = buildSuggestionIndex(hassLamps, {
      living: { name: "Living Room" },
    });
    const labels = items
      .filter((i) => i.kind === "device")
      .map((i) => i.label)
      .sort();
    expect(labels).toEqual(["Lamp (Left)", "Lamp (Right)"]);
  });

  it("keeps distinct same-name devices while the registry is pending", () => {
    // First-use path: the `devices` map hasn't loaded, so device-inherited
    // areas resolve to null. Two "Bed Light"s in different rooms must NOT
    // collapse — device_id disambiguates them even with no area chip yet.
    const hassPending = {
      states: {
        "light.bed_a": {
          attributes: { friendly_name: "Bed Light" },
          state: "off",
        },
        "light.bed_b": {
          attributes: { friendly_name: "Bed Light" },
          state: "off",
        },
      },
      entities: {
        "light.bed_a": { device_id: "dev_master" },
        "light.bed_b": { device_id: "dev_guest" },
      },
    };
    // devices arg omitted (null) — areas can't be resolved yet.
    const items = buildSuggestionIndex(hassPending, {});
    const rows = items.filter((i) => i.kind === "device");
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.entity_id).sort()).toEqual([
      "light.bed_a",
      "light.bed_b",
    ]);
  });

  it("keeps same-named devices in different areas apart", () => {
    const hassMulti = {
      states: {
        "light.bed_a": {
          attributes: { friendly_name: "Bed Light" },
          state: "off",
        },
        "light.bed_b": {
          attributes: { friendly_name: "Bed Light" },
          state: "off",
        },
      },
      entities: {
        "light.bed_a": { area_id: "master" },
        "light.bed_b": { area_id: "guest" },
      },
    };
    const items = buildSuggestionIndex(hassMulti, {
      master: { name: "Master" },
      guest: { name: "Guest" },
    });
    expect(items.filter((i) => i.kind === "device")).toHaveLength(2);
  });
});

describe("rankSuggestions", () => {
  const items = [
    { kind: "device", label: "Kitchen Lamp", _lowerLabel: "kitchen lamp" },
    {
      kind: "device",
      label: "Kitchen Counter",
      _lowerLabel: "kitchen counter",
    },
    {
      kind: "device",
      label: "Living Room Lamp",
      _lowerLabel: "living room lamp",
    },
    { kind: "device", label: "Bedroom Light", _lowerLabel: "bedroom light" },
    { kind: "area", label: "Kitchen", _lowerLabel: "kitchen" },
  ];

  it("filters by kind", () => {
    const ranked = rankSuggestions(items, "area", "kit");
    expect(ranked.every((i) => i.kind === "area")).toBe(true);
    expect(ranked).toHaveLength(1);
    expect(ranked[0].label).toBe("Kitchen");
  });

  it("prefers prefix matches over word matches", () => {
    const ranked = rankSuggestions(items, "device", "kit");
    expect(ranked[0].label.toLowerCase().startsWith("kit")).toBe(true);
  });

  it("ranks whole-word matches above partial-word prefixes", () => {
    // 'bed' is a whole word in 'Bed Light' but only a partial-word
    // prefix of 'Bedroom'. The light should come first.
    const ranked = rankSuggestions(
      [
        { kind: "device", label: "Bedroom", _lowerLabel: "bedroom" },
        { kind: "device", label: "Bed Light", _lowerLabel: "bed light" },
      ],
      "device",
      "bed",
    );
    expect(ranked[0].label).toBe("Bed Light");
    expect(ranked[1].label).toBe("Bedroom");
  });

  it("matches a word starting with the query (not just whole label prefix)", () => {
    const ranked = rankSuggestions(items, "device", "lamp");
    const labels = ranked.map((r) => r.label);
    expect(labels).toContain("Kitchen Lamp");
    expect(labels).toContain("Living Room Lamp");
  });

  it("returns empty for an empty query", () => {
    expect(rankSuggestions(items, "device", "")).toEqual([]);
    expect(rankSuggestions(items, "device", "   ")).toEqual([]);
  });

  it("caps results", () => {
    const many = Array.from({ length: 20 }, (_, i) => ({
      kind: "device",
      label: `Light ${i}`,
      _lowerLabel: `light ${i}`,
    }));
    const ranked = rankSuggestions(many, "device", "light", 5);
    expect(ranked).toHaveLength(5);
  });
});

describe("includeAreas flag", () => {
  it("is set on generic device verbs", () => {
    expect(detectTrigger("turn on the bed", 15).includeAreas).toBe(true);
    expect(detectTrigger("set the bed", 11).includeAreas).toBe(true);
  });
  it("is set on the @ shortcut", () => {
    expect(detectTrigger("hey @bed", 8).includeAreas).toBe(true);
  });
  it("is NOT set on domain-constrained verbs", () => {
    expect(detectTrigger("lock the bed", 12).includeAreas).toBe(false);
    expect(detectTrigger("dim the bed", 11).includeAreas).toBe(false);
  });
});

describe("empty-query trigger after domain-constraining verbs", () => {
  it("opens after 'unlock the ' (with trailing space) with empty query", () => {
    const got = detectTrigger("unlock the ", 11);
    expect(got).not.toBeNull();
    expect(got.query).toBe("");
    expect(got.domains).toEqual(["lock"]);
  });

  it("opens after 'unlock ' (no article) with empty query", () => {
    const got = detectTrigger("unlock ", 7);
    expect(got).not.toBeNull();
    expect(got.query).toBe("");
    expect(got.domains).toEqual(["lock"]);
  });

  it("still rejects empty query for unconstrained verbs", () => {
    // "turn on the " would dump every device; require the user to type
    // at least one character first.
    expect(detectTrigger("turn on the ", 12)).toBeNull();
  });

  it("still rejects when the user is mid-article", () => {
    expect(detectTrigger("unlock the", 10)).toBeNull();
  });
});

describe("listByDomain", () => {
  const items = [
    {
      kind: "device",
      domain: "lock",
      label: "Front Door",
      _lowerLabel: "front door",
    },
    {
      kind: "device",
      domain: "lock",
      label: "Back Door",
      _lowerLabel: "back door",
    },
    {
      kind: "device",
      domain: "light",
      label: "Hallway",
      _lowerLabel: "hallway",
    },
  ];
  it("returns items in the requested domains, sorted alphabetically", () => {
    const out = listByDomain(items, "device", ["lock"]);
    expect(out.map((i) => i.label)).toEqual(["Back Door", "Front Door"]);
  });
  it("respects max", () => {
    expect(listByDomain(items, "device", ["lock"], 1)).toHaveLength(1);
  });
  it("returns [] when no domains given", () => {
    expect(listByDomain(items, "device", null)).toEqual([]);
    expect(listByDomain(items, "device", [])).toEqual([]);
  });
});

describe("verb-driven domain hints", () => {
  it("lock/unlock constrains to locks", () => {
    expect(detectTrigger("lock the kit", 12).domains).toEqual(["lock"]);
    expect(detectTrigger("unlock the kit", 14).domains).toEqual(["lock"]);
  });
  it("dim/brighten constrains to lights", () => {
    expect(detectTrigger("dim the kit", 11).domains).toEqual(["light"]);
  });
  it("play/pause constrains to media_player", () => {
    expect(detectTrigger("play the kit", 12).domains).toEqual(["media_player"]);
  });
  it("turn on/off is unconstrained", () => {
    expect(detectTrigger("turn on the kit", 15).domains).toBeNull();
  });
  it("@ shortcut is unconstrained", () => {
    expect(detectTrigger("hey @kit", 8).domains).toBeNull();
  });
});

describe("rank+exact with domain filter", () => {
  const items = [
    {
      kind: "device",
      domain: "light",
      label: "Kitchen Lamp",
      _lowerLabel: "kitchen lamp",
    },
    {
      kind: "device",
      domain: "lock",
      label: "Kitchen Door",
      _lowerLabel: "kitchen door",
    },
    {
      kind: "device",
      domain: "switch",
      label: "Kitchen Outlet",
      _lowerLabel: "kitchen outlet",
    },
  ];
  it("rankSuggestions filters by domain when provided", () => {
    const ranked = rankSuggestions(items, "device", "kit", undefined, ["lock"]);
    expect(ranked).toHaveLength(1);
    expect(ranked[0].label).toBe("Kitchen Door");
  });
  it("rankSuggestions returns all when no domain filter", () => {
    const ranked = rankSuggestions(items, "device", "kit");
    expect(ranked.length).toBeGreaterThan(1);
  });
  it("findExactMatches honors the domain filter", () => {
    expect(findExactMatches(items, "device", "Kitchen Lamp", ["lock"])).toEqual(
      [],
    );
    expect(
      findExactMatches(items, "device", "Kitchen Door", ["lock"]),
    ).toHaveLength(1);
  });
});

describe("findGhostSuggestion", () => {
  it("returns the suffix that completes the partial word", () => {
    const got = findGhostSuggestion("Create an auto", 14);
    expect(got).not.toBeNull();
    expect(got.suffix).toBe("mation");
    expect(got.word).toBe("automation");
  });

  it("is case-insensitive on the input prefix", () => {
    const got = findGhostSuggestion("Create an AUTO", 14);
    expect(got.suffix).toBe("mation");
  });

  it("returns null when the prefix is too short", () => {
    expect(findGhostSuggestion("au", 2)).toBeNull();
  });

  it("returns null when the word is already complete", () => {
    expect(findGhostSuggestion("automation", 10)).toBeNull();
  });

  it("returns null when caret is mid-word", () => {
    // Caret right after "auto" but more word chars follow → mid-word.
    expect(findGhostSuggestion("automation", 4)).toBeNull();
  });

  it("returns null for unknown words", () => {
    expect(findGhostSuggestion("xyzzy", 5)).toBeNull();
  });

  it("prefers the SHORTEST matching vocabulary entry", () => {
    // 'automation' (10) is shorter than 'automations' (11) and both
    // start with the same prefix; the shorter, more-common target wins.
    const got = findGhostSuggestion("autom", 5);
    expect(got.word).toBe("automation");
  });
});

describe("findExactMatches", () => {
  const items = [
    { kind: "device", label: "AC", _lowerLabel: "ac" },
    { kind: "device", label: "TV", _lowerLabel: "tv" },
    { kind: "device", label: "Kitchen Lamp", _lowerLabel: "kitchen lamp" },
  ];

  it("returns devices whose name exactly matches the query (case-insensitive)", () => {
    expect(findExactMatches(items, "device", "AC")).toHaveLength(1);
    expect(findExactMatches(items, "device", "ac")).toHaveLength(1);
    expect(findExactMatches(items, "device", "tv")).toHaveLength(1);
  });

  it("does not return partial matches", () => {
    expect(findExactMatches(items, "device", "A")).toEqual([]);
    expect(findExactMatches(items, "device", "Kitchen")).toEqual([]);
  });

  it("respects the kind filter", () => {
    expect(findExactMatches(items, "area", "AC")).toEqual([]);
  });
});

describe("stripEntityMarkers", () => {
  it("removes [[entity:…]] markers", () => {
    expect(stripEntityMarkers("turn on the AC [[entity:switch.ac]]")).toBe(
      "turn on the AC",
    );
  });
  it("removes [[entities:…]] markers", () => {
    expect(stripEntityMarkers("hi [[entities:light.a,light.b]]")).toBe("hi");
  });
  it("removes [[areas:…]] markers", () => {
    expect(stripEntityMarkers("clean up [[areas:Kitchen,Bedroom]]")).toBe(
      "clean up",
    );
  });
  it("passes through text without markers", () => {
    expect(stripEntityMarkers("plain message")).toBe("plain message");
  });
  it("handles empty / null", () => {
    expect(stripEntityMarkers("")).toBe("");
    expect(stripEntityMarkers(null)).toBe(null);
  });
});

describe("applySelection", () => {
  it("replaces the query slice with the chosen friendly name", () => {
    const text = "turn on the kit";
    const trigger = { kind: "device", query: "kit", start: 12, end: 15 };
    const item = { label: "Kitchen Lamp" };
    const { text: out, caret } = applySelection(text, trigger, item);
    expect(out).toBe("turn on the Kitchen Lamp ");
    expect(caret).toBe(out.length);
  });

  it("preserves text after the caret", () => {
    const text = "turn on the kit then go to bed";
    const trigger = { kind: "device", query: "kit", start: 12, end: 15 };
    const { text: out } = applySelection(text, trigger, {
      label: "Kitchen Lamp",
    });
    expect(out).toBe("turn on the Kitchen Lamp then go to bed");
  });
});

describe("buildEntityMarker", () => {
  it("returns empty for no selections", () => {
    expect(buildEntityMarker([])).toBe("");
    expect(buildEntityMarker(null)).toBe("");
  });

  it("emits single-entity marker", () => {
    const out = buildEntityMarker([
      { entity_id: "light.kitchen", label: "Kitchen Lamp", kind: "device" },
    ]);
    expect(out).toContain("[[entity:light.kitchen]]");
  });

  it("emits multi-entity marker", () => {
    const out = buildEntityMarker([
      { entity_id: "light.a", label: "A", kind: "device" },
      { entity_id: "light.b", label: "B", kind: "device" },
    ]);
    expect(out).toContain("[[entities:light.a,light.b]]");
  });

  it("de-duplicates entity IDs", () => {
    const out = buildEntityMarker([
      { entity_id: "light.a", label: "A", kind: "device" },
      { entity_id: "light.a", label: "A", kind: "device" },
    ]);
    expect(out).toContain("[[entity:light.a]]");
    expect(out).not.toContain("[[entities:");
  });

  it("emits an areas hint when only areas are picked", () => {
    const out = buildEntityMarker([
      { kind: "area", area_id: "kitchen", label: "Kitchen" },
    ]);
    expect(out).toContain("[[areas:Kitchen]]");
  });

  it("combines entities and areas in one marker block", () => {
    const out = buildEntityMarker([
      { entity_id: "light.a", label: "A", kind: "device" },
      { kind: "area", area_id: "bedroom", label: "Bedroom" },
    ]);
    expect(out).toContain("[[entity:light.a]]");
    expect(out).toContain("[[areas:Bedroom]]");
  });
});

describe("pruneStaleSelections", () => {
  it("keeps selections whose label still appears in the text", () => {
    const sels = [
      { label: "Kitchen Lamp", entity_id: "light.a" },
      { label: "Bedroom Light", entity_id: "light.b" },
    ];
    const kept = pruneStaleSelections("turn on the Kitchen Lamp", sels);
    expect(kept).toHaveLength(1);
    expect(kept[0].entity_id).toBe("light.a");
  });

  it("returns the input untouched when empty", () => {
    expect(pruneStaleSelections("hello", [])).toEqual([]);
  });

  it("is case-insensitive", () => {
    const sels = [{ label: "Kitchen Lamp", entity_id: "light.a" }];
    expect(pruneStaleSelections("turn on the kitchen lamp", sels)).toHaveLength(
      1,
    );
  });

  it("requires whole-word match so short labels don't match accidentally", () => {
    // 'AC' must not match when the user rewrote the prompt to mention
    // 'back door' — without word boundaries 'ac' is a substring of
    // 'back' and the stale marker leaks through.
    const sels = [{ label: "AC", entity_id: "switch.ac" }];
    expect(pruneStaleSelections("unlock the back door", sels)).toEqual([]);
    expect(pruneStaleSelections("turn on the AC", sels)).toHaveLength(1);
    expect(pruneStaleSelections("turn on the AC.", sels)).toHaveLength(1);
  });

  it("matches multi-word labels with word boundaries", () => {
    const sels = [{ label: "AC Unit", entity_id: "climate.ac_unit" }];
    expect(pruneStaleSelections("turn on the AC Unit", sels)).toHaveLength(1);
    // 'AC Unit' must not match 'BACK AC Unitary' substring inside a longer word
    expect(pruneStaleSelections("the AC Unitary system", sels)).toEqual([]);
  });
});
