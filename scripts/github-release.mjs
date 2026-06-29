#!/usr/bin/env node
/**
 * Creates a GitHub release for the HACS mirror repo and uploads a zip asset.
 *
 * Called by semantic-release via @semantic-release/exec:
 *   node scripts/github-release.mjs <version>
 *
 * Expects:
 *   GH_TOKEN  env var — GitHub PAT with `repo` scope
 *   argv[2]   — semver version (without "v" prefix)
 *
 * Reads release notes from CHANGELOG.md (already updated by the changelog plugin).
 */

import { request } from "https";
import { readFileSync, rmSync } from "fs";
import { execSync } from "child_process";

export const GITHUB_REPO = "SeloraHomes/ha-selora-ai";
export const ZIP_FILENAME = "selora_ai.zip";
export const INTEGRATION_DIR = "custom_components/selora_ai";

/** Extract the latest release section from CHANGELOG.md */
export function extractNotes(changelog) {
  const sections = changelog.split(/^## /m);
  if (sections.length < 2) return "";
  let body = sections[1].substring(sections[1].indexOf("\n") + 1).trim();
  body = body.replace(/\n# [\s\S]*$/, "").trim();
  return body;
}

/** Low-level HTTPS request wrapper around Node's built-in `https.request`. */
export function githubApi(options, body) {
  return new Promise((resolve, reject) => {
    const req = request(options, (res) => {
      let data = "";
      res.on("data", (chunk) => (data += chunk));
      res.on("end", () => {
        if (res.statusCode >= 300) {
          reject(
            Object.assign(new Error(`GitHub API ${res.statusCode}: ${data}`), {
              statusCode: res.statusCode,
              body: data,
            })
          );
        } else {
          resolve(data ? JSON.parse(data) : undefined);
        }
      });
    });
    req.on("error", reject);
    req.end(body);
  });
}

/**
 * Create a GitHub release or reuse an existing one (idempotent).
 * Returns the release object from the GitHub API.
 *
 * `targetCommitish` controls where GitHub creates the tag if it does
 * not already exist on the mirror. For semantic-release runs on the
 * default branch that's "main", but for maintenance patches it must
 * be the actual tagged commit — otherwise GitHub auto-creates the tag
 * on `main` and the source archives ship main's code under a
 * maintenance version number.
 */
export async function ensureRelease({
  repo,
  tag,
  notes,
  token,
  targetCommitish = "main",
  prerelease = false,
  apiFn = githubApi,
}) {
  const headers = {
    Authorization: `token ${token}`,
    "User-Agent": "semantic-release",
  };

  try {
    const body = JSON.stringify({
      tag_name: tag,
      target_commitish: targetCommitish,
      name: tag,
      body: notes,
      // HACS installs the latest non-prerelease release by default, so marking
      // rc tags as prereleases keeps them off normal users' update lists unless
      // they enable "show beta versions" for this repo.
      prerelease,
    });
    const release = await apiFn(
      {
        hostname: "api.github.com",
        path: `/repos/${repo}/releases`,
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) },
      },
      body
    );
    console.log("GitHub release created:", release.html_url);
    return release;
  } catch (err) {
    if (err.statusCode !== 422) throw err;

    // Release already exists — fetch it by tag
    const release = await apiFn(
      {
        hostname: "api.github.com",
        path: `/repos/${repo}/releases/tags/${tag}`,
        method: "GET",
        headers,
      },
      null
    );
    console.log("GitHub release already exists, reusing:", release.html_url);
    return release;
  }
}

/**
 * Delete an existing asset by name from a release, if present.
 * Returns the deleted asset id, or null if no matching asset was found.
 */
export async function deleteAsset({ repo, release, filename, token, apiFn = githubApi }) {
  const existing = (release.assets || []).find((a) => a.name === filename);
  if (!existing) return null;

  await apiFn(
    {
      hostname: "api.github.com",
      path: `/repos/${repo}/releases/assets/${existing.id}`,
      method: "DELETE",
      headers: {
        Authorization: `token ${token}`,
        "User-Agent": "semantic-release",
      },
    },
    null
  );
  console.log("Deleted stale asset:", existing.id);
  return existing.id;
}

/** Upload a zip buffer as a release asset. Returns the asset object. */
export async function uploadAsset({ repo, releaseId, filename, zipData, token, apiFn = githubApi }) {
  return apiFn(
    {
      hostname: "uploads.github.com",
      path: `/repos/${repo}/releases/${releaseId}/assets?name=${filename}`,
      method: "POST",
      headers: {
        Authorization: `token ${token}`,
        "Content-Type": "application/zip",
        "User-Agent": "semantic-release",
        "Content-Length": zipData.length,
      },
    },
    zipData
  );
}

/** Main entry point — only runs when executed directly (not imported). */
export async function main(version, token, targetCommitish = "main") {
  // 1. Build the zip first — fail early before touching GitHub
  //    Zips from inside the integration dir so paths are relative (no selora_ai/ prefix).
  //    HACS extracts directly into custom_components/selora_ai/, so the zip must be flat.
  //
  //    Remove any pre-existing zip before invoking `zip -r`. The
  //    `zip` command UPDATES an existing archive rather than recreating
  //    it, so a leftover from a direct invocation or interrupted publish
  //    would carry over obsolete entries that aren't part of the tagged
  //    commit — silently shipping stale files inside the HACS asset.
  rmSync(ZIP_FILENAME, { force: true });
  execSync(
    `cd ${INTEGRATION_DIR} && zip -r ../../${ZIP_FILENAME} .` +
    ` -x "./frontend/node_modules/*"` +
    ` -x "./frontend/src/*"` +
    ` -x "./frontend/build.js"` +
    ` -x "./frontend/postbuild.js"` +
    ` -x "./frontend/package*.json"` +
    ` -x "./frontend/vitest.config.js"` +
    ` -x "./__pycache__/*"` +
    ` -x "./.git*"`,
    { stdio: "inherit" }
  );
  const zipData = readFileSync(ZIP_FILENAME);
  console.log(`Built ${ZIP_FILENAME} (${zipData.length} bytes)`);

  // 2. Create or reuse the GitHub release
  const changelog = (() => {
    try { return readFileSync("CHANGELOG.md", "utf8"); } catch { return ""; }
  })();
  const tag = `v${version}`;
  // A semver prerelease identifier (e.g. 0.12.0-rc.1) means this is a
  // prerelease tag — flag it so HACS keeps it off the default update list.
  const prerelease = version.includes("-");
  const release = await ensureRelease({
    repo: GITHUB_REPO,
    tag,
    notes: extractNotes(changelog),
    token,
    targetCommitish,
    prerelease,
  });

  // 3. Remove any stale asset (required — GitHub rejects duplicate names),
  //    then upload the replacement immediately
  await deleteAsset({ repo: GITHUB_REPO, release, filename: ZIP_FILENAME, token });
  const asset = await uploadAsset({
    repo: GITHUB_REPO,
    releaseId: release.id,
    filename: ZIP_FILENAME,
    zipData,
    token,
  });
  console.log("Asset uploaded:", asset.browser_download_url);
}

// CLI entry point — skip when imported as a module for testing
const isCliRun =
  process.argv[1] &&
  import.meta.url.endsWith(process.argv[1].replace(/\\/g, "/"));

if (isCliRun) {
  const version = process.argv[2];
  const targetCommitish = process.argv[3] || "main";
  if (!version) {
    console.error("Usage: github-release.mjs <version> [target-commitish]");
    process.exit(1);
  }
  const token = process.env.GH_TOKEN;
  if (!token) {
    console.error("GH_TOKEN env var is required");
    process.exit(1);
  }
  main(version, token, targetCommitish).catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
