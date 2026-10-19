import { describe, it, expect, vi, afterEach } from "vitest";
import {
  _checkTabParam,
  _loadSessions,
  _openChatHome,
} from "../session-actions.js";

function makeHost() {
  const host = {
    hass: {
      callWS: vi.fn(async ({ type }) =>
        type === "selora_ai/get_sessions" ? [{ id: "last" }] : [],
      ),
    },
    _activeTab: "chat",
    _activeSessionId: null,
    _tabRestored: true,
    _openSession: vi.fn(),
  };
  host._newSession = vi.fn(async () => {
    host._activeSessionId = "new";
    return true;
  });
  host._openChatHome = _openChatHome;
  return host;
}

describe("the home-suggestions notification link", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("opens the chat home, not the last conversation", async () => {
    const replaceState = vi.fn();
    vi.stubGlobal("window", {
      location: new URL("http://ha/selora-ai?home_suggestions=1"),
      history: { replaceState },
    });
    const host = makeHost();

    _checkTabParam.call(host);
    await _loadSessions.call(host);

    expect(host._newSession).toHaveBeenCalledOnce();
    expect(host._openSession).not.toHaveBeenCalled();
    expect(String(replaceState.mock.calls.at(-1)[2])).not.toContain(
      "home_suggestions",
    );
  });

  it("leaves auto-opening the last conversation alone otherwise", async () => {
    vi.stubGlobal("window", {
      location: new URL("http://ha/selora-ai"),
      history: { replaceState: vi.fn() },
    });
    const host = makeHost();

    _checkTabParam.call(host);
    await _loadSessions.call(host);

    expect(host._newSession).not.toHaveBeenCalled();
    expect(host._openSession).toHaveBeenCalledWith("last");
  });
});
