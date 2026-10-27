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
    expect(out).toContain("#10b981");
    expect(out).not.toContain("#ef4444");
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
    expect(out).not.toContain("#ef4444");
    expect(out).toContain("#10b981");
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
