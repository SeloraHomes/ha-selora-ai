import { describe, expect, it, vi } from "vitest";

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

// Every event handler in a template, in document order.
function handlers(node, out = []) {
  if (Array.isArray(node)) node.forEach((n) => handlers(n, out));
  else if (typeof node === "function") out.push(node);
  else if (node && typeof node === "object" && "values" in node) {
    handlers(node.values, out);
  }
  return out;
}

// Which page pressing the entry's name opens.
function opened(result) {
  const pushState = vi.fn();
  vi.stubGlobal("window", { history: { pushState }, dispatchEvent: vi.fn() });
  vi.stubGlobal("Event", class {});
  try {
    const [press] = handlers(renderIntegrationAddedCard(host, reply(result)));
    press({ stopPropagation() {} });
    return pushState.mock.calls.at(-1)?.[2];
  } finally {
    vi.unstubAllGlobals();
  }
}

const host = {
  _t: (_key, fallback) => fallback,
  hass: { states: { "media_player.samsung_q6": { state: "off" } } },
};

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

  it("shows the entities it added as tiles, those with a state yet", () => {
    const out = flatten(
      renderIntegrationAddedCard(
        host,
        reply({
          ...KITCHEN,
          entity_ids: ["media_player.samsung_q6", "sensor.not_written_yet"],
        }),
      ),
    );
    expect(out).toContain("selora-entity-grid");
    expect(out).toMatch(/data-entity-ids=\s*"?media_player\.samsung_q6"?\s*>/);
    expect(out).not.toContain("Devices: 1");
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

  it("opens the device it set up, in Home Assistant", () => {
    expect(opened({ ...KITCHEN, entry_id: "e1", device_id: "d1" })).toBe(
      "/config/devices/device/d1",
    );
  });

  it("opens the entry's page when it set up no single device", () => {
    expect(
      opened({ ...KITCHEN, domain: "dlna_dms", entry_id: "e1", devices: 0 }),
    ).toBe("/config/integrations/integration/dlna_dms#config_entry=e1");
  });
});
