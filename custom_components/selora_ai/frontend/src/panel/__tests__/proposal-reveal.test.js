import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { _markProposalRevealing, REVEAL_TOTAL_MS } from "../proposal-reveal.js";

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
