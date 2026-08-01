import { describe, it, expect } from "vitest";
import {
  _loadVersionStatus,
  _dismissStaleCodeNotice,
} from "../version-actions.js";

function host(callWS) {
  return {
    _versionStatus: undefined,
    _staleCodeDismissed: false,
    hass: { callWS },
  };
}

// HA's websocket layer rejects with an object carrying `code` + `message`.
function wsError(code, message = code) {
  return Object.assign(new Error(message), { code });
}

describe("_loadVersionStatus", () => {
  it("stores the backend's answer", async () => {
    const answer = { restart_required: false, panel_reload_required: true };
    const h = host(async () => answer);
    await _loadVersionStatus.call(h);
    expect(h._versionStatus).toBe(answer);
  });

  it("sends the baked build id", async () => {
    const calls = [];
    const h = host(async (msg) => {
      calls.push(msg);
      return {};
    });
    await _loadVersionStatus.call(h);
    expect(calls[0].type).toBe("selora_ai/version_status");
    expect(calls[0]).toHaveProperty("panel_build");
  });

  it("treats an unknown command as restart-required", async () => {
    // The first no-restart upgrade onto this feature: this panel ships with the
    // command, so a backend that doesn't know it is a stale process in memory.
    const h = host(async () => {
      throw wsError("unknown_command", "Unknown command.");
    });
    await _loadVersionStatus.call(h);
    expect(h._versionStatus).toEqual({
      restart_required: true,
      panel_reload_required: false,
    });
  });

  it("stays silent when the caller isn't an admin", async () => {
    const h = host(async () => {
      throw wsError("unauthorized", "Unauthorized");
    });
    await _loadVersionStatus.call(h);
    expect(h._versionStatus).toBeNull();
  });

  it("stays silent on a transient failure", async () => {
    const h = host(async () => {
      throw new Error("connection lost");
    });
    await _loadVersionStatus.call(h);
    expect(h._versionStatus).toBeNull();
  });

  it("stays silent on a backend error other than unknown command", async () => {
    const h = host(async () => {
      throw wsError("unknown_error", "boom");
    });
    await _loadVersionStatus.call(h);
    expect(h._versionStatus).toBeNull();
  });
});

describe("_dismissStaleCodeNotice", () => {
  it("hides the banner for this page view", () => {
    const h = host(async () => ({}));
    _dismissStaleCodeNotice.call(h);
    expect(h._staleCodeDismissed).toBe(true);
  });
});
