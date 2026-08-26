import { describe, it, expect } from "vitest";
import { savedDiff } from "../render-proposal-diff.js";

const V = (id, yaml) => ({ version_id: id, yaml });

function host(versions, { messages } = {}) {
  const h = {
    _messages: messages || [
      { automation_id: "a1", automation_status: "saved" },
    ],
    _versions: versions,
    _loadCalls: [],
    _loadVersionHistory(id) {
      this._loadCalls.push(id);
    },
    requestUpdate() {},
  };
  return h;
}

describe("savedDiff", () => {
  it("diffs older -> newer, so the new version's lines read as additions", () => {
    // `_loadVersionHistory` reverses, so index 0 is the NEWEST.
    const h = host({
      a1: [V("new", "alias: x\nmode: restart\n"), V("old", "alias: x\n")],
    });
    const diff = savedDiff(h, 0);
    expect(diff.added).toBe(1);
    expect(diff.removed).toBe(0);
  });

  it("is null on a create, where there is nothing before it", () => {
    expect(savedDiff(host({ a1: [V("only", "alias: x\n")] }), 0)).toBeNull();
  });

  it("fetches the history once and renders nothing until it lands", () => {
    const h = host({});
    expect(savedDiff(h, 0)).toBeNull();
    expect(savedDiff(h, 0)).toBeNull();
    expect(savedDiff(h, 0)).toBeNull();
    // A render returning null must not schedule another fetch every pass.
    expect(h._loadCalls).toEqual(["a1"]);
  });

  it("is null for a message with no saved automation", () => {
    const h = host({ a1: [V("new", "x"), V("old", "y")] }, { messages: [{}] });
    expect(savedDiff(h, 0)).toBeNull();
    expect(h._loadCalls).toEqual([]);
  });

  it("recomputes when the stored versions change", () => {
    const h = host({
      a1: [V("new", "alias: x\nmode: restart\n"), V("old", "alias: x\n")],
    });
    const first = savedDiff(h, 0);
    h._versions = {
      a1: [
        V("newer", "alias: x\nmode: single\nmax: 3\n"),
        V("old", "alias: x\n"),
      ],
    };
    const second = savedDiff(h, 0);
    expect(second).not.toBe(first);
    expect(second.added).toBe(2);
  });
});
