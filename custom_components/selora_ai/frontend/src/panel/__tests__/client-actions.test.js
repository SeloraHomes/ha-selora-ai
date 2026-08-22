import { readdirSync, readFileSync } from "node:fs";

import { describe, expect, it, vi } from "vitest";

import { resolveClientActions, runClientAction } from "../client-actions.js";

// Every handler now asks for the dashboard list first, so a mock that answers
// only the create call is not a faithful stand-in.
const hassWith = (impl) => ({
  callWS: vi.fn(async (payload) =>
    payload.type === "lovelace/dashboards/list" ? [] : impl(payload),
  ),
});

describe("runClientAction", () => {
  it("builds the fixed lovelace command from the intent", async () => {
    const hass = hassWith(async () => ({
      url_path: "kitchen",
      title: "Kitchen",
    }));

    const result = await runClientAction(hass, {
      kind: "create_dashboard",
      title: "Kitchen",
      url_path: "kitchen",
      icon: "mdi:chef-hat",
      require_admin: false,
      show_in_sidebar: true,
      allow_single_word: true,
    });

    expect(hass.callWS).toHaveBeenCalledWith({
      type: "lovelace/dashboards/create",
      title: "Kitchen",
      url_path: "kitchen",
      require_admin: false,
      show_in_sidebar: true,
      icon: "mdi:chef-hat",
      allow_single_word: true,
    });
    expect(result).toEqual({
      ok: true,
      kind: "create_dashboard",
      detail: { url_path: "kitchen", title: "Kitchen" },
    });
  });

  it("never forwards a field it was not expecting", async () => {
    // The security boundary. If the intent were spread into the call, anything
    // the model put in it would ride along into a privileged admin command.
    const hass = hassWith(async () => ({}));

    await runClientAction(hass, {
      kind: "create_dashboard",
      title: "Kitchen",
      url_path: "kitchen",
      type: "config/auth/create",
      user_id: "someone",
      extra: "smuggled",
    });

    const sent = hass.callWS.mock.calls.find(
      (c) => c[0].type === "lovelace/dashboards/create",
    )[0];
    expect(sent).not.toHaveProperty("user_id");
    expect(sent).not.toHaveProperty("extra");
  });

  it("refuses a kind it does not implement", async () => {
    const hass = hassWith(async () => ({}));

    const result = await runClientAction(hass, { kind: "delete_everything" });

    expect(result.ok).toBe(false);
    expect(hass.callWS).not.toHaveBeenCalled();
  });

  it("reports a websocket failure verbatim", async () => {
    // Usually the user's own permissions or a colliding url_path; a generic
    // message would send them looking in the wrong place.
    const hass = hassWith(async () => {
      throw new Error("url_path already exists");
    });

    const result = await runClientAction(hass, {
      kind: "create_dashboard",
      title: "Kitchen",
      url_path: "kitchen",
    });

    expect(result).toEqual({
      ok: false,
      kind: "create_dashboard",
      detail: "url_path already exists",
    });
  });

  it("omits optional fields that were not set", async () => {
    const hass = hassWith(async () => ({}));

    await runClientAction(hass, {
      kind: "create_dashboard",
      title: "Kitchen",
      url_path: "ground-floor",
      icon: null,
      allow_single_word: false,
    });

    const sent = hass.callWS.mock.calls.find(
      (c) => c[0].type === "lovelace/dashboards/create",
    )[0];
    expect(sent).not.toHaveProperty("icon");
    expect(sent).not.toHaveProperty("allow_single_word");
  });
});

describe("resolveClientActions", () => {
  const hostWith = (overrides = {}) => ({
    hass: {
      callWS: vi.fn(async (p) =>
        p.type === "lovelace/dashboards/list"
          ? []
          : { url_path: "kitchen", title: "Kitchen" },
      ),
    },
    _activeSessionId: "sess-1",
    _messages: [],
    _openSession: vi.fn(async () => {}),
    requestUpdate: vi.fn(),
    ...overrides,
  });

  it("reports against the panel's active session", async () => {
    // `_sessionId` does not exist on the panel; sending it undefined makes the
    // websocket schema reject the report while the dashboard already exists.
    const host = hostWith();
    const msg = {};

    await resolveClientActions(host, msg, {
      proposal_id: "p1",
      client_actions: [
        { kind: "create_dashboard", title: "Kitchen", url_path: "kitchen" },
      ],
    });

    const report = host.hass.callWS.mock.calls.find(
      (c) => c[0].type === "selora_ai/client_action_result",
    );
    expect(report[0].session_id).toBe("sess-1");
    expect(report[0].proposal_id).toBe("p1");
  });

  it("marks the originating message so the button cannot be pressed twice", async () => {
    const host = hostWith();
    const msg = { quick_actions: ["something"] };

    const pending = resolveClientActions(host, msg, {
      proposal_id: "p1",
      client_actions: [{ kind: "create_dashboard", title: "K", url_path: "k" }],
    });
    // The card reads msg.approval_status, not a map on the host.
    expect(msg.approval_status).toBe("resolving");
    expect(msg.quick_actions).toBeNull();

    await pending;
    expect(msg.approval_status).toBe("approved");
  });

  it("marks the card denied when the command failed", async () => {
    const host = hostWith({
      hass: {
        callWS: vi.fn(async (p) => {
          if (p.type === "lovelace/dashboards/list") return [];
          if (p.type === "lovelace/dashboards/create") throw new Error("nope");
          return {};
        }),
      },
    });
    const msg = {};

    await resolveClientActions(host, msg, {
      proposal_id: "p1",
      client_actions: [{ kind: "create_dashboard", title: "K", url_path: "k" }],
    });

    expect(msg.approval_status).toBe("denied");
  });

  it("reloads the session so the appended result is visible", async () => {
    const host = hostWith();

    await resolveClientActions(
      host,
      {},
      {
        proposal_id: "p1",
        client_actions: [
          { kind: "create_dashboard", title: "K", url_path: "k" },
        ],
      },
    );

    expect(host._openSession).toHaveBeenCalledWith("sess-1");
  });
});

describe("resolveClientActions resilience", () => {
  const baseHost = (callWS) => ({
    hass: { callWS },
    _activeSessionId: "sess-1",
    _messages: [],
    _openSession: vi.fn(async () => {}),
    requestUpdate: vi.fn(),
  });

  it("keeps the card resolved when only the report failed", async () => {
    // The dashboard exists. Reloading would swap in the server's still-pending
    // copy, put the button back, and invite a second creation.
    const host = baseHost(
      vi.fn(async (p) => {
        if (p.type === "lovelace/dashboards/list") return [];
        if (p.type === "selora_ai/client_action_result")
          throw new Error("offline");
        return { url_path: "kitchen", title: "Kitchen" };
      }),
    );
    const msg = {};

    await resolveClientActions(host, msg, {
      proposal_id: "p1",
      client_actions: [{ kind: "create_dashboard", title: "K", url_path: "k" }],
    });

    expect(msg.approval_status).toBe("approved");
    expect(host._openSession).not.toHaveBeenCalled();
  });

  it("reports against the session the card belongs to, not the current one", async () => {
    // Switching conversations mid-flight would otherwise report the old
    // proposal against the new session, and the card stays pending forever.
    const host = baseHost(
      vi.fn(async (p) => {
        if (p.type === "lovelace/dashboards/list") return [];
        if (p.type === "lovelace/dashboards/create") {
          host._activeSessionId = "sess-2";
          return { url_path: "kitchen" };
        }
        return {};
      }),
    );

    await resolveClientActions(
      host,
      {},
      {
        proposal_id: "p1",
        client_actions: [
          { kind: "create_dashboard", title: "K", url_path: "k" },
        ],
      },
    );

    const report = host.hass.callWS.mock.calls.find(
      (c) => c[0].type === "selora_ai/client_action_result",
    );
    expect(report[0].session_id).toBe("sess-1");
    // And it does not yank the user back to the conversation they left.
    expect(host._openSession).not.toHaveBeenCalled();
  });
});

describe("runClientAction idempotence", () => {
  it("reconciles instead of re-creating when the dashboard is already there", async () => {
    // The card can outlive its own execution: create succeeds, the result
    // report fails, and after a refresh the backend still serves the proposal
    // as pending. A blind retry would fail on a taken url_path and record the
    // card as denied for a dashboard sitting right there.
    const hass = {
      callWS: vi.fn(async (p) => {
        if (p.type === "lovelace/dashboards/list")
          return [{ url_path: "kitchen", title: "Kitchen" }];
        throw new Error("url_path already exists");
      }),
    };

    const result = await runClientAction(hass, {
      kind: "create_dashboard",
      title: "Kitchen",
      url_path: "kitchen",
    });

    expect(result.ok).toBe(true);
    expect(result.detail).toEqual({ url_path: "kitchen", title: "Kitchen" });
    // And it never attempted the create.
    expect(
      hass.callWS.mock.calls.some(
        (c) => c[0].type === "lovelace/dashboards/create",
      ),
    ).toBe(false);
  });

  it("refuses a different dashboard sitting on the url_path", async () => {
    // The path was free when the proposal was built, so something else claimed
    // it in between. Reconciling to it would report success for a dashboard
    // nobody created, and the transcript would name a title that exists
    // nowhere.
    const hass = {
      callWS: vi.fn(async (p) => {
        if (p.type === "lovelace/dashboards/list")
          return [
            { url_path: "kitchen", title: "Garage", show_in_sidebar: true },
          ];
        return { url_path: "kitchen", title: "Kitchen" };
      }),
    };

    const result = await runClientAction(hass, {
      kind: "create_dashboard",
      title: "Kitchen",
      url_path: "kitchen",
      show_in_sidebar: true,
    });

    expect(result.ok).toBe(false);
    expect(result.detail).toContain("Garage");
    // And it did not create a second one either.
    expect(
      hass.callWS.mock.calls.some(
        (c) => c[0].type === "lovelace/dashboards/create",
      ),
    ).toBe(false);
  });

  it("reconciles a retry of its own create, flags and all", async () => {
    const action = {
      kind: "create_dashboard",
      title: "Kitchen",
      url_path: "kitchen",
      icon: "mdi:chef-hat",
      require_admin: true,
      show_in_sidebar: true,
    };
    const hass = {
      callWS: vi.fn(async (p) => {
        if (p.type === "lovelace/dashboards/list")
          return [
            {
              url_path: "kitchen",
              title: "Kitchen",
              icon: "mdi:chef-hat",
              require_admin: true,
              show_in_sidebar: true,
            },
          ];
        throw new Error("url_path already exists");
      }),
    };

    const result = await runClientAction(hass, action);

    expect(result.ok).toBe(true);
    expect(result.detail).toEqual({ url_path: "kitchen", title: "Kitchen" });
  });

  it("still creates when nothing matches the url_path", async () => {
    const hass = {
      callWS: vi.fn(async (p) =>
        p.type === "lovelace/dashboards/list"
          ? [{ url_path: "something-else" }]
          : { url_path: "kitchen", title: "Kitchen" },
      ),
    };

    const result = await runClientAction(hass, {
      kind: "create_dashboard",
      title: "Kitchen",
      url_path: "kitchen",
    });

    expect(result.ok).toBe(true);
    expect(
      hass.callWS.mock.calls.some(
        (c) => c[0].type === "lovelace/dashboards/create",
      ),
    ).toBe(true);
  });
});

describe("resolveClientActions re-entry", () => {
  const gatedHost = (release) => {
    const host = {
      hass: {
        callWS: vi.fn(async (p) => {
          if (p.type === "lovelace/dashboards/list") {
            await release;
            return [];
          }
          return { url_path: "kitchen", title: "Kitchen" };
        }),
      },
      _activeSessionId: "sess-1",
      _messages: [],
      _openSession: vi.fn(async () => {}),
      requestUpdate: vi.fn(),
    };
    return host;
  };

  it("ignores a second click before the first finishes", async () => {
    // Both handlers would otherwise clear the dashboard-list request before
    // either creates, so the idempotency check inside the action misses the
    // race entirely and two creates run.
    let release;
    const gate = new Promise((r) => (release = r));
    const host = gatedHost(gate);
    const msg = {};
    const approval = {
      proposal_id: "p1",
      client_actions: [
        { kind: "create_dashboard", title: "K", url_path: "kitchen" },
      ],
    };

    const first = resolveClientActions(host, msg, approval);
    const second = resolveClientActions(host, msg, approval);
    release();
    await Promise.all([first, second]);

    const creates = host.hass.callWS.mock.calls.filter(
      (c) => c[0].type === "lovelace/dashboards/create",
    );
    expect(creates).toHaveLength(1);
    expect(msg.approval_status).toBe("approved");
  });

  it("guards by proposal id when there is no message to mark", async () => {
    let release;
    const gate = new Promise((r) => (release = r));
    const host = gatedHost(gate);
    const approval = {
      proposal_id: "p1",
      client_actions: [
        { kind: "create_dashboard", title: "K", url_path: "kitchen" },
      ],
    };

    const first = resolveClientActions(host, null, approval);
    const second = resolveClientActions(host, null, approval);
    release();
    await Promise.all([first, second]);

    expect(
      host.hass.callWS.mock.calls.filter(
        (c) => c[0].type === "lovelace/dashboards/create",
      ),
    ).toHaveLength(1);
  });

  it("releases the guard so a later retry is possible", async () => {
    const host = gatedHost(Promise.resolve());
    const approval = {
      proposal_id: "p1",
      client_actions: [
        { kind: "create_dashboard", title: "K", url_path: "kitchen" },
      ],
    };

    await resolveClientActions(host, null, approval);
    await resolveClientActions(host, null, approval);

    // Two sequential runs both proceed — a leaked guard would make the card
    // permanently unpressable.
    expect(
      host.hass.callWS.mock.calls.filter(
        (c) => c[0].type === "lovelace/dashboards/create",
      ),
    ).toHaveLength(2);
  });
});

describe("the confirmation card uses the panel's own styles", () => {
  // The first version of this card invented `approval-card`, `approval-head`,
  // `approval-row`, `approval-buttons` and `approve`. None existed in any
  // stylesheet, so it shipped as a bare div and a browser-default button —
  // which no test could see, because the markup was valid and the tests only
  // ever checked behaviour.
  const read = (p) => readFileSync(new URL(p, import.meta.url), "utf8");

  const CARD = read("../render-approval-card.js");

  // Every stylesheet, found rather than listed: the first version of this test
  // named the files it knew about and missed the quick-actions one, so a class
  // that DID exist was reported as invented.
  const STYLES = (function collect(dir, out = []) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = new URL(
        `${entry.name}${entry.isDirectory() ? "/" : ""}`,
        dir,
      );
      if (entry.isDirectory()) collect(path, out);
      else if (entry.name.endsWith(".css.js"))
        out.push(readFileSync(path, "utf8"));
    }
    return out;
  })(new URL("../../", import.meta.url)).join("\n");

  it("names no class that no stylesheet defines", () => {
    const used = new Set();
    for (const [, attr] of CARD.matchAll(/class="([^"$]+)"/g)) {
      for (const name of attr.split(/\s+/).filter(Boolean)) used.add(name);
    }
    expect(used.size).toBeGreaterThan(0);

    const missing = [...used].filter(
      (name) => !new RegExp(`\\.${name}\\b`).test(STYLES),
    );
    expect(missing).toEqual([]);
  });

  it("is a variant of the confirmation card, not a second card", () => {
    // It shipped as its own renderer with its own markup, which is how it
    // ended up with classes no stylesheet defined. The file already had one
    // confirmation card parameterized by variant — delete and destructive —
    // so a third shape is a variant of it.
    expect(CARD).toMatch(/client_action:\s*\{/);
    expect(CARD).not.toMatch(/function renderClientActionCard/);
    // And exactly one function draws a confirmation card.
    expect([...CARD.matchAll(/function renderConfirmationCard/g)]).toHaveLength(
      1,
    );
  });

  it("confirms with the same chip the risk card's Allow uses", () => {
    // Not a button of its own: quick-actions owns the "tap to authorize"
    // chip, and the lock approval's Allow / Deny are the same component.
    expect(CARD).toMatch(/renderConfirmChip/);
    expect(CARD).not.toMatch(/class="btn btn-primary"/);
  });
});

describe("resuming what the card left unfinished", () => {
  const cardHost = (approval, extra = {}) => ({
    hass: {
      callWS: async (payload) =>
        payload.type === "lovelace/dashboards/list"
          ? []
          : { url_path: "office", title: "Office" },
    },
    _activeSessionId: "s1",
    _messages: [],
    _openSession: async () => {},
    requestUpdate: () => {},
    _sendMessage: vi.fn(async () => {}),
    ...extra,
  });

  const APPROVAL = {
    proposal_id: "p1",
    approval_kind: "client_action",
    remaining_intent: "add the Office lights",
    client_actions: [
      { kind: "create_dashboard", title: "Office", url_path: "office" },
    ],
  };

  it("continues the turn when the card declared work left", async () => {
    const host = cardHost(APPROVAL);
    const msg = { session_id: "s1", command_approval: APPROVAL };

    await resolveClientActions(host, msg, APPROVAL);

    expect(host._sendMessage).toHaveBeenCalledWith({ resumeProposalId: "p1" });
  });

  it("does not continue when nothing was left", async () => {
    // Which is most cards. A model round to discover there is nothing to do
    // would be paid on every single approval.
    const approval = { ...APPROVAL, remaining_intent: undefined };
    const host = cardHost(approval);

    await resolveClientActions(host, { session_id: "s1" }, approval);

    expect(host._sendMessage).not.toHaveBeenCalled();
  });

  it("does not continue when the action itself failed", async () => {
    const host = cardHost(APPROVAL, {
      hass: {
        callWS: async (p) => {
          if (p.type === "lovelace/dashboards/list") return [];
          throw new Error("nope");
        },
      },
    });

    await resolveClientActions(host, { session_id: "s1" }, APPROVAL);

    expect(host._sendMessage).not.toHaveBeenCalled();
  });

  it("does not continue into a conversation the user has left", async () => {
    // The switch happens WHILE the websocket call is in flight — resuming then
    // would stream a continuation into whatever conversation they moved to.
    const host = cardHost(APPROVAL);
    host.hass = {
      callWS: async (payload) => {
        if (payload.type === "lovelace/dashboards/list") return [];
        host._activeSessionId = "somewhere-else";
        return { url_path: "office", title: "Office" };
      },
    };

    await resolveClientActions(host, { session_id: "s1" }, APPROVAL);

    expect(host._sendMessage).not.toHaveBeenCalled();
  });
});
