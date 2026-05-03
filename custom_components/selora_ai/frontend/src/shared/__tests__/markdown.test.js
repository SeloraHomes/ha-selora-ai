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
      partialBlockType: null,
    });
    expect(stripAutomationBlock("")).toEqual({
      text: "",
      hasAutomationBlock: false,
      isPartialBlock: false,
      partialBlockType: null,
    });
  });

  it("detects complete automation block", () => {
    const input =
      "Here is an automation:\n```automation\nalias: Test\n```\nDone.";
    const result = stripAutomationBlock(input);
    expect(result.hasAutomationBlock).toBe(true);
    expect(result.isPartialBlock).toBe(false);
    expect(result.partialBlockType).toBe(null);
    expect(result.text).not.toContain("```automation");
    expect(result.text).toContain("Done.");
  });

  it("detects partial automation block (still streaming)", () => {
    const input = "Creating automation:\n```automation\nalias: Test\ntrigger:";
    const result = stripAutomationBlock(input);
    expect(result.hasAutomationBlock).toBe(false);
    expect(result.isPartialBlock).toBe(true);
    expect(result.partialBlockType).toBe("automation");
    expect(result.text).not.toContain("```automation");
  });

  it("returns text as-is when no automation block", () => {
    const input = "Hello, this is a normal message.";
    const result = stripAutomationBlock(input);
    expect(result.text).toBe("Hello, this is a normal message.");
    expect(result.hasAutomationBlock).toBe(false);
    expect(result.isPartialBlock).toBe(false);
    expect(result.partialBlockType).toBe(null);
  });

  it("detects complete scene block", () => {
    const input =
      'Here is a scene:\n```scene\n{"name":"Cozy","entities":{}}\n```\nDone.';
    const result = stripAutomationBlock(input);
    expect(result.hasAutomationBlock).toBe(true);
    expect(result.isPartialBlock).toBe(false);
    expect(result.partialBlockType).toBe(null);
    expect(result.text).not.toContain("```scene");
    expect(result.text).toContain("Done.");
  });

  it("detects partial scene block (still streaming)", () => {
    const input = 'Creating scene:\n```scene\n{"name":"Cozy","entities":{';
    const result = stripAutomationBlock(input);
    expect(result.hasAutomationBlock).toBe(false);
    expect(result.isPartialBlock).toBe(true);
    expect(result.partialBlockType).toBe("scene");
    expect(result.text).not.toContain("```scene");
    expect(result.text).toBe("Creating scene:");
  });

  it("strips both automation and scene complete blocks", () => {
    const input =
      'Auto:\n```automation\nalias: Test\n```\nScene:\n```scene\n{"name":"Test"}\n```\nEnd.';
    const result = stripAutomationBlock(input);
    expect(result.hasAutomationBlock).toBe(true);
    expect(result.text).toContain("End.");
    expect(result.text).not.toContain("```automation");
    expect(result.text).not.toContain("```scene");
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

  it("renders single-entity references as a one-card grid", () => {
    // Both [[entity:…|label]] and [[entities:…]] route to the same
    // grid placeholder — the chat layer hydrates them with HA tile
    // cards. The label from the legacy form is intentionally dropped
    // (the tile shows the registry friendly_name).
    const result = renderMarkdown(
      "The [[entity:light.kitchen|kitchen light]] is on.",
    );
    expect(result).toContain('class="selora-entity-grid"');
    expect(result).toContain('data-entity-ids="light.kitchen"');
    expect(result).not.toContain("[[entity:");
  });

  it("ignores malformed entity placeholders", () => {
    // Missing entity_id pattern — must look like <domain>.<object_id>.
    const result = renderMarkdown("[[entity:not-an-id|nope]]");
    expect(result).not.toContain("selora-entity-grid");
    expect(result).toContain("[[entity:not-an-id|nope]]");
  });

  it("renders entity-grid placeholders", () => {
    const result = renderMarkdown(
      "Lights on:\n[[entities:light.kitchen,light.office_rgbw]]",
    );
    expect(result).toContain('class="selora-entity-grid"');
    expect(result).toContain(
      'data-entity-ids="light.kitchen,light.office_rgbw"',
    );
    expect(result).not.toContain("[[entities:");
  });

  it("ignores malformed entity-grid placeholders", () => {
    // Empty list and bad ids must fall through unchanged.
    expect(renderMarkdown("[[entities:]]")).toContain("[[entities:]]");
    expect(renderMarkdown("[[entities:not-an-id]]")).toContain(
      "[[entities:not-an-id]]",
    );
  });

  it("salvages bullet lists of entity_ids into a tile grid", () => {
    // Mirror the shape the LLM tends to emit when it ignores the prompt:
    // bulleted entity_ids interspersed with "— on (brightness …)" lines.
    const input = [
      "Here are the lights that are currently on:",
      "",
      "- light.ceiling_lights",
      "- — on (brightness: 180)",
      "",
      "- light.kitchen_lights",
      "- — on (brightness: 180)",
      "",
      "- light.office_rgbw_lights",
      "- — on (brightness: 180)",
      "",
      "Let me know if you need anything else.",
    ].join("\n");
    const result = renderMarkdown(input);
    expect(result).toContain('class="selora-entity-grid"');
    expect(result).toContain(
      'data-entity-ids="light.ceiling_lights,light.kitchen_lights,light.office_rgbw_lights"',
    );
    // The raw entity_ids must NOT appear — they're inside the data attr only.
    expect(result).not.toContain(">light.ceiling_lights<");
    // Surrounding prose stays intact.
    expect(result).toContain("Here are the lights");
    expect(result).toContain("Let me know if you need anything else");
  });

  it("coalesces consecutive bare markers into one grid", () => {
    // The LLM sometimes emits one `[[entities:…]]` line per entity
    // instead of one combined block. Without coalescing, each becomes
    // its own one-tile grid, the area-grouping pass sees groups.size
    // === 1 per grid, and no headers appear. Combine the run.
    const input = [
      "Lights currently on:",
      "",
      "[[entities:light.kitchen]]",
      "[[entities:light.bedroom_main]]",
      "[[entities:light.exterior_back_porch]]",
      "",
      "All set.",
    ].join("\n");
    const result = renderMarkdown(input);
    const gridMatches = result.match(/class="selora-entity-grid"/g) || [];
    expect(gridMatches.length).toBe(1);
    expect(result).toContain(
      'data-entity-ids="light.kitchen,light.bedroom_main,light.exterior_back_porch"',
    );
  });

  it("coalesces consecutive bulleted markers into one grid", () => {
    // Without coalescing, each `- [[entity:…]]` bullet becomes its
    // own one-tile grid block with a <br> between, producing huge
    // vertical gaps. The combined `[[entities:…]]` block lets the
    // grid auto-flow tiles inline with normal spacing.
    const input = [
      "You have the following lights:",
      "",
      "- [[entity:light.ceiling_lights|Ceiling Lights]]",
      "- [[entity:light.kitchen_lights|Kitchen Lights]]",
      "- [[entity:light.office_rgbw_lights|Office RGBW Lights]]",
      "",
      "Let me know if you need anything.",
    ].join("\n");
    const result = renderMarkdown(input);
    // Exactly one grid (not three).
    const gridMatches = result.match(/class="selora-entity-grid"/g) || [];
    expect(gridMatches.length).toBe(1);
    expect(result).toContain(
      'data-entity-ids="light.ceiling_lights,light.kitchen_lights,light.office_rgbw_lights"',
    );
  });

  it("strips trailing state hints from same-line bulleted markers", () => {
    // Common LLM shape: bulleted marker with the state glued onto
    // the same line ("- [[entity:…]] — brightness: 255"). The
    // coalescing pass groups the consecutive bulleted markers into
    // a single combined grid (no per-tile vertical stacking) and
    // drops the trailing dash hint (the tile shows live state).
    const input = [
      "Lights currently on:",
      "",
      "- [[entity:light.living_room_floor_lamp|Living Room Floor Lamp]] — brightness: 255",
      "- [[entity:light.living_room_side_lamp|Living Room Side Lamp]] — brightness: 17",
      "",
      "All set.",
    ].join("\n");
    const result = renderMarkdown(input);
    // One combined grid, not two separate ones.
    const gridMatches = result.match(/class="selora-entity-grid"/g) || [];
    expect(gridMatches.length).toBe(1);
    expect(result).toContain(
      'data-entity-ids="light.living_room_floor_lamp,light.living_room_side_lamp"',
    );
    // Trailing hint dropped.
    expect(result).not.toContain("brightness: 255");
    expect(result).not.toContain("brightness: 17");
    expect(result).toContain("All set");
  });

  it("strips trailing state hints from bare same-line markers", () => {
    // Same shape but without the leading bullet — the marker stays
    // (gets inline-substituted into a tile grid), the dash hint goes.
    const input = "[[entity:light.kitchen|Kitchen]] — on (brightness: 200)";
    const result = renderMarkdown(input);
    expect(result).toContain('data-entity-ids="light.kitchen"');
    expect(result).not.toContain("brightness");
    expect(result).not.toContain("— on");
  });

  it("unwraps bulleted entity markers and drops their state line", () => {
    // The LLM follows the prompt and emits `[[entity:…]]`, but as a
    // bullet item — without the unwrap we'd render the tile inside a
    // bullet div (visible orange bar to its left) AND keep the
    // redundant "— on (…)" bullet underneath the tile.
    const input = [
      "However, the following light is on in the **Living Room**:",
      "",
      "- [[entity:light.living_room_rgbww_lights|Living Room Lights]]",
      "- — on (brightness: 180)",
      "",
      "If you need anything else, let me know!",
    ].join("\n");
    const result = renderMarkdown(input);
    // Tile placeholder rendered.
    expect(result).toContain('class="selora-entity-grid"');
    expect(result).toContain(
      'data-entity-ids="light.living_room_rgbww_lights"',
    );
    // No bullet wrapper around the tile (would produce border-left).
    expect(result).not.toMatch(
      /border-left:[^"]*"[^>]*>[\s\S]*selora-entity-grid/,
    );
    // State annotation gone — the tile shows it live.
    expect(result).not.toContain("brightness: 180");
    expect(result).not.toContain("— on");
  });

  it("absorbs state annotations after blank lines", () => {
    // Common LLM shape: each bullet is separated by a blank line, so
    // the "— on …" annotation lands two lines after the entity_id.
    // The tile card already shows the live state, so we drop the
    // annotation rather than render it next to the card.
    const input = [
      "The light is on:",
      "",
      "- light.living_room_rgbww_lights",
      "",
      "- — on (brightness: 180)",
      "",
      "Let me know if you need anything else.",
    ].join("\n");
    const result = renderMarkdown(input);
    expect(result).toContain('class="selora-entity-grid"');
    expect(result).toContain(
      'data-entity-ids="light.living_room_rgbww_lights"',
    );
    // The annotation must not survive — it's redundant with the tile.
    expect(result).not.toContain("brightness: 180");
    expect(result).not.toContain("— on");
    expect(result).toContain("Let me know if you need anything else");
  });

  it("salvages a single bulleted entity_id into a tile", () => {
    // Mirror the "list of one" shape from a state query for a single
    // room — the LLM emits the entity_id alone on a bullet line plus
    // a state hint. Even a run of one is unambiguously a list since
    // ID_LINE refuses to match a line that has any text past the id.
    const input = [
      "The following light is currently on in the Living Room:",
      "",
      "- light.living_room_rgbww_lights",
      "- — on (brightness: 180)",
      "",
      "If you need anything else, let me know!",
    ].join("\n");
    const result = renderMarkdown(input);
    expect(result).toContain('class="selora-entity-grid"');
    expect(result).toContain(
      'data-entity-ids="light.living_room_rgbww_lights"',
    );
    expect(result).toContain("Living Room");
    expect(result).toContain("If you need anything else");
  });

  it("leaves prose mentions of an entity_id alone", () => {
    // ID_LINE refuses to match a line with content past the id, so a
    // mid-paragraph mention can't get collapsed even when bulleted.
    const result = renderMarkdown("- set light.kitchen to 50% brightness");
    expect(result).not.toContain("selora-entity-grid");
    expect(result).toContain("light.kitchen");
  });

  it("hides unclosed entity markers mid-stream", () => {
    // Streaming chunk lands with the marker half-typed — must not
    // appear as raw `[[entity:light.kitch` text in the bubble.
    expect(renderMarkdown("Turning on:\n[[entity:light.kitch")).not.toContain(
      "[[entity:",
    );
    expect(
      renderMarkdown("Five lights are on:\n[[entities:light.kit"),
    ).not.toContain("[[entities:");
    // A complete marker after the partial one still renders as a grid.
    const complete = renderMarkdown(
      "Turning on:\n[[entity:light.kitchen|Kitchen Lights]]",
    );
    expect(complete).toContain('class="selora-entity-grid"');
    expect(complete).toContain('data-entity-ids="light.kitchen"');
  });
});
