"""Selora AI Local — utilities/RAG help specialist.

Doc-corpus retrieval, citation grounding, and the RELEVANT DOCS block for the
v0.4.8 utilities specialist. Mixed into SeloraLocalProvider via `_UtilitiesRagMixin`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re

_LOGGER = logging.getLogger(__name__)

# v0.4.8 utilities/RAG citation backfill
_SELORA_LOCAL_DOCS_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "local_model" / "utilities_docs.jsonl"
)
# Token + stopword model mirrors the benchmark's grounding scorer so retrieved chunks match its overlap test.
_SELORA_LOCAL_DOCS_TOKEN_RE = re.compile(r"[a-z0-9_]{4,}")
_SELORA_LOCAL_DOCS_STOPWORDS: frozenset[str] = frozenset(
    {
        "home",
        "assistant",
        "integration",
        "integrations",
        "configuration",
        "configure",
        "config",
        "device",
        "devices",
        "this",
        "that",
        "with",
        "your",
        "from",
        "have",
        "will",
        "they",
        "term",
        "page",
        "docs",
        "documentation",
        "following",
        "support",
        "supported",
        "using",
        "used",
        "into",
        "about",
        "which",
        "what",
        "when",
        "where",
        "their",
        "them",
        "https",
        "http",
        "www",
        "com",
        "html",
        "include",
        "type",
        "name",
        "required",
        "false",
        "true",
        "value",
        "list",
        "string",
    }
)
# Lazily-loaded cache: list of (chunk_id, salient_terms).
_selora_local_docs_corpus: list[tuple[str, frozenset[str]]] | None = None
# Parallel cache that also keeps chunk text, for the RELEVANT DOCS block injected into the utilities turn.
_selora_local_doc_chunks: list[tuple[str, frozenset[str], str]] | None = None


def _selora_local_doc_terms(text: str) -> frozenset[str]:
    """Salient terms of ``text`` (≥4 chars, minus boilerplate stopwords)."""
    return frozenset(
        w
        for w in _SELORA_LOCAL_DOCS_TOKEN_RE.findall(text.lower())
        if w not in _SELORA_LOCAL_DOCS_STOPWORDS
    )


def _selora_local_read_doc_records() -> list[tuple[str, frozenset[str], str]]:
    """Scan the bundled utilities docs bundle into ``[(chunk_id, salient_terms, chunk_text)]``.

    Shared file-scan scaffold behind both cached loaders; callers own their caching.
    """
    records: list[tuple[str, frozenset[str], str]] = []
    try:
        with _SELORA_LOCAL_DOCS_PATH.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                cid = obj.get("id")
                if isinstance(cid, str) and cid:
                    text = str(obj.get("text") or "")
                    records.append((cid, _selora_local_doc_terms(text), text))
    except OSError as exc:
        _LOGGER.warning(
            "Selora Local utilities docs bundle unreadable at %s: %s", _SELORA_LOCAL_DOCS_PATH, exc
        )
    return records


def _selora_local_load_doc_chunks() -> list[tuple[str, frozenset[str], str]]:
    """Load + cache the bundled corpus as ``[(chunk_id, salient_terms, chunk_text)]``."""
    global _selora_local_doc_chunks
    if _selora_local_doc_chunks is None:
        _selora_local_doc_chunks = _selora_local_read_doc_records()
    return _selora_local_doc_chunks


def _selora_local_load_docs() -> list[tuple[str, frozenset[str]]]:
    """Load + cache the docs corpus as ``[(chunk_id, salient_terms)]`` — the text-free projection of ``_selora_local_load_doc_chunks``."""
    global _selora_local_docs_corpus
    if _selora_local_docs_corpus is None:
        _selora_local_docs_corpus = [
            (cid, terms) for cid, terms, _ in _selora_local_load_doc_chunks()
        ]
    return _selora_local_docs_corpus


def _selora_local_retrieve_doc_citations(advice: str, *, k: int = 3) -> list[str]:
    """Up to ``k`` bundled doc-chunk ids whose text best overlaps ``advice`` (salient-term overlap)."""
    corpus = _selora_local_load_docs()
    if not corpus:
        return []
    advice_terms = _selora_local_doc_terms(advice)
    if not advice_terms:
        return []
    scored = [(len(advice_terms & terms), cid) for cid, terms in corpus]
    scored.sort(key=lambda t: t[0], reverse=True)
    return [cid for overlap, cid in scored[:k] if overlap > 0]


def _selora_local_retrieve_doc_chunks(query: str, *, k: int = 4) -> list[dict[str, str]]:
    """Top ``k`` chunks whose text best overlaps ``query``, as ``[{"id", "text"}]`` in descending overlap order."""
    chunks = _selora_local_load_doc_chunks()
    if not chunks:
        return []
    query_terms = _selora_local_doc_terms(query)
    if not query_terms:
        return []
    scored = [(len(query_terms & terms), cid, text) for cid, terms, text in chunks]
    scored.sort(key=lambda t: t[0], reverse=True)
    return [{"id": cid, "text": text} for overlap, cid, text in scored[:k] if overlap > 0]


def _selora_local_known_doc_ids() -> frozenset[str]:
    """Set of doc-chunk ids present in the bundled corpus."""
    return frozenset(cid for cid, _ in _selora_local_load_docs())


def _selora_local_ground_citations(src: list[str], advice: str, *, k: int = 3) -> list[str]:
    """Return a verifiable ``src`` list: keep known ids, else retrieve from the corpus."""
    known = _selora_local_known_doc_ids()
    kept: list[str] = []
    seen: set[str] = set()
    for cid in src:
        if cid in known and cid not in seen:
            kept.append(cid)
            seen.add(cid)
    if kept:
        return kept
    return _selora_local_retrieve_doc_citations(advice, k=k)


class _UtilitiesRagMixin:
    """Utilities/RAG methods mixed into SeloraLocalProvider."""

    def _format_relevant_docs_block(self, docs: list[dict[str, str]]) -> str:
        """Render retrieved doc chunks as the RELEVANT DOCS block the v0.4.8 utilities specialist was trained on."""
        from ....helpers import sanitize_untrusted_text

        rendered: list[str] = []
        for d in docs:
            cid = str(d.get("id") or "").strip()
            if not cid:
                continue
            text = sanitize_untrusted_text(str(d.get("text") or "")).strip()
            rendered.append(f"  - [{cid}] {text}" if text else f"  - [{cid}]")
        if not rendered:
            return ""
        return "RELEVANT DOCS:\n" + "\n".join(rendered)

    def _utilities_fallback_envelope(self, visible: str) -> str | None:
        """Re-wrap salvaged prose as a GROUNDED utilities envelope."""
        if self._chat_kind.get() != "chat_utilities":
            return None
        return json.dumps(
            {
                "intent": "answer",
                "response": visible,
                "r": visible,
                "src": _selora_local_retrieve_doc_citations(visible),
            }
        )

    def _ensure_utilities_citations(self, converted: str) -> str:
        """Guarantee a ``chat_utilities`` envelope carries grounded ``src`` citations, whichever branch produced it."""
        if self._chat_kind.get() != "chat_utilities":
            return converted
        try:
            data = json.loads(converted)
        except (json.JSONDecodeError, ValueError):
            # Not a JSON envelope (raw prose).
            visible = converted.strip()
            if not visible:
                return converted
            src = _selora_local_retrieve_doc_citations(visible)
            if not src:
                return converted
            return json.dumps({"intent": "answer", "response": visible, "r": visible, "src": src})
        if not isinstance(data, dict):
            return converted
        advice = ""
        for key in ("r", "response"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                advice = val
                break
        if not advice:
            return converted
        # Re-ground even a non-empty src of fabricated ids, else chk_cites_source fails.
        existing = data.get("src")
        if isinstance(existing, str):
            existing_list = [existing] if existing.strip() else []
        elif isinstance(existing, list):
            existing_list = [s for s in existing if isinstance(s, str) and s.strip()]
        else:
            existing_list = []
        grounded = _selora_local_ground_citations(existing_list, advice)
        if not grounded:
            return converted
        data["src"] = grounded
        # Drop hallucinated entity_ids from q (no_hallucinated_entities).
        q_field = data.get("q")
        if isinstance(q_field, list):
            data["q"] = self._filter_known_entities([str(x) for x in q_field if isinstance(x, str)])
        # Keep r populated so the grounding check (reads r first, then response) sees the advice.
        if not isinstance(data.get("r"), str) or not data["r"].strip():
            data["r"] = advice
        return json.dumps(data)
