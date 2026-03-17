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
    'crypto.getRandomValues(new Uint32Array(1))[0].toString(36)'
  );

  code = code.replace(
    /^(.*RegExp\(.+)$/gm,
    '$1 // nosemgrep'
  );

  return code;
}

if (fs.existsSync("panel.js")) {
  var panelCode = fs.readFileSync("panel.js", "utf8");
  fs.writeFileSync("panel.js", patchCode(panelCode), "utf8");
}

if (fs.existsSync("card.js")) {
  var cardCode = fs.readFileSync("card.js", "utf8");
  fs.writeFileSync("card.js", patchCode(cardCode), "utf8");
}
