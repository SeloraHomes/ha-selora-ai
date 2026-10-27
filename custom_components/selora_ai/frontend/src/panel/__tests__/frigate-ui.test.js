import { afterEach, describe, expect, it, vi } from "vitest";

import {
  eventPage,
  frigateApp,
  openFrigatePage,
  zonesPage,
} from "../frigate-ui.js";

const PANELS = {
  lovelace: { component_name: "lovelace", config: {} },
  ccab4aaf_frigate_proxy: {
    component_name: "app",
    url_path: "ccab4aaf_frigate-proxy",
    config: { addon: "ccab4aaf_frigate-proxy" },
  },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Frigate's own UI", () => {
  it("is found by the app's sidebar panel, old or new", () => {
    expect(frigateApp({ panels: PANELS })).toBe("ccab4aaf_frigate-proxy");
    expect(
      frigateApp({
        panels: { x: { config: { ingress: "ccab4aaf_frigate" } } },
      }),
    ).toBe("ccab4aaf_frigate");
    expect(frigateApp({ panels: { lovelace: PANELS.lovelace } })).toBeNull();
    expect(frigateApp(undefined)).toBeNull();
  });

  it("links to Frigate 0.18's own pages", () => {
    expect(eventPage("1793061055.1-ays28t")).toBe(
      "explore?event_id=1793061055.1-ays28t",
    );
    expect(zonesPage("wyze_front")).toBe(
      "settings?page=masksAndZones&camera=wyze_front",
    );
  });

  it("opens the page through ingress with a session, like HA's frontend", async () => {
    const tab = { location: { href: "" }, close: vi.fn() };
    vi.stubGlobal("window", { open: vi.fn(() => tab) });
    vi.stubGlobal("document", { cookie: "" });
    vi.stubGlobal("location", { protocol: "https:" });
    const callWS = vi.fn(async ({ endpoint }) =>
      endpoint === "/ingress/session"
        ? { session: "s3cr3t" }
        : { ingress_entry: "/api/hassio_ingress/TOKEN" },
    );

    await openFrigatePage(
      { hass: { panels: PANELS, callWS } },
      zonesPage("wyze_front"),
    );

    // Opened during the click, before any await, or it is a blocked pop-up.
    expect(window.open).toHaveBeenCalledWith("", "_blank");
    expect(callWS).toHaveBeenCalledWith({
      type: "supervisor/api",
      endpoint: "/addons/ccab4aaf_frigate-proxy/info",
      method: "get",
    });
    expect(document.cookie).toBe(
      "ingress_session=s3cr3t;path=/api/hassio_ingress/;SameSite=Strict;Secure",
    );
    expect(tab.location.href).toBe(
      "/api/hassio_ingress/TOKEN/settings?page=masksAndZones&camera=wyze_front",
    );
  });

  it("closes the tab when the app cannot be reached", async () => {
    const tab = { location: { href: "" }, close: vi.fn() };
    vi.stubGlobal("window", { open: vi.fn(() => tab) });
    vi.stubGlobal("document", { cookie: "" });
    vi.spyOn(console, "error").mockImplementation(() => {});
    const callWS = vi.fn(async () => {
      throw new Error("supervisor down");
    });

    await openFrigatePage({ hass: { panels: PANELS, callWS } }, "explore");

    expect(tab.close).toHaveBeenCalled();
    expect(tab.location.href).toBe("");
  });
});
