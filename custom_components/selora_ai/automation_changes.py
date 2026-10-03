"""What one automation version changed against the version before it.

Computed once, when the version is saved, and stored on it as ``changes`` —
the History tab reads the list back and phrases it in the viewer's language,
so opening the tab never re-compares documents.

The output is concrete on purpose. "Changed the actions and the description"
is true of every refinement and tells nobody anything; what the user wants to
read is that the delay went from 5 to 2 minutes. So a changed item carries the
values that differ inside it (``details``), and the item itself when it is
small enough for the panel to describe in Flow-tab wording.
"""

from __future__ import annotations

from collections import Counter
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .types import LeafChange, VersionChange

_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("triggers", ("triggers", "trigger")),
    ("conditions", ("conditions", "condition")),
    ("actions", ("actions", "action")),
)

# `id` is the automation's identity and `initial_state` is the enabled flag
# Selora manages itself — neither is an edit a user made.
_IGNORED_FIELDS: frozenset[str] = frozenset(
    {"id", "initial_state", *(k for _, keys in _SECTIONS for k in keys)}
)

# Leaf values kept per changed item. Past this the panel falls back to
# "<section> updated" with the count rather than naming one of many.
_MAX_DETAILS = 3

# An item larger than this is not copied into the change list: the panel only
# describes items short enough to read in one line, and the full item is in
# the version's own `data` anyway.
_MAX_ITEM_CHARS = 2000


# Bumped whenever the comparison changes, so versions stored under an older
# one are recomputed on the next load instead of keeping a worse answer.
CHANGES_FORMAT = 2

# Home Assistant's own renames. A version rewritten into the current syntax —
# which a chat refinement routinely does — must compare equal to the old
# spelling, or the summary reports "platform removed, trigger set to state"
# for an automation that does exactly what it did before.
_KEY_RENAMES: dict[str, str] = {
    "platform": "trigger",
    "service": "action",
    "data_template": "data",
}

# Keys whose value may be one item or a list of them, meaning the same thing.
_LIST_KEYS: frozenset[str] = frozenset(
    {"sequence", "conditions", "then", "else", "default", "parallel", "choose"}
)
_TARGET_KEYS: frozenset[str] = frozenset({"entity_id", "device_id", "area_id"})

# Free-form payloads: their keys belong to the service being called, so none
# of the renames above may touch them.
_OPAQUE_KEYS: frozenset[str] = frozenset({"data", "data_template", "variables", "event_data"})


def _canonicalize(value: Any) -> Any:
    """Rewrite equivalent spellings to one, so only real edits differ."""
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if not isinstance(value, dict):
        return value
    out: dict[str, Any] = {}
    for key, item in value.items():
        new_key = _KEY_RENAMES.get(key, key)
        if new_key != key and new_key in value:
            # Both spellings present: keep the current one, drop the legacy.
            continue
        if key in _OPAQUE_KEYS:
            out[new_key] = item
            continue
        if new_key in _LIST_KEYS and not isinstance(item, list):
            item = [item]
        if new_key in _TARGET_KEYS and isinstance(item, list) and len(item) == 1:
            item = item[0]
        out[new_key] = _canonicalize(item)
    return out


def _canonical(value: Any) -> str:
    """Return a key-order-independent serialization for equality checks."""
    return json.dumps(value, sort_keys=True, default=str)


def _section_items(data: dict[str, Any], keys: tuple[str, ...]) -> list[Any]:
    """Return a section's items as a list, accepting either key spelling."""
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        items = value if isinstance(value, list) else [value]
        return [_canonicalize(item) for item in items if item is not None]
    return []


def _target_entity(node: dict[str, Any]) -> str | None:
    """Return the single entity an action or condition is about, if any."""
    target = node.get("target")
    for source in (target if isinstance(target, dict) else {}, node):
        entity = source.get("entity_id")
        if isinstance(entity, str):
            return entity
    return None


def _leaf_changes(
    before: Any, after: Any, path: list[str | int], entity: str | None = None
) -> list[LeafChange]:
    """Return every leaf value that differs between two values.

    Each leaf carries the entity of the nearest enclosing step, so "16 → 15"
    can be read as the heat pump's temperature rather than a bare number.
    """
    if isinstance(before, dict) and isinstance(after, dict):
        entity = _target_entity(after) or _target_entity(before) or entity
        out: list[LeafChange] = []
        for key in sorted(set(before) | set(after), key=str):
            out.extend(_leaf_changes(before.get(key), after.get(key), [*path, key], entity))
        return out
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        out = []
        for i, (b, a) in enumerate(zip(before, after, strict=True)):
            out.extend(_leaf_changes(b, a, [*path, i], entity))
        return out
    if _canonical(before) == _canonical(after):
        return []
    leaf: LeafChange = {"path": path, "before": before, "after": after}
    if entity and path and path[-1] != "entity_id":
        leaf["entity"] = entity
    return [leaf]


def _small(item: Any) -> bool:
    return len(_canonical(item)) <= _MAX_ITEM_CHARS


def _item_changed(section: str, index: int, before: Any, after: Any) -> VersionChange:
    leaves = _leaf_changes(before, after, [])
    change: VersionChange = {
        "kind": "item_changed",
        "section": section,
        "index": index,
        "details": leaves[:_MAX_DETAILS],
        "detail_count": len(leaves),
    }
    if _small(before) and _small(after):
        change["before"] = before
        change["after"] = after
    return change


def _item_presence(kind: str, section: str, item: Any) -> VersionChange:
    change: VersionChange = {"kind": kind, "section": section}
    if _small(item):
        change["after" if kind == "item_added" else "before"] = item
    return change


def _section_changes(section: str, before: list[Any], after: list[Any]) -> list[VersionChange]:
    if _canonical(before) == _canonical(after):
        return []
    before_keys = [_canonical(item) for item in before]
    after_keys = [_canonical(item) for item in after]
    if Counter(before_keys) == Counter(after_keys):
        return [{"kind": "reordered", "section": section}]
    if len(before) == len(after):
        # Same shape: an edit in place, so pair the items by position.
        return [
            _item_changed(section, i, b, a)
            for i, (b, a, bk, ak) in enumerate(
                zip(before, after, before_keys, after_keys, strict=True)
            )
            if bk != ak
        ]
    # Items came or went: report what is new and what is gone. A multiset
    # difference, so removing one of two identical items reports one.
    remaining = Counter(before_keys)
    added: list[Any] = []
    for item, key in zip(after, after_keys, strict=True):
        if remaining[key]:
            remaining[key] -= 1
        else:
            added.append(item)
    removed: list[Any] = []
    for item, key in zip(before, before_keys, strict=True):
        if remaining[key]:
            remaining[key] -= 1
            removed.append(item)
    if len(added) == 1 and len(removed) == 1:
        return [_item_changed(section, after.index(added[0]), removed[0], added[0])]
    return [
        *(_item_presence("item_removed", section, item) for item in removed),
        *(_item_presence("item_added", section, item) for item in added),
    ]


def compute_version_changes(
    before: dict[str, Any] | None, after: dict[str, Any]
) -> list[VersionChange] | None:
    """Return what ``after`` changed relative to ``before``.

    None when there is no earlier version to compare with; an empty list when
    the two mean the same automation (key order, the singular key spellings).
    """
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    changes: list[VersionChange] = []
    for section, keys in _SECTIONS:
        changes.extend(
            _section_changes(section, _section_items(before, keys), _section_items(after, keys))
        )
    for key in sorted((set(before) | set(after)) - _IGNORED_FIELDS, key=str):
        old, new = before.get(key), after.get(key)
        if _canonical(old) == _canonical(new):
            continue
        field: VersionChange = {"kind": "field_changed", "field": key}
        if _small(old) and _small(new):
            field["before"] = old
            field["after"] = new
        changes.append(field)
    return changes
