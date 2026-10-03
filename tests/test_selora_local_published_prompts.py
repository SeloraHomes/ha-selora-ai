"""The bundled Selora AI Local prompts must be the published ones, byte for byte.

The specialists were fine-tuned on the prompts in the models repo, and a
prompt that differs from training puts the model out of distribution with
no error anywhere — the replies just get worse. Each release publishes
those prompts beside its adapters and lists them in the bundle's
``manifest.json`` under ``system_prompts``; the fixture is a copy of that
block, plus the fused build's ``system`` file for the unified prompt, which
no manifest entry covers.

A failure here is not fixed by editing the prompt OR the fixture to match
the other. Prompt wording changes in the models repo and arrives with a
model release: copy the published files and the release's
``system_prompts`` block together.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from custom_components.selora_ai.providers.selora_local import (
    _SELORA_LOCAL_PROMPT_FILENAMES,
    _SELORA_LOCAL_PROMPTS_DIR,
    _SELORA_LOCAL_UNIFIED_PROMPT_KEY,
)

_PUBLISHED: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "selora_local_published_prompts.json").read_text(
        encoding="utf-8"
    )
)

# intent -> the entry its bundled file is pinned to.
_PINS: dict[str, dict[str, Any]] = {
    **_PUBLISHED["system_prompts"],
    _SELORA_LOCAL_UNIFIED_PROMPT_KEY: _PUBLISHED["unified"],
}


@pytest.mark.parametrize("intent", sorted(_PINS))
def test_bundled_prompt_matches_the_published_release(intent: str) -> None:
    """sha256 over the file's raw bytes, as the manifest computes it — the
    trailing newline counts, so a reformatted file fails too."""
    pin = _PINS[intent]
    filename = _SELORA_LOCAL_PROMPT_FILENAMES[intent]
    assert filename == pin["filename"], (
        f"{intent} loads {filename}, but the release publishes it as {pin['filename']}"
    )
    data = (_SELORA_LOCAL_PROMPTS_DIR / filename).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    assert digest == pin["sha256"], (
        f"{filename} has drifted from the prompt published with v{_PUBLISHED['version']} "
        f"({len(data)} bytes, sha256 {digest}; published {pin['size_bytes']} bytes, "
        f"sha256 {pin['sha256']}). Copy the published file rather than editing it here."
    )


def test_every_bundled_prompt_is_pinned() -> None:
    """A prompt added to the loader without a pin is one nothing checks."""
    assert set(_SELORA_LOCAL_PROMPT_FILENAMES) == set(_PINS)
