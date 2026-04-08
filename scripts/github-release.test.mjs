import { describe, it, mock } from "node:test";
import assert from "node:assert/strict";
import { extractNotes, ensureRelease, deleteAsset, uploadAsset, ZIP_FILENAME } from "./github-release.mjs";

// ---------------------------------------------------------------------------
// extractNotes
// ---------------------------------------------------------------------------
describe("extractNotes", () => {
  it("extracts the first release section", () => {
    const changelog = [
      "# Changelog",
      "",
      "## 1.2.0 (2026-04-08)",
      "",
      "### Features",
      "",
      "* added widget",
      "",
      "## 1.1.0 (2026-03-01)",
      "",
      "### Bug Fixes",
      "",
      "* fixed thing",
    ].join("\n");

    assert.equal(extractNotes(changelog), "### Features\n\n* added widget");
  });

  it("returns empty string when there are no ## headings", () => {
    assert.equal(extractNotes("# Changelog\n\nNothing here"), "");
  });

  it("strips trailing top-level heading and content", () => {
    const changelog = [
      "## 1.0.0 (2026-01-01)",
      "",
      "### Bug Fixes",
      "",
      "* fix",
      "",
      "# Changelog",
      "",
      "Preamble text",
    ].join("\n");

    assert.equal(extractNotes(changelog), "### Bug Fixes\n\n* fix");
  });

  it("handles single release with no trailing content", () => {
    const changelog = "## 0.1.0 (2026-01-01)\n\nInitial release.";
    assert.equal(extractNotes(changelog), "Initial release.");
  });
});

// ---------------------------------------------------------------------------
// ensureRelease
// ---------------------------------------------------------------------------
describe("ensureRelease", () => {
  const baseArgs = {
    repo: "owner/repo",
    tag: "v1.0.0",
    notes: "release notes",
    token: "fake-token",
  };

  it("creates a new release on first attempt", async () => {
    const expected = { id: 42, html_url: "https://github.com/owner/repo/releases/tag/v1.0.0" };
    const apiFn = mock.fn(async () => expected);

    const result = await ensureRelease({ ...baseArgs, apiFn });

    assert.deepEqual(result, expected);
    assert.equal(apiFn.mock.calls.length, 1);
    assert.equal(apiFn.mock.calls[0].arguments[0].method, "POST");
    assert.equal(apiFn.mock.calls[0].arguments[0].path, "/repos/owner/repo/releases");
  });

  it("reuses existing release on 422 conflict", async () => {
    const existingRelease = {
      id: 99,
      html_url: "https://github.com/owner/repo/releases/tag/v1.0.0",
      assets: [],
    };

    const apiFn = mock.fn(async (options) => {
      if (options.method === "POST") {
        throw Object.assign(new Error("already exists"), { statusCode: 422, body: "{}" });
      }
      return existingRelease;
    });

    const result = await ensureRelease({ ...baseArgs, apiFn });

    assert.equal(result.id, 99);
    assert.equal(apiFn.mock.calls.length, 2);
    assert.equal(apiFn.mock.calls[1].arguments[0].method, "GET");
    assert.match(apiFn.mock.calls[1].arguments[0].path, /\/releases\/tags\/v1\.0\.0/);
  });

  it("does not touch assets on reuse", async () => {
    const existingRelease = {
      id: 99,
      html_url: "https://github.com/owner/repo/releases/tag/v1.0.0",
      assets: [{ id: 555, name: ZIP_FILENAME }],
    };

    const calls = [];
    const apiFn = mock.fn(async (options) => {
      calls.push(options.method);
      if (options.method === "POST") {
        throw Object.assign(new Error("already exists"), { statusCode: 422, body: "{}" });
      }
      return existingRelease;
    });

    await ensureRelease({ ...baseArgs, apiFn });

    assert.deepEqual(calls, ["POST", "GET"]);
  });

  it("propagates non-422 errors", async () => {
    const apiFn = mock.fn(async () => {
      throw Object.assign(new Error("forbidden"), { statusCode: 403, body: "{}" });
    });

    await assert.rejects(() => ensureRelease({ ...baseArgs, apiFn }), { statusCode: 403 });
  });
});

// ---------------------------------------------------------------------------
// deleteAsset
// ---------------------------------------------------------------------------
describe("deleteAsset", () => {
  const baseArgs = {
    repo: "owner/repo",
    token: "fake-token",
  };

  it("deletes matching asset and returns its id", async () => {
    const release = { assets: [{ id: 555, name: ZIP_FILENAME }] };
    const apiFn = mock.fn(async () => undefined);

    const result = await deleteAsset({ ...baseArgs, release, filename: ZIP_FILENAME, apiFn });

    assert.equal(result, 555);
    assert.equal(apiFn.mock.calls.length, 1);
    assert.equal(apiFn.mock.calls[0].arguments[0].method, "DELETE");
    assert.match(apiFn.mock.calls[0].arguments[0].path, /\/assets\/555/);
  });

  it("returns null when no matching asset exists", async () => {
    const release = { assets: [{ id: 777, name: "other_file.zip" }] };
    const apiFn = mock.fn(async () => undefined);

    const result = await deleteAsset({ ...baseArgs, release, filename: ZIP_FILENAME, apiFn });

    assert.equal(result, null);
    assert.equal(apiFn.mock.calls.length, 0);
  });

  it("returns null when release has no assets", async () => {
    const release = { assets: [] };
    const apiFn = mock.fn(async () => undefined);

    const result = await deleteAsset({ ...baseArgs, release, filename: ZIP_FILENAME, apiFn });

    assert.equal(result, null);
    assert.equal(apiFn.mock.calls.length, 0);
  });

  it("handles 204 No Content from GitHub", async () => {
    const release = { assets: [{ id: 555, name: ZIP_FILENAME }] };
    const apiFn = mock.fn(async () => undefined);

    const result = await deleteAsset({ ...baseArgs, release, filename: ZIP_FILENAME, apiFn });

    assert.equal(result, 555);
  });
});

// ---------------------------------------------------------------------------
// uploadAsset
// ---------------------------------------------------------------------------
describe("uploadAsset", () => {
  it("uploads to the correct endpoint with zip content-type", async () => {
    const expected = { browser_download_url: "https://github.com/dl/selora_ai.zip" };
    const apiFn = mock.fn(async () => expected);
    const zipData = Buffer.from("fake-zip-data");

    const result = await uploadAsset({
      repo: "owner/repo",
      releaseId: 42,
      filename: "selora_ai.zip",
      zipData,
      token: "fake-token",
      apiFn,
    });

    assert.deepEqual(result, expected);
    const opts = apiFn.mock.calls[0].arguments[0];
    assert.equal(opts.hostname, "uploads.github.com");
    assert.equal(opts.path, "/repos/owner/repo/releases/42/assets?name=selora_ai.zip");
    assert.equal(opts.method, "POST");
    assert.equal(opts.headers["Content-Type"], "application/zip");
    assert.equal(opts.headers["Content-Length"], zipData.length);
  });
});
