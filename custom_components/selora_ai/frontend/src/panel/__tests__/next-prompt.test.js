import { describe, it, expect, vi } from "vitest";
import {
  acceptNextPrompt,
  activeNextPrompt,
  isPredicting,
  refreshNextPrompt,
  requestNextPrompt,
} from "../next-prompt.js";

const reply = { role: "assistant", content: "Updated the announcement." };

function makeHost(prompt, overrides = {}) {
  return {
    _config: { next_prompt_enabled: true },
    _activeSessionId: "s1",
    _messages: [{ role: "user", content: "also on person" }, reply],
    _input: "",
    _loading: false,
    _streaming: false,
    _nextPrompt: null,
    hass: { language: "en", callWS: vi.fn(async () => ({ prompt })) },
    ...overrides,
  };
}

describe("next-message prediction", () => {
  it("shows the prediction for the reply it followed", async () => {
    const host = makeHost("Rename it to match");
    await requestNextPrompt(host, reply, "s1");
    expect(host.hass.callWS).toHaveBeenCalledWith({
      type: "selora_ai/predict_next_prompt",
      session_id: "s1",
      language: "en",
    });
    expect(activeNextPrompt(host)).toBe("Rename it to match");
  });

  it("asks nothing when the setting is off", async () => {
    const host = makeHost("Rename it", { _config: {} });
    await requestNextPrompt(host, reply, "s1");
    expect(host.hass.callWS).not.toHaveBeenCalled();
    expect(activeNextPrompt(host)).toBe("");
  });

  it("retires once anything follows the reply or the user types", async () => {
    const host = makeHost("Rename it");
    await requestNextPrompt(host, reply, "s1");
    host._input = "R";
    expect(activeNextPrompt(host)).toBe("");
    host._input = "";
    host._messages = [...host._messages, { role: "user", content: "next" }];
    expect(activeNextPrompt(host)).toBe("");
  });

  it("does not follow the user into another session", async () => {
    const host = makeHost("Rename it");
    await requestNextPrompt(host, reply, "s1");
    host._activeSessionId = "s2";
    expect(activeNextPrompt(host)).toBe("");
  });

  it("predicts again for the reply an accept reloaded", async () => {
    const host = makeHost("Rename it to match");
    await requestNextPrompt(host, reply, "s1");
    // Accepting reloads the session: same reply, new objects.
    const reloaded = { ...reply, automation_status: "saved" };
    host._messages = [host._messages[0], reloaded];
    expect(activeNextPrompt(host)).toBe("");
    refreshNextPrompt(host);
    await vi.waitFor(() =>
      expect(activeNextPrompt(host)).toBe("Rename it to match"),
    );
  });

  it("holds the composer until the prediction is in", async () => {
    let answer;
    const host = makeHost(null);
    host.hass.callWS = vi.fn(
      () => new Promise((resolve) => (answer = resolve)),
    );
    const pending = requestNextPrompt(host, reply, "s1");
    expect(isPredicting(host)).toBe(true);
    // Another conversation's composer is not held.
    host._activeSessionId = "s2";
    expect(isPredicting(host)).toBe(false);
    host._activeSessionId = "s1";
    answer({ prompt: "Rename it" });
    await pending;
    expect(isPredicting(host)).toBe(false);
    expect(activeNextPrompt(host)).toBe("Rename it");
  });

  it("releases the composer when the provider hangs", async () => {
    vi.useFakeTimers();
    const host = makeHost(null);
    host.hass.callWS = vi.fn(() => new Promise(() => {}));
    const pending = requestNextPrompt(host, reply, "s1");
    await vi.advanceTimersByTimeAsync(10000);
    await pending;
    expect(isPredicting(host)).toBe(false);
    expect(activeNextPrompt(host)).toBe("");
    vi.useRealTimers();
  });

  it("asks nothing and holds nothing when the setting is off", async () => {
    const host = makeHost("Rename it", { _config: {} });
    await requestNextPrompt(host, reply, "s1");
    expect(isPredicting(host)).toBe(false);
  });

  it("a failed request shows nothing", async () => {
    const host = makeHost(null);
    host.hass.callWS = vi.fn(async () => {
      throw new Error("unknown command");
    });
    await requestNextPrompt(host, reply, "s1");
    expect(activeNextPrompt(host)).toBe("");
  });

  it("accepting fills the composer instead of sending", async () => {
    vi.stubGlobal("requestAnimationFrame", (cb) => cb());
    const host = makeHost("Rename it");
    await requestNextPrompt(host, reply, "s1");
    expect(acceptNextPrompt(host, null)).toBe(true);
    expect(host._input).toBe("Rename it");
    expect(host._nextPrompt).toBe(null);
    expect(acceptNextPrompt(host, null)).toBe(false);
    vi.unstubAllGlobals();
  });
});
