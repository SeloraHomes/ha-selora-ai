#!/usr/bin/env node
/**
 * Creates a GitHub release for the HACS mirror repo.
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

const GITHUB_REPO = "SeloraHomes/ha-selora-ai";

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

// Extract the latest release section from CHANGELOG.md
function extractNotes() {
  try {
    const changelog = readFileSync("CHANGELOG.md", "utf8");
    // Match from the first ## heading to the next ## heading (or EOF)
    const match = changelog.match(/^## .+?\n([\s\S]*?)(?=\n## |\n*$)/m);
    return match ? match[1].trim() : "";
  } catch {
    return "";
  }
}

const body = JSON.stringify({
  tag_name: `v${version}`,
  name: `v${version}`,
  body: extractNotes(),
});

const req = request(
  {
    hostname: "api.github.com",
    path: `/repos/${GITHUB_REPO}/releases`,
    method: "POST",
    headers: {
      Authorization: `token ${token}`,
      "Content-Type": "application/json",
      "User-Agent": "semantic-release",
      "Content-Length": Buffer.byteLength(body),
    },
  },
  (res) => {
    let data = "";
    res.on("data", (chunk) => (data += chunk));
    res.on("end", () => {
      if (res.statusCode >= 300) {
        console.error(`GitHub API ${res.statusCode}: ${data}`);
        process.exit(1);
      }
      console.log("GitHub release created:", JSON.parse(data).html_url);
    });
  }
);
req.on("error", (err) => {
  console.error(err);
  process.exit(1);
});
req.end(body);
