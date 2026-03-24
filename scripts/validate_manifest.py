#!/usr/bin/env python3
"""Validate required fields in custom_components/selora_ai/manifest.json.

Run locally:  python3 scripts/validate_manifest.py
Also called by the validate:manifest GitLab CI job.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
REQUIRED_FIELDS = [
    "domain", "name", "version", "documentation",
    "codeowners", "config_flow", "iot_class",
]

manifest_path = ROOT / "custom_components" / "selora_ai" / "manifest.json"
errors: list[str] = []

if not manifest_path.exists():
    print("✗ manifest.json not found at", manifest_path)
    sys.exit(1)

try:
    manifest = json.loads(manifest_path.read_text())
except json.JSONDecodeError as exc:
    print("✗ manifest.json is invalid JSON:", exc)
    sys.exit(1)

for field in REQUIRED_FIELDS:
    if field not in manifest:
        errors.append(f"missing required field: '{field}'")

version = manifest.get("version", "")
parts = version.split(".")
if len(parts) != 3 or not all(p.isdigit() for p in parts):
    errors.append(f"'version' must be semver X.Y.Z (got '{version}')")

if errors:
    print("\n✗ manifest.json validation FAILED\n")
    for e in errors:
        print(f"  • {e}")
    sys.exit(1)

print("✓ manifest.json OK")
print(f"  domain  : {manifest['domain']}")
print(f"  version : {manifest['version']}")
