import { describe, it, expect } from "vitest";
import { renderFaceLibraryLink } from "../face-library-link.js";

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

const FRIGATE_APP = { frigate: { config: { addon: "ccab4aaf_frigate" } } };

const host = (panels = FRIGATE_APP) => ({
  hass: { panels },
  _t: (_key, fallback) => fallback,
});

const reply = (result) => ({
  tool_calls: [{ tool: "get_face_recognition", result }],
});

describe("renderFaceLibraryLink", () => {
  it("opens Frigate under a reply where it is on", () => {
    const out = flatten(
      renderFaceLibraryLink(host(), reply({ face_library: true })),
    );
    expect(out).toContain("Open in Frigate");
  });

  it("offers nothing while face recognition is off", () => {
    expect(
      renderFaceLibraryLink(host(), reply({ face_recognition: "off" })),
    ).toBe("");
  });

  it("offers nothing without an app serving Frigate's UI", () => {
    expect(renderFaceLibraryLink(host({}), reply({ face_library: true }))).toBe(
      "",
    );
  });

  it("offers nothing under a reply that never asked", () => {
    expect(renderFaceLibraryLink(host(), { tool_calls: [] })).toBe("");
  });
});
