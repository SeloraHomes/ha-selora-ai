"""LLM client package — business-logic facade over pluggable LLM providers.

The package is split into focused modules; only ``LLMClient`` is exported
here. Internal callers import helpers (prompts, parsers, command policy,
usage tracking, sanitisers) directly from the relevant submodule.
"""

from __future__ import annotations

from .client import LLMClient

__all__ = ["LLMClient"]
