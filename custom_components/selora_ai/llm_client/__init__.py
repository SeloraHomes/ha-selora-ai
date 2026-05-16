"""LLM client package — business-logic facade over pluggable LLM providers.

The package is split into focused modules; only ``LLMClient`` is the stable
public entry point. A handful of helpers are also re-exported here because
they are consumed by the ``mcp_server``, top-level integration entrypoint,
and the test suite — keeping the import surface compatible with the
pre-split monolithic module.
"""

from __future__ import annotations

from .client import LLMClient
from .command_policy import (
    _build_command_confirmation,
    _executed_service_calls_from_log,
    _is_generic_acknowledgement,
    _response_describes_executed_call,
    _response_is_synthesized_confirmation,
    _suppress_duplicate_command_after_tool,
    _tool_failure_response,
    validate_command_action,
)
from .prompts import _read_prompt_files

__all__ = [
    "LLMClient",
    "_build_command_confirmation",
    "_executed_service_calls_from_log",
    "_is_generic_acknowledgement",
    "_read_prompt_files",
    "_response_describes_executed_call",
    "_response_is_synthesized_confirmation",
    "_suppress_duplicate_command_after_tool",
    "_tool_failure_response",
    "validate_command_action",
]
