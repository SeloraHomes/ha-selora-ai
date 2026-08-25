"""Read and edit Lovelace dashboard content — views and cards.

Backs the chat tools that answer "what's on my dashboard", "put the thermostat
next to the lights", and "give me a page for the garage". Before these existed
the model could place a card but never *see* one: ``insert_dashboard_card`` took
a ``view`` argument the model had no way to learn, so it guessed, and in
practice always landed on view 0.

What is reachable, and what is not:

* **A dashboard's config is read/write.** ``LovelaceStorage.async_load`` /
  ``async_save`` round-trip the whole document, so views and cards are fully
  editable. Everything here works on that document.
* **A dashboard ENTRY is not creatable FROM HERE.** Not the same as impossible:
  ``DashboardsCollection`` — which owns adding and deleting dashboards — is a
  local inside ``lovelace.async_setup``, published only to the admin-only
  ``lovelace/dashboards/*`` websocket commands and never to ``hass.data``. The
  supported API exists; it is only reachable by an authenticated websocket
  client, which an in-process integration is not. So ``create_dashboard`` is
  absent here, and the user adds an empty dashboard in the UI for Selora to
  build out. See the note in CLAUDE.md on the panel-executed route, which would
  serve interactive panel sessions only — not MCP, and not unattended runs.

Three properties of the document shape drive most of the code here:

* **Nothing is validated server-side.** Lovelace storage is free-form JSON; the
  frontend owns the schema. So view ``title`` and ``path`` are *not* unique, and
  a resolver that takes the first match will edit an arbitrary page. Only the
  index is a guaranteed handle.
* **A sections view keeps its cards somewhere else.** A classic view holds them
  at ``view["cards"]``; a ``type: sections`` view holds them at
  ``view["sections"][n]["cards"]`` and ignores a top-level ``cards`` key
  entirely. Card addressing is therefore a flat index across every card list in
  the view, not an index into one of them.
* **The UI writes this document too.** Saving is read-modify-write of the whole
  config, so a card index captured in one call means nothing by the next one.
  Every edit carries a content fingerprint that is re-checked against the
  freshly-loaded document immediately before the save. ``DASHBOARD_LOCK`` (in
  ``helpers``, shared with the recipe install stage) covers the writers we own.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import quote

from .const import MAX_TOOL_RESULT_CHARS
from .dashboard_cards import with_card_reference
from .helpers import (
    CALLER_CAN_WRITE,
    CALLER_IS_ADMIN,
    DASHBOARD_LOCK,
    dashboard_info,
    default_dashboard_key,
    is_auto_generated_dashboard,
    is_strategy_document,
    sanitize_untrusted_text,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Re-exported: the lock is shared with `recipes.dashboard`, whose install and
# uninstall stages rewrite these same documents. See helpers for why it is one
# lock for every dashboard and what it deliberately does not cover.
__all__ = ["DASHBOARD_LOCK"]

# Caps for what a read echoes back. Cards live inside a list of view dicts, which
# ``ToolExecutor._find_longest_list`` cannot reach — it only trims top-level
# lists and lists one dict deep — so an oversized dashboard would otherwise have
# a whole VIEW record popped rather than being trimmed. Counts stay exact.
_MAX_VIEWS: Final = 30
_MAX_CARDS_PER_VIEW: Final = 40

# Ceiling on the removed-card config echoed back for restoration. Below
# ``MAX_TOOL_RESULT_CHARS`` so ``_truncate_result`` never gets to trim the card
# on its way out — a silently shortened restore payload is the hazard.
_MAX_RESTORE_CHARS: Final = MAX_TOOL_RESULT_CHARS - 2000

# Ceiling on a single card fetched for editing, for the same reason and with the
# same margin — the executor trims the assembled result, not the card alone.
_MAX_CARD_CHARS: Final = MAX_TOOL_RESULT_CHARS - 2000


def _lovelace_dashboard(hass: HomeAssistant, target: str | None) -> tuple[Any, str | None]:
    """Return ``(config, error)`` for a dashboard, readable or not.

    Unlike ``recipes.dashboard._get_storage_dashboard`` this does not require
    storage mode — a YAML dashboard is perfectly readable, and telling the user
    what is on it is useful even though we cannot edit it. Writers call
    :func:`_writable_dashboard` instead.
    """
    try:
        from homeassistant.components.lovelace import LovelaceData  # noqa: PLC0415
        from homeassistant.components.lovelace.const import LOVELACE_DATA  # noqa: PLC0415
    except ImportError:  # pragma: no cover — lovelace ships with core
        return None, "Lovelace is not available on this install."

    data: LovelaceData | None = hass.data.get(LOVELACE_DATA)
    if data is None:
        return None, "Lovelace is not set up yet."

    # An unqualified target — omitted, empty, or the name list_dashboards
    # reports — resolves the way HA itself does, which is not always None.
    key = target or None
    if key in ("lovelace", "", None):
        key = default_dashboard_key(data.dashboards)
    config = data.dashboards.get(key)
    if config is not None and _hidden_from_caller(config):
        config = None
    if config is None:
        known = ", ".join(
            sorted(
                str(k or "lovelace")
                for k, v in data.dashboards.items()
                if not _hidden_from_caller(v)
            )
        )
        return None, (
            f"No dashboard '{sanitize_untrusted_text(target or 'lovelace', 60)}'. "
            f"Available: {known or 'none'}."
        )
    return config, None


def _hidden_from_caller(config: Any) -> bool:
    """Whether HA hides this dashboard from the caller of the current tool call.

    A dashboard carries ``require_admin`` in its metadata and Home Assistant
    registers no panel for it for a non-admin, so it is invisible to them in the
    UI. The read tools here are deliberately available to non-admin chat users
    and read-only MCP credentials, so without this check they hand back the full
    card configuration — camera entity ids, whatever the household admin chose to
    keep to themselves — of a dashboard HA is hiding on purpose.

    Reported as absent rather than refused. A distinct "you may not read that"
    confirms the dashboard exists, which is the one bit HA is withholding.
    """
    if CALLER_IS_ADMIN.get():
        return False
    meta = getattr(config, "config", None)
    return bool(isinstance(meta, dict) and meta.get("require_admin"))


async def list_dashboards(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Every Lovelace dashboard, each marked with whether it can be edited.

    Distinct from ``recipes.dashboard.list_writable_dashboards``, which is the
    right answer for a card-placement picker and drops YAML boards because it
    cannot write to them. This is the discovery half of the READ tools, and they
    read YAML boards perfectly well — omitting those left a user able to see a
    dashboard in their sidebar that Selora insisted was not there, with no way
    to learn the ``url_path`` that would have fetched it.
    """
    try:
        from homeassistant.components.lovelace import LovelaceData  # noqa: PLC0415
        from homeassistant.components.lovelace.const import (  # noqa: PLC0415
            LOVELACE_DATA,
            MODE_STORAGE,
        )
    except ImportError:  # pragma: no cover — lovelace ships with core
        return []

    data: LovelaceData | None = hass.data.get(LOVELACE_DATA)
    if data is None:
        return []

    # When "lovelace" exists the None entry is the emptied placeholder the
    # migration left behind — HA registers no panel for it, so offering it would
    # be a dashboard the user cannot see anywhere.
    default_key = default_dashboard_key(data.dashboards)

    out: list[dict[str, Any]] = []
    for url_path, config in data.dashboards.items():
        if url_path is None and default_key is not None:
            continue
        if _hidden_from_caller(config):
            continue
        # Whether a write would actually be allowed, not merely what mode it is
        # in. A fresh install's Overview is storage-mode and still generated, so
        # a mode-only answer told the caller to go ahead and every write then
        # refused — inviting a workflow that cannot finish.
        # Asked of the same classifier the writes use, so the listing cannot
        # advertise a dashboard that every mutation then refuses — a Map-style
        # strategy board is storage-mode and reports storage from
        # `async_get_info`, so nothing cheaper distinguishes it.
        editable = (
            getattr(config, "mode", None) == MODE_STORAGE
            and CALLER_CAN_WRITE.get()
            and (await _load_or_reason(config))[1] is None
        )
        out.append(
            {
                "url_path": url_path,
                "title": _dashboard_title(config, url_path),
                "editable": editable,
            }
        )
    # Default dashboard first — it is what an unqualified request means.
    out.sort(key=lambda d: (d["url_path"] is not None, str(d["url_path"] or "")))
    return out


def _writable_dashboard(hass: HomeAssistant, target: str | None) -> tuple[Any, str | None]:
    """Return ``(config, error)`` for a dashboard that can be saved.

    A YAML dashboard is refused with an explanation rather than reported as
    missing — the user can see it in their sidebar, and "no such dashboard"
    would read as a bug in us rather than as a property of their setup.
    """
    from homeassistant.components.lovelace.const import MODE_STORAGE  # noqa: PLC0415

    config, error = _lovelace_dashboard(hass, target)
    if error or config is None:
        return None, error
    if getattr(config, "mode", None) != MODE_STORAGE:
        return None, (
            f"'{sanitize_untrusted_text(target or 'lovelace', 60)}' is a YAML-mode "
            f"dashboard, so Home Assistant does not let anything edit it through the "
            f"UI or the API. It has to be changed in its YAML file."
        )
    return config, None


_STRATEGY_NOTE: Final = (
    "That dashboard is generated by a strategy, so Home Assistant builds it from "
    "the strategy every time and ignores any views stored alongside it — an edit "
    "would save but never show. Open it, use the pencil and pick 'Take control' "
    "to turn it into a normal dashboard first."
)

_AUTO_GEN_NOTE: Final = (
    "Home Assistant is still generating that dashboard for you — it has no "
    "stored configuration of its own, which is why it has no views I can list. "
    "Open it, use the pencil in the top right and pick 'Take control', and Home "
    "Assistant will save the page you can see now as a real config. After that I "
    "can add views and cards to it."
)


async def _auto_generated(config: Any) -> bool:
    """See :func:`helpers.is_auto_generated_dashboard`.

    There is no way to materialise the generated config here: the strategy runs
    in the frontend, and core ships no server-side generator (the map dashboard
    is seeded by writing a strategy config, not by rendering one). So writes are
    refused and the user is pointed at Take control, which is the supported
    one-click way to turn the generated page into a stored one.
    """
    return await is_auto_generated_dashboard(config)


def _dashboard_title(config: Any, target: str | None) -> str:
    """A dashboard's display title.

    It lives in the dashboard's METADATA (`config.config["title"]`), not in the
    Lovelace document — `async_load` normally returns just `views`, so reading
    the title from there reported an empty string for every named dashboard
    while `list_dashboards` reported it correctly off the same object.
    """
    meta = getattr(config, "config", None)
    title = meta.get("title") if isinstance(meta, dict) else None
    if not title:
        # The legacy default dashboard has no metadata entry at all.
        title = "Overview" if target in (None, "", "lovelace") else str(target)
    return sanitize_untrusted_text(str(title), 60)


async def _load_or_reason(config: Any) -> tuple[dict[str, Any] | None, str | None]:
    """``(document, refusal)`` for a dashboard, without copying it.

    The one place that decides whether a dashboard can be read and written, so
    `_load_config` and `list_dashboards` cannot disagree about it — `editable`
    saying yes to something every write then refuses is the same bug as the
    write refusing something the listing called editable.

    ``({}, None)`` is a genuinely blank storage dashboard, which is writable.
    """
    from homeassistant.components.lovelace.const import ConfigNotFound  # noqa: PLC0415

    try:
        loaded = await config.async_load(False)
    except ConfigNotFound:
        info = await dashboard_info(config)
        if error := info.get("error"):
            # A YAML dashboard whose file is missing or unreadable. Its mode is
            # still `yaml`, so the auto-gen probe says nothing — and reporting
            # zero views hides a configuration problem behind an empty page.
            return None, (
                f"Home Assistant could not read that dashboard's YAML file: "
                f"{sanitize_untrusted_text(str(error), 120)}"
            )
        if info.get("mode", "auto-gen") == "auto-gen":
            # NOT an empty dashboard: HA is still generating it and the user is
            # looking at a full Overview. Guarded here rather than at each caller
            # so a reader cannot report zero views and a writer cannot save over
            # the generated page. `{}` from a failed probe lands here too.
            return None, _AUTO_GEN_NOTE
        return {}, None
    except Exception as exc:  # noqa: BLE001 — a broken board must not raise at the caller
        return None, f"Could not read that dashboard: {exc}"

    if is_strategy_document(loaded):
        return None, _STRATEGY_NOTE
    return dict(loaded or {}), None


async def _load_config(config: Any) -> tuple[dict[str, Any], str | None]:
    """Load a dashboard's stored document, or ``({}, None)`` when it has none."""
    document, error = await _load_or_reason(config)
    if error or document is None:
        return {}, error

    # DEEP copy. LovelaceStorage.async_load hands back its cached config object
    # itself, and dict() copies only the root mapping — the views list and every
    # view and card inside it stay the live objects HA is serving. Writers here
    # mutate before they validate, so `dict()` alone lets a REJECTED edit stick
    # in the cache: rename a view, hit the duplicate-path check, return an
    # error, and the cached title has already changed. The next save by anyone
    # persists it. Readers copy too — the returned card would otherwise be a
    # live reference that whatever trims the tool result could shorten in place.
    return copy.deepcopy(document), None


def card_fingerprint(card: Any) -> str:
    """Content hash of one card, used to re-identify it across a round trip.

    A card has no id. Its index is the only handle a caller can hold, and the
    index moves the moment anything is inserted or removed — including by the
    Lovelace UI, which this module cannot lock out. Hashing the card is what
    makes "replace card 3" mean the card the caller was actually shown.
    """
    canonical = json.dumps(card, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def view_fingerprint(view: Any) -> str:
    """Content hash of a whole view, used to re-identify it across a round trip.

    A card COUNT is not identity. Two views commonly hold the same number of
    cards, so if another view is removed or reordered while a confirmation card
    is open, the stored index resolves to a different page whose count happens
    to match — the check passes and deletes a page the user never approved.
    """
    canonical = json.dumps(view, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _views(document: dict[str, Any]) -> list[dict[str, Any]]:
    views = document.get("views")
    return [v for v in views if isinstance(v, dict)] if isinstance(views, list) else []


def _flat_cards(view: dict[str, Any]) -> list[tuple[list[Any], int, Any]]:
    """Every card in a view as ``(owning_list, position, card)``, in render order.

    Flattens the classic and sections layouts into one addressable sequence so a
    caller can say "card 3" without knowing which of them it is looking at. The
    owning list is a live reference, so a caller can mutate through it.
    """
    from .recipes.dashboard import _view_card_lists  # noqa: PLC0415

    out: list[tuple[list[Any], int, Any]] = []
    for card_list in _view_card_lists(view):
        for position, card in enumerate(card_list):
            out.append((card_list, position, card))
    return out


def resolve_view(document: dict[str, Any], ref: object) -> tuple[int | None, str | None]:
    """Resolve a view to its index. Returns ``(index, error)``.

    Accepts an integer index, a numeric string, a ``path``, or a ``title``.
    Index wins because it is the only unambiguous handle: Lovelace validates
    nothing server-side, so two views may share a title *or* a path, and taking
    the first match would edit an arbitrary page. An ambiguous name is refused
    with the indices to choose between.
    """
    views = _views(document)
    if not views:
        # Names the tool that fixes it. A dashboard just created has no pages,
        # and a bare statement of that fact is something the model relays to the
        # user as a step for THEM to perform — it asked for a view to be created
        # by hand on a dashboard it had made itself moments earlier.
        return None, "That dashboard has no pages yet. add_dashboard_view creates one."

    if isinstance(ref, bool):  # bool is an int subclass; never a view reference
        return None, "A view index, path, or title is required."
    if isinstance(ref, int):
        index = ref
    elif isinstance(ref, str) and ref.strip().lstrip("-").isdigit():
        index = int(ref.strip())
    else:
        index = None

    if index is not None:
        if 0 <= index < len(views):
            return index, None
        return None, f"This dashboard has {len(views)} views, so index {index} is out of range."

    wanted = " ".join(str(ref or "").split()).casefold()
    if not wanted:
        return None, "A view index, path, or title is required."

    # Collect across both fields before deciding. A name can match one view's
    # title and a *different* view's path; checking path first and returning on
    # its single hit resolves that silently, and to the view the user was least
    # likely to mean — they named it by the label the sidebar shows.
    matched: dict[int, list[str]] = {}
    for i, view in enumerate(views):
        for key in ("path", "title"):
            if " ".join(str(view.get(key) or "").split()).casefold() == wanted:
                matched.setdefault(i, []).append(key)
    if len(matched) == 1:
        return next(iter(matched)), None
    if len(matched) > 1:
        detail = ", ".join(f"{i} (by {' and '.join(keys)})" for i, keys in sorted(matched.items()))
        return None, (
            f"'{sanitize_untrusted_text(str(ref), 40)}' matches {len(matched)} views "
            f"— {detail}. Use the index instead."
        )

    labels = ", ".join(
        f"{i}: {sanitize_untrusted_text(v.get('title') or v.get('path') or '(untitled)', 30)}"
        for i, v in enumerate(views[:_MAX_VIEWS])
    )
    return None, f"No view '{sanitize_untrusted_text(str(ref), 40)}'. Views are — {labels}."


def _describe_card(card: Any, index: int) -> dict[str, Any]:
    """One card as the model needs to see it: what it is, and how to address it."""
    if not isinstance(card, dict):
        return {"index": index, "type": "(malformed)", "fingerprint": card_fingerprint(card)}

    described: dict[str, Any] = {
        "index": index,
        "type": str(card.get("type") or "(unspecified)"),
        "fingerprint": card_fingerprint(card),
    }
    if title := card.get("title") or card.get("name"):
        described["title"] = sanitize_untrusted_text(title, 60)
    # The entity a card points at is what a user names it by ("the thermostat
    # card"), so it is worth the tokens even though the full config is not.
    if entity := card.get("entity"):
        described["entity"] = str(entity)
    elif isinstance(card.get("entities"), list):
        described["entity_count"] = len(card["entities"])
    return described


# ── Read ────────────────────────────────────────────────────────────────────


async def async_get_dashboard(
    hass: HomeAssistant, target: str | None = None, view: object = None
) -> dict[str, Any]:
    """Return a dashboard's views, and the cards in one of them.

    Cards are returned for a single view at a time. Returning every card on
    every view is what makes a dashboard unreadable — a modest home runs to
    hundreds — and the caller almost always wants one page. Ask for the view
    when you know it; without one you get the view list and card counts, which
    is what you need to pick.
    """
    config, error = _lovelace_dashboard(hass, target)
    if error or config is None:
        return {"error": error or "Dashboard not found."}

    document, error = await _load_config(config)
    if error:
        return {"error": error}

    from homeassistant.components.lovelace.const import MODE_STORAGE  # noqa: PLC0415

    views = _views(document)
    writable_mode = getattr(config, "mode", None) == MODE_STORAGE
    result: dict[str, Any] = {
        "dashboard": target or "lovelace",
        "title": _dashboard_title(config, target),
        # Whether THIS caller could edit it. Every mutation tool is admin-gated,
        # so telling a read-only credential the dashboard is editable offers a
        # workflow it cannot finish. (An auto-generated board never reaches here
        # — `_load_config` refuses it outright.)
        "editable": writable_mode and CALLER_CAN_WRITE.get(),
        "view_count": len(views),
        "views": [
            {
                "index": i,
                "title": sanitize_untrusted_text(v.get("title") or "", 60),
                "path": str(v.get("path") or ""),
                "type": str(v.get("type") or "cards"),
                "card_count": len(_flat_cards(v)),
                # Pass this back on an edit so it cannot land on a different
                # page if the dashboard changed in between.
                "fingerprint": view_fingerprint(v),
            }
            for i, v in enumerate(views[:_MAX_VIEWS])
        ],
    }
    if len(views) > _MAX_VIEWS:
        result["views_omitted"] = len(views) - _MAX_VIEWS
    if not result["editable"]:
        # Naming the wrong reason is worse than naming none: the model repeats it
        # to the user, and "it's a YAML dashboard" is not something they can act on
        # when the real answer is that their account cannot edit dashboards.
        result["note"] = (
            "This is a YAML-mode dashboard: readable here, but only editable in its YAML file."
            if not writable_mode
            else "Readable but not editable by you: your credential cannot write dashboards."
        )

    if view is None:
        return with_card_reference(result)

    index, error = resolve_view(document, view)
    if error or index is None:
        return {**result, "error": error}

    cards = _flat_cards(views[index])
    result["view"] = {
        "index": index,
        "title": sanitize_untrusted_text(views[index].get("title") or "", 60),
        "card_count": len(cards),
        # Independently of the `views` cap above: a caller that selected view 30
        # or later finds it missing from that summary, and without a fingerprint
        # here it cannot pass `expected_view_fingerprint` to any of the writes
        # that demand one.
        "fingerprint": view_fingerprint(views[index]),
        "cards": [_describe_card(card, i) for i, (_, _, card) in enumerate(cards)][
            :_MAX_CARDS_PER_VIEW
        ],
    }
    if len(cards) > _MAX_CARDS_PER_VIEW:
        result["view"]["cards_omitted"] = len(cards) - _MAX_CARDS_PER_VIEW
    # The taxonomy rides on the read the model does BEFORE composing — this
    # tool's own description tells it to always call this first — so it arrives
    # once per turn rather than on every write.
    return with_card_reference(result)


async def async_get_card(
    hass: HomeAssistant, target: str | None, view: object, card_index: int
) -> dict[str, Any]:
    """Return one card's FULL configuration, plus its fingerprint.

    ``get_dashboard`` deliberately summarises — this is how a caller gets the
    whole card back before editing it. The fingerprint travels with it so the
    edit can prove it is changing the card it was shown.
    """
    config, error = _lovelace_dashboard(hass, target)
    if error or config is None:
        return {"error": error or "Dashboard not found."}
    document, error = await _load_config(config)
    if error:
        return {"error": error}

    index, error = resolve_view(document, view)
    if error or index is None:
        return {"error": error}

    cards = _flat_cards(_views(document)[index])
    if not 0 <= card_index < len(cards):
        return {
            "error": (f"That view has {len(cards)} cards, so index {card_index} is out of range.")
        }
    card = cards[card_index][2]
    result = {
        "dashboard": target or "lovelace",
        "view_index": index,
        "card_index": card_index,
        "fingerprint": card_fingerprint(card),
        "card": card,
    }

    # A card big enough to be trimmed on the way out must not travel with an
    # editable fingerprint. `_truncate_result` drops items from the longest
    # list — an `entities` list, or a nested stack's `cards` — while the
    # fingerprint still describes the WHOLE card, so sending the shortened
    # version back through update_dashboard_card passes the identity check and
    # silently deletes every entry that was trimmed. The caller is an LLM
    # editing what it was shown; it has no way to know rows went missing.
    if len(json.dumps(result, ensure_ascii=False, default=str)) > _MAX_CARD_CHARS:
        # `.get` only after the isinstance check. Lovelace storage is free-form
        # and the rest of this module handles a non-dict card deliberately, so
        # the one path that assumed a mapping turned an oversized malformed card
        # into an AttributeError surfacing as "Tool execution failed".
        card_type = (
            str(card.get("type") or "unknown") if isinstance(card, dict) else type(card).__name__
        )
        return {
            "error": (
                f"Card {card_index} in that view is too large to fetch intact "
                f"({len(json.dumps(card, ensure_ascii=False, default=str))} characters), and a "
                f"partial copy would delete whatever was cut when it was written back. "
                f"It is a '{sanitize_untrusted_text(card_type, 40)}' "
                f"card — edit it in the dashboard UI, or split it into smaller cards first."
            )
        }
    return result


# ── Write ───────────────────────────────────────────────────────────────────


async def _save(
    config: Any, document: dict[str, Any], previous: dict[str, Any] | None = None
) -> str | None:
    """Persist a dashboard document, returning an error string instead of raising.

    ``previous`` is the document as it stood BEFORE the mutation, and passing it
    makes a failed save leave nothing behind. A save that RAISES has already
    taken effect everywhere except the file: `LovelaceStorage.async_save`
    replaces its cached config and fires the update event before it awaits the
    store write, and `async_load` serves that cache rather than re-reading —
    so the frontend is showing the change and every later read agrees with it,
    while the caller is told the save failed. Re-saving the original puts both
    back, and whether its own write lands does not matter, because the cache
    and the event are updated ahead of the await either way.
    """
    try:
        await config.async_save(document)
    except Exception as exc:  # noqa: BLE001 — surfaced to the user, never raised at them
        _LOGGER.warning("Could not save dashboard: %s", exc)
        if previous is not None:
            try:
                await config.async_save(previous)
            except Exception as revert_exc:  # noqa: BLE001 — the first failure is the one to report
                _LOGGER.warning(
                    "Could not restore the dashboard after a failed save: %s", revert_exc
                )
        return f"Home Assistant refused to save the dashboard: {exc}"
    return None


async def _copies_on_disk(config: Any, view_index: int, fingerprint: str) -> int | None:
    """How many copies of a card the FILE holds. ``None`` when unknowable.

    A ``_save`` that reports success means Home Assistant ACCEPTED the write,
    not that it landed. ``Store.async_save`` does not raise on an ordinary
    write failure — ``_async_handle_write_data`` catches ``WriteError`` and
    ``SerializationError``, logs them and returns — and it skips the write
    outright when the store is read-only or Home Assistant is stopping. So the
    commonest failures there are all silent.

    Reading the dashboard back settles nothing either: ``async_save`` replaced
    that cache before it attempted the write, so a read agrees with the caller
    whether or not anything reached the file. Only the file itself answers, and
    it is reachable only through the ``Store`` the dashboard keeps privately —
    which is why an unexpected shape is UNKNOWABLE rather than a failure. The
    caller treats unknowable as fine: this exists to catch a destination that
    silently did not save, and refusing every move on a Home Assistant whose
    internals have moved would be worse than the case it guards.

    Counted rather than tested, because a view may already hold a card
    identical to the one being moved — a bare "is it there" then answers yes
    off the copy that was already on disk.
    """
    store = getattr(config, "_store", None)
    if store is None or not hasattr(store, "async_load"):
        return None
    try:
        # Genuinely re-reads: `_async_handle_write_data` cleared the pending
        # data and invalidated the manager's cache before writing.
        raw = await store.async_load()
    except Exception:  # noqa: BLE001 — a probe that fails is unknowable, not a failure
        _LOGGER.debug("Could not read a dashboard store back", exc_info=True)
        return None
    if not isinstance(raw, dict) or not isinstance(document := raw.get("config"), dict):
        return None
    views = _views(document)
    if not 0 <= view_index < len(views):
        return 0
    return sum(
        1 for _, _, card in _flat_cards(views[view_index]) if card_fingerprint(card) == fingerprint
    )


def _copies_in_view(view: dict[str, Any], fingerprint: str) -> int:
    """How many copies of a card a loaded view holds — the count to expect on disk."""
    return sum(1 for _, _, card in _flat_cards(view) if card_fingerprint(card) == fingerprint)


async def async_insert_card(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Append a card to a view. Shared by the chat and MCP surfaces.

    Both surfaces need identical validation and the same ``view`` coercion, and
    a hand-written second copy drifts on the next argument added — quietly, in
    the shape where the MCP client rejects a card chat accepts. Same reason the
    MCP schemas are derived from the chat ``ToolDef``s rather than restated.
    """
    from .recipes.dashboard import async_place_card  # noqa: PLC0415

    card = arguments.get("card")
    if not isinstance(card, dict) or not str(card.get("type", "")).strip():
        return {"error": "card must be an object with a 'type' field"}
    # Lovelace stores whatever it is given, so an invented entity id renders as
    # "Entity not found" on the user's wall panel with nothing else to catch it.
    if error := _card_type_error(hass, card):
        return {"error": error}
    if error := _entity_error(hass, card):
        return {"error": error}

    # No auto-generation preflight here. `async_place_card` probes in the one
    # place the answer is needed — after `async_load` raises `ConfigNotFound`,
    # where seeding a document would replace a generated Overview. Probing
    # before the load meant a dashboard whose metadata read merely failed was
    # refused fail-closed without the load ever being attempted, so a transient
    # `async_get_info` error disabled card insertion alone while every other
    # dashboard write carried on working.

    # ``view`` arrives as a string; coerce a numeric one to an int so it indexes
    # the views list rather than matching a title.
    view_raw = arguments.get("view", 0)
    view: int | str
    if isinstance(view_raw, str) and view_raw.strip().lstrip("-").isdigit():
        view = int(view_raw)
    else:
        view = view_raw if view_raw not in ("", None) else 0

    result = await async_place_card(
        hass,
        card=card,
        tag=str(arguments.get("tag") or "selora_chat"),
        target=arguments.get("dashboard_target") or None,
        view=view,
    )
    return {
        "ok": result.ok,
        "reason": result.reason,
        "target": result.target,
        "view": result.view,
        "message": result.message,
        # Where to go and look. Composed here, where the target and the view
        # are known and encoded, rather than left to prose that names a page
        # and gives no way to reach it.
        **(
            {
                "url": _view_url(
                    result.target,
                    result.view if isinstance(result.view, str) else None,
                    result.view if isinstance(result.view, int) else 0,
                )
            }
            if result.ok
            else {}
        ),
    }


def _view_url(target: str | None, path: str | None, index: int) -> str:
    """The in-app URL of a view — what a user needs to go and look at it.

    Falls back to the index when the view has no path, which is how Home
    Assistant addresses an unnamed view in the URL bar.

    Both segments are percent-encoded. Lovelace validates nothing server-side,
    so a stored path may hold characters that mean something else in a URL — a
    view saved as ``kitchen#lights`` would otherwise produce
    ``/lovelace/kitchen#lights``, which the browser reads as the fragment
    ``#lights`` on a view that does not exist. The view was stored fine; only
    the link would have been wrong, which is the worst shape for this
    particular result since its whole job is to be followed.
    """
    dashboard = quote(str(target or "lovelace"), safe="")
    slug = quote(str(path or "").strip() or str(index), safe="")
    return f"/{dashboard}/{slug}"


async def async_add_view(
    hass: HomeAssistant,
    *,
    target: str | None = None,
    title: str,
    path: str | None = None,
    icon: str | None = None,
    sections: bool = False,
) -> dict[str, Any]:
    """Append a view (a page) to a dashboard.

    Appends rather than inserts: a view's position is what the user's sidebar
    order looks like, and silently pushing their existing pages along is a
    change they did not ask for.
    """
    title = str(title or "").strip()
    if not title:
        return {"error": "A view title is required."}

    async with DASHBOARD_LOCK:
        config, error = _writable_dashboard(hass, target)
        if error or config is None:
            return {"error": error or "Dashboard not found."}
        document, error = await _load_config(config)
        if error:
            return {"error": error}

        views = document.setdefault("views", [])
        if not isinstance(views, list):
            return {"error": "That dashboard's stored config is malformed (views is not a list)."}

        view: dict[str, Any] = {"title": title}
        if path:
            slug = str(path).strip()
            if any(str(v.get("path") or "") == slug for v in _views(document)):
                return {
                    "error": f"A view with the path '{sanitize_untrusted_text(slug, 40)}' already exists."
                }
            view["path"] = slug
        if icon:
            view["icon"] = str(icon).strip()
        # A sections view stores cards under sections[]; seed one so the view is
        # immediately usable as an insert target rather than silently dropping
        # the first card added to it.
        if sections:
            view["type"] = "sections"
            view["sections"] = [{"type": "grid", "cards": []}]
        else:
            view["cards"] = []

        views.append(view)
        if error := await _save(config, document):
            return {"error": error}
        # Off the FILTERED list. `views` is the free-form stored list and may
        # hold a stray non-dict; every reader here indexes the dict-only
        # sequence, so a raw index would be reported back and then rejected as
        # out of range by the next call that used it.
        new_index = len(_views(document)) - 1

    return {
        "status": "created",
        "dashboard": target or "lovelace",
        "view_index": new_index,
        "title": sanitize_untrusted_text(title, 60),
        # Where to actually find it. A view appended to an existing dashboard is
        # invisible to a user who was told "created" and then went looking under
        # Settings > Dashboards, because no dashboard was created — this is the
        # only thing in the result that points at the page itself.
        "url": _view_url(target, path, new_index),
        # States what was made, and nothing about whether it was the right tool.
        # That is a pre-call decision and belongs in the tool's description; the
        # only turn a restatement here can reach is one that already chose
        # correctly, and on a resumed turn — the dashboard created moments
        # earlier, this its first page — it reads as the wrong tool having been
        # used.
        "note": (
            "This is a new page ON that dashboard, not a new dashboard. It is empty "
            "until you add cards to it."
        ),
    }


async def async_update_view(
    hass: HomeAssistant,
    *,
    target: str | None = None,
    view: object,
    title: str | None = None,
    path: str | None = None,
    icon: str | None = None,
    clear: list[str] | None = None,
    expected_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Change a view's title, path, or icon. Cards are untouched.

    ``clear`` names fields to REMOVE. Setting and clearing need separate
    arguments because an empty string cannot mean "clear" here: `_opt_str`
    treats blank as absent throughout this codebase, precisely because models
    fill unused optional params with `""` — reading that as a clear would strip
    the icon off any view updated by a model that padded its arguments.

    ``expected_fingerprint`` (from ``get_dashboard``) pins the target across the
    read. A rename that lands on the wrong page is quieter than a deletion —
    nothing disappears, so nobody goes looking.
    """
    async with DASHBOARD_LOCK:
        config, error = _writable_dashboard(hass, target)
        if error or config is None:
            return {"error": error or "Dashboard not found."}
        document, error = await _load_config(config)
        if error:
            return {"error": error}

        index, error = resolve_view(document, view)
        if error or index is None:
            return {"error": error}

        target_view = _views(document)[index]
        if expected_fingerprint and view_fingerprint(target_view) != expected_fingerprint:
            return {
                "error": (
                    "That view has changed since it was read — the index now points at "
                    "a different page. Read the dashboard again and retry."
                )
            }

        changes: list[str] = []
        if title and str(title).strip():
            target_view["title"] = str(title).strip()
            changes.append("title")
        if path and str(path).strip():
            slug = str(path).strip()
            clash = [
                i
                for i, v in enumerate(_views(document))
                if i != index and str(v.get("path") or "") == slug
            ]
            if clash:
                return {
                    "error": f"View {clash[0]} already uses the path '{sanitize_untrusted_text(slug, 40)}'."
                }
            target_view["path"] = slug
            changes.append("path")
        if icon and str(icon).strip():
            target_view["icon"] = str(icon).strip()
            changes.append("icon")
        for field in clear or ():
            if field not in ("icon", "path"):
                return {"error": f"clear accepts 'icon' or 'path', not '{field}'."}
            if target_view.pop(field, None) is not None:
                changes.append(f"cleared {field}")

        if not changes:
            return {
                "status": "unchanged",
                "view_index": index,
                "message": "No changes were requested.",
            }
        if error := await _save(config, document):
            return {"error": error}

    return {
        "status": "updated",
        "dashboard": target or "lovelace",
        "view_index": index,
        "changed": changes,
    }


async def async_remove_view(
    hass: HomeAssistant,
    *,
    target: str | None = None,
    view: object,
    expected_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Delete a view and every card on it.

    ``expected_fingerprint`` is the identity check, re-computed here against the
    freshly-loaded document immediately before the save. A view has no id and
    its index shifts whenever an earlier one is removed, so the index alone can
    resolve to a different page by the time the user taps Delete.
    """
    async with DASHBOARD_LOCK:
        config, error = _writable_dashboard(hass, target)
        if error or config is None:
            return {"error": error or "Dashboard not found."}
        document, error = await _load_config(config)
        if error:
            return {"error": error}

        index, error = resolve_view(document, view)
        if error or index is None:
            return {"error": error}

        views = _views(document)
        removed = views[index]
        card_count = len(_flat_cards(removed))
        if expected_fingerprint and view_fingerprint(removed) != expected_fingerprint:
            return {
                "error": (
                    "That view has changed since it was shown — the index now points at "
                    "a different page, or its contents moved. Read the dashboard again "
                    "and retry. Nothing was removed."
                )
            }

        # Removed by OBJECT identity, not by index. ``resolve_view`` indexes the
        # dict-only view list, while ``document["views"]`` is free-form and may
        # hold non-dict entries — a stray ``None`` ahead of the target shifts
        # every position, so applying the filtered index to the raw list deletes
        # the wrong element and reports success for a page still on screen.
        document["views"] = [v for v in document.get("views", []) if v is not removed]
        if error := await _save(config, document):
            return {"error": error}

    return {
        "status": "deleted",
        "dashboard": target or "lovelace",
        "view_index": index,
        "title": sanitize_untrusted_text(removed.get("title") or "", 60),
        "cards_removed": card_count,
    }


async def async_update_card(
    hass: HomeAssistant,
    *,
    target: str | None = None,
    view: object,
    card_index: int,
    card: dict[str, Any],
    expected_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Replace one card outright.

    The replacement is total, not a merge — there is no way to express "keep the
    other keys" in a tool schema, and a half-merged card renders as neither what
    was there nor what was asked for. Call ``get_card`` first.
    """
    if not isinstance(card, dict) or not str(card.get("type", "")).strip():
        return {"error": "card must be an object with a 'type' field."}
    if error := _card_type_error(hass, card):
        return {"error": error}
    if error := _entity_error(hass, card):
        return {"error": error}

    async with DASHBOARD_LOCK:
        config, error = _writable_dashboard(hass, target)
        if error or config is None:
            return {"error": error or "Dashboard not found."}
        document, error = await _load_config(config)
        if error:
            return {"error": error}

        index, error = resolve_view(document, view)
        if error or index is None:
            return {"error": error}

        cards = _flat_cards(_views(document)[index])
        if not 0 <= card_index < len(cards):
            return {
                "error": f"That view has {len(cards)} cards, so index {card_index} is out of range."
            }

        owner, position, existing = cards[card_index]
        if error := _fingerprint_error(existing, expected_fingerprint):
            return {"error": error}

        owner[position] = card
        if error := await _save(config, document):
            return {"error": error}

    return {
        "status": "updated",
        # Where the change landed, encoded, so the reply can link it instead of
        # naming a page the user then has to go and find.
        "url": _view_url(target, str(_views(document)[index].get("path") or "") or None, index),
        "dashboard": target or "lovelace",
        "view_index": index,
        "card_index": card_index,
        "fingerprint": card_fingerprint(card),
    }


async def async_remove_card(
    hass: HomeAssistant,
    *,
    target: str | None = None,
    view: object,
    card_index: int,
    expected_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Remove one card from a view."""
    async with DASHBOARD_LOCK:
        config, error = _writable_dashboard(hass, target)
        if error or config is None:
            return {"error": error or "Dashboard not found."}
        document, error = await _load_config(config)
        if error:
            return {"error": error}

        index, error = resolve_view(document, view)
        if error or index is None:
            return {"error": error}

        cards = _flat_cards(_views(document)[index])
        if not 0 <= card_index < len(cards):
            return {
                "error": f"That view has {len(cards)} cards, so index {card_index} is out of range."
            }

        owner, position, existing = cards[card_index]
        if error := _fingerprint_error(existing, expected_fingerprint):
            return {"error": error}

        owner.pop(position)
        if error := await _save(config, document):
            return {"error": error}

    result: dict[str, Any] = {
        "status": "deleted",
        "dashboard": target or "lovelace",
        "view_index": index,
        "card_index": card_index,
        "card_type": str(existing.get("type") or "") if isinstance(existing, dict) else "",
    }
    # The whole card comes back, which is what makes removal reversible: the
    # tool promises the caller can put it straight back, and a type alone
    # restores none of a card's entities, actions, or styling.
    #
    # Withheld rather than truncated if it will not fit — ``_truncate_result``
    # would trim the card's own lists silently, and a partial card handed back
    # as a restore payload is worse than none, because only one of them looks
    # usable.
    if len(json.dumps(existing, ensure_ascii=False, default=str)) <= _MAX_RESTORE_CHARS:
        result["card"] = existing
    else:
        result["card_omitted"] = True
        result["message"] = (
            "The removed card was too large to return, so it cannot be restored from "
            "this result — undo it in the dashboard editor if that was a mistake."
        )
    return result


def _fingerprint_error(card: Any, expected: str | None) -> str | None:
    """Why the card at this index is not the one the caller was shown.

    Checked against the document about to be written, with nothing awaited in
    between. An absent fingerprint is allowed through so a caller that knows
    exactly what it is doing — or a card placed and removed in one turn — is not
    forced through a read first.
    """
    if not expected:
        return None
    if card_fingerprint(card) != expected:
        return (
            "That card has changed since it was read — the index now points at "
            "something else. Read the view again and retry."
        )
    return None


# An entity id as Home Assistant spells it. Used to tell an entity row from a
# label in a bare ``entities`` list, where both are plain strings.
_ENTITY_ID_RE: Final = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+")


def _unknown_entities(hass: HomeAssistant, card: Any) -> list[str]:
    """Entity ids a card references that do not exist.

    Lovelace validates nothing on save, so a card naming a typo'd entity is
    stored happily and renders as "Entity not found" on the user's dashboard.
    Nothing else catches it: the model composes the card, we write it, and the
    first sign of trouble is a red tile on the wall panel.

    Walks the whole card rather than a fixed key list — an entity id can sit in
    ``entity``, ``entities`` (as a string or as ``{entity: …}``), a nested
    stack's ``cards``, a ``tap_action`` target, or a custom card's own schema.

    A list carries its parent key down to its elements, so the bare form the
    entities card is normally written in — ``entities: ["light.one"]``, by far
    the most common shape — arrives here keyed ``entities``. Checking only
    ``entity``/``entity_id`` let exactly that spelling through unvalidated.
    Strings under ``entities`` are shape-checked first: a row there may be a
    label or a divider on some cards, and refusing those would block a card the
    home can render perfectly well. A typo we care about is a typo in an
    entity id, and an entity id always has a domain and a dot.
    """
    found: list[str] = []

    def walk(node: Any, key: str | None = None) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for item in node:
                walk(item, key)
        elif (
            isinstance(node, str)
            and key in ("entity", "entity_id", "entities")
            # Shape-checked on EVERY key, not just `entities`. A custom card may
            # hold a template where an id goes — button-card's
            # `[[[ return ... ]]]`, or Jinja — and that is a valid card the home
            # renders, not a typo. The state lookup would find no such entity
            # and refuse the whole write. A typo worth catching is a typo in an
            # entity id, which always has a domain and a dot.
            and _ENTITY_ID_RE.fullmatch(node)
        ):
            found.append(node)

    walk(card)
    return sorted({e for e in found if hass.states.get(e) is None})


# Card types named after an entity DOMAIN. This is the whole list, and it is
# the only thing here that needs maintaining — a handful of names that change
# about once a year, rather than HA's card catalogue, which grows every release
# and can never include the custom cards a home has installed.
#
# The check is inverted for that reason: instead of asking "is this a known
# card?" (unanswerable — Lovelace has no server-side validator and custom cards
# come from resources this integration cannot enumerate), it asks "is this the
# name of a domain in THIS home that has no card of its own?". A model writing
# `type: fan` for a fan is drawing on the home's own vocabulary, which is
# exactly where the wrong guesses come from, and an unknown type we have never
# heard of is left alone rather than refused on a guess.
_DOMAIN_NAMED_CARDS: Final = frozenset(
    {
        "light",
        "lock",
        "thermostat",
        "humidifier",
        "water_heater",
        "sensor",
        "calendar",
        "map",
        "todo_list",
        "media_control",
        "alarm_panel",
        "weather_forecast",
    }
)

# Structural elements of a SECTIONS VIEW, not cards. A different explanation:
# the model reached for real Lovelace vocabulary, just in the wrong position.
_VIEW_STRUCTURE_TYPES: Final = frozenset({"section", "view"})


def _card_type_error(hass: HomeAssistant, card: Any) -> str | None:
    """Refuse a card type Home Assistant cannot render.

    Lovelace resolves a type to a custom element and calls setConfig on it; a
    type with no element renders "Unknown type encountered" on the user's wall.
    The document validates, saves, and breaks — a card is just a dict with a
    `type`, so nothing else catches it, and unlike an automation there is no
    `async_validate_config_item` to ask.

    What can be answered without a catalogue is the shape the mistakes take: a
    domain the home has, used as a card. Anything else — including every
    `custom:` card and any card type newer than this code — passes.
    """
    if not isinstance(card, dict):
        return None
    # Every card in the tree, not just the outer one. A container holds its
    # children under `cards`, and the wrong type is usually one of THOSE — a
    # grid of tiles with a single `type: fan` among them. Checking the root
    # alone passed the container and stored the broken child, which is exactly
    # what `_unknown_entities` walks the whole card to avoid.
    domains: set[str] | None = None
    for nested in _cards_in_tree(card):
        raw = str(nested.get("type", "")).strip()
        kind = raw.lower().replace("-", "_")
        if not kind or kind.startswith("custom:"):
            continue
        if domains is None:
            domains = {eid.split(".", 1)[0] for eid in hass.states.async_entity_ids()}
        if error := _one_card_type_error(raw, kind, domains):
            return error
    return None


def _cards_in_tree(card: dict[str, Any]) -> list[dict[str, Any]]:
    """The card and every card nested inside it.

    Only dicts under a `cards` key count as cards. A `features` list holds
    feature configs which carry their own `type` vocabulary (`light-brightness`,
    `fan-speed`) and are not cards — walking those would refuse every tile that
    has one.
    """
    found: list[dict[str, Any]] = [card]
    stack: list[Any] = [card]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        nested = node.get("cards")
        if isinstance(nested, list):
            for item in nested:
                if isinstance(item, dict):
                    found.append(item)
                    stack.append(item)
        # A sections view's grid, and the conditional card's single child.
        for key in ("sections", "card"):
            value = node.get(key)
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, dict):
                    found.append(item)
                    stack.append(item)
    return found


def _one_card_type_error(raw: str, kind: str, domains: set[str]) -> str | None:
    """The refusal for a single card type, or None."""
    safe = sanitize_untrusted_text(raw, 40)
    if kind in _VIEW_STRUCTURE_TYPES:
        return (
            f"'{safe}' is not a card type — it is how a sections VIEW holds its "
            f"cards, and Home Assistant renders it as 'Configuration error'. To "
            f"group cards inside a view, use a container card ('grid', "
            f"'vertical-stack', 'horizontal-stack') and put them in its 'cards'. "
            f"To get a sections layout, create the view with sections=true."
        )
    if kind in _DOMAIN_NAMED_CARDS or kind not in domains:
        return None
    return (
        f"'{safe}' is a domain, not a card type, so the dashboard would show "
        f"'Unknown type encountered: {safe}'. Use 'tile' for a single entity or "
        f"'entities' for a list. Only these domains have a card of their own: "
        f"{', '.join(sorted(_DOMAIN_NAMED_CARDS))}."
    )


def _entity_error(hass: HomeAssistant, card: Any) -> str | None:
    """Refuse a card that names entities the home does not have."""
    if missing := _unknown_entities(hass, card):
        return (
            f"This card references {len(missing)} entity id"
            f"{'s' if len(missing) != 1 else ''} that do not exist: "
            f"{', '.join(missing[:5])}. Home Assistant would store it and render "
            f"'Entity not found'. Look the entities up with search_entities and retry."
        )
    return None


async def async_move_card(
    hass: HomeAssistant,
    *,
    target: str | None = None,
    view: object,
    from_index: int,
    to_index: int | None = None,
    to_dashboard: str | None = None,
    to_view: object = None,
    expected_fingerprint: str | None = None,
    expected_view_fingerprint: str | None = None,
    expected_to_view_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Move a card: within a view, onto another view, or onto another dashboard.

    The same-view case is a REORDER, and the only way to achieve one. Without it
    a caller can append, replace in place, or remove — so "keep the garage door
    at the top" is unachievable, and the attempt turns into rewriting cards by
    hand, which is how a mistyped entity reaches the dashboard.

    The other two are a TRANSFER, which is the same operation seen from one
    dashboard rather than one view. Nothing else here performs one:
    ``get_dashboard_card`` then ``insert_dashboard_card`` then
    ``remove_dashboard_card`` only LOOKS equivalent, because the caller
    re-serialises the card in between — and the caller is an LLM working from
    what it was shown, so it drops whatever it did not think to copy, and a card
    too large to fetch intact cannot be moved at all. That composition also has
    no safe ordering: remove-then-insert loses the card outright when the insert
    is refused.

    Every path moves the card OBJECT, so a move can never alter what the card
    shows.

    ``to_dashboard`` omitted means the source dashboard. ``to_view`` omitted
    means the source view on the same dashboard, and the FIRST page of a
    different one — a transfer names the dashboard, and which page it lands on
    is rarely the point.

    A transfer has TWO views to pin, and ``expected_view_fingerprint`` covers
    only the source. On a same-view move they are the same object, so one
    fingerprint answers for both; the moment the destination is somewhere else
    that guarantee is silently gone, and it is the destination that carries the
    index. ``expected_to_view_fingerprint`` is the other half — without it a
    page reordered between the read and this call takes the card at an index
    that no longer means what the caller read, on a page it may not even have
    looked at.
    """
    from .recipes.dashboard import _insert_target_cards  # noqa: PLC0415

    async with DASHBOARD_LOCK:
        src_config, error = _writable_dashboard(hass, target)
        if error or src_config is None:
            return {"error": error or "Dashboard not found."}
        src_document, error = await _load_config(src_config)
        if error:
            return {"error": error}

        src_index, error = resolve_view(src_document, view)
        if error or src_index is None:
            return {"error": error}

        # Compared by resolved IDENTITY, never by the argument strings. The
        # default dashboard answers to None, "" and "lovelace" at once, so
        # comparing what was passed reads a move onto the same dashboard as a
        # cross-dashboard transfer — which then loads that one document twice
        # and saves the second copy over the first, discarding either the
        # removal or the insert depending on the order they land in.
        if to_dashboard is None:
            dst_config, dst_document = src_config, src_document
        else:
            dst_config, error = _writable_dashboard(hass, to_dashboard)
            if error or dst_config is None:
                # Marked as the DESTINATION's problem. Both dashboards can be
                # refused for the same reasons and the messages read alike, so
                # unmarked the caller retries against the source it was never
                # told was fine.
                return {
                    "error": (
                        f"The destination dashboard cannot be written to: "
                        f"{error or 'Destination dashboard not found.'}"
                    )
                }
            if dst_config is src_config:
                dst_document = src_document
            else:
                dst_document, error = await _load_config(dst_config)
                if error:
                    return {"error": f"The destination dashboard cannot be written to: {error}"}

        cross_dashboard = dst_document is not src_document
        if to_view is not None:
            dst_ref: object = to_view
        elif cross_dashboard:
            # Its first page. Resolving the SOURCE view's path or title against
            # another dashboard fails on every dashboard that does not happen to
            # carry the same name, which is most of them.
            dst_ref = 0
        else:
            dst_ref = src_index

        if cross_dashboard and not _views(dst_document):
            # Exactly the state a dashboard Selora has just created is in: the
            # entry exists and it has no pages. Naming the tool that fixes it is
            # the difference between the model making the page and handing the
            # job back to the user.
            return {
                "error": (
                    "The destination dashboard has no pages yet, so there is nowhere to "
                    "put the card. Call add_dashboard_view to give it one, then move the "
                    "card onto it."
                )
            }
        dst_index, error = resolve_view(dst_document, dst_ref)
        if error or dst_index is None:
            return {"error": error}

        src_view_obj = _views(src_document)[src_index]
        cards = _flat_cards(src_view_obj)
        if not 0 <= from_index < len(cards):
            return {
                "error": f"That view has {len(cards)} cards, so index {from_index} is out of range."
            }

        owner, position, moving = cards[from_index]
        if error := _fingerprint_error(moving, expected_fingerprint):
            return {"error": error}
        # The card fingerprint pins the SOURCE only. Another edit between the
        # read and this call can leave it at from_index while to_index now
        # names somewhere else — the move then succeeds and produces a layout
        # nobody asked for.
        if (
            expected_view_fingerprint
            and view_fingerprint(src_view_obj) != expected_view_fingerprint
        ):
            return {
                "error": (
                    "That view has changed since it was read, so the destination index "
                    "no longer means what it did. Read the dashboard again and retry."
                )
            }

        dst_view_obj = _views(dst_document)[dst_index]
        same_view = dst_view_obj is src_view_obj
        # The DESTINATION's own pin, and the one that matters most on a
        # transfer: the index the caller named is relative to that page, on a
        # dashboard it may have read in a separate call. Checked before either
        # document is mutated, so a stale destination cannot leave the card
        # pulled out of the source. A caller that passes it for a same-view move
        # is checking the source twice, which is harmless and stays correct.
        if (
            expected_to_view_fingerprint
            and view_fingerprint(dst_view_obj) != expected_to_view_fingerprint
        ):
            return {
                "error": (
                    "The destination view has changed since it was read, so the "
                    "destination index no longer means what it did. Read that dashboard "
                    "again and retry."
                )
            }
        dst_cards = cards if same_view else _flat_cards(dst_view_obj)

        if same_view:
            if to_index is None:
                return {
                    "error": (
                        "to_index is required to reorder a card within a view. To move it "
                        "to another page or dashboard, pass to_view or to_dashboard."
                    )
                }
            if not 0 <= to_index < len(dst_cards):
                return {
                    "error": f"That view has {len(dst_cards)} cards, so index {to_index} is out of range."
                }
        elif to_index is not None and not 0 <= to_index <= len(dst_cards):
            # Inclusive upper bound: the destination gains a card, so landing
            # past its last one is the end of the view rather than an overrun.
            return {
                "error": (
                    f"The destination view has {len(dst_cards)} cards, so index "
                    f"{to_index} is out of range."
                )
            }

        # Snapshotted before anything is touched, so a failed save can put the
        # cache back — see `_save`. Two documents means two snapshots, and one
        # document shared by both views means one.
        src_before = copy.deepcopy(src_document)
        dst_before = copy.deepcopy(dst_document) if cross_dashboard else src_before

        # Re-flattened after the removal, because pulling the card out shifts
        # every later index — including the destination the caller named. That
        # holds for a transfer between two views of ONE dashboard as well, where
        # both of them live in the same document.
        owner.pop(position)
        remaining = _flat_cards(dst_view_obj)
        if to_index is not None and to_index < len(remaining):
            # Land BEFORE whatever now sits at the destination — plain
            # ``pop(from); insert(to)`` semantics. Adding one for a forward move
            # lands after it instead, so moving 0 → 1 in [A, B, C] gives
            # [B, C, A] rather than the [B, A, C] that was asked for.
            dest_owner, dest_position, _ = remaining[to_index]
            dest_owner.insert(dest_position, moving)
        elif remaining:
            # Past the last remaining card, or no destination named at all: the
            # end of the view.
            dest_owner, dest_position, _ = remaining[-1]
            dest_owner.insert(dest_position + 1, moving)
        elif same_view:
            # It was the view's only card, so there is no destination to land
            # relative to. Back into the list it came from — `_insert_target_cards`
            # would send it to the FIRST section, silently moving a lone card in
            # a later section somewhere the caller did not ask for.
            owner.append(moving)
        else:
            # An empty destination view, where for a sections view the cards go
            # in the first section — a top-level `cards` key would store fine
            # and render nothing.
            _insert_target_cards(dst_view_obj).append(moving)

        # Where it actually landed, by identity. An appended card's index is the
        # one thing the caller cannot work out from its own arguments, and it is
        # what addresses the card on every later call.
        landed = next(
            (i for i, (_, _, card) in enumerate(_flat_cards(dst_view_obj)) if card is moving),
            to_index if to_index is not None else 0,
        )
        dst_path = str(dst_view_obj.get("path") or "") or None

        if cross_dashboard:
            # Two documents, so two saves and no transaction. DESTINATION
            # FIRST: when the second one fails the card is on both dashboards,
            # which the user can see and undo. The other order loses it, and a
            # card nobody kept a copy of cannot be put back.
            #
            # Both saves carry their snapshot, which is what makes these two
            # sentences TRUE rather than merely intended: without the rollback a
            # failed write still lands in HA's cache, so "the card was not
            # moved" would be said over a destination already showing it, and
            # "it now appears on both" over a source it had just vanished from.
            if error := await _save(dst_config, dst_document, dst_before):
                return {"error": f"The card was not moved: {error}"}

            # Confirmed on DISK before the removal, because a `_save` that
            # reports success has only been ACCEPTED — HA swallows an ordinary
            # write failure (see `_copies_on_disk`). Removing the source on the
            # strength of that is how the card is lost outright: both halves
            # look fine, the cache agrees, and the dashboard it was moved to is
            # empty after the next restart. Destination-first only protects
            # anything if the destination is known to have landed.
            moved_fp = card_fingerprint(moving)
            landed_copies = await _copies_on_disk(dst_config, dst_index, moved_fp)
            if landed_copies is not None and landed_copies < _copies_in_view(
                dst_view_obj, moved_fp
            ):
                await _save(dst_config, dst_before)
                return {
                    "error": (
                        "The card was not moved: Home Assistant accepted the write to the "
                        "destination dashboard but it did not reach disk, so removing it "
                        "from the original would have lost it. Its config directory may be "
                        "read-only or out of space, or Home Assistant may be shutting down."
                    )
                }

            if error := await _save(src_config, src_document, src_before):
                return {
                    "error": (
                        "The card was added to the destination dashboard but could not be "
                        f"removed from the original, so it now appears on both: {error}"
                    )
                }
            # Same question of the source, where a silent failure costs a
            # duplicate rather than the card — worth reporting, not worth
            # undoing a destination that did land.
            left_copies = await _copies_on_disk(src_config, src_index, moved_fp)
            if left_copies is not None and left_copies > _copies_in_view(src_view_obj, moved_fp):
                return {
                    "error": (
                        "The card was added to the destination dashboard, but Home "
                        "Assistant did not write the removal from the original to disk, so "
                        "it will appear on both after a restart. Remove it from "
                        f"'{sanitize_untrusted_text(target or 'lovelace', 60)}' by hand."
                    )
                }
        elif error := await _save(src_config, src_document, src_before):
            return {"error": error}

    result: dict[str, Any] = {
        "status": "moved",
        "dashboard": target or "lovelace",
        "view_index": src_index,
        "from_index": from_index,
        "to_index": landed,
    }
    if not same_view:
        destination = to_dashboard if to_dashboard is not None else target
        result["to_dashboard"] = destination or "lovelace"
        result["to_view_index"] = dst_index
        # A card moved to another page is invisible to a user who was told
        # "moved" and then looked where it used to be.
        result["url"] = _view_url(destination, dst_path, dst_index)
    return result


async def async_group_cards(
    hass: HomeAssistant,
    *,
    target: str | None = None,
    view: object,
    card_indices: list[int],
    container: dict[str, Any],
    expected_view_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Move existing cards into a container card, in place.

    This is how "put these three on the same row" is done. A masonry view has no
    rows — cards flow into columns — so side-by-side placement is a *container*,
    not an ordering, and no amount of reordering achieves it.

    The existing card objects are moved into the container untouched. That is
    the point: the alternative is the caller re-describing each card from a
    summary, which loses whatever it did not think to copy and invents entity
    ids that were never there.

    The container lands at the position of the first grouped card, so the group
    stays where the user was already looking.

    ``container`` is the caller's own card config — ``{"type": "grid",
    "columns": 3}``, a stack, whatever fits — and only its ``cards`` is filled
    in. The caller already knows Lovelace's card schemas, so restating which
    container types exist and what each one's options mean would be a second,
    staler copy of that knowledge here, and every option it did not think to
    include would be unreachable.

    Every card index is relative to the view as it was read, so a stale view
    invalidates all of them at once — hence ``expected_view_fingerprint``.
    """
    if not isinstance(container, dict) or not str(container.get("type", "")).strip():
        return {"error": "container must be a card object with a 'type' field."}
    # The container is a caller-supplied card like any other, so it carries the
    # same obligation: a custom one can name entities of its own, and Lovelace
    # stores a typo happily and renders "Entity not found" on the wall panel.
    # The cards being grouped were validated when they were written.
    if error := _card_type_error(hass, container):
        return {"error": error}
    if error := _entity_error(hass, container):
        return {"error": error}

    wanted = sorted({i for i in card_indices if isinstance(i, int)})
    if len(wanted) < 2:
        return {"error": "Give at least two card indices to group."}

    async with DASHBOARD_LOCK:
        config, error = _writable_dashboard(hass, target)
        if error or config is None:
            return {"error": error or "Dashboard not found."}
        document, error = await _load_config(config)
        if error:
            return {"error": error}

        index, error = resolve_view(document, view)
        if error or index is None:
            return {"error": error}

        view_obj = _views(document)[index]
        if expected_view_fingerprint and view_fingerprint(view_obj) != expected_view_fingerprint:
            return {
                "error": (
                    "That view has changed since it was read, so the card indices no "
                    "longer mean what they did. Read the dashboard again and retry."
                )
            }

        cards = _flat_cards(view_obj)
        if out_of_range := [i for i in wanted if not 0 <= i < len(cards)]:
            return {
                "error": (
                    f"That view has {len(cards)} cards, so "
                    f"{', '.join(str(i) for i in out_of_range)} is out of range."
                )
            }

        grouped = [cards[i][2] for i in wanted]
        anchor_owner, anchor_position, _ = cards[wanted[0]]

        container_card: dict[str, Any] = {**container, "cards": grouped}

        # Removed by identity, high index first, so earlier positions stay valid.
        for i in reversed(wanted):
            owner, position, _ = cards[i]
            owner.pop(position)

        # Where the first grouped card was, which is the whole point of the
        # anchor. Correct when nothing else remains too: an empty `anchor_owner`
        # takes the insert at 0, whereas `_insert_target_cards` would put the
        # group in the FIRST section regardless of which one it came from.
        anchor_owner.insert(min(anchor_position, len(anchor_owner)), container_card)

        if error := await _save(config, document):
            return {"error": error}

    return {
        "status": "grouped",
        "dashboard": target or "lovelace",
        "view_index": index,
        "container": container_card.get("type"),
        "grouped_card_count": len(grouped),
    }


# ── Client-executed dashboard creation ───────────────────────────────────────
#
# Creating a dashboard ENTRY needs `DashboardsCollection`, which lovelace keeps
# as a local and exposes only through its admin-only `lovelace/dashboards/*`
# websocket commands. We cannot reach it; the PANEL can, because it is already
# an authenticated websocket client.
#
# So this half only ever PROPOSES. It validates against HA's own create schema,
# returns a closed intent, and the panel builds the fixed websocket call from
# it. The model never authors a websocket payload — if it could, it could issue
# any admin command through the user's session.

_DASHBOARD_SLUG_RE: Final = re.compile(r"[a-z0-9-]+")


def _dashboard_slug(title: str, url_path: str | None) -> str:
    """A url_path HA will accept, derived from the title when not given.

    Uses HA's own ``slugify``, as `script_manager` and `scene_utils` do, rather
    than folding the string through an ASCII character class. This ships in 13
    locales: a title written in the user's own script — "Кухня", "厨房" — has no
    ASCII to keep, so it reduced to nothing and the request was refused as
    having "no usable URL path", and a merely accented one lost the accented
    letters outright ("Küche Öl" → "k-che-l"). ``slugify`` transliterates
    instead ("kukhnia", "chu-fang", "kuche-ol"), which is both a path HA
    accepts and one the user can recognise.
    """
    from homeassistant.util import slugify  # noqa: PLC0415

    raw = str(url_path or title or "").strip()
    slug = slugify(raw, separator="-")
    # ``slugify`` substitutes the literal "unknown" when nothing of the input
    # survives, so a title of "!!!" would quietly become /unknown — and the
    # next one would collide with it. Report it as unusable instead, which is
    # what the caller's empty-slug branch already says, unless "unknown" is
    # what was actually asked for.
    if slug == "unknown" and "unknown" not in raw.casefold():
        return ""
    return slug


def _panel_exists(hass: HomeAssistant, url_path: str) -> bool:
    """Whether a panel already answers to this URL.

    `frontend.async_panel_exists` is absent from some supported HA versions,
    while the registry it reads — `hass.data[DATA_PANELS]` — is present in all
    of them, so fall back to reading it directly.
    """
    from homeassistant.components import frontend  # noqa: PLC0415

    panel_exists = getattr(frontend, "async_panel_exists", None)
    if panel_exists is not None:
        return bool(panel_exists(hass, url_path))
    return url_path in hass.data.get(frontend.DATA_PANELS, {})


async def async_initialize_created_dashboard(hass: HomeAssistant, url_path: str) -> bool:
    """Give a just-created dashboard a stored document, so it can be written to.

    ``lovelace/dashboards/create`` creates the ENTRY and nothing else: the
    dashboard has no stored config, and ``LovelaceStorage.async_get_info``
    reports that as ``mode: auto-gen`` — the same answer a generated Overview
    gives, because from storage's side they are the same state. So without this
    every write to the dashboard Selora just made is refused with the Take
    control note, `list_dashboards` calls it ``editable: false``, and the user
    is told to go and do by hand the thing they had just asked for. Creating a
    dashboard nobody can then fill is not creating a dashboard.

    Saving an empty document is what Take control does, minus the strategy
    render there is no server-side way to perform. The dashboard is empty
    either way — the difference is that this one is EDITABLE, and the next
    request can put the cards on it.

    Only ever when nothing is stored: a document that exists is never touched,
    so a re-report cannot blank a dashboard that has since been filled.
    Returns whether it seeded.
    """
    from homeassistant.components.lovelace.const import ConfigNotFound  # noqa: PLC0415

    config, error = _writable_dashboard(hass, url_path)
    if error or config is None:
        # The entry not being registered yet is not worth failing the report
        # over — the create itself succeeded, and the next write reports its own
        # problem. Same for a caller-hidden dashboard.
        _LOGGER.debug("Cannot initialize dashboard %s: %s", url_path, error)
        return False

    save = getattr(config, "async_save", None)
    if save is None:
        return False

    async with DASHBOARD_LOCK:
        try:
            await config.async_load(False)
        except ConfigNotFound:
            pass
        except Exception:  # noqa: BLE001 — a failed read must not blank a document
            _LOGGER.debug("Not initializing %s: its config could not be read", url_path)
            return False
        else:
            # Something is stored. Never overwrite it.
            return False

        try:
            await save({"views": []})
        except Exception as exc:  # noqa: BLE001 — the dashboard exists either way
            _LOGGER.warning("Could not initialize dashboard %s: %s", url_path, exc)
            return False
    return True


async def async_propose_dashboard_delete(hass: HomeAssistant, target: str) -> dict[str, Any]:
    """Validate a delete-dashboard request and hand back a closed intent.

    Deletes nothing. Like creation, removing a dashboard ENTRY needs
    `DashboardsCollection`, which lovelace publishes only to its admin-only
    websocket commands — so the panel performs it, and everything that can be
    known in advance is checked here rather than after the button.

    The card names the blast radius. HA deletes the dashboard and its stored
    document together, so every view and every card on it goes; a count is the
    difference between an informed confirmation and a surprised one.
    """
    target = str(target or "").strip()

    config, error = _lovelace_dashboard(hass, target)
    if error or config is None:
        return {"error": error or "Dashboard not found."}

    # Whether this IS the default, not whether it is spelled like one.
    # `/default` is a URL path a user can genuinely have — single-word paths
    # are allowed and this module's own create tool makes them — so reserving
    # the STRING left that dashboard undeletable while telling its owner it was
    # the built-in Overview. Asking the resolver a second time is what keeps
    # the two answers in agreement: it already collapses "", None and
    # "lovelace" onto whichever key HA is actually serving.
    default_config, _ = _lovelace_dashboard(hass, None)
    if default_config is not None and config is default_config:
        return {
            "error": (
                "The default dashboard cannot be deleted — Home Assistant always "
                "keeps one. Name the dashboard's url_path from list_dashboards."
            )
        }

    from homeassistant.components.lovelace.const import MODE_STORAGE  # noqa: PLC0415

    if getattr(config, "mode", None) != MODE_STORAGE:
        return {
            "error": (
                f"'{sanitize_untrusted_text(target, 60)}' is a YAML-mode dashboard. "
                f"It is defined in configuration.yaml, so it has to be removed there."
            )
        }

    title = _dashboard_title(config, target)
    # Read for the label only, so a document that cannot be loaded — an
    # auto-generated board, a strategy — does not block a deletion the user is
    # entitled to make.
    document, _ = await _load_or_reason(config)

    # UNKNOWN, not zero. `_load_or_reason` returns None precisely when the
    # dashboard renders content we cannot enumerate — a generated Overview is
    # covered in cards — so counting `{}` would put "0 views, 0 cards" on the
    # confirmation for an irreversible delete. A false blast radius is worse
    # than no number: it invites the tap.
    view_count: int | None = None
    card_count: int | None = None
    if document is not None:
        views = _views(document)
        view_count = len(views)
        # Every card, not just the addressable ones. `_flat_cards` yields what
        # a card index can name; a grid holding twenty tiles is one of those
        # and all twenty go with it.
        card_count = sum(
            # A stored card need not be a dict — Lovelace storage is free-form.
            # It has no tree to walk, but it is an entry the delete removes, so
            # it counts as one rather than as nothing. `_flat_cards` yields
            # (owning_list, position, card); the card is the third element.
            len(_cards_in_tree(card)) if isinstance(card, dict) else 1
            for view in views
            for _, _, card in _flat_cards(view)
        )

    # The collection id, which is what HA deletes by and the only STABLE handle
    # on this dashboard. A url_path is not: delete a dashboard and make another
    # at the same path, and a card approved for the first would remove the
    # second. `config.config` is the collection item the dashboard was built
    # from — `_dashboard_title` reads the same mapping.
    meta = getattr(config, "config", None)
    meta = meta if isinstance(meta, dict) else {}
    dashboard_id = str(meta.get("id") or "")

    return {
        "requires_approval": True,
        "client_action": {
            "kind": "delete_dashboard",
            # Both: the id is what identifies it, the url_path is what the user
            # named and what the panel checks the id still answers to.
            "dashboard_id": dashboard_id,
            "url_path": target,
            "title": title,
            # Omitted rather than zeroed when the document cannot be read —
            # the panel says "contents unknown" instead of naming a count.
            **({"view_count": view_count} if view_count is not None else {}),
            **({"card_count": card_count} if card_count is not None else {}),
            # The metadata as it stands now, so the panel can tell this
            # dashboard from a DIFFERENT one wearing its id. A collection id is
            # the url_path (`DashboardsCollection._get_suggested_id`), so
            # deleting a dashboard frees BOTH handles at once: make another at
            # the same path between the proposal and the tap and it inherits
            # them. Nothing a dashboard carries is immutable enough to be a
            # true identity, so this is the same four fields the create
            # reconciles on — a recreation made identical is one nobody can
            # tell apart, the user included.
            #
            # Raw, not `title`: that one is sanitized and truncated for the
            # card label, and comparing it against what HA stores would report
            # a mismatch for any dashboard named at length.
            "expected": {
                "title": str(meta.get("title") or ""),
                "icon": str(meta.get("icon") or ""),
                "require_admin": bool(meta.get("require_admin")),
                "show_in_sidebar": bool(meta.get("show_in_sidebar", True)),
            },
        },
    }


async def async_propose_dashboard(
    hass: HomeAssistant,
    *,
    title: str,
    url_path: str | None = None,
    icon: str | None = None,
    require_admin: bool = False,
    show_in_sidebar: bool = True,
) -> dict[str, Any]:
    """Validate a new-dashboard request and hand back a closed intent.

    Creates nothing. Everything HA's schema can reject is checked here so the
    user is not shown a button that fails when they press it, but the create
    itself happens in the panel under the user's own credentials.
    """
    from homeassistant.helpers import config_validation as cv  # noqa: PLC0415
    import voluptuous as vol  # noqa: PLC0415

    title = str(title or "").strip()
    if not title:
        return {"error": "A dashboard title is required."}

    slug = _dashboard_slug(title, url_path)
    if not slug or not _DASHBOARD_SLUG_RE.fullmatch(slug):
        return {
            "error": (
                f"'{sanitize_untrusted_text(str(url_path or title), 60)}' does not give a "
                f"usable URL path. Use lowercase letters, numbers and hyphens."
            )
        }
    # Validated with HA's own validator, not a regex of our own: the schema this
    # proposal is destined for uses `cv.icon`, so anything it rejects makes a
    # Create button that always fails after the user presses it.
    clean_icon = str(icon).strip() if icon and str(icon).strip() else None
    if clean_icon is not None:
        try:
            cv.icon(clean_icon)
        except vol.Invalid:
            return {
                "error": (
                    f"'{sanitize_untrusted_text(clean_icon, 40)}' is not a usable icon. "
                    f"Home Assistant wants the 'prefix:name' form, like 'mdi:chef-hat'."
                )
            }

    if _panel_exists(hass, slug):
        return {
            "error": (
                f"A dashboard or panel already uses the URL '/{slug}'. Pick another "
                f"url_path, or edit the existing one instead."
            )
        }

    return {
        "requires_approval": True,
        "client_action": {
            # Every field here is validated above and allowlisted by the panel.
            # The panel constructs `lovelace/dashboards/create` itself; it never
            # forwards anything shaped by the model.
            "kind": "create_dashboard",
            "title": title,
            "url_path": slug,
            "icon": clean_icon,
            "require_admin": bool(require_admin),
            "show_in_sidebar": bool(show_in_sidebar),
            # HA rejects a single-word path unless told otherwise, and a
            # one-word title is the common case ("Kitchen").
            "allow_single_word": "-" not in slug,
            "label": f"Create the {sanitize_untrusted_text(title, 60)} dashboard at /{slug}",
        },
    }
