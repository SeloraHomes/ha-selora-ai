import { describe, it, expect } from "vitest";
import { describeFlowItem } from "../flow-description.js";

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
    expect(result).toContain("8:00 AM");
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

  it("describes sun trigger with offset", () => {
    const result = describeFlowItem(mockHass, {
      platform: "sun",
      event: "sunrise",
      offset: "-00:30:00",
    });
    expect(result).toContain("sunrise");
    expect(result).toContain("-00:30:00");
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
});

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------
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
