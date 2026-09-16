"""Resolution of the entry options that relax the command policy.

Both default to fully-enforced and are read LIVE on every command turn,
the way ``telemetry.is_enabled`` reads its toggle: there is no reload to
forget and no cached copy to go stale between the option landing on the
entry and the next request.

Kept in its own module rather than in ``llm_client/command_policy`` so
``__init__._collect_entity_states`` can ask the same question without
importing the policy package, and so the resolution has one
implementation for the JSON path, the tool path and the snapshot filter
to share.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .const import (
    CONF_COMMAND_ALLOWLIST_ENABLED,
    CONF_COMMAND_APPROVAL_REQUIRED,
    CONF_COMMAND_HANDLERS_ENABLED,
    DEFAULT_COMMAND_ALLOWLIST_ENABLED,
    DEFAULT_COMMAND_APPROVAL_REQUIRED,
    DEFAULT_COMMAND_HANDLERS_ENABLED,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@dataclass(frozen=True, slots=True)
class CommandPolicyOptions:
    """Which parts of the command policy are in force for this install."""

    approval_required: bool = DEFAULT_COMMAND_APPROVAL_REQUIRED
    allowlist_enabled: bool = DEFAULT_COMMAND_ALLOWLIST_ENABLED
    handlers_enabled: bool = DEFAULT_COMMAND_HANDLERS_ENABLED

    @property
    def fully_enforced(self) -> bool:
        """True when NO override is set — the shipped behaviour.

        Every field belongs in this conjunction. It is the question other
        code asks when it wants "is anything relaxed?", so a field left
        out of it keeps the property's name while checking less than the
        name claims — and answers True for an install that is running
        relaxed.
        """
        return self.approval_required and self.allowlist_enabled and self.handlers_enabled


# The answer whenever there is nothing to read: no ``hass``, no config
# entry, a ``hass`` whose internals have moved. Every one of those is a
# caller that cannot establish the user opted out, and the default has
# to be the enforcing one — a policy that fails open is not a policy.
ENFORCED: CommandPolicyOptions = CommandPolicyOptions()


def resolve_command_policy_options(hass: HomeAssistant | None) -> CommandPolicyOptions:
    """Return the command-policy overrides carried by the LLM config entry.

    ``data`` is merged under ``options`` exactly as ``telemetry`` merges
    them, so a harness may set either. An absent key, an absent entry or
    an absent ``hass`` all resolve to :data:`ENFORCED`.

    The entry comes from ``_resolve_llm_entry`` rather than a scan of our
    own: a second "Add entry" that never linked a provider is a supported
    state, and it can sit AHEAD of the configured one. Taking the first
    match reads a stray entry's options instead of the live provider's —
    which either leaves the overrides inert or, worse, applies them from
    an entry nobody configured. Asking the one resolver is what keeps
    this answer and the client's agreeing; a second copy of the
    preference rule drifts the moment either changes.
    """
    if hass is None:
        return ENFORCED
    try:
        from . import _resolve_llm_entry

        entry = _resolve_llm_entry(hass)
    except (AttributeError, ImportError):
        # A stub/partial hass, or an import too early in bootstrap for
        # the package body to be ready. Neither is evidence of an
        # opt-out, and this sits on the command path — it must answer,
        # and it must answer with the enforcing default.
        return ENFORCED
    if entry is None:
        return ENFORCED
    merged = {**entry.data, **entry.options}
    return CommandPolicyOptions(
        approval_required=bool(
            merged.get(CONF_COMMAND_APPROVAL_REQUIRED, DEFAULT_COMMAND_APPROVAL_REQUIRED)
        ),
        allowlist_enabled=bool(
            merged.get(CONF_COMMAND_ALLOWLIST_ENABLED, DEFAULT_COMMAND_ALLOWLIST_ENABLED)
        ),
        handlers_enabled=bool(
            merged.get(CONF_COMMAND_HANDLERS_ENABLED, DEFAULT_COMMAND_HANDLERS_ENABLED)
        ),
    )
