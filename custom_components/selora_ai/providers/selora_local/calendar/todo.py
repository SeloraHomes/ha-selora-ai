"""Selora AI Local — to-do / task-list question and command handlers."""

from __future__ import annotations

import json
import logging
import re
from types import SimpleNamespace
from typing import Any

from ..answers.state import _COMMAND_VERB_RE, _CONJUNCTION_RE, _safe_fname_for_prose

_LOGGER = logging.getLogger(__name__)


# To-do / shopping list command override.
_SELORA_LOCAL_TODO_ADD_RE = re.compile(
    r"\b(?:add|put|place|append|jot|stick)\b\s+(?P<item>.+?)\s+"
    r"\b(?:to|on|onto|in|into)\b\s+(?P<list>.+?)\s*$",
    re.IGNORECASE,
)

# Shopping / grocery signal words (the list itself is often named after a store, e.g.
_SELORA_LOCAL_TODO_SHOPPING_WORDS: tuple[str, ...] = (
    "shopping",
    "grocery",
    "groceries",
    "market",
    "supermarket",
)

# Known grocery-store names (normalised, alphanumeric-only) used to classify a list as a shopping list by its friendly_name.
_SELORA_LOCAL_TODO_GROCERY_STORES: tuple[str, ...] = (
    "traderjoe",
    "wholefood",
    "safeway",
    "kroger",
    "costco",
    "aldi",
    "walmart",
    "publix",
    "wegmans",
    "albertsons",
    "sprouts",
    "ralphs",
    "vons",
)

# Task / chore signal words.
_SELORA_LOCAL_TODO_TASK_WORDS: tuple[str, ...] = (
    "task",
    "tasks",
    "chore",
    "chores",
    "todo",
    "errand",
    "errands",
)

# Leading determiners stripped off the extracted item text.
_SELORA_LOCAL_TODO_ITEM_LEADERS: frozenset[str] = frozenset(
    {"a", "an", "the", "some", "my", "our", "that", "this"}
)


def _is_todo_question(prompt: str) -> bool:
    """True when ``prompt`` is a read-only question about the user's to-do / task list (e.g."""
    if not prompt:
        return False
    msg = prompt.strip()
    if len(msg.split()) > 20:
        return False
    low = msg.lower()
    if _TODO_MUTATE_RE.search(low):
        return False
    first = low.split()[0].strip("'\"") if low.split() else ""
    if "?" not in low and first not in _TODO_QUESTION_OPENERS:
        return False
    if _TODO_SIGNAL_RE.search(low) is not None:
        return True
    # "Who do I need to call?" carries no explicit list keyword but maps to the open task list — answer it from the enumerated open items.
    return _TODO_CALL_RE.search(low) is not None


# To-do / task-list question support
_TODO_SIGNAL_RE = re.compile(
    r"\b(?:task list|tasks? list|to-?do(?:\s*list)?|shopping list|"
    r"grocery|groceries|chores?|errands?|my tasks?)\b",
    re.IGNORECASE,
)

# Verbs that MUTATE the list ("add milk", "remove eggs").
_TODO_MUTATE_RE = re.compile(
    r"\b(?:add|remove|delete|put|cross|check|tick|clear|append)\b",
    re.IGNORECASE,
)

# "Who do I need to call?" / "who should I call?" — a phone-call task question that names no explicit list keyword but maps to the open todo items (the home's task list carries a "Call <name>" entry).
_TODO_CALL_RE = re.compile(
    r"\bwho\b.*\b(?:need(?:s)?\s+to\s+call|have\s+to\s+call|"
    r"gotta\s+call|should\s+i\s+call|do\s+i\s+(?:have\s+to|need\s+to)\s+call|"
    r"to\s+call)\b",
    re.IGNORECASE,
)

_TODO_QUESTION_OPENERS = frozenset(
    {
        "what",
        "what's",
        "whats",
        "who",
        "who's",
        "whos",
        "how",
        "which",
        "do",
        "does",
        "is",
        "are",
        "show",
        "list",
        "tell",
    }
)


class _CalendarTodoMixin:
    """Selora AI Local — to-do / task-list question and command handlers."""

    @staticmethod
    def _answer_envelope(r_text: str) -> str:
        """Serialise a deterministic answer envelope (the shared scaffold for calendar/to-do replies)."""
        return json.dumps({"intent": "answer", "response": r_text, "r": r_text})

    def _format_todo_entity_lines(self, eid: str, attrs: dict[str, Any]) -> str:
        """Render a todo entity with its injected open-item list."""
        from ....helpers import sanitize_untrusted_text

        fname = sanitize_untrusted_text(attrs.get("friendly_name") or eid).replace('"', "")
        items = attrs.get("todo_items")
        head = f'- entity_id={eid}; friendly_name="{fname}"'
        if not isinstance(items, list) or not items:
            return head + "; open_items=none (the list is empty)"
        out = [head + f"; open_items ({len(items)}):"]
        for item in items:
            summary = sanitize_untrusted_text(str(item or "")).strip()
            if not summary:
                continue
            out.append(f"    - {summary}")
        return "\n".join(out)

    def _open_todo_items_from_snapshot(self) -> list[str] | None:
        """Return the open item summaries from the entity snapshot the conversation layer injected (``attributes.todo_items``), or ``None`` when no ``todo.*`` entity is present in the snapshot."""
        entities = self._entities_for_lora.get() or []
        found = False
        summaries: list[str] = []
        for e in entities:
            if not isinstance(e, dict):
                continue
            eid = e.get("entity_id", "")
            if not (isinstance(eid, str) and eid.startswith("todo.")):
                continue
            attrs = e.get("attributes") or {}
            items = attrs.get("todo_items")
            if not isinstance(items, list):
                continue
            found = True
            for item in items:
                summary = str(item or "").strip()
                if summary:
                    summaries.append(_safe_fname_for_prose(summary))
        return summaries if found else None

    def _collect_open_todo_items(self) -> list[str] | None:
        """Return the open (``needs_action``) item summaries across every loaded ``todo.*`` list, or ``None`` when the todo component isn't loaded."""
        # Prefer the open items the conversation layer injected into the entity snapshot via todo.get_items (attributes.todo_items).
        snapshot_items = self._open_todo_items_from_snapshot()
        if snapshot_items is not None:
            return snapshot_items
        if not self._hass:
            return None
        component = self._hass.data.get("todo")
        entities = getattr(component, "entities", None)
        if entities is None:
            return None
        summaries: list[str] = []
        for entity in entities:
            items = getattr(entity, "todo_items", None)
            if not items:
                continue
            for item in items:
                status_val = getattr(item, "status", None)
                status = str(getattr(status_val, "value", status_val) or "needs_action").lower()
                if status not in ("needs_action", ""):
                    continue
                summary = str(getattr(item, "summary", "") or "").strip()
                if not summary:
                    continue
                summaries.append(_safe_fname_for_prose(summary))
        return summaries

    def _maybe_todo_question_envelope(self) -> str | None:
        """If the current turn is a question about the user's to-do / task list, answer it deterministically from the live todo entities."""
        raw_msg = self._user_message_raw.get() or ""
        if not _is_todo_question(raw_msg):
            return None
        # Gate on the per-turn classifier kind.
        if self._chat_kind.get() != "chat_answer" and (
            _COMMAND_VERB_RE.search(raw_msg) or _CONJUNCTION_RE.search(raw_msg)
        ):
            return None
        open_items = self._collect_open_todo_items()
        if open_items is None:
            return None
        count = len(open_items)
        if count == 0:
            r_text = "Your task list is empty — there are 0 items on it."
        elif count == 1:
            r_text = f"You have 1 item on your task list: {open_items[0]}."
        else:
            r_text = f"You have {count} items on your task list: {', '.join(open_items)}."
        return self._answer_envelope(r_text)

    @staticmethod
    def _clean_todo_item(raw_item: str) -> str:
        """Normalise the extracted to-do item text: drop a leading determiner ("the milk" -> "milk", "some apples" -> "apples") and strip surrounding punctuation/whitespace."""
        words = [w for w in raw_item.strip().split() if w]
        while words and words[0].lower() in _SELORA_LOCAL_TODO_ITEM_LEADERS:
            words.pop(0)
        return " ".join(words).strip(" .,!?;:\"'")

    def _resolve_todo_list(
        self, list_desc: str, msg: str, todo_states: list[Any]
    ) -> tuple[str | None, str]:
        """Resolve the target ``todo.<list>`` entity for an add-to-list request."""

        def _norm(s: str) -> str:
            return re.sub(r"[^a-z0-9]", "", s.lower())

        norm_msg = _norm(msg)
        # (eid, friendly_name, normalised-name, normalised-slug)
        entries: list[tuple[str, str, str, str]] = []
        for state in todo_states:
            eid = state.entity_id
            fname = str((state.attributes or {}).get("friendly_name") or "").strip() or eid
            slug = eid.split(".", 1)[-1]
            entries.append((eid, fname, _norm(fname), _norm(slug)))

        # 1.
        best: tuple[int, str, str] | None = None
        for eid, fname, norm_name, norm_slug in entries:
            for needle in (norm_name, norm_slug):
                if (
                    len(needle) >= 3
                    and needle in norm_msg
                    and (best is None or len(needle) > best[0])
                ):
                    best = (len(needle), eid, fname)
        if best is not None:
            return best[1], best[2]

        signal = f"{_norm(list_desc)} {norm_msg}"

        def _is_task(haystack: str) -> bool:
            return any(_norm(w) in haystack for w in _SELORA_LOCAL_TODO_TASK_WORDS)

        def _is_shopping(haystack: str) -> bool:
            if any(_norm(w) in haystack for w in _SELORA_LOCAL_TODO_SHOPPING_WORDS):
                return True
            return any(store in haystack for store in _SELORA_LOCAL_TODO_GROCERY_STORES)

        msg_task = _is_task(signal)
        msg_shopping = _is_shopping(signal)

        task_lists = [(eid, fname) for eid, fname, nn, ns in entries if _is_task(f"{nn} {ns}")]
        shopping_lists = [
            (eid, fname) for eid, fname, nn, ns in entries if _is_shopping(f"{nn} {ns}")
        ]
        non_task_lists = [
            (eid, fname) for eid, fname, nn, ns in entries if not _is_task(f"{nn} {ns}")
        ]

        # 2.
        if msg_task and len(task_lists) == 1:
            return task_lists[0]
        # 3.
        if msg_shopping:
            if len(shopping_lists) == 1:
                return shopping_lists[0]
            if len(non_task_lists) == 1:
                return non_task_lists[0]
        return None, ""

    def _todo_states_from_snapshot(self) -> list[Any]:
        """Return the ``todo.*`` list entities from the conversation-injected entity snapshot (``_entities_for_lora``) as lightweight state shims."""

        shims: list[Any] = []
        for e in self._current_entities() or []:
            if not isinstance(e, dict):
                continue
            eid = e.get("entity_id", "")
            if not (isinstance(eid, str) and eid.startswith("todo.")):
                continue
            attrs = e.get("attributes") or {}
            fname = str(attrs.get("friendly_name") or "").strip()
            shims.append(
                SimpleNamespace(
                    entity_id=eid,
                    attributes={"friendly_name": fname} if fname else {},
                )
            )
        return shims

    def _todo_states_from_component(self) -> list[Any]:
        """Return the loaded ``todo.*`` list entities as lightweight state shims, read straight from the todo ``EntityComponent`` (``hass.data["todo"]``)."""
        if not self._hass:
            return []
        component = self._hass.data.get("todo")
        entities = getattr(component, "entities", None)
        if not entities:
            return []

        shims: list[Any] = []
        for entity in entities:
            eid = str(getattr(entity, "entity_id", "") or "")
            if not eid:
                continue
            fname = str(getattr(entity, "name", "") or "").strip()
            shims.append(
                SimpleNamespace(
                    entity_id=eid,
                    attributes={"friendly_name": fname} if fname else {},
                )
            )
        return shims

    def _maybe_todo_command_envelope(self) -> str | None:
        """Deterministically build a ``todo.add_item`` command envelope when the current turn asks to add an item to a to-do / shopping list, for ANY chat kind except ``chat_automation``."""
        # Read the chat kind through the ContextVar→instance fallback rather than the bare ``_chat_kind.get()``: this pre-conversion override runs in the same post-stream pass where ``set_chat_context`` frequently ran in a different context, so the bare ContextVar reads back empty and the gate would suppress the override entirely — leaving the LoRA's mis-routed ``media_player.media_add`` to reach the policy and refuse.
        if self._current_chat_kind() == "chat_automation":
            return None
        return self._resolve_todo_add_envelope()

    def _todo_states_from_registry(self) -> list[Any]:
        """Return the home's ``todo.*`` list entities from the ENTITY REGISTRY as lightweight state shims."""
        if not self._hass:
            return []
        try:
            from homeassistant.helpers import entity_registry as er
        except Exception:  # noqa: BLE001 — registry is best-effort here
            return []
        ent_reg = er.async_get(self._hass)

        shims: list[Any] = []
        for entry in ent_reg.entities.values():
            eid = str(getattr(entry, "entity_id", "") or "")
            if not eid.startswith("todo."):
                continue
            if getattr(entry, "disabled_by", None) is not None:
                continue
            fname = str(
                getattr(entry, "name", None) or getattr(entry, "original_name", None) or ""
            ).strip()
            shims.append(
                SimpleNamespace(
                    entity_id=eid,
                    attributes={"friendly_name": fname} if fname else {},
                )
            )
        return shims

    def _all_todo_states(self) -> list[Any]:
        """Return the home's ``todo.*`` list entities, UNIONED across every available source and de-duplicated by ``entity_id``."""
        merged: list[Any] = []
        seen: set[str] = set()
        for source in (
            self._filtered_domain_states("todo"),
            self._todo_states_from_snapshot(),
            self._todo_states_from_component(),
            self._todo_states_from_registry(),
        ):
            for state in source:
                eid = getattr(state, "entity_id", None)
                if not (isinstance(eid, str) and eid):
                    continue
                if eid in seen:
                    continue
                seen.add(eid)
                merged.append(state)
        return merged

    def _resolve_todo_add_envelope(self) -> str | None:
        """Gate-independent core of the to-do command override."""
        raw = self._current_user_message()
        msg = raw.strip()
        if not msg:
            return None
        # Resolve against the UNION of every todo source, not the first non-empty one.
        todo_states = self._all_todo_states()
        if not todo_states:
            return None
        m = _SELORA_LOCAL_TODO_ADD_RE.search(msg)
        if m is None:
            return None
        item = self._clean_todo_item(m.group("item") or "")
        if not item:
            return None
        list_desc = (m.group("list") or "").strip()
        target_eid, target_fname = self._resolve_todo_list(list_desc, msg, todo_states)
        if target_eid is None:
            return None
        call: dict[str, Any] = {
            "service": "todo.add_item",
            "target": {"entity_id": target_eid},
            "data": {"item": item},
        }
        r_text = f"Added {item} to {target_fname}."
        return json.dumps({"intent": "command", "response": r_text, "calls": [call]})
