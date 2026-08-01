"""Contract test: every key the panel sends must be accepted by the ws schema.

Handler tests drive the *unwrapped* coroutine with a hand-built dict, so the
`@websocket_command` voluptuous schema never runs — a panel payload carrying a
key the schema does not declare passes every unit test and then fails at runtime
with ``extra keys not allowed @ data['<key>']``. This test closes that gap by
validating the payload literals in `frontend/src` against the real schemas
(`_ws_schema`, built by HA's decorator) collected from the websocket package.

Statically-invisible keys (spread of a computed object, dynamic `type`) are
skipped: the test is a one-way guard against drift, never a completeness claim.
"""

from __future__ import annotations

import importlib
from pathlib import Path
import re
from typing import Any

import pytest
import voluptuous as vol

from custom_components.selora_ai.websocket.automations import (
    _handle_websocket_update_automation_yaml,
)

COMPONENT = Path(__file__).parent.parent / "custom_components" / "selora_ai"
FRONTEND_SRC = COMPONENT / "frontend" / "src"

# Always present on every websocket message.
_BASE_KEYS = {"id", "type"}


def _command_modules() -> list[str]:
    """Dotted paths of every module that declares websocket commands.

    Handlers live in the `websocket` package, but a few are registered from
    other modules (recipes, the chat entry point), so scan the whole component
    rather than one directory — a command the scan misses would be reported as
    "unknown", not silently skipped.
    """
    modules: list[str] = []
    for path in sorted(COMPONENT.rglob("*.py")):
        if "frontend" in path.parts or "websocket_command(" not in path.read_text():
            continue
        relative = path.relative_to(COMPONENT.parent.parent).with_suffix("")
        parts = [part for part in relative.parts if part != "__init__"]
        modules.append(".".join(parts))
    return modules


def _allowed_keys() -> dict[str, set[str]]:
    """Map command name → keys its registered schema accepts."""
    commands: dict[str, set[str]] = {}
    for dotted in _command_modules():
        module = importlib.import_module(dotted)
        for obj in vars(module).values():
            command = getattr(obj, "_ws_command", None)
            if command is None:
                continue
            schema = getattr(obj, "_ws_schema", False)
            if schema is False:
                commands[command] = set(_BASE_KEYS)
                continue
            mapping = schema.validators[0] if isinstance(schema, vol.All) else schema
            commands[command] = {str(key) for key in mapping.schema} | _BASE_KEYS
    return commands


_CALL_RE = re.compile(r"callWS\(\s*\{")
_TYPE_RE = re.compile(r'^\s*type:\s*"(selora_ai/[^"]+)"', re.M)
_KEY_RE = re.compile(r'^\s*(?:"([A-Za-z_][\w]*)"|([A-Za-z_][\w]*))\s*:', re.M)
# `...(cond ? { automation_id: x } : {})` — conditionally spread literals still
# land at the top level, so their keys count as sent.
_SPREAD_RE = re.compile(r"\.\.\.\([^()]*\{([^{}]*)\}")


def _object_literal(source: str, open_brace: int) -> str:
    """Return the balanced `{...}` body starting at `open_brace`."""
    depth = 0
    for index in range(open_brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace + 1 : index]
    raise AssertionError(f"unbalanced object literal at offset {open_brace}")


def _top_level(body: str) -> str:
    """Blank out nested braces/brackets so only depth-1 keys remain visible."""
    out: list[str] = []
    depth = 0
    for char in body:
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            out.append("\n")
            continue
        out.append(char if depth == 0 else ("\n" if char == "\n" else " "))
    return "".join(out)


def _sent_payloads() -> list[tuple[str, str, set[str]]]:
    """Collect (file, command, top-level keys) for every static callWS payload."""
    payloads: list[tuple[str, str, set[str]]] = []
    for js_file in sorted(FRONTEND_SRC.rglob("*.js")):
        if "__tests__" in js_file.parts:
            continue
        source = js_file.read_text()
        for match in _CALL_RE.finditer(source):
            body = _object_literal(source, source.index("{", match.start()))
            flat = _top_level(body)
            type_match = _TYPE_RE.search(flat)
            if type_match is None:
                continue  # dynamic or non-Selora command — nothing to check
            keys = {name or quoted for quoted, name in _KEY_RE.findall(flat)}
            for spread in _SPREAD_RE.findall(body):
                keys |= {name or quoted for quoted, name in _KEY_RE.findall(spread)}
            payloads.append(
                (
                    str(js_file.relative_to(FRONTEND_SRC)),
                    type_match.group(1),
                    keys | _BASE_KEYS,
                )
            )
    return payloads


def test_frontend_payloads_match_websocket_schemas() -> None:
    """No panel payload may carry a key its command's schema rejects."""
    allowed = _allowed_keys()
    problems: list[str] = []
    for js_file, command, keys in _sent_payloads():
        if command not in allowed:
            problems.append(f"{js_file}: unknown command {command}")
            continue
        extra = keys - allowed[command]
        if extra:
            problems.append(f"{js_file}: {command} sends {sorted(extra)} — not in schema")
    assert not problems, "panel/websocket contract drift:\n" + "\n".join(problems)


def test_contract_scan_finds_the_expected_surface() -> None:
    """Guard the scanner itself: a silent parse failure must not pass as clean."""
    payloads = _sent_payloads()
    assert len(payloads) > 100
    commands = {command for _, command, _ in payloads}
    assert "selora_ai/update_automation_yaml" in commands


@pytest.mark.parametrize(
    "command",
    [
        "selora_ai/update_automation_yaml",
        "selora_ai/apply_automation_yaml",
    ],
)
def test_refinement_flag_is_accepted(command: str) -> None:
    """The accept-a-refinement path sends `preserve_enabled_state`."""
    assert "preserve_enabled_state" in _allowed_keys()[command]


def test_accept_refinement_message_validates() -> None:
    """End-to-end: the exact message the panel sends passes the real schema."""
    message: dict[str, Any] = {
        "id": 1,
        "type": "selora_ai/update_automation_yaml",
        "automation_id": "selora_ai_x",
        "yaml_text": "alias: X\n",
        "session_id": "session-1",
        "version_message": "Refined via chat",
        "preserve_enabled_state": True,
    }
    schema = _handle_websocket_update_automation_yaml._ws_schema
    assert schema(message)["preserve_enabled_state"] is True
