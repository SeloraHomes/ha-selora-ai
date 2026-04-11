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
import { readFileSync } from "fs";
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
 */
export async function ensureRelease({ repo, tag, notes, token, apiFn = githubApi }) {
  const headers = {
    Authorization: `token ${token}`,
    "User-Agent": "semantic-release",
  };

  try {
    const body = JSON.stringify({
      tag_name: tag,
      target_commitish: "main",
      name: tag,
      body: notes,
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
export async function main(version, token) {
  // 1. Build the zip first — fail early before touching GitHub
  //    Uses `zip` instead of `git archive` because panel.js/card.js are
  //    build artifacts not tracked in git — they are built in CI before this runs.
  execSync(
    `cd custom_components && zip -r ../${ZIP_FILENAME} selora_ai` +
    ` -x "selora_ai/frontend/node_modules/*"` +
    ` -x "selora_ai/frontend/src/*"` +
    ` -x "selora_ai/frontend/build.js"` +
    ` -x "selora_ai/frontend/postbuild.js"` +
    ` -x "selora_ai/frontend/package*.json"` +
    ` -x "selora_ai/frontend/vitest.config.js"`,
    { stdio: "inherit" }
  );
  const zipData = readFileSync(ZIP_FILENAME);
  console.log(`Built ${ZIP_FILENAME} (${zipData.length} bytes)`);

  // 2. Create or reuse the GitHub release
  const changelog = (() => {
    try { return readFileSync("CHANGELOG.md", "utf8"); } catch { return ""; }
  })();
  const tag = `v${version}`;
  const release = await ensureRelease({ repo: GITHUB_REPO, tag, notes: extractNotes(changelog), token });

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
  if (!version) {
    console.error("Usage: github-release.mjs <version>");
    process.exit(1);
  }
  const token = process.env.GH_TOKEN;
  if (!token) {
    console.error("GH_TOKEN env var is required");
    process.exit(1);
  }
  main(version, token).catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
