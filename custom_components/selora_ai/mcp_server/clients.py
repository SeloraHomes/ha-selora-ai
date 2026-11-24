"""Which app an MCP call comes from — ChatGPT, Claude, Cursor — for the person.

The server is stateless, so a tool call carries no memory of the ``initialize``
that introduced its client. Each ``initialize`` names the client
(``clientInfo.name``); it is remembered under the credential AND the request's
User-Agent, which tells two apps sharing one Selora account apart. A tool call
looks that pair up, then falls back to recognising the User-Agent itself, then
to the credential's latest client.

Self-reported, so a label and never an identity: a grant binds to the
credential, which the panel shows beside the app name.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING, Final

from ..const import DOMAIN
from ..helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_DATA_KEY: Final = "mcp_clients"
_MAX_REMEMBERED: Final = 200
_MAX_NAME: Final = 40

# Substring of a client name or User-Agent → the name people know. Ordered:
# "claude-code" before "claude".
_KNOWN: Final = (
    ("openai", "ChatGPT"),
    ("chatgpt", "ChatGPT"),
    ("claude-code", "Claude Code"),
    ("claude code", "Claude Code"),
    ("claude", "Claude"),
    ("anthropic", "Claude"),
    ("cursor", "Cursor"),
    ("windsurf", "Windsurf"),
    ("copilot", "GitHub Copilot"),
    ("visual studio code", "VS Code"),
    ("vscode", "VS Code"),
    ("gemini", "Gemini"),
    ("mistral", "Le Chat"),
    ("le chat", "Le Chat"),
    ("n8n", "n8n"),
)


def _known(raw: str) -> str | None:
    lowered = raw.lower()
    return next((name for needle, name in _KNOWN if needle in lowered), None)


def friendly_client(raw: object) -> str | None:
    """A client's name as people know it, or its own (sanitized) name."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    return _known(raw) or sanitize_untrusted_text(raw.strip(), _MAX_NAME) or None


def _memory(hass: HomeAssistant) -> OrderedDict[tuple[str, str], str]:
    return hass.data.setdefault(DOMAIN, {}).setdefault(_DATA_KEY, OrderedDict())


def remember(
    hass: HomeAssistant, credential_key: str, user_agent: str, client_name: object
) -> None:
    """Note the client an ``initialize`` introduced."""
    if (name := friendly_client(client_name)) is None:
        return
    memory = _memory(hass)
    memory.pop((credential_key, user_agent), None)
    memory[(credential_key, user_agent)] = name
    while len(memory) > _MAX_REMEMBERED:
        memory.popitem(last=False)


def recall(hass: HomeAssistant, credential_key: str, user_agent: str) -> str | None:
    """The app this credential is being used from, if anything says."""
    memory = _memory(hass)
    if name := memory.get((credential_key, user_agent)):
        return name
    if user_agent and (name := _known(user_agent)):
        return name
    latest = [n for (key, _), n in memory.items() if key == credential_key]
    return latest[-1] if latest else None
