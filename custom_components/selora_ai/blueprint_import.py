"""Import a blueprint from a URL, or delete one — each after a confirmation.

Importing fetches YAML from a URL and writes it into the config directory, and
the URL may have been chosen by a model from a page it was asked to read. So:

* **Home Assistant's own importer does the fetching**, and its schema validates
  the result.
* **Only through the readers it has for known hosts** — the community forum,
  GitHub, gists, the HA website — over https, each called directly. Its generic
  reader fetches whatever it is given, following redirects, and a hostname
  checked here is resolved again when fetched: no check made beforehand stops a
  public-looking URL reaching a router or another device's admin page. So other
  hosts, and URLs a host's reader does not recognise, are refused.
* **Validated with the domain's own schema** before the preview, so what is
  saved is a blueprint Home Assistant will load.
* **The first call writes nothing**: it returns what the blueprint is, where it
  would go, and a hash of its text. The confirmed call fetches again and writes
  only if the text still has that hash — what the user agreed to is what lands.
  An existing file is never overwritten.

Deleting goes through the blueprint store, which refuses a blueprint still in
use; the first call names what uses it, or asks for the confirmation.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Any, Final

import aiohttp
from homeassistant.exceptions import HomeAssistantError
import yarl

from .blueprint_manager import _domain_blueprints
from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# The hosts Home Assistant's importer has a dedicated reader for.
_KNOWN_HOSTS: Final = frozenset(
    {
        "community.home-assistant.io",
        "github.com",
        "raw.githubusercontent.com",
        "gist.github.com",
        "www.home-assistant.io",
    }
)
_CONFIRM: Final = "Tell the user, and only once they agree call again with confirmed=true"


def _text_hash(text: str) -> str:
    # Whole: the source chooses the text, and a truncated digest is short
    # enough to find two texts sharing it.
    return hashlib.sha256(text.encode()).hexdigest()


def _url_error(url: str) -> str | None:
    """Why *url* may not be fetched, or None."""
    try:
        parsed = yarl.URL(url)
        # A malformed IDNA name parses, then fails when its host is read.
        host = (parsed.host or "").lower()
    except (ValueError, UnicodeError):
        return "That is not a URL."
    if parsed.scheme != "https" or host not in _KNOWN_HOSTS:
        return (
            "Blueprints are imported from the Home Assistant community forum, GitHub, "
            "gists or the Home Assistant website, over https. Download one from "
            "elsewhere and add it as a file instead."
        )
    return None


async def _fetch(hass: HomeAssistant, url: str) -> Any:
    """Fetch with the host's own reader only.

    Not ``fetch_blueprint_from_url``: for a URL its readers do not recognise —
    even on an allowed host — it falls through to the generic reader, which
    follows redirects anywhere. A reader that does not recognise the URL raises
    ``UnsupportedUrl``.
    """
    from homeassistant.components.blueprint import importer  # noqa: PLC0415

    host = (yarl.URL(url).host or "").lower()
    reader = {
        "community.home-assistant.io": importer.fetch_blueprint_from_community_post,
        "github.com": importer.fetch_blueprint_from_github_url,
        "raw.githubusercontent.com": importer.fetch_blueprint_from_github_url,
        "gist.github.com": importer.fetch_blueprint_from_github_gist_url,
        "www.home-assistant.io": importer.fetch_blueprint_from_website_url,
    }[host]
    imported = await reader(hass, url)
    imported.blueprint.update_metadata(source_url=url)
    return imported


async def async_import_blueprint(
    hass: HomeAssistant,
    url: str,
    *,
    confirmed: bool = False,
    content_hash: str | None = None,
) -> dict[str, Any]:
    """Preview a blueprint from *url*, or save it once confirmed."""
    url = str(url or "").strip()
    shown_url = sanitize_untrusted_text(url, 300)
    if error := _url_error(url):
        return {"error": error}
    if not _domain_blueprints(hass):
        return {"error": "Blueprints are not set up on this install."}
    try:
        imported = await asyncio.wait_for(_fetch(hass, url), 30)
    except TimeoutError:
        return {"error": "The blueprint could not be fetched in time."}
    except (HomeAssistantError, ValueError, AssertionError, OSError, aiohttp.ClientError) as exc:
        # The importer asserts the YAML is a mapping and raises on bad schema.
        return {
            "error": f"That is not a blueprint Home Assistant can import: {sanitize_untrusted_text(str(exc), 200)}"
        }

    domain = imported.blueprint.domain
    store = _domain_blueprints(hass).get(domain)
    if store is None:
        return {"error": f"This install has no {sanitize_untrusted_text(domain, 40)} blueprints."}
    # The importer checks only the generic blueprint schema, and the store
    # writes what it is given; its own domain schema (an automation blueprint
    # needs triggers and actions) is applied only when it loads the file.
    # Applied here, so what is saved is a blueprint that loads.
    try:
        blueprint = _domain_checked(imported.blueprint, store)
    except (HomeAssistantError, ValueError) as exc:
        return {
            "error": f"That is not a usable {domain} blueprint: {sanitize_untrusted_text(str(exc), 200)}"
        }
    path = f"{imported.suggested_filename}.yaml"
    try:
        exists = bool(await store.async_get_blueprint(path))
    except HomeAssistantError:
        exists = False
    if exists:
        return {
            "error": (
                f"A {domain} blueprint already exists at {sanitize_untrusted_text(path, 120)}. "
                "Delete it first to replace it."
            )
        }
    digest = _text_hash(imported.raw_data)
    summary = {
        "domain": domain,
        "path": path,
        "name": sanitize_untrusted_text(str(blueprint.name or path), 80),
        "description": sanitize_untrusted_text(
            str((blueprint.metadata or {}).get("description") or ""), 400
        ),
        "inputs": sorted((blueprint.inputs or {}).keys()),
        "source_url": shown_url,
        "content_hash": digest,
    }
    if not confirmed:
        return {
            "requires_confirmation": True,
            **summary,
            "hint": (
                f"This saves a {domain} blueprint from {shown_url}: its actions run as "
                f"written by its author. {_CONFIRM} and this content_hash."
            ),
        }
    if content_hash != digest:
        return {
            "error": (
                "The blueprint at that URL is not the one shown for confirmation (or no "
                "content_hash was passed). Ask again without confirmed to see it."
            )
        }
    try:
        await store.async_add_blueprint(blueprint, path, allow_override=False)
    except (HomeAssistantError, OSError) as exc:
        return {"error": f"It was not saved: {sanitize_untrusted_text(str(exc), 200)}"}
    return {"status": "imported", **summary}


def _domain_checked(blueprint: Any, store: Any) -> Any:
    """The blueprint, validated with the store's own domain schema."""
    from homeassistant.components.blueprint.models import Blueprint  # noqa: PLC0415

    schema = getattr(store, "_blueprint_schema", None)
    if schema is None:
        return blueprint
    return Blueprint(blueprint.data, expected_domain=store.domain, schema=schema)


def _users(hass: HomeAssistant, domain: str, path: str) -> list[str]:
    """Automations or scripts built on the blueprint at *path*."""
    try:
        if domain == "automation":
            from homeassistant.components.automation import (  # noqa: PLC0415
                automations_with_blueprint,
            )

            return list(automations_with_blueprint(hass, path))
        if domain == "script":
            from homeassistant.components.script import scripts_with_blueprint  # noqa: PLC0415

            return list(scripts_with_blueprint(hass, path))
        if domain == "template":
            from homeassistant.components.template.helpers import (  # noqa: PLC0415
                templates_with_blueprint,
            )

            return list(templates_with_blueprint(hass, path))
    except (ImportError, KeyError):
        return []
    return []


def _file_exists(folder: Any, path: str) -> bool:
    """Whether *path* names a file inside *folder* (no escaping it)."""
    root = folder.resolve()
    target = (folder / path).resolve()
    return target.is_relative_to(root) and target.is_file()


async def async_delete_blueprint(
    hass: HomeAssistant, domain: str, path: str, *, confirmed: bool = False
) -> dict[str, Any]:
    """Delete a blueprint nothing uses, once confirmed."""
    from homeassistant.components.blueprint.errors import BlueprintInUse  # noqa: PLC0415

    domain = str(domain or "").strip()
    path = str(path or "").strip()
    shown = sanitize_untrusted_text(f"{domain}/{path}", 120)
    store = _domain_blueprints(hass).get(domain)
    if store is None or not path:
        return {"error": f"No blueprint {shown}. Call list_blueprints for its domain and path."}
    # By file, not by loading it: a blueprint that fails to parse is one the
    # list reports, and exactly the kind worth removing.
    if not await hass.async_add_executor_job(_file_exists, store.blueprint_folder, path):
        return {"error": f"No blueprint {shown}. Call list_blueprints for its domain and path."}
    if users := _users(hass, domain, path):
        return {
            "error": (
                f"{shown} is used by {', '.join(sorted(users))}. Home Assistant keeps a "
                "blueprint in use; change or delete those first."
            )
        }
    if not confirmed:
        return {
            "requires_confirmation": True,
            "domain": domain,
            "path": path,
            "hint": f"This deletes the {shown} blueprint file. {_CONFIRM}.",
        }
    try:
        await store.async_remove_blueprint(path)
    except BlueprintInUse:
        return {"error": f"{shown} is in use; change or delete what uses it first."}
    except (HomeAssistantError, OSError) as exc:
        return {"error": f"It was not deleted: {sanitize_untrusted_text(str(exc), 200)}"}
    return {"status": "deleted", "domain": domain, "path": path}
