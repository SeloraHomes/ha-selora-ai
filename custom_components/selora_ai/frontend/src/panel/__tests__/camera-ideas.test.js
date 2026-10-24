import { describe, expect, it, vi } from "vitest";

import { describeIdea, ideaMessage, renderIdeaChips } from "../camera-ideas.js";
import { messageDetails, stripEntityMarkers } from "../chat-autocomplete.js";
import { renderHomeSuggestions } from "../home-suggestions.js";

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

const AWAY = {
  idea_id: "idea:person_away:camera.e1_zoom",
  kind: "person_away",
  camera: { entity_id: "camera.e1_zoom", name: "E1 Zoom" },
  facts: {
    trigger: "binary_sensor.e1_zoom_person_occupancy",
    presence: "zone.home",
    presence_empty: "0",
    notify: "notify.mobile_app_pixel",
    image: "/api/camera_proxy/camera.e1_zoom",
  },
};

const idea = (kind, trigger) => ({
  idea_id: `idea:${kind}:camera.e1_zoom`,
  kind,
  camera: { entity_id: "camera.e1_zoom", name: "E1 Zoom" },
  facts: { trigger, notify: "notify.mobile_app_pixel", light: "light.garden" },
});

const host = (ideas = []) => ({
  hass: {
    language: "en",
    callWS: vi.fn(async () => ({ ideas })),
  },
  _t: (_key, fallback) => fallback,
  _quickStart: vi.fn(),
  _askInNewChat: vi.fn(),
  requestUpdate: vi.fn(),
});

describe("camera ideas", () => {
  it("words an idea with the camera's name", () => {
    const d = describeIdea(host(), AWAY);

    expect(d.title).toBe(
      "Alert me when someone is at E1 Zoom while nobody is home",
    );
    expect(d.chip).toBe("Alert me when away");
  });

  it("asks for the snapshot only when there is one to attach", () => {
    const withImage = messageDetails(ideaMessage(host(), AWAY));
    const { image, ...withoutImageFacts } = AWAY.facts;
    const without = messageDetails(
      ideaMessage(host(), {
        ...AWAY,
        facts: {
          ...withoutImageFacts,
          notify: "persistent_notification.create",
        },
      }),
    );

    expect(withImage).toContain(`data image ${image}`);
    expect(without).not.toContain("data image");
    expect(without).toContain("persistent_notification.create");
  });

  it("drops a kind this panel does not know", () => {
    expect(describeIdea(host(), { ...AWAY, kind: "teleport" })).toBeNull();
  });

  it("asks with the title, the exact entities folded away", () => {
    const message = ideaMessage(host(), AWAY);

    expect(stripEntityMarkers(message)).toBe(
      "Alert me when someone is at E1 Zoom while nobody is home",
    );
    const details = messageDetails(message);
    for (const id of [
      "binary_sensor.e1_zoom_person_occupancy",
      "zone.home",
      "notify.mobile_app_pixel",
      "/api/camera_proxy/camera.e1_zoom",
    ]) {
      expect(details).toContain(id);
    }
  });

  it("asks the backend once, then shows up to three chips that ask in this chat", async () => {
    const h = host([
      AWAY,
      idea("person_night_light", "binary_sensor.e1_zoom_person_occupancy"),
      idea("animal", "binary_sensor.e1_zoom_dog_occupancy"),
      idea("vehicle", "binary_sensor.e1_zoom_car_occupancy"),
    ]);

    expect(renderIdeaChips(h, "camera.e1_zoom")).toBe("");
    await vi.waitFor(() => expect(h.requestUpdate).toHaveBeenCalled());
    const chips = renderIdeaChips(h, "camera.e1_zoom");
    renderIdeaChips(h, "camera.e1_zoom");

    expect(h.hass.callWS).toHaveBeenCalledTimes(1);
    expect(h.hass.callWS).toHaveBeenCalledWith({
      type: "selora_ai/frigate/ideas",
      camera_entity: "camera.e1_zoom",
    });
    const out = flatten(chips);
    expect(out).toContain("Alert me when away");
    expect(out).toContain("Light up at night");
    expect(out).toContain("Animal alert");
    expect(out).not.toContain("Car alert");

    handlers(chips)[0]();
    expect(h._quickStart).toHaveBeenCalledWith(ideaMessage(h, AWAY));
  });

  it("shows nothing for a camera with no ideas", async () => {
    const h = host([]);
    renderIdeaChips(h, "camera.quiet");
    await vi.waitFor(() => expect(h.requestUpdate).toHaveBeenCalled());

    expect(renderIdeaChips(h, "camera.quiet")).toBe("");
  });

  it("is a chat home suggestion too", () => {
    const h = host();
    h._homeSuggestions = [
      {
        suggestion_id: AWAY.idea_id,
        type: "camera_idea",
        entities: [AWAY.camera],
        collection: { source: "camera", name: "E1 Zoom", ref: "" },
        idea: AWAY,
      },
    ];

    expect(flatten(renderHomeSuggestions(h))).toContain(
      "Alert me when someone is at E1 Zoom while nobody is home",
    );
  });
});
