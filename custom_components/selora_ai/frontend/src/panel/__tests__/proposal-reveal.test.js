import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  _clearProposalReveals,
  _markProposalRevealing,
  BUILD_PIECE_MS,
  BUILD_SPAN_MS,
  BUILD_START_MS,
  BUILD_STEP_MS,
  REVEAL_TOTAL_MS,
  buildDelays,
  clearProposalBuild,
  stageProposalBuild,
} from "../proposal-reveal.js";

function makeHost() {
  return {
    _revealingProposals: {},
    _revealTimers: {},
    requestUpdate: vi.fn(),
  };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("_markProposalRevealing", () => {
  it("flags the arriving proposal so its card plays the reveal", () => {
    const host = makeHost();
    _markProposalRevealing.call(host, 3);
    expect(host._revealingProposals[3]).toBe(true);
  });

  it("clears the flag once the reveal finishes", () => {
    const host = makeHost();
    _markProposalRevealing.call(host, 3);
    vi.advanceTimersByTime(REVEAL_TOTAL_MS - 1);
    expect(host._revealingProposals[3]).toBe(true);
    vi.advanceTimersByTime(1);
    // Must be absent, not false: the flag also gates whether the particle
    // canvas is in the template, and it has to leave the DOM to stop its
    // requestAnimationFrame loop.
    expect(3 in host._revealingProposals).toBe(false);
    expect(host._revealTimers[3]).toBeUndefined();
    expect(host.requestUpdate).toHaveBeenCalled();
  });

  it("does not disturb another card mid-reveal when it clears", () => {
    const host = makeHost();
    _markProposalRevealing.call(host, 1);
    vi.advanceTimersByTime(200);
    _markProposalRevealing.call(host, 2);
    vi.advanceTimersByTime(REVEAL_TOTAL_MS - 200);
    // First card's timer fired; the second is still playing.
    expect(1 in host._revealingProposals).toBe(false);
    expect(host._revealingProposals[2]).toBe(true);
  });

  it("restarts the window when the same index is re-marked", () => {
    const host = makeHost();
    _markProposalRevealing.call(host, 0);
    vi.advanceTimersByTime(REVEAL_TOTAL_MS - 100);
    _markProposalRevealing.call(host, 0);
    // The original timer must have been cancelled, not left to fire early.
    vi.advanceTimersByTime(150);
    expect(host._revealingProposals[0]).toBe(true);
    vi.advanceTimersByTime(REVEAL_TOTAL_MS);
    expect(0 in host._revealingProposals).toBe(false);
  });

  it("ignores a missing or negative index", () => {
    const host = makeHost();
    _markProposalRevealing.call(host, null);
    _markProposalRevealing.call(host, -1);
    expect(host._revealingProposals).toEqual({});
    expect(Object.keys(host._revealTimers)).toEqual([]);
  });
});

describe("buildDelays", () => {
  it("spaces pieces one step apart after the card starts rising", () => {
    expect(buildDelays(3)).toEqual([
      BUILD_START_MS,
      BUILD_START_MS + BUILD_STEP_MS,
      BUILD_START_MS + 2 * BUILD_STEP_MS,
    ]);
  });

  it("compresses a long automation so the last piece starts within the span", () => {
    const delays = buildDelays(40);
    expect(delays.at(-1)).toBe(BUILD_START_MS + BUILD_SPAN_MS);
    // Still strictly in reading order.
    for (let i = 1; i < delays.length; i++) {
      expect(delays[i]).toBeGreaterThan(delays[i - 1]);
    }
  });

  it("ends every piece before the reveal flag clears", () => {
    expect(buildDelays(200).at(-1) + BUILD_PIECE_MS).toBeLessThan(
      REVEAL_TOTAL_MS,
    );
  });

  it("handles a single piece and none", () => {
    expect(buildDelays(1)).toEqual([BUILD_START_MS]);
    expect(buildDelays(0)).toEqual([]);
  });
});

function fakeEl(name) {
  const attrs = new Map();
  const props = new Map();
  return {
    name,
    attrs,
    props,
    setAttribute: (k, v) => attrs.set(k, v),
    removeAttribute: (k) => attrs.delete(k),
    style: {
      setProperty: (k, v) => props.set(k, v),
      removeProperty: (k) => props.delete(k),
    },
  };
}

function fakeCard({ footer = true, meta = true } = {}) {
  const header = fakeEl("header");
  const flow = [fakeEl("label"), fakeEl("trigger"), fakeEl("arrow")];
  const foot = footer ? fakeEl("footer") : null;
  const bubbleMeta = meta ? fakeEl("meta") : null;
  return {
    querySelector: (sel) =>
      sel.includes("header") ? header : sel.includes("footer") ? foot : null,
    querySelectorAll: () => flow,
    closest: () => ({ querySelector: () => bubbleMeta }),
  };
}

describe("stageProposalBuild", () => {
  it("tags header, flow, footer, then the action row, in that order", () => {
    const pieces = stageProposalBuild(fakeCard());
    expect(pieces.map((p) => p.name)).toEqual([
      "header",
      "label",
      "trigger",
      "arrow",
      "footer",
      "meta",
    ]);
    const delays = buildDelays(pieces.length);
    pieces.forEach((p, i) => {
      expect(p.attrs.has("data-build")).toBe(true);
      expect(p.props.get("--build-delay")).toBe(`${delays[i]}ms`);
    });
  });

  it("skips pieces the card doesn't render", () => {
    const pieces = stageProposalBuild(fakeCard({ footer: false, meta: false }));
    expect(pieces.map((p) => p.name)).toEqual([
      "header",
      "label",
      "trigger",
      "arrow",
    ]);
  });

  it("does nothing without a card", () => {
    expect(stageProposalBuild(null)).toEqual([]);
  });

  it("untags every piece so the build never replays", () => {
    const pieces = stageProposalBuild(fakeCard());
    clearProposalBuild(pieces);
    for (const p of pieces) {
      expect(p.attrs.size).toBe(0);
      expect(p.props.size).toBe(0);
    }
  });
});

describe("_clearProposalReveals", () => {
  it("cancels the timers and untags staged pieces", () => {
    const host = makeHost();
    _markProposalRevealing.call(host, 2);
    const pieces = stageProposalBuild(fakeCard());
    host._revealPieces = { 2: pieces };
    _clearProposalReveals.call(host);
    expect(host._revealingProposals).toEqual({});
    expect(host._revealPieces).toEqual({});
    for (const p of pieces) expect(p.attrs.size).toBe(0);
    // The cancelled timer never fires a late update.
    vi.advanceTimersByTime(REVEAL_TOTAL_MS);
    expect(host.requestUpdate).not.toHaveBeenCalled();
  });
});
