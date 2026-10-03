import { describe, it, expect } from "vitest";
import { phraseVersionChanges, versionSummary } from "../version-summary.js";

const host = {
  hass: {
    language: "en",
    states: {
      "light.sconces": {
        entity_id: "light.sconces",
        attributes: { friendly_name: "Family Room Sconces" },
      },
    },
  },
};
const phrase = (changes) => phraseVersionChanges(host, changes);

const DELAY_EDIT = {
  kind: "item_changed",
  section: "actions",
  index: 1,
  before: { delay: { minutes: 5 } },
  after: { delay: { minutes: 2 } },
  details: [{ path: ["delay", "minutes"], before: 5, after: 2 }],
  detail_count: 1,
};

describe("phraseVersionChanges", () => {
  it("names the edited action in Flow wording, not the section", () => {
    const s = phrase([
      DELAY_EDIT,
      { kind: "field_changed", field: "description", before: "a", after: "b" },
    ]);
    expect(s).toMatch(/^Action changed from “.+5.+” to “.+2.+”\.$/);
    expect(s).not.toMatch(/description/);
  });

  it("names a value inside a branch by the device it sets", () => {
    const setTemp = (t) => ({
      action: "climate.set_temperature",
      data: { temperature: t },
      target: { entity_id: "climate.heatpump" },
    });
    const branch = (state, t) => ({
      conditions: [{ condition: "state", entity_id: "person.me", state }],
      sequence: [setTemp(t)],
    });
    const h = {
      hass: {
        language: "en",
        states: {
          "climate.heatpump": {
            entity_id: "climate.heatpump",
            attributes: { friendly_name: "Heatpump" },
          },
        },
      },
    };
    expect(
      phraseVersionChanges(h, [
        {
          kind: "item_changed",
          section: "actions",
          before: { choose: [branch("not_home", 16), branch("home", 20)] },
          after: { choose: [branch("not_home", 15), branch("home", 20)] },
          details: [
            {
              path: ["choose", 0, "sequence", 0, "data", "temperature"],
              before: 16,
              after: 15,
              entity: "climate.heatpump",
            },
          ],
          detail_count: 1,
        },
      ]),
    ).toBe("Heatpump temperature changed from 16 to 15.");
  });

  it("reads a removal and an identical-looking addition as one update", () => {
    const at7 = { trigger: "time", at: "07:00:00" };
    expect(
      phrase([
        { kind: "item_removed", section: "triggers", before: at7 },
        {
          kind: "item_added",
          section: "triggers",
          after: { ...at7, id: "thursday_on" },
        },
      ]),
    ).toBe("Trigger updated.");
  });

  it("falls back to the values when the wording does not show them", () => {
    const { before, after, ...valuesOnly } = DELAY_EDIT;
    expect(phrase([valuesOnly])).toBe("Delay (minutes) changed from 5 to 2.");
  });

  it("names entities by their friendly name", () => {
    expect(
      phrase([
        {
          kind: "item_changed",
          section: "actions",
          details: [
            {
              path: ["target", "entity_id"],
              before: "light.other",
              after: "light.sconces",
            },
          ],
          detail_count: 1,
        },
      ]),
    ).toBe("Entity id changed from light.other to Family Room Sconces.");
  });

  it("mentions the description only when nothing else changed", () => {
    expect(phrase([{ kind: "field_changed", field: "description" }])).toBe(
      "Description updated.",
    );
  });

  it("phrases renames, settings, additions and reorders", () => {
    expect(
      phrase([{ kind: "field_changed", field: "alias", after: "Night" }]),
    ).toBe("Renamed to “Night”.");
    expect(
      phrase([
        {
          kind: "field_changed",
          field: "mode",
          before: "single",
          after: "restart",
        },
      ]),
    ).toBe("Run mode changed from single to restart.");
    expect(phrase([{ kind: "reordered", section: "actions" }])).toBe(
      "Actions reordered.",
    );
    expect(
      phrase([
        { kind: "item_removed", section: "conditions" },
        { kind: "field_changed", field: "max", before: null, after: 3 },
      ]),
    ).toBe("Condition removed and max set to 3.");
  });

  it("caps a long list with “other changes”", () => {
    const many = ["a", "b", "c", "d"].map((f) => ({
      kind: "field_changed",
      field: f,
      before: 1,
      after: 2,
    }));
    expect(phrase(many)).toBe(
      "A changed from 1 to 2, b changed from 1 to 2, and other changes.",
    );
  });

  it("is empty with nothing stored", () => {
    expect(phrase(undefined)).toBe("");
    expect(phrase(null)).toBe("");
    expect(phrase([])).toBe("");
  });
});

describe("versionSummary", () => {
  it("prefers the stored sentence in the viewer's language", () => {
    const version = {
      version_id: "v2",
      changes: [DELAY_EDIT],
      summary: "Now turns the pump on only on Thursdays.",
      summary_language: "en",
    };
    expect(
      versionSummary({ hass: { language: "en", states: {} } }, "a1", version),
    ).toBe("Now turns the pump on only on Thursdays.");
    expect(
      versionSummary({ hass: { language: "fr", states: {} } }, "a1", version),
    ).not.toBe("Now turns the pump on only on Thursdays.");
  });

  it("phrases each version once, until the entity registry changes", () => {
    const version = { version_id: "v2", changes: [DELAY_EDIT] };
    const h = { hass: { language: "en", states: {}, entities: {} } };
    const first = versionSummary(h, "a1", version);
    const cache = h._versionSummaryCache;
    h.hass = { ...h.hass, states: { x: {} } };
    expect(versionSummary(h, "a1", version)).toBe(first);
    expect(h._versionSummaryCache).toBe(cache);
    h.hass = { ...h.hass, entities: {} };
    versionSummary(h, "a1", version);
    expect(h._versionSummaryCache).not.toBe(cache);
  });
});
