import { describe, expect, it, vi } from "vitest";

import { _runRecipeUpdate } from "../recipe-update-actions.js";

// A host with just what an update touches; the stream answers at once.
const hostWith = (result) => {
  const host = {
    _recipesList: { updates: { pool: "2.1.0" } },
    _recipesCatalog: { updates: { pool: "2.1.0" } },
    _recipesBusy: false,
    _t: (_key, fallback) => fallback,
    _recipeUpdateVersion() {
      return (
        this._recipesList?.updates?.pool || this._recipesCatalog?.updates?.pool
      );
    },
    _loadRecipesList: vi.fn(async () => {
      host._recipesList = { updates: {} };
    }),
    _loadRecipesCatalog: vi.fn(async (force) => {
      if (force) host._recipesCatalog = { updates: {} };
    }),
    hass: {
      connection: {
        subscribeMessage: (callback) => {
          callback({ event: { type: "result", result } });
          return Promise.resolve(() => {});
        },
      },
    },
  };
  return host;
};

describe("_runRecipeUpdate", () => {
  it("stops offering the update once it is installed", async () => {
    const host = hostWith({ ok: true });

    await _runRecipeUpdate.call(host, "pool");

    expect(host._loadRecipesCatalog).toHaveBeenCalledWith(true);
    expect(host._recipeUpdateVersion()).toBeUndefined();
    expect(host._recipeUpdateNotice.message).toBe("Updated to v2.1.0.");
  });
});
