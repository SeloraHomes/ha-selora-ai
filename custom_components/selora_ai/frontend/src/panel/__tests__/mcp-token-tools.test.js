import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { mcpToolLabel } from "../render-settings.js";

describe("custom-token tool picker", () => {
  it("labels a tool from its name", () => {
    expect(mcpToolLabel("selora_list_automations")).toBe("List automations");
    expect(mcpToolLabel("selora_add_dashboard_resource")).toBe(
      "Add dashboard resource",
    );
  });

  it("keeps no copy of the server's tool list", () => {
    // The picker once carried its own list, and every tool added to the MCP
    // server afterwards could not be granted to a custom token. The list is
    // the server's (`list_mcp_tokens` → `tools`); a tool entry written here
    // would be the start of a second copy.
    const source = readFileSync(
      new URL("../render-settings.js", import.meta.url),
      "utf8",
    );
    expect(source).not.toMatch(/name:\s*["']selora_[a-z_]+["']/);
    expect(source).not.toMatch(/MCP_TOOLS/);
  });
});
