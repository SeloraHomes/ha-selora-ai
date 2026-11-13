import { describe, it, mock } from "node:test";
import assert from "node:assert/strict";
import * as gitlab from "@semantic-release/gitlab";
import * as plugin from "./semantic-release-gitlab.mjs";

const context = () => ({ logger: { warn: mock.fn(), log: mock.fn(), error: mock.fn() } });

describe("semantic-release-gitlab", () => {
  it("keeps the plugin's own verify, publish and fail steps", () => {
    assert.equal(plugin.verifyConditions, gitlab.verifyConditions);
    assert.equal(plugin.publish, gitlab.publish);
    assert.equal(plugin.fail, gitlab.fail);
  });

  it("logs a failed MR comment instead of failing the release", async () => {
    const ctx = context();
    const step = plugin.tolerant(async () => {
      throw Object.assign(new Error("Socket closed before the connection was established"), {
        code: "ERR_SOCKET_CLOSED_BEFORE_CONNECTION",
      });
    });

    assert.equal(await step({}, ctx), undefined);
    assert.equal(ctx.logger.warn.mock.callCount(), 1);
    assert.match(ctx.logger.warn.mock.calls[0].arguments[0], /Socket closed/);
  });

  it("passes the step's arguments and result through", async () => {
    const inner = mock.fn(async () => "done");
    const ctx = context();

    assert.equal(await plugin.tolerant(inner)({ a: 1 }, ctx), "done");
    assert.deepEqual(inner.mock.calls[0].arguments, [{ a: 1 }, ctx]);
    assert.equal(ctx.logger.warn.mock.callCount(), 0);
  });
});
