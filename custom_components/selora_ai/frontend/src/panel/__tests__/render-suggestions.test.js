import { readFileSync, readdirSync } from "fs";

import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import { renderSuggestionsSection } from "../render-suggestions.js";

// collapsedSuggestionCount() reads window.innerWidth; these tests run in node.
beforeAll(() => {
  globalThis.window = { innerWidth: 1400 };
});

// A repeat() directive, unrolled into the templates it would render.
const unrepeat = (value) =>
  value?._$litDirective$ && Array.isArray(value.values?.[0])
    ? value.values[0].map((item, i) =>
        (value.values[2] ?? value.values[1])(item, i),
      )
    : value;

function ser(value) {
  value = unrepeat(value);
  if (value == null || typeof value === "boolean") return "";
  if (typeof value === "string" || typeof value === "number")
    return String(value);
  if (typeof value === "function") return "[fn]";
  if (Array.isArray(value)) return value.map(ser).join("");
  if (value.strings) {
    let out = "";
    for (let i = 0; i < value.strings.length; i++) {
      out += value.strings[i];
      if (i < value.values.length) out += ser(value.values[i]);
    }
    return out;
  }
  return "[obj]";
}

// Event listeners, paired with the markup chunk they sit in, so a test can say
// which element it is clicking without a DOM.
function listeners(value, out = []) {
  value = unrepeat(value);
  if (value == null || typeof value !== "object") return out;
  if (Array.isArray(value)) {
    for (const v of value) listeners(v, out);
    return out;
  }
  if (!value.strings) return out;
  for (let i = 0; i < value.values.length; i++) {
    const at = /@([a-z]+)=$/.exec(value.strings[i]);
    if (at && typeof value.values[i] === "function") {
      // The markup before the listener, not just the chunk it sits in: an
      // interpolated class attribute splits the element across chunks.
      const before = value.strings.slice(0, i + 1).join(" ");
      out.push({
        type: at[1],
        fn: value.values[i],
        markup: before.slice(-400),
      });
    }
    listeners(value.values[i], out);
  }
  return out;
}

const AUTOMATION = {
  alias: "Stair Motion Lights",
  description: "Illuminate the living room when motion is detected at night.",
  triggers: [{ trigger: "state", entity_id: "binary_sensor.stairs_motion" }],
  actions: [{ action: "light.turn_on" }],
};
const KEY = `sug_${AUTOMATION.alias}`;

function makeHost(over = {}) {
  return {
    _t: (_key, fallback) => fallback,
    _suggestions: [{ automation: AUTOMATION, automation_yaml: "alias: x\n" }],
    _proactiveSuggestions: [],
    _editedYaml: {},
    _savingYaml: {},
    _acceptingProactive: {},
    _dismissingProactive: {},
    _fadingOutSuggestions: {},
    _cardActiveTab: {},
    _cardLastTab: {},
    _cardPanelSettled: {},
    _selectedSuggestionKeys: {},
    _suggestionFilter: "",
    _suggestionSourceFilter: "all",
    _suggestionSortBy: "recent",
    _suggestionsVisibleCount: 3,
    _config: {},
    ...over,
  };
}

const render = (host) =>
  ser(renderSuggestionsSection(host)).replace(/\s+/g, " ");

describe("the suggestion card's disclosure", () => {
  it("puts the pointer on every region that toggles it", () => {
    // The pointer used to appear over the title and description while only the
    // chevron opened the card, so the card read as clickable and was inert.
    const out = render(makeHost());
    expect(out.match(/card-disclosure/g)).toHaveLength(3);
    // The chevron is inside the tab strip, which carries the handler now, so
    // it needs none of its own.
    expect(out).toMatch(/mdi:chevron-down"? class="card-chevron[^"]*"/);
  });

  it("opens the panel from the header, the description and the tab strip", () => {
    for (const region of ["card-header", "clamp-2", "card-tabs"]) {
      const host = makeHost();
      const hit = listeners(renderSuggestionsSection(host)).find(
        (l) => l.type === "click" && l.markup.includes(region),
      );
      expect(hit, region).toBeTruthy();
      hit.fn({ stopPropagation: () => {} });
      expect(host._cardActiveTab[KEY], region).toBe("flow");
    }
  });

  it("keeps a tab button from also toggling the card shut", () => {
    // The tab strip toggles, so a click on Flow or YAML inside it would set
    // the tab and then immediately close the card it just opened.
    const picked = [];
    for (const tab of ["flow", "yaml"]) {
      const host = makeHost();
      const buttons = listeners(renderSuggestionsSection(host)).filter(
        (l) =>
          l.type === "click" && /class="card-tab .*@click=$/s.test(l.markup),
      );
      expect(buttons).toHaveLength(2);
      const event = { stopPropagation: vi.fn() };
      buttons[tab === "flow" ? 0 : 1].fn(event);
      expect(event.stopPropagation).toHaveBeenCalled();
      picked.push(host._cardActiveTab[KEY]);
    }
    expect(new Set(picked)).toEqual(new Set(["flow", "yaml"]));
  });

  it("does not toggle when the bulk-select checkbox is clicked", () => {
    const host = makeHost({ _suggestionBulkMode: true });
    const box = listeners(renderSuggestionsSection(host)).find(
      (l) => l.type === "click" && l.markup.includes("card-select"),
    );
    const event = { stopPropagation: vi.fn() };
    box.fn(event);
    expect(event.stopPropagation).toHaveBeenCalled();
    expect(host._cardActiveTab[KEY]).toBeUndefined();
  });
});

describe("the grow/shrink animation", () => {
  it("marks the panel open only while a tab is active", () => {
    expect(render(makeHost())).not.toMatch(/class="card-panel open/);
    const open = render(
      makeHost({
        _cardActiveTab: { [KEY]: "yaml" },
        _cardLastTab: { [KEY]: "yaml" },
      }),
    );
    expect(open).toMatch(/class="card-panel open/);
    expect(open).toContain("ha-code-editor");
  });

  it("keeps the closed panel's content mounted so the shrink has something to shrink", () => {
    // Unmounting on close collapses the height in one frame and the transition
    // never runs; the content goes once the collapse has finished.
    const closing = render(
      makeHost({
        _cardActiveTab: { [KEY]: null },
        _cardLastTab: { [KEY]: "yaml" },
      }),
    );
    expect(closing).toContain("ha-code-editor");
    expect(closing).not.toMatch(/class="card-panel open/);
  });

  it("opens the panel in a detail row after the card, not inside it", () => {
    // A card that spans the row itself drops below its neighbours and leaves
    // them stranded in half-empty rows; the detail row under the card's row
    // leaves the clicked card where it was.
    const open = render(
      makeHost({
        _cardActiveTab: { [KEY]: "yaml" },
        _cardLastTab: { [KEY]: "yaml" },
      }),
    );
    expect(open).toContain("suggestions-grid");
    expect(open).toContain("card-open");
    expect(open.indexOf("Accept")).toBeLessThan(open.indexOf("card-detail"));
    expect(open).not.toContain("card-expanded");
  });

  it("places the detail after the last card of its row, in focus order", () => {
    // Right after its own card, dense flow draws the row's later cards above
    // the detail while Tab reaches them only after the detail's editor.
    const alias = (n) => ({ ...AUTOMATION, alias: `Card ${n}` });
    const host = makeHost({
      _suggestions: [1, 2, 3, 4].map((n) => ({
        automation: alias(n),
        automation_yaml: "alias: x\n",
      })),
      _suggestionsVisibleCount: 4,
      _suggestionGridCols: 3,
      _cardActiveTab: { "sug_Card 2": "yaml" },
      _cardLastTab: { "sug_Card 2": "yaml" },
    });
    const out = render(host);
    const at = (needle) => out.indexOf(needle);
    expect(at("Card 3")).toBeLessThan(at("card-detail"));
    expect(at("card-detail")).toBeLessThan(at("Card 4"));
  });

  it("keys every card and detail so an open editor stays with its card", () => {
    const host = makeHost({
      _cardActiveTab: { [KEY]: "yaml" },
      _cardLastTab: { [KEY]: "yaml" },
    });
    const section = renderSuggestionsSection(host);
    const find = (value) => {
      if (value?._$litDirective$ && Array.isArray(value.values?.[0]))
        return value;
      if (Array.isArray(value)) return value.map(find).find(Boolean);
      return value?.values ? find(value.values) : null;
    };
    const parts = find(section).values[0];
    expect(parts.map((part) => find(section).values[1](part))).toEqual([
      `card:${KEY}`,
      `detail:${KEY}`,
    ]);
  });

  it("keeps the detail row until the collapse has finished", () => {
    const mid = makeHost({
      _cardActiveTab: { [KEY]: null },
      _cardLastTab: { [KEY]: "yaml" },
      _cardPanelSettled: { [KEY]: false },
    });
    expect(render(mid)).toContain("card-detail");
    expect(render(mid)).not.toContain("card-open");
    expect(render(makeHost())).not.toContain("card-detail");
  });

  it("clips the panel only while it moves", () => {
    // Left clipping, the code editor's entity autocomplete is cut off where the
    // list falls past the panel's bottom edge.
    const moving = render(makeHost({ _cardActiveTab: { [KEY]: "yaml" } }));
    expect(moving).not.toContain("settled");
    const settled = render(
      makeHost({
        _cardActiveTab: { [KEY]: "yaml" },
        _cardPanelSettled: { [KEY]: true },
      }),
    );
    expect(settled).toMatch(/class="card-panel open settled/);
  });
});

describe("the settle timer", () => {
  // Both of these are sequences over time, which is exactly what the
  // render-only tests above cannot see.
  const clickHeader = (host) => {
    const hit = listeners(renderSuggestionsSection(host)).find(
      (l) => l.type === "click" && l.markup.includes("card-header"),
    );
    hit.fn({ stopPropagation: () => {} });
  };

  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("drops the panel's content once the collapse has finished", () => {
    // Left mounted, every card the user ever opened keeps a CodeMirror
    // instance alive inside a zero-height panel.
    const host = makeHost();
    clickHeader(host);
    vi.advanceTimersByTime(1000);
    expect(host._cardLastTab[KEY]).toBe("flow");

    clickHeader(host);
    expect(host._cardActiveTab[KEY]).toBeNull();
    // Still mounted while the shrink runs.
    expect(host._cardLastTab[KEY]).toBe("flow");
    vi.advanceTimersByTime(1000);
    expect(host._cardLastTab[KEY]).toBeNull();
    expect(render(host)).not.toContain("flow-chart");
  });

  it("keeps the content of a card reopened before the timer fires", () => {
    const host = makeHost();
    clickHeader(host);
    vi.advanceTimersByTime(1000);
    clickHeader(host);
    clickHeader(host);
    vi.advanceTimersByTime(1000);
    expect(host._cardActiveTab[KEY]).toBe("flow");
    expect(host._cardLastTab[KEY]).toBe("flow");
  });

  it("does not let a superseded timer settle the next transition", () => {
    // Toggling again mid-animation left the first timer running, so it
    // settled the second transition early and unmounted the detail row mid-shrink.
    const host = makeHost();
    clickHeader(host);
    vi.advanceTimersByTime(100);
    clickHeader(host);
    // Past when the FIRST timer would have fired, still inside the second.
    vi.advanceTimersByTime(250);
    expect(host._cardPanelSettled[KEY]).toBe(false);
    vi.advanceTimersByTime(200);
    expect(host._cardPanelSettled[KEY]).toBe(true);
  });
  it("collapses the open card when another one opens", () => {
    // Every open card adds a detail row, so leaving the first open stacked
    // unrelated editors down the page.
    const other = { ...AUTOMATION, alias: "Other" };
    const host = makeHost({
      _suggestions: [AUTOMATION, other].map((automation) => ({
        automation,
        automation_yaml: "alias: x\n",
      })),
      _cardActiveTab: { [KEY]: "yaml", "automation.kept": "flow" },
      _cardLastTab: { [KEY]: "yaml" },
      _cardPanelSettled: { [KEY]: true },
    });
    const headers = listeners(renderSuggestionsSection(host)).filter(
      (l) => l.type === "click" && l.markup.includes("card-header"),
    );
    headers.at(-1).fn({ stopPropagation: () => {} });
    expect(host._cardActiveTab["sug_Other"]).toBe("flow");
    expect(host._cardActiveTab[KEY]).toBeNull();
    // An Automations-tab card shares the map and stays as it was.
    expect(host._cardActiveTab["automation.kept"]).toBe("flow");
    // The closed card shrinks like any other collapse, then unmounts.
    expect(host._cardLastTab[KEY]).toBe("yaml");
    vi.advanceTimersByTime(1000);
    expect(host._cardLastTab[KEY]).toBeNull();
  });
});

describe("the card's classes exist", () => {
  const STYLES = (function collect(dir, out = []) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = new URL(
        `${entry.name}${entry.isDirectory() ? "/" : ""}`,
        dir,
      );
      if (entry.isDirectory()) collect(path, out);
      else if (entry.name.endsWith(".css.js"))
        out.push(readFileSync(path, "utf8"));
    }
    return out;
  })(new URL("../../", import.meta.url)).join("\n");

  it("names no class that no stylesheet defines", () => {
    // Same guard as the confirmation card's: valid markup naming a class
    // nothing styles renders as a bare div and no behavioural test can see it.
    const source = readFileSync(
      new URL("../render-suggestions.js", import.meta.url),
      "utf8",
    );
    const used = new Set();
    for (const [, attr] of source.matchAll(/class="([^"$]+)"/g)) {
      for (const name of attr.split(/\s+/).filter(Boolean)) used.add(name);
    }
    for (const name of [
      "card-panel",
      "card-panel-inner",
      "card-open",
      "card-detail",
      "card-disclosure",
    ]) {
      used.add(name);
    }
    const missing = [...used].filter(
      (n) => !new RegExp(`\\.${n}\\b`).test(STYLES),
    );
    expect(missing).toEqual([]);
  });

  it("animates over the same duration the stylesheet uses", () => {
    // The settle timer is what unmounts the detail row and stops the
    // clipping; a stylesheet that outlasts it snaps mid-animation.
    const source = readFileSync(
      new URL("../render-suggestions.js", import.meta.url),
      "utf8",
    );
    const js = Number(/PANEL_ANIM_MS = (\d+)/.exec(source)[1]);
    const css = Number(/grid-template-rows (\d+)ms/.exec(STYLES)[1]);
    expect(css).toBe(js);
  });
});
