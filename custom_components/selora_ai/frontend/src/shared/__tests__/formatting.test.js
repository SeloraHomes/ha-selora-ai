import { describe, it, expect } from "vitest";
import {
  humanizeToken,
  fmtEntity,
  fmtEntities,
  fmtState,
  fmtDuration,
  fmtWeekdays,
  fmtNumericValue,
  fmtTime,
} from "../formatting.js";

// ---------------------------------------------------------------------------
// humanizeToken
// ---------------------------------------------------------------------------
describe("humanizeToken", () => {
  it("converts underscores to spaces and title-cases", () => {
    expect(humanizeToken("living_room_light")).toBe("Living Room Light");
  });

  it("returns empty string for null", () => {
    expect(humanizeToken(null)).toBe("");
  });

  it("returns empty string for empty string", () => {
    expect(humanizeToken("")).toBe("");
  });

  it("handles single word", () => {
    expect(humanizeToken("kitchen")).toBe("Kitchen");
  });
});

// ---------------------------------------------------------------------------
// fmtEntity
// ---------------------------------------------------------------------------
describe("fmtEntity", () => {
  it("returns friendly_name from hass state", () => {
    const hass = {
      states: {
        "light.living_room": {
          attributes: { friendly_name: "Living Room Light" },
        },
      },
    };
    expect(fmtEntity(hass, "light.living_room")).toBe("Living Room Light");
  });

  it("falls back to humanized entity name without hass", () => {
    expect(fmtEntity(null, "light.living_room")).toBe("Living Room");
  });

  it("returns empty string for falsy id", () => {
    expect(fmtEntity(null, "")).toBe("");
    expect(fmtEntity(null, null)).toBe("");
  });

  it("handles entity without friendly_name in hass", () => {
    const hass = { states: {} };
    expect(fmtEntity(hass, "light.kitchen_main")).toBe("Kitchen Main");
  });
});

// ---------------------------------------------------------------------------
// fmtEntities
// ---------------------------------------------------------------------------
describe("fmtEntities", () => {
  it("returns empty string for falsy input", () => {
    expect(fmtEntities(null, null)).toBe("");
    expect(fmtEntities(null, "")).toBe("");
  });

  it("formats single entity", () => {
    expect(fmtEntities(null, "light.kitchen")).toBe("Kitchen");
  });

  it("formats two entities with 'and'", () => {
    const result = fmtEntities(null, ["light.kitchen", "light.living_room"]);
    expect(result).toBe("Kitchen and Living Room");
  });

  it("formats three+ entities with Oxford comma", () => {
    const result = fmtEntities(null, [
      "light.kitchen",
      "light.living_room",
      "light.bedroom",
    ]);
    expect(result).toBe("Kitchen, Living Room, and Bedroom");
  });

  it("wraps non-array in array", () => {
    expect(fmtEntities(null, "light.kitchen")).toBe("Kitchen");
  });
});

// ---------------------------------------------------------------------------
// fmtState
// ---------------------------------------------------------------------------
describe("fmtState", () => {
  it("returns null for null input", () => {
    expect(fmtState(null)).toBe(null);
  });

  it("maps known states", () => {
    expect(fmtState("on")).toBe("on");
    expect(fmtState("off")).toBe("off");
    expect(fmtState("not_home")).toBe("away");
    expect(fmtState("locked")).toBe("locked");
  });

  it("humanizes unknown states", () => {
    expect(fmtState("some_state")).toBe("some state");
  });
});

// ---------------------------------------------------------------------------
// fmtDuration
// ---------------------------------------------------------------------------
describe("fmtDuration", () => {
  it("returns empty string for falsy input", () => {
    expect(fmtDuration(null)).toBe("");
    expect(fmtDuration("")).toBe("");
  });

  it("returns string as-is", () => {
    expect(fmtDuration("00:05:00")).toBe("00:05:00");
  });

  it("formats object with hours, minutes, seconds", () => {
    expect(fmtDuration({ hours: 1, minutes: 30 })).toBe("1h 30m");
  });

  it("formats object with only seconds", () => {
    expect(fmtDuration({ seconds: 45 })).toBe("45s");
  });

  it("returns stringified value for non-object, non-string", () => {
    expect(fmtDuration(42)).toBe("42");
  });
});

// ---------------------------------------------------------------------------
// fmtWeekdays
// ---------------------------------------------------------------------------
describe("fmtWeekdays", () => {
  it("returns empty string for falsy input", () => {
    expect(fmtWeekdays(null)).toBe("");
  });

  it("maps day abbreviations", () => {
    expect(fmtWeekdays(["mon", "tue"])).toBe("Mon, Tue");
  });

  it("handles full weekday set", () => {
    expect(fmtWeekdays(["mon", "tue", "wed", "thu", "fri"])).toBe(
      "Mon, Tue, Wed, Thu, Fri",
    );
  });

  it("wraps single value in array", () => {
    expect(fmtWeekdays("sat")).toBe("Sat");
  });
});

// ---------------------------------------------------------------------------
// fmtNumericValue
// ---------------------------------------------------------------------------
describe("fmtNumericValue", () => {
  it("returns empty string for null value", () => {
    expect(fmtNumericValue("sensor.temp", null)).toBe("");
    expect(fmtNumericValue("sensor.temp", "")).toBe("");
  });

  it("appends % for battery-like entities", () => {
    expect(fmtNumericValue("sensor.phone_battery_level", 85)).toBe("85%");
  });

  it("does not append % if already has %", () => {
    expect(fmtNumericValue("sensor.battery", "85%")).toBe("85%");
  });

  it("does not append % for non-battery entities", () => {
    expect(fmtNumericValue("sensor.temperature", 22.5)).toBe("22.5");
  });
});

// ---------------------------------------------------------------------------
// fmtTime
// ---------------------------------------------------------------------------
describe("fmtTime", () => {
  it("returns 'null' for null input", () => {
    expect(fmtTime(null, null)).toBe("null");
  });

  it("formats HH:MM to 12-hour time", () => {
    expect(fmtTime(null, "14:30")).toBe("2:30 PM");
    expect(fmtTime(null, "08:00")).toBe("8:00 AM");
  });

  it("formats midnight correctly", () => {
    expect(fmtTime(null, "00:00")).toBe("12:00 AM");
  });

  it("formats raw seconds", () => {
    // 43200 seconds = 12:00 PM
    expect(fmtTime(null, "43200")).toBe("12:00 PM");
  });

  it("handles Jinja template with states() reference", () => {
    const hass = {
      states: {
        "input_datetime.wake_up": {
          attributes: { friendly_name: "Wake Up Time" },
        },
      },
    };
    expect(fmtTime(hass, "{{ states('input_datetime.wake_up') }}")).toBe(
      "Wake Up Time",
    );
  });

  it("returns 'a calculated time' for unrecognized Jinja", () => {
    expect(fmtTime(null, "{{ now() + timedelta(hours=1) }}")).toBe(
      "a calculated time",
    );
  });

  it("resolves entity references", () => {
    expect(fmtTime(null, "input_datetime.bedtime")).toBe("Bedtime");
  });
});
