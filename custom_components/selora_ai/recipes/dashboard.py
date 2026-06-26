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

from dataclasses import dataclass
import logging
import re
from typing import TYPE_CHECKING, Any

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
    config = data.dashboards.get(target)
    if config is None:
        return None
    # Only storage-mode dashboards expose async_save.
    if getattr(config, "mode", None) != MODE_STORAGE:
        return None
    return config


def _find_view(config_dict: dict[str, Any], view: int | str) -> dict[str, Any] | None:
    views = config_dict.setdefault("views", [])
    if not isinstance(views, list):
        return None
    if isinstance(view, int):
        if 0 <= view < len(views):
            return views[view]
        if view == 0 and not views:
            # Empty dashboard: seed a first view so there's somewhere to land.
            first: dict[str, Any] = {"title": "Home", "cards": []}
            views.append(first)
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
        try:
            config = await dashboard.async_load(False)
        except ConfigNotFound:
            # Auto-generated default dashboard that's never been saved —
            # seed an empty config so we can take it over (same thing HA
            # does the first time a user edits it).
            config = {"views": [{"title": "Home", "cards": []}]}
        # async_load may hand back a shared/immutable view; copy before mutating.
        config = dict(config)

        view_obj = _find_view(config, view)
        if view_obj is None:
            return DashboardInsertResult(
                ok=False,
                reason="view_not_found",
                target=target,
                view=view,
                message=f"View {view!r} not found on the target dashboard.",
            )

        # Drop our prior card(s) for this tag anywhere in the view —
        # idempotent re-insert across both classic and sections layouts.
        for card_list in _view_card_lists(view_obj):
            card_list[:] = [
                c for c in card_list if not (isinstance(c, dict) and c.get(CARD_TAG_KEY) == tag)
            ]
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
    for config in data.dashboards.values():
        if getattr(config, "mode", None) != MODE_STORAGE:
            continue
        try:
            cfg = dict(await config.async_load(False))
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
            # (view["sections"][n]["cards"]) layouts.
            for card_list in _view_card_lists(view):
                kept = [
                    c
                    for c in card_list
                    if not (isinstance(c, dict) and c.get(CARD_TAG_KEY) == slug)
                ]
                if len(kept) != len(card_list):
                    removed += len(card_list) - len(kept)
                    card_list[:] = kept
                    changed = True
        if changed:
            try:
                await config.async_save(cfg)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Recipe %s: dashboard card removal save failed: %s", slug, exc)
    return removed
