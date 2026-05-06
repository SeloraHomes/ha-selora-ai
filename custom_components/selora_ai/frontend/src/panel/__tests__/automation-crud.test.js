import { describe, it, expect } from "vitest";
import { _createdToast } from "../automation-crud.js";

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
