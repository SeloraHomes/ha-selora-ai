import { describe, it, expect } from "vitest";
import {
  describeFlowItem,
  collectFlowEntityIds,
  collectFlowDeviceRefs,
  displayTriggers,
  mergeEquivalentTriggers,
  asArray,
} from "../flow-description.js";

const mockHass = {
  states: {
    "light.living_room": {
      attributes: { friendly_name: "Living Room Light" },
    },
    "binary_sensor.front_door": {
      attributes: { friendly_name: "Front Door" },
    },
    "sensor.temperature": {
      attributes: { friendly_name: "Temperature Sensor" },
    },
    "cover.blinds": {
      attributes: { friendly_name: "Blinds" },
    },
  },
};

// ---------------------------------------------------------------------------
// Triggers
// ---------------------------------------------------------------------------
describe("Triggers", () => {
  it("describes time trigger", () => {
    const result = describeFlowItem(mockHass, {
      platform: "time",
      at: "08:00",
    });
    expect(result).toContain("When the time is");
    expect(result).toContain("08:00");
  });

  it("describes time trigger with array of times", () => {
    const result = describeFlowItem(mockHass, {
      platform: "time",
      at: ["08:00", "20:00"],
    });
    expect(result).toContain("or");
  });

  it("describes state trigger — turns on", () => {
    const result = describeFlowItem(mockHass, {
      platform: "state",
      entity_id: "light.living_room",
      to: "on",
    });
    expect(result).toBe("When Living Room Light turns on");
  });

  it("describes state trigger — turns off", () => {
    const result = describeFlowItem(mockHass, {
      platform: "state",
      entity_id: "light.living_room",
      to: "off",
    });
    expect(result).toBe("When Living Room Light turns off");
  });

  it("describes state trigger with from/to", () => {
    const result = describeFlowItem(mockHass, {
      platform: "state",
      entity_id: "light.living_room",
      from: "off",
      to: "on",
    });
    // Should mention "changes from off to on" since to is "on" it uses "turns on"
    expect(result).toContain("Living Room Light");
    expect(result).toContain("on");
  });

  it("describes state trigger with duration", () => {
    const result = describeFlowItem(mockHass, {
      platform: "state",
      entity_id: "light.living_room",
      to: "on",
      for: { minutes: 5 },
    });
    expect(result).toContain("for 5m");
  });

  it("describes sun trigger — sunset", () => {
    const result = describeFlowItem(mockHass, {
      platform: "sun",
      event: "sunset",
    });
    expect(result).toBe("When it is sunset");
  });

  it("describes sun trigger with positive offset", () => {
    const result = describeFlowItem(mockHass, {
      platform: "sun",
      event: "sunset",
      offset: "01:00:00",
    });
    expect(result).toBe("1h after sunset");
  });

  it("describes sun trigger with negative offset", () => {
    const result = describeFlowItem(mockHass, {
      platform: "sun",
      event: "sunrise",
      offset: "-00:30:00",
    });
    expect(result).toBe("30min before sunrise");
  });

  it("describes sun trigger with hours and minutes offset", () => {
    const result = describeFlowItem(mockHass, {
      platform: "sun",
      event: "sunset",
      offset: "-01:30:00",
    });
    expect(result).toBe("1h 30min before sunset");
  });

  it("describes sun trigger with seconds offset", () => {
    const result = describeFlowItem(mockHass, {
      platform: "sun",
      event: "sunrise",
      offset: "-00:00:30",
    });
    expect(result).toBe("30s before sunrise");
  });

  it("describes sun trigger with minutes and seconds offset", () => {
    const result = describeFlowItem(mockHass, {
      platform: "sun",
      event: "sunset",
      offset: "00:05:30",
    });
    expect(result).toBe("5min 30s after sunset");
  });

  it("describes numeric_state trigger — above", () => {
    const result = describeFlowItem(mockHass, {
      platform: "numeric_state",
      entity_id: "sensor.temperature",
      above: 30,
    });
    expect(result).toContain("Temperature Sensor");
    expect(result).toContain("rises above");
    expect(result).toContain("30");
  });

  it("describes numeric_state trigger — below", () => {
    const result = describeFlowItem(mockHass, {
      platform: "numeric_state",
      entity_id: "sensor.temperature",
      below: 10,
    });
    expect(result).toContain("drops below");
  });

  it("describes numeric_state trigger — between", () => {
    const result = describeFlowItem(mockHass, {
      platform: "numeric_state",
      entity_id: "sensor.temperature",
      above: 20,
      below: 30,
    });
    expect(result).toContain("between");
  });

  it("describes homeassistant trigger — start", () => {
    const result = describeFlowItem(mockHass, {
      platform: "homeassistant",
      event: "start",
    });
    expect(result).toBe("When Home Assistant starts");
  });

  it("describes time_pattern trigger", () => {
    expect(
      describeFlowItem(mockHass, { platform: "time_pattern", minutes: 5 }),
    ).toBe("Every 5 minutes");
    expect(
      describeFlowItem(mockHass, { platform: "time_pattern", seconds: 1 }),
    ).toBe("Every 1 second");
  });

  it("describes webhook trigger", () => {
    expect(describeFlowItem(mockHass, { platform: "webhook" })).toBe(
      "When an outside service sends an update",
    );
  });

  it("describes zone trigger", () => {
    const result = describeFlowItem(mockHass, {
      platform: "zone",
      entity_id: "binary_sensor.front_door",
      zone: "zone.home",
      event: "enter",
    });
    expect(result).toContain("Front Door");
    expect(result).toContain("enters");
  });

  it("describes device trigger", () => {
    const result = describeFlowItem(mockHass, {
      platform: "device",
      device_id: "abc123",
      type: "turned_on",
    });
    expect(result).toContain("device");
    expect(result).toContain("turned on");
  });

  it("uses the device registry name for device triggers when available", () => {
    const hass = {
      ...mockHass,
      devices: { abc123: { name: "Front Door Lock" } },
    };
    const result = describeFlowItem(hass, {
      trigger: "device",
      device_id: "abc123",
      domain: "lock",
      type: "unlocked",
    });
    expect(result).toBe("When Front Door Lock unlocked");
  });

  it("prefers the user-given device name over the integration name", () => {
    const hass = {
      ...mockHass,
      devices: {
        abc123: { name: "ZB-LOCK-01", name_by_user: "Front Door Lock" },
      },
    };
    const result = describeFlowItem(hass, {
      trigger: "device",
      device_id: "abc123",
      type: "unlocked",
    });
    expect(result).toBe("When Front Door Lock unlocked");
  });

  it("describes event trigger", () => {
    const result = describeFlowItem(mockHass, {
      platform: "event",
      event_type: "tag_scanned",
    });
    expect(result).toContain("tag scanned");
    expect(result).toContain("happens");
  });

  it("handles unknown trigger platform gracefully", () => {
    const result = describeFlowItem(mockHass, {
      platform: "future_platform",
    });
    expect(result).toBe("When this trigger happens");
  });

  it("handles 'trigger' key (new format)", () => {
    const result = describeFlowItem(mockHass, {
      trigger: "time",
      at: "08:00",
    });
    expect(result).toContain("When the time is");
  });

  it("describes bare state trigger on a sensor as a value change", () => {
    const result = describeFlowItem(mockHass, {
      trigger: "state",
      entity_id: "sensor.temperature",
    });
    expect(result).toBe("When Temperature Sensor value changes");
  });

  it("keeps 'changes state' for bare state trigger on non-sensor", () => {
    const result = describeFlowItem(mockHass, {
      trigger: "state",
      entity_id: "binary_sensor.front_door",
    });
    expect(result).toBe("When Front Door changes state");
  });
});

// ---------------------------------------------------------------------------
// Conditions
// ---------------------------------------------------------------------------
describe("Conditions", () => {
  it("describes state condition", () => {
    const result = describeFlowItem(mockHass, {
      condition: "state",
      entity_id: "light.living_room",
      state: "on",
    });
    expect(result).toBe("Living Room Light is on");
  });

  it("describes time condition with weekday", () => {
    const result = describeFlowItem(mockHass, {
      condition: "time",
      weekday: ["mon", "tue", "wed", "thu", "fri"],
    });
    expect(result).toContain("Mon, Tue, Wed, Thu, Fri");
  });

  it("describes time condition with after/before", () => {
    const result = describeFlowItem(mockHass, {
      condition: "time",
      after: "08:00",
      before: "22:00",
    });
    expect(result).toContain("after");
    expect(result).toContain("before");
  });

  it("describes sun condition", () => {
    const result = describeFlowItem(mockHass, {
      condition: "sun",
      after: "sunset",
      before: "sunrise",
    });
    expect(result).toContain("sunset");
    expect(result).toContain("sunrise");
  });

  it("describes 'and' condition", () => {
    const result = describeFlowItem(mockHass, {
      condition: "and",
      conditions: [{}, {}],
    });
    expect(result).toBe("All 2 conditions must be true");
  });

  it("phrases a trigger condition on a time trigger as a time check", () => {
    const result = describeFlowItem(
      mockHass,
      { condition: "trigger", id: "at_11" },
      { triggers: [{ trigger: "time", at: "11:00:00", id: "at_11" }] },
    );
    expect(result).toBe("at 11:00");
  });

  it("quotes the trigger description for non-time trigger conditions", () => {
    const result = describeFlowItem(
      mockHass,
      { condition: "trigger", id: "door" },
      {
        triggers: [
          {
            trigger: "state",
            entity_id: "binary_sensor.front_door",
            to: "on",
            id: "door",
          },
        ],
      },
    );
    expect(result).toContain("Triggered by");
    expect(result).toContain("When Front Door turns on");
  });

  it("falls back to the raw id when a trigger condition has no match", () => {
    const result = describeFlowItem(mockHass, {
      condition: "trigger",
      id: "at_11",
    });
    expect(result).toContain("Triggered by");
    expect(result).toContain("at_11");
  });

  it("resolves a trigger condition against a default (index) trigger id", () => {
    // A trigger with no explicit id gets its zero-based index as the id,
    // so `condition: trigger` with id "0" targets the first trigger.
    const result = describeFlowItem(
      mockHass,
      { condition: "trigger", id: "0" },
      { triggers: [{ trigger: "time", at: "11:00:00" }] },
    );
    expect(result).toBe("at 11:00");
  });

  it("resolves a default index id to a non-time trigger description", () => {
    const result = describeFlowItem(
      mockHass,
      { condition: "trigger", id: "1" },
      {
        triggers: [
          { trigger: "time", at: "08:00" },
          { trigger: "state", entity_id: "light.living_room", to: "on" },
        ],
      },
    );
    expect(result).toContain("Triggered by");
    expect(result).toContain("When Living Room Light turns on");
  });

  it("describes an inclusive range comparison template on an attribute", () => {
    const tmpl =
      "{{ (state_attr('cover.blinds', 'current_position') | int(0)) >= 45 " +
      "and (state_attr('cover.blinds', 'current_position') | int(0)) <= 55 }}";
    const result = describeFlowItem(mockHass, {
      condition: "template",
      value_template: tmpl,
    });
    expect(result).toBe("Blinds current position between 45 and 55");
  });

  it("stays generic for a strict two-sided range", () => {
    // "between" reads inclusive, so a `> … <` range would misstate the
    // boundary — fall back rather than claim an inclusivity it lacks.
    const tmpl =
      "{{ states('sensor.temperature') | float > 45 and " +
      "states('sensor.temperature') | float < 55 }}";
    const result = describeFlowItem(mockHass, {
      condition: "template",
      value_template: tmpl,
    });
    expect(result).toBe("Template evaluates to true");
  });

  it("stays generic for a mixed-inclusivity two-sided range", () => {
    const tmpl =
      "{{ states('sensor.temperature') | float >= 45 and " +
      "states('sensor.temperature') | float < 55 }}";
    const result = describeFlowItem(mockHass, {
      condition: "template",
      value_template: tmpl,
    });
    expect(result).toBe("Template evaluates to true");
  });

  it("describes a single-bound comparison template on states()", () => {
    const result = describeFlowItem(mockHass, {
      condition: "template",
      value_template: "{{ states('sensor.temperature') | float > 20 }}",
    });
    expect(result).toBe("Temperature Sensor above 20");
  });

  it("renders >= as inclusive 'at least', not 'above'", () => {
    // Regression: dropping the "=" contradicted the automation at the
    // boundary value (20 satisfies >= 20 but not "above 20").
    const result = describeFlowItem(mockHass, {
      condition: "template",
      value_template: "{{ states('sensor.temperature') | float >= 20 }}",
    });
    expect(result).toBe("Temperature Sensor at least 20");
  });

  it("renders <= as inclusive 'at most', not 'below'", () => {
    const result = describeFlowItem(mockHass, {
      condition: "template",
      value_template: "{{ states('sensor.temperature') | float <= 20 }}",
    });
    expect(result).toBe("Temperature Sensor at most 20");
  });

  it("keeps strict < as 'below'", () => {
    const result = describeFlowItem(mockHass, {
      condition: "template",
      value_template: "{{ states('sensor.temperature') | float < 20 }}",
    });
    expect(result).toBe("Temperature Sensor below 20");
  });

  it("stays generic when a value-transforming filter is applied", () => {
    // Regression: `| abs` was discarded, so -30 (which satisfies the real
    // template) would be described as "above 20". Only int/float convert.
    const result = describeFlowItem(mockHass, {
      condition: "template",
      value_template: "{{ states('sensor.temperature') | float | abs > 20 }}",
    });
    expect(result).toBe("Template evaluates to true");
  });

  it("stays generic for a rounding filter", () => {
    const result = describeFlowItem(mockHass, {
      condition: "template",
      value_template: "{{ states('sensor.temperature') | round(1) > 20 }}",
    });
    expect(result).toBe("Template evaluates to true");
  });

  it("describes an is_state template like a state condition", () => {
    const result = describeFlowItem(mockHass, {
      condition: "template",
      value_template: "{{ is_state('light.living_room', 'on') }}",
    });
    expect(result).toBe("Living Room Light is on");
  });

  it("uses the device registry name for device conditions when available", () => {
    const hass = {
      ...mockHass,
      devices: { abc123: { name: "Front Door Lock" } },
    };
    const result = describeFlowItem(hass, {
      condition: "device",
      device_id: "abc123",
      domain: "lock",
      type: "is_locked",
    });
    expect(result).toBe("Front Door Lock is locked");
  });

  it("keeps generic wording for templates without entity references", () => {
    const result = describeFlowItem(mockHass, {
      condition: "template",
      value_template: "{{ now().hour > 8 }}",
    });
    expect(result).toBe("Template evaluates to true");
  });

  it("does not invert a negated is_state template", () => {
    // Regression: an unanchored match reported "Living Room Light is on"
    // for the opposite condition. A negated template must stay generic.
    const result = describeFlowItem(mockHass, {
      condition: "template",
      value_template: "{{ not is_state('light.living_room', 'on') }}",
    });
    expect(result).toBe("Template evaluates to true");
  });

  it("does not drop clauses from a compound is_state template", () => {
    // Regression: only the first is_state() was described, silently
    // omitting the second — the full expression must stay generic.
    const result = describeFlowItem(mockHass, {
      condition: "template",
      value_template:
        "{{ is_state('light.living_room', 'on') and " +
        "is_state('cover.blinds', 'open') }}",
    });
    expect(result).toBe("Template evaluates to true");
  });

  it("stays generic for a disjunctive comparison template", () => {
    const result = describeFlowItem(mockHass, {
      condition: "template",
      value_template:
        "{{ states('sensor.temperature') | float > 30 or " +
        "states('sensor.temperature') | float < 5 }}",
    });
    expect(result).toBe("Template evaluates to true");
  });

  it("stays generic when a range spans two different entities", () => {
    const result = describeFlowItem(mockHass, {
      condition: "template",
      value_template:
        "{{ states('sensor.temperature') | float > 20 and " +
        "states('cover.blinds') | float < 55 }}",
    });
    expect(result).toBe("Template evaluates to true");
  });

  it("stays generic when a template mixes a comparison with extra logic", () => {
    const result = describeFlowItem(mockHass, {
      condition: "template",
      value_template:
        "{{ states('sensor.temperature') | float > 20 and is_state('light.living_room', 'on') }}",
    });
    expect(result).toBe("Template evaluates to true");
  });
});

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// A sun window, both bounds and both offsets
//
// "Replace the illuminance with 15 minutes before sunset to 15 minutes after
// sunrise" produced the right YAML and a flowchart reading "after sunset" /
// "before sunrise" — the offsets were dropped, so a window asked to open
// fifteen minutes early read as opening at sunset. A condition keeps its shift
// in `after_offset` / `before_offset`, which the condition branch never looked
// at.
// ---------------------------------------------------------------------------
describe("Sun conditions", () => {
  it("renders the offset on each bound", () => {
    expect(
      describeFlowItem(mockHass, {
        condition: "sun",
        after: "sunset",
        after_offset: "-00:15:00",
      }),
    ).toBe("after 15min before sunset");
    expect(
      describeFlowItem(mockHass, {
        condition: "sun",
        before: "sunrise",
        before_offset: "00:15:00",
      }),
    ).toBe("before 15min after sunrise");
  });

  // Both bounds on ONE condition is HA's wrap-around window — the
  // sunset/sunrise pair is special-cased to an OR, not an AND — so it is one
  // fact and has to read as one. Joined with ", " it stated two independent
  // tests, which is the reading that makes a night window look impossible and
  // sends the user off to "fix" a condition that was right.
  it("reads both bounds of one condition as a single window", () => {
    expect(
      describeFlowItem(mockHass, {
        condition: "sun",
        after: "sunset",
        after_offset: "-00:15:00",
        before: "sunrise",
        before_offset: "00:15:00",
      }),
    ).toBe("between 15min before sunset and 15min after sunrise");
  });

  it("reads a daytime window the same way", () => {
    expect(
      describeFlowItem(mockHass, {
        condition: "sun",
        after: "sunrise",
        before: "sunset",
      }),
    ).toBe("between sunrise and sunset");
  });

  it("keeps the bare event when there is no offset", () => {
    expect(
      describeFlowItem(mockHass, { condition: "sun", after: "sunset" }),
    ).toBe("after sunset");
  });

  it("treats an all-zero offset as no offset", () => {
    expect(
      describeFlowItem(mockHass, {
        condition: "sun",
        after: "sunset",
        after_offset: "00:00:00",
      }),
    ).toBe("after sunset");
  });

  it("falls back to the raw value rather than dropping an unparseable offset", () => {
    expect(
      describeFlowItem(mockHass, {
        condition: "sun",
        after: "sunset",
        after_offset: "whenever",
      }),
    ).toBe("after whenever after sunset");
  });

  it("accepts the mapping and seconds forms HA also allows", () => {
    expect(
      describeFlowItem(mockHass, {
        condition: "sun",
        after: "sunset",
        after_offset: { minutes: 30 },
      }),
    ).toBe("after 30min after sunset");
    expect(
      describeFlowItem(mockHass, {
        condition: "sun",
        before: "sunrise",
        before_offset: -900,
      }),
    ).toBe("before 15min before sunrise");
  });

  it("reads the direction from a signed mapping", () => {
    // `cv.time_period` accepts `{minutes: -30}` — half an hour BEFORE. Handing
    // that straight to `fmtDuration` kept the minus inside the label, so the
    // chip stated the direction twice and got one of them wrong: "after -30m
    // after sunset".
    expect(
      describeFlowItem(mockHass, {
        condition: "sun",
        after: "sunset",
        after_offset: { minutes: -30 },
      }),
    ).toBe("after 30min before sunset");
    expect(
      describeFlowItem(mockHass, {
        condition: "sun",
        after: "sunset",
        after_offset: { hours: -1, minutes: -15 },
      }),
    ).toBe("after 1h 15min before sunset");
  });

  it("leaves the trigger form alone", () => {
    expect(
      describeFlowItem(mockHass, {
        platform: "sun",
        event: "sunset",
        offset: "-00:15:00",
      }),
    ).toBe("15min before sunset");
  });
});

describe("Actions", () => {
  it("describes turn_on action with target", () => {
    const result = describeFlowItem(mockHass, {
      action: "light.turn_on",
      target: { entity_id: "light.living_room" },
    });
    expect(result).toBe("Turn on Living Room Light");
  });

  it("describes turn_off action", () => {
    const result = describeFlowItem(mockHass, {
      action: "light.turn_off",
      target: { entity_id: "light.living_room" },
    });
    expect(result).toBe("Turn off Living Room Light");
  });

  it("describes action with brightness", () => {
    const result = describeFlowItem(mockHass, {
      action: "light.turn_on",
      target: { entity_id: "light.living_room" },
      data: { brightness_pct: 50 },
    });
    expect(result).toContain("50%");
  });

  it("describes notification action", () => {
    const result = describeFlowItem(mockHass, {
      action: "notify.mobile_app_phone",
      data: { title: "Alert", message: "Door opened" },
    });
    expect(result).toContain("Notify");
    expect(result).toContain("Alert");
  });

  it("describes persistent notification", () => {
    const result = describeFlowItem(mockHass, {
      action: "notify.persistent_notification",
      data: { message: "Something happened" },
    });
    expect(result).toContain("Notify");
  });

  it("describes tts action", () => {
    const result = describeFlowItem(mockHass, {
      action: "tts.google_translate_say",
      data: { message: "Hello world" },
    });
    expect(result).toContain('Say: "Hello world"');
  });

  it("describes delay action (string)", () => {
    const result = describeFlowItem(mockHass, {
      delay: "00:05:00",
    });
    expect(result).toBe("Wait 00:05:00");
  });

  it("describes delay action (object)", () => {
    const result = describeFlowItem(mockHass, {
      delay: { minutes: 5, seconds: 30 },
    });
    expect(result).toBe("Wait 5m 30s");
  });

  it("describes choose action", () => {
    const result = describeFlowItem(mockHass, {
      choose: [{}, {}, {}],
    });
    expect(result).toBe("Choose between 3 options");
  });

  it("describes repeat action with count", () => {
    const result = describeFlowItem(mockHass, {
      repeat: { count: 3 },
    });
    expect(result).toBe("Repeat 3 times");
  });

  it("describes parallel action", () => {
    const result = describeFlowItem(mockHass, {
      parallel: [{}, {}],
    });
    expect(result).toBe("Run 2 actions in parallel");
  });

  it("describes scene activation", () => {
    const result = describeFlowItem(mockHass, {
      scene: "scene.movie_night",
    });
    expect(result).toContain("Activate scene");
  });

  it("describes variables step", () => {
    const result = describeFlowItem(mockHass, {
      variables: { brightness: 100 },
    });
    expect(result).toBe("Set variables");
  });
});

// ---------------------------------------------------------------------------
// Edge cases
// ---------------------------------------------------------------------------
describe("Edge cases", () => {
  it("returns string for null item", () => {
    expect(describeFlowItem(mockHass, null)).toBe("");
  });

  it("returns string for non-object", () => {
    expect(describeFlowItem(mockHass, "hello")).toBe("hello");
  });

  it("returns fallback for empty object", () => {
    const result = describeFlowItem(mockHass, {});
    expect(result).toBe("Automation step");
  });
});

describe("collectFlowEntityIds", () => {
  it("collects entity ids referenced inside value_template", () => {
    const ids = collectFlowEntityIds({
      condition: "template",
      value_template:
        "{{ (state_attr('cover.blinds', 'current_position') | int(0)) >= 45 " +
        "and states('sensor.temperature') | float > 20 }}",
    });
    expect(ids).toEqual(["cover.blinds", "sensor.temperature"]);
  });

  it("still collects plain entity_id fields", () => {
    const ids = collectFlowEntityIds({
      condition: "state",
      entity_id: "light.living_room",
      state: "on",
    });
    expect(ids).toEqual(["light.living_room"]);
  });
});

describe("mergeEquivalentTriggers", () => {
  it("merges bare state triggers into one multi-entity trigger", () => {
    const merged = mergeEquivalentTriggers([
      { trigger: "state", entity_id: "sensor.uv" },
      { trigger: "state", entity_id: "sensor.temperature" },
      { trigger: "time", at: "11:00:00", id: "at_11" },
    ]);
    expect(merged).toHaveLength(2);
    expect(merged[0].entity_id).toEqual(["sensor.uv", "sensor.temperature"]);
    expect(merged[1].trigger).toBe("time");
  });

  it("keeps triggers with target states, durations, or ids separate", () => {
    const triggers = [
      { trigger: "state", entity_id: "light.a", to: "on" },
      { trigger: "state", entity_id: "sensor.b", for: { minutes: 5 } },
      { trigger: "state", entity_id: "sensor.c", id: "c" },
    ];
    expect(mergeEquivalentTriggers(triggers)).toEqual(triggers);
  });

  it("does not merge triggers constrained by not_from / not_to", () => {
    const triggers = [
      { trigger: "state", entity_id: "sensor.a", not_to: "unavailable" },
      { trigger: "state", entity_id: "sensor.b", not_from: "unavailable" },
    ];
    expect(mergeEquivalentTriggers(triggers)).toEqual(triggers);
  });

  it("joins merged entities with 'or' in the description", () => {
    const [merged] = mergeEquivalentTriggers([
      { trigger: "state", entity_id: "sensor.temperature" },
      { trigger: "state", entity_id: "sensor.humidity" },
    ]);
    expect(describeFlowItem(mockHass, merged)).toBe(
      "When Temperature Sensor or Humidity value changes",
    );
  });
});

describe("displayTriggers", () => {
  // Shape of the "Stores UV Management" automation: every trigger either
  // re-evaluates conditions shown in the branches or is quoted there via
  // a trigger condition — the section carries no extra information.
  const uvTriggers = [
    { entity_id: "sensor.uv", trigger: "state" },
    { entity_id: "sensor.temperature", trigger: "state" },
    { at: "11:00:00", id: "at_11", trigger: "time" },
  ];
  const uvActions = [
    {
      choose: [
        {
          conditions: {
            condition: "and",
            conditions: [
              { condition: "time", after: "07:00:00", before: "11:00:00" },
              { condition: "numeric_state", entity_id: "sensor.uv", above: 5 },
              {
                condition: "numeric_state",
                entity_id: "sensor.temperature",
                above: 16,
              },
            ],
          },
          sequence: [{ service: "scene.turn_on" }],
        },
        {
          conditions: {
            condition: "and",
            conditions: [
              { condition: "trigger", id: "at_11" },
              {
                condition: "template",
                value_template:
                  "{{ state_attr('cover.blinds', 'current_position') | int(0) >= 45 }}",
              },
            ],
          },
          sequence: [{ service: "cover.open_cover" }],
        },
      ],
    },
  ];

  it("hides the section when every trigger is redundant with the logic below", () => {
    expect(displayTriggers(uvTriggers, [], uvActions)).toEqual([]);
  });

  it("keeps triggers that carry their own semantics", () => {
    const triggers = [
      { trigger: "state", entity_id: "binary_sensor.motion", to: "on" },
    ];
    const actions = [{ service: "light.turn_on" }];
    expect(displayTriggers(triggers, [], actions)).toEqual(triggers);
  });

  it("keeps bare state triggers whose entity is not in any condition", () => {
    const triggers = [{ trigger: "state", entity_id: "sensor.uv" }];
    const actions = [{ service: "notify.notify" }];
    const shown = displayTriggers(triggers, [], actions);
    expect(shown).toHaveLength(1);
    expect(asArray(shown[0].entity_id)).toEqual(["sensor.uv"]);
  });

  it("shows all triggers (merged) when only some are redundant", () => {
    const triggers = [
      { trigger: "state", entity_id: "sensor.uv" },
      { trigger: "state", entity_id: "sensor.humidity" },
    ];
    const conditions = [
      { condition: "numeric_state", entity_id: "sensor.uv", above: 5 },
    ];
    const shown = displayTriggers(triggers, conditions, []);
    expect(shown).toHaveLength(1);
    expect(shown[0].entity_id).toEqual(["sensor.uv", "sensor.humidity"]);
  });

  it("finds trigger-condition references in top-level conditions too", () => {
    const triggers = [{ trigger: "time", at: "11:00:00", id: "at_11" }];
    const conditions = [{ condition: "trigger", id: "at_11" }];
    expect(displayTriggers(triggers, conditions, [])).toEqual([]);
  });

  it("treats a trigger referenced by its default index id as redundant", () => {
    const triggers = [{ trigger: "time", at: "11:00:00" }];
    const conditions = [{ condition: "trigger", id: "0" }];
    expect(displayTriggers(triggers, conditions, [])).toEqual([]);
  });

  it("keeps a state-change trigger when a choose default runs unconditionally", () => {
    // Regression: the trigger's entity recurring in a branch condition hid
    // the whole section, dropping "runs on every temperature change" — the
    // default branch runs on each change regardless of the condition.
    const triggers = [{ trigger: "state", entity_id: "sensor.temperature" }];
    const actions = [
      {
        choose: [
          {
            conditions: [
              {
                condition: "numeric_state",
                entity_id: "sensor.temperature",
                above: 20,
              },
            ],
            sequence: [{ service: "light.turn_on" }],
          },
        ],
        default: [{ service: "light.turn_off" }],
      },
    ];
    const shown = displayTriggers(triggers, [], actions);
    expect(shown).toHaveLength(1);
    expect(asArray(shown[0].entity_id)).toEqual(["sensor.temperature"]);
  });

  it("keeps a state-change trigger when a top-level action is unconditional", () => {
    const triggers = [{ trigger: "state", entity_id: "sensor.temperature" }];
    const conditions = [
      {
        condition: "numeric_state",
        entity_id: "sensor.temperature",
        above: 20,
      },
    ];
    const actions = [{ service: "notify.notify" }];
    expect(displayTriggers(triggers, conditions, actions)).toHaveLength(1);
  });

  it("still hides when every branch is conditional (no default)", () => {
    const triggers = [{ trigger: "state", entity_id: "sensor.temperature" }];
    const actions = [
      {
        choose: [
          {
            conditions: [
              {
                condition: "numeric_state",
                entity_id: "sensor.temperature",
                above: 20,
              },
            ],
            sequence: [{ service: "light.turn_on" }],
          },
        ],
      },
    ];
    expect(displayTriggers(triggers, [], actions)).toEqual([]);
  });

  it("suppresses a trigger whose only overlap is an if condition", () => {
    // renderActionItem expands `if` into an IF branch, so the condition is
    // visible and the trigger would only re-state it.
    const triggers = [{ trigger: "state", entity_id: "sensor.temperature" }];
    const actions = [
      {
        if: [
          {
            condition: "numeric_state",
            entity_id: "sensor.temperature",
            above: 20,
          },
        ],
        then: [{ service: "light.turn_on" }],
      },
    ];
    expect(displayTriggers(triggers, [], actions)).toEqual([]);
  });

  it("suppresses a trigger overlapping a shorthand template if condition", () => {
    // The renderer expands the shorthand to a template condition and shows
    // "Light A is on", so the trigger only re-states it.
    const triggers = [{ trigger: "state", entity_id: "light.a" }];
    const actions = [
      {
        if: "{{ is_state('light.a', 'on') }}",
        then: [{ service: "light.turn_on" }],
      },
    ];
    expect(displayTriggers(triggers, [], actions)).toEqual([]);
  });

  it("keeps a trigger when the shorthand template stays opaque", () => {
    // A negated template renders as "Template evaluates to true" with no
    // entity visible, so the trigger is the only place light.a appears.
    const triggers = [{ trigger: "state", entity_id: "light.a" }];
    const actions = [
      {
        if: "{{ not is_state('light.a', 'on') }}",
        then: [{ service: "light.turn_on" }],
      },
    ];
    expect(displayTriggers(triggers, [], actions)).toHaveLength(1);
  });

  it("keeps that trigger when the if has an else branch", () => {
    // An `if`/`else` always runs one side, so the trigger's timing is
    // load-bearing on every firing.
    const triggers = [{ trigger: "state", entity_id: "sensor.temperature" }];
    const actions = [
      {
        if: [
          {
            condition: "numeric_state",
            entity_id: "sensor.temperature",
            above: 20,
          },
        ],
        then: [{ service: "light.turn_on" }],
        else: [{ service: "light.turn_off" }],
      },
    ];
    expect(displayTriggers(triggers, [], actions)).toHaveLength(1);
  });

  it("keeps a referenced trigger when a sibling action runs unconditionally", () => {
    // at_11 is quoted by a choose branch, but the top-level service runs on
    // every firing (at 11:00). Hiding the only trigger would drop that
    // timing from the chart.
    const triggers = [{ trigger: "time", at: "11:00:00", id: "at_11" }];
    const actions = [
      { service: "light.turn_on" },
      {
        choose: [
          {
            conditions: [{ condition: "trigger", id: "at_11" }],
            sequence: [{ service: "light.turn_off" }],
          },
        ],
      },
    ];
    expect(displayTriggers(triggers, [], actions)).toHaveLength(1);
  });

  it("still hides a referenced trigger when every action is conditional", () => {
    const triggers = [{ trigger: "time", at: "11:00:00", id: "at_11" }];
    const actions = [
      {
        choose: [
          {
            conditions: [{ condition: "trigger", id: "at_11" }],
            sequence: [{ service: "light.turn_off" }],
          },
        ],
      },
    ];
    expect(displayTriggers(triggers, [], actions)).toEqual([]);
  });

  it("keeps a trigger whose entity only appears in an opaque template", () => {
    // The template renders as "Template evaluates to true", so the entity
    // is invisible — suppressing the trigger would leave nothing on screen.
    const triggers = [{ trigger: "state", entity_id: "sensor.temperature" }];
    const conditions = [
      {
        condition: "template",
        value_template:
          "{{ states('sensor.temperature') | float > 20 or " +
          "is_state('light.living_room', 'on') }}",
      },
    ];
    expect(displayTriggers(triggers, conditions, [])).toHaveLength(1);
  });

  it("still hides a trigger whose entity is in a concrete template", () => {
    const triggers = [{ trigger: "state", entity_id: "sensor.temperature" }];
    const conditions = [
      {
        condition: "template",
        value_template: "{{ states('sensor.temperature') | float > 20 }}",
      },
    ];
    expect(displayTriggers(triggers, conditions, [])).toEqual([]);
  });

  it("keeps a not_from/not_to-constrained state trigger visible", () => {
    const triggers = [
      {
        trigger: "state",
        entity_id: "sensor.temperature",
        not_from: "unavailable",
      },
    ];
    const actions = [
      {
        choose: [
          {
            conditions: [
              {
                condition: "numeric_state",
                entity_id: "sensor.temperature",
                above: 20,
              },
            ],
            sequence: [{ service: "light.turn_on" }],
          },
        ],
      },
    ];
    expect(displayTriggers(triggers, [], actions)).toHaveLength(1);
  });
});

describe("collectFlowDeviceRefs", () => {
  const hassWithDevices = {
    ...mockHass,
    devices: {
      abc123: { name: "ZB-LOCK-01", name_by_user: "Yale Lock" },
    },
  };

  it("resolves a device trigger's device_id to a linkable ref", () => {
    const refs = collectFlowDeviceRefs(hassWithDevices, {
      trigger: "device",
      device_id: "abc123",
      domain: "lock",
      type: "unlocked",
    });
    expect(refs).toEqual([
      { deviceId: "abc123", name: "Yale Lock", domain: "lock" },
    ]);
  });

  it("resolves device conditions too", () => {
    const refs = collectFlowDeviceRefs(hassWithDevices, {
      condition: "device",
      device_id: "abc123",
      type: "is_locked",
    });
    expect(refs).toEqual([
      { deviceId: "abc123", name: "Yale Lock", domain: null },
    ]);
  });

  it("returns nothing for non-device items", () => {
    expect(
      collectFlowDeviceRefs(hassWithDevices, {
        trigger: "state",
        entity_id: "light.living_room",
      }),
    ).toEqual([]);
  });

  it("returns nothing when the device is not in the registry", () => {
    expect(
      collectFlowDeviceRefs(mockHass, {
        trigger: "device",
        device_id: "missing",
        type: "unlocked",
      }),
    ).toEqual([]);
  });
});

describe("asArray", () => {
  it("wraps a single object (HA allows non-list conditions/sequence)", () => {
    const one = { condition: "state", entity_id: "light.x", state: "on" };
    expect(asArray(one)).toEqual([one]);
  });

  it("passes arrays through unchanged", () => {
    expect(asArray([1, 2])).toEqual([1, 2]);
  });

  it("maps null/undefined/false to empty array", () => {
    expect(asArray(null)).toEqual([]);
    expect(asArray(undefined)).toEqual([]);
    expect(asArray(false)).toEqual([]);
  });

  it("a choose branch with a single condition object no longer throws", () => {
    // Regression: (branch.conditions || []).map crashed when conditions was
    // a lone object. asArray normalizes so .map/.length are always safe.
    const branch = {
      conditions: { condition: "state", entity_id: "light.x", state: "on" },
    };
    expect(() => asArray(branch.conditions).map((c) => c)).not.toThrow();
    expect(asArray(branch.conditions)).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// Accuracy regressions: state filters, `for:`, negated trigger refs
// ---------------------------------------------------------------------------

describe("state filter accuracy", () => {
  it("renders a list `to:` with the locale's or-joiner, not a stringified array", () => {
    const out = describeFlowItem(mockHass, {
      platform: "state",
      entity_id: "device_tracker.phone",
      to: ["home", "not_home"],
    });
    // fmtState maps not_home -> "away", so the joined form is "home or away".
    expect(out).not.toContain("home,not home");
    expect(out).toContain("home");
    expect(out).toContain("away");
    expect(out).toMatch(/ or /);
  });

  it("describes not_to as an exclusion instead of a bare state change", () => {
    const out = describeFlowItem(mockHass, {
      platform: "state",
      entity_id: "light.living_room",
      not_to: "off",
    });
    // Pre-fix this fell through to "changes state" — indistinguishable from a
    // trigger with no filter at all.
    expect(out).not.toMatch(/changes state$/);
    expect(out).toContain("other than");
    expect(out).toContain("off");
  });

  it("describes not_from as an exclusion", () => {
    const out = describeFlowItem(mockHass, {
      platform: "state",
      entity_id: "light.living_room",
      not_from: "on",
    });
    expect(out).not.toMatch(/changes state$/);
    expect(out).toContain("other than");
  });

  it("keeps `for:` on a numeric_state trigger", () => {
    const out = describeFlowItem(mockHass, {
      platform: "numeric_state",
      entity_id: "sensor.temperature",
      above: 25,
      for: "00:10:00",
    });
    expect(out).toContain("25");
    expect(out).toContain("00:10:00");
  });

  it("keeps `for:` on a state condition", () => {
    const out = describeFlowItem(mockHass, {
      condition: "state",
      entity_id: "light.living_room",
      state: "off",
      for: { minutes: 10 },
    });
    expect(out).toContain("10m");
  });

  it("joins a list condition state with the or-joiner", () => {
    const out = describeFlowItem(mockHass, {
      condition: "state",
      entity_id: "device_tracker.phone",
      state: ["home", "not_home"],
    });
    expect(out).not.toContain("home,not home");
    expect(out).toContain("away");
    expect(out).toMatch(/ or /);
  });
});

describe("displayTriggers polarity", () => {
  const triggers = [
    { platform: "time", at: "07:00", id: "morning" },
    { platform: "state", entity_id: "binary_sensor.front_door", id: "motion" },
  ];

  it("does not suppress a trigger whose only reference sits under a `not`", () => {
    // "NOT triggered by morning" never tells the user the automation runs at
    // 07:00, so the trigger section must still list it.
    const shown = displayTriggers(
      triggers,
      [
        {
          condition: "not",
          conditions: [{ condition: "trigger", id: "morning" }],
        },
        { condition: "trigger", id: "motion" },
      ],
      [],
    );
    expect(shown.length).toBeGreaterThan(0);
    expect(shown.some((t) => t.id === "morning")).toBe(true);
  });

  it("still suppresses a positively-referenced trigger", () => {
    const shown = displayTriggers(
      triggers,
      [
        { condition: "trigger", id: "morning" },
        { condition: "trigger", id: "motion" },
      ],
      [],
    );
    expect(shown).toEqual([]);
  });
});

describe("mergeEquivalentTriggers and disabled triggers", () => {
  it("does not fold a disabled trigger into a shared pill", () => {
    const merged = mergeEquivalentTriggers([
      { platform: "state", entity_id: "sensor.temperature" },
      { platform: "state", entity_id: "cover.blinds", enabled: false },
    ]);
    expect(merged).toHaveLength(2);
    const active = merged.find((t) => t.enabled !== false);
    expect(asArray(active.entity_id)).toEqual(["sensor.temperature"]);
  });
});

// ---------------------------------------------------------------------------
// Combined source + destination constraints
// ---------------------------------------------------------------------------
// HA allows ONE source constraint (`from` xor `not_from`) alongside ONE
// destination constraint (`to` xor `not_to`). Describing a single side made a
// narrow trigger read as broad.

describe("state trigger source+destination combinations", () => {
  const d = (item) =>
    describeFlowItem(mockHass, {
      platform: "state",
      entity_id: "device_tracker.phone",
      ...item,
    });

  it("keeps the from-side when combined with not_to", () => {
    const out = d({ from: "home", not_to: "unavailable" });
    expect(out).toContain("home");
    expect(out).toContain("unavailable");
    expect(out).toMatch(/other than/);
  });

  it("keeps the not_from-side when combined with to", () => {
    const out = d({ not_from: "unavailable", to: "home" });
    expect(out).toContain("unavailable");
    expect(out).toContain("home");
    expect(out).toMatch(/other than/);
  });

  it("describes both sides when both are exclusions", () => {
    const out = d({ not_from: "unknown", not_to: "unavailable" });
    expect(out).toContain("unknown");
    expect(out).toContain("unavailable");
    expect(out.match(/other than/g)).toHaveLength(2);
  });

  it("describes a from-only trigger instead of a bare state change", () => {
    const out = d({ from: "home" });
    expect(out).toContain("home");
    expect(out).not.toMatch(/changes state$/);
  });

  it("still uses the idiomatic shortcut when the source is implied", () => {
    expect(d({ from: "off", to: "on" })).toBe("When Phone turns on");
  });

  it("spells out a non-implied source rather than saying just 'turns on'", () => {
    const out = d({ from: "unavailable", to: "on" });
    expect(out).toContain("unavailable");
    expect(out).not.toBe("When Phone turns on");
  });

  it("spells out not_from even when the destination is on", () => {
    const out = d({ not_from: "unavailable", to: "on" });
    expect(out).toContain("unavailable");
    expect(out).not.toBe("When Phone turns on");
  });
});

// ---------------------------------------------------------------------------
// List-valued `from` must not be mistaken for an unconstrained source
// ---------------------------------------------------------------------------

describe("list-valued state sources", () => {
  const d = (item) =>
    describeFlowItem(mockHass, {
      platform: "state",
      entity_id: "light.living_room",
      ...item,
    });

  it("spells out a multi-state from list instead of saying just 'turns on'", () => {
    const out = d({ from: ["off", "unavailable"], to: "on" });
    expect(out).not.toBe("When Living Room Light turns on");
    expect(out).toContain("off");
    expect(out).toContain("unavailable");
  });

  it("still uses the shortcut for a single-element list matching the complement", () => {
    expect(d({ from: ["off"], to: "on" })).toBe(
      "When Living Room Light turns on",
    );
    expect(d({ from: ["on"], to: "off" })).toBe(
      "When Living Room Light turns off",
    );
  });

  it("spells out a single-element list that is NOT the complement", () => {
    const out = d({ from: ["unavailable"], to: "on" });
    expect(out).not.toBe("When Living Room Light turns on");
    expect(out).toContain("unavailable");
  });
});

// ---------------------------------------------------------------------------
// Disabled steps must not participate in trigger-redundancy analysis
// ---------------------------------------------------------------------------

describe("displayTriggers and disabled steps", () => {
  it("does not let a DISABLED condition suppress an active trigger", () => {
    // The condition re-checks the trigger's entity, which would normally make the
    // bare trigger redundant — but it never runs, so the trigger is load-bearing.
    const shown = displayTriggers(
      [{ platform: "state", entity_id: "binary_sensor.front_door" }],
      [
        {
          condition: "state",
          entity_id: "binary_sensor.front_door",
          state: "on",
          enabled: false,
        },
      ],
      [],
    );
    expect(shown).toHaveLength(1);
  });

  it("does not let a DISABLED condition: trigger reference suppress a trigger", () => {
    const shown = displayTriggers(
      [{ platform: "time", at: "07:00", id: "morning" }],
      [{ condition: "trigger", id: "morning", enabled: false }],
      [],
    );
    expect(shown).toHaveLength(1);
  });

  it("keeps a disabled trigger visible instead of hiding the section", () => {
    // Both triggers look redundant against the condition, but the disabled one
    // cannot fire, so "every trigger is redundant" must not hide the section.
    const shown = displayTriggers(
      [
        { platform: "state", entity_id: "cover.blinds", enabled: false },
        { platform: "state", entity_id: "binary_sensor.front_door" },
      ],
      [
        { condition: "state", entity_id: "cover.blinds", state: "open" },
        {
          condition: "state",
          entity_id: "binary_sensor.front_door",
          state: "on",
        },
      ],
      [],
    );
    expect(shown.length).toBeGreaterThan(0);
  });

  it("ignores conditions inside a disabled action block", () => {
    const shown = displayTriggers(
      [{ platform: "state", entity_id: "binary_sensor.front_door" }],
      [],
      [
        {
          enabled: false,
          choose: [
            {
              conditions: [
                {
                  condition: "state",
                  entity_id: "binary_sensor.front_door",
                  state: "on",
                },
              ],
              sequence: [{ service: "light.turn_on" }],
            },
          ],
        },
      ],
    );
    expect(shown).toHaveLength(1);
  });
});
