import { describe, it, expect } from "vitest";
import { callTargetEntityIds, describeCall } from "../action-format.js";

// callTargetEntityIds returns the user-visible target of a service call. For
// tts.speak that is the SPEAKER (data.media_player_entity_id), not the TTS
// engine in target.entity_id — the engine is an implementation detail the
// approval card / tile / scope chip must never surface. Mirrors the backend
// command_policy.approval_entity_ids.

describe("callTargetEntityIds", () => {
  it("returns the speaker for tts.speak, not the engine", () => {
    const call = {
      service: "tts.speak",
      target: { entity_id: "tts.google_translate_en_com" },
      data: { media_player_entity_id: "media_player.kitchen", message: "hi" },
    };
    expect(callTargetEntityIds(call)).toEqual(["media_player.kitchen"]);
  });

  it("supports a list of speakers for tts.speak", () => {
    const call = {
      service: "tts.speak",
      target: { entity_id: "tts.piper" },
      data: {
        media_player_entity_id: ["media_player.kitchen", "media_player.den"],
        message: "hi",
      },
    };
    expect(callTargetEntityIds(call)).toEqual([
      "media_player.kitchen",
      "media_player.den",
    ]);
  });

  it("returns no ids for a tts.speak missing a speaker (never the engine)", () => {
    const call = {
      service: "tts.speak",
      target: { entity_id: "tts.piper" },
      data: { message: "hi" },
    };
    expect(callTargetEntityIds(call)).toEqual([]);
  });

  it("reads target.entity_id for non-tts services", () => {
    const lock = {
      service: "lock.unlock",
      target: { entity_id: "lock.front" },
    };
    expect(callTargetEntityIds(lock)).toEqual(["lock.front"]);
    const lights = {
      service: "light.turn_on",
      target: { entity_id: ["light.a", "light.b"] },
    };
    expect(callTargetEntityIds(lights)).toEqual(["light.a", "light.b"]);
  });
});

describe("describeCall for tts.speak", () => {
  it("describes the speaker as the target, not the engine", () => {
    const host = {
      hass: {
        states: {
          "media_player.kitchen": { attributes: { friendly_name: "Kitchen" } },
        },
      },
    };
    const call = {
      service: "tts.speak",
      target: { entity_id: "tts.google_translate_en_com" },
      data: { media_player_entity_id: "media_player.kitchen", message: "hi" },
    };
    const { targetText, entityIds } = describeCall(host, call);
    expect(entityIds).toEqual(["media_player.kitchen"]);
    expect(targetText).toBe("Kitchen");
  });
});
