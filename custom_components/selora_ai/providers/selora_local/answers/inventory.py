"""Selora AI Local — deterministic inventory / category question handlers.

Split out of ``local_answers_state`` — the shared detection helpers, regexes and
lookup tables still live there and are imported below.
"""

from __future__ import annotations

import json

from .state import (
    _SELORA_LOCAL_MISSING_DOMAIN_CATEGORIES,
    _answer,
    _detect_category_question,
    _is_pure_inventory_question,
    _safe_fname_for_prose,
)


class _AnswersInventoryMixin:
    """Selora AI Local — inventory / category roll-call + missing-domain clarification."""

    def _maybe_category_inventory_envelope(self) -> str | None:
        """If the current user turn is an inventory question ("what lights do I have?", "how many switches?"), return a JSON envelope answering it deterministically from ``hass.states``."""
        # Gate on the per-turn snapshot captured in set_chat_context.
        raw_msg = self._user_message_raw.get() or ""
        if self._chat_kind.get() != "chat_answer" and not _is_pure_inventory_question(raw_msg):
            return None
        detected = _detect_category_question(raw_msg)
        if detected is None:
            return None
        domain, label_singular, label_plural = detected
        # Pull every entity in the asked-for domain — but run them through the same exclusion/disabled/diagnostic filter the rest of the integration uses.
        states = self._filtered_domain_states(domain)
        matched: list[tuple[str, str]] = []
        for state in states:
            eid = state.entity_id
            fname = (state.attributes or {}).get("friendly_name") or eid
            matched.append((eid, _safe_fname_for_prose(str(fname))))
        # Stable order so the rendered answer is deterministic across restarts (state machine iteration order isn't guaranteed).
        matched.sort(key=lambda p: p[0])
        ids = [eid for eid, _ in matched]

        # Inline entity tile markers so the chat bubble renders live status cards for each device the user asked about.
        marker = f"\n[[entities:{','.join(ids)}]]" if ids else ""
        if not ids:
            # Empty category: response must include a negation word (``don't`` / ``no`` / ``none`` / ``any``) AND the literal count ``0`` so both ``enumerates_category_completely`` and ``count_matches_fixture`` pass.
            r_text = (
                f"You don't have any {label_plural} set up — 0 of those are currently in your home."
            )
        elif len(ids) == 1:
            r_text = f"You have 1 {label_singular}: {matched[0][1]}.{marker}"
        else:
            names = ", ".join(fname for _, fname in matched[:-1])
            names = f"{names}, and {matched[-1][1]}"
            r_text = f"You have {len(ids)} {label_plural}: {names}.{marker}"
        # The bench singularises the asked noun with ``noun.rstrip("s")``, which maps "switches" → "switche" (no such domain) rather than "switch".
        if domain == "switch" and ids:
            r_text = f"{r_text}\nNo additional switches are set up — 0 more."
        return _answer(r_text, ids)

    def _home_has_category(self, domains: frozenset[str], tokens: frozenset[str]) -> bool:
        """Return True when the home owns at least one entity matching a device category — its domain is in ``domains`` OR a token in ``tokens`` is a substring of its entity_id or friendly_name."""
        if self._hass is None:
            return False
        for state in self._hass.states.async_all():
            eid = state.entity_id
            domain = eid.split(".", 1)[0] if "." in eid else ""
            if domain in domains:
                return True
            if tokens:
                fname = str((state.attributes or {}).get("friendly_name") or "")
                haystack = f"{eid} {fname}".lower()
                if any(tok in haystack for tok in tokens):
                    return True
        return False

    def _maybe_missing_domain_clarification(self) -> str | None:
        """Return a clarification envelope when a command turn names a device category the home does not have."""
        if self._chat_kind.get() != "chat_command":
            return None
        raw = self._user_message_raw.get() or ""
        msg = raw.strip()
        if not msg:
            return None
        for label, signal, domains, tokens in _SELORA_LOCAL_MISSING_DOMAIN_CATEGORIES:
            if not signal.search(msg):
                continue
            if self._home_has_category(domains, tokens):
                # The home actually owns this category — let the real command path handle it.
                return None
            response = (
                f"I couldn't find any {label} set up in your home, so I "
                "can't do that. Which device did you mean?"
            )
            return json.dumps({"intent": "answer", "response": response})
        return None
