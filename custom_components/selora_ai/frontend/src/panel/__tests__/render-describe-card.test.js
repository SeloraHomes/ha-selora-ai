import { describe, expect, it, vi } from "vitest";

import { renderDescribeCard } from "../render-describe-card.js";

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

const QUESTION = "Should Selora AI describe what Garage sees?";
const INTRO = {
  title: "Camera descriptions",
  tagline: "Search what your cameras saw, in plain words.",
  lead: "Frigate tells you a car went by.",
  image: "/api/selora_ai/images/describe-example.jpg",
  seen: "Frigate sees",
  label: "Car",
  described: "Selora AI describes",
  example: "A white FedEx cargo van parked on the driveway.",
  points: [
    { icon: "mdi:magnify", text: "Then ask about it." },
    { icon: "mdi:shield-lock-outline", text: "Pictures are sent." },
  ],
};

const message = (intro) => ({
  role: "assistant",
  content: QUESTION,
  describe_card: {
    camera: "garage",
    camera_name: "Garage",
    step: "Camera 2 of 3",
    question: QUESTION,
    ...(intro ? { intro } : {}),
  },
});

const host = (states = {}) => ({
  hass: { states },
  _t: (_key, fallback) => fallback,
  dispatchEvent: vi.fn(),
});

describe("the camera-descriptions card", () => {
  it("renders nothing for an ordinary message", () => {
    expect(renderDescribeCard(host(), { content: "hi" })).toBe("");
  });

  it("opens with the example and what it means, then the question", () => {
    const out = flatten(renderDescribeCard(host(), message(INTRO)));
    expect(out).toContain(INTRO.image);
    expect(out).toContain("Camera descriptions");
    // A heading's line, short; the lead is the message text, for the model.
    expect(out).toContain("Search what your cameras saw, in plain words.");
    expect(out).not.toContain("Frigate tells you a car went by.");
    expect(out).toContain("card-heading-icon");
    expect(out).toContain("Frigate sees");
    expect(out).toContain("A white FedEx cargo van parked on the driveway.");
    expect(out).toContain("mdi:shield-lock-outline");
    expect(out.indexOf("Pictures are sent.")).toBeLessThan(
      out.indexOf(QUESTION),
    );
  });

  it("shows the camera's snapshot above its question, opening the camera", () => {
    const states = {
      "camera.garage": {
        state: "idle",
        attributes: { entity_picture: "/api/camera_proxy/camera.garage?t=1" },
      },
    };
    const h = host(states);
    const card = renderDescribeCard(h, message());
    const out = flatten(card);
    expect(out).toContain("/api/camera_proxy/camera.garage?t=1&t=");
    expect(out).not.toContain("Frigate sees");
    handlers(card).forEach((fn) => fn());
    expect(h.dispatchEvent.mock.calls[0][0].detail).toEqual({
      entityId: "camera.garage",
    });
  });

  it("asks without a picture when the camera is not in Home Assistant", () => {
    const out = flatten(renderDescribeCard(host(), message()));
    expect(out).not.toContain("<img");
    expect(out).toContain("Camera 2 of 3");
    expect(out).toContain(QUESTION);
  });

  it("ends with a tick that draws only when the reply is fresh", () => {
    const done = (minutesAgo) => ({
      role: "assistant",
      content: "That's every camera, and Frigate already runs your answers.",
      timestamp: new Date(Date.now() - minutesAgo * 60000).toISOString(),
      describe_card: { done: "saved" },
    });
    const fresh = flatten(renderDescribeCard(host(), done(0)));
    expect(fresh).toContain("created-check drawing");
    expect(fresh).toContain("pop-in");
    expect(fresh).toContain("Frigate already runs your answers.");
    const old = flatten(renderDescribeCard(host(), done(10)));
    expect(old).toContain("created-check");
    expect(old).not.toContain("drawing");
  });

  it("warns instead when Frigate could not restart", () => {
    const out = flatten(
      renderDescribeCard(host(), {
        content: "Your answers are saved, but Frigate could not restart.",
        describe_card: { done: "not_restarted" },
      }),
    );
    expect(out).toContain("mdi:alert-outline");
    expect(out).not.toContain("created-check");
  });
});
