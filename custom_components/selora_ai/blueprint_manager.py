"""Blueprint reads.

A blueprint is a parameterised automation or script template: the author writes
the triggers and actions once with named ``!input`` placeholders, and a user
supplies the inputs. It is one of the most common ways a Home Assistant home
gets its automations, and nothing here could see one.

Reachable in-process, unlike helpers or dashboard entries: ``hass.data["blueprint"]``
is a ``dict[domain, DomainBlueprints]`` published by the automation and script
components, which is the same object the websocket API serves from.

Reads only. Importing a blueprint means fetching YAML from a URL and writing it
to the config directory, and a URL an LLM chose — possibly from a page it was
asked to summarise — is a different risk class from the registry edits alongside
this. That belongs behind a confirmation card showing the source, and is left
out rather than done quietly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# The domains that publish blueprints. Read off hass.data rather than assumed,
# but named here so an empty result can say which were even looked at.
_KNOWN_DOMAINS: Final = ("automation", "script", "template")

_MAX_LISTED: Final = 40


def _domain_blueprints(hass: HomeAssistant) -> dict[str, Any]:
    """The per-domain blueprint stores, or ``{}`` before they are set up."""
    data = hass.data.get("blueprint")
    return data if isinstance(data, dict) else {}


async def async_list_blueprints(hass: HomeAssistant, domain: str | None = None) -> dict[str, Any]:
    """Installed blueprints, with the inputs each one asks for.

    The inputs are the point: a caller that knows a blueprint exists still
    cannot use it without knowing what it wants filled in, and a second
    round-trip per blueprint to find out is worse than carrying them here.
    """
    stores = _domain_blueprints(hass)
    if not stores:
        return {
            "blueprints": [],
            "count": 0,
            "note": "Blueprints are not set up on this install.",
        }

    wanted = [str(domain).strip()] if domain and str(domain).strip() else sorted(stores)
    records: list[dict[str, Any]] = []
    for one in wanted:
        store = stores.get(one)
        if store is None:
            continue
        for path, blueprint in (await store.async_get_blueprints()).items():
            # A folder can hold a blueprint that fails to parse; the store
            # reports it as the exception rather than raising, and dropping it
            # silently would leave the user wondering where their file went.
            if not hasattr(blueprint, "metadata"):
                records.append(
                    {
                        "domain": one,
                        "path": path,
                        "error": "This blueprint could not be loaded.",
                    }
                )
                continue
            records.append(
                {
                    "domain": one,
                    "path": path,
                    "name": sanitize_untrusted_text(str(blueprint.name or path), 80),
                    "inputs": sorted(blueprint.inputs or {}),
                }
            )

    result: dict[str, Any] = {
        "blueprints": records[:_MAX_LISTED],
        "count": len(records),
        "domains_searched": wanted,
    }
    if len(records) > _MAX_LISTED:
        result["blueprints_omitted"] = len(records) - _MAX_LISTED
    return result


async def async_get_blueprint(hass: HomeAssistant, domain: str, path: str) -> dict[str, Any]:
    """One blueprint's inputs in full — name, description, selector, default.

    ``use_blueprint.input`` must name inputs this blueprint declares, and an
    input without a default is required; both facts live only here, so a caller
    composing an automation has to read this first or it is guessing.
    """
    domain = str(domain or "").strip()
    path = str(path or "").strip()
    if not domain or not path:
        return {"error": "A blueprint domain and path are required."}

    store = _domain_blueprints(hass).get(domain)
    if store is None:
        return {
            "error": (
                f"No blueprints for '{sanitize_untrusted_text(domain, 40)}'. "
                f"Try one of: {', '.join(_KNOWN_DOMAINS)}."
            )
        }

    try:
        blueprint = await store.async_get_blueprint(path)
    except Exception as exc:  # noqa: BLE001 — a bad path must not raise at the caller
        return {"error": f"Could not read that blueprint: {exc}"}

    inputs: dict[str, Any] = {}
    for name, spec in (blueprint.inputs or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        inputs[name] = {
            "name": sanitize_untrusted_text(str(spec.get("name") or name), 60),
            "description": sanitize_untrusted_text(str(spec.get("description") or ""), 200),
            # The selector says what SHAPE the value takes — an entity id, a
            # number, a duration — which is the difference between a working
            # automation and one HA rejects at reload.
            "selector": spec.get("selector"),
            "required": "default" not in spec,
        }
        if "default" in spec:
            inputs[name]["default"] = spec["default"]

    return {
        "domain": domain,
        "path": path,
        "name": sanitize_untrusted_text(str(blueprint.name or path), 80),
        "description": sanitize_untrusted_text(
            str((blueprint.metadata or {}).get("description") or ""), 400
        ),
        "inputs": inputs,
    }
