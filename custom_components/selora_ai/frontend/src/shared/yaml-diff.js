// ---------------------------------------------------------------------------
// Line-level diff between two YAML documents
// ---------------------------------------------------------------------------
// Pure functions — no host, no DOM. Used by the chat proposal card to show what
// accepting a refinement will write over what.
//
// A line diff is honest here because callers hand it two documents produced by
// the same PyYAML dump with sorted keys: a key never moves between them, so a
// changed value shows up as one -/+ pair rather than a reshuffled block.
// ---------------------------------------------------------------------------

// Above this, the O(n*m) LCS table stops being worth its memory. An automation
// is a few dozen lines; anything near this ceiling is not a document a human is
// going to read as a diff either, so we report "too large" and the caller hides
// the affordance rather than freezing the panel.
const MAX_DIFF_LINES = 800;

// Unchanged lines kept either side of a change before the run collapses.
export const DEFAULT_CONTEXT_RADIUS = 2;

// Opens a literal or folded block scalar: `key: |`, `key: >-`, `- |2+`. The
// capture holds the chomping and indentation indicators, which YAML accepts in
// either order. Requires them to end the line, so a plain value that merely
// contains `>` (`description: temp > 19`) is not mistaken for one.
const BLOCK_SCALAR_HEADER = /(?:^|\s)[|>]([+-]?[0-9]*[+-]?)[ \t]*$/;

const indentOf = (line) => /^[ \t]*/.exec(line)[0].length;

// Trailing whitespace is dumper noise, not a change the user made — normalize
// it away so it can't show as a -/+ pair with visually identical text.
//
// Except inside a block scalar, where trailing spaces are part of the value:
// normalizing there would let an edit to a `message: |` body change what gets
// written while the panel reports the lines as identical. Block content runs
// until a non-empty line returns to the header's indentation or less.
function splitLines(text) {
  const raw = String(text ?? "")
    .replace(/\r\n/g, "\n")
    .split("\n");

  const lines = [];
  let blockIndent = null;
  let blockKeepsTrailing = false;
  for (const line of raw) {
    if (blockIndent !== null) {
      if (line.trim() === "" || indentOf(line) > blockIndent) {
        lines.push(line);
        continue;
      }
      blockIndent = null;
      blockKeepsTrailing = false;
    }
    // The header's own trailing spaces sit after the indicator and mean
    // nothing, so it is normalized like any other line.
    lines.push(line.replace(/[ \t]+$/, ""));
    const header = BLOCK_SCALAR_HEADER.exec(line);
    if (header) {
      blockIndent = indentOf(line);
      blockKeepsTrailing = header[1].includes("+");
    }
  }

  // A trailing blank line is dumper noise too — unless the document ends
  // inside a `|+` block, the one form that keeps it as part of the value.
  if (!blockKeepsTrailing) {
    while (lines.length && lines[lines.length - 1] === "") lines.pop();
  }
  return lines;
}

// A mapping key, including the spellings YAML permits that a dumper never emits
// but a hand-written document can: matching quotes around the name
// (`"id": x`) and space before the colon (`id : x`).
//
// The dash group makes a sequence entry's own indentation count, so `- above:`
// reads as a child of the key above it rather than a sibling. The colon must
// be followed by space or end of line, as YAML requires in block context.
const KEY_RE =
  /^([ \t]*)((?:-[ \t]+)*)(['"]?)([A-Za-z_][A-Za-z0-9_.-]*)\3[ \t]*:(?:[ \t]|$)/;

// Where a line's own token starts, and the key it declares (null if it
// declares none).
function lineShape(text) {
  const m = KEY_RE.exec(text);
  if (m) return { key: m[4], start: m[1].length + m[2].length };
  return { key: null, start: indentOf(text) };
}

// Mapping key of a line at column 0, or null for anything nested / not a key.
function topLevelKey(line) {
  const shape = lineShape(line);
  return shape.start === 0 ? shape.key : null;
}

// Longest-common-subsequence table, filled from the tail so the forward walk
// in diffLines can emit removals before additions at every branch point.
function lcsTable(a, b) {
  const n = a.length;
  const m = b.length;
  const width = m + 1;
  const dp = new Uint32Array((n + 1) * width);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i * width + j] =
        a[i] === b[j]
          ? dp[(i + 1) * width + (j + 1)] + 1
          : Math.max(dp[(i + 1) * width + j], dp[i * width + (j + 1)]);
    }
  }
  return dp;
}

/**
 * Diff two YAML documents line by line.
 *
 * @param {string} before
 * @param {string} after
 * @returns {{lines: Array<{type: "ctx"|"add"|"del", text: string}>,
 *            added: number, removed: number} | null}
 *   null when either side is too large to diff (see MAX_DIFF_LINES).
 *
 * Deliberately reports no similarity score. Line overlap looks like a cheap way
 * to guess whether two documents are versions of the same automation, and it is
 * not — HA automations share too much boilerplate for any threshold to separate
 * them.
 */
export function diffLines(before, after) {
  const a = splitLines(before);
  const b = splitLines(after);
  if (a.length > MAX_DIFF_LINES || b.length > MAX_DIFF_LINES) return null;

  const width = b.length + 1;
  const dp = lcsTable(a, b);
  const lines = [];
  let added = 0;
  let removed = 0;

  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      lines.push({ type: "ctx", text: a[i] });
      i++;
      j++;
    } else if (dp[(i + 1) * width + j] >= dp[i * width + (j + 1)]) {
      lines.push({ type: "del", text: a[i] });
      removed++;
      i++;
    } else {
      lines.push({ type: "add", text: b[j] });
      added++;
      j++;
    }
  }
  for (; i < a.length; i++) {
    lines.push({ type: "del", text: a[i] });
    removed++;
  }
  for (; j < b.length; j++) {
    lines.push({ type: "add", text: b[j] });
    added++;
  }

  return { lines, added, removed };
}

/**
 * Keys enclosing the line at `index`, outermost first.
 *
 * The answer to "which part of the automation is this change in?" — a diff
 * showing `above: 18 → 19` is meaningless until you know it sits under
 * `actions › choose › conditions`.
 *
 * @param {Array<{text: string}>} lines
 * @param {number} index
 * @returns {string[]}
 */
export function yamlContextPath(lines, index) {
  if (index < 0 || index >= lines.length) return [];
  const path = [];
  let depth = lineShape(lines[index].text).start;
  for (let i = index - 1; i >= 0 && depth > 0; i--) {
    const shape = lineShape(lines[i].text);
    if (shape.key === null || shape.start >= depth) continue;
    path.push(shape.key);
    depth = shape.start;
  }
  return path.reverse();
}

/**
 * Collapse long runs of unchanged lines into gap markers.
 *
 * A refinement usually touches one value in a forty-line document; rendering
 * every untouched line buries the two that moved.
 *
 * Each gap carries the `start` index of the first line it hides — the handle
 * the caller passes back in `expanded` to reveal that run — and the key path
 * of the line right after it, so a collapsed diff still says where the next
 * change lives.
 *
 * @param {Array<{type: string, text: string}>} lines from diffLines
 * @param {number} radius unchanged lines kept either side of a change
 * @param {Set<number>|number[]} expanded gap start indices to reveal in full
 * @returns {Array<{type: "ctx"|"add"|"del"|"gap", text?: string,
 *                  count?: number, start?: number, path?: string[]}>}
 */
export function collapseDiff(
  lines,
  radius = DEFAULT_CONTEXT_RADIUS,
  expanded = [],
) {
  const revealed = expanded instanceof Set ? expanded : new Set(expanded);
  const keep = new Array(lines.length).fill(false);
  lines.forEach((line, idx) => {
    if (line.type === "ctx") return;
    for (
      let k = Math.max(0, idx - radius);
      k <= Math.min(lines.length - 1, idx + radius);
      k++
    ) {
      keep[k] = true;
    }
  });

  const out = [];
  let hidden = 0;
  const flush = (end) => {
    if (hidden === 0) return;
    const start = end - hidden;
    // A gap of one line costs as much space as the line it hides and tells the
    // reader less, so only collapse runs worth collapsing.
    if (hidden === 1 || revealed.has(start)) {
      for (let k = start; k < end; k++) out.push(lines[k]);
    } else {
      out.push({
        type: "gap",
        count: hidden,
        start,
        path: yamlContextPath(lines, end),
      });
    }
    hidden = 0;
  };

  lines.forEach((line, idx) => {
    if (keep[idx]) {
      flush(idx);
      out.push(line);
      return;
    }
    hidden++;
  });
  flush(lines.length);
  return out;
}
