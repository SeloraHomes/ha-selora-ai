import { describe, expect, it, vi } from "vitest";

import { renderCameraEventsCard } from "../camera-events-card.js";

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
  if (typeof node === "function") return "";
  return String(node);
}

function handlers(node, out = []) {
  if (Array.isArray(node)) node.forEach((n) => handlers(n, out));
  else if (typeof node === "function") out.push(node);
  else if (node && typeof node === "object" && "values" in node) {
    handlers(node.values, out);
  }
  return out;
}

const PORCH = {
  id: "1792887092.349239-pcgr5a",
  camera: "front door",
  camera_name: "Front Door",
  label: "person",
  sub_label: "Alice",
  start: "2026-10-24T14:31:32+02:00",
  seconds: 9,
  has_snapshot: true,
  has_clip: true,
};
const NOW = {
  id: "1792887200.5-abc123",
  camera: "drive",
  camera_name: "Drive",
  label: "car",
  start: "2026-10-24T14:33:20+02:00",
  in_progress: true,
  has_snapshot: true,
  has_clip: true,
};

const host = () => ({
  hass: {
    language: "en",
    callWS: vi.fn(async ({ path }) => ({ path: `${path}&authSig=x` })),
  },
  _t: (_key, fallback) => fallback,
  requestUpdate: vi.fn(),
});

const message = (timestamp, events, total) => ({
  role: "assistant",
  content: "Alice was at the front door.",
  timestamp,
  tool_calls: [
    { tool: "search_entities", result: { entities: [] } },
    { tool: "find_camera_events", result: { events, total } },
    { tool: "find_camera_events", result: { events: [events[0]] } },
  ],
});

describe("the camera events card", () => {
  it("shows nothing for a reply that looked up no events", () => {
    expect(renderCameraEventsCard(host(), { tool_calls: [] })).toBe("");
  });

  it("signs each snapshot once, then shows it", async () => {
    const h = host();
    const msg = message("t1", [PORCH, NOW], 5);

    const first = flatten(renderCameraEventsCard(h, msg));
    await vi.waitFor(() => expect(h.requestUpdate).toHaveBeenCalledTimes(2));
    const out = flatten(renderCameraEventsCard(h, msg));

    expect(first).not.toContain("authSig");
    expect(h.hass.callWS).toHaveBeenCalledTimes(2);
    expect(h.hass.callWS).toHaveBeenCalledWith({
      type: "auth/sign_path",
      path: "/api/frigate/snapshot/1792887092.349239-pcgr5a?h=240&crop=1",
      expires: 3600,
    });
    expect(out).toContain("authSig=x");
    expect(out).toContain("Person · Alice · Front Door");
    expect(out).toContain("Still going");
    expect(out).toContain("3 more in this time range.");
    // The second search listed only an event already shown.
    expect(out.match(/more in this time range/g)).toHaveLength(1);
    expect(out.match(/class="camera-event"/g)).toHaveLength(2);
  });

  it("shows the event's thumbnail on a camera with snapshots off", async () => {
    // Frigate keeps a thumbnail for every event; a snapshot only when the
    // camera's snapshots are on, which a camera added from chat leaves off.
    // Through the integration's instance route, never its notification
    // views, which can be switched off.
    const h = host();
    const car = {
      ...NOW,
      id: "1793063296.945653-2oc391",
      in_progress: false,
      has_snapshot: false,
    };
    const msg = {
      timestamp: "t-thumb",
      tool_calls: [
        {
          tool: "find_camera_events",
          result: { events: [car], instance: "frigate" },
        },
      ],
    };

    renderCameraEventsCard(h, msg);
    await vi.waitFor(() => expect(h.requestUpdate).toHaveBeenCalled());
    const out = flatten(renderCameraEventsCard(h, msg));

    expect(h.hass.callWS).toHaveBeenCalledWith({
      type: "auth/sign_path",
      path: "/api/frigate/frigate/thumbnail/1793063296.945653-2oc391",
      expires: 3600,
    });
    expect(out).toContain("thumbnail/1793063296.945653-2oc391&authSig=x");
    expect(out).not.toContain("notifications");
  });

  it("encodes an instance id and refuses one that would change the path", () => {
    const h = host();
    const car = { ...NOW, id: "1793063298.1-enc000", has_snapshot: false };
    const at = (instance, ts) => ({
      timestamp: ts,
      tool_calls: [
        { tool: "find_camera_events", result: { events: [car], instance } },
      ],
    });

    renderCameraEventsCard(h, at("frigate:garage", "t-enc"));
    expect(h.hass.callWS).toHaveBeenCalledWith(
      expect.objectContaining({
        path: "/api/frigate/frigate%3Agarage/thumbnail/1793063298.1-enc000",
      }),
    );
    h.hass.callWS.mockClear();
    for (const bad of ["..", "a/b", "a?b"]) {
      renderCameraEventsCard(h, at(bad, `t-${bad}`));
    }
    expect(h.hass.callWS).not.toHaveBeenCalled();
  });

  it("keeps the placeholder for a reply stored without an instance", () => {
    const h = host();
    const car = { ...NOW, id: "1793063297.1-old000", has_snapshot: false };
    const msg = message("t-noinstance", [car]);

    const out = flatten(renderCameraEventsCard(h, msg));

    expect(h.hass.callWS).not.toHaveBeenCalled();
    expect(out).toContain("mdi:cctv");
  });

  it("counts what each search left out on its own", () => {
    const msg = {
      timestamp: "t4",
      tool_calls: [
        { tool: "find_camera_events", result: { events: [PORCH], total: 4 } },
        { tool: "find_camera_events", result: { events: [NOW], total: 2 } },
      ],
    };

    const out = flatten(renderCameraEventsCard(host(), msg));

    expect(out).toContain("3 more in this time range.");
    expect(out).toContain("1 more in this time range.");
  });

  it("opens an event large and plays its recording", async () => {
    const h = host();
    const msg = message("t2", [PORCH], 1);

    handlers(renderCameraEventsCard(h, msg)).find((fn) => fn.length === 0)();
    renderCameraEventsCard(h, msg);
    await vi.waitFor(() =>
      expect(h.hass.callWS).toHaveBeenCalledWith(
        expect.objectContaining({
          path: "/api/frigate/recording/front%20door/start/1792887092/end/1792887103",
        }),
      ),
    );
    await vi.waitFor(() => {
      const out = flatten(renderCameraEventsCard(h, msg));
      expect(out).toContain("<video");
      expect(out).toContain("/api/frigate/recording/front%20door/");
    });
  });

  it("does not offer a clip for an event still going", () => {
    const out = flatten(renderCameraEventsCard(host(), message("t3", [NOW])));

    expect(out).not.toContain("mdi:play-circle");
  });
});

describe("links into Frigate's own UI", () => {
  const opened = (panels) => {
    const h = host();
    h.hass.panels = panels;
    const msg = message("t-links", [PORCH]);
    // Open the event, as a click on its tile does.
    for (const fn of handlers(renderCameraEventsCard(h, msg))) fn();
    return flatten(renderCameraEventsCard(h, msg));
  };

  it("offers the event and the camera's zones when an app serves Frigate", () => {
    const out = opened({
      frigate: { config: { addon: "ccab4aaf_frigate-proxy" } },
    });
    expect(out).toContain("Open in Frigate");
    expect(out).toContain("Zones");
  });

  it("offers nothing without one", () => {
    const out = opened({});
    expect(out).not.toContain("Open in Frigate");
  });
});
