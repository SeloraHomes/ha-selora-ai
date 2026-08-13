import { describe, it, expect } from "vitest";
import { diffLines, collapseDiff, yamlContextPath } from "../yaml-diff.js";

const types = (d) => d.lines.map((l) => l.type).join("");
const rendered = (d) =>
  d.lines.map(
    (l) => (l.type === "add" ? "+" : l.type === "del" ? "-" : " ") + l.text,
  );

describe("diffLines", () => {
  it("reports no changes for identical documents", () => {
    const yaml = "alias: Test\ntrigger:\n  - platform: state\n";
    const d = diffLines(yaml, yaml);
    expect(d.added).toBe(0);
    expect(d.removed).toBe(0);
    expect(types(d)).toBe("ctxctxctx");
  });

  it("pairs a changed value as one removal and one addition", () => {
    const before = "alias: Shades\nabove: 18\nbelow: 6\n";
    const after = "alias: Shades\nabove: 19\nbelow: 6\n";
    const d = diffLines(before, after);
    expect(d.added).toBe(1);
    expect(d.removed).toBe(1);
    expect(rendered(d)).toEqual([
      " alias: Shades",
      "-above: 18",
      "+above: 19",
      " below: 6",
    ]);
  });

  it("emits the removal before the addition at a branch point", () => {
    const d = diffLines("a\nb\n", "a\nc\n");
    expect(types(d)).toBe("ctxdeladd");
  });

  it("counts pure insertions and deletions", () => {
    const grown = diffLines("a\nb\n", "a\nx\ny\nb\n");
    expect(grown.added).toBe(2);
    expect(grown.removed).toBe(0);

    const shrunk = diffLines("a\nx\ny\nb\n", "a\nb\n");
    expect(shrunk.added).toBe(0);
    expect(shrunk.removed).toBe(2);
  });

  it("ignores trailing whitespace and a trailing newline", () => {
    const d = diffLines("alias: Test  \nabove: 19", "alias: Test\nabove: 19\n");
    expect(d.added).toBe(0);
    expect(d.removed).toBe(0);
  });

  describe("block scalars", () => {
    // Trailing spaces inside a `|` or `>` body are part of the value, so an
    // edit to them changes what gets written and has to show up.
    const withTrailing = "action:\n  message: |\n    Hello   \n    World\n";
    const withoutTrailing = "action:\n  message: |\n    Hello\n    World\n";

    it("reports a trailing-space edit inside a literal block", () => {
      const d = diffLines(withTrailing, withoutTrailing);
      expect(d.added).toBe(1);
      expect(d.removed).toBe(1);
      expect(rendered(d)).toEqual([
        " action:",
        "   message: |",
        "-    Hello   ",
        "+    Hello",
        "     World",
      ]);
    });

    it("reports it inside a folded block with a chomping indicator", () => {
      const d = diffLines(
        "template: >-\n  a  \n  b\n",
        "template: >-\n  a\n  b\n",
      );
      expect(d.added + d.removed).toBe(2);
    });

    it("resumes normalizing once the block ends", () => {
      const before = "message: |\n  body\nmode: single   \n";
      const after = "message: |\n  body\nmode: single\n";
      expect(diffLines(before, after).added).toBe(0);
    });

    it("keeps a trailing blank line when the document ends inside a block", () => {
      const d = diffLines("message: |+\n  body\n\n", "message: |+\n  body\n");
      expect(d.removed).toBe(1);
    });

    it("does not read a plain value containing > as a block header", () => {
      const before = "description: temp > 19\nmode: single   \n";
      const after = "description: temp > 19\nmode: single\n";
      expect(
        diffLines(before, after).added + diffLines(before, after).removed,
      ).toBe(0);
    });

    it("does not treat a deeper sibling key as block content", () => {
      // `mode` returns to column 0, so it is outside the block and normalized.
      const before = "a:\n  message: |\n    body\nmode: single  \n";
      const after = "a:\n  message: |\n    body\nmode: single\n";
      expect(diffLines(before, after).added).toBe(0);
    });
  });

  it("normalizes CRLF line endings", () => {
    const d = diffLines("a\r\nb\r\n", "a\nb\n");
    expect(d.added + d.removed).toBe(0);
  });

  it("handles an empty side", () => {
    const d = diffLines("", "alias: New\n");
    expect(d.added).toBe(1);
    expect(d.removed).toBe(0);
  });

  it("treats two empty documents as identical", () => {
    const d = diffLines("", "");
    expect(d.lines).toEqual([]);
    expect(d.added + d.removed).toBe(0);
  });

  it("returns null when a document is too large to diff", () => {
    const huge = "x\n".repeat(801);
    expect(diffLines(huge, "x\n")).toBeNull();
    expect(diffLines("x\n", huge)).toBeNull();
  });
});

describe("collapseDiff", () => {
  const doc = (n) => Array.from({ length: n }, (_, i) => `line${i}`).join("\n");

  it("keeps context around a change and collapses the rest", () => {
    const before = doc(20);
    const after = before.replace("line10", "line10-changed");
    const entries = collapseDiff(diffLines(before, after).lines, 2);

    const gaps = entries.filter((e) => e.type === "gap");
    expect(gaps).toHaveLength(2);
    expect(gaps[0].count).toBe(8); // line0..line7
    expect(gaps[1].count).toBe(7); // line13..line19
    expect(entries.filter((e) => e.type === "add")).toHaveLength(1);
    expect(entries.filter((e) => e.type === "del")).toHaveLength(1);
  });

  it("keeps a single hidden line rather than collapsing it", () => {
    // Changes at both ends leave exactly one unchanged line in the middle,
    // which costs no less to hide than to show.
    const before = "a\nb\nc\nd\ne\nf\ng\n";
    const after = "A\nb\nc\nd\ne\nf\nG\n";
    const entries = collapseDiff(diffLines(before, after).lines, 2);
    expect(entries.some((e) => e.type === "gap")).toBe(false);
    expect(entries.map((e) => e.text).filter(Boolean)).toContain("d");
  });

  it("leaves a short diff untouched", () => {
    const entries = collapseDiff(diffLines("a\nb\n", "a\nc\n").lines, 2);
    expect(entries.every((e) => e.type !== "gap")).toBe(true);
    expect(entries).toHaveLength(3);
  });

  it("returns nothing but context for an unchanged document", () => {
    const entries = collapseDiff(diffLines(doc(30), doc(30)).lines, 2);
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({ type: "gap", count: 30, start: 0 });
  });

  it("reports each gap's start index and the key path below it", () => {
    const yaml = [
      "actions:",
      "- choose:",
      "  - conditions:",
      "    - condition: numeric_state",
      "      entity_id: sensor.uv",
      "      above: 6",
      "      unrelated: 1",
    ].join("\n");
    const entries = collapseDiff(
      diffLines(yaml, yaml.replace("above: 6", "above: 7")).lines,
      1,
    );
    const gap = entries.find((e) => e.type === "gap");
    expect(gap.start).toBe(0);
    expect(gap.count).toBe(4);
    expect(gap.path).toEqual(["actions", "choose", "conditions"]);
  });

  it("reveals only the gap whose start index is expanded", () => {
    const before = doc(40);
    const after = before
      .replace("line10", "line10-changed")
      .replace("line30", "line30-changed");
    const lines = diffLines(before, after).lines;
    const collapsed = collapseDiff(lines, 2);
    const gaps = collapsed.filter((e) => e.type === "gap");
    expect(gaps).toHaveLength(3);

    const opened = collapseDiff(lines, 2, [gaps[0].start]);
    expect(opened.filter((e) => e.type === "gap")).toHaveLength(2);
    expect(opened.filter((e) => e.type === "ctx")).toHaveLength(
      collapsed.filter((e) => e.type === "ctx").length + gaps[0].count,
    );

    const all = collapseDiff(
      lines,
      2,
      gaps.map((g) => g.start),
    );
    expect(all.some((e) => e.type === "gap")).toBe(false);
    expect(all).toHaveLength(lines.length);
  });

  it("accepts the expanded set as a Set", () => {
    const before = doc(20);
    const after = before.replace("line10", "line10-changed");
    const lines = diffLines(before, after).lines;
    const start = collapseDiff(lines, 2).find((e) => e.type === "gap").start;
    const opened = collapseDiff(lines, 2, new Set([start]));
    expect(opened.filter((e) => e.type === "gap")).toHaveLength(1);
  });
});

describe("yamlContextPath", () => {
  const asLines = (yaml) => yaml.split("\n").map((text) => ({ text }));

  it("walks up nested mappings and sequences", () => {
    const lines = asLines(
      [
        "actions:",
        "- choose:",
        "  - conditions:",
        "    - condition: numeric_state",
        "      above: 19",
      ].join("\n"),
    );
    expect(yamlContextPath(lines, 4)).toEqual([
      "actions",
      "choose",
      "conditions",
    ]);
  });

  it("returns nothing for a top-level key", () => {
    const lines = asLines("alias: Test\nmode: single");
    expect(yamlContextPath(lines, 1)).toEqual([]);
  });

  it("treats a sequence dash as indentation", () => {
    // `at` is a sibling of `trigger` inside the same list item, not its child:
    // the dash counts as indentation, so `- trigger:` does not enclose it.
    const lines = asLines("triggers:\n- trigger: time\n  at: '07:00:00'");
    expect(yamlContextPath(lines, 2)).toEqual(["triggers"]);
    // A key nested one level deeper than the dash IS enclosed by it.
    const nested = asLines("actions:\n- choose:\n  - alias: first");
    expect(yamlContextPath(nested, 2)).toEqual(["actions", "choose"]);
  });

  it("skips continuation lines that are not keys", () => {
    const lines = asLines(
      "description: one\n  wrapped continuation\nmode: single",
    );
    expect(yamlContextPath(lines, 1)).toEqual(["description"]);
  });

  it("returns nothing for an out-of-range index", () => {
    const lines = asLines("alias: Test");
    expect(yamlContextPath(lines, 5)).toEqual([]);
    expect(yamlContextPath(lines, -1)).toEqual([]);
  });
});
