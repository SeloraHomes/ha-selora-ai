import { describe, it, expect } from "vitest";
import { _createdToast, _getRefiningAutomationId } from "../automation-crud.js";

// _createdToast translates the WS reply from selora_ai/create_automation
// (or selora_ai/apply_automation_yaml) into a toast. Per project policy
// every chat-driven automation lands DISABLED, so the toast must never
// claim it is enabled. Elevated-risk payloads upgrade to a warning toast
// so the user reviews carefully before flipping the switch.

describe("_createdToast", () => {
  it("returns info toast for a normal create result", () => {
    const t = _createdToast("Porch lights", { risk_level: "normal" });
    expect(t.type).toBe("info");
    expect(t.message).toContain("Porch lights");
    expect(t.message).toMatch(/review and enable/i);
    // Must NEVER claim the automation is enabled.
    expect(t.message).not.toMatch(/enabled\b(?!.*review)/i);
    expect(t.message).not.toContain("created and enabled");
  });

  it("returns info toast when result is null/undefined", () => {
    expect(_createdToast("X", null).type).toBe("info");
    expect(_createdToast("X", undefined).type).toBe("info");
    expect(_createdToast("X", null).message).not.toContain(
      "created and enabled",
    );
  });

  it("returns warning toast when risk_level is elevated", () => {
    const t = _createdToast("Risky webhook", { risk_level: "elevated" });
    expect(t.type).toBe("warning");
    expect(t.message).toContain("Risky webhook");
    expect(t.message).toContain("DISABLED");
    expect(t.message).toMatch(/elevated.risk/i);
    expect(t.message).toMatch(/review/i);
    expect(t.message).not.toContain("created and enabled");
  });

  it("does not claim enabled even on elevated risk", () => {
    const t = _createdToast("X", { risk_level: "elevated" });
    expect(t.message).not.toMatch(/created and enabled/i);
  });
});

// _getRefiningAutomationId decides whether Accept & Save calls the
// create-automation WS or the update-automation WS. It must only return
// an id that refers to an automation already persisted in automations.yaml;
// the proposed automation's `id` field (chosen by the LLM) is unreliable
// and must NOT be used, otherwise fresh proposals 404 with
// "Automation not found in automations.yaml".

describe("_getRefiningAutomationId", () => {
  function call(messages, msgIndex) {
    return _getRefiningAutomationId.call({ _messages: messages }, msgIndex);
  }

  it("returns the backend-supplied refining id when present", () => {
    const messages = [
      { role: "assistant", refining_automation_id: "selora_ai_abc123" },
    ];
    expect(call(messages, 0)).toBe("selora_ai_abc123");
  });

  it("returns msg.automation_id when set by the persist flow", () => {
    const messages = [{ role: "assistant", automation_id: "selora_ai_def456" }];
    expect(call(messages, 0)).toBe("selora_ai_def456");
  });

  it("returns null for a fresh proposal even if the LLM put an id in the YAML", () => {
    // Regression: msg.automation.id is the LLM's proposed id and does
    // not exist on disk. Treating it as a refine target makes Accept
    // & Save 404 ("Automation not found in automations.yaml").
    const messages = [
      {
        role: "assistant",
        automation: {
          id: "dishwasher_done_announce",
          alias: "Dishwasher done",
        },
        automation_yaml:
          "id: dishwasher_done_announce\nalias: Dishwasher done\n",
      },
    ];
    expect(call(messages, 0)).toBeNull();
  });

  it("finds a refining sibling only via persisted automation_id", () => {
    const messages = [
      {
        role: "assistant",
        automation_status: "refining",
        automation_id: "selora_ai_xyz789",
      },
      { role: "user", content: "tweak it" },
      { role: "assistant", automation: { id: "llm_chosen_id" } },
    ];
    expect(call(messages, 2)).toBe("selora_ai_xyz789");
  });

  it("ignores a refining sibling that only has a proposal id", () => {
    const messages = [
      {
        role: "assistant",
        automation_status: "refining",
        automation: { id: "llm_chosen_id" },
      },
      { role: "assistant", automation: { id: "another_llm_id" } },
    ];
    expect(call(messages, 1)).toBeNull();
  });

  it("returns null when no msgIndex and no refining siblings exist", () => {
    expect(call([{ role: "assistant" }], null)).toBeNull();
  });
});
