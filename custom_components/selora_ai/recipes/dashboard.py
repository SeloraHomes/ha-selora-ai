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
  bound entity for that role) and ``${input:<id>}`` (→ the input value).
  Substituted here, at apply time, against the resolved bindings.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
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

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .manifest import DashboardCardSpec

_LOGGER = logging.getLogger(__name__)

# Marker key stamped on every card we insert, so we can find + replace +
# remove our own cards without touching anything the user authored.
CARD_TAG_KEY = "selora_recipe"

_PLACEHOLDER = re.compile(r"\$\{(role|input):([a-zA-Z0-9_]+)\}")

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
    # "view_not_found", "save_failed".
    reason: str
    target: str | None = None
    view: int | str | None = None
    message: str = ""


def _substitute(value: Any, bindings: dict[str, list[str]], inputs: dict[str, Any]) -> Any:
    """Recursively resolve ``${role:x}`` / ``${input:x}`` placeholders in
    a card config. A whole-string placeholder (``"${input:bedtime}"``)
    yields the raw value (preserving non-string types); an embedded one
    (``"Tap ${role:button}"``) is string-interpolated.
    """
    if isinstance(value, dict):
        return {k: _substitute(v, bindings, inputs) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, bindings, inputs) for v in value]
    if not isinstance(value, str):
        return value

    def resolve_one(kind: str, name: str) -> Any:
        if kind == "role":
            ids = bindings.get(name) or []
            return ids[0] if ids else ""
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
) -> dict[str, Any]:
    """Build the concrete card dict to insert: placeholders resolved +
    the ownership marker stamped on. Pure; unit-testable without HA.
    """
    card = _substitute(spec.card, bindings, inputs)
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
    card = resolve_card(spec, slug, bindings, inputs)
    return await async_place_card(hass, card=card, tag=slug, target=spec.target, view=spec.view)


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
    user to do. A container emptied by the removal is dropped with its contents
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
        return 0

    data: LovelaceData | None = hass.data.get(LOVELACE_DATA)
    if data is None:
        return 0

    removed = 0
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
    return removed


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
