import { describe, it, expect } from "vitest";
import {
  sceneProposalDiff,
  sceneDiffKey,
  resetProposalDiffState,
  invalidateProposalPreviews,
} from "../render-proposal-diff.js";

const CURRENT = "entities:\n  light.a:\n    state: 'on'\nid: s1\n";
const PROPOSED = "entities:\n  light.a:\n    state: 'off'\nid: s1\n";

const sceneMsg = (extra = {}) => ({
  scene_status: "pending",
  scene: { name: "Salon ON", entities: { "light.a": { state: "on" } } },
  ...extra,
});

// Wait on the request itself rather than on a fixed delay: a busy machine
// makes a timing guess flaky, and the entry says plainly when it is done.
async function flush(host, key = sceneDiffKey(0)) {
  for (let i = 0; i < 200; i++) {
    const entry = host._scenePreviewCache?.get(key);
    if (entry && !entry.pending) return;
    await new Promise((resolve) => setTimeout(resolve, 1));
  }
  throw new Error("scene preview never settled");
}

function makeHost({ messages, reply } = {}) {
  const calls = [];
  return {
    calls,
    host: {
      _messages: messages,
      _activeSessionId: "sess-1",
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

describe("sceneProposalDiff", () => {
  it("shows nothing until the preview arrives", () => {
    const { host } = makeHost({ messages: [sceneMsg()] });
    expect(sceneProposalDiff(host, 0)).toBeNull();
  });

  it("diffs what accepting would overwrite against what it writes", async () => {
    const { host } = makeHost({ messages: [sceneMsg()] });
    sceneProposalDiff(host, 0);
    await flush(host);
    const diff = sceneProposalDiff(host, 0);
    expect(diff.added).toBe(1);
    expect(diff.removed).toBe(1);
    expect(diff.lines.map((l) => l.text)).toContain("    state: 'off'");
  });

  it("asks about the stored message index, not the local one", async () => {
    const { host, calls } = makeHost({
      messages: [sceneMsg({ scene_message_index: 7 })],
    });
    sceneProposalDiff(host, 0);
    await flush(host);
    expect(calls[0]).toEqual({
      type: "selora_ai/preview_scene_write",
      session_id: "sess-1",
      message_index: 7,
    });
  });

  it("has nothing to show when accepting creates", async () => {
    const { host } = makeHost({
      messages: [sceneMsg()],
      reply: { current_yaml: "", proposed_yaml: "" },
    });
    sceneProposalDiff(host, 0);
    await flush(host);
    expect(sceneProposalDiff(host, 0)).toBeNull();
  });

  it("swallows a failed preview rather than showing a guess", async () => {
    const { host } = makeHost({
      messages: [sceneMsg()],
      reply: () => {
        throw new Error("unknown command");
      },
    });
    sceneProposalDiff(host, 0);
    await flush(host);
    expect(sceneProposalDiff(host, 0)).toBeNull();
  });

  it("asks once and serves the cached answer after that", async () => {
    const { host, calls } = makeHost({ messages: [sceneMsg()] });
    sceneProposalDiff(host, 0);
    await flush(host);
    sceneProposalDiff(host, 0);
    sceneProposalDiff(host, 0);
    await flush(host);
    expect(calls).toHaveLength(1);
  });

  it("re-requests once invalidated, keeping the old answer meanwhile", async () => {
    let current = CURRENT;
    const { host, calls } = makeHost({
      messages: [sceneMsg()],
      reply: () => ({ current_yaml: current, proposed_yaml: PROPOSED }),
    });
    sceneProposalDiff(host, 0);
    await flush(host);
    current = "entities:\n  light.a:\n    state: 'on'\nid: s1\nname: X\n";
    invalidateProposalPreviews(host, sceneDiffKey(0));
    // Still answering from the previous preview rather than blanking.
    expect(sceneProposalDiff(host, 0)).not.toBeNull();
    await flush(host);
    expect(calls).toHaveLength(2);
    expect(sceneProposalDiff(host, 0).removed).toBe(2);
  });

  it("drops its state when the conversation is swapped", async () => {
    const { host, calls } = makeHost({ messages: [sceneMsg()] });
    sceneProposalDiff(host, 0);
    await flush(host);
    resetProposalDiffState(host);
    expect(sceneProposalDiff(host, 0)).toBeNull();
    await flush(host);
    expect(calls).toHaveLength(2);
  });

  it("is keyed apart from an automation card at the same index", () => {
    expect(sceneDiffKey(0)).not.toBe(0);
  });
});
