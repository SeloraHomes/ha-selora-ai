// Fuzzy filter + snippet extraction for the conversation sidebar search.
//
// Sessions carry a `search_text` blob (concatenated message contents, capped
// server-side). We fuzzy-match the query against both the title and the body,
// rank title hits above body-only hits, and for body hits extract a short
// snippet around the match so the sidebar can show where the conversation
// matched under its title.

// Characters of context to show on each side of a body match.
const SNIPPET_RADIUS = 40;

// Score a lowercased haystack against a lowercased query. Mirrors the
// autocomplete heuristic (chat-autocomplete.js): exact word > whole prefix >
// word prefix > substring > subsequence. Returns 0 when there is no match.
//
// `allowSubsequence` gates the weakest tier. It is safe on short haystacks
// (titles) but disastrous on the multi-thousand-char body blob: over that
// length nearly any short query's chars appear somewhere in order, so a
// subsequence match there is almost always a false positive. Callers pass
// false for the body and keep contiguous-substring-or-better there.
function scoreText(hayLower, queryLower, allowSubsequence = true) {
  if (!hayLower || !queryLower) return 0;
  const words = hayLower.split(/\s+/);
  for (const w of words) {
    if (w === queryLower) return 1500;
  }
  if (hayLower.startsWith(queryLower)) return 1000;
  for (const w of words) {
    if (w.startsWith(queryLower)) return 500;
  }
  if (hayLower.includes(queryLower)) return 100;
  if (!allowSubsequence) return 0;
  // Subsequence: every query char appears in order.
  let qi = 0;
  for (let i = 0; i < hayLower.length && qi < queryLower.length; i++) {
    if (hayLower[i] === queryLower[qi]) qi += 1;
  }
  return qi === queryLower.length ? 10 : 0;
}

// Build a {before, match, after} snippet around the first substring match of
// the query in `text`. Falls back to the first matched subsequence character
// (with an empty `match`) when there is no contiguous substring. Returns null
// when the query is not found at all.
export function extractSnippet(text, queryLower) {
  if (!text) return null;
  const lower = text.toLowerCase();
  let idx = lower.indexOf(queryLower);
  let matchLen = queryLower.length;
  if (idx < 0) {
    // No contiguous run — anchor on the first matched subsequence char.
    matchLen = 0;
    let qi = 0;
    for (let i = 0; i < lower.length && qi < queryLower.length; i++) {
      if (lower[i] === queryLower[qi]) {
        if (qi === 0) idx = i;
        qi += 1;
      }
    }
    if (qi !== queryLower.length) return null;
  }

  let start = Math.max(0, idx - SNIPPET_RADIUS);
  let end = Math.min(text.length, idx + matchLen + SNIPPET_RADIUS);
  // Trim to word boundaries so we don't slice mid-word on either side.
  if (start > 0) {
    const space = text.indexOf(" ", start);
    if (space >= 0 && space < idx) start = space + 1;
  }
  if (end < text.length) {
    const space = text.lastIndexOf(" ", end);
    if (space > idx + matchLen) end = space;
  }

  const before = (start > 0 ? "…" : "") + text.slice(start, idx);
  const match = text.slice(idx, idx + matchLen);
  const after =
    text.slice(idx + matchLen, end) + (end < text.length ? "…" : "");
  return { before, match, after };
}

// Filter and rank sessions against a query. Returns an array of
// `{ session, snippet }` in display order. When the query is blank the input
// order is preserved and every snippet is null (normal, unfiltered list).
export function filterSessions(sessions, query) {
  const list = Array.isArray(sessions) ? sessions : [];
  const q = (query || "").trim().toLowerCase();
  if (!q) return list.map((session) => ({ session, snippet: null }));

  const scored = [];
  for (const session of list) {
    const titleScore = scoreText((session.title || "").toLowerCase(), q);
    // Body: contiguous-substring-or-better only — no subsequence over the blob.
    const bodyScore = scoreText(
      (session.search_text || "").toLowerCase(),
      q,
      false,
    );
    if (titleScore <= 0 && bodyScore <= 0) continue;
    // Title hits always outrank body-only hits. A snippet is only shown for a
    // body-ONLY hit: if the title also matched it is already on screen, so a
    // body snippet would be redundant (and misleading when they differ).
    const rank = titleScore > 0 ? 100000 + titleScore : bodyScore;
    const snippet =
      titleScore <= 0 && bodyScore > 0
        ? extractSnippet(session.search_text, q)
        : null;
    scored.push({ session, snippet, rank, updated: session.updated_at || "" });
  }
  scored.sort((a, b) => b.rank - a.rank || b.updated.localeCompare(a.updated));
  return scored.map(({ session, snippet }) => ({ session, snippet }));
}
