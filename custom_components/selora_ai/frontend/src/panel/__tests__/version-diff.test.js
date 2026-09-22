import { describe, it, expect } from "vitest";
import { versionDiff } from "../render-version-history.js";

const V = (id, yaml) => ({ version_id: id, yaml });

// `_loadVersionHistory` reverses the store's order, so index 0 is the NEWEST.
const VERSIONS = [
  V("v3", "alias: x\nmode: restart\n"),
  V("v2", "alias: x\n"),
  V("v1", "alias: w\n"),
];

describe("versionDiff", () => {
  it("diffs the version below against this one, so its lines read as additions", () => {
    const diff = versionDiff({}, "a1", VERSIONS, 0);
    expect(diff.added).toBe(1);
    expect(diff.removed).toBe(0);
    expect(diff.lines).toContainEqual({ type: "add", text: "mode: restart" });
  });

  it("reads a replaced value as one -/+ pair", () => {
    const diff = versionDiff({}, "a1", VERSIONS, 1);
    expect(diff.added).toBe(1);
    expect(diff.removed).toBe(1);
  });

  it("is null on the oldest version, which changed nothing before it", () => {
    expect(versionDiff({}, "a1", VERSIONS, 2)).toBeNull();
  });

  it("is null when either side has no stored YAML", () => {
    const versions = [V("v2", "alias: x\n"), V("v1", "")];
    expect(versionDiff({}, "a1", versions, 0)).toBeNull();
  });

  it("reads yaml_content when that is the spelling stored", () => {
    const versions = [
      { version_id: "v2", yaml_content: "alias: x\nmode: single\n" },
      { version_id: "v1", yaml_content: "alias: x\n" },
    ];
    expect(versionDiff({}, "a1", versions, 0).added).toBe(1);
  });

  it("reuses the cached diff while both documents are unchanged", () => {
    const host = {};
    const first = versionDiff(host, "a1", VERSIONS, 0);
    expect(versionDiff(host, "a1", VERSIONS, 0)).toBe(first);

    // A restore rewrites the version's contents under the same id.
    const rewritten = [
      V("v3", "alias: x\nmode: queued\n"),
      V("v2", "alias: x\n"),
    ];
    expect(versionDiff(host, "a1", rewritten, 0)).not.toBe(first);
  });

  it("keys the cache per automation, so two automations do not share a diff", () => {
    const host = {};
    const other = [V("v3", "alias: y\nmode: queued\n"), V("v2", "alias: y\n")];
    const a = versionDiff(host, "a1", VERSIONS, 0);
    const b = versionDiff(host, "a2", other, 0);
    expect(b).not.toBe(a);
    expect(versionDiff(host, "a1", VERSIONS, 0)).toBe(a);
  });
});
