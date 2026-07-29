import { describe, it, expect, vi } from "vitest";
import { _acceptScene } from "../scene-actions.js";

function makeHost() {
  const callWS = vi.fn(async ({ type }) => {
    if (type === "selora_ai/accept_scene")
      return { scene_id: "scene_new", entity_id: "scene.movie_night_2" };
    return {};
  });
  const host = {
    hass: { callWS },
    _activeSessionId: "s1",
    _messages: [{ scene: { name: "Movie Night" }, scene_message_index: 0 }],
    _loadScenes: vi.fn().mockResolvedValue(undefined),
    _markJustCreated: vi.fn(),
    _markSceneCreated: vi.fn(),
    _showToast: vi.fn(),
    _t: (_key, fallback) => fallback,
  };
  return { host, callWS };
}

describe("_acceptScene", () => {
  it("records the backend-resolved ids on the message", async () => {
    const { host } = makeHost();
    await _acceptScene.call(host, 0);
    expect(host._messages[0].scene_status).toBe("saved");
    expect(host._messages[0].scene_id).toBe("scene_new");
    // HA may collision-suffix the entity id, so Activate must use what the
    // backend returned rather than scene.<scene_id>.
    expect(host._messages[0].entity_id).toBe("scene.movie_night_2");
  });

  it("drives both creation animations off the new scene id", async () => {
    const { host } = makeHost();
    await _acceptScene.call(host, 0);
    // Drawn checkmark on the chat card...
    expect(host._markJustCreated).toHaveBeenCalledWith("scene_new");
    // ...and the row highlight, armed for whenever the Scenes tab renders.
    expect(host._markSceneCreated).toHaveBeenCalledWith("scene_new");
    expect(host._showToast).toHaveBeenCalledWith(
      expect.stringContaining("Movie Night"),
      "success",
    );
  });

  it("arms the highlight only after the scene list reloads", async () => {
    const { host } = makeHost();
    const order = [];
    host._loadScenes = vi.fn(async () => order.push("load"));
    host._markSceneCreated = vi.fn(() => order.push("mark"));
    await _acceptScene.call(host, 0);
    // Marking before the reload would target a row that isn't in the list yet.
    expect(order).toEqual(["load", "mark"]);
  });

  it("reports failure instead of animating", async () => {
    const { host } = makeHost();
    host.hass.callWS = vi.fn().mockRejectedValue(new Error("boom"));
    await _acceptScene.call(host, 0);
    expect(host._markJustCreated).not.toHaveBeenCalled();
    expect(host._markSceneCreated).not.toHaveBeenCalled();
    expect(host._showToast).toHaveBeenCalledWith(
      expect.stringContaining("boom"),
      "error",
    );
  });
});
