import { describe, it, expect } from "vitest";
import { filterSessions, extractSnippet } from "../session-search.js";

// filterSessions powers the conversation sidebar search: it fuzzy-matches the
// query against each session's title and its `search_text` blob (concatenated
// message contents), ranks title hits above body-only hits, and attaches a
// snippet when the match lives in the body so the sidebar can show WHERE the
// conversation matched under its title.

const sessions = [
  {
    id: "a",
    title: "Living room lights",
    updated_at: "2026-07-10T10:00:00",
    search_text: "turn on the living room lights every evening at sunset",
  },
  {
    id: "b",
    title: "Kitchen automation",
    updated_at: "2026-07-11T10:00:00",
    search_text:
      "the coffee machine should start when the bedroom motion fires",
  },
  {
    id: "c",
    title: "Garage door",
    updated_at: "2026-07-12T10:00:00",
    search_text: "close the door automatically at midnight",
  },
];

describe("filterSessions", () => {
  it("returns every session untouched when the query is blank", () => {
    const out = filterSessions(sessions, "");
    expect(out).toHaveLength(3);
    expect(out.every((r) => r.snippet === null)).toBe(true);
    expect(out.map((r) => r.session.id)).toEqual(["a", "b", "c"]);
  });

  it("matches on the title and does not attach a body snippet", () => {
    const out = filterSessions(sessions, "kitchen");
    expect(out).toHaveLength(1);
    expect(out[0].session.id).toBe("b");
    expect(out[0].snippet).toBeNull();
  });

  it("suppresses the body snippet when the query also matches the title", () => {
    // "lights" is a title word of session a AND appears in its body; the
    // title is already on screen, so no redundant body snippet.
    const out = filterSessions(sessions, "lights");
    expect(out).toHaveLength(1);
    expect(out[0].session.id).toBe("a");
    expect(out[0].snippet).toBeNull();
  });

  it("matches on the body and attaches a snippet around the hit", () => {
    const out = filterSessions(sessions, "coffee");
    expect(out).toHaveLength(1);
    expect(out[0].session.id).toBe("b");
    expect(out[0].snippet).not.toBeNull();
    expect(out[0].snippet.match).toBe("coffee");
    expect(
      out[0].snippet.before + out[0].snippet.match + out[0].snippet.after,
    ).toContain("coffee machine");
  });

  it("ranks a title hit above a body-only hit", () => {
    // "garage" is a title word for c and a body word for none other; add a
    // session whose body mentions garage to prove ordering.
    const extra = [
      ...sessions,
      {
        id: "d",
        title: "Notes",
        updated_at: "2026-07-13T10:00:00",
        search_text: "remember to paint the garage next week",
      },
    ];
    const out = filterSessions(extra, "garage");
    expect(out.map((r) => r.session.id)).toEqual(["c", "d"]);
    expect(out[0].snippet).toBeNull(); // title hit, no snippet
    expect(out[1].snippet).not.toBeNull(); // body hit, snippet
  });

  it("excludes sessions that do not match at all", () => {
    expect(filterSessions(sessions, "thermostat")).toHaveLength(0);
  });

  it("does NOT subsequence-match the body (avoids false positives)", () => {
    // "cfe" is a subsequence of "coffee machine ... fires" but appears in no
    // single word — a body subsequence match would be a false positive.
    expect(filterSessions(sessions, "cfe")).toHaveLength(0);
  });

  it("still subsequence-matches short titles", () => {
    // "krm" is a subsequence of "Kitchen" (K…r…? no) — use a real one:
    // "ktn" ⊂ "Kitchen automation".
    const out = filterSessions(sessions, "ktn");
    expect(out).toHaveLength(1);
    expect(out[0].session.id).toBe("b");
  });

  it("requires a contiguous substring for a body-only hit", () => {
    const out = filterSessions(sessions, "coffee machine");
    expect(out).toHaveLength(1);
    expect(out[0].session.id).toBe("b");
    expect(out[0].snippet).not.toBeNull();
  });

  it("is case-insensitive", () => {
    expect(filterSessions(sessions, "GARAGE")).toHaveLength(1);
  });

  it("tolerates missing search_text", () => {
    const out = filterSessions(
      [{ id: "x", title: "Hello", updated_at: "" }],
      "hello",
    );
    expect(out).toHaveLength(1);
  });
});

describe("extractSnippet", () => {
  it("returns null when the query is absent", () => {
    expect(extractSnippet("nothing here", "xyz")).toBeNull();
  });

  it("adds ellipses when the match is not at the edges", () => {
    const text = "prefix ".repeat(20) + "TARGET" + " suffix".repeat(20);
    const snip = extractSnippet(text, "target");
    expect(snip.match).toBe("TARGET");
    expect(snip.before.startsWith("…")).toBe(true);
    expect(snip.after.endsWith("…")).toBe(true);
  });

  it("omits the leading ellipsis when the match is at the start", () => {
    const snip = extractSnippet("target is right here at the front", "target");
    expect(snip.before).toBe("");
    expect(snip.match).toBe("target");
  });
});
