import { describe, it, expect } from "vitest";
import { _maybeDecodePercentEncoded } from "../render-chat.js";

const YAML = `alias: Doorbell Announcement
description: Announces on the Home Assistant Voice
mode: single
conditions: []`;

describe("_maybeDecodePercentEncoded", () => {
  it("decodes a fully percent-encoded multi-line document", () => {
    expect(_maybeDecodePercentEncoded(encodeURIComponent(YAML))).toBe(YAML);
  });

  it("decodes a form-encoded blob (spaces as +)", () => {
    const formEncoded = encodeURIComponent(YAML).replace(/%20/g, "+");
    expect(_maybeDecodePercentEncoded(formEncoded)).toBe(YAML);
  });

  it("preserves a literal plus carried as %2B in a form-encoded blob", () => {
    const src = "a + b\nc";
    // encodeURIComponent keeps `+`? No — it encodes it to %2B. Simulate a
    // form-encoded source: real spaces -> '+', literal '+' -> %2B.
    const formEncoded = "a+%2B+b%0Ac";
    expect(_maybeDecodePercentEncoded(formEncoded)).toBe(src);
  });

  it("leaves a URL with an encoded space untouched", () => {
    expect(
      _maybeDecodePercentEncoded("https://example.com/foo%20bar"),
    ).toBeNull();
  });

  it("leaves a mailto URL containing an encoded newline untouched", () => {
    expect(_maybeDecodePercentEncoded("mailto:?body=Line1%0ALine2")).toBeNull();
  });

  it("leaves an API query URL containing an encoded newline untouched", () => {
    expect(
      _maybeDecodePercentEncoded("https://api.example.com/x?q=a%0Ab"),
    ).toBeNull();
  });

  it("leaves a scheme-less URL with an encoded newline untouched", () => {
    expect(_maybeDecodePercentEncoded("example.com/x?q=a%0Ab")).toBeNull();
  });

  it("ignores a short encoded token with no encoded newline", () => {
    expect(_maybeDecodePercentEncoded("alias%3A%20Doorbell")).toBeNull();
  });

  it("ignores text that already contains literal whitespace", () => {
    expect(_maybeDecodePercentEncoded("alias: Doorbell\nmode%0A")).toBeNull();
  });

  it("ignores prose with a stray literal percent sign", () => {
    expect(_maybeDecodePercentEncoded("50%off%0Asale")).toBeNull();
  });

  it("returns null on malformed percent sequences", () => {
    expect(_maybeDecodePercentEncoded("foo%0Abar%ZZ")).toBeNull();
  });

  it("returns null for non-strings and empty input", () => {
    expect(_maybeDecodePercentEncoded("")).toBeNull();
    expect(_maybeDecodePercentEncoded(null)).toBeNull();
    expect(_maybeDecodePercentEncoded(undefined)).toBeNull();
  });
});
