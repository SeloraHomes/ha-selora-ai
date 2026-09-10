"""Selora AI Local — calendar / schedule question and helper handlers."""

from __future__ import annotations

import logging
import re
from typing import Any

_LOGGER = logging.getLogger(__name__)


# Calendar / schedule question detection (deterministic answer)
_CALENDAR_QUESTION_RE = re.compile(
    r"\b(?:calendar|schedule|agenda|appointments?|events?|classes?|class|"
    r"meeting\s+for\s+dinner|who\s+am\s+i\s+meeting|"
    r"leaving\s+the\s+house|away\s+from\s+home|"
    r"(?:coming\s+to\s+)?visit(?:ing|s|ors?)?|"
    r"need\s+to\s+cook|nights?\b.*\bcook|cook\b.*\bnights?)\b",
    re.IGNORECASE,
)

# "Leaving the house" / "away from home" sub-class — answered as a yes/no on whether any event today is at a non-home location.
_CALENDAR_LEAVING_RE = re.compile(
    r"\b(?:leaving\s+the\s+house|away\s+from\s+home|"
    r"out\s+of\s+the\s+house|going\s+out)\b",
    re.IGNORECASE,
)

# "Is anyone visiting?" sub-class — searched across the whole week.
_CALENDAR_VISIT_RE = re.compile(r"\b(?:coming\s+to\s+)?visit(?:ing|s|ors?)?\b", re.IGNORECASE)

# "How many … this week" counting sub-class.
_CALENDAR_COUNT_RE = re.compile(r"\bhow\s+many\b", re.IGNORECASE)

# Interrogative-shape gate.
_CALENDAR_INTERROGATIVE_RE = re.compile(
    r"\?\s*$"
    r"|^\s*(?:what|what's|whats|who|who's|whos|when|where|which|how|is|are|"
    r"am|do|does|did|can|could|will|would|should|according\s+to|"
    r"tell\s+me|any(?:one|body)?)\b",
    re.IGNORECASE,
)

# Whole-week (vs today-only) window signals.
_CALENDAR_WEEK_RE = re.compile(r"\b(?:week|cook|visit)", re.IGNORECASE)

# YYYY-MM-DD extractor for the injected ``today`` reference string and the ISO start/end of each event.
_CALENDAR_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

# Stopwords dropped when deriving the salient noun for a "how many … cook?" style count (so it matches the event summary, e.g.
_CALENDAR_COUNT_STOPWORDS = frozenset(
    {
        "how",
        "many",
        "this",
        "that",
        "the",
        "a",
        "an",
        "do",
        "does",
        "did",
        "i",
        "we",
        "you",
        "my",
        "our",
        "need",
        "needs",
        "to",
        "on",
        "in",
        "of",
        "for",
        "have",
        "has",
        "are",
        "is",
        "am",
        "any",
        "personal",
        "calendar",
        "schedule",
        "week",
        "today",
        "night",
        "nights",
        "day",
        "days",
        "event",
        "events",
        "according",
        "from",
    }
)

# Visit SYNONYMS for the "is anyone visiting?" sub-class: an event whose summary carries one of these is a guest/visit even when the literal word "visit" is absent ("Mom staying over", "Guests this weekend").
_CALENDAR_VISIT_SYNONYMS: frozenset[str] = frozenset(
    {
        "visit",
        "visitor",
        "guest",
        "houseguest",
        "staying",
        "stay over",
        "sleepover",
        "coming over",
        "in town",
        "stopping by",
    }
)

# Routine personal / chore event summaries that are NEVER a guest visit.
_CALENDAR_VISIT_ROUTINE_WORDS: frozenset[str] = frozenset(
    {
        "cook",
        "dinner",
        "lunch",
        "breakfast",
        "meal",
        "work",
        "office",
        "shift",
        "gym",
        "workout",
        "exercise",
        "yoga",
        "class",
        "lesson",
        "school",
        "study",
        "homework",
        "meeting",
        "appointment",
        "appt",
        "dentist",
        "doctor",
        "practice",
        "rehearsal",
        "game",
        "match",
        "call",
        "conference",
        "interview",
        "clean",
        "laundry",
        "shopping",
        "groceries",
        "errand",
        "pickup",
        "soccer",
        "piano",
        "trip",
        "flight",
        "vacation",
    }
)


class _CalendarMixin:
    """Selora AI Local — calendar / schedule question and helper handlers."""

    def _format_calendar_entity_lines(self, eid: str, attrs: dict[str, Any]) -> str:
        """Render a calendar entity with its injected upcoming-event list."""
        from ....helpers import sanitize_untrusted_text

        fname = sanitize_untrusted_text(attrs.get("friendly_name") or eid).replace('"', "")
        today = sanitize_untrusted_text(str(attrs.get("today") or "")).strip()
        head = f'- entity_id={eid}; friendly_name="{fname}"'
        if today:
            head += f"; today={today}"
        events = attrs.get("events")
        if not isinstance(events, list) or not events:
            return head + "; events=none scheduled in the next week"
        out = [head + "; events:"]
        for ev in events:
            if not isinstance(ev, dict):
                continue
            summary = sanitize_untrusted_text(str(ev.get("summary") or "")).strip() or "(no title)"
            start = sanitize_untrusted_text(str(ev.get("start") or "")).strip()
            end = sanitize_untrusted_text(str(ev.get("end") or "")).strip()
            location = sanitize_untrusted_text(str(ev.get("location") or "")).strip()
            piece = f"    - {summary}"
            if start:
                piece += f" (start={start}"
                if end:
                    piece += f", end={end}"
                if location:
                    piece += f", location={location}"
                piece += ")"
            elif location:
                piece += f" (location={location})"
            out.append(piece)
        return "\n".join(out)

    def _resolve_calendar_placeholder(self, entity_id: str) -> str | None:
        """Render a calendar entity's injected upcoming events as a compact inline string for ``{entity_id}`` placeholder substitution."""
        from ....helpers import sanitize_untrusted_text

        # current_entities() (not the bare ContextVar) so the instance mirror is used under the convert-pass propagation race — same rationale as _collect_calendar_events below.
        for ent in self._current_entities():
            if not isinstance(ent, dict) or ent.get("entity_id") != entity_id:
                continue
            attrs = ent.get("attributes") or {}
            fname = sanitize_untrusted_text(str(attrs.get("friendly_name") or entity_id)).strip()
            events = attrs.get("events")
            if not isinstance(events, list) or not events:
                return f"{fname}: no upcoming events"
            parts: list[str] = []
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                summary = (
                    sanitize_untrusted_text(str(ev.get("summary") or "")).strip() or "(no title)"
                )
                start = sanitize_untrusted_text(str(ev.get("start") or "")).strip()
                location = sanitize_untrusted_text(str(ev.get("location") or "")).strip()
                piece = summary
                extras: list[str] = []
                if start:
                    extras.append(f"start {start}")
                if location:
                    extras.append(f"at {location}")
                if extras:
                    piece += f" ({', '.join(extras)})"
                parts.append(piece)
            if not parts:
                return f"{fname}: no upcoming events"
            return f"{fname}: " + "; ".join(parts)
        return None

    def _collect_calendar_events(
        self,
    ) -> tuple[list[dict[str, str]], str | None]:
        """Gather every injected calendar event from this turn's snapshot."""
        from ....helpers import sanitize_untrusted_text

        events: list[dict[str, str]] = []
        today: str | None = None
        # Read via _current_entities() (ContextVar → instance-attribute fallback), NOT the bare ContextVar: the non-streaming convert pass runs in a context that never saw set_chat_context's ``.set()`` (the documented propagation race).
        for ent in self._current_entities():
            if not isinstance(ent, dict):
                continue
            eid = str(ent.get("entity_id") or "")
            if not eid.startswith("calendar."):
                continue
            attrs = ent.get("attributes") or {}
            if today is None:
                m = _CALENDAR_DATE_RE.search(str(attrs.get("today") or ""))
                if m:
                    today = m.group(1)
            raw = attrs.get("events")
            if not isinstance(raw, list):
                continue
            for ev in raw:
                if not isinstance(ev, dict):
                    continue
                events.append(
                    {
                        "summary": sanitize_untrusted_text(str(ev.get("summary") or "")).strip(),
                        "start": str(ev.get("start") or "").strip(),
                        "end": str(ev.get("end") or "").strip(),
                        "location": sanitize_untrusted_text(str(ev.get("location") or "")).strip(),
                    }
                )
        return events, today

    @staticmethod
    def _format_calendar_event(ev: dict[str, str]) -> str:
        """Render one event as ``Summary (HH:MM, at Location)`` for an answer listing."""
        summary = ev.get("summary") or "(untitled event)"
        extras: list[str] = []
        start = ev.get("start") or ""
        tm = re.search(r"T(\d{2}:\d{2})", start)
        if tm:
            extras.append(tm.group(1))
        location = ev.get("location") or ""
        if location:
            extras.append(f"at {location}")
        if extras:
            return f"{summary} ({', '.join(extras)})"
        return summary

    @staticmethod
    def _calendar_date_of(value: str) -> str | None:
        """Extract the first ``YYYY-MM-DD`` date from an ISO start/end string, or ``None``."""
        m = _CALENDAR_DATE_RE.search(value)
        return m.group(1) if m else None

    @classmethod
    def _calendar_event_on_date(cls, ev: dict[str, str], today: str | None) -> bool:
        """True when ``ev`` spans ``today`` (``None`` today, or an undated event, counts as in-scope)."""
        if today is None:
            return True
        start_d = cls._calendar_date_of(ev.get("start") or "")
        end_d = cls._calendar_date_of(ev.get("end") or "") or start_d
        if start_d is None:
            return True
        if start_d > today:
            return False
        end_d = end_d or start_d
        # An all-day event carries DATE-ONLY start/end and HA serialises its end as the
        # EXCLUSIVE next day, so "2026-09-09 -> 2026-09-10" occupies the 9th alone. A timed
        # event's end is a real instant on the last day it covers, so that one is inclusive.
        raw_end = ev.get("end") or ""
        if "T" not in raw_end and end_d > start_d:
            return today < end_d
        return today <= end_d

    def _maybe_calendar_question_envelope(self) -> str | None:
        """Answer a calendar / schedule question deterministically from the injected event list."""
        prompt = (self._current_user_message() or "").strip()
        if not prompt or not _CALENDAR_QUESTION_RE.search(prompt):
            return None
        # The override normally only runs on chat_answer turns.
        if self._current_chat_kind() != "chat_answer" and not _CALENDAR_INTERROGATIVE_RE.search(
            prompt
        ):
            return None
        events, today = self._collect_calendar_events()
        if not events:
            return None

        week_mode = bool(_CALENDAR_WEEK_RE.search(prompt))
        scoped = (
            events if week_mode else [e for e in events if self._calendar_event_on_date(e, today)]
        )
        scope_word = "this week" if week_mode else "today"

        # "How many … cook?" — count events whose summary matches the salient noun from the question (falling back to every scoped event).
        if _CALENDAR_COUNT_RE.search(prompt):
            tokens = {
                t
                for t in re.findall(r"[a-z]+", prompt.lower())
                if t not in _CALENDAR_COUNT_STOPWORDS and len(t) > 2
            }
            matched = [e for e in scoped if any(t in e.get("summary", "").lower() for t in tokens)]
            pool = matched or scoped
            count = len(pool)
            listing = "; ".join(self._format_calendar_event(e) for e in pool)
            r_text = f"You have {count} events {scope_word} on your calendar" + (
                f": {listing}." if listing else "."
            )
            return self._answer_envelope(r_text)

        # "Leaving the house?" / "away from home?" — yes/no on whether any scoped event is at a non-home location.
        if _CALENDAR_LEAVING_RE.search(prompt):
            today_events = [e for e in events if self._calendar_event_on_date(e, today)]
            away = [
                e
                for e in today_events
                if e.get("location") and e["location"].strip().lower() not in ("home", "the house")
            ]
            if away:
                listing = "; ".join(self._format_calendar_event(e) for e in away)
                r_text = f"Yes — you have events away from home today: {listing}."
            else:
                r_text = (
                    "No, you do not have any events away from home today — "
                    "everything on your calendar is at home."
                )
            return self._answer_envelope(r_text)

        # "Is anyone visiting?" — search the whole week for a visit event.
        if _CALENDAR_VISIT_RE.search(prompt):
            visiting = [
                e
                for e in events
                if any(syn in e.get("summary", "").lower() for syn in _CALENDAR_VISIT_SYNONYMS)
            ]
            # No event literally names a visit/guest.
            if not visiting:
                visiting = [
                    e
                    for e in scoped
                    if not any(
                        word in e.get("summary", "").lower()
                        for word in _CALENDAR_VISIT_ROUTINE_WORDS
                    )
                ]
            if visiting:
                listing = "; ".join(self._format_calendar_event(e) for e in visiting)
                r_text = f"Yes — your calendar shows: {listing}."
            else:
                r_text = "No, your calendar does not show anyone visiting this week."
            return self._answer_envelope(r_text)

        # Generic listing ("what classes today?", "who am I meeting for dinner?").
        if not scoped:
            r_text = f"You have no events on your calendar {scope_word}."
        else:
            listing = "; ".join(self._format_calendar_event(e) for e in scoped)
            r_text = f"On your calendar {scope_word} you have: {listing}."
        return self._answer_envelope(r_text)
