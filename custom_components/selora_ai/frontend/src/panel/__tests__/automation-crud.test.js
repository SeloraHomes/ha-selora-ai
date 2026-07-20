import { describe, it, expect, vi } from "vitest";
import {
  _createdToast,
  _acceptAutomation,
  _acceptAutomationWithEdits,
  _extractInitialState,
  _initialStateEdited,
} from "../automation-crud.js";

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
    // Must communicate the disabled state and that the user can enable it.
    expect(t.message).toMatch(/disabled/i);
    expect(t.message).toMatch(/enable/i);
    // Must NEVER claim the automation is already enabled / running.
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

describe("_extractInitialState / _initialStateEdited", () => {
  it("extracts a top-level initial_state, normalized", () => {
    expect(_extractInitialState("alias: X\ninitial_state: false\n")).toBe(
      "false",
    );
    expect(_extractInitialState('initial_state: "true"\n')).toBe("true");
    expect(_extractInitialState("alias: X\n")).toBeUndefined();
  });

  it("ignores an indented (non-top-level) initial_state", () => {
    expect(
      _extractInitialState("triggers:\n  - initial_state: true\n"),
    ).toBeUndefined();
  });

  it("recognizes quoted top-level keys", () => {
    expect(_extractInitialState("'initial_state': true\n")).toBe("true");
    expect(_extractInitialState('"initial_state": false\n')).toBe("false");
    // detects a flip when the key is quoted
    expect(
      _initialStateEdited(
        "'initial_state': true\n",
        "'initial_state': false\n",
      ),
    ).toBe(true);
  });

  it("ignores inline comments on the value", () => {
    expect(
      _extractInitialState("initial_state: false # start disabled\n"),
    ).toBe("false");
    expect(_extractInitialState('initial_state: "true"  # note\n')).toBe(
      "true",
    );
    // a comment-only change is NOT a state edit
    expect(
      _initialStateEdited(
        "initial_state: false # start disabled\n",
        "initial_state: false # keep it off for now\n",
      ),
    ).toBe(false);
    // but flipping the value alongside a comment still counts
    expect(
      _initialStateEdited(
        "initial_state: false # x\n",
        "initial_state: true # x\n",
      ),
    ).toBe(true);
  });

  it("extracts from an indented root block mapping", () => {
    expect(_extractInitialState("  alias: X\n  initial_state: false\n")).toBe(
      "false",
    );
    // a nested (deeper) initial_state must not be mistaken for the top-level one
    expect(
      _extractInitialState(
        "  alias: X\n  triggers:\n    - initial_state: true\n",
      ),
    ).toBeUndefined();
    // detects a flip in indented form
    expect(
      _initialStateEdited(
        "  alias: X\n  initial_state: false\n",
        "  alias: X\n  initial_state: true\n",
      ),
    ).toBe(true);
  });

  it("extracts from a flow mapping", () => {
    expect(_extractInitialState("{alias: X, initial_state: false}")).toBe(
      "false",
    );
    expect(
      _extractInitialState('{ alias: X, "initial_state": true, mode: single }'),
    ).toBe("true");
    expect(_extractInitialState("{alias: X, mode: single}")).toBeUndefined();
    // detects a flip in flow form
    expect(
      _initialStateEdited(
        "{alias: X, initial_state: false}",
        "{alias: X, initial_state: true}",
      ),
    ).toBe(true);
  });

  it("accepts whitespace before the key separator", () => {
    // YAML permits `initial_state : false` (space/tab before the colon).
    expect(_extractInitialState("initial_state : false\n")).toBe("false");
    expect(_extractInitialState("initial_state\t: true\n")).toBe("true");
    expect(_extractInitialState("'initial_state' : false\n")).toBe("false");
    expect(_extractInitialState("{alias: X, initial_state : true}")).toBe(
      "true",
    );
    // a flip with spaced separators is detected as a change
    expect(
      _initialStateEdited("initial_state : true\n", "initial_state : false\n"),
    ).toBe(true);
  });

  it("reads a continuation-line (multiline) block scalar", () => {
    expect(_extractInitialState("alias: X\ninitial_state:\n  false\n")).toBe(
      "false",
    );
    // flipping the indented scalar is detected as a change
    expect(
      _initialStateEdited(
        "alias: X\ninitial_state:\n  true\n",
        "alias: X\ninitial_state:\n  false\n",
      ),
    ).toBe(true);
    // continuation value with an inline comment still normalizes
    expect(
      _extractInitialState("initial_state:\n  false # off at boot\n"),
    ).toBe("false");
  });

  it("handles escaped quotes in flow-style scalars", () => {
    // Double-quoted scalar with an escaped quote AND a comma inside it: the comma
    // must not split the mapping, so initial_state is still found.
    expect(
      _extractInitialState(`{alias: "a \\" , b", initial_state: true}`),
    ).toBe("true");
    expect(
      _initialStateEdited(
        `{alias: "a \\" , b", initial_state: true}`,
        `{alias: "a \\" , b", initial_state: false}`,
      ),
    ).toBe(true);
    // Single-quoted scalar with a doubled '' escape and a comma inside.
    expect(
      _extractInitialState("{alias: 'it''s, ok', initial_state: false}"),
    ).toBe("false");
  });

  it("ignores a nested initial_state in a flow mapping", () => {
    // The nested one (inside `variables`) must not shadow the top-level key.
    expect(
      _extractInitialState(
        "{variables: {initial_state: false}, initial_state: true}",
      ),
    ).toBe("true");
    // editing only the real top-level value is detected as a change
    expect(
      _initialStateEdited(
        "{variables: {initial_state: false}, initial_state: true}",
        "{variables: {initial_state: false}, initial_state: false}",
      ),
    ).toBe(true);
    // a top-level key present only via nesting is not found
    expect(
      _extractInitialState("{variables: {initial_state: false}, alias: X}"),
    ).toBeUndefined();
  });

  it("detects add/remove/flip of initial_state, ignores unrelated edits", () => {
    // unrelated edit (action changed), initial_state untouched → not edited
    expect(
      _initialStateEdited(
        "alias: X\ninitial_state: true\naction: a\n",
        "alias: X\ninitial_state: true\naction: b\n",
      ),
    ).toBe(false);
    // flipped
    expect(
      _initialStateEdited("initial_state: true\n", "initial_state: false\n"),
    ).toBe(true);
    // removed
    expect(_initialStateEdited("initial_state: true\n", "alias: X\n")).toBe(
      true,
    );
    // added
    expect(_initialStateEdited("alias: X\n", "initial_state: false\n")).toBe(
      true,
    );
  });
});

describe("_acceptAutomationWithEdits (edited refinement)", () => {
  function makeHost(overrides) {
    const callWS = vi.fn(async ({ type }) => {
      if (type === "selora_ai/get_session") return { messages: [] };
      return {};
    });
    const host = {
      hass: { callWS },
      _acceptAnimating: {},
      _savingYaml: {},
      _activeSessionId: "s1",
      _getRefiningAutomationId: () => "auto_1",
      _loadAutomations: vi.fn().mockResolvedValue(undefined),
      _autoEnableAfterAccept: vi.fn().mockResolvedValue(undefined),
      _showToast: vi.fn(),
      _t: (_key, fallback) => fallback,
      requestUpdate: vi.fn(),
      ...overrides,
    };
    return { host, callWS };
  }

  it("opts out of preservation when the user edited initial_state", async () => {
    const original = "alias: X\ninitial_state: true\n";
    const { host, callWS } = makeHost({
      _editedYaml: { k0: "alias: X\ninitial_state: false\n" },
      _originalYaml: { k0: original },
      _messages: [{ automation_yaml: original, automation_message_index: 0 }],
    });

    await _acceptAutomationWithEdits.call(host, 0, {}, "k0");

    const update = callWS.mock.calls.find(
      ([arg]) => arg?.type === "selora_ai/update_automation_yaml",
    );
    expect(update).toBeDefined();
    expect(update[0].preserve_enabled_state).toBe(false);
  });

  it("requests preservation when only an unrelated field was edited", async () => {
    const original = "alias: X\ninitial_state: true\naction: a\n";
    const { host, callWS } = makeHost({
      _editedYaml: { k0: "alias: X\ninitial_state: true\naction: b\n" },
      _originalYaml: { k0: original },
      _messages: [{ automation_yaml: original, automation_message_index: 0 }],
    });

    await _acceptAutomationWithEdits.call(host, 0, {}, "k0");

    const update = callWS.mock.calls.find(
      ([arg]) => arg?.type === "selora_ai/update_automation_yaml",
    );
    expect(update).toBeDefined();
    // The endpoint defaults to authoritative, so an unedited state must be
    // explicitly preserved.
    expect(update[0].preserve_enabled_state).toBe(true);
  });
});

describe("_acceptAutomation (generated refinement)", () => {
  it("explicitly requests preservation on the refine update", async () => {
    const callWS = vi.fn(async ({ type }) => {
      if (type === "selora_ai/get_session") return { messages: [] };
      return {};
    });
    const host = {
      hass: { callWS },
      _activeSessionId: "s1",
      _messages: [
        {
          automation_yaml: "alias: X\ninitial_state: true\n",
          automation_message_index: 0,
          refining_automation_id: "auto_1",
        },
      ],
      _getRefiningAutomationId: function (i) {
        return this._messages[i]?.refining_automation_id || null;
      },
      _removeDraftForSession: vi.fn().mockResolvedValue(undefined),
      _loadAutomations: vi.fn().mockResolvedValue(undefined),
      _autoEnableAfterAccept: vi.fn().mockResolvedValue(undefined),
      _showToast: vi.fn(),
      _t: (_key, fallback) => fallback,
    };

    await _acceptAutomation.call(host, 0, {});

    const update = callWS.mock.calls.find(
      ([arg]) => arg?.type === "selora_ai/update_automation_yaml",
    );
    expect(update).toBeDefined();
    expect(update[0].preserve_enabled_state).toBe(true);
  });
});
