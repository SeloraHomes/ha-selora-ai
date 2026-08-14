/**
 * Build identity for the code-skew handshake.
 *
 * The panel reports this id to `selora_ai/version_status`, which compares it
 * with `panel.build.json` on disk; a mismatch means the browser is running a
 * bundle other than the deployed one and is told to reload. The question that
 * handshake asks is only ever about the artifact — "are these the bytes on
 * disk?" — so the id is the hash of the built bundle itself, taken after every
 * transform that shapes it (esbuild, `postbuild.js`, prettier).
 *
 * Hashing the *inputs* instead answers a strictly broader question, and the
 * excess is not free. Every input that can't change the output still moves the
 * id: `package.json`/`package-lock.json` were hashed whole, so bumping a
 * devDependency that never reaches the bundle — prettier, vitest, eslint —
 * rewrote `panel.build.json` and, since the id is substituted *into* the
 * bundle, one line of the committed `panel.js` too. Renovate rebuilds on every
 * branch, so all of them edited those same two generated lines and the first to
 * merge conflicted the rest. Deriving from output makes a no-op upgrade a no-op
 * diff, while a bump that genuinely rewrites the bundle (Lit ships *inside* it;
 * esbuild and prettier decide its formatting) still moves the id — the coverage
 * that matters is kept, the churn is not.
 *
 * The id can't be hashed from the bundle it lives in, so `build.js` builds
 * twice: once with `BUILD_PLACEHOLDER` standing in, whose output is hashed, then
 * again with the real id. The id therefore identifies the bundle modulo itself.
 *
 * Derived from contents rather than a timestamp so rebuilding unchanged sources
 * produces no diff.
 */

const { createHash } = require("crypto");
const { readdirSync, statSync } = require("fs");
const { join } = require("path");

const FRONTEND_DIR = __dirname;

// Substituted for the id on the hashed pass. Its value is arbitrary — it just
// has to be constant, so that identical sources hash identically.
const BUILD_PLACEHOLDER = "__selora_build_placeholder__";

// The define esbuild substitutes the id through.
const SELF_KEY = "__SELORA_BUILD__";

const ID_CHARS = 12;

/** Yield every `*.js` path under `dir`, relative to the frontend directory. */
function* walkJs(dir) {
  for (const name of readdirSync(join(FRONTEND_DIR, dir))) {
    if (name === "node_modules" || name.startsWith(".")) continue;
    const relative = join(dir, name);
    const st = statSync(join(FRONTEND_DIR, relative));
    if (st.isDirectory()) yield* walkJs(relative);
    else if (st.isFile() && name.endsWith(".js")) yield relative;
  }
}

/**
 * Hash the built bundle into a build id.
 *
 * @param {Buffer|string} bundle - the finished `panel.js` bytes, built with
 *   `BUILD_PLACEHOLDER` in place of the id
 * @returns {string} hex build id
 */
function computeBuildId(bundle) {
  return createHash("sha256").update(bundle).digest("hex").slice(0, ID_CHARS);
}

module.exports = {
  computeBuildId,
  walkJs,
  FRONTEND_DIR,
  BUILD_PLACEHOLDER,
  SELF_KEY,
  ID_CHARS,
};
