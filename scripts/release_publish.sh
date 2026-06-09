#!/usr/bin/env bash
# Publish a patch release prepared by release_patch.py.
#
# Pushes the current N.N.x branch + the new tag to GitLab, mirrors the
# release to GitHub (HACS reads from there), and creates a GitLab
# release page that links the changelog notes.
#
# The `v*` tag pattern is protected — only the semantic-release CI user
# (35769985) can create tags. This script will fail at the tag push if
# that protection is still in place and prints the unprotect/reprotect
# steps so the operator can decide rather than silently relaxing
# project-wide security.
#
# Requires:
#   GH_TOKEN env var  (or `gh auth token` for a local fallback)
#   glab CLI logged in for the GitLab release page

set -euo pipefail

VERSION="${1:-}"
if [[ -z "${VERSION}" ]]; then
  echo "usage: $0 <version>" >&2
  exit 1
fi

TAG="v${VERSION}"
PROJECT_ID="selorahomes%2Fproducts%2Fselora-ai%2Fha-integration"
GITHUB_REPO="SeloraHomes/ha-selora-ai"
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "${REPO_ROOT}"

BRANCH="$(git branch --show-current)"
if [[ ! "${BRANCH}" =~ ^[0-9]+\.[0-9]+\.x$ ]]; then
  echo "error: current branch '${BRANCH}' is not an N.N.x maintenance branch" >&2
  exit 1
fi

if ! git rev-parse --verify --quiet "${TAG}" >/dev/null; then
  echo "error: tag ${TAG} does not exist locally — run \`just release-patch ${VERSION}\` first" >&2
  exit 1
fi

# Bind the published archive to the exact tag.
#
# github-release.mjs zips the current checkout (not `git archive ${TAG}`),
# so if HEAD has advanced past the tag — or if the working tree has any
# edits — the HACS zip would contain code that isn't in vX.Y.Z. That
# silently ships an inconsistent release. Refuse to publish until HEAD
# matches the tag exactly and there are no uncommitted changes.
HEAD_SHA="$(git rev-parse HEAD)"
TAG_SHA="$(git rev-parse "${TAG}^{commit}")"
if [[ "${HEAD_SHA}" != "${TAG_SHA}" ]]; then
  echo "error: HEAD (${HEAD_SHA:0:7}) does not match tag ${TAG} (${TAG_SHA:0:7})." >&2
  echo "       The HACS zip would not match the tagged code. Check out ${TAG} first." >&2
  exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo "error: working tree is not clean — uncommitted changes would leak into the HACS zip." >&2
  git status --short >&2
  exit 1
fi
export TAG_SHA

# Resolve GitHub token before doing anything irreversible.
if [[ -z "${GH_TOKEN:-}" ]]; then
  if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
    GH_TOKEN="$(gh auth token)"
    export GH_TOKEN
  else
    echo "error: GH_TOKEN unset and \`gh\` is not authenticated" >&2
    exit 1
  fi
fi

echo "→ pushing branch ${BRANCH}..."
git push origin "${BRANCH}"

echo
echo "→ pushing tag ${TAG}..."
if ! git push origin "${TAG}" 2>&1; then
  cat <<EOF >&2

Tag push rejected. The \`v*\` pattern is protected on GitLab.

To finish the release:
  1. GitLab → Settings → Repository → Protected tags → Unprotect \`v*\`
     (or: glab api --method DELETE "projects/${PROJECT_ID}/protected_tags/v*")
  2. Re-run \`just release-publish ${VERSION}\`
  3. Re-protect \`v*\` with the original ACL:
       allowed_to_create = [
         { access_level: 0 },           # "No one"
         { user_id: 35769985 },         # semantic-release CI user
       ]

Stopping here so the protection state is your explicit choice.
EOF
  exit 1
fi

echo
echo "→ verifying maintenance commit exists on the GitHub mirror..."
# github-release.mjs passes TAG_SHA as target_commitish to GitHub. If
# the GitLab → GitHub mirror has not synced this commit yet, GitHub
# returns 422 ("Object does not exist") on POST /releases. The
# ensureRelease retry path then misreads that 422 as "release already
# exists" and the follow-up GET returns 404, leaving the publish in
# an indeterminate state. Probe first so the failure mode is clear
# and recoverable.
if ! curl -fsS \
    -H "Authorization: token ${GH_TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${GITHUB_REPO}/commits/${TAG_SHA}" \
    >/dev/null 2>&1; then
  cat <<EOF >&2

error: commit ${TAG_SHA:0:7} is not on the GitHub mirror (${GITHUB_REPO}).

The mirror sync from GitLab is asynchronous. Wait a minute and retry,
or push the branch + tag to GitHub manually if the mirror is stuck:

  git push git@github.com:${GITHUB_REPO}.git ${BRANCH}
  git push git@github.com:${GITHUB_REPO}.git ${TAG}

Stopping here so the GitHub release does not get anchored to ${TAG}
on a commit that does not exist in the mirror.
EOF
  exit 1
fi

# If a stale `vX.Y.Z` tag is already on the mirror pointing at a
# different commit (botched earlier run, manual push, etc.), GitHub
# ignores `target_commitish` on the next release call and the release
# page keeps serving source archives anchored on the wrong commit
# while we happily upload the right zip on top. The mismatch is
# silent and breaks HACS users that download the source archive
# instead of the zip. Refuse to continue until the remote tag
# agrees with TAG_SHA.
# Resolve the existing GH tag (if any) to its commit SHA.
#
# A lightweight tag's ref.object.sha IS the commit. An annotated tag
# points at a tag object — read /git/tags/<sha> and use ITS object.sha
# (which is the commit). Without this dereference, an annotated tag
# always compares unequal to TAG_SHA and we'd block every retry.
if ! EXISTING_GH_TAG_SHA="$(
  GITHUB_REPO="${GITHUB_REPO}" \
  GH_TOKEN="${GH_TOKEN}" \
  TAG="${TAG}" \
  python3 - <<'PY'
import json, os, sys, urllib.error, urllib.request

REPO = os.environ["GITHUB_REPO"]
TOKEN = os.environ["GH_TOKEN"]
TAG = os.environ["TAG"]

def fetch(path):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}{path}",
        headers={
            "Authorization": f"token {TOKEN}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "release-publish.sh",
        },
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        # 401 / 403 / 5xx / rate-limit all propagate — masking them as
        # "tag absent" would let a stale mismatched tag slip past the
        # integrity check below.
        raise

ref = fetch(f"/git/refs/tags/{TAG}")
if ref is None:
    sys.exit(0)
obj = ref.get("object") or {}
sha, otype = obj.get("sha"), obj.get("type")
# Walk through annotated-tag indirection. In practice this is at most
# one hop; the loop guards against the pathological multi-level case.
while otype == "tag" and sha:
    tag_obj = fetch(f"/git/tags/{sha}")
    if not tag_obj:
        break
    inner = tag_obj.get("object") or {}
    sha, otype = inner.get("sha"), inner.get("type")
print(sha or "")
PY
)"; then
  # Fail closed: a transient API error must not be conflated with
  # "tag absent". The previous form swallowed errors with `|| true`,
  # which silently green-lit publishing on top of a possibly-stale
  # tag pointing at the wrong commit — exactly the case this guard
  # exists to catch.
  echo "error: GitHub tag lookup for ${TAG} failed (auth / rate-limit / network?)" >&2
  echo "       Refusing to publish without confirming the tag's current target." >&2
  exit 1
fi
if [[ -n "${EXISTING_GH_TAG_SHA}" && "${EXISTING_GH_TAG_SHA}" != "${TAG_SHA}" ]]; then
  cat <<EOF >&2

error: GitHub mirror already has tag ${TAG} pointing at
       ${EXISTING_GH_TAG_SHA:0:7}, not ${TAG_SHA:0:7}.

The release page's auto-generated source archives are anchored on
the existing tag and won't be updated by the publish call. The zip
asset would replace, but source archives would still ship the wrong
commit's code.

Either delete the stale tag on the mirror and retry, or align the
intended commit with whatever the mirror already has:

  # Force-move the mirror tag (requires write access to the GH repo):
  git push --force git@github.com:${GITHUB_REPO}.git ${TAG_SHA}:refs/tags/${TAG}

EOF
  exit 1
fi

echo
echo "→ creating GitHub release + HACS zip..."
# Install an exit trap BEFORE github-release.mjs runs. The script
# builds `selora_ai.zip` in the repo root and the trailing `rm -f`
# at the bottom of this file only fires on the happy path — `set -e`
# bails out of any non-zero step before reaching it. Without the
# trap, a failed GitHub publish leaves the zip in place, which then
# trips the clean-tree preflight on the next retry and defeats the
# whole point of the idempotent flow.
trap 'rm -f "${NOTES_FILE:-}" "${REPO_ROOT}/selora_ai.zip"' EXIT
# Pass the maintenance commit SHA so the GitHub release does not get
# anchored to `main` when the tag has not propagated to the mirror yet
# — otherwise the auto-generated source archives would contain main's
# code instead of the maintenance tag's code.
node scripts/github-release.mjs "${VERSION}" "${TAG_SHA}"

echo
echo "→ creating GitLab release page..."
NOTES="$(python3 - <<'PY'
from pathlib import Path
text = Path("CHANGELOG.md").read_text()
parts = text.split("## ", 2)
body = "## " + parts[1]
print(body.split("\n## ")[0].rstrip())
PY
)"

# `-f description=...` would shell-expand and clobber backticks/newlines
# in the changelog. Use --raw-field's stdin form via a temp file so the
# notes round-trip exactly as written. (The cleanup trap installed
# above already covers NOTES_FILE — don't overwrite it here.)
NOTES_FILE="$(mktemp)"
printf '%s' "${NOTES}" > "${NOTES_FILE}"

# Make the GitLab release create idempotent.
#
# A retry of a half-completed publish (network blip after the POST
# succeeded, etc.) would otherwise fail with "release already exists"
# and leave whoever's running the script unsure what state the
# remote is in. Mirror the github-release.mjs behaviour: try POST,
# fall through to PUT if the release already exists.
if glab api "projects/${PROJECT_ID}/releases/${TAG}" >/dev/null 2>&1; then
  echo "  GitLab release already exists — updating description."
  GITLAB_METHOD=PUT
  GITLAB_PATH="projects/${PROJECT_ID}/releases/${TAG}"
  GITLAB_ARGS=(-F "description=@${NOTES_FILE}")
else
  GITLAB_METHOD=POST
  GITLAB_PATH="projects/${PROJECT_ID}/releases"
  GITLAB_ARGS=(
    -f "tag_name=${TAG}"
    -f "name=${TAG}"
    -F "description=@${NOTES_FILE}"
  )
fi

glab api --method "${GITLAB_METHOD}" "${GITLAB_PATH}" "${GITLAB_ARGS[@]}" \
  | python3 -c 'import sys, json; d=json.load(sys.stdin); print("GitLab release:", d.get("_links",{}).get("self", d.get("message","?")))'

# Zip + notes-file cleanup is handled by the EXIT trap installed
# before github-release.mjs ran — no explicit rm needed here.

echo
echo "✓ ${TAG} published"
echo "  - https://gitlab.com/selorahomes/products/selora-ai/ha-integration/-/releases/${TAG}"
echo "  - https://github.com/${GITHUB_REPO}/releases/tag/${TAG}"
echo
echo "Don't forget to re-protect the \`v*\` tag pattern if you relaxed it."
