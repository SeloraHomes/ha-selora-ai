import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { loadSortPrefs, saveSortPref } from "../sort-prefs.js";

function memoryStorage() {
  const data = new Map();
  return {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
    removeItem: (k) => data.delete(k),
  };
}

const defaults = () => ({
  _sortBy: "recent",
  _sortDir: "desc",
  _sceneSortBy: "recent",
  _sceneSortDir: "desc",
});

describe("sort prefs", () => {
  let original;
  beforeEach(() => {
    original = globalThis.localStorage;
    globalThis.localStorage = memoryStorage();
  });
  afterEach(() => {
    globalThis.localStorage = original;
  });

  it("restores each list's order after a reload", () => {
    const before = {
      ...defaults(),
      _sceneSortBy: "alpha",
      _sceneSortDir: "asc",
    };
    saveSortPref(before, "scenes");
    before._sortBy = "enabled_first";
    saveSortPref(before, "automations");

    const after = defaults();
    loadSortPrefs(after);
    expect(after).toEqual({
      _sortBy: "enabled_first",
      _sortDir: "desc",
      _sceneSortBy: "alpha",
      _sceneSortDir: "asc",
    });
  });

  it("keeps the default for a value the list no longer offers", () => {
    globalThis.localStorage.setItem(
      "selora_ai_sort:scenes",
      JSON.stringify({ by: "gone", dir: "sideways" }),
    );
    const host = defaults();
    loadSortPrefs(host);
    expect(host).toEqual(defaults());
  });

  it("survives unreadable storage", () => {
    globalThis.localStorage.setItem("selora_ai_sort:scenes", "{not json");
    const host = defaults();
    expect(() => loadSortPrefs(host)).not.toThrow();
    expect(host).toEqual(defaults());
  });
});
