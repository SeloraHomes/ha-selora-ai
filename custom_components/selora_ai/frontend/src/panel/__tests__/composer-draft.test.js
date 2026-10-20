import { describe, it, expect, beforeEach, afterEach } from "vitest";
import {
  loadComposerDraft,
  restoreComposerDraft,
  saveComposerDraft,
} from "../composer-draft.js";

const KEY = "selora_ai_composer_draft:alice";
const CHIP = { label: "Kitchen Light", entity_id: "light.kitchen" };

function memoryStorage() {
  const data = new Map();
  return {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
    removeItem: (k) => data.delete(k),
  };
}

function makeHost(userId = "alice", props = {}) {
  return {
    hass: userId ? { user: { id: userId } } : undefined,
    _input: "",
    _autocompleteSelections: [],
    _historyIndex: null,
    _draftRestored: true,
    ...props,
  };
}

describe("composer draft", () => {
  let original;
  beforeEach(() => {
    original = globalThis.localStorage;
    globalThis.localStorage = memoryStorage();
  });
  afterEach(() => {
    globalThis.localStorage = original;
  });

  it("round-trips the text and its entity chips", () => {
    saveComposerDraft(
      makeHost("alice", {
        _input: "turn on Kitchen Light",
        _autocompleteSelections: [CHIP],
      }),
    );
    const reloaded = makeHost("alice", { _draftRestored: false });
    restoreComposerDraft(reloaded);
    expect(reloaded._input).toBe("turn on Kitchen Light");
    expect(reloaded._autocompleteSelections).toEqual([CHIP]);
  });

  it("never shows one user's draft to another", () => {
    saveComposerDraft(makeHost("alice", { _input: "a private thought" }));
    const bob = makeHost("bob", { _draftRestored: false });
    restoreComposerDraft(bob);
    expect(bob._input).toBe("");
  });

  it("waits for the user before restoring or saving", () => {
    globalThis.localStorage.setItem(KEY, JSON.stringify({ text: "kept" }));
    const host = makeHost(null, { _draftRestored: false });
    restoreComposerDraft(host);
    saveComposerDraft(host);
    expect(host._draftRestored).toBe(false);
    // The empty composer of a panel still loading must not erase the draft.
    host.hass = { user: { id: "alice" } };
    saveComposerDraft(host);
    expect(globalThis.localStorage.getItem(KEY)).not.toBeNull();
    restoreComposerDraft(host);
    expect(host._input).toBe("kept");
  });

  it("does not overwrite text typed before the restore", () => {
    globalThis.localStorage.setItem(KEY, JSON.stringify({ text: "old" }));
    const host = makeHost("alice", { _draftRestored: false, _input: "new" });
    restoreComposerDraft(host);
    expect(host._input).toBe("new");
    expect(loadComposerDraft(host).text).toBe("new");
  });

  it("clears the draft once the composer is emptied", () => {
    saveComposerDraft(makeHost("alice", { _input: "hello" }));
    saveComposerDraft(makeHost("alice", { _input: "" }));
    expect(globalThis.localStorage.getItem(KEY)).toBeNull();
  });

  it("keeps the draft while browsing sent messages", () => {
    saveComposerDraft(makeHost("alice", { _input: "my draft" }));
    const browsing = makeHost("alice", {
      _input: "an old message",
      _historyIndex: 0,
      _historyRecalled: "an old message",
    });
    saveComposerDraft(browsing);
    expect(loadComposerDraft(makeHost()).text).toBe("my draft");
    // Editing the recalled message makes it the text worth keeping.
    browsing._input = "an old message, edited";
    saveComposerDraft(browsing);
    expect(loadComposerDraft(makeHost()).text).toBe("an old message, edited");
  });

  it("keeps a chip whose label only changed case", () => {
    saveComposerDraft(
      makeHost("alice", {
        _input: "turn on kitchen light",
        _autocompleteSelections: [CHIP],
      }),
    );
    expect(loadComposerDraft(makeHost()).selections).toEqual([CHIP]);
  });

  it("drops chips whose label left the text", () => {
    globalThis.localStorage.setItem(
      KEY,
      JSON.stringify({ text: "turn on the fan", selections: [CHIP] }),
    );
    expect(loadComposerDraft(makeHost()).selections).toEqual([]);
  });

  it("ignores corrupt or missing storage", () => {
    globalThis.localStorage.setItem(KEY, "{not json");
    expect(loadComposerDraft(makeHost())).toBeNull();
    globalThis.localStorage = undefined;
    expect(loadComposerDraft(makeHost())).toBeNull();
    expect(() =>
      saveComposerDraft(makeHost("alice", { _input: "x" })),
    ).not.toThrow();
  });
});
