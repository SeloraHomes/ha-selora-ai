import { describe, it, expect } from "vitest";
import { renderAutomationFlowchart } from "../render-automations.js";
import { describeFlowItem } from "../../shared/flow-description.js";

// Same Lit-template flattener the condition-grouping tests use: walk
// { strings, values } results down to their rendered text so labels can be
// asserted without a DOM.
function flatten(node) {
  if (node == null || typeof node === "boolean") return "";
  if (Array.isArray(node)) return node.map(flatten).join("");
  if (typeof node === "object" && node.strings && "values" in node) {
    let out = "";
    node.strings.forEach((s, i) => {
      out += s;
      if (i < node.values.length) out += flatten(node.values[i]);
    });
    return out;
  }
  return String(node);
}

const hass = {
  states: {
    "light.main": { attributes: { friendly_name: "Family Room Main Lights" } },
    "light.sconces": { attributes: { friendly_name: "Family Room Sconces" } },
  },
};
const host = { hass, _t: (_key, fallback) => fallback };

// The "Family Room Auto Off" shape: trigger on either light, wait, then a
// single `if`/`then` that turns both off if they're still on.
const autoOff = {
  triggers: [
    { trigger: "state", entity_id: "light.main", to: "on" },
    { trigger: "state", entity_id: "light.sconces", to: "on" },
  ],
  conditions: [],
  actions: [
    { delay: "00:10:00" },
    {
      if: [
        {
          condition: "or",
          conditions: [
            { condition: "state", entity_id: "light.main", state: "on" },
            { condition: "state", entity_id: "light.sconces", state: "on" },
          ],
        },
      ],
      then: [
        { service: "light.turn_off", target: { entity_id: "light.main" } },
        { service: "light.turn_off", target: { entity_id: "light.sconces" } },
      ],
    },
  ],
};

describe("flow chart if/then/else expansion", () => {
  it("expands an if/then action into an IF branch with its conditions and actions", () => {
    const out = flatten(renderAutomationFlowchart(host, autoOff));
    expect(out).not.toContain("if: …");
    expect(out).toContain("If");
    expect(out).toContain("Any of the following:");
    expect(out).toContain("Turn off");
    // Both branch conditions and both then-actions are visible.
    expect(out.match(/Family Room Main Lights/g).length).toBeGreaterThan(1);
    expect(out.match(/Family Room Sconces/g).length).toBeGreaterThan(1);
  });

  it("renders an else list under an Otherwise panel", () => {
    const out = flatten(
      renderAutomationFlowchart(host, {
        triggers: [{ trigger: "state", entity_id: "light.main", to: "on" }],
        actions: [
          {
            if: [{ condition: "state", entity_id: "light.main", state: "on" }],
            then: [
              {
                service: "light.turn_off",
                target: { entity_id: "light.main" },
              },
            ],
            else: [
              {
                service: "light.turn_on",
                target: { entity_id: "light.sconces" },
              },
            ],
          },
        ],
      }),
    );
    expect(out).toContain("Otherwise");
    expect(out).toContain("Turn off");
    expect(out).toContain("Turn on");
  });

  it("omits the Otherwise panel when there is no else", () => {
    const out = flatten(renderAutomationFlowchart(host, autoOff));
    expect(out).not.toContain("Otherwise");
  });

  it("keeps the triggers visible when an unconditional action precedes the if", () => {
    const out = flatten(renderAutomationFlowchart(host, autoOff));
    // The delay runs on every firing, so trigger timing stays load-bearing.
    expect(out).toContain("Wait 00:10:00");
    expect(out).toContain("turns on");
  });

  it("renders a shorthand template condition readably, not as raw Jinja", () => {
    const out = flatten(
      renderAutomationFlowchart(host, {
        triggers: [{ trigger: "time", at: "23:00:00" }],
        actions: [
          {
            if: "{{ is_state('light.main', 'on') }}",
            then: [
              {
                service: "light.turn_off",
                target: { entity_id: "light.main" },
              },
            ],
          },
        ],
      }),
    );
    expect(out).not.toContain("{{");
    expect(out).not.toContain("is_state");
    // The name and the phrasing are separated in the output because the
    // renderer splits the description to inject a clickable entity link —
    // which is the other half of the fix, so assert the link is there too.
    expect(out).toContain("Family Room Main Lights");
    expect(out).toContain("is on");
    expect(out).toContain("hass-more-info");
  });

  it("renders a list of shorthand template conditions", () => {
    const out = flatten(
      renderAutomationFlowchart(host, {
        triggers: [{ trigger: "time", at: "23:00:00" }],
        actions: [
          {
            if: [
              "{{ is_state('light.main', 'on') }}",
              "{{ is_state('light.sconces', 'on') }}",
            ],
            then: [{ service: "light.turn_off" }],
          },
        ],
      }),
    );
    expect(out).not.toContain("{{");
    expect(out).toContain("Family Room Main Lights");
    expect(out).toContain("Family Room Sconces");
    expect(out.match(/is on/g)).toHaveLength(2);
  });

  it("falls back to the generic template text for an unparseable shorthand", () => {
    const out = flatten(
      renderAutomationFlowchart(host, {
        triggers: [{ trigger: "time", at: "23:00:00" }],
        actions: [
          {
            if: "{{ not is_state('light.main', 'on') }}",
            then: [{ service: "light.turn_off" }],
          },
        ],
      }),
    );
    // A negation can't be phrased affirmatively — better opaque than inverted.
    expect(out).not.toContain("{{");
    expect(out).toContain("Template evaluates to true");
  });

  it("describes a collapsed if/then instead of dumping raw keys", () => {
    expect(describeFlowItem(hass, { if: [], then: [{}, {}] })).toBe(
      "If conditions are met, run 2 actions",
    );
    expect(describeFlowItem(hass, { if: [], then: [{}], else: [{}] })).toBe(
      "If conditions are met, run 1 action, otherwise 1",
    );
  });
});

// ── repeat labels: literal vs templated ──────────────────────────────────────
// `count` and `for_each` both accept a template resolved at runtime; the label
// must not claim an iteration count it can't know.
describe("repeat label counting", () => {
  const label = (repeat) =>
    flatten(
      renderAutomationFlowchart(host, {
        triggers: [{ trigger: "state", entity_id: "light.main", to: "on" }],
        actions: [{ repeat }],
      }),
    );

  const step = {
    service: "light.turn_off",
    target: { entity_id: "light.main" },
  };

  it("counts a literal for_each array", () => {
    expect(label({ for_each: ["a", "b", "c"], sequence: [step] })).toContain(
      "Repeat for each item (3)",
    );
  });

  it("does not claim a count for a templated for_each", () => {
    const out = label({ for_each: "{{ dynamic_items }}", sequence: [step] });
    expect(out).toContain("Repeat for each item");
    // No parenthesised count of ANY value — the old code ran the template string
    // through asArray() and confidently reported "(1)".
    expect(out).not.toMatch(/Repeat for each item \(\d+\)/);
  });

  it("counts a literal numeric count", () => {
    expect(label({ count: 4, sequence: [step] })).toContain("Repeat 4");
  });

  it("counts a numeric string count", () => {
    expect(label({ count: "4", sequence: [step] })).toContain("Repeat 4");
  });

  it("falls back to an uncounted label for a templated count", () => {
    const out = label({ count: "{{ n_items }}", sequence: [step] });
    expect(out).not.toContain("{{ n_items }}");
    expect(out).toContain("Repeat");
  });
});
