import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  relativeTime,
  formatDate,
  formatTimeAgo,
  formatTime,
  formatRelativeTime,
} from "../date-utils.js";

// ---------------------------------------------------------------------------
// relativeTime — takes a Date object
// ---------------------------------------------------------------------------
describe("relativeTime", () => {
  it("returns 'just now' for < 60 seconds ago", () => {
    const d = new Date(Date.now() - 30_000);
    expect(relativeTime(d)).toBe("just now");
  });

  it("returns minutes ago", () => {
    const d = new Date(Date.now() - 5 * 60_000);
    expect(relativeTime(d)).toBe("5m ago");
  });

  it("returns hours ago", () => {
    const d = new Date(Date.now() - 3 * 3600_000);
    expect(relativeTime(d)).toBe("3h ago");
  });

  it("returns days ago for < 7 days", () => {
    const d = new Date(Date.now() - 2 * 86400_000);
    expect(relativeTime(d)).toBe("2d ago");
  });

  it("returns toLocaleDateString for >= 7 days", () => {
    const d = new Date(Date.now() - 10 * 86400_000);
    expect(relativeTime(d)).toBe(d.toLocaleDateString());
  });
});

// ---------------------------------------------------------------------------
// formatDate — takes an ISO string
// ---------------------------------------------------------------------------
describe("formatDate", () => {
  it("returns empty string for falsy input", () => {
    expect(formatDate(null)).toBe("");
    expect(formatDate(undefined)).toBe("");
    expect(formatDate("")).toBe("");
  });

  it("returns 'just now' for recent timestamps", () => {
    const iso = new Date(Date.now() - 10_000).toISOString();
    expect(formatDate(iso)).toBe("just now");
  });

  it("returns minutes ago", () => {
    const iso = new Date(Date.now() - 5 * 60_000).toISOString();
    expect(formatDate(iso)).toBe("5m ago");
  });

  it("returns hours ago", () => {
    const iso = new Date(Date.now() - 3 * 3600_000).toISOString();
    expect(formatDate(iso)).toBe("3h ago");
  });

  it("returns date string for > 24h", () => {
    const d = new Date(Date.now() - 48 * 3600_000);
    expect(formatDate(d.toISOString())).toBe(d.toLocaleDateString());
  });

  it("returns empty string for future dates", () => {
    const iso = new Date(Date.now() + 60_000).toISOString();
    expect(formatDate(iso)).toBe("");
  });

  it("returns date string for unparseable input (no exception)", () => {
    // "not-a-date" produces NaN diff → falls through to toLocaleDateString
    const result = formatDate("not-a-date");
    expect(typeof result).toBe("string");
  });
});

// ---------------------------------------------------------------------------
// formatTimeAgo — takes an ISO string, returns null on invalid
// ---------------------------------------------------------------------------
describe("formatTimeAgo", () => {
  it("returns null for falsy input", () => {
    expect(formatTimeAgo(null)).toBe(null);
    expect(formatTimeAgo("")).toBe(null);
  });

  it("returns 'just now' for < 1 minute", () => {
    const iso = new Date(Date.now() - 10_000).toISOString();
    expect(formatTimeAgo(iso)).toBe("just now");
  });

  it("returns minutes ago", () => {
    const iso = new Date(Date.now() - 10 * 60_000).toISOString();
    expect(formatTimeAgo(iso)).toBe("10m ago");
  });

  it("returns hours ago", () => {
    const iso = new Date(Date.now() - 2 * 3600_000).toISOString();
    expect(formatTimeAgo(iso)).toBe("2h ago");
  });

  it("returns days ago", () => {
    const iso = new Date(Date.now() - 3 * 86400_000).toISOString();
    expect(formatTimeAgo(iso)).toBe("3d ago");
  });

  it("returns null for future dates", () => {
    const iso = new Date(Date.now() + 60_000).toISOString();
    expect(formatTimeAgo(iso)).toBe(null);
  });
});

// ---------------------------------------------------------------------------
// formatTime
// ---------------------------------------------------------------------------
describe("formatTime", () => {
  it("returns empty string for falsy input", () => {
    expect(formatTime(null)).toBe("");
    expect(formatTime("")).toBe("");
  });

  it("returns formatted time for valid ISO", () => {
    const iso = "2025-01-15T14:30:00Z";
    const result = formatTime(iso);
    // Should contain colon (time format), exact output depends on locale
    expect(result).toBeTruthy();
    expect(result).toContain(":");
  });
});

// ---------------------------------------------------------------------------
// formatRelativeTime
// ---------------------------------------------------------------------------
describe("formatRelativeTime", () => {
  it("returns 'Never' for falsy input", () => {
    expect(formatRelativeTime(null)).toBe("Never");
    expect(formatRelativeTime("")).toBe("Never");
  });

  it("returns 'Just now' for recent timestamps", () => {
    const iso = new Date(Date.now() - 10_000).toISOString();
    expect(formatRelativeTime(iso)).toBe("Just now");
  });

  it("returns minutes ago", () => {
    const iso = new Date(Date.now() - 5 * 60_000).toISOString();
    expect(formatRelativeTime(iso)).toBe("5m ago");
  });

  it("returns days ago for < 7 days", () => {
    const iso = new Date(Date.now() - 3 * 86400_000).toISOString();
    expect(formatRelativeTime(iso)).toBe("3d ago");
  });

  it("returns toLocaleDateString for >= 7 days", () => {
    const d = new Date(Date.now() - 10 * 86400_000);
    expect(formatRelativeTime(d.toISOString())).toBe(d.toLocaleDateString());
  });
});
