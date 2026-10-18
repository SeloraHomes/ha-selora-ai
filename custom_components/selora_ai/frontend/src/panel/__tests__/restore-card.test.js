import { describe, expect, it, vi } from "vitest";

import { renderRestoreCard } from "../restore-card.js";

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

function automation(id, delay) {
  return {
    trash_id: `t-${id}`,
    kind: "automation",
    name: "Garage Night Spots",
    description: `Spots off after ${delay}`,
    deleted_at: "2026-10-18T03:21:16+00:00",
    expires_at: "2026-11-17T03:21:16+00:00",
  };
}

// What `selora_ai/trash/get` adds to a match.
function full(c, delay) {
  return {
    ...c,
    config: {
      alias: c.name,
      triggers: [{ trigger: "state", entity_id: "binary_sensor.garage" }],
      actions: [{ delay }],
    },
    yaml: `alias: ${c.name}\nactions:\n- delay: ${delay}\n`,
  };
}

const TWO = automation("a2", "00:02:00");
const THREE = automation("a3", "00:03:00");

function makeHost(overrides = {}) {
  return {
    _t: (key, fallback) => fallback,
    hass: {
      language: "en",
      states: {},
      callWS: vi.fn(async () => ({
        items: [full(TWO, "00:02:00"), full(THREE, "00:03:00")],
      })),
    },
    _renderYamlEditor: (key, yaml) => yaml,
    requestUpdate: vi.fn(),
    ...overrides,
  };
}

const msg = (candidates) => ({
  role: "assistant",
  tool_calls: [{ tool: "restore_deleted", result: { candidates } }],
});

describe("the restore card", () => {
  it("fetches each match in full, once", async () => {
    const host = makeHost();
    renderRestoreCard(host, msg([TWO, THREE]));
    renderRestoreCard(host, msg([TWO, THREE]));
    expect(host.hass.callWS).toHaveBeenCalledTimes(1);
    expect(host.hass.callWS).toHaveBeenCalledWith({
      type: "selora_ai/trash/get",
      trash_ids: ["t-a2", "t-a3"],
    });
    await vi.waitFor(() => expect(host._restoreDetails["t-a2"]).toBeTruthy());
  });

  it("shows what tells same-named matches apart", () => {
    const host = makeHost({
      _restoreDetails: {
        "t-a2": full(TWO, "00:02:00"),
        "t-a3": full(THREE, "00:03:00"),
      },
    });
    const out = ser(renderRestoreCard(host, msg([TWO, THREE])));
    expect(out).toContain("Spots off after 00:02:00");
    expect(out).toContain("Spots off after 00:03:00");
    expect(out).toContain("Deleted ");
    expect(out.match(/Restore/g).length).toBeGreaterThanOrEqual(2);
  });

  it("opens each match's YAML on its own", () => {
    const host = makeHost({
      _restoreDetails: { "t-a2": full(TWO, "00:02:00") },
      _yamlOpen: { "restore_t-a2": true },
    });
    expect(ser(renderRestoreCard(host, msg([TWO])))).toContain(
      "- delay: 00:02:00",
    );
  });

  it("says when a match was restored or is gone", () => {
    const host = makeHost({
      _restoreDetails: { "t-a2": full(TWO, "00:02:00"), "t-a3": null },
      _restoredTrash: new Set(["t-a2"]),
    });
    const out = ser(renderRestoreCard(host, msg([TWO, THREE])));
    expect(out).toContain("Restored");
    expect(out).toContain("No longer in Recently deleted");
  });

  it("renders nothing for any other reply", () => {
    const host = makeHost();
    expect(renderRestoreCard(host, { role: "assistant" })).toBe("");
    expect(renderRestoreCard(host, msg([]))).toBe("");
  });
});
