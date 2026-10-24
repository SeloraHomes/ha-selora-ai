import { describe, expect, it, vi } from "vitest";

import { renderFrigateResult } from "../render-frigate-result.js";

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

const CAMERA = {
  name: "E1 Zoom",
  frigate_name: "e1_zoom",
  camera_entity: "camera.e1_zoom",
  recordings_entity: "switch.e1_zoom_recordings",
};

const message = (action, minutesAgo = 0) => ({
  role: "assistant",
  content: "Added E1 Zoom to Frigate.",
  timestamp: new Date(Date.now() - minutesAgo * 60000).toISOString(),
  frigate_result: {
    action,
    cameras: [CAMERA],
    objects: ["person"],
    record: true,
    record_days: 7,
  },
});

const host = (states = {}) => ({
  hass: { states },
  _t: (_key, fallback) => fallback,
  dispatchEvent: vi.fn(),
});

describe("the Frigate result card", () => {
  it("renders nothing for an ordinary message", () => {
    expect(renderFrigateResult(host(), { content: "hi" })).toBe("");
  });

  it("waits for Frigate while the camera is not in Home Assistant yet", () => {
    const out = flatten(renderFrigateResult(host(), message("added")));

    expect(out).toContain("Added to Frigate");
    expect(out).toContain("Frigate is restarting");
    expect(out).toContain("spinner");
    expect(out).not.toContain("camera_proxy");
  });

  it("says so when the wait is longer than a restart", () => {
    const out = flatten(renderFrigateResult(host(), message("added", 4)));

    expect(out).toContain("taking longer than usual");
  });

  it("shows the snapshot and the camera's controls as tiles once it is there", () => {
    const h = host({
      "camera.e1_zoom": {
        state: "streaming",
        attributes: {
          entity_picture: "/api/camera_proxy/camera.e1_zoom?token=abc",
        },
      },
      "switch.e1_zoom_recordings": { state: "on" },
      "switch.e1_zoom_detect": { state: "on" },
      "binary_sensor.e1_zoom_person_occupancy": { state: "off" },
    });

    const out = flatten(renderFrigateResult(h, message("added")));

    expect(out).toMatch(
      /src=\/api\/camera_proxy\/camera\.e1_zoom\?token=abc&t=\d+/,
    );
    expect(out).toContain(
      "data-entity-ids=switch.e1_zoom_recordings,switch.e1_zoom_detect,binary_sensor.e1_zoom_person_occupancy",
    );
    // What the tiles already say is not repeated as text.
    expect(out).not.toContain("Detects person");
    // Short names and icons that say what each one is, not HA's house.
    const options = JSON.parse(out.match(/data-tile-options=(\{.*?\}\})/)[1]);
    expect(options).toEqual({
      "switch.e1_zoom_recordings": {
        icon: "mdi:filmstrip-box",
        name: "Recording",
      },
      "switch.e1_zoom_detect": { icon: "mdi:motion-sensor", name: "Detection" },
      "binary_sensor.e1_zoom_person_occupancy": {
        icon: "mdi:account",
        name: "Person",
      },
    });
    expect(out).not.toContain("Frigate is restarting");
  });

  it("opens the camera's live view from its name and its snapshot", () => {
    const h = host({
      "camera.e1_zoom": {
        state: "streaming",
        attributes: {
          entity_picture: "/api/camera_proxy/camera.e1_zoom?token=abc",
        },
      },
    });

    const opens = handlers(renderFrigateResult(h, message("added")));
    expect(opens).toHaveLength(2);
    for (const open of opens) open({ preventDefault: () => {} });

    for (const [event] of h.dispatchEvent.mock.calls) {
      expect(event.type).toBe("hass-more-info");
      expect(event.detail).toEqual({ entityId: "camera.e1_zoom" });
    }
  });

  it("says a camera Frigate could not restart for starts later", () => {
    const out = flatten(renderFrigateResult(host(), message("saved")));

    expect(out).toContain("Saved in Frigate");
    expect(out).toContain("starts with Frigate's next restart");
    expect(out).not.toContain("spinner");
  });

  it("confirms a removal without a preview", () => {
    const out = flatten(renderFrigateResult(host(), message("removed")));

    expect(out).toContain("Removed from Frigate");
    expect(out).toContain("E1 Zoom");
    expect(out).not.toContain("<img");
  });

  it("waits for Frigate's camera, not one that already had its id", () => {
    // HA had camera.e1_zoom (the camera Frigate was given), so Frigate's
    // entity became camera.e1_zoom_2.
    const states = {
      "camera.e1_zoom": {
        state: "streaming",
        attributes: {
          entity_picture: "/api/camera_proxy/camera.e1_zoom?token=reolink",
        },
      },
    };
    const entities = { "camera.e1_zoom": { platform: "reolink" } };
    const waiting = host(states);
    waiting.hass.entities = entities;

    expect(flatten(renderFrigateResult(waiting, message("added")))).toContain(
      "Frigate is restarting",
    );

    const ready = host({
      ...states,
      "camera.e1_zoom_2": {
        state: "streaming",
        attributes: {
          entity_picture: "/api/camera_proxy/camera.e1_zoom_2?token=frigate",
        },
      },
    });
    ready.hass.entities = {
      ...entities,
      "camera.e1_zoom_2": { platform: "frigate" },
    };

    const out = flatten(renderFrigateResult(ready, message("added")));
    expect(out).toContain("camera.e1_zoom_2?token=frigate");
    expect(out).not.toContain("token=reolink");
  });
});
