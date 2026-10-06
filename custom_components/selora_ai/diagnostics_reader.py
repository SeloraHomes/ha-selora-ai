"""System health, and one integration's diagnostics — Home Assistant's own.

``check_system`` answers "is anything wrong?" from integrations, repairs,
updates and backups. These answer the next question, the way the user would
look it up in Settings:

* **System health** is Settings → System → Repairs → System information: the
  core version and install, and each integration's own section (recorder
  database, Supervisor, cloud …), gathered by Home Assistant's
  ``system_health``, each check awaited with a time limit.
* **Diagnostics** is an integration's "Download diagnostics": the data its own
  diagnostics platform returns, already redacted by the integration for
  sharing in a bug report. Credential-like keys are redacted again here, since
  not every integration is careful, and a large dump is narrowed by
  ``fields`` rather than cut mid-structure.

Both are admin-only, as Home Assistant's own are.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import json
import re
from typing import TYPE_CHECKING, Any, Final

from homeassistant.exceptions import HomeAssistantError

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_HEALTH_TIMEOUT: Final = 20
_MAX_CHARS: Final = 30000
_MAX_FIELDS_LISTED: Final = 50
_SECRET_KEY: Final = re.compile(
    r"token|password|passcode|secret|api_?key|private|credential|auth", re.I
)
_REDACTED: Final = "**REDACTED**"


def _plain(value: Any, depth: int = 0) -> Any:
    """JSON-safe, bounded, credential-like keys redacted."""
    if depth > 12:
        return "…"
    if isinstance(value, Mapping):
        return {
            str(k): (_REDACTED if _SECRET_KEY.search(str(k)) else _plain(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [_plain(v, depth + 1) for v in value]
    if isinstance(value, bool | int | float) or value is None:
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return sanitize_untrusted_text(value, 500)


async def _health_value(value: Any) -> Any:
    if not asyncio.iscoroutine(value):
        return value
    try:
        return await asyncio.wait_for(value, _HEALTH_TIMEOUT)
    except TimeoutError:
        return "Timed out"
    except (HomeAssistantError, OSError, ValueError) as exc:
        return f"Failed: {sanitize_untrusted_text(str(exc), 200)}"


async def _registrations(hass: HomeAssistant) -> list[tuple[str, dict[str, Any]]]:
    """Each integration's health section, values still pending where they are
    checks (a server reachable, a disk read), as Home Assistant registers them."""
    from homeassistant.components import system_health  # noqa: PLC0415

    gather = getattr(system_health, "_registered_domain_data", None)
    if gather is not None:
        return [(domain, data) async for domain, data in gather(hass)]
    # HA 2025.1: the registrations, as its websocket reads them.
    return [
        (domain, await system_health.get_integration_info(hass, registration))
        for domain, registration in sorted(hass.data.get("system_health", {}).items())
    ]


async def async_system_health(hass: HomeAssistant) -> dict[str, Any]:
    """Every integration's system health section.

    Each pending check is awaited on its own, with its own limit and all at
    once: one integration whose check never answers reads "Timed out" beside
    everyone else's answers, rather than costing them all.
    """
    if "system_health" not in hass.config.components:
        return {"error": "System health is not set up on this Home Assistant."}
    sections = await _registrations(hass)
    keys = [
        (domain, key, value)
        for domain, data in sections
        for key, value in (data.get("info") or {}).items()
    ]
    values = await asyncio.gather(*(_health_value(value) for _, _, value in keys))
    info: dict[str, dict[str, Any]] = {domain: {} for domain, _ in sections}
    for (domain, key, _), value in zip(keys, values, strict=True):
        if isinstance(value, dict) and value.get("type") == "failed":
            value = f"Failed: {value.get('error', 'unknown')}"
        info[domain][key] = value
    return {"system_health": _plain(info)}


async def async_integration_diagnostics(
    hass: HomeAssistant,
    entry_id: str,
    *,
    device: str | None = None,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """An integration's diagnostics for one config entry (or one of its devices)."""
    from homeassistant.helpers import device_registry as dr  # noqa: PLC0415
    from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415

    entry = hass.config_entries.async_get_entry(str(entry_id or "").strip())
    if entry is None:
        return {"error": "No such integration entry. Call list_integrations for its entry_id."}
    data_store = hass.data.get("diagnostics")
    platform = getattr(data_store, "platforms", {}).get(entry.domain)
    if platform is None:
        return {"error": f"{entry.domain} has no diagnostics to download."}

    try:
        if device:
            found = dr.async_get(hass).async_get(str(device).strip())
            if found is None or entry.entry_id not in found.config_entries:
                return {"error": "That device does not belong to this integration entry."}
            if platform.device_diagnostics is None:
                return {"error": f"{entry.domain} has no device diagnostics."}
            data = await platform.device_diagnostics(hass, entry, found)
        else:
            if platform.config_entry_diagnostics is None:
                return {"error": f"{entry.domain} has no diagnostics for an entry."}
            data = await platform.config_entry_diagnostics(hass, entry)
    except (HomeAssistantError, OSError, ValueError, KeyError) as exc:
        return {
            "error": f"{entry.domain} could not gather its diagnostics: "
            f"{sanitize_untrusted_text(str(exc), 200)}"
        }

    plain = _plain(data if isinstance(data, Mapping) else {"data": data})
    if fields:
        missing = [f for f in fields if f not in plain]
        if missing:
            return {
                "error": f"No top-level fields {missing}.",
                "fields": sorted(plain),
            }
        plain = {k: plain[k] for k in fields}
    issues = [
        issue_id for (domain, issue_id) in ir.async_get(hass).issues if domain == entry.domain
    ]
    result: dict[str, Any] = {
        "domain": entry.domain,
        "entry_id": entry.entry_id,
        **({"device_id": device} if device else {}),
        **({"repairs": issues} if issues else {}),
    }
    encoded = json.dumps(plain)
    if len(encoded) > _MAX_CHARS:
        sizes = sorted(((k, len(json.dumps(v))) for k, v in plain.items()), key=lambda kv: -kv[1])
        return {
            **result,
            "too_large": True,
            # The largest parts, bounded: a dump with thousands of top-level keys
            # must not make this summary the oversized answer instead.
            "fields": dict(sizes[:_MAX_FIELDS_LISTED]),
            **(
                {"fields_omitted": len(sizes) - _MAX_FIELDS_LISTED}
                if len(sizes) > _MAX_FIELDS_LISTED
                else {}
            ),
            "hint": (
                f"The diagnostics are {len(encoded)} characters; ask again with `fields` "
                "naming the top-level parts needed (their sizes are listed)."
            ),
        }
    return {**result, "diagnostics": plain}
