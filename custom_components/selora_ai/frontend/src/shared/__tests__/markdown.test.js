import { describe, it, expect } from "vitest";
import { stripAutomationBlock, renderMarkdown } from "../markdown.js";

// ---------------------------------------------------------------------------
// stripAutomationBlock
// ---------------------------------------------------------------------------
describe("stripAutomationBlock", () => {
  it("returns empty result for null/empty input", () => {
    expect(stripAutomationBlock(null)).toEqual({
      text: "",
      hasAutomationBlock: false,
      isPartialBlock: false,
    });
    expect(stripAutomationBlock("")).toEqual({
      text: "",
      hasAutomationBlock: false,
      isPartialBlock: false,
    });
  });

  it("detects complete automation block", () => {
    const input =
      "Here is an automation:\n```automation\nalias: Test\n```\nDone.";
    const result = stripAutomationBlock(input);
    expect(result.hasAutomationBlock).toBe(true);
    expect(result.isPartialBlock).toBe(false);
    expect(result.text).not.toContain("```automation");
    expect(result.text).toContain("Done.");
  });

  it("detects partial automation block (still streaming)", () => {
    const input = "Creating automation:\n```automation\nalias: Test\ntrigger:";
    const result = stripAutomationBlock(input);
    expect(result.hasAutomationBlock).toBe(false);
    expect(result.isPartialBlock).toBe(true);
    expect(result.text).not.toContain("```automation");
  });

  it("returns text as-is when no automation block", () => {
    const input = "Hello, this is a normal message.";
    const result = stripAutomationBlock(input);
    expect(result.text).toBe("Hello, this is a normal message.");
    expect(result.hasAutomationBlock).toBe(false);
    expect(result.isPartialBlock).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// renderMarkdown
// ---------------------------------------------------------------------------
describe("renderMarkdown", () => {
  it("returns empty string for falsy input", () => {
    expect(renderMarkdown(null)).toBe("");
    expect(renderMarkdown("")).toBe("");
  });

  it("escapes HTML entities", () => {
    const result = renderMarkdown("a < b & c > d");
    expect(result).toContain("&lt;");
    expect(result).toContain("&amp;");
    expect(result).toContain("&gt;");
  });

  it("renders bold text", () => {
    const result = renderMarkdown("This is **bold** text.");
    expect(result).toContain(">bold</strong>");
  });

  it("renders italic text", () => {
    const result = renderMarkdown("This is *italic* text.");
    expect(result).toContain("<em>italic</em>");
  });

  it("renders code blocks", () => {
    const result = renderMarkdown("```\nsome code\n```");
    expect(result).toContain("<pre");
    expect(result).toContain("some code");
  });

  it("renders inline code", () => {
    const result = renderMarkdown("Use `entity_id` here.");
    expect(result).toContain("<code");
    expect(result).toContain("entity_id");
  });

  it("renders headings", () => {
    expect(renderMarkdown("# Title")).toContain("Title");
    expect(renderMarkdown("# Title")).toContain("font-weight:700");
    expect(renderMarkdown("## Subtitle")).toContain("Subtitle");
    expect(renderMarkdown("### Section")).toContain("Section");
    expect(renderMarkdown("#### Subsection")).toContain("Subsection");
  });

  it("renders bullet lists", () => {
    const result = renderMarkdown("- Item one\n- Item two");
    expect(result).toContain("Item one");
    expect(result).toContain("Item two");
    expect(result).toContain("border-left");
  });

  it("renders numbered lists", () => {
    const result = renderMarkdown("1. First\n2. Second");
    expect(result).toContain("First");
    expect(result).toContain("Second");
  });

  it("converts newlines to <br>", () => {
    const result = renderMarkdown("Line 1\nLine 2");
    expect(result).toContain("<br>");
  });
});
