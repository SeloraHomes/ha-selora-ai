import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  catalogUrlOverride,
  debugEnabled,
  installDevConsole,
  logChatTurn,
  scanCameras,
} from "../dev-tools.js";

function memoryStorage() {
  const data = new Map();
  return {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
    removeItem: (k) => data.delete(k),
  };
}

const host = () => ({
  _config: { selora_connect_url: "https://connect.selorahomes.com" },
  _loadConfig: vi.fn(async () => {}),
  _loadRecipesCatalog: vi.fn(),
  _triggerPatternScan: vi.fn(async () => {}),
  _triggerGenerateSuggestions: vi.fn(async () => {}),
  hass: {
    callWS: vi.fn(async (msg) =>
      msg.type === "selora_ai/frigate/scan"
        ? { cameras: [{ name: "Reolink Lumus Pro", host: "192.168.2.178" }] }
        : [],
    ),
  },
});

describe("developer console", () => {
  let info;
  let original;

  beforeEach(() => {
    original = globalThis.localStorage;
    globalThis.localStorage = memoryStorage();
    info = vi.spyOn(console, "info").mockImplementation(() => {});
    vi.spyOn(console, "table").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
    globalThis.localStorage = original;
  });

  it("scans, then reloads the chat home's suggestions", async () => {
    const h = host();

    const cameras = await scanCameras(h);

    expect(cameras).toEqual([
      { name: "Reolink Lumus Pro", host: "192.168.2.178" },
    ]);
    expect(h.hass.callWS.mock.calls.map((c) => c[0].type)).toEqual([
      "selora_ai/frigate/scan",
      "selora_ai/home_suggestions",
    ]);
  });

  it("asks for the direct sweep only when told to", async () => {
    const h = host();

    await scanCameras(h, { sweep: true });
    await scanCameras(h);

    const scans = h.hass.callWS.mock.calls
      .map((c) => c[0])
      .filter((m) => m.type === "selora_ai/frigate/scan");
    expect(scans).toEqual([
      { type: "selora_ai/frigate/scan", sweep: true },
      { type: "selora_ai/frigate/scan" },
    ]);
  });

  it("installs `selora` for every panel, and announces it once", () => {
    installDevConsole(host());
    installDevConsole(host());

    expect(typeof globalThis.selora.scanCameras).toBe("function");
    expect(info).toHaveBeenCalledTimes(1);
    expect(info.mock.calls[0][0]).toContain("██████");
    expect(info.mock.calls[0][0]).toContain("selora.scanCameras()");
    expect(info.mock.calls[0][0]).toContain("selora.debug(");

    globalThis.selora.help();
    expect(info).toHaveBeenCalledTimes(2);
  });

  it("drives the panel it was installed by", async () => {
    const h = host();
    installDevConsole(h);

    await globalThis.selora.scanPatterns();
    await globalThis.selora.generateSuggestions();

    expect(h._triggerPatternScan).toHaveBeenCalledOnce();
    expect(h._triggerGenerateSuggestions).toHaveBeenCalledOnce();
  });

  it("sets and resets the recipes catalog URL, refetching each time", () => {
    const h = host();
    installDevConsole(h);

    globalThis.selora.catalogUrl(" http://localhost:1313/api/recipes.json ");
    expect(catalogUrlOverride()).toBe("http://localhost:1313/api/recipes.json");

    expect(globalThis.selora.catalogUrl("")).toBe("(default)");
    expect(catalogUrlOverride()).toBe("");
    expect(h._loadRecipesCatalog).toHaveBeenCalledTimes(2);
  });

  it("saves the Selora Connect URL and reloads the config", async () => {
    const h = host();
    installDevConsole(h);

    await globalThis.selora.connectUrl("http://192.168.2.10:8080");

    expect(h.hass.callWS).toHaveBeenCalledWith({
      type: "selora_ai/update_config",
      config: { selora_connect_url: "http://192.168.2.10:8080" },
    });
    expect(h._loadConfig).toHaveBeenCalledOnce();
  });

  it("logs chat turns only after selora.debug(true)", () => {
    installDevConsole(host());
    const group = vi
      .spyOn(console, "groupCollapsed")
      .mockImplementation(() => {});
    vi.spyOn(console, "log").mockImplementation(() => {});
    vi.spyOn(console, "groupEnd").mockImplementation(() => {});
    const event = {
      tool_calls: [{ tool: "search_entities", arguments: { query: "lamp" } }],
    };

    logChatTurn("Done.", event, 1200);
    expect(group).not.toHaveBeenCalled();

    globalThis.selora.debug(true);
    expect(debugEnabled()).toBe(true);
    logChatTurn("Done.", event, 1200);
    expect(group.mock.calls[0][0]).toContain("1200 ms");
    expect(group.mock.calls[0][0]).toContain("1 tool call(s)");

    globalThis.selora.debug(false);
    expect(debugEnabled()).toBe(false);
  });
});
