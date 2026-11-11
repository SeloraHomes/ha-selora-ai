// `_t()` never warns: a key missing from the translations silently renders
// its English fallback in every language. So every key the panel asks for by
// name must be in each locale's `common`.
import { readFileSync, readdirSync } from "fs";

import { describe, expect, it } from "vitest";

const SRC = new URL("../../", import.meta.url);
const TRANSLATIONS = new URL("../../../../translations/", import.meta.url);

function sources(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = new URL(entry.name + (entry.isDirectory() ? "/" : ""), dir);
    if (entry.isDirectory())
      return entry.name === "__tests__" ? [] : sources(path);
    return entry.name.endsWith(".js") ? [readFileSync(path, "utf8")] : [];
  });
}

const KEY = /\b_t\(\s*(["'`])([a-z0-9_]+)\1\s*,/g;
const asked = new Set(
  sources(SRC).flatMap((text) => [...text.matchAll(KEY)].map((m) => m[2])),
);

describe("translation keys", () => {
  it("finds the keys the panel asks for", () => {
    expect(asked.size).toBeGreaterThan(100);
  });

  for (const file of readdirSync(TRANSLATIONS).filter((f) =>
    f.endsWith(".json"),
  )) {
    it(`has every key in ${file}`, () => {
      const { common } = JSON.parse(
        readFileSync(new URL(file, TRANSLATIONS), "utf8"),
      );
      expect([...asked].filter((key) => !(key in common))).toEqual([]);
    });
  }
});
