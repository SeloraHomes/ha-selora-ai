/**
 * Build identity for the code-skew handshake — and the list of inputs it covers.
 *
 * The panel reports this id to `selora_ai/version_status`, which compares it
 * with `panel.build.json` on disk; a mismatch means the browser is running a
 * bundle other than the deployed one and is told to reload. So the id has to
 * cover EVERY input that can change the bundle's bytes:
 *
 * - the panel sources under `src/`;
 * - the scripts that transform them (`build.js`, `postbuild.js`, this file);
 * - the dependency manifests — Lit ships *inside* the bundle, so upgrading it
 *   (or esbuild) rewrites `panel.js` without touching a single source file;
 * - the values esbuild substitutes via `define` (`__SELORA_VERSION__` comes from
 *   manifest.json, so a release that only bumps the version still produces a
 *   different bundle).
 *
 * manifest.json is deliberately covered through that define rather than hashed
 * whole: its other fields (requirements, iot_class…) don't reach the bundle, and
 * hashing them would churn the id — and the committed `panel.js` — for nothing.
 *
 * Derived from contents rather than a timestamp so rebuilding unchanged inputs
 * produces no diff. Paths are hashed relative to this directory so the id is
 * identical on every machine.
 */

const { createHash } = require("crypto");
const { readFileSync, readdirSync, statSync } = require("fs");
const { join } = require("path");

const FRONTEND_DIR = __dirname;

// Excluded from the hash because it *is* the hash — including it would be
// circular. Kept as a named constant so the exclusion can't drift silently.
const SELF_KEY = "__SELORA_BUILD__";

const ID_CHARS = 12;

// Inputs that aren't panel sources but still decide the bundle's bytes.
const NON_SOURCE_INPUTS = [
  "package.json",
  "package-lock.json",
  "build.js",
  "postbuild.js",
  "build-id.js",
];

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

/** Every file whose contents feed the bundle. */
function bundleInputs() {
  return [...walkJs("src"), ...NON_SOURCE_INPUTS].sort();
}

/**
 * @param {object} args
 * @param {string[]} args.files - paths, relative to `root`, of the bundle inputs
 * @param {Record<string, string>} args.defines - esbuild `define` map
 * @param {string} [args.root] - directory the paths are relative to
 * @returns {string} hex build id
 */
function computeBuildId({ files, defines = {}, root = FRONTEND_DIR }) {
  const hash = createHash("sha256");
  for (const file of [...files].sort()) {
    hash.update(file);
    hash.update("\0");
    hash.update(readFileSync(join(root, file)));
    hash.update("\0");
  }
  for (const key of Object.keys(defines).sort()) {
    if (key === SELF_KEY) continue;
    hash.update(key);
    hash.update("\0");
    hash.update(String(defines[key]));
    hash.update("\0");
  }
  return hash.digest("hex").slice(0, ID_CHARS);
}

module.exports = {
  bundleInputs,
  computeBuildId,
  walkJs,
  FRONTEND_DIR,
  NON_SOURCE_INPUTS,
  SELF_KEY,
  ID_CHARS,
};
