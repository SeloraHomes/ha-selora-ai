import { describe, it, expect } from "vitest";
import { renderAutomationFlowchart } from "../render-automations.js";

// Flatten a Lit TemplateResult (and nested arrays/results) to its text so
// we can assert on the labels the flow chart renders without a DOM. Lit's
// html`` returns { strings, values }; values may themselves be results,
// arrays, or primitives.
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

const host = {
  hass: {
    states: {
      "light.a": { attributes: { friendly_name: "Light A" } },
      "light.b": { attributes: { friendly_name: "Light B" } },
      "light.c": { attributes: { friendly_name: "Light C" } },
    },
  },
  _t: (_key, fallback) => fallback,
};

const cond = (entity) => ({
  condition: "state",
  entity_id: entity,
  state: "on",
});
const base = {
  triggers: [{ trigger: "state", entity_id: "light.a", to: "on" }],
  actions: [{ service: "light.turn_on" }],
};

describe("flow chart condition grouping", () => {
  it("flattens a top-level AND (implicit all-of context)", () => {
    const out = flatten(
      renderAutomationFlowchart(host, {
        ...base,
        conditions: [
          {
            condition: "and",
            conditions: [cond("light.a"), cond("light.b")],
          },
        ],
      }),
    );
    expect(out).not.toContain("All of the following:");
    expect(out).toContain("Light A");
    expect(out).toContain("Light B");
  });

  it("keeps an explicit AND group when nested under OR", () => {
    // (A and B) or C must NOT read as "any of A, B, C".
    const out = flatten(
      renderAutomationFlowchart(host, {
        ...base,
        conditions: [
          {
            condition: "or",
            conditions: [
              {
                condition: "and",
                conditions: [cond("light.a"), cond("light.b")],
              },
              cond("light.c"),
            ],
          },
        ],
      }),
    );
    expect(out).toContain("Any of the following:");
    expect(out).toContain("All of the following:");
  });

  it("keeps an explicit AND group when nested under NOT", () => {
    // not(A and B) must read as "none of [all of A, B]", not flatten.
    const out = flatten(
      renderAutomationFlowchart(host, {
        ...base,
        conditions: [
          {
            condition: "not",
            conditions: [
              {
                condition: "and",
                conditions: [cond("light.a"), cond("light.b")],
              },
            ],
          },
        ],
      }),
    );
    expect(out).toContain("None of the following:");
    expect(out).toContain("All of the following:");
  });
});
