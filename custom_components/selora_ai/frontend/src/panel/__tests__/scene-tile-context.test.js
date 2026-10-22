import { describe, expect, it, vi } from "vitest";

import { provideSceneTileContext } from "../scene-tile-context.js";

// The tests run in node, so the grid is a bare EventTarget and the request is
// built the way @lit/context's ContextRequestEvent carries it.
function request(target, context, { subscribe = false } = {}) {
  const calls = [];
  const ev = new Event("context-request", { bubbles: true, composed: true });
  ev.context = context;
  ev.subscribe = subscribe;
  ev.callback = (value, dispose) => calls.push({ value, dispose });
  target.dispatchEvent(ev);
  return { ev, calls };
}

function forcedHass(brightness, callService = vi.fn()) {
  return {
    states: { "light.office": { state: "on", attributes: { brightness } } },
    callService,
    callWS: vi.fn(),
  };
}

describe("a scene grid answers its tiles' context requests", () => {
  // HA card features read state and callService from context, not from the
  // hass handed to the card: without this the slider showed and drove the
  // live light while the tile's label showed the scene's value.
  it("gives the features the pinned states, not the live ones", () => {
    const grid = new EventTarget();
    provideSceneTileContext(grid, forcedHass(74));
    const { calls } = request(grid, "states");
    expect(calls[0].value["light.office"].attributes.brightness).toBe(74);
  });

  it("routes a feature's service call to the grid's callService", () => {
    const grid = new EventTarget();
    const intercepted = vi.fn();
    provideSceneTileContext(grid, forcedHass(74, intercepted));
    const { calls } = request(grid, "hassApi");
    calls[0].value.callService("light", "turn_on", { brightness_pct: 85 });
    expect(intercepted).toHaveBeenCalledWith("light", "turn_on", {
      brightness_pct: 85,
    });
  });

  it("stops the request so HA's root provider never answers it", () => {
    const grid = new EventTarget();
    provideSceneTileContext(grid, forcedHass(74));
    const { ev } = request(grid, "states");
    expect(ev.cancelBubble).toBe(true);
  });

  it("leaves every other context to HA", () => {
    const grid = new EventTarget();
    provideSceneTileContext(grid, forcedHass(74));
    const { ev, calls } = request(grid, "hassInternationalization");
    expect(calls).toHaveLength(0);
    expect(ev.cancelBubble).toBe(false);
  });

  it("pushes an edited target to subscribers with a stable disposer", () => {
    // HA's consumer drops a subscription whose disposer changes between
    // callbacks, so an edit has to arrive with the original one.
    const grid = new EventTarget();
    provideSceneTileContext(grid, forcedHass(74));
    const { calls } = request(grid, "states", { subscribe: true });
    provideSceneTileContext(grid, forcedHass(217));
    expect(calls).toHaveLength(2);
    expect(calls[1].value["light.office"].attributes.brightness).toBe(217);
    expect(calls[1].dispose).toBe(calls[0].dispose);
  });

  it("stops pushing once the subscriber disposes", () => {
    const grid = new EventTarget();
    provideSceneTileContext(grid, forcedHass(74));
    const { calls } = request(grid, "states", { subscribe: true });
    calls[0].dispose();
    provideSceneTileContext(grid, forcedHass(217));
    expect(calls).toHaveLength(1);
  });
});
