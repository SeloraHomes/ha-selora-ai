import { describe, it, expect, vi } from "vitest";
import {
  activeRefinement,
  prefillComposer,
  refineSuggestions,
  renderRefineHeading,
  stepPrefill,
} from "../refine-guide.js";
import { renderProposalCard } from "../render-automations.js";

const host = { _t: (key, fallback) => fallback, hass: { states: {} } };

const loaded = (automation = {}) => ({
  role: "assistant",
  content: "Describe the changes",
  automation_status: "refining",
  automation_id: "auto_1",
  automation: { alias: "Game Area Lights", ...automation },
  automation_yaml: "alias: Game Area Lights\n",
});

// Serialize a lit template to text, enough to assert on classes and copy.
function ser(value) {
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
  return "";
}

describe("activeRefinement", () => {
  it("is the loaded automation while nothing has answered it", () => {
    const messages = [loaded(), { role: "user", content: "which sensor?" }];
    expect(activeRefinement(messages)).toEqual({ index: 0 });
  });

  it.each(["pending", "saved", "declined"])(
    "ends at a newer %s proposal, which now carries the target",
    (status) => {
      const messages = [loaded(), { automation_status: status }];
      expect(activeRefinement(messages)).toBeNull();
    },
  );

  it("is null without a refinement", () => {
    expect(activeRefinement([])).toBeNull();
    expect(activeRefinement(undefined)).toBeNull();
  });
});

describe("refineSuggestions", () => {
  const labels = (automation) =>
    refineSuggestions(host, automation).map((s) => s.label);

  it("quotes the values the automation already uses", () => {
    const automation = {
      triggers: [
        {
          trigger: "numeric_state",
          entity_id: "sensor.lux",
          below: 15,
        },
        {
          trigger: "state",
          entity_id: "binary_sensor.motion",
          to: "off",
          for: "00:05:00",
        },
      ],
      actions: [
        {
          action: "light.turn_on",
          target: { entity_id: "light.game" },
          data: { brightness_pct: 50 },
        },
      ],
    };
    expect(labels(automation)).toEqual([
      "Change the threshold (15)",
      "Change the delay (00:05:00)",
      "Change the brightness (50%)",
      "Only between certain hours",
    ]);
  });

  it("finds values nested in choose / if blocks", () => {
    const automation = {
      triggers: [{ trigger: "time", at: "07:00:00" }],
      actions: [
        {
          choose: [
            {
              conditions: [
                {
                  condition: "numeric_state",
                  entity_id: "sensor.t",
                  above: "20",
                },
              ],
              sequence: [{ delay: { minutes: 10 } }],
            },
          ],
        },
      ],
    };
    expect(labels(automation)).toEqual([
      "Change the threshold (20)",
      "Change the delay (10m)",
      "Change the time (07:00)",
      "Also notify me",
    ]);
  });

  it("offers additions only when the automation lacks them", () => {
    const automation = {
      triggers: [{ trigger: "state", entity_id: "binary_sensor.door" }],
      conditions: [{ condition: "time", after: "22:00:00" }],
      actions: [{ action: "notify.mobile_app_phone", data: { message: "x" } }],
    };
    expect(labels(automation)).toEqual([]);
  });

  it("ignores disabled steps and everything under them", () => {
    const automation = {
      triggers: [
        {
          trigger: "numeric_state",
          entity_id: "s.x",
          below: 15,
          enabled: false,
        },
        { trigger: "state", entity_id: "binary_sensor.door" },
      ],
      conditions: [{ condition: "time", after: "22:00:00", enabled: false }],
      actions: [
        { action: "notify.notify", enabled: false },
        {
          enabled: false,
          if: [{ condition: "state", entity_id: "a.b", state: "on" }],
          then: [{ delay: { minutes: 5 } }],
        },
      ],
    };
    // No threshold or delay that never runs; the disabled time condition and
    // notification don't count as present.
    expect(labels(automation)).toEqual([
      "Only between certain hours",
      "Also notify me",
    ]);
  });

  it("never quotes a template or an entity reference", () => {
    const automation = {
      triggers: [
        { trigger: "time", at: "input_datetime.wake" },
        { trigger: "state", entity_id: "a.b", for: "{{ states('x') }}" },
      ],
      actions: [{ action: "notify.notify" }],
    };
    // The time trigger still counts as a time window, so nothing is offered.
    expect(labels(automation)).toEqual([]);
  });

  it("prefills an unfinished sentence for the user to complete", () => {
    const [threshold] = refineSuggestions(host, {
      triggers: [{ trigger: "numeric_state", entity_id: "s.x", below: 15 }],
    });
    expect(threshold.prefill).toBe("Change the threshold from 15 to");
  });
});

describe("prefillComposer", () => {
  it("fills, focuses and puts the caret at the end, without sending", async () => {
    const ta = { value: "", focus: vi.fn(), setSelectionRange: vi.fn() };
    const h = {
      _input: "",
      requestUpdate: vi.fn(),
      updateComplete: Promise.resolve(),
      shadowRoot: { querySelector: () => ta },
      _sendMessage: vi.fn(),
    };
    await prefillComposer(h, stepPrefill(host, "Outside Temperature below 15"));
    expect(h._input).toBe('Change "Outside Temperature below 15" to ');
    ta.value = h._input;
    expect(ta.focus).toHaveBeenCalled();
    expect(h._sendMessage).not.toHaveBeenCalled();
  });
});

describe("the loaded automation card", () => {
  const automation = {
    triggers: [{ trigger: "state", entity_id: "binary_sensor.motion" }],
    actions: [{ action: "light.turn_on" }],
  };

  it("makes steps editable while the edit is waiting", () => {
    const msg = loaded(automation);
    const out = ser(renderProposalCard({ ...host, _messages: [msg] }, msg, 0));
    expect(out).toContain("flow-node--editable");
    expect(out).toContain("Current version");
  });

  it("goes back to a plain card once a proposal answered it", () => {
    const msg = loaded(automation);
    const messages = [msg, { automation_status: "pending" }];
    const out = ser(
      renderProposalCard({ ...host, _messages: messages }, msg, 0),
    );
    expect(out).not.toContain("flow-node--editable");
  });
});

describe("renderRefineHeading", () => {
  it("names the automation and how to edit it, above its message", () => {
    const h = { ...host, _messages: [loaded()] };
    const out = ser(renderRefineHeading(h, 0));
    expect(out).toContain("Editing Game Area Lights");
    expect(out).toContain("click any step");
    expect(out).toContain("Nothing changes until you accept the new version.");
  });

  it("is gone once a proposal answered the edit", () => {
    const h = {
      ...host,
      _messages: [loaded(), { automation_status: "pending" }],
    };
    expect(ser(renderRefineHeading(h, 0))).toBe("");
  });

  it("only heads the message the refinement points at", () => {
    const older = { ...loaded(), automation_status: "saved" };
    const h = { ...host, _messages: [older, loaded()] };
    expect(ser(renderRefineHeading(h, 0))).toBe("");
    expect(ser(renderRefineHeading(h, 1))).toContain("Editing");
  });
});
