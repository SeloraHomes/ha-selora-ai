import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "fs";
import { join } from "path";
import { tmpdir } from "os";
import {
  bundleInputs,
  computeBuildId,
  FRONTEND_DIR,
  SELF_KEY,
} from "../build-id.js";
import { existsSync } from "fs";

// The id is what tells a browser its panel is stale. Anything that can change
// the bundle's bytes has to change the id, or a cached bundle is reported as
// current and the reload prompt never appears.
describe("computeBuildId", () => {
  let dir;
  let a;
  let b;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "selora-build-id-"));
    a = "a.js";
    b = "b.js";
    writeFileSync(join(dir, a), "export const A = 1;\n");
    writeFileSync(join(dir, b), "export const B = 2;\n");
  });

  afterEach(() => rmSync(dir, { recursive: true, force: true }));

  const id = (files, defines) => computeBuildId({ files, defines, root: dir });

  it("is stable for identical inputs", () => {
    expect(id([a, b], { X: '"1"' })).toBe(id([a, b], { X: '"1"' }));
  });

  it("does not depend on the order files are passed in", () => {
    expect(id([a, b], {})).toBe(id([b, a], {}));
  });

  it("changes when a source file's contents change", () => {
    const before = id([a, b], {});
    writeFileSync(join(dir, a), "export const A = 99;\n");
    expect(id([a, b], {})).not.toBe(before);
  });

  it("changes when a file is dropped from the bundle", () => {
    expect(id([a], {})).not.toBe(id([a, b], {}));
  });

  it("changes when identical contents move to another path", () => {
    const moved = "c.js";
    writeFileSync(join(dir, moved), "export const B = 2;\n");
    expect(id([a, moved], {})).not.toBe(id([a, b], {}));
  });

  it("changes when an injected define changes", () => {
    // The release-only case: manifest.json bumps, __SELORA_VERSION__ is
    // substituted into the bundle, no source file is touched. Without the
    // defines in the hash, an old cached bundle reports the deployed id.
    const before = id([a, b], { __SELORA_VERSION__: '"0.13.0"' });
    const after = id([a, b], { __SELORA_VERSION__: '"0.14.0"' });
    expect(after).not.toBe(before);
  });

  it("changes when a define is added", () => {
    expect(id([a, b], { NEW: '"x"' })).not.toBe(id([a, b], {}));
  });

  it("ignores its own key so the hash can't be circular", () => {
    const plain = id([a, b], { __SELORA_VERSION__: '"0.13.0"' });
    const withSelf = id([a, b], {
      __SELORA_VERSION__: '"0.13.0"',
      [SELF_KEY]: '"whatever"',
    });
    expect(withSelf).toBe(plain);
  });

  it("returns a short hex id", () => {
    const value = id([a, b], {});
    expect(value).toMatch(/^[0-9a-f]{12}$/);
  });

  it("hashes paths relative to the root, so ids match across machines", () => {
    // An absolute path in the hash would make every checkout produce a
    // different id and churn the committed bundle between dev and CI.
    const other = mkdtempSync(join(tmpdir(), "selora-build-id-other-"));
    try {
      writeFileSync(join(other, a), "export const A = 1;\n");
      writeFileSync(join(other, b), "export const B = 2;\n");
      expect(computeBuildId({ files: [a, b], root: other })).toBe(
        computeBuildId({ files: [a, b], root: dir }),
      );
    } finally {
      rmSync(other, { recursive: true, force: true });
    }
  });
});

// Three review rounds found a *missing input* rather than a broken hash, so pin
// the list itself: anything that can rewrite panel.js has to appear here.
describe("bundleInputs", () => {
  const inputs = bundleInputs();

  it("covers the dependency manifests", () => {
    // Lit is bundled into panel.js, so a lockfile bump rewrites the output
    // with no source change. Without these, that bundle keeps the old id and
    // browsers on the previous build are never told to reload.
    expect(inputs).toContain("package.json");
    expect(inputs).toContain("package-lock.json");
  });

  it("covers the scripts that transform the sources", () => {
    expect(inputs).toContain("build.js");
    expect(inputs).toContain("postbuild.js");
    expect(inputs).toContain("build-id.js");
  });

  it("covers the panel sources", () => {
    expect(inputs).toContain("src/panel.js");
    expect(inputs.filter((f) => f.startsWith("src/")).length).toBeGreaterThan(
      50,
    );
  });

  it("excludes node_modules", () => {
    expect(inputs.some((f) => f.includes("node_modules"))).toBe(false);
  });

  it("lists real, deduplicated, relative paths", () => {
    expect(new Set(inputs).size).toBe(inputs.length);
    for (const file of inputs) {
      expect(file.startsWith("/")).toBe(false);
      expect(existsSync(join(FRONTEND_DIR, file))).toBe(true);
    }
  });
});
