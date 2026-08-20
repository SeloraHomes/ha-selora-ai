"""Category registry reads and writes.

Categories are the registry sibling of labels, with one structural difference
that shapes everything here: they are **scoped**. A category lives under a scope
string — the Home Assistant UI keeps a separate set for its Automations,
Scripts, Scenes and Helpers pages — and an entity carries at most one category
per scope (``RegistryEntry.categories`` is ``{scope: category_id}``). Labels are
a flat set an entity may carry many of; a category is a single-choice filing
within one list.

The scope is free-form server-side: ``category_registry`` validates nothing and
HA's own tests create categories under ``"any"`` and ``"bullshizzle"``. That
matters because a category created under a scope no UI reads exists and appears
nowhere — a silent no-op from the user's point of view — so the tool schema
carries an enum of the scopes the UI actually uses and `list_categories` reports
the scopes already in play.

Deliberately mirrors ``label_manager``: list, create, delete, assign, and no
rename. A category rename is rare and reachable in the UI, and adding one here
that labels do not have would make the two registries diverge for no reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from homeassistant.helpers import category_registry as cr
from homeassistant.helpers import entity_registry as er

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from collections.abc import Iterable

    from homeassistant.core import HomeAssistant

# The scopes Home Assistant's own UI keeps category lists for. Not enforced —
# `async_create` accepts any string, and refusing a scope a future HA release
# adds would be worse than allowing an odd one — but carried on the tool schema
# so the model picks one that a user can actually see.
UI_SCOPES: Final = ("automation", "script", "scene", "helper")

_MAX_LISTED: Final = 60

# The entity domains each UI scope's page actually lists. A category is a filing
# on ONE page, so an entity that page never shows cannot be filed there — the
# mapping is written, the count goes up, and the user sees nothing anywhere.
# `helper` is the odd one: its page lists a family of domains rather than one.
_HELPER_DOMAINS: Final = frozenset(
    {
        "input_boolean",
        "input_button",
        "input_datetime",
        "input_number",
        "input_select",
        "input_text",
        "counter",
        "timer",
        "schedule",
    }
)
_SCOPE_DOMAINS: Final = {
    "automation": frozenset({"automation"}),
    "script": frozenset({"script"}),
    "scene": frozenset({"scene"}),
    "helper": _HELPER_DOMAINS,
}


async def _entities_outside_scope(
    hass: HomeAssistant, scope: str, entity_ids: list[str]
) -> set[str]:
    """Those that could never appear on the scope's page.

    Only the scopes we know the page contents for are policed. A scope outside
    `_SCOPE_DOMAINS` is one HA may have added or the user invented, and refusing
    every entity under it would be worse than the invisible mapping this guards.

    ``helper`` cannot be answered from the entity domain: the storage-collection
    helpers own theirs (`input_boolean.*`), but a template, utility-meter,
    derivative or threshold helper is an ordinary `sensor.*` or
    `binary_sensor.*` that the Helpers page nonetheless lists. Membership comes
    from the config entry's integration declaring `integration_type: helper`,
    which is the same question `helper_overview` asks.
    """
    allowed = _SCOPE_DOMAINS.get(scope)
    if allowed is None:
        return set()
    if scope != "helper":
        return {e for e in entity_ids if e.split(".", 1)[0] not in allowed}

    from .registry_manager import _config_entry_helper_domains  # noqa: PLC0415

    helper_domains = await _config_entry_helper_domains(hass)
    registry = er.async_get(hass)
    outside: set[str] = set()
    for entity_id in entity_ids:
        if entity_id.split(".", 1)[0] in _HELPER_DOMAINS:
            continue
        entry = registry.async_get(entity_id)
        config_entry = (
            hass.config_entries.async_get_entry(entry.config_entry_id)
            if entry is not None and entry.config_entry_id
            else None
        )
        if config_entry is None or config_entry.domain not in helper_domains:
            outside.add(entity_id)
    return outside


def _norm(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def resolve_category(
    hass: HomeAssistant, scope: str, ref: str
) -> tuple[cr.CategoryEntry | None, str | None]:
    """Resolve a category within a scope, by id or name.

    No ambiguity handling, unlike areas and floors: the registry's
    ``_async_ensure_name_is_available`` makes a name unique WITHIN a scope, so
    a name resolves to exactly one category or none. The same name under two
    scopes is two different categories, which is why the scope is required
    rather than searched across.
    """
    scope = str(scope or "").strip()
    ref = str(ref or "").strip()
    if not scope:
        return None, "A category scope is required."
    if not ref:
        return None, "A category name or category_id is required."

    registry = cr.async_get(hass)
    if (entry := registry.async_get_category(scope=scope, category_id=ref)) is not None:
        return entry, None

    candidates = list(registry.async_list_categories(scope=scope))

    # The registry's own uniqueness check is `name.casefold()` and nothing more,
    # so "Outdoor Lights" and "Outdoor  Lights" are two categories a user can
    # genuinely have. Match that first, or the exact name of the second one
    # resolves to the first.
    for candidate in candidates:
        if candidate.name.casefold() == ref.casefold():
            return candidate, None

    # Only then be forgiving about spacing — and only when it is unambiguous.
    # This is what a caller typing a name from memory needs, but picking the
    # first of several would be the silent mis-targeting the exact match above
    # exists to prevent.
    wanted = _norm(ref)
    loose = [c for c in candidates if _norm(c.name) == wanted]
    if len(loose) == 1:
        return loose[0], None
    if len(loose) > 1:
        return None, (
            f"'{sanitize_untrusted_text(ref, 40)}' matches {len(loose)} categories that "
            f"differ only in spacing. Use the category_id from list_categories."
        )

    return None, (
        f"No category '{sanitize_untrusted_text(ref, 40)}' under the "
        f"'{sanitize_untrusted_text(scope, 40)}' scope."
    )


def _entities_in_category(hass: HomeAssistant, scope: str, category_id: str) -> list[str]:
    return sorted(
        entry.entity_id
        for entry in er.async_get(hass).entities.values()
        if (entry.categories or {}).get(scope) == category_id
    )


def category_overview(hass: HomeAssistant, scope: str | None = None) -> dict[str, Any]:
    """Categories, with how many entities are filed under each.

    Without a scope this reports every scope in use, which is the only way a
    caller discovers what a home actually keeps categories for — the scope
    strings are not enumerable from HA and a guess creates an invisible list.
    """
    registry = cr.async_get(hass)
    scopes = [str(scope).strip()] if scope and str(scope).strip() else sorted(registry.categories)

    out: dict[str, Any] = {"scopes_in_use": sorted(registry.categories)}
    records: list[dict[str, Any]] = []
    for one in scopes:
        for entry in sorted(registry.async_list_categories(scope=one), key=lambda c: _norm(c.name)):
            records.append(
                {
                    "scope": one,
                    "category_id": entry.category_id,
                    "name": sanitize_untrusted_text(entry.name, 40),
                    "icon": entry.icon,
                    "entity_count": len(_entities_in_category(hass, one, entry.category_id)),
                }
            )
    out["categories"] = records[:_MAX_LISTED]
    out["count"] = len(records)
    if len(records) > _MAX_LISTED:
        out["categories_omitted"] = len(records) - _MAX_LISTED
    return out


def async_create_category(
    hass: HomeAssistant, *, scope: str, name: str, icon: str | None = None
) -> dict[str, Any]:
    """Create a category, or report the existing one with the same name.

    Same choice as areas, floors and labels: HA's ``async_create`` raises on a
    duplicate name, and a raised error reads to the model as a failure worth
    retrying differently rather than "it is already there".
    """
    scope = str(scope or "").strip()
    name = str(name or "").strip()
    if not scope:
        return {"error": "A category scope is required."}
    if not name:
        return {"error": "A category name is required."}

    # The EXACT comparison, not the forgiving resolver: `resolve_category`
    # falls back to collapsed whitespace so a caller can find a category from
    # memory, but HA allows "Outdoor Lights" and "Outdoor  Lights" to coexist —
    # so treating the loose match as a duplicate refuses a creation HA would
    # happily accept, and the caller is told the category already exists.
    existing = next(
        (
            candidate
            for candidate in cr.async_get(hass).async_list_categories(scope=scope)
            if candidate.name.casefold() == name.casefold()
        ),
        None,
    )
    if existing is not None:
        return {
            "status": "exists",
            "scope": scope,
            "category_id": existing.category_id,
            "name": sanitize_untrusted_text(existing.name, 40),
        }

    entry = cr.async_get(hass).async_create(
        scope=scope, name=name, icon=str(icon).strip() or None if icon else None
    )
    return {
        "status": "created",
        "scope": scope,
        "category_id": entry.category_id,
        "name": sanitize_untrusted_text(entry.name, 40),
    }


def category_dependents(hass: HomeAssistant, scope: str, category_id: str) -> dict[str, Any]:
    """What a category's removal would affect: the entities filed under it."""
    return {"entities": _entities_in_category(hass, scope, category_id)}


def async_delete_category(hass: HomeAssistant, scope: str, category_id: str) -> dict[str, Any]:
    """Delete a category. Entities filed under it survive, uncategorised.

    HA's entity registry listens for the removal and calls
    ``async_clear_category_id``, so nothing dangles — but nothing announces it
    either, which is why the confirmation card carries the count.
    """
    # Normalized here too: `resolve_category` strips, so a caller that resolved
    # successfully and passed its raw scope on would be told the category does
    # not exist.
    scope = str(scope or "").strip()
    registry = cr.async_get(hass)
    entry = registry.async_get_category(scope=scope, category_id=category_id)
    if entry is None:
        return {
            "error": (
                f"No category '{sanitize_untrusted_text(category_id, 40)}' under the "
                f"'{sanitize_untrusted_text(scope, 40)}' scope."
            )
        }

    freed = _entities_in_category(hass, scope, category_id)
    registry.async_delete(scope=scope, category_id=category_id)
    return {
        "status": "deleted",
        "scope": scope,
        "category_id": category_id,
        "name": sanitize_untrusted_text(entry.name, 40),
        "entities_uncategorised": len(freed),
    }


async def async_assign_category(
    hass: HomeAssistant,
    *,
    entity_ids: Iterable[str],
    scope: str,
    category: str | None = None,
) -> dict[str, Any]:
    """File entities under a category within one scope, or clear that scope.

    Writes ONE scope's key and leaves the rest of ``categories`` alone. The
    field is a per-scope mapping several unrelated concerns write to, so
    replacing it wholesale from a caller that only knows about ``automation``
    would drop whatever filing the Scripts page had.

    ``category`` omitted means "remove these from their category in this scope",
    which is the only way to undo an assignment — an empty name is not a
    category a user can name.
    """
    scope = str(scope or "").strip()
    if not scope:
        return {"error": "A category scope is required."}

    wanted = [str(e).strip() for e in entity_ids if str(e).strip()]
    if not wanted:
        return {"error": "At least one entity_id is required."}

    category_id: str | None = None
    if category is not None and str(category).strip():
        entry, error = resolve_category(hass, scope, str(category))
        if error or entry is None:
            return {"error": error or "Category not found."}
        category_id = entry.category_id

    registry = er.async_get(hass)
    # Only when ASSIGNING. Clearing removes a mapping that already exists, and
    # an entity can be out of scope precisely because something wrote a stale
    # one — or because its helper integration's metadata is momentarily
    # unreadable. Refusing then makes the bad state unfixable through the tool
    # that caused it.
    outside = await _entities_outside_scope(hass, scope, wanted) if category_id else set()
    updated: list[str] = []
    missing: list[str] = []
    wrong_scope: list[str] = []
    for entity_id in wanted:
        if registry.async_get(entity_id) is None:
            missing.append(entity_id)
            continue
        if entity_id in outside:
            wrong_scope.append(entity_id)
            continue
        current = dict(registry.async_get(entity_id).categories or {})
        if category_id is None:
            current.pop(scope, None)
        else:
            current[scope] = category_id
        registry.async_update_entity(entity_id, categories=current)
        updated.append(entity_id)

    result: dict[str, Any] = {
        "status": "assigned" if category_id else "cleared",
        "scope": scope,
        "entities_updated": updated,
    }
    if category_id:
        result["category_id"] = category_id
    if missing:
        result["not_found"] = missing
    if wrong_scope:
        result["wrong_scope"] = wrong_scope
        result["message"] = (
            f"The '{sanitize_untrusted_text(scope, 40)}' page never lists these, so "
            f"filing them there would show up nowhere: {', '.join(wrong_scope[:5])}."
        )
    return result
