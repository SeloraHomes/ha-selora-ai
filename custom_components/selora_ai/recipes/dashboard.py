"""Deterministic Lovelace card insertion — the recipe pipeline's final
"add the toggle to your dashboard" stage.

A recipe can declare an optional ``dashboard:`` block (see
:class:`selora_ai.recipes.manifest.DashboardCardSpec`). After the package
reloads, the install pipeline calls :func:`async_insert_card` to drop that
card onto a dashboard via Home Assistant's Lovelace storage API. No LLM is
involved — this is a pure data-in / config-out write so it stays
replayable in CI / remote-preview, exactly like the rest of the pipeline.

Design choices:

- **Storage mode only.** Only storage-mode dashboards expose
  ``async_save``. YAML-mode dashboards are read-only, so we skip them and
  let the caller fall back to the recipe's manual instructions.
- **Tagged for idempotency + clean uninstall.** Each inserted card carries
  a ``selora_recipe: <slug>`` marker. Re-installing replaces the prior
  card instead of duplicating it; uninstall removes every card carrying
  the slug. The marker is an extra top-level key on the card dict — the
  frontend ignores unknown keys for the built-in cards we target
  (button / entity / entities).
- **Placeholders.** Card values may use ``${role:<id>}`` (→ the first
  bound entity for that role), ``${device:<id>}`` (→ the device that
  entity belongs to) and ``${input:<id>}`` (→ the input value).
  Substituted here, at apply time, against the resolved bindings.
  ``${device:}`` exists for the custom cards that target a device rather
  than an entity — the toothbrush card is the first — since a recipe only
  ever binds entities and has no way to name a device id itself.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
import json
import logging
import re
from typing import TYPE_CHECKING, Any

from ..helpers import (
    DASHBOARD_LOCK,
    default_dashboard_key,
    is_auto_generated_dashboard,
    is_strategy_document,
)
from .resources import (
    RESOURCE_URL_BASE,
    async_drop_if_unshared,
    async_ensure_resource,
    async_is_registered,
    async_prune_superseded,
    async_registered_urls,
    async_remove_resource,
    record_claims,
    resource_url,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .manifest import DashboardCardSpec

_LOGGER = logging.getLogger(__name__)

# Marker key stamped on every card we insert, so we can find + replace +
# remove our own cards without touching anything the user authored.
CARD_TAG_KEY = "selora_recipe"

_PLACEHOLDER = re.compile(r"\$\{(role|device|input):([a-zA-Z0-9_]+)\}")

# Sentinel install target meaning "the user chose not to add a card".
SKIP_TARGET = "__skip__"


def list_writable_dashboards(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Enumerate storage-mode (writable) Lovelace dashboards.

    Returns ``[{"url_path": str | None, "title": str}]`` with the
    default dashboard (``url_path`` None) first. YAML-mode dashboards are
    omitted — they're read-only, so we can't insert a card there. Used by
    the wizard's "which dashboard?" picker and the ``list_dashboards``
    LLM tool.
    """
    try:
        from homeassistant.components.lovelace import LovelaceData
        from homeassistant.components.lovelace.const import (
            LOVELACE_DATA,
            MODE_STORAGE,
        )
    except ImportError:  # pragma: no cover — lovelace ships with core
        return []

    data: LovelaceData | None = hass.data.get(LOVELACE_DATA)
    if data is None:
        return []

    out: list[dict[str, Any]] = []
    for url_path, config in data.dashboards.items():
        if getattr(config, "mode", None) != MODE_STORAGE:
            continue
        meta = getattr(config, "config", None)
        title = meta.get("title") if isinstance(meta, dict) else None
        if not title:
            title = "Overview" if url_path is None else str(url_path)
        out.append({"url_path": url_path, "title": title})
    # Default dashboard first, then alphabetical by title for stability.
    out.sort(key=lambda d: (d["url_path"] is not None, str(d["title"]).lower()))
    return out


@dataclass(frozen=True, slots=True)
class DashboardInsertResult:
    """Outcome of an insert attempt. ``ok`` False is NOT fatal to the
    install — the package is already live; a card we couldn't place is a
    soft advisory the wizard surfaces with a fallback to manual steps.
    """

    ok: bool
    # Stable reason code for the UI / punch list. One of: "inserted",
    # "lovelace_unavailable", "dashboard_not_found", "yaml_mode",
    # "view_not_found", "save_failed", "resource_missing",
    # "resource_install_failed".
    reason: str
    target: str | None = None
    view: int | str | None = None
    message: str = ""
    # Every managed card resource this recipe is on the hook for after
    # this attempt, for uninstall to take back out. Usually one, or none
    # when the recipe declared no resource or the home had the card
    # already. Two when an upgrade could not take the old version out:
    # the record has to keep both or the leftover has no owner.
    #
    # Absent (the default) is different from empty: the pipeline writes
    # this key only when a placement actually ran, and a record whose
    # dashboard step was skipped keeps the claims it already had.
    resource_urls: tuple[str, ...] | None = None


def _substitute(
    value: Any,
    bindings: dict[str, list[str]],
    inputs: dict[str, Any],
    devices: dict[str, str] | None = None,
) -> Any:
    """Recursively resolve ``${role:x}`` / ``${device:x}`` / ``${input:x}``
    placeholders in a card config. A whole-string placeholder
    (``"${input:bedtime}"``) yields the raw value (preserving non-string
    types); an embedded one (``"Tap ${role:button}"``) is string-interpolated.

    An unresolvable placeholder becomes an empty string rather than being
    left as literal ``${...}`` text: a card carrying a visible placeholder
    reads as a bug to the homeowner, while an empty field is what every
    other missing binding already looks like.
    """
    if isinstance(value, dict):
        return {k: _substitute(v, bindings, inputs, devices) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, bindings, inputs, devices) for v in value]
    if not isinstance(value, str):
        return value

    def resolve_one(kind: str, name: str) -> Any:
        if kind == "role":
            ids = bindings.get(name) or []
            return ids[0] if ids else ""
        if kind == "device":
            return (devices or {}).get(name, "")
        return inputs.get(name, "")

    full = _PLACEHOLDER.fullmatch(value)
    if full:
        # Sole placeholder → keep the resolved value's native type.
        return resolve_one(full.group(1), full.group(2))
    return _PLACEHOLDER.sub(lambda m: str(resolve_one(m.group(1), m.group(2))), value)


def resolve_card(
    spec: DashboardCardSpec,
    slug: str,
    bindings: dict[str, list[str]],
    inputs: dict[str, Any],
    devices: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the concrete card dict to insert: placeholders resolved +
    the ownership marker stamped on. Pure; unit-testable without HA.

    ``devices`` maps role id → device id, built by the caller (which has
    ``hass``) via :func:`device_ids_for_bindings`. Kept as a parameter
    rather than looked up here so this stays a pure data transform.
    """
    card = _substitute(spec.card, bindings, inputs, devices)
    if not isinstance(card, dict):  # defensive — manifest validation guards this
        card = {}
    return {**card, CARD_TAG_KEY: slug}


def _get_storage_dashboard(hass: HomeAssistant, target: str | None) -> Any | None:
    """Return the writable (storage-mode) LovelaceConfig for ``target``,
    or None when Lovelace isn't ready, the dashboard is missing, or it's
    a read-only YAML dashboard.
    """
    try:
        from homeassistant.components.lovelace import LovelaceData
        from homeassistant.components.lovelace.const import (
            LOVELACE_DATA,
            MODE_STORAGE,
        )
    except ImportError:  # pragma: no cover — lovelace ships with core
        return None

    data: LovelaceData | None = hass.data.get(LOVELACE_DATA)
    if data is None:
        return None
    key = target or None
    if key in ("lovelace", "", None):
        key = default_dashboard_key(data.dashboards)
    config = data.dashboards.get(key)
    if config is None:
        return None
    # Only storage-mode dashboards expose async_save.
    if getattr(config, "mode", None) != MODE_STORAGE:
        return None
    return config


def _find_view(config_dict: dict[str, Any], view: int | str) -> dict[str, Any] | None:
    raw = config_dict.setdefault("views", [])
    if not isinstance(raw, list):
        return None
    # Indexed over the DICT-ONLY views, matching what get_dashboard reports. The
    # stored list is free-form and may hold a stray non-dict; indexing it raw
    # means an index the caller was just handed lands on a different page.
    views = [v for v in raw if isinstance(v, dict)]
    if isinstance(view, int):
        if 0 <= view < len(views):
            return views[view]
        if view == 0 and not raw:
            # Empty dashboard: seed a first view so there's somewhere to land.
            first: dict[str, Any] = {"title": "Home", "cards": []}
            raw.append(first)
            return first
        return None
    # String → match by title or path.
    for v in views:
        if isinstance(v, dict) and (v.get("title") == view or v.get("path") == view):
            return v
    return None


def device_ids_for_bindings(hass: HomeAssistant, bindings: dict[str, list[str]]) -> dict[str, str]:
    """Map each role id to the device its first bound entity belongs to.

    Feeds ``${device:<role>}``. Strictly the *first* binding, the same
    entity ``${role:<id>}`` resolves to — never a later one, even when
    the first carries no device and a later one would. A card naming
    both placeholders has to describe one thing; falling through to the
    next entity would quietly point the card at a second device while
    the entity rows still showed the first.

    A role whose first entity has no device (a helper, a template
    entity) is absent from the map, and the placeholder resolves to an
    empty string like any other unfillable binding.
    """
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    registry = er.async_get(hass)
    out: dict[str, str] = {}
    for role, entity_ids in bindings.items():
        if not entity_ids:
            continue
        entry = registry.async_get(entity_ids[0])
        if entry and entry.device_id:
            out[role] = entry.device_id
    return out


def _custom_elements(value: Any) -> set[str]:
    """Every ``custom:<element>`` card type in a card config, including the
    ones nested inside stacks and grids."""
    found: set[str] = set()
    if isinstance(value, dict):
        card_type = value.get("type")
        if isinstance(card_type, str) and card_type.startswith("custom:"):
            element = card_type.split(":", 1)[1].strip()
            if element:
                found.add(element)
        for v in value.values():
            found |= _custom_elements(v)
    elif isinstance(value, list):
        for v in value:
            found |= _custom_elements(v)
    return found


def _url_segments(url: str) -> list[str]:
    """Path segments of a resource URL, lower-cased, with a trailing
    ``.js`` dropped: ``/hacsfiles/foo-card/foo-card.js`` becomes
    ``["hacsfiles", "foo-card", "foo-card"]``."""
    path = url.split("?")[0].split("#")[0].lower()
    return [seg.removesuffix(".js") for seg in path.split("/") if seg]


async def _missing_card_resources(
    hass: HomeAssistant,
    card: dict[str, Any],
    requires_resource: str = "",
    ignore_prefix: str = "",
) -> list[str] | None:
    """What the homeowner has to install before this card can render.

    Names are what to go looking for in HACS: the author's declared
    resource when there is one, otherwise the custom element itself.

    Matching is by URL fragment, because that is all Home Assistant
    knows: resources are registered as URLs and only the browser learns
    which elements a bundle defines. ``requires_resource`` is the
    recipe author's fragment for exactly this reason — declare it when
    the bundle is named for its project rather than its card
    (``lovelace-mushroom/mushroom.js`` ships ``mushroom-chips-card``).
    Without it we look for the element name, which covers the usual
    ``/hacsfiles/<card>/<card>.js`` layout.

    The declaration stands in for the card's element wherever it sits,
    nested inside a stack included, but only while the card carries
    exactly one custom element: with several there is no way to tell
    which one a single fragment speaks for, so each falls back to its
    own name. A card mixing bundles is better split than declared.

    A miss means "we could not find it", not "it cannot work", and the
    caller treats it as such: the card is withheld with instructions
    and the install itself is unaffected.

    Empty when the card is built-in and when every element is covered.
    ``None`` when the resource list can't be read at all: unknown is not
    the same as nothing-missing, and the caller decides what to do with
    it — install its declared bundle if it has one, or place the card
    rather than withhold it over a lookup that failed.

    ``ignore_prefix`` drops resources under a URL base from the answer.
    Callers managing their own copy pass ours, so the question stays
    "does the home have this from somewhere else?" — otherwise a recipe
    upgrading its pinned version would find its own previous one and
    conclude there was nothing to do.
    """
    elements = _custom_elements(card)
    if not elements:
        return []
    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA  # noqa: PLC0415
    except ImportError:  # pragma: no cover — lovelace ships with core
        return None
    resources = getattr(hass.data.get(LOVELACE_DATA), "resources", None)
    if resources is None:
        return None
    try:
        # async_get_info loads the storage collection on first use;
        # async_items alone returns [] on a collection nobody touched yet,
        # which would read as "no resources installed".
        await resources.async_get_info()
        items = resources.async_items() or []
    except Exception:  # noqa: BLE001 — treat any read failure as "unknown"
        _LOGGER.debug("Could not read Lovelace resources", exc_info=True)
        return None
    urls = [str(i.get("url", "")) for i in items if isinstance(i, dict)]
    if ignore_prefix:
        urls = [u for u in urls if not u.split("?")[0].startswith(f"{ignore_prefix}/")]
    segments = [seg for u in urls for seg in _url_segments(u)]

    def registered(fragment: str) -> bool:
        """Whether a resource URL names ``fragment`` as one of its path
        segments, rather than merely containing those characters
        somewhere. ``slider-button-card`` must not answer for
        ``button-card``, and a substring test says it does.

        A segment may carry a suffix — our own files are stored as
        ``<name>-<version>-<digest>.js`` — so a delimiter after the
        fragment counts as a match, but nothing before it does.
        """
        needle = fragment.strip().lower()
        if not needle:
            return False
        return any(
            seg == needle or seg.startswith(f"{needle}-") or seg.startswith(f"{needle}.")
            for seg in segments
        )

    # One element, one declaration: the fragment IS that element's
    # identity, wherever the card sits, and it is the name to report when
    # it turns out to be missing. Telling someone to install
    # "mushroom-chips-card" when the HACS project is "lovelace-mushroom"
    # is not guidance they can act on.
    if requires_resource and len(elements) == 1:
        return [] if registered(requires_resource) else [requires_resource]

    # Several elements: a single fragment can't be attributed to one of
    # them, so it is checked on its own and each element on its name.
    missing = sorted(e for e in elements if not registered(e))
    if requires_resource and not registered(requires_resource):
        missing.append(requires_resource)
    return missing


async def _async_prior_claims(hass: HomeAssistant, slug: str) -> list[str]:
    """Managed URLs this recipe's record already says it owns."""
    try:
        from .store import get_install_store  # noqa: PLC0415

        record = await get_install_store(hass).async_get(slug)
    except Exception:  # noqa: BLE001 — housekeeping, never fatal
        _LOGGER.debug("Recipe %s: could not read its install record", slug, exc_info=True)
        return []
    return record_claims(record.dashboard_card) if record else []


async def _async_claims(hass: HomeAssistant, slug: str, installed_url: str = "") -> tuple[str, ...]:
    """What this recipe is on the hook for once the dust settles.

    Asked of the resource collection rather than inferred from whether
    each step reported success: a prune that quietly failed, a rollback
    that could not deregister, a file another recipe also claims — they
    all come down to the same question, which is whether the URL is still
    registered. Anything still there that this recipe put there stays its
    responsibility, so uninstall can come back for it.
    """
    candidates: list[str] = []
    for url in [installed_url, *await _async_prior_claims(hass, slug)]:
        if url and url not in candidates:
            candidates.append(url)
    if not candidates:
        # Nothing was ever ours. A built-in card on a recipe that never
        # declared a resource takes this path, and it has no business
        # reading the resource collection at all.
        return ()
    registered = await async_registered_urls(hass)
    if registered is None:
        # Can't tell. Keep every candidate: an orphan uninstall comes back
        # to beats a file with nobody responsible for it.
        return tuple(candidates)
    return tuple(url for url in candidates if url.split("?")[0] in registered)


async def async_insert_card(
    hass: HomeAssistant,
    *,
    slug: str,
    spec: DashboardCardSpec,
    bindings: dict[str, list[str]],
    inputs: dict[str, Any],
) -> DashboardInsertResult:
    """Insert (or replace) the recipe's card on its target dashboard.

    Idempotent: any existing card tagged with ``slug`` is dropped first,
    so re-installing updates in place instead of stacking duplicates.
    Never raises — failures come back as a non-ok result for the caller
    to surface as a soft advisory.
    """
    try:
        devices = device_ids_for_bindings(hass, bindings)
    except Exception:  # noqa: BLE001 — a registry hiccup must not fail the install
        _LOGGER.debug("Could not resolve device ids for %s bindings", slug, exc_info=True)
        devices = {}
    card = resolve_card(spec, slug, bindings, inputs, devices)

    # A custom card whose JS nobody installed renders as a red
    # "Custom element doesn't exist" box on the homeowner's dashboard.
    # That is worse than no card: the recipe works fine, but the dashboard
    # says something is broken.
    # When the recipe manages its own resource, our own copies are excluded
    # from the "does the home already have this?" question. Otherwise a
    # recipe pinning a newer version would see its own v1 registered, call
    # the element available, and never install v2.
    missing = await _missing_card_resources(
        hass,
        card,
        spec.requires_resource,
        ignore_prefix=RESOURCE_URL_BASE if spec.resource is not None else "",
    )

    # A recipe that declares where its card comes from installs it, the
    # same way a recipe sets up the integration it needs. Three cases,
    # and only the last one downloads anything:
    #   - our own copy is registered from an earlier install: keep the
    #     ownership claim on the new record, or uninstall would orphan it;
    #   - the card is available from somewhere else (HACS, a hand-rolled
    #     resource): leave that alone, it is not ours to manage;
    #   - nothing provides the element: fetch it.
    installed_url = ""
    # Whether THIS call downloaded it, which decides only whether to say
    # "refresh your browser". Ownership is a separate question: a copy an
    # earlier install left behind is still ours to remove at uninstall.
    fresh_install = False
    if spec.resource is not None:
        # Run the installer when our own copy is registered — it returns
        # "present" without downloading, and prunes anything this recipe
        # superseded — or when nothing provides the element. A card the
        # home has from elsewhere is left well alone.
        ours_registered = await async_is_registered(hass, resource_url(spec.resource))
        # ``missing`` was asked with our own copies excluded, so an empty
        # list means somebody else's provides the element: a HACS install,
        # or a resource added by hand. Theirs wins — it is the one the
        # homeowner manages — and any copy of ours is surplus, pruned once
        # the card is safely placed.
        external_provider = missing is not None and not missing
        # ``missing is None`` means the resource list couldn't be read.
        # With a declared bundle in hand the useful move is to install it:
        # the alternative is placing a card whose module may not be there.
        if not external_provider and (ours_registered or missing is None or missing):
            outcome = await async_ensure_resource(hass, spec.resource, owner_slug=slug)
            if not outcome.ok:
                _LOGGER.warning(
                    "Recipe %s: card resource %s not installed (%s)",
                    slug,
                    spec.resource.name,
                    outcome.reason,
                )
                # Clear the card only if nothing can render it. A failed
                # upgrade still has the previous version registered, and
                # the card it already placed keeps working — tearing that
                # down over a transient download failure would take a
                # working dashboard backwards.
                #
                # "repair_failed" is the exception: the registration
                # survives while the file behind it does not, so a URL
                # check would call the element provided when the browser
                # is about to get a 404 for it.
                if outcome.reason == "repair_failed" or await _missing_card_resources(
                    hass, card, spec.requires_resource
                ):
                    await async_remove_cards(hass, slug)
                return DashboardInsertResult(
                    ok=False,
                    reason="resource_install_failed",
                    target=spec.target,
                    view=spec.view,
                    # An upgrade whose download failed leaves the previous
                    # version registered and in use. Say so, or the record
                    # loses the only handle uninstall has on it.
                    resource_urls=await _async_claims(hass, slug),
                    message=outcome.message,
                )
            installed_url = outcome.url
            # "installed" alone: a repaired file sits behind a registration
            # that was already there, and rolling that back would take away
            # something another recipe or an existing card is using.
            fresh_install = outcome.reason == "installed"
            if len(_custom_elements(card)) == 1:
                # One element, one declaration: the bundle we just put
                # there is what provides it, whatever it happens to be
                # called. Matching URL text against the element name would
                # withhold a card we know is now installable.
                missing = []
            else:
                # Several elements: this bundle answers for its own, and a
                # stack nesting one from another project is still missing
                # that one. Our copy now counts, so nothing is excluded.
                missing = await _missing_card_resources(hass, card, spec.requires_resource) or []

    if missing:
        names = ", ".join(missing)
        _LOGGER.info("Skipping %s card: no Lovelace resource for %s", slug, names)
        # Drop the card a previous install left behind. Its resource is
        # gone now, so leaving it is leaving the exact broken box this
        # check exists to prevent — and re-installing is how a homeowner
        # would expect to clear one.
        await async_remove_cards(hass, slug)
        if fresh_install and installed_url:
            # We downloaded a bundle and then found another element the
            # card needs that nothing provides, so no card goes up and
            # nothing uses what we fetched. Pruning hasn't run either, so
            # the previous version is still registered: take the new one
            # back out and leave the record's claim on the one that is
            # still there.
            # Not cleared by hand afterwards: what this recipe still owns
            # is whatever is still registered, which the survey below asks
            # the resource collection directly. A deregistration that
            # failed leaves the URL there, and it stays ours.
            await async_remove_resource(hass, installed_url)
        return DashboardInsertResult(
            ok=False,
            reason="resource_missing",
            target=spec.target,
            view=spec.view,
            # Whatever survived the rollback is still ours to clean up.
            resource_urls=await _async_claims(hass, slug, installed_url),
            message=(
                f"Card not added: this dashboard card needs {names}, which is not "
                f"installed. Add it from HACS, then use Add card to place it."
            ),
        )
    placed = await async_place_card(hass, card=card, tag=slug, target=spec.target, view=spec.view)

    if not placed.ok:
        if fresh_install and installed_url:
            # Nothing uses what we just downloaded, and pruning waits for a
            # placed card — so whatever this was replacing is still
            # registered, under the card still on the dashboard. Taking the
            # new one back out returns the home to where it was.
            await async_remove_resource(hass, installed_url)
        return replace(placed, resource_urls=await _async_claims(hass, slug, installed_url))

    if spec.resource is None:
        # This revision names no bundle. If it also placed no custom card,
        # whatever an earlier one downloaded is dead weight the frontend
        # still loads, and it goes.
        #
        # A revision that dropped the ``resource`` block but kept the
        # ``custom:`` card is a different story: the card just went up,
        # and the module under it may well be the one we installed. The
        # missing-resource check would have withheld the card otherwise,
        # so something provides it — possibly ours. Keep claiming rather
        # than pull the module out from under a card we just placed.
        if not _custom_elements(card):
            for url in await _async_prior_claims(hass, slug):
                await async_drop_if_unshared(hass, url, owner_slug=slug)
        return replace(placed, resource_urls=await _async_claims(hass, slug))

    # The card is live, so what it replaced can go. ``installed_url`` empty
    # means it is running on someone else's copy — a HACS install that
    # turned up after ours — and every managed copy of this bundle is
    # surplus.
    await async_prune_superseded(hass, spec.resource, owner_slug=slug, keep_url=installed_url or "")
    placed = replace(placed, resource_urls=await _async_claims(hass, slug, installed_url))
    if installed_url and fresh_install:
        # The browser only loads a Lovelace resource on page load, so a
        # card installed this second renders as missing until a refresh.
        # Say so rather than letting it look broken for a minute.
        placed = replace(
            placed,
            message=(
                f"Installed {spec.resource.name} and added the card. Refresh your "
                f"browser if the card doesn't appear yet."
            ),
        )
    return placed


def _view_card_lists(view_obj: dict[str, Any]) -> list[list[Any]]:
    """Every mutable card list within a view, across both layouts.

    A classic view holds cards under ``view["cards"]``; a ``type:
    sections`` view holds them under ``view["sections"][n]["cards"]`` and
    *ignores* a top-level ``cards`` key. Returns live references so
    callers can filter in place (used for idempotent replace + removal).
    """
    if view_obj.get("type") == "sections":
        out: list[list[Any]] = []
        for sec in view_obj.get("sections") or []:
            if isinstance(sec, dict):
                out.append(sec.setdefault("cards", []))
        return out
    return [view_obj.setdefault("cards", [])]


def _replace_tagged(
    card_list: list[Any],
    tag: str,
    replacement: dict[str, Any] | None,
    done: list[bool],
) -> int:
    """Substitute or drop every card tagged ``tag``. Returns the number dropped.

    ``replacement`` lands on the FIRST tagged card found, wherever it sits, and
    any others are dropped — so a re-install refreshes the card where the user
    put it instead of removing it and appending a new one at the end of the
    view. ``done`` carries "already substituted" across the several card lists a
    sections view has, and is shared with the recursive calls.

    Pass ``replacement=None`` to remove without substituting, which is uninstall.

    A tagged card can be nested: ``group_dashboard_cards`` wraps existing cards
    in a container, and organising a recipe's card is an ordinary thing for a
    user to do. Both shapes of container are followed — a list of ``cards``,
    and the single ``card`` a conditional-style wrapper holds. A container emptied by the removal is dropped with its contents
    — an empty grid renders as a labelled box holding nothing, which reads as
    breakage rather than as the tidy removal uninstall promised. A container
    whose card was *substituted* is not empty, so it survives untouched.
    """
    removed = 0
    kept: list[Any] = []
    for card in card_list:
        if isinstance(card, dict):
            if card.get(CARD_TAG_KEY) == tag:
                if replacement is not None and not done[0]:
                    done[0] = True
                    kept.append(replacement)
                else:
                    removed += 1
                continue
            nested = card.get("cards")
            if isinstance(nested, list) and nested:
                removed += _replace_tagged(nested, tag, replacement, done)
                if not nested:
                    continue
            # Singular wrappers hold their child under "card" rather than in
            # a list — a conditional card is the common one, and dropping a
            # recipe's card into one is an ordinary thing to do. Reuse the
            # same recursion by lending it a one-item list, then read back
            # what it left there.
            inner = card.get("card")
            if isinstance(inner, dict):
                holder = [inner]
                removed += _replace_tagged(holder, tag, replacement, done)
                if not holder:
                    # The wrapper's only child is gone; a conditional card
                    # with nothing in it renders as an error, so it goes too.
                    continue
                card["card"] = holder[0]
        kept.append(card)
    card_list[:] = kept
    return removed


def purge_tagged_cards(card_list: list[Any], tag: str) -> int:
    """Drop every card tagged ``tag`` from ``card_list``, nested ones included."""
    return _replace_tagged(card_list, tag, None, [True])


def replace_tagged_card(view_obj: dict[str, Any], tag: str, card: dict[str, Any]) -> bool:
    """Swap ``card`` in for this tag's existing card, in place. False if absent."""
    done = [False]
    for card_list in _view_card_lists(view_obj):
        _replace_tagged(card_list, tag, card, done)
    return done[0]


def _insert_target_cards(view_obj: dict[str, Any]) -> list[Any]:
    """The card list a NEW card should be appended to. For a sections
    view that's the first section (created if none exist) — appending to
    the view's top-level ``cards`` there would silently not render.
    """
    if view_obj.get("type") == "sections":
        sections = view_obj.setdefault("sections", [])
        if not sections or not isinstance(sections[0], dict):
            sections.insert(0, {"type": "grid", "cards": []})
        return sections[0].setdefault("cards", [])
    return view_obj.setdefault("cards", [])


async def async_place_card(
    hass: HomeAssistant,
    *,
    card: dict[str, Any],
    tag: str,
    target: str | None = None,
    view: int | str = 0,
) -> DashboardInsertResult:
    """Insert (or replace) one already-resolved card on a dashboard.

    The low-level write shared by the recipe install stage and the
    ``insert_dashboard_card`` LLM tool. ``card`` is a complete Lovelace
    card config; ``tag`` is stamped under :data:`CARD_TAG_KEY` for
    idempotent replace + clean removal. Never raises.
    """
    from homeassistant.components.lovelace.const import ConfigNotFound

    dashboard = _get_storage_dashboard(hass, target)
    if dashboard is None:
        return DashboardInsertResult(
            ok=False,
            reason="yaml_mode",
            target=target,
            message=(
                "Target dashboard is unavailable or in YAML mode (read-only); "
                "add the card manually."
            ),
        )

    tagged = {**card, CARD_TAG_KEY: tag}
    try:
        # Held across load→mutate→save: this is a whole-document rewrite, so a
        # chat edit that loads between our load and our save loses its work.
        async with DASHBOARD_LOCK:
            try:
                config = await dashboard.async_load(False)
            except ConfigNotFound:
                # ConfigNotFound is ambiguous: a dashboard HA is still
                # generating raises it while showing the user a full Overview,
                # and seeding a document here replaces all of it with one card.
                # A dashboard that is genuinely blank raises it too, and there
                # seeding is right — so probe rather than assume.
                if await is_auto_generated_dashboard(dashboard):
                    return DashboardInsertResult(
                        ok=False,
                        reason="auto_generated",
                        target=target,
                        view=view,
                        message=(
                            "Home Assistant is still generating that dashboard, so adding "
                            "a card would replace the page the user can see with just this "
                            "one. Open it, use the pencil and pick 'Take control' first."
                        ),
                    )
                config = {"views": [{"title": "Home", "cards": []}]}
            if is_strategy_document(config):
                # A Map-style dashboard stores a strategy and no views, and
                # `async_load` succeeds — so seeding a view here saves it
                # alongside the strategy, reports success, and the frontend
                # keeps building from the strategy and never shows the card.
                return DashboardInsertResult(
                    ok=False,
                    reason="strategy_dashboard",
                    target=target,
                    view=view,
                    message=(
                        "That dashboard is built from a strategy, so Home Assistant "
                        "ignores any cards stored on it. Open it, use the pencil and "
                        "pick 'Take control' first."
                    ),
                )

            # async_load hands back HA's cached config object, and dict() copies
            # only the root — the views and cards inside stay live. Mutating
            # those edits the cache whether or not the save below succeeds.
            config = copy.deepcopy(dict(config))

            view_obj = _find_view(config, view)
            if view_obj is None:
                return DashboardInsertResult(
                    ok=False,
                    reason="view_not_found",
                    target=target,
                    view=view,
                    message=f"View {view!r} not found on the target dashboard.",
                )

            # Refresh our prior card for this tag IN PLACE, wherever it sits —
            # across both layouts, and inside any container the user has since
            # grouped it into. Purging and appending would dedupe correctly and
            # still undo the grouping, moving the card back to the end of the
            # view with nothing to say why. Only a genuinely new card is
            # appended.
            if not replace_tagged_card(view_obj, tag, tagged):
                _insert_target_cards(view_obj).append(tagged)

            await dashboard.async_save(config)
    except Exception as exc:  # noqa: BLE001 — never let a card failure abort the caller
        _LOGGER.warning("Dashboard card insert failed (tag %s): %s", tag, exc)
        return DashboardInsertResult(
            ok=False,
            reason="save_failed",
            target=target,
            view=view,
            message=str(exc),
        )

    return DashboardInsertResult(ok=True, reason="inserted", target=target, view=view)


async def async_remove_cards(hass: HomeAssistant, slug: str) -> int:
    """Remove every card tagged with ``slug`` across all storage-mode
    dashboards. Returns the number removed. Best-effort + idempotent:
    called from uninstall, swallows per-dashboard failures.
    """
    try:
        from homeassistant.components.lovelace import LovelaceData
        from homeassistant.components.lovelace.const import (
            LOVELACE_DATA,
            MODE_STORAGE,
            ConfigNotFound,
        )
    except ImportError:  # pragma: no cover
        _mark_sweep_incomplete(hass, slug)
        return 0

    data: LovelaceData | None = hass.data.get(LOVELACE_DATA)
    if data is None:
        # Lovelace not loaded yet: no dashboard was looked at, so nothing
        # can be said about what the recipe left behind. Reporting a clean
        # sweep here would let uninstall pull the module out from under a
        # card still sitting in storage.
        _mark_sweep_incomplete(hass, slug)
        return 0

    removed = 0
    failed = False
    hass.data.get("selora_ai", {}).get("_card_removal_failed", set()).discard(slug)
    # One acquisition for the whole sweep: each dashboard is a separate
    # load→save cycle, and releasing between them would let a chat edit land on
    # a board we have already read but not yet written.
    async with DASHBOARD_LOCK:
        for config in data.dashboards.values():
            if getattr(config, "mode", None) != MODE_STORAGE:
                continue
            try:
                cfg = copy.deepcopy(dict(await config.async_load(False)))
            except ConfigNotFound:
                continue
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Skipping dashboard during card removal: %s", exc)
                failed = True
                continue
            changed = False
            for view in cfg.get("views", []) or []:
                if not isinstance(view, dict):
                    continue
                # Both classic (view["cards"]) and sections
                # (view["sections"][n]["cards"]) layouts, and nested containers.
                for card_list in _view_card_lists(view):
                    if dropped := purge_tagged_cards(card_list, slug):
                        removed += dropped
                        changed = True
            if changed:
                try:
                    await config.async_save(cfg)
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning("Recipe %s: dashboard card removal save failed: %s", slug, exc)
                    failed = True
    if failed:
        # Recorded rather than raised: removal is best-effort by design and
        # must not abort an uninstall. Callers that go on to delete what the
        # surviving cards depend on need to know, though.
        _mark_sweep_incomplete(hass, slug)
    return removed


def _mark_sweep_incomplete(hass: HomeAssistant, slug: str) -> None:
    """Record that a card removal pass couldn't account for every card."""
    hass.data.setdefault("selora_ai", {}).setdefault("_card_removal_failed", set()).add(slug)


def cards_fully_removed(hass: HomeAssistant, slug: str) -> bool:
    """Whether the last :func:`async_remove_cards` swept every dashboard.

    A dashboard that couldn't be read or saved keeps its tagged card, and
    that card still needs the module behind it. Consumed once: an
    uninstall asks, and a later reinstall starts from a clean slate.
    """
    failures: set[str] = hass.data.get("selora_ai", {}).get("_card_removal_failed", set())
    return slug not in failures


async def async_dashboards_with_entity(
    hass: HomeAssistant, entity_id: str
) -> tuple[list[str], list[str]]:
    """Dashboards referencing *entity_id*, and dashboards we could not inspect.

    Returns ``(referencing, unreadable)`` — both lists of ``url_path``
    (``"lovelace"`` for the default dashboard).

    Used to block an entity_id rename. Home Assistant rewrites no references
    when an id changes, and a Lovelace card is the one referrer with no
    ``*_with_entity`` helper — automations, scripts, scenes, and groups all
    have one, so a rename that checked only those would report itself safe
    while leaving cards pointing at an id that no longer exists.

    **YAML-mode dashboards are scanned too.** They cannot be edited from here,
    which makes blocking more important rather than less: a storage dashboard
    the user can repair with a few clicks is the mild case, while a YAML
    dashboard has to be hand-edited by someone who first has to work out why
    their card went blank. A dashboard that cannot be read at all is reported
    as unreadable so the caller can refuse rather than assume it is clean.

    The match is a substring test over the serialised config rather than a walk
    of every card schema. Cards nest arbitrarily (stacks, grids, custom cards)
    and third-party cards invent their own keys, so a structural walk would
    miss references this catches. It over-matches on a shared prefix
    (``light.kitchen`` inside ``light.kitchen_counter``), which is the safe
    direction: the cost is refusing a rename the user can still do in the UI,
    against silently breaking a dashboard they will not think to check.
    """
    if not entity_id:
        return [], []
    try:
        from homeassistant.components.lovelace import LovelaceData
        from homeassistant.components.lovelace.const import LOVELACE_DATA, ConfigNotFound
    except ImportError:  # pragma: no cover — lovelace ships with core
        return [], []

    data: LovelaceData | None = hass.data.get(LOVELACE_DATA)
    if data is None:
        return [], []

    found: list[str] = []
    unreadable: list[str] = []
    for url_path, config in data.dashboards.items():
        name = url_path or "lovelace"
        try:
            cfg = await config.async_load(False)
        except ConfigNotFound:
            # No config saved yet — genuinely nothing to reference.
            continue
        except Exception as exc:  # noqa: BLE001 — an unreadable board is not a clean one
            _LOGGER.debug("Could not inspect dashboard %s for references: %s", name, exc)
            unreadable.append(name)
            continue
        if entity_id in json.dumps(cfg, default=str):
            found.append(name)
    return found, unreadable
