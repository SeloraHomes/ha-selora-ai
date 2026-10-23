import { afterEach, describe, expect, it, vi } from "vitest";

import { scanCameras, syncDevConsole } from "../dev-tools.js";

const host = (developerMode) => ({
  _config: { developer_mode: developerMode },
  hass: {
    callWS: vi.fn(async (msg) =>
      msg.type === "selora_ai/frigate/scan"
        ? { cameras: [{ name: "Reolink Lumus Pro", host: "192.168.2.178" }] }
        : [],
    ),
  },
});

describe("developer tools", () => {
  afterEach(() => {
    delete globalThis.seloraDev;
  });

  it("scans, then reloads the chat home's suggestions", async () => {
    const h = host(true);

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
    const h = host(true);

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

  it("offers the console helper only in developer mode, and says so once", () => {
    const info = vi.spyOn(console, "info").mockImplementation(() => {});

    syncDevConsole(host(true));
    syncDevConsole(host(true));
    expect(typeof globalThis.seloraDev.scanCameras).toBe("function");
    expect(info).toHaveBeenCalledTimes(1);
    expect(info.mock.calls[0][0]).toContain("seloraDev.scanCameras()");
    expect(info.mock.calls[0][0]).toContain("{ sweep: true }");

    syncDevConsole(host(false));
    expect(globalThis.seloraDev).toBeUndefined();
    info.mockRestore();
  });
});
