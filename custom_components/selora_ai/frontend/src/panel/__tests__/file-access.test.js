import { describe, it, expect, vi, afterEach } from "vitest";

import { _checkTabParam } from "../session-actions.js";
import * as actions from "../file-access-actions.js";
import {
  agentLabel,
  groupWritesByFile,
  fileAccessIndicatorState,
  renderFileAccessIndicator,
  renderFileAccessDialog,
} from "../render-file-access.js";

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

// Every literal template chunk, joined. Text passed as a VALUE never appears
// here — Lit escapes values, so untrusted text must only ever be one.
function literals(node) {
  if (Array.isArray(node)) return node.map(literals).join("");
  if (node && typeof node === "object" && node.strings && "values" in node) {
    return node.strings.join("") + literals(node.values);
  }
  return "";
}

function handlers(node, out = []) {
  if (Array.isArray(node)) node.forEach((n) => handlers(n, out));
  else if (typeof node === "function") out.push(node);
  else if (node && typeof node === "object" && "values" in node) {
    handlers(node.values, out);
  }
  return out;
}

const REQUEST = {
  id: "req1",
  agent: "Claude (MCP token 'claude-desktop')",
  folder: "www/selora",
  reason: "<img src=x onerror=alert(1)> install the dashboard",
  minutes: 90,
  created_at: "2026-11-22T10:00:00Z",
  expires_at: "2026-11-22T10:15:00Z",
};

const GRANT = {
  id: "g1",
  agent: "Claude",
  folder: "www/selora",
  reason: "install",
  approved_by: "Philippe",
  approved_at: "2026-11-22T10:01:00Z",
  expires_at: "2026-11-22T11:01:00Z",
  ended_at: null,
  ended_reason: null,
  writes: [
    {
      file: "www/selora/selora-home.js",
      url: "/local/selora/selora-home.js",
      size: 2048,
      at: "2026-11-22T10:02:00Z",
      action: "created",
      findings: [
        {
          kind: "service",
          severity: "info",
          detail: "light.turn_on",
          line: 12,
        },
        {
          kind: "credentials",
          severity: "high",
          detail: "localStorage.hassTokens",
          line: 40,
        },
      ],
    },
    {
      file: "www/selora/vendor/lit-core.min.js",
      url: null,
      size: 10,
      at: "2026-11-22T10:03:00Z",
      action: "created",
      findings: [],
    },
  ],
};

function makeHost(overrides = {}) {
  const host = {
    hass: { callWS: vi.fn(async () => ({})) },
    _t: (_key, fallback) => fallback,
    _showToast: vi.fn(),
    _fileAccess: { requests: [REQUEST], grants: [GRANT] },
    _fileAccessDialog: null,
    _fileAccessBusy: false,
    _fileAccessMinutes: null,
    ...overrides,
  };
  for (const [name, fn] of Object.entries(actions)) {
    if (typeof fn === "function" && name.startsWith("_")) host[name] = fn;
  }
  return host;
}

describe("the notification deep link", () => {
  afterEach(() => vi.unstubAllGlobals());

  function stub(url) {
    const replaceState = vi.fn();
    vi.stubGlobal("window", {
      location: new URL(url),
      history: { replaceState },
    });
    return replaceState;
  }

  it("opens the approval dialog for the request and drops the param", () => {
    const replaceState = stub("http://ha/selora-ai?file_access=req1");
    const host = makeHost({ _activeTab: "chat", _tabRestored: true });
    host._openFileAccess = vi.fn();

    _checkTabParam.call(host);

    expect(host._openFileAccess).toHaveBeenCalledWith("req1");
    expect(String(replaceState.mock.calls.at(-1)[2])).not.toContain(
      "file_access",
    );
  });

  it("waits for hass on a cold load", () => {
    stub("http://ha/selora-ai?file_access=req1");
    const host = makeHost({
      hass: null,
      _activeTab: "chat",
      _tabRestored: true,
    });
    host._openFileAccess = vi.fn();

    _checkTabParam.call(host);

    expect(host._openFileAccess).not.toHaveBeenCalled();
    expect(host._pendingFileAccess).toBe("req1");
  });
});

describe("the websocket calls", () => {
  it("approves with exactly the documented fields", async () => {
    const host = makeHost();
    host.hass.callWS = vi.fn(async (msg) =>
      msg.type === "selora_ai/file_access/decide"
        ? { grant: { id: "g9" } }
        : {},
    );

    await host._decideFileAccess("req1", true, 30);

    expect(host.hass.callWS.mock.calls[0][0]).toEqual({
      type: "selora_ai/file_access/decide",
      request_id: "req1",
      approve: true,
      minutes: 30,
    });
    expect(host._fileAccessDialog).toEqual({ kind: "grant", id: "g9" });
  });

  it("denies without a duration", async () => {
    const host = makeHost();
    host.hass.callWS = vi.fn(async () => ({ status: "denied" }));
    host._fileAccessDialog = { kind: "request", id: "req1" };

    await host._decideFileAccess("req1", false);

    expect(host.hass.callWS.mock.calls[0][0]).toEqual({
      type: "selora_ai/file_access/decide",
      request_id: "req1",
      approve: false,
    });
    expect(host._fileAccessDialog).toBeNull();
  });

  it("revokes and dismisses by grant id only", async () => {
    const host = makeHost();
    await host._revokeFileAccess("g1");
    await host._dismissFileAccess("g1");
    const sent = host.hass.callWS.mock.calls.map((c) => c[0]);
    expect(sent).toContainEqual({
      type: "selora_ai/file_access/revoke",
      grant_id: "g1",
    });
    expect(sent).toContainEqual({
      type: "selora_ai/file_access/dismiss",
      grant_id: "g1",
    });
  });

  it("says where to approve from when a token is refused", async () => {
    const host = makeHost();
    host.hass.callWS = vi.fn(async (msg) => {
      if (msg.type === "selora_ai/file_access/decide") {
        throw { code: "login_session_required", message: "nope" };
      }
      return {};
    });

    await host._decideFileAccess("req1", true, 60);

    const [text, type] = host._showToast.mock.calls[0];
    expect(type).toBe("error");
    expect(text).toContain("Home Assistant app");
  });

  it("shows an answered or lapsed request instead of the form", async () => {
    const host = makeHost();
    host.hass.callWS = vi.fn(async (msg) => {
      if (msg.type === "selora_ai/file_access/decide") {
        throw { code: "expired", message: "expired" };
      }
      return { requests: [], grants: [] };
    });

    await host._decideFileAccess("req1", true, 60);

    expect(host._fileAccessDialog).toEqual({ kind: "gone", id: "req1" });
    const out = flatten(renderFileAccessDialog(host));
    expect(out).toContain("already answered or has expired");
    expect(out).not.toContain("Allow for");
  });
});

describe("the dialog", () => {
  it("caps the default duration at an hour", () => {
    expect(actions.defaultMinutes(90)).toBe(60);
    expect(actions.defaultMinutes(20)).toBe(30);
    expect(actions.defaultMinutes(15)).toBe(15);
  });

  it("renders the agent's reason as escaped text, marked as theirs", () => {
    const host = makeHost({
      _fileAccessDialog: { kind: "request", id: "req1" },
    });
    const tpl = renderFileAccessDialog(host);
    const out = flatten(tpl);
    expect(out).toContain("Temporary access request");
    // The risk is said apart from the safeguards, never listed among them.
    expect(out).toContain("signed in as you");
    expect(out).toContain("Safeguards");
    expect(out.indexOf("signed in as you")).toBeLessThan(
      out.indexOf("Safeguards"),
    );
    expect(out).toContain(REQUEST.reason);
    // One gold button: the duration is a choice, not a second call to action.
    expect(out.match(/btn-primary/g)).toHaveLength(1);
    expect(out).toMatch(/aria-checked="?true"?[^>]*>\s*60 min/);
    // Only ever a value, never part of the template markup.
    expect(literals(tpl)).not.toContain("onerror");
    expect(literals(tpl)).not.toContain(REQUEST.agent);
  });

  it("an unknown id shows the lapsed state", () => {
    const host = makeHost({
      _fileAccessDialog: { kind: "request", id: "nope" },
    });
    expect(flatten(renderFileAccessDialog(host))).toContain(
      "already answered or has expired",
    );
  });

  it("groups writes by file and flags the risky one", () => {
    const host = makeHost({ _fileAccessDialog: { kind: "grant", id: "g1" } });
    const out = flatten(renderFileAccessDialog(host));
    expect(out).toContain("Reads your login");
    expect(out).toContain("file-access-finding--high");
    expect(out).toContain("mdi:language-javascript");
    // The verdict comes before the detail.
    expect(out).toContain("1 file does something worth checking");
    expect(out.indexOf("worth checking")).toBeLessThan(
      out.indexOf("Reads your login"),
    );
    // The vendored file had no findings at all.
    expect(out.match(/No risky patterns found/g)).toHaveLength(1);
    expect(out).toContain("Revoke");
    expect(groupWritesByFile(GRANT.writes).map((g) => g.file)).toEqual([
      "www/selora/selora-home.js",
      "www/selora/vendor/lit-core.min.js",
    ]);
  });

  it("an ended grant offers Dismiss, not Revoke", () => {
    const ended = {
      ...GRANT,
      ended_at: "2026-11-22T11:01:00Z",
      ended_reason: "expired",
    };
    const host = makeHost({
      _fileAccess: { requests: [], grants: [ended] },
      _fileAccessDialog: { kind: "grant", id: "g1" },
    });
    const out = flatten(renderFileAccessDialog(host));
    expect(out).toContain("access has ended");
    expect(out).toContain("Expired at");
    expect(out).toContain("Mark as reviewed");
    expect(out).not.toContain("Revoke");
  });
});

describe("the header indicator", () => {
  it("puts a waiting request first, with a dot and the full sentence as its name", () => {
    const host = makeHost();
    const shown = fileAccessIndicatorState(host);
    expect(shown.kind).toBe("request");
    const tpl = renderFileAccessIndicator(host);
    expect(flatten(tpl)).toContain("file-access-indicator-dot");
    expect(tpl.values).toContain(
      "Claude (MCP token 'claude-desktop') is asking to write browser code to www/selora",
    );
  });

  it("shows a live grant's end time and opens it", () => {
    const host = makeHost({ _fileAccess: { requests: [], grants: [GRANT] } });
    host._openFileAccess = vi.fn();
    const shown = fileAccessIndicatorState(host);
    expect(shown.kind).toBe("grant");
    expect(shown.label).not.toBe("");
    const [open] = handlers(renderFileAccessIndicator(host));
    open();
    expect(host._openFileAccess).toHaveBeenCalledWith("g1", "grant");
  });

  it("marks an ended grant still to be reviewed, without a time", () => {
    const ended = {
      ...GRANT,
      ended_at: "2026-11-22T11:00:00Z",
      ended_reason: "expired",
    };
    const host = makeHost({ _fileAccess: { requests: [], grants: [ended] } });
    const shown = fileAccessIndicatorState(host);
    expect(shown.icon).toBe("mdi:shield-check-outline");
    expect(shown.label).toBe("");
    expect(flatten(renderFileAccessIndicator(host))).toContain(
      "file-access-indicator--ended",
    );
  });

  it("is absent when there is nothing to show", () => {
    expect(renderFileAccessIndicator(makeHost({ _fileAccess: null }))).toBe("");
    expect(
      renderFileAccessIndicator(
        makeHost({ _fileAccess: { requests: [], grants: [] } }),
      ),
    ).toBe("");
  });
});

describe("who is asking", () => {
  it("names a Selora login without its internal id", () => {
    const host = makeHost();
    const item = { agent: "x", agent_kind: "selora_account", agent_name: null };
    expect(agentLabel(host, item)).toBe(
      "An AI assistant connected with your Selora account",
    );
  });

  it("uses the name a person recognises when there is one", () => {
    const host = makeHost();
    expect(
      agentLabel(host, {
        agent_kind: "mcp_token",
        agent_name: "claude-desktop",
      }),
    ).toBe("An AI assistant using the key “claude-desktop”");
    expect(
      agentLabel(host, { agent_kind: "ha_token", agent_name: "Philippe" }),
    ).toBe("An AI assistant using Philippe's Home Assistant access");
  });

  it("keeps the stored label for a record from before kinds", () => {
    expect(agentLabel(makeHost(), { agent: "Old label" })).toBe("Old label");
  });
});

describe("the request form", () => {
  it("shows the duration once — in the picker, not also as a fact", () => {
    const host = makeHost({
      _fileAccessDialog: { id: "req1", kind: "request" },
    });
    const out = flatten(renderFileAccessDialog(host));
    expect(out).not.toContain("Asked for");
  });
});

describe("the app that asks", () => {
  const FROM_CHATGPT = {
    ...REQUEST,
    agent: "ChatGPT",
    agent_kind: "selora_account",
    agent_name: null,
    agent_client: "ChatGPT",
  };

  it("heads the request with the app, and says nothing of the usual sign-in", () => {
    const host = makeHost({
      _fileAccess: { requests: [FROM_CHATGPT], grants: [] },
      _fileAccessDialog: { id: "req1", kind: "request" },
    });
    expect(agentLabel(host, FROM_CHATGPT)).toBe("ChatGPT");
    const out = flatten(renderFileAccessDialog(host));
    expect(out).toContain(
      "ChatGPT wants to add code that runs in your browser",
    );
    expect(out).not.toContain("Connected with");
  });

  it("names the key a request came in on", () => {
    const host = makeHost({
      _fileAccess: {
        requests: [
          { ...FROM_CHATGPT, agent_kind: "mcp_token", agent_name: "n8n" },
        ],
        grants: [],
      },
      _fileAccessDialog: { id: "req1", kind: "request" },
    });
    const out = flatten(renderFileAccessDialog(host));
    expect(out).toContain("Connected with");
    expect(out).toContain("The key “n8n”");
  });

  it("falls back to a generic name when no app named itself", () => {
    const host = makeHost({
      _fileAccess: {
        requests: [{ ...FROM_CHATGPT, agent_client: null }],
        grants: [],
      },
      _fileAccessDialog: { id: "req1", kind: "request" },
    });
    expect(flatten(renderFileAccessDialog(host))).toContain(
      "An AI assistant wants to add code that runs in your browser",
    );
  });
});

describe("time left", () => {
  const now = Date.parse("2026-11-22T10:31:00Z");

  it("reads hours and minutes, rounded up", () => {
    expect(actions.formatRemaining("2026-11-22T11:01:00Z", now)).toBe("0:30");
    expect(actions.formatRemaining("2026-11-22T11:31:00Z", now)).toBe("1:00");
    // The last seconds still read as a minute, never 0:00 while it lasts.
    expect(actions.formatRemaining("2026-11-22T10:31:20Z", now)).toBe("0:01");
    expect(actions.formatRemaining("2026-11-22T10:00:00Z", now)).toBe("0:00");
    expect(actions.formatRemaining("nope", now)).toBe("");
  });

  it("drains the bar from approval to expiry", () => {
    expect(actions.remainingFraction(GRANT, now)).toBe(0.5);
    expect(actions.remainingFraction({}, now)).toBe(0);
  });

  it("ticks only while a grant runs", () => {
    vi.useFakeTimers();
    const host = makeHost();
    host.requestUpdate = vi.fn();
    actions._syncFileAccessTick.call(host);
    vi.advanceTimersByTime(15000);
    expect(host.requestUpdate).toHaveBeenCalledTimes(1);
    host._fileAccess = { requests: [], grants: [] };
    actions._syncFileAccessTick.call(host);
    vi.advanceTimersByTime(60000);
    expect(host.requestUpdate).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it("the live grant shows time left, not a clock time", () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const host = makeHost({
      _fileAccess: { requests: [], grants: [GRANT] },
      _fileAccessDialog: { kind: "grant", id: "g1" },
    });
    const out = flatten(renderFileAccessDialog(host));
    expect(out).toContain("can add code that runs in your browser");
    expect(out).not.toContain("until");
    expect(out).toContain("0:30");
    expect(fileAccessIndicatorState(host).label).toBe("0:30");
    vi.useRealTimers();
  });
});

describe("actions a file calls", () => {
  it("are one row, sensitive ones marked", () => {
    const grant = {
      ...GRANT,
      writes: [
        {
          ...GRANT.writes[0],
          findings: [
            {
              kind: "service",
              severity: "info",
              detail: "light.turn_on",
              line: 3,
            },
            {
              kind: "service",
              severity: "medium",
              detail: "lock.unlock",
              line: 9,
            },
            {
              kind: "service",
              severity: "info",
              detail: "may also call actions decided while it runs",
              line: 2,
            },
          ],
        },
      ],
    };
    const host = makeHost({
      _fileAccess: { requests: [], grants: [grant] },
      _fileAccessDialog: { kind: "grant", id: "g1" },
    });
    const out = flatten(renderFileAccessDialog(host));
    expect(out.match(/Controls devices/g)).toHaveLength(1);
    // In words, the sensitive ones set apart.
    expect(out).toContain("Light: Turn on");
    expect(out).toContain("Can open, unlock or disarm");
    expect(out).toContain("Lock: Unlock");
    expect(out.indexOf("Lock: Unlock")).toBeLessThan(
      out.indexOf("Everyday controls"),
    );
    expect(out).toContain("It may also call others");
    expect(out).toContain("file-access-finding--medium");
  });
});

describe("the files it wrote", () => {
  it("say what each is for, and keep a finding's label apart", () => {
    const grant = {
      ...GRANT,
      writes: [
        {
          ...GRANT.writes[0],
          loaded: true,
          findings: [
            {
              kind: "service",
              severity: "medium",
              detail: "service chosen at runtime",
              line: 296,
            },
          ],
        },
        { ...GRANT.writes[1], loaded: false },
      ],
    };
    const host = makeHost({
      _fileAccess: { requests: [], grants: [grant] },
      _fileAccessDialog: { kind: "grant", id: "g1" },
    });
    const out = flatten(renderFileAccessDialog(host));
    expect(out).toContain("Files it wrote");
    expect(out).toContain("it is a dashboard resource");
    expect(out).toContain("another file it wrote may load it");
    // A check's label is its own line, not the start of a sentence.
    expect(out).toMatch(
      /file-access-finding-kind">\s*Controls devices\s*<\/div>/,
    );
    // An older record's sentence reads as a note, not as an action.
    expect(out).toContain("Which ones is decided while it runs");
    expect(out).not.toContain("service chosen at runtime");
  });
});

describe("an earlier version's findings", () => {
  it("stay on the record, marked", () => {
    const grant = {
      ...GRANT,
      writes: [
        {
          ...GRANT.writes[0],
          action: "deleted",
          findings: [
            {
              kind: "network",
              severity: "high",
              detail: "fetch(",
              line: 3,
              earlier: true,
            },
          ],
        },
      ],
    };
    const host = makeHost({
      _fileAccess: { requests: [], grants: [grant] },
      _fileAccessDialog: { kind: "grant", id: "g1" },
    });
    const out = flatten(renderFileAccessDialog(host));
    expect(out).toContain("Talks to the network");
    expect(out).toContain("in an earlier version");
    expect(out).toContain("file-access-finding--high");
  });
});
