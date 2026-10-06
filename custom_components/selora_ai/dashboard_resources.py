"""List, add, change and remove Lovelace dashboard resources (the JS custom cards load).

A ``custom:`` card renders only once its JavaScript is registered as a
resource, so building a dashboard on community cards needs this as much as it
needs the cards. A resource is code that runs in EVERY user's browser with
their Home Assistant session, so where it comes from decides what it takes:

* a same-origin path (``/hacsfiles/…``, ``/local/…``) is a file already on the
  hub, put there by HACS or the user — added directly;
* an external ``https://`` URL is third-party code — it needs
  ``confirmed: true``, the way a risky service call does over MCP, unless the
  install turned the approval requirement off;
* anything else (``http:``, ``data:``, ``javascript:``, a protocol-relative
  ``//host``) is refused.

Changing one (a card's new version at a new path, or a different type) goes
through the same gate as adding — a new external URL is new code — and keeps
the resource's id, so there is no window in which the cards using it break.

Resources under ``/selora_ai_resources/`` belong to recipes, which download,
verify and prune them (``recipes/resources.py``); they are listed but neither
added nor removed here. Adding shares the recipes' install lock, because Home
Assistant does not deduplicate resources by URL and a module loaded twice
throws in the browser.
"""

from __future__ import annotations

import contextlib
import re
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlparse

from .helpers import sanitize_untrusted_text
from .recipes.resources import (
    _INSTALL_LOCK,
    RESOURCE_URL_BASE,
    _lovelace_resources,
    _registered_items,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# What a card resource is. ``html`` is in Home Assistant's list too but is a
# deprecated HTML-import format no current card ships.
_RESOURCE_TYPES: Final = ("module", "js", "css")

_YAML_MODE_NOTE: Final = (
    "This home's dashboard resources are defined in YAML (lovelace: resources: in "
    "configuration.yaml), so they cannot be changed from here — edit them there."
)


# A same-origin path as a browser will NOT reinterpret: plain path characters
# only. A backslash is read as a slash (`/\evil.example/x.js` loads from
# evil.example), `%2e` as a dot, and `.`/`..` segments and `//` move the
# resolved path — each would let an external or recipe-owned URL pass as an
# ordinary local one, so the string checked is the string the browser loads.
_LOCAL_PATH_RE: Final = re.compile(r"/[A-Za-z0-9_~.\-]+(?:/[A-Za-z0-9_~.\-]+)*")


def _local_path_ok(path: str) -> bool:
    return bool(_LOCAL_PATH_RE.fullmatch(path)) and not any(
        segment in (".", "..") for segment in path.split("/")
    )


def _bare(url: str) -> str:
    """The URL without its query string — the cache-buster HACS appends."""
    return url.split("?", 1)[0].split("#", 1)[0]


def _is_recipe_managed(url: str) -> bool:
    return _bare(url).startswith(f"{RESOURCE_URL_BASE}/")


def _classify_url(url: str) -> tuple[str | None, str | None]:
    """``("local" | "external", None)``, or ``(None, why it is refused)``."""
    if not url or any(c.isspace() or c == "\\" or not c.isprintable() for c in url):
        return None, "A resource URL cannot be empty or contain spaces or backslashes."
    if url.startswith("/"):
        if _local_path_ok(_bare(url)):
            return "local", None
        return None, (
            "A path on this Home Assistant must be plain: letters, digits, '-', '_', "
            "'.' and '/', with no '..', '%' or '//'."
        )
    try:
        parsed = urlparse(url)
        external = parsed.scheme == "https" and bool(parsed.hostname)
    except ValueError:
        # An unbalanced IPv6 bracket ("https://[") raises rather than parses.
        external = False
    if external:
        return "external", None
    return None, (
        "Use a path on this Home Assistant (e.g. /hacsfiles/button-card/button-card.js "
        "or /local/my-card.js) or an https:// URL."
    )


async def _storage_collection(hass: HomeAssistant) -> tuple[Any | None, str | None]:
    from homeassistant.components.lovelace.resources import (  # noqa: PLC0415
        ResourceStorageCollection,
    )

    resources = _lovelace_resources(hass)
    if resources is None:
        return None, "Dashboards are not set up on this Home Assistant yet."
    if not isinstance(resources, ResourceStorageCollection):
        return None, _YAML_MODE_NOTE
    return resources, None


def _describe(item: dict[str, Any]) -> dict[str, Any]:
    url = str(item.get("url") or "")
    return {
        "id": str(item.get("id") or ""),
        "url": sanitize_untrusted_text(url, 300),
        "type": str(item.get("type") or ""),
        "managed_by_recipe": _is_recipe_managed(url),
    }


async def async_list_resources(hass: HomeAssistant) -> dict[str, Any]:
    """Every registered resource, and whether this home's are editable."""
    from homeassistant.components.lovelace.resources import (  # noqa: PLC0415
        ResourceStorageCollection,
    )

    resources = _lovelace_resources(hass)
    if resources is None:
        return {"error": "Dashboards are not set up on this Home Assistant yet."}
    items = await _registered_items(resources)
    return {
        "editable": isinstance(resources, ResourceStorageCollection),
        "resources": [_describe(i) for i in items],
    }


async def async_add_resource(
    hass: HomeAssistant,
    url: str,
    resource_type: str = "module",
    *,
    confirmed: bool = False,
) -> dict[str, Any]:
    """Register a resource; an external one only once the user confirmed it."""
    from homeassistant.exceptions import HomeAssistantError  # noqa: PLC0415
    import voluptuous as vol  # noqa: PLC0415

    from .command_policy_options import resolve_command_policy_options  # noqa: PLC0415

    url = str(url or "").strip()
    resource_type = str(resource_type or "module").strip().lower()
    if resource_type not in _RESOURCE_TYPES:
        return {"error": f"type must be one of {', '.join(_RESOURCE_TYPES)}."}
    origin, refusal = _classify_url(url)
    if refusal:
        return {"error": refusal}
    if _is_recipe_managed(url):
        return {
            "error": (
                f"{RESOURCE_URL_BASE}/ is where recipes keep the cards they install and "
                "verify; it is managed by installing or removing the recipe."
            )
        }
    if (
        origin == "external"
        and not confirmed
        and resolve_command_policy_options(hass).approval_required
    ):
        return {
            "added": False,
            "requires_confirmation": True,
            "url": sanitize_untrusted_text(url, 300),
            "reason": (
                "This loads third-party code into every user's browser with their Home "
                "Assistant session. Ask the user to confirm, then call again with "
                "confirmed=true. A card installed through HACS is served from "
                "/hacsfiles/ and needs no confirmation."
            ),
        }

    resources, error = await _storage_collection(hass)
    if error:
        return {"error": error}
    async with _INSTALL_LOCK:
        existing = next(
            (
                i
                for i in await _registered_items(resources)
                if _bare(str(i.get("url"))) == _bare(url)
            ),
            None,
        )
        if existing is not None:
            return {
                "error": (
                    f"{sanitize_untrusted_text(url, 300)} is already registered (id "
                    f"{existing.get('id')}). Loading it twice breaks the card."
                )
            }
        try:
            item = await resources.async_create_item({"res_type": resource_type, "url": url})
        except (vol.Invalid, HomeAssistantError, ValueError) as exc:
            return {"error": f"Home Assistant refused the resource: {exc}"}
    return {
        "added": True,
        **_describe(item),
        "note": "Browsers load resources at page load: reload the dashboard to use it.",
    }


async def async_remove_resource(hass: HomeAssistant, ref: str) -> dict[str, Any]:
    """Unregister a resource by id or URL. Cards using it stop rendering."""
    from homeassistant.helpers.collection import ItemNotFound  # noqa: PLC0415

    ref = str(ref or "").strip()
    if not ref:
        return {"error": "Pass the resource's id or URL, from list_dashboard_resources."}
    resources, error = await _storage_collection(hass)
    if error:
        return {"error": error}
    async with _INSTALL_LOCK:
        items = await _registered_items(resources)
        item = next((i for i in items if str(i.get("id")) == ref), None) or next(
            (i for i in items if _bare(str(i.get("url"))) == _bare(ref)), None
        )
        if item is None:
            return {"error": "No such resource. Call list_dashboard_resources."}
        if _is_recipe_managed(str(item.get("url"))):
            return {
                "error": (
                    "That resource belongs to an installed recipe; remove the recipe to remove it."
                )
            }
        # Gone between the read and the delete: what was asked for is true.
        with contextlib.suppress(ItemNotFound):
            await resources.async_delete_item(item["id"])
    return {
        "removed": True,
        **_describe(item),
        "note": "Cards that use it show 'Custom element doesn't exist' until it is re-added.",
    }


async def async_update_resource(
    hass: HomeAssistant,
    ref: str,
    *,
    url: str | None = None,
    resource_type: str | None = None,
    confirmed: bool = False,
) -> dict[str, Any]:
    """Point a resource at a new URL and/or type, in place.

    In place rather than remove-then-add: between the two, every card using it
    renders "Custom element doesn't exist". A new URL passes the gate an added
    one does; an external one is new third-party code, so it is confirmed.
    """
    from homeassistant.exceptions import HomeAssistantError  # noqa: PLC0415
    import voluptuous as vol  # noqa: PLC0415

    from .command_policy_options import resolve_command_policy_options  # noqa: PLC0415

    ref = str(ref or "").strip()
    new_url = str(url or "").strip() or None
    new_type = str(resource_type or "").strip().lower() or None
    if not ref:
        return {"error": "Pass the resource's id or URL, from list_dashboard_resources."}
    if new_url is None and new_type is None:
        return {"error": "Pass the new url and/or type."}
    if new_type is not None and new_type not in _RESOURCE_TYPES:
        return {"error": f"type must be one of {', '.join(_RESOURCE_TYPES)}."}
    origin = None
    if new_url is not None:
        origin, refusal = _classify_url(new_url)
        if refusal:
            return {"error": refusal}
        if _is_recipe_managed(new_url):
            return {"error": f"{RESOURCE_URL_BASE}/ is managed by installing recipes."}

    resources, error = await _storage_collection(hass)
    if error:
        return {"error": error}
    async with _INSTALL_LOCK:
        items = await _registered_items(resources)
        item = next((i for i in items if str(i.get("id")) == ref), None) or next(
            (i for i in items if _bare(str(i.get("url"))) == _bare(ref)), None
        )
        if item is None:
            return {"error": "No such resource. Call list_dashboard_resources."}
        if _is_recipe_managed(str(item.get("url"))):
            return {
                "error": "That resource belongs to an installed recipe; it changes with the recipe."
            }
        changes: dict[str, Any] = {}
        if new_url is not None and new_url != item.get("url"):
            clash = next(
                (
                    i
                    for i in items
                    if i.get("id") != item.get("id") and _bare(str(i.get("url"))) == _bare(new_url)
                ),
                None,
            )
            if clash is not None:
                return {
                    "error": (
                        f"{sanitize_untrusted_text(new_url, 300)} is already registered (id "
                        f"{clash.get('id')}). Loading it twice breaks the card."
                    )
                }
            changes["url"] = new_url
        if new_type is not None and new_type != item.get("type"):
            changes["res_type"] = new_type
        if not changes:
            return {"updated": False, **_describe(item), "note": "Nothing to change."}
        if (
            "url" in changes
            and origin == "external"
            and not confirmed
            and resolve_command_policy_options(hass).approval_required
        ):
            return {
                "updated": False,
                "requires_confirmation": True,
                "url": sanitize_untrusted_text(new_url or "", 300),
                "reason": (
                    "This points the resource at third-party code that every user's "
                    "browser runs with their Home Assistant session. Ask the user to "
                    "confirm, then call again with confirmed=true."
                ),
            }
        try:
            updated = await resources.async_update_item(item["id"], changes)
        except (vol.Invalid, HomeAssistantError, ValueError) as exc:
            return {"error": f"Home Assistant refused the change: {exc}"}
    return {
        "updated": True,
        **_describe(updated),
        "previous_url": sanitize_untrusted_text(str(item.get("url") or ""), 300),
        "note": "Browsers load resources at page load: reload the dashboard to use it.",
    }
