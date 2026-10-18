import { describe, expect, it, vi } from "vitest";

import { loadTrash, renderTrashList, renderTrashTab } from "../trash.js";

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

const ITEM = {
  trash_id: "t1",
  kind: "automation",
  name: "Garage Night Spots",
  deleted_at: "2026-10-18T03:21:16+00:00",
  expires_at: "2026-11-17T03:21:16+00:00",
};

function makeHost(callWS) {
  return {
    _t: (key, fallback) => fallback,
    hass: { language: "en", callWS: vi.fn(callWS) },
    _showToast: vi.fn(),
    _loadAutomations: vi.fn(async () => {}),
    _loadScenes: vi.fn(async () => {}),
  };
}

// The handlers a rendered row binds, found by the button they sit on.
function clickHandler(template, label) {
  const strings = template.values.find((v) => Array.isArray(v))[0];
  const idx = strings.strings.findIndex((s) => s.includes(label));
  return strings.values.slice(idx).find((v) => typeof v === "function");
}

describe("Recently deleted", () => {
  it("lists only the tab's own kind, and has no tab when empty", async () => {
    const host = makeHost(async () => [ITEM, { ...ITEM, kind: "scene" }]);
    await loadTrash(host);
    const tab = ser(renderTrashTab(host, "automation", false, () => {}));
    expect(tab).toContain("Recently deleted (1)");
    const out = ser(renderTrashList(host, "automation"));
    expect(out).toContain("Garage Night Spots");
    host._trash = [];
    expect(renderTrashTab(host, "scene", false, () => {})).toBe("");
  });

  it("an older backend without the command shows nothing", async () => {
    const host = makeHost(async () => {
      throw new Error("unknown command");
    });
    await loadTrash(host);
    expect(host._trash).toEqual([]);
  });

  it("a restore puts it back and reloads the tab", async () => {
    const host = makeHost(async () => ({ kind: "automation" }));
    host._trash = [ITEM];
    await clickHandler(renderTrashList(host, "automation"), 'class="btn')();
    expect(host.hass.callWS).toHaveBeenCalledWith({
      type: "selora_ai/trash/restore",
      trash_id: "t1",
    });
    expect(host._showToast).toHaveBeenCalledWith(
      "Restored “Garage Night Spots”.",
      "success",
    );
    expect(host._loadAutomations).toHaveBeenCalled();
  });

  it("says why when the id is taken again", async () => {
    const host = makeHost(async () => {
      throw { code: "in_use", message: "in_use" };
    });
    host._trash = [ITEM];
    await clickHandler(renderTrashList(host, "automation"), 'class="btn')();
    expect(host._showToast).toHaveBeenCalledWith(
      "Couldn't restore “Garage Night Spots”: another one now uses its id.",
      "error",
    );
  });
});
