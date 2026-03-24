#!/usr/bin/env python3
"""Validate that the integration meets HACS requirements.

Run locally:
    python3 scripts/validate_hacs.py

Also executed in:
  - GitLab CI: validate:hacs job
  - Lefthook:  pre-push validate-hacs command
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
errors: list[str] = []

# ── 1. hacs.json ──────────────────────────────────────────────────────────────
hacs_path = ROOT / "hacs.json"
hacs: dict = {}
if not hacs_path.exists():
    errors.append("Missing hacs.json at repo root")
else:
    try:
        hacs = json.loads(hacs_path.read_text())
        for field in ("name", "render_readme", "homeassistant"):
            if field not in hacs:
                errors.append(f"hacs.json: missing required field '{field}'")
    except json.JSONDecodeError as exc:
        errors.append(f"hacs.json: invalid JSON — {exc}")

# ── 2. manifest.json ──────────────────────────────────────────────────────────
manifest_path = ROOT / "custom_components" / "selora_ai" / "manifest.json"
manifest: dict = {}
if not manifest_path.exists():
    errors.append("Missing custom_components/selora_ai/manifest.json")
else:
    try:
        manifest = json.loads(manifest_path.read_text())
        required_fields = [
            "domain", "name", "version", "documentation",
            "codeowners", "config_flow", "iot_class",
        ]
        for field in required_fields:
            if field not in manifest:
                errors.append(f"manifest.json: missing required field '{field}'")
        version = manifest.get("version", "")
        parts = version.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            errors.append(f"manifest.json: 'version' must be semver X.Y.Z (got '{version}')")
    except json.JSONDecodeError as exc:
        errors.append(f"manifest.json: invalid JSON — {exc}")

# ── 3. Required files ─────────────────────────────────────────────────────────
required_files = [
    ROOT / "README.md",
    ROOT / "custom_components" / "selora_ai" / "__init__.py",
    ROOT / "custom_components" / "selora_ai" / "strings.json",
    ROOT / "custom_components" / "selora_ai" / "translations" / "en.json",
]
for path in required_files:
    if not path.exists():
        errors.append(f"Missing required file: {path.relative_to(ROOT)}")

# ── 4. strings.json / translations/en.json top-level key parity ───────────────
strings_path = ROOT / "custom_components" / "selora_ai" / "strings.json"
en_path = ROOT / "custom_components" / "selora_ai" / "translations" / "en.json"
if strings_path.exists() and en_path.exists():
    try:
        strings_keys = set(json.loads(strings_path.read_text()).keys())
        en_keys = set(json.loads(en_path.read_text()).keys())
        missing_in_en = strings_keys - en_keys
        extra_in_en = en_keys - strings_keys
        if missing_in_en:
            errors.append(f"translations/en.json missing keys: {sorted(missing_in_en)}")
        if extra_in_en:
            errors.append(f"translations/en.json has extra keys vs strings.json: {sorted(extra_in_en)}")
    except json.JSONDecodeError as exc:
        errors.append(f"strings.json or translations/en.json: invalid JSON — {exc}")

# ── Result ────────────────────────────────────────────────────────────────────
if errors:
    print("\n✗ HACS validation FAILED\n")
    for err in errors:
        print(f"  • {err}")
    print()
    sys.exit(1)

print("✓ HACS validation passed")
print(f"  Integration : {hacs.get('name', '?')}")
print(f"  Domain      : {manifest.get('domain', '?')}")
print(f"  Version     : {manifest.get('version', '?')}")
print(f"  HA min      : {hacs.get('homeassistant', '?')}")
