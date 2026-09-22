import { describe, it, expect } from "vitest";

import { interpolate } from "../i18n.js";
import {
  automationSearchFields,
  buildSearchRegistry,
  matchesSearchFields,
} from "../entity-search.js";

describe("interpolate", () => {
  // `String.replace` with a STRING pattern expands `$&`, "$`" and `$'` in the
  // replacement, so a user-typed query substituted itself back into its own
  // "no results" line.
  it("treats $-sequences in the value as literal text", () => {
    const phrase = 'No automations match "{query}"';
    expect(interpolate(phrase, { query: "$`" })).toBe(
      'No automations match "$`"',
    );
    expect(interpolate(phrase, { query: "$&" })).toBe(
      'No automations match "$&"',
    );
    expect(interpolate(phrase, { query: "$'" })).toBe(
      'No automations match "$\'"',
    );
  });

  it("fills every occurrence and leaves unknown placeholders alone", () => {
    expect(interpolate("{a} and {a} but not {b}", { a: "x" })).toBe(
      "x and x but not {b}",
    );
  });

  it("stringifies non-string values", () => {
    expect(interpolate("matches {count}", { count: 3 })).toBe("matches 3");
  });
});

describe("buildSearchRegistry lookup cost", () => {
  // `pick()` ran `Object.keys(live).length` on every lookup, and `device` and
  // `entity` each call it once per referenced entity — so filtering was
  // O(entities x devices) and froze the first keystroke on a large home.
  const buildHome = (deviceCount) => {
    const devices = {};
    const entities = {};
    const states = {};
    for (let i = 0; i < deviceCount; i += 1) {
      devices[`dev${i}`] = { name: `Device ${i}`, area_id: "a1" };
      entities[`light.l${i}`] = { device_id: `dev${i}` };
      states[`light.l${i}`] = { attributes: { friendly_name: `Light ${i}` } };
    }
    return { devices, entities, states, areas: { a1: { name: "Kitchen" } } };
  };

  it("reads each registry once per identity, not once per lookup", () => {
    const home = buildHome(200);
    let keyCalls = 0;
    const realKeys = Object.keys;
    const counting = new Proxy(home.devices, {
      ownKeys(target) {
        keyCalls += 1;
        return realKeys(target);
      },
    });
    const hass = { ...home, devices: counting };
    const reg = buildSearchRegistry(hass, null);

    const automation = {
      alias: "Evening",
      triggers: [],
      actions: Array.from({ length: 50 }, (_, i) => ({
        entity_id: `light.l${i}`,
      })),
    };
    automationSearchFields(automation, reg);

    // One resolution for the whole pass — the count is the point, not the
    // exact value, so this fails loudly if the memo is removed.
    expect(keyCalls).toBeLessThanOrEqual(2);
  });

  it("still prefers the live collection and still finds matches", () => {
    const home = buildHome(3);
    const reg = buildSearchRegistry(home, {
      devices: { dev0: { name: "Stale Name" } },
    });
    const fields = automationSearchFields(
      { alias: "Evening", actions: [{ entity_id: "light.l0" }] },
      reg,
    );
    expect(matchesSearchFields(fields, ["device 0"]).match).toBe(true);
    expect(matchesSearchFields(fields, ["stale"]).match).toBe(false);
    expect(matchesSearchFields(fields, ["kitchen"]).match).toBe(true);
  });

  it("falls back to the full registry when the live one is empty", () => {
    const reg = buildSearchRegistry(
      { states: {}, entities: {}, devices: {}, areas: {} },
      {
        devices: { dev0: { name: "Hallway Sensor" } },
        entities: { "light.l0": { device_id: "dev0" } },
      },
    );
    const fields = automationSearchFields(
      { alias: "Evening", actions: [{ entity_id: "light.l0" }] },
      reg,
    );
    expect(matchesSearchFields(fields, ["hallway"]).match).toBe(true);
  });
});

describe("panel teardown", () => {
  // `_cardPanelTimers` entries are otherwise cleared only by a later toggle of
  // the SAME card, so leaving the panel inside PANEL_SETTLE_MS left a callback
  // writing to a detached host and the map kept one key per card ever opened.
  // Asserted against the source because instantiating the whole panel element
  // to observe a 320ms timer is a worse test than reading the teardown.
  it("clears the suggestion-card panel timers on disconnect", async () => {
    const fs = await import("node:fs");
    const url = await import("node:url");
    const src = fs.readFileSync(
      url.fileURLToPath(new URL("../../panel.js", import.meta.url)),
      "utf8",
    );
    const body = src.slice(src.indexOf("disconnectedCallback()"));
    const end = body.indexOf("\n  }\n");
    expect(end).toBeGreaterThan(-1);
    expect(body.slice(0, end)).toMatch(/_cardPanelTimers/);
  });
});
