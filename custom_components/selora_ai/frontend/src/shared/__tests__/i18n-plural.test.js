import { describe, it, expect } from "vitest";
import { localizePlural, pickLocale } from "../i18n.js";

// The duration phrase in the delete-device confirmation is the only counted
// string in the panel, and it is read inside a sentence ("ist offline seit …"),
// so getting the plural category wrong produces visibly broken grammar in the
// confirmation for an irreversible action.

const hass = (language) => ({ language });

// Mirrors _offlineFor in panel/render-delete-device-modal.js — days once there
// is at least one, hours below that, both always >= 1.
const offlineFor = (language, seconds) => {
  const days = Math.floor(seconds / 86400);
  if (days >= 1) {
    return localizePlural(hass(language), "insights_offline_days", days, "x");
  }
  const hours = Math.max(1, Math.floor(seconds / 3600));
  return localizePlural(hass(language), "insights_offline_hours", hours, "x");
};

const DAYS = (n) => n * 86400;
const HOURS = (n) => n * 3600;

describe("localizePlural", () => {
  it("interpolates the count into the resolved phrase", () => {
    expect(offlineFor("en", DAYS(9))).toBe("9 days");
    expect(offlineFor("en", HOURS(5))).toBe("5 hours");
  });

  // Hours only ever show below a day, so a 36-hour outage reads as 1 day.
  it("prefers days once there is at least one", () => {
    expect(offlineFor("en", HOURS(36))).toBe("1 day");
    expect(offlineFor("en", HOURS(23))).toBe("23 hours");
  });

  it("uses the singular only for one", () => {
    expect(offlineFor("en", DAYS(1))).toBe("1 day");
    expect(offlineFor("en", HOURS(1))).toBe("1 hour");
  });

  // `seit` governs the dative, so the plural is Tagen rather than Tage. The
  // singular stays Tag (dative singular is unmarked).
  it("gives German the dative plural for days", () => {
    expect(offlineFor("de", DAYS(1))).toBe("1 Tag");
    expect(offlineFor("de", DAYS(2))).toBe("2 Tagen");
    expect(offlineFor("de", DAYS(21))).toBe("21 Tagen");
    expect(offlineFor("de", HOURS(1))).toBe("1 Stunde");
    expect(offlineFor("de", HOURS(5))).toBe("5 Stunden");
  });

  // Three integer forms: 1/21/31, then 2-4/22-24, then 5-20 and 11-14. An
  // English one/other split renders "2 дней" and "21 дней".
  it("selects the right Russian form for each plural category", () => {
    expect(offlineFor("ru", DAYS(1))).toBe("1 день");
    expect(offlineFor("ru", DAYS(2))).toBe("2 дня");
    expect(offlineFor("ru", DAYS(4))).toBe("4 дня");
    expect(offlineFor("ru", DAYS(5))).toBe("5 дней");
    expect(offlineFor("ru", DAYS(11))).toBe("11 дней");
    expect(offlineFor("ru", DAYS(14))).toBe("14 дней");
    expect(offlineFor("ru", DAYS(21))).toBe("21 день");
    expect(offlineFor("ru", DAYS(22))).toBe("22 дня");
    expect(offlineFor("ru", HOURS(1))).toBe("1 час");
    expect(offlineFor("ru", HOURS(2))).toBe("2 часа");
    expect(offlineFor("ru", HOURS(11))).toBe("11 часов");
    expect(offlineFor("ru", HOURS(21))).toBe("21 час");
  });

  // The separator belongs to the language: a caller joining `${n} ${word}`
  // would put a space where Japanese wants none.
  it("keeps CJK counters unspaced", () => {
    expect(offlineFor("ja", DAYS(2))).toBe("2日");
    expect(offlineFor("ja", HOURS(3))).toBe("3時間");
    expect(offlineFor("ko", DAYS(2))).toBe("2일");
  });

  it("keeps the Hungarian noun singular after a numeral", () => {
    expect(offlineFor("hu", DAYS(1))).toBe("1 nap");
    expect(offlineFor("hu", DAYS(7))).toBe("7 nap");
  });

  it("resolves a region subtag to its shipped catalog", () => {
    expect(pickLocale(hass("de-CH"))).toBe("de");
    expect(offlineFor("de-CH", DAYS(3))).toBe("3 Tagen");
    expect(pickLocale(hass("zh-Hant"))).toBe("zh-hant");
  });

  it("falls back to English for an unshipped language", () => {
    expect(offlineFor("cs", DAYS(3))).toBe("3 days");
  });

  // A tag Intl cannot parse must not take the dialog down with it — the count
  // still has to render.
  it("survives a malformed locale tag", () => {
    expect(offlineFor("not a tag", DAYS(3))).toBe("3 days");
  });

  it("uses the caller fallback when the key is absent entirely", () => {
    expect(
      localizePlural(hass("en"), "no_such_key", 4, "{count} widgets"),
    ).toBe("4 widgets");
  });
});
