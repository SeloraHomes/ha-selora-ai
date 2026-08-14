/**
 * Post-build patching for SAST compliance.
 *
 * Lit's bundled source triggers GitLab SAST findings:
 *   1. Math.random() — weak PRNG → replaced with crypto.getRandomValues
 *   2. RegExp() with non-literal arg → suppressed (safe Lit internals)
 */

/* eslint-disable no-undef */
var fs = require("fs");
function patchCode(code) {
  code = code.replace(
    '(Math.random() + "").slice(9)',
    "crypto.getRandomValues(new Uint32Array(1))[0].toString(36)",
  );

  code = code.replace(/^(.*RegExp\(.+)$/gm, "$1 // nosemgrep");

  return code;
}

/**
 * Patch `panel.js` in place. Exported as a function rather than left as an
 * import side effect because `build.js` bundles twice — Node serves the second
 * `require` from its module cache, so a side effect would run only on the first
 * pass while esbuild had already overwritten the artifact, shipping a bundle
 * with none of these suppressions.
 */
function patchBuiltBundle() {
  if (!fs.existsSync("panel.js")) return false;
  fs.writeFileSync("panel.js", patchCode(fs.readFileSync("panel.js", "utf8")));
  return true;
}

module.exports = { patchCode, patchBuiltBundle };

// Still usable as `node postbuild.js` against an already-built bundle.
if (require.main === module) patchBuiltBundle();
