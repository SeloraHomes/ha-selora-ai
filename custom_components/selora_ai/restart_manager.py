"""Restart Home Assistant on request: config checked first, then confirmed.

A restart is how YAML edits and newly installed integrations take effect, so a
caller who made either over MCP needs one — and ``homeassistant.restart`` is on
the service denylist, as an action the generic tool must not reach. This is the
dedicated path:

* **The configuration is checked first**, with Home Assistant's own check. HA's
  restart service runs the same check and refuses on an invalid config, but
  only by raising after the call: a client that fired it would not learn why.
  Here the errors come back before anything happens.
* **A database upgrade in progress refuses it**, as HA does.
* **It asks first** — every automation stops and every connection drops,
  including the one the request came in on.
* **The restart is started, not awaited**: Home Assistant stops under the call,
  so the answer has to leave before it does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def _config_errors(hass: HomeAssistant) -> str | None:
    from homeassistant.config import async_check_ha_config_file  # noqa: PLC0415

    return await async_check_ha_config_file(hass)


def _migrating(hass: HomeAssistant) -> bool:
    # The helper module, not the recorder component: it exists on every
    # supported core and answers False when the recorder is not set up.
    from homeassistant.helpers.recorder import async_migration_in_progress  # noqa: PLC0415

    return bool(async_migration_in_progress(hass))


async def async_restart(hass: HomeAssistant, *, confirmed: bool = False) -> dict[str, Any]:
    """Check the configuration, then — once confirmed — restart."""
    if _migrating(hass):
        return {"error": "A database upgrade is in progress; Home Assistant cannot restart now."}
    try:
        errors = await _config_errors(hass)
    except HomeAssistantError as exc:
        errors = str(exc)
    if errors:
        from .config_yaml import _redact_problem  # noqa: PLC0415

        return {
            "error": "The configuration is not valid, so Home Assistant would not restart.",
            # Redacted as the YAML editor's check is: HA's message can quote the
            # rejected value, and that value can be a password.
            "config_errors": sanitize_untrusted_text(_redact_problem(errors), 2000),
        }
    if not confirmed:
        return {
            "requires_confirmation": True,
            "config_valid": True,
            "hint": (
                "The configuration is valid. Restarting stops every automation for about "
                "a minute and drops every connection, this one included. Tell the user, "
                "and only once they agree call again with confirmed=true."
            ),
        }
    await hass.services.async_call("homeassistant", "restart", {}, blocking=False)
    return {
        "status": "restarting",
        "hint": "Home Assistant is restarting; reconnect in a minute or two.",
    }
