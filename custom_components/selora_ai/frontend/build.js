const { readFileSync, readdirSync, statSync } = require("fs");
const { join } = require("path");
const { execSync } = require("child_process");
const esbuild = require("esbuild");

// Format source files first
execSync("npx prettier --write 'src/**/*.js' build.js postbuild.js", {
  stdio: "inherit",
});

const manifest = JSON.parse(readFileSync("../manifest.json", "utf-8"));
const version = JSON.stringify(manifest.version);

const define = { __SELORA_VERSION__: version };

// ─ Pre-build guard: backticks inside lit tagged-template literals ──
// Lit's html`...` opens with a backtick and ANY raw backtick inside the
// template — including in CSS or HTML comments — closes the template
// early. esbuild bundles the broken result without complaining, so the
// failure surfaces in the browser as "x(...) is not a function" pointing
// at a CSS comment line.
//
// We scan every source file for ``html`...` `` blocks and reject any
// stray backticks inside them. Two safe escape hatches: ``\``` (the
// JS-level escape) or the placeholder hole ``${"`"}`` for a literal
// backtick at runtime. The guard runs before bundling so the failure
// shows up locally, never in production.
function* walkJs(dir) {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules" || name.startsWith(".")) continue;
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) yield* walkJs(full);
    else if (st.isFile() && name.endsWith(".js")) yield full;
  }
}

function scanForUnescapedBackticksInHtmlTemplates(path) {
  const src = readFileSync(path, "utf8");
  const issues = [];
  // Match html`...` and css`...` (greedy backtick close). We catch
  // both because CSS template literals have the same termination
  // problem — a stray backtick inside a /* */ block comment closes
  // the template early. We only catch top-level templates here;
  // nested template literals get their own scan since the inner
  // template's backticks are themselves balanced. Good enough —
  // most regressions are unbalanced top-level templates.
  const re = /\b(?:html|css)\s*`/g;
  let m;
  while ((m = re.exec(src)) !== null) {
    const start = m.index + m[0].length;
    // Walk to the matching close, tracking ${...} expression nesting
    // so backticks inside Lit expression interpolations don't trip us.
    let i = start;
    let depth = 0;
    while (i < src.length) {
      const ch = src[i];
      if (ch === "\\") {
        i += 2;
        continue;
      }
      if (ch === "$" && src[i + 1] === "{") {
        depth++;
        i += 2;
        continue;
      }
      if (ch === "}" && depth > 0) {
        depth--;
        i++;
        continue;
      }
      if (ch === "`" && depth === 0) {
        // Found the matching close.
        break;
      }
      i++;
    }
    // Build the static-text-only view of this template by stripping
    // the bytes inside ${...} expression holes (where backticks are
    // legal JS template-literal delimiters, not template-closers).
    // The remaining bytes are the static template text — that's
    // where stray backticks would break the template open early.
    let staticText = "";
    let staticOffsets = []; // map staticText pos -> source pos
    {
      let j = start;
      let d = 0;
      while (j < i) {
        const ch = src[j];
        if (ch === "\\") {
          if (d === 0) {
            staticText += src.slice(j, j + 2);
            staticOffsets.push(j, j + 1);
          }
          j += 2;
          continue;
        }
        if (ch === "$" && src[j + 1] === "{") {
          d++;
          j += 2;
          continue;
        }
        if (ch === "}" && d > 0) {
          d--;
          j++;
          continue;
        }
        if (d === 0) {
          staticText += ch;
          staticOffsets.push(j);
        }
        j++;
      }
    }
    // Pull out CSS/HTML comments from the static text only; flag any
    // that contain a backtick. The JS-comment form ``//`` doesn't
    // appear in template text in this codebase, so we only scan the
    // block-comment shapes the user actually writes.
    const commentRe = /\/\*[\s\S]*?\*\/|<!--[\s\S]*?-->/g;
    let cm;
    while ((cm = commentRe.exec(staticText)) !== null) {
      if (cm[0].includes("`")) {
        const offset = staticOffsets[cm.index] ?? start;
        const lineNum = src.slice(0, offset).split("\n").length;
        issues.push({ path, line: lineNum, snippet: cm[0].slice(0, 80) });
      }
    }
  }
  return issues;
}

// Catches the most common shape that slips past the template-aware
// guard: ``code-style emphasis`` inside a /* */ block comment. Once
// one of these lands inside an html`/css` template, the walker can't
// even reach the back end of the template, so it never inspects the
// bad comment. We only flag block comments (// JS line-comments are
// safe because they terminate at newline before the template parser
// sees them).
function scanForDoubleBacktickInBlockComments(path) {
  const src = readFileSync(path, "utf8");
  const issues = [];
  const re = /\/\*[\s\S]*?\*\//g;
  let m;
  while ((m = re.exec(src)) !== null) {
    if (m[0].includes("``")) {
      const lineNum = src.slice(0, m.index).split("\n").length;
      issues.push({ path, line: lineNum, snippet: m[0].slice(0, 80) });
    }
  }
  return issues;
}

function preBuildGuard() {
  const issues = [];
  for (const file of walkJs("src")) {
    issues.push(...scanForUnescapedBackticksInHtmlTemplates(file));
    issues.push(...scanForDoubleBacktickInBlockComments(file));
  }
  if (issues.length === 0) return;
  console.error(
    "\nBuild aborted: backtick(s) found inside html`...` template " +
      "comments. These close the lit template early and produce a " +
      "broken bundle that crashes in the browser as " +
      '"x(...) is not a function". Remove the backticks (or escape ' +
      "with \\` if literal text is required):\n",
  );
  for (const i of issues) {
    console.error(`  ${i.path}:${i.line}  ${i.snippet}`);
  }
  process.exit(1);
}

preBuildGuard();

async function build() {
  await esbuild.build({
    entryPoints: ["src/panel.js"],
    bundle: true,
    format: "esm",
    outfile: "panel.js",
    define,
  });
  require("./postbuild.js");

  // Format built output
  execSync("npx prettier --write panel.js", { stdio: "inherit" });
}

build();
