import { describe, it, expect } from "vitest";
import { renderAutomationFlowchart } from "../render-automations.js";

function flatten(node) {
  if (node == null || typeof node === "boolean") return "";
  if (Array.isArray(node)) return node.map(flatten).join("");
  if (typeof node === "object" && node.strings && "values" in node) {
    let out = "";
    node.strings.forEach((s, i) => {
      out += s;
      if (i < node.values.length) out += flatten(node.values[i]);
    });
    return out;
  }
  return String(node);
}

const name = (friendly_name) => ({ attributes: { friendly_name } });
const hass = {
  states: {
    "binary_sensor.front_door_visitor": name("Front Door Visitor"),
    "sensor.front_door_last_recognized_face": name(
      "Front Door Last Recognized Face",
    ),
    "sensor.front_camera_last_recognized_face": name(
      "Front Camera Last Recognized Face",
    ),
    "lock.yale_lock": name("Yale Lock"),
    "media_player.family_room": name("Family Room Speaker"),
  },
};
const host = { hass, _t: (_key, fallback) => fallback };

const say = (message) => ({
  action: "tts.speak",
  target: { entity_id: "tts.piper" },
  data: { media_player_entity_id: "media_player.family_room", message },
});

// A visitor announcement: a face trigger names the visitor, any other
// trigger announces "someone". Both template conditions used to render as
// "Template evaluates to true".
const announcement = {
  triggers: [
    {
      trigger: "state",
      entity_id: "binary_sensor.front_door_visitor",
      to: "on",
    },
    {
      trigger: "state",
      entity_id: "sensor.front_door_last_recognized_face",
      to: "Kira",
    },
    {
      trigger: "state",
      entity_id: "sensor.front_camera_last_recognized_face",
      to: "Kira",
    },
  ],
  conditions: [
    {
      condition: "template",
      value_template:
        "{{ trigger.from_state is not none and trigger.from_state.state != 'on' and states('lock.yale_lock') != 'unlocked' }}",
    },
  ],
  actions: [
    {
      choose: [
        {
          conditions: [
            {
              condition: "template",
              value_template:
                "{{ trigger.entity_id in ['sensor.front_door_last_recognized_face', 'sensor.front_camera_last_recognized_face'] }}",
            },
          ],
          sequence: [say("Kira is at the door")],
        },
      ],
      default: [say("Someone is at the door")],
    },
    { delay: "00:00:30" },
  ],
};

describe("template conditions in the flow chart", () => {
  const out = flatten(renderAutomationFlowchart(host, announcement));

  it("never falls back to the opaque template text", () => {
    expect(out).not.toContain("Template evaluates to true");
    expect(out).not.toContain("{{");
  });

  it("phrases a trigger.entity_id check as the triggers it names", () => {
    expect(out).toContain("Triggered by");
    expect(out).toContain("(sensor.front_door_last_recognized_face)");
    expect(out).toContain("(sensor.front_camera_last_recognized_face)");
  });

  it("draws each check of an `and` template as its own step", () => {
    expect(out).toContain("Was not on before the change");
    expect(out).toContain("is not unlocked");
    expect(out).toContain("(lock.yale_lock)");
    // The `is not none` guard says nothing about the home.
    expect(out).not.toContain("none");
  });

  it("shows the delay as a duration, after an arrow", () => {
    expect(out).toContain("30 sec");
    expect(out).not.toContain("00:00:30");
    expect(out).toMatch(/flow-arrow-sm[^]*Wait/);
  });

  it("keeps a sub-second delay whole in its chip", () => {
    const chart = flatten(
      renderAutomationFlowchart(host, {
        triggers: announcement.triggers,
        actions: [{ delay: 0.5 }],
      }),
    );
    expect(chart).toContain(">0.5 sec<");
  });
});
