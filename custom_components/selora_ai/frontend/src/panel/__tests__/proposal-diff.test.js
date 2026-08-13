import { describe, it, expect, vi } from "vitest";
import {
  proposalDiff,
  resetProposalDiffState,
  invalidateProposalPreviews,
  toggleProposalDiff,
} from "../render-proposal-diff.js";

// Message shapes as the chat session stores them.
const refining = (id) => ({
  automation_status: "refining",
  automation_id: id,
  automation_yaml: "alias: A\n",
});
const proposal = (yaml, refiningId = null) => ({
  automation_status: "pending",
  refining_automation_id: refiningId,
  automation_yaml: yaml,
  automation: { alias: "A" },
});
const user = (content) => ({ role: "user", content });

const CURRENT = "alias: A\nid: X\nmode: single\n";
const PROPOSED = "alias: A\nid: X\nmode: queued\n";

// The preview is fetched asynchronously and the first request for a card is
// scheduled on a zero timer, so let both the timer and its promise settle.
const flush = () => new Promise((resolve) => setTimeout(resolve, 5));
// Long enough for a re-keyed request to clear PREVIEW_DEBOUNCE_MS.
const settleDebounce = () => new Promise((resolve) => setTimeout(resolve, 400));

function makeHost({ messages, reply, edited } = {}) {
  const calls = [];
  return {
    calls,
    host: {
      _messages: messages,
      _editedYaml: edited || {},
      hass: {
        callWS: async (payload) => {
          calls.push(payload);
          if (typeof reply === "function") return reply(payload);
          return reply ?? { current_yaml: CURRENT, proposed_yaml: PROPOSED };
        },
      },
      requestUpdate: () => {},
    },
  };
}

const session = (yaml = "alias: A\nmode: queued\n") => [
  refining("X"),
  user("tweak"),
  proposal(yaml, "X"),
];

describe("proposalDiff", () => {
  it("shows nothing until the preview arrives", () => {
    const { host } = makeHost({ messages: session() });
    expect(proposalDiff(host, 2)).toBeNull();
  });

  it("diffs the two sides the backend reports", async () => {
    const { host } = makeHost({ messages: session() });
    proposalDiff(host, 2);
    await flush();
    const diff = proposalDiff(host, 2);
    expect(diff.lines).toContainEqual({ type: "del", text: "mode: single" });
    expect(diff.lines).toContainEqual({ type: "add", text: "mode: queued" });
    // `id` is identical on both sides because the save rewrites it, so it stays
    // ordinary context rather than needing to be filtered out here.
    expect(diff.lines).toContainEqual({ type: "ctx", text: "id: X" });
  });

  it("asks about the automation the accept path will write", async () => {
    const { host, calls } = makeHost({ messages: session() });
    proposalDiff(host, 2);
    await flush();
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({
      type: "selora_ai/preview_automation_write",
      automation_id: "X",
      // The chat accept path preserves the enabled state; so must the preview,
      // or it would describe a different call.
      preserve_enabled_state: true,
    });
  });

  it("previews the text the user would submit, edits included", async () => {
    const { host, calls } = makeHost({
      messages: session(),
      edited: { proposal_2: "alias: A\nmode: restart\n" },
    });
    proposalDiff(host, 2);
    await flush();
    expect(calls[0].yaml_text).toBe("alias: A\nmode: restart\n");
  });

  it("does not ask when accepting will create a new automation", async () => {
    const { host, calls } = makeHost({
      messages: [user("make me one"), proposal("alias: A\n")],
    });
    expect(proposalDiff(host, 1)).toBeNull();
    await flush();
    expect(calls).toHaveLength(0);
  });

  it("shows nothing when the preview cannot be produced", async () => {
    const { host } = makeHost({
      messages: session(),
      reply: () => {
        throw new Error("not_found");
      },
    });
    proposalDiff(host, 2);
    await flush();
    expect(proposalDiff(host, 2)).toBeNull();
  });

  it("collapses a burst of edits into one request", async () => {
    vi.useFakeTimers();
    try {
      const edited = {};
      const { host, calls } = makeHost({ messages: session(), edited });
      // First render fires immediately, so the chip is not delayed.
      proposalDiff(host, 2);
      await vi.advanceTimersByTimeAsync(1);
      expect(calls).toHaveLength(1);

      // Typing: every keystroke changes the text and reschedules.
      for (const text of ["m", "mo", "mod", "mode"]) {
        edited.proposal_2 = `alias: A\n${text}\n`;
        proposalDiff(host, 2);
        await vi.advanceTimersByTimeAsync(50);
      }
      expect(calls).toHaveLength(1);

      await vi.advanceTimersByTimeAsync(400);
      expect(calls).toHaveLength(2);
      expect(calls[1].yaml_text).toBe("alias: A\nmode\n");
    } finally {
      vi.useRealTimers();
    }
  });

  it("drops a scheduled preview when the session changes", async () => {
    vi.useFakeTimers();
    try {
      const edited = {};
      const { host, calls } = makeHost({ messages: session(), edited });
      proposalDiff(host, 2);
      await vi.advanceTimersByTimeAsync(1);
      edited.proposal_2 = "alias: A\nmode: restart\n";
      proposalDiff(host, 2);

      resetProposalDiffState(host);
      await vi.advanceTimersByTimeAsync(1000);
      expect(calls).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("proposalDiff request shape", () => {
  const GENERATED = "alias: A\ninitial_state: true\nmode: queued\n";

  const withEdit = (editedText) => {
    const messages = [refining("X"), user("tweak"), proposal(GENERATED, "X")];
    return makeHost({
      messages,
      edited: editedText === null ? {} : { proposal_2: editedText },
    });
  };

  it("preserves the enabled state when the edit leaves initial_state alone", async () => {
    const { host, calls } = withEdit(
      "alias: A\ninitial_state: true\nmode: restart\n",
    );
    proposalDiff(host, 2);
    await flush();
    expect(calls[0].preserve_enabled_state).toBe(true);
  });

  it("hands the edit authority when the user changed initial_state", async () => {
    // Mirrors _acceptAutomationWithEdits, which sends preserve_enabled_state:
    // false here — previewing the other call would hide the enable/disable.
    const { host, calls } = withEdit(
      "alias: A\ninitial_state: false\nmode: queued\n",
    );
    proposalDiff(host, 2);
    await flush();
    expect(calls[0].preserve_enabled_state).toBe(false);
  });

  it("hands the edit authority when the user removed initial_state", async () => {
    const { host, calls } = withEdit("alias: A\nmode: queued\n");
    proposalDiff(host, 2);
    await flush();
    expect(calls[0].preserve_enabled_state).toBe(false);
  });
});

describe("proposalDiff staleness", () => {
  it("re-requests after an invalidation, without blanking the panel", async () => {
    let reply = { current_yaml: CURRENT, proposed_yaml: PROPOSED };
    const { host, calls } = makeHost({
      messages: session(),
      reply: () => reply,
    });
    proposalDiff(host, 2);
    await flush();
    expect(proposalDiff(host, 2).lines).toContainEqual({
      type: "del",
      text: "mode: single",
    });

    // The automation is edited in HA's editor and the panel reloads it.
    reply = {
      current_yaml: "alias: A\nid: X\nmode: parallel\n",
      proposed_yaml: PROPOSED,
    };
    invalidateProposalPreviews(host);

    // The stale answer keeps rendering while the new one is in flight.
    expect(proposalDiff(host, 2)).not.toBeNull();
    await flush();
    expect(calls).toHaveLength(2);
    expect(proposalDiff(host, 2).lines).toContainEqual({
      type: "del",
      text: "mode: parallel",
    });
  });

  it("re-requests when the diff panel is reopened", async () => {
    const { host, calls } = makeHost({ messages: session() });
    proposalDiff(host, 2);
    await flush();
    expect(calls).toHaveLength(1);

    toggleProposalDiff(host, 2); // open
    proposalDiff(host, 2);
    await flush();
    expect(calls).toHaveLength(2);

    toggleProposalDiff(host, 2); // close — nothing to refresh
    proposalDiff(host, 2);
    await flush();
    expect(calls).toHaveLength(2);
  });
});

// A refresh can be asked for while the previous request is still awaiting its
// answer — the timer handle is already gone by then, so scheduling alone does
// not tell you whether a request is outstanding.
describe("proposalDiff concurrent refreshes", () => {
  const deferredHost = (messages = session()) => {
    const calls = [];
    const resolvers = [];
    return {
      calls,
      resolvers,
      host: {
        _messages: messages,
        _editedYaml: {},
        hass: {
          callWS: (payload) => {
            calls.push(payload);
            return new Promise((resolve) => resolvers.push(resolve));
          },
        },
        requestUpdate: () => {},
      },
    };
  };

  it("waits for the in-flight request instead of racing it", async () => {
    const { host, calls, resolvers } = deferredHost();
    proposalDiff(host, 2);
    await flush();
    expect(calls).toHaveLength(1); // in flight, timer already cleared

    invalidateProposalPreviews(host);
    proposalDiff(host, 2);
    invalidateProposalPreviews(host);
    proposalDiff(host, 2);
    await flush();
    expect(calls).toHaveLength(1);

    // The follow-up goes out once the slot frees, not three times over.
    resolvers[0]({ current_yaml: CURRENT, proposed_yaml: PROPOSED });
    await flush();
    expect(calls).toHaveLength(2);
  });

  it("drops an answer that a newer request has superseded", async () => {
    const { host, resolvers } = deferredHost();
    proposalDiff(host, 2);
    await flush();

    // The automation changes on disk twice; only the newest answer counts.
    invalidateProposalPreviews(host);
    proposalDiff(host, 2);
    resolvers[0]({
      current_yaml: "alias: A\nid: X\nmode: stale\n",
      proposed_yaml: PROPOSED,
    });
    await flush();
    resolvers[1]({
      current_yaml: "alias: A\nid: X\nmode: fresh\n",
      proposed_yaml: PROPOSED,
    });
    await flush();

    const texts = proposalDiff(host, 2).lines.map((l) => l.text);
    expect(texts).toContain("mode: fresh");
    expect(texts).not.toContain("mode: stale");
  });

  it("ignores a slow answer once the request parameters change", async () => {
    const { host, resolvers } = deferredHost();
    proposalDiff(host, 2);
    await flush();

    // The user types in the YAML editor: a different document is now proposed.
    host._editedYaml.proposal_2 = "alias: A\nmode: restart\n";
    proposalDiff(host, 2);
    await settleDebounce();

    // The first request answers late.
    resolvers[0]({
      current_yaml: "alias: A\nid: X\nmode: stale\n",
      proposed_yaml: PROPOSED,
    });
    await flush();
    expect(proposalDiff(host, 2)).toBeNull(); // still waiting on the live one

    resolvers[1]({
      current_yaml: CURRENT,
      proposed_yaml: "alias: A\nid: X\nmode: restart\n",
    });
    await flush();
    const texts = proposalDiff(host, 2).lines.map((l) => l.text);
    expect(texts).toContain("mode: restart");
    expect(texts).not.toContain("mode: stale");
  });
});
