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

describe("reloading the panel", () => {
  afterEach(() => vi.unstubAllGlobals());

  function stubWindow(stored) {
    const storage = new Map([["selora_ai_active_session", stored]]);
    vi.stubGlobal("window", {
      location: new URL("http://ha/selora-ai"),
      history: { replaceState: vi.fn() },
      sessionStorage: {
        getItem: (k) => storage.get(k) ?? null,
        setItem: (k, v) => storage.set(k, v),
        removeItem: (k) => storage.delete(k),
      },
    });
  }

  // `listed` is what get_sessions returns; `stored` sessions also include
  // the empty ones it leaves out. Opening an id the store lacks fails.
  function hostWith(listed, stored = listed) {
    const host = makeHost();
    host.hass.callWS = vi.fn(async () => listed.map((id) => ({ id })));
    host._openSession = vi.fn(async (id) => {
      if (stored.includes(id)) host._activeSessionId = id;
    });
    return host;
  }

  it("reopens the conversation that was open", async () => {
    stubWindow("older");
    const host = hostWith(["last", "older"]);

    await _loadSessions.call(host);

    expect(host._openSession).toHaveBeenCalledWith("older");
  });

  it("falls back to the latest when that one is gone", async () => {
    stubWindow("deleted");
    const host = hostWith(["last", "older"]);

    await _loadSessions.call(host);

    expect(host._activeSessionId).toBe("last");
  });

  it("stays on a new chat, which the list leaves out", async () => {
    stubWindow("new-chat");
    const host = hostWith(["last"], ["last", "new-chat"]);

    await _loadSessions.call(host);

    expect(host._activeSessionId).toBe("new-chat");
    expect(host._openSession).not.toHaveBeenCalledWith("last");
  });
});
