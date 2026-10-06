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
from custom_components.selora_ai.providers.selora_local.runtime.request_build import (
    _SELORA_LOCAL_PROMPT_TOKENS,
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
    assert len(data) == pin["size_bytes"], (
        f"{filename} is {len(data)} bytes; the fixture pins {pin['size_bytes']}"
    )


def test_every_bundled_prompt_is_pinned() -> None:
    """A prompt added to the loader without a pin is one nothing checks."""
    assert set(_SELORA_LOCAL_PROMPT_FILENAMES) == set(_PINS)


# sha256 of the file each ``_SELORA_LOCAL_PROMPT_TOKENS`` count was measured
# on with the Qwen3 tokenizer. The entity budget is sized from those counts,
# so a released prompt of a different size would over- or under-fill the
# context window without failing anything if the old number stayed.
_TOKENS_MEASURED_ON: dict[str, str] = {
    "command": "9921c6fef09c6ebad4a2ed4fad1dbe7e76efe0bfe4e532bf7c7fe096864de6a4",
    "automation": "04e2d8231e91d00afba0c50964a0647b036303bd4c4dbc9e7c58dd3434d2b682",
    "answer": "ec4c2dfb6bcd378e65f891a15d9066d9f0c295a1ac3fc9dbc01cb01a9c0d6cb2",
    "clarification": "c6833a17147574946a7176447a88d65e687bc393e62db1aaa89c57d1fdf9a3ac",
    "utilities": "ae1155b644b529ba63d9441b2abc347fd6f4e4b3d4bbb25b323509707df90d36",
    _SELORA_LOCAL_UNIFIED_PROMPT_KEY: (
        "cdbd03ed54d0243652bd11871aa055a14ef29f18825f7a4b5207ca461b7b3be0"
    ),
}


def test_every_bundled_prompt_has_a_token_count() -> None:
    assert set(_SELORA_LOCAL_PROMPT_TOKENS) == set(_SELORA_LOCAL_PROMPT_FILENAMES)
    assert set(_TOKENS_MEASURED_ON) == set(_SELORA_LOCAL_PROMPT_FILENAMES)


@pytest.mark.parametrize("intent", sorted(_PINS))
def test_prompt_token_count_was_measured_on_the_bundled_file(intent: str) -> None:
    """A new prompt needs a new count: encode the stripped file with the
    Qwen3 tokenizer (no special tokens), then update
    ``_SELORA_LOCAL_PROMPT_TOKENS`` and the hash here together."""
    assert _PINS[intent]["sha256"] == _TOKENS_MEASURED_ON[intent], (
        f"{_PINS[intent]['filename']} changed since its token count "
        f"({_SELORA_LOCAL_PROMPT_TOKENS[intent]}) was measured; re-measure it"
    )


@pytest.mark.parametrize("intent", sorted(_PINS))
def test_prompt_token_count_is_plausible_for_its_size(intent: str) -> None:
    """Catches a mistyped count: Qwen3 encodes these English prompts at
    about 4 characters per token."""
    text = (_SELORA_LOCAL_PROMPTS_DIR / _SELORA_LOCAL_PROMPT_FILENAMES[intent]).read_text(
        encoding="utf-8"
    )
    ratio = len(text.strip()) / _SELORA_LOCAL_PROMPT_TOKENS[intent]
    assert 3.5 <= ratio <= 5.0, f"{intent}: {ratio:.2f} characters per token"
