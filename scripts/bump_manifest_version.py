#!/usr/bin/env python3
"""Bump the version field in custom_components/selora_ai/manifest.json.

Called by semantic-release during the prepare step:
    python3 scripts/bump_manifest_version.py 0.2.0

Writes the new version in-place, preserving all other fields and
JSON key ordering so diffs stay minimal.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

MANIFEST_PATH = (
    Path(__file__).parent.parent
    / "custom_components"
    / "selora_ai"
    / "manifest.json"
)


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: bump_manifest_version.py <new_version>", file=sys.stderr)
        sys.exit(1)

    new_version = sys.argv[1].lstrip("v")  # strip leading "v" if present

    # Validate semver shape
    parts = new_version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        print(
            f"Error: version must be X.Y.Z semver, got '{new_version}'",
            file=sys.stderr,
        )
        sys.exit(1)

    manifest = json.loads(MANIFEST_PATH.read_text())
    old_version = manifest.get("version", "?")
    manifest["version"] = new_version

    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"manifest.json: {old_version} → {new_version}")


if __name__ == "__main__":
    main()
