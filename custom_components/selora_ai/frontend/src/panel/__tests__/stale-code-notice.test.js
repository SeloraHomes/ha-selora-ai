import { describe, it, expect } from "vitest";
import { renderStaleCodeNotice } from "../render-stale-code-notice.js";

// Same Lit-template flattener the flowchart tests use: walk
// { strings, values } results down to their rendered text so copy and
// classes can be asserted without a DOM.
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

const host = (status, dismissed = false) => ({
  _versionStatus: status,
  _staleCodeDismissed: dismissed,
  _t: (_key, fallback) => fallback,
});

describe("renderStaleCodeNotice", () => {
  it("renders nothing before the handshake answers", () => {
    expect(renderStaleCodeNotice(host(null))).toBe("");
  });

  it("renders nothing when loaded code and bundle are current", () => {
    const status = { restart_required: false, panel_reload_required: false };
    expect(renderStaleCodeNotice(host(status))).toBe("");
  });

  it("asks for a restart when the Python on disk is newer", () => {
    const status = { restart_required: true, panel_reload_required: false };
    const out = flatten(renderStaleCodeNotice(host(status)));
    expect(out).toContain("Restart to finish updating");
    expect(out).toContain("Restart Home Assistant to finish the update.");
    expect(out).toContain("stale-code-notice");
    // A reload would not re-import Python — never offer it for this state.
    expect(out).not.toContain("Reload to finish updating");
  });

  it("asks for a page reload when only the bundle is stale", () => {
    const status = { restart_required: false, panel_reload_required: true };
    const out = flatten(renderStaleCodeNotice(host(status)));
    expect(out).toContain("Reload to finish updating");
    expect(out).toContain("Reload");
  });

  it("prefers the restart message when both are stale", () => {
    const status = { restart_required: true, panel_reload_required: true };
    const out = flatten(renderStaleCodeNotice(host(status)));
    expect(out).toContain("Restart to finish updating");
    expect(out).not.toContain("Reload to finish updating");
  });

  it("stays hidden once dismissed", () => {
    const status = { restart_required: true, panel_reload_required: false };
    expect(renderStaleCodeNotice(host(status, true))).toBe("");
  });
});
