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

// The listeners a template binds to `event`, found by the attribute before each.
function handlers(node, event = "click", out = []) {
  if (Array.isArray(node)) node.forEach((n) => handlers(n, event, out));
  else if (node && typeof node === "object" && "values" in node) {
    node.values.forEach((value, i) => {
      if (typeof value !== "function") handlers(value, event, out);
      else if (node.strings[i].trimEnd().endsWith(`@${event}=`))
        out.push(value);
    });
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

    expect(out).toContain("Added to Selora Vision");
    expect(out).toContain("Selora Vision is restarting");
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
    expect(out).not.toContain("Selora Vision is restarting");
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

  it("shows a placeholder, not a blank box, while the picture fails", () => {
    const h = host({
      "camera.e1_zoom": {
        state: "streaming",
        attributes: { entity_picture: "/api/camera_proxy/camera.e1_zoom" },
      },
    });
    const card = renderFrigateResult(h, message("added"));
    const preview = { classList: { toggle: vi.fn() } };
    const event = { target: { closest: () => preview } };

    expect(flatten(card)).toContain("No picture right now");
    handlers(card, "error")[0](event);
    expect(preview.classList.toggle).toHaveBeenLastCalledWith(
      "is-missing",
      true,
    );
    handlers(card, "load")[0](event);
    expect(preview.classList.toggle).toHaveBeenLastCalledWith(
      "is-missing",
      false,
    );
  });

  it("says a camera Frigate could not restart for starts later", () => {
    const out = flatten(renderFrigateResult(host(), message("saved")));

    expect(out).toContain("Saved in Selora Vision");
    expect(out).toContain("starts with Selora Vision's next restart");
    expect(out).not.toContain("spinner");
  });

  it("confirms a removal without a preview", () => {
    const out = flatten(renderFrigateResult(host(), message("removed")));

    expect(out).toContain("Removed from Selora Vision");
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
      "Selora Vision is restarting",
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

describe("a camera changed in Frigate", () => {
  const changed = (action, objects = ["bus", "car", "person"]) => ({
    role: "assistant",
    content: "Wyze Front now looks for bus, car, person.",
    timestamp: new Date().toISOString(),
    frigate_result: {
      action,
      cameras: [{ ...CAMERA, objects }],
    },
  });

  it("says what it looks for while Frigate restarts", () => {
    // The card replaces the message that said it, and the new sensors only
    // appear once Frigate is back.
    const out = flatten(renderFrigateResult(host(), changed("updated")));
    expect(out).toContain("Updated in Selora Vision");
    expect(out).toContain("Looks for");
    expect(out).toContain("Bus");
    expect(out).toContain("Car");
    expect(out).toContain("Person");
    expect(out).toContain("the camera is back in about a minute");
    expect(out).toContain("created-check drawing");
  });

  it("shows a tile for each object it looks for once it is back", () => {
    const ready = host({
      "camera.e1_zoom": {
        state: "idle",
        attributes: { entity_picture: "/api/camera_proxy/camera.e1_zoom" },
      },
      "switch.e1_zoom_recordings": { state: "on" },
      "switch.e1_zoom_detect": { state: "on" },
      "binary_sensor.e1_zoom_bus_occupancy": { state: "off" },
      "binary_sensor.e1_zoom_person_occupancy": { state: "off" },
    });
    const out = flatten(renderFrigateResult(ready, changed("updated")));
    expect(out).toContain(
      "data-entity-ids=switch.e1_zoom_recordings,switch.e1_zoom_detect,binary_sensor.e1_zoom_bus_occupancy,binary_sensor.e1_zoom_person_occupancy",
    );
  });

  it("says a change Frigate could not restart for takes effect later", () => {
    const out = flatten(renderFrigateResult(host(), changed("update_saved")));
    expect(out).toContain("Saved in Selora Vision");
    expect(out).toContain("it takes effect when Selora Vision restarts");
    expect(out).not.toContain("the camera is back");
  });

  it("names no objects for a camera that was only moved", () => {
    const out = flatten(renderFrigateResult(host(), changed("updated", [])));
    expect(out).toContain("Updated in Selora Vision");
    expect(out).not.toContain("Looks for");
  });
});
