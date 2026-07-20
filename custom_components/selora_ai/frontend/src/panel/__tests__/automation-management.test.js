import { describe, it, expect, vi } from "vitest";
import {
  _automationIsEnabled,
  _restoreVersion,
} from "../automation-management.js";

// _automationIsEnabled is exported as a standalone function but designed
// to be prototype-assigned.  For unit tests we can call it directly since
// it only reads the `automation` argument — no `this` needed.

describe("_automationIsEnabled", () => {
  it("returns true when state is 'on'", () => {
    expect(_automationIsEnabled({ state: "on" })).toBe(true);
  });

  it("returns false when state is 'off'", () => {
    expect(_automationIsEnabled({ state: "off" })).toBe(false);
  });

  it("returns false when state is 'unavailable'", () => {
    expect(_automationIsEnabled({ state: "unavailable" })).toBe(false);
  });

  it("returns false for unavailable even if persisted_enabled is true", () => {
    expect(
      _automationIsEnabled({ state: "unavailable", persisted_enabled: true }),
    ).toBe(false);
  });

  it("returns false for null automation", () => {
    expect(_automationIsEnabled(null)).toBe(false);
  });

  it("returns false for undefined automation", () => {
    expect(_automationIsEnabled(undefined)).toBe(false);
  });

  it("returns false for unknown state", () => {
    expect(_automationIsEnabled({ state: "unknown" })).toBe(false);
  });
});

describe("_restoreVersion", () => {
  it("opts out of enabled-state preservation so the version's initial_state is restored", async () => {
    const callWS = vi.fn().mockResolvedValue(undefined);
    const host = {
      hass: { callWS },
      _restoringVersion: {},
      _versionHistoryOpen: {},
      _versions: {},
      _loadAutomations: vi.fn().mockResolvedValue(undefined),
      _showToast: vi.fn(),
      _t: (_key, fallback) => fallback,
      requestUpdate: vi.fn(),
    };

    await _restoreVersion.call(
      host,
      "auto_1",
      3,
      "alias: X\ninitial_state: false\n",
    );

    const call = callWS.mock.calls.find(
      ([arg]) => arg?.type === "selora_ai/update_automation_yaml",
    );
    expect(call).toBeDefined();
    expect(call[0].preserve_enabled_state).toBe(false);
  });
});
