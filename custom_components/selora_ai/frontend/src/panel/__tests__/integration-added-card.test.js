import { describe, expect, it } from "vitest";

import { renderIntegrationAddedCard } from "../integration-added-card.js";

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

const host = { _t: (_key, fallback) => fallback };

function reply(result) {
  return {
    timestamp: new Date().toISOString(),
    tool_calls: [
      { tool: "discover_network_devices", result: {} },
      { tool: "add_integration", arguments: {}, result },
    ],
  };
}

const KITCHEN = {
  status: "added",
  name: "Apple TV",
  domain: "apple_tv",
  title: "Kitchen",
  state: "loaded",
  devices: 1,
  entities: 2,
};

describe("integration added card", () => {
  it("ticks what the setup added, with what it brought in", () => {
    const out = flatten(renderIntegrationAddedCard(host, reply(KITCHEN)));
    expect(out).toContain("created-check");
    expect(out).toContain("Added to Home Assistant");
    expect(out).toContain("Kitchen");
    expect(out).toContain("Apple TV");
    expect(out).toContain("Devices: 1 · Entities: 2");
  });

  it("does not tick an entry Home Assistant could not start", () => {
    const out = flatten(
      renderIntegrationAddedCard(
        host,
        reply({
          ...KITCHEN,
          state: "setup_retry",
          reason: "Device unreachable",
        }),
      ),
    );
    expect(out).not.toContain("created-check");
    expect(out).toContain("Device unreachable");
  });

  it("shows nothing for a step still waiting", () => {
    expect(
      renderIntegrationAddedCard(host, reply({ status: "needs_user" })),
    ).toBe("");
  });
});
