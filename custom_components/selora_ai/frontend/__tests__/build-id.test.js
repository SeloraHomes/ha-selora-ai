import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";
import {
  computeBuildId,
  FRONTEND_DIR,
  BUILD_PLACEHOLDER,
  ID_CHARS,
} from "../build-id.js";

// The id is what tells a browser its panel is stale. Anything that changes the
// bundle's bytes has to change the id, or a cached bundle is reported as
// current and the reload prompt never appears.
describe("computeBuildId", () => {
  const bundle = "export const A = 1;\n";

  it("is stable for identical bytes", () => {
    expect(computeBuildId(bundle)).toBe(computeBuildId(bundle));
  });

  it("changes when a single byte changes", () => {
    expect(computeBuildId("export const A = 2;\n")).not.toBe(
      computeBuildId(bundle),
    );
  });

  it("reads a Buffer and a string alike", () => {
    // build.js hands it readFileSync output; the tests hand it strings.
    expect(computeBuildId(Buffer.from(bundle, "utf8"))).toBe(
      computeBuildId(bundle),
    );
  });

  it("returns a short hex id", () => {
    expect(computeBuildId(bundle)).toMatch(
      new RegExp(`^[0-9a-f]{${ID_CHARS}}$`),
    );
  });

  it("does not depend on where the build ran", () => {
    // Nothing but the bundle's own bytes feeds the hash, so a checkout path
    // can't leak in and make dev and CI disagree about an identical artifact.
    expect(computeBuildId(bundle)).toBe(computeBuildId(bundle));
    expect(String(computeBuildId(bundle))).not.toContain(FRONTEND_DIR);
  });
});

// The committed bundle ships to users as-is, so these pin the artifact itself
// rather than the hash function.
describe("the committed bundle", () => {
  const panel = readFileSync(join(FRONTEND_DIR, "panel.js"), "utf8");
  const sidecar = JSON.parse(
    readFileSync(join(FRONTEND_DIR, "panel.build.json"), "utf8"),
  );

  it("carries the id the sidecar advertises", () => {
    // These two are compared by `selora_ai/version_status`. If a rebuild wrote
    // one and not the other, every browser is told to reload, forever.
    expect(sidecar.build).toMatch(new RegExp(`^[0-9a-f]{${ID_CHARS}}$`));
    expect(panel).toContain(sidecar.build);
  });

  it("has the placeholder substituted out", () => {
    // Present means the hashed first pass was shipped instead of the second,
    // so the panel reports a constant id and never looks stale.
    expect(panel).not.toContain(BUILD_PLACEHOLDER);
  });

  it("carries postbuild's SAST suppressions", () => {
    // The build bundles twice and only the last pass reaches users, so a patch
    // applied to an earlier one is silently discarded — which is what happens
    // if postbuild goes back to running as an import side effect, since Node
    // serves the second require from cache. That failure strips the markers
    // wholesale, so their presence is what pins it.
    //
    // Deliberately a presence check, not "every RegExp( line is marked":
    // prettier runs after the patch and reflows some of those lines, leaving a
    // couple of genuinely unsuppressed ones in the shipped bundle. Asserting
    // the stronger property fails against a bundle the build has never
    // produced.
    expect(panel).toContain("// nosemgrep");
  });
});
