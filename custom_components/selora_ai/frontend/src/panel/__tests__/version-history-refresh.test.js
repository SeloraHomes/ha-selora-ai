import { describe, it, expect, vi } from "vitest";
import { _refreshStaleVersionHistories } from "../automation-management.js";

function host({ cached, current, open = false }) {
  return {
    _automations: [
      {
        automation_id: "a1",
        entity_id: "automation.a1",
        current_version_id: current,
      },
    ],
    _versions: { a1: cached },
    _cardActiveTab: open ? { "automation.a1": "history" } : {},
    _versionHistoryOpen: {},
    _loadVersionHistory: vi.fn(),
  };
}

const V2_FIRST = [{ version_id: "v2" }, { version_id: "v1" }];

describe("_refreshStaleVersionHistories", () => {
  it("refetches an open history once a newer version exists", () => {
    const h = host({ cached: V2_FIRST, current: "v3", open: true });
    _refreshStaleVersionHistories.call(h);
    expect(h._loadVersionHistory).toHaveBeenCalledWith("a1");
  });

  it("drops a closed one so the next open fetches it", () => {
    const h = host({ cached: V2_FIRST, current: "v3" });
    _refreshStaleVersionHistories.call(h);
    expect(h._versions.a1).toBeNull();
    expect(h._loadVersionHistory).not.toHaveBeenCalled();
  });

  it("leaves an up-to-date history alone", () => {
    const h = host({ cached: V2_FIRST, current: "v2", open: true });
    const before = h._versions;
    _refreshStaleVersionHistories.call(h);
    expect(h._versions).toBe(before);
    expect(h._loadVersionHistory).not.toHaveBeenCalled();
  });
});
