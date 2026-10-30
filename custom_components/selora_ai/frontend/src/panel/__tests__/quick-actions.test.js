import { describe, expect, it } from "vitest";

import { renderQuickActions } from "../quick-actions.js";

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
  if (typeof node === "function") return "";
  return String(node);
}

const host = { _t: (_key, fallback) => fallback };
const cancel = { label: "Cancel", value: "approve:cancel:p1", mode: "choice" };

describe("approval buttons", () => {
  it("say Apply in green for the apply scope, with no override", () => {
    const out = flatten(
      renderQuickActions(host, [
        { label: "Apply", value: "approve:apply:p1", mode: "choice" },
        cancel,
      ]),
    );

    expect(out).toContain("Apply");
    expect(out).not.toContain("Delete");
    expect(out).toContain("action-btn--primary");
    expect(out).not.toContain("action-btn--danger");
  });

  it("say Apply for a change that deletes nothing", () => {
    // Same scope as a delete (one confirm path), its own wording.
    const out = flatten(
      renderQuickActions(host, [
        {
          label: "Apply",
          presentation: "apply",
          value: "approve:delete:p1",
          mode: "choice",
        },
        cancel,
      ]),
    );

    expect(out).toContain("Apply");
    expect(out).not.toContain("Delete");
    // Not the delete red: nothing is being deleted.
    expect(out).not.toContain("action-btn--danger");
    expect(out).toContain("action-btn--primary");
  });

  it("keep Apply red when the change also deletes", () => {
    const out = flatten(
      renderQuickActions(host, [
        {
          label: "Apply",
          presentation: "apply",
          value: "approve:delete:p1",
          mode: "choice",
          tone: "deny",
        },
        cancel,
      ]),
    );

    expect(out).toContain("Apply");
    expect(out).toContain("action-btn--danger");
    expect(out).not.toContain("action-btn--primary");
  });

  it("still say Delete for a deletion", () => {
    const out = flatten(
      renderQuickActions(host, [
        { label: "Delete", value: "approve:delete:p1", mode: "choice" },
        cancel,
      ]),
    );

    expect(out).toContain("Delete");
  });
});

describe("every quick action is the one action button", () => {
  const count = (out, cls) => out.split(`${cls}`).length - 1;

  it("lights only the first approval gold, and declines in grey", () => {
    const out = flatten(
      renderQuickActions(host, [
        { label: "Allow once", value: "approve:once:p1", mode: "choice" },
        { label: "Session", value: "approve:session:p1", mode: "choice" },
        { label: "Always", value: "approve:always:p1", mode: "choice" },
        { label: "Deny", value: "approve:deny:p1", mode: "choice" },
      ]),
    );

    expect(count(out, "action-btn--primary")).toBe(1);
    expect(out).toMatch(/action-btn--primary[^]*Allow once/);
    // Declining deletes nothing: grey, its cross in red.
    expect(out).not.toContain("action-btn--danger");
    expect(out).toMatch(/action-btn--decline[^]*action-btn-cross/);
  });

  it("reddens a press that deletes", () => {
    const out = flatten(
      renderQuickActions(host, [
        { label: "Delete", value: "approve:delete:p1", mode: "choice" },
        cancel,
      ]),
    );

    expect(out).toContain("action-btn--danger");
    expect(out).not.toContain("action-btn--primary");
  });

  it("sends a suggestion with the arrow, none of them gold", () => {
    const out = flatten(
      renderQuickActions(host, [
        { label: "Turn off the lights", value: "Turn off the lights" },
        { label: "Lock the door", value: "Lock the door" },
      ]),
    );

    expect(count(out, "mdi:arrow-up")).toBe(2);
    expect(out).not.toContain("action-btn--primary");
    expect(out).not.toContain("qa-group--answers");
  });
});
