import { describe, expect, it } from "vitest";

import {
  flowFieldControl,
  isAutoSetupRow,
  nextMatchFocus,
} from "../render-recipes.js";

const row = (id, status, extra = {}) => ({
  id,
  stage: "configure",
  kind: "integration",
  status,
  ...extra,
});
const role = (id, status, maxCount) =>
  row(id, status, {
    kind: "role_selection",
    payload: { role: { id, max_count: maxCount } },
  });

describe("nextMatchFocus", () => {
  it("opens the next row to set up once the open one is done", () => {
    const before = [row("tv", "needs_input"), row("wol", "needs_input")];
    const after = [row("tv", "ok"), row("wol", "needs_input")];
    expect(nextMatchFocus(before, after, "tv")).toBe("wol");
  });

  it("keeps a multi-device role open after its first pick", () => {
    const before = [
      role("tvs", "needs_input", null),
      row("wol", "needs_input"),
    ];
    const after = [role("tvs", "ok", null), row("wol", "needs_input")];
    expect(nextMatchFocus(before, after, "tvs")).toBe("tvs");
  });

  it("moves on from a single-device role once it is picked", () => {
    const before = [role("tv", "needs_input", 1), row("wol", "needs_input")];
    const after = [role("tv", "ok", 1), row("wol", "needs_input")];
    expect(nextMatchFocus(before, after, "tv")).toBe("wol");
  });

  it("stays put when the open row was already done or is still open", () => {
    const done = [row("tv", "ok"), row("wol", "needs_input")];
    expect(nextMatchFocus(done, done, "tv")).toBe("tv");
    const open = [row("tv", "needs_input"), row("wol", "needs_input")];
    expect(nextMatchFocus(open, open, "tv")).toBe("tv");
  });

  it("stays on the finished row when nothing else needs setup", () => {
    const before = [row("tv", "needs_input"), row("wol", "ok")];
    const after = [row("tv", "ok"), row("wol", "ok")];
    expect(nextMatchFocus(before, after, "tv")).toBe("tv");
  });
});

describe("automatic setup rows", () => {
  const wol = (status) =>
    row("wol", status, { payload: { domain: "wake_on_lan", deferred: true } });

  it("counts only a deferred integration still to set up", () => {
    expect(isAutoSetupRow(wol("needs_input"))).toBe(true);
    expect(isAutoSetupRow(wol("ok"))).toBe(false);
    expect(
      isAutoSetupRow(
        row("tv", "needs_input", { payload: { domain: "samsungtv" } }),
      ),
    ).toBe(false);
  });

  it("is not where the picker sends the homeowner next", () => {
    const before = [role("tv", "needs_input", 1), wol("needs_input")];
    const after = [role("tv", "ok", 1), wol("needs_input")];
    expect(nextMatchFocus(before, after, "tv")).toBe("tv");
  });
});

describe("flowFieldControl", () => {
  it("renders a text selector as a text box, not an empty dropdown", () => {
    expect(flowFieldControl({ name: "host", selector: { text: {} } })).toEqual({
      type: "string",
      options: [],
    });
  });

  it("reads a select selector's options", () => {
    const control = flowFieldControl({
      name: "mode",
      selector: { select: { options: [{ value: "a", label: "A" }, "b"] } },
    });
    expect(control.type).toBe("select");
    expect(control.options).toEqual([
      { value: "a", label: "A" },
      { value: "b", label: "b" },
    ]);
  });

  it("reads vol.In options as value/label pairs", () => {
    expect(
      flowFieldControl({ name: "x", type: "select", options: [["1", "One"]] })
        .options,
    ).toEqual([{ value: "1", label: "One" }]);
  });

  it("maps boolean and number selectors and float fields", () => {
    expect(flowFieldControl({ selector: { boolean: {} } }).type).toBe(
      "boolean",
    );
    expect(flowFieldControl({ selector: { number: {} } }).type).toBe("number");
    expect(flowFieldControl({ type: "float" }).type).toBe("number");
  });
});
