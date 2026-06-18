// ---------------------------------------------------------------------------
// Markdown & automation block helpers
// ---------------------------------------------------------------------------

/**
 * @param {string|null|undefined} text
 * @returns {{ text: string, hasAutomationBlock: boolean, isPartialBlock: boolean, partialBlockType: string|null }}
 */
export function stripAutomationBlock(text) {
  if (!text)
    return {
      text: "",
      hasAutomationBlock: false,
      isPartialBlock: false,
      partialBlockType: null,
    };

  // Block types the backend extracts and re-attaches structurally — never
  // show their raw JSON to the user.
  const blockTypes =
    "automation|scene|quick_actions|command|delayed_command|cancel";

  // Complete block: ```<type> ... ```
  const completeRe = new RegExp("```(?:" + blockTypes + ")[\\s\\S]*?```", "g");
  const hasComplete = completeRe.test(text);
  let cleaned = text.replace(completeRe, "").trim();

  // Partial block (still streaming): ```<type> ... (no closing ```)
  const partialRe = new RegExp("```(" + blockTypes + ")[\\s\\S]*$");
  const partialMatch = !hasComplete ? cleaned.match(partialRe) : null;
  let hasPartial = !!partialMatch;
  if (hasPartial) {
    cleaned = cleaned.replace(partialRe, "").trim();
  }

  // Bare-fence window: while streaming, the opening ``` arrives a few
  // tokens before the type word (`command`, `automation`, …). In that
  // gap neither the complete nor the typed-partial regex matches, so a
  // lone ``` flashes in the bubble until the next chunk lands. Strip any
  // dangling unclosed fence (odd ``` count → the last one has no closer)
  // from the opener to end-of-text. Anchors to the FINAL fence so a
  // complete earlier code block is left intact.
  let barePartialType = null;
  if (!hasComplete && !hasPartial) {
    const fenceCount = (cleaned.match(/```/g) || []).length;
    if (fenceCount % 2 === 1) {
      cleaned = cleaned.replace(/```(?:(?!```)[\s\S])*$/, "").trim();
      hasPartial = true;
    } else {
      // Bare typed block (no opening ``` fence): some models drop the
      // opener and stream `automation\n{ …` directly. While the body
      // streams there is no fence to hide it, so the raw JSON fills the
      // bubble until the response finalizes. Detect the type word alone
      // on its own line followed by a JSON `{` body and strip from there
      // to end-of-text, surfacing the building spinner instead.
      //
      // SCOPE: only `automation` / `scene` with a `{` body — exactly the
      // shapes `parse_streamed_response` salvages on the final parse (see
      // llm_client/parsers.py). We must NOT hide bare shapes the backend
      // can't extract (other block types, or YAML `key:` bodies): with no
      // structural fallback the final message would silently lose the
      // block. Those keep rendering as raw text instead.
      //
      // And NOT matches inside a ``` code fence: a normal fenced example
      // (```\nautomation\n{...}\n```) is shown as code, not a proposal —
      // stripping it would truncate the answer to a dangling fence. Walk
      // the opener candidates and take the first one outside any fence
      // (an odd number of ``` before it means it's enclosed).
      const openerRe = /(?:^|\n)[ \t]*(automation|scene)[ \t]*\n[ \t]*\{/g;
      let bm;
      while ((bm = openerRe.exec(cleaned)) !== null) {
        let idx = bm.index;
        if (cleaned[idx] === "\n") idx += 1;
        const fencesBefore = (cleaned.slice(0, idx).match(/```/g) || []).length;
        if (fencesBefore % 2 === 0) {
          cleaned = cleaned.slice(0, idx).trim();
          hasPartial = true;
          barePartialType = bm[1];
          break;
        }
      }
    }
  }

  // Spinners only make sense for the long-form blocks; quick_actions /
  // delayed_command / cancel are short and finalize quickly, so we don't
  // surface a building-state UI for them.
  const spinnerCandidate = partialMatch?.[1] || barePartialType;
  const spinnerType = ["automation", "scene"].includes(spinnerCandidate)
    ? spinnerCandidate
    : null;

  return {
    text: cleaned,
    hasAutomationBlock: hasComplete,
    isPartialBlock: hasPartial,
    partialBlockType: spinnerType,
  };
}

// Salvage path for prompts the LLM doesn't follow: when the model emits a
// plain-text bullet listing of entity_ids ("- light.kitchen\n  — on …\n
// - light.office\n  — on …"), gather the run into a single
// `[[entities:…]]` marker so the chat still renders proper HA tile cards.
//
// Conservative on purpose: an "entity_id-only line" must contain only
// whitespace, an optional bullet marker, the id, and end-of-line — so
// inline mentions like "the kitchen lights are on (light.kitchen)" don't
// trigger. Only runs of ≥1 such lines collapse; the surrounding "— state"
// hints and blank separator lines are absorbed into the run so the prose
// reads cleanly afterwards.
function _coalesceEntityListings(text) {
  const ID_LINE = /^[\s>]*[-•*]?\s*([a-z_]+\.[a-z0-9_\-]+)\s*$/;
  // Bulleted entity marker — the LLM followed the prompt and emitted
  // `[[entity:…]]` / `[[entities:…]]` but as a bullet item. We strip
  // the bullet wrapper so the tile renders block-level (no orange
  // bullet bar next to it) and so the annotation cleanup below can
  // see what the marker is "anchored" to. The optional trailing
  // `[—–][^\n]*` group handles the very common shape where the LLM
  // glues the state hint onto the same line:
  //   `- [[entity:light.x|Kitchen]] — brightness: 255`
  // The hint is dropped (the tile already shows live state).
  const MARKER_BULLET =
    /^[\s>]*[-•*]\s*(\[\[entit(?:y|ies):[^\]\n]+\]\])\s*(?:[—–][^\n]*)?$/;
  // Non-bulleted marker followed by a state hint on the same line.
  // We keep the marker (the inline-marker substitution below turns
  // it into a tile grid) but drop the dash hint so the bubble
  // doesn't repeat what the tile already shows.
  const MARKER_TAIL_STATE =
    /^(\s*\[\[entit(?:y|ies):[^\]\n]+\]\])\s*[—–][^\n]*$/;
  // "State annotation" lines the LLM tends to emit alongside an
  // entity_id ("— on (brightness: 180)", "— off", "— locked", etc.).
  // The tile card shows the same info live, so we consume these so
  // the bubble doesn't repeat the state next to a tile. We accept
  // em-dash (—) and en-dash (–) but NOT hyphen-minus — including the
  // hyphen would let the regex backtrack and match the leading bullet
  // marker of the next entity line, eating subsequent ids.
  const STATE_LINE = /^[\s>]*[-•*]?\s*[—–]\s*\S/;
  const BLANK = /^\s*$/;
  const lines = text.split("\n");
  const out = [];
  let i = 0;
  const skipBlanks = (j) => {
    while (j < lines.length && BLANK.test(lines[j])) j++;
    return j;
  };
  const skipStateLines = (j) => {
    // Consume any number of consecutive state-annotation lines (with
    // optional blanks between them). Some renderings split the state
    // across multiple bullets ("— on", "— brightness 180"); we drop
    // them all rather than leave half visible.
    while (j < lines.length) {
      const k = skipBlanks(j);
      if (k >= lines.length || !STATE_LINE.test(lines[k])) return j;
      j = k + 1;
    }
    return j;
  };
  // Pull entity_ids out of either marker shape. Returns an array (1+
  // for single-entity / multi-entity markers, [] for malformed).
  const idsFromMarker = (marker) => {
    const single = marker.match(/^\[\[entity:([a-z_]+\.[a-z0-9_\-]+)/);
    if (single) return [single[1]];
    const multi = marker.match(/^\[\[entities:([^\]\n]+)\]\]/);
    if (multi) {
      return multi[1]
        .split(",")
        .map((s) => s.trim())
        .filter((s) => /^[a-z_]+\.[a-z0-9_\-]+$/.test(s));
    }
    return [];
  };
  // Bare-marker line (no leading bullet) — used by the non-bulleted
  // coalesce loop below.
  const BARE_MARKER =
    /^\s*(\[\[entit(?:y|ies):[^\]\n]+\]\])\s*(?:[—–][^\n]*)?$/;
  while (i < lines.length) {
    // Probe-then-consume helper: only advance `i` past the run if at
    // least one valid entity_id was extracted. Otherwise we leave the
    // line for the inline regex below to handle (or to fall through
    // to the next case, e.g. raw entity_id bullets).
    const tryCoalesce = (firstLineRe) => {
      if (!firstLineRe.test(lines[i])) return false;
      const runIds = [];
      let j = i;
      while (j < lines.length) {
        const m = lines[j].match(firstLineRe);
        if (!m) break;
        for (const id of idsFromMarker(m[1])) runIds.push(id);
        j++;
        j = skipStateLines(j);
        j = skipBlanks(j);
      }
      if (runIds.length === 0) return false;
      out.push(`[[entities:${runIds.join(",")}]]`);
      i = j;
      return true;
    };
    // Bulleted-marker run: a group of `- [[entity:…]]` lines (each
    // optionally with a trailing dash-state hint) gets coalesced into
    // a single `[[entities:…]]` block. Without this, six bulleted
    // markers produce six separate `<div class="selora-entity-grid">`
    // blocks stacked vertically with `<br>`s between.
    if (tryCoalesce(MARKER_BULLET)) continue;
    // Bare-marker run: same shape without bullets. The LLM
    // occasionally emits one `[[entities:x]]` line per entity instead
    // of one combined block; coalesce so the area-grouping pass sees
    // the full set as a single grid (otherwise each becomes a
    // one-tile grid, groups.size==1, no headers).
    if (tryCoalesce(BARE_MARKER)) continue;
    // Non-bulleted marker with trailing state hint on the same line —
    // keep the marker, drop the dash hint, then let the loop continue
    // (it might still be followed by separate state-annotation lines).
    const tailMatch = lines[i].match(MARKER_TAIL_STATE);
    if (tailMatch) {
      out.push(tailMatch[1]);
      let j = i + 1;
      j = skipStateLines(j);
      i = j;
      continue;
    }

    const ids = [];
    let j = i;
    while (j < lines.length) {
      const m = lines[j].match(ID_LINE);
      if (!m) break;
      ids.push(m[1]);
      j++;
      // Tolerate blanks between the id line and any annotation lines
      // — the LLM frequently inserts an empty bullet separator.
      j = skipStateLines(j);
      j = skipBlanks(j);
    }
    if (ids.length >= 1) {
      // ID_LINE only matches lines whose entire content is a bullet
      // marker + entity_id — never a paragraph mention. So even a
      // run of one is unambiguously a list, not prose; collapse it
      // to a one-card grid so the user sees a real tile.
      out.push(`[[entities:${ids.join(",")}]]`);
      i = j;
      continue;
    }
    out.push(lines[i]);
    i++;
  }
  return out.join("\n");
}

/** @param {string|null|undefined} text @returns {string} sanitized HTML */
export function renderMarkdown(text) {
  if (!text) return "";
  // While streaming, the LLM emits markers token-by-token. Until the
  // closing `]]` arrives, the partial `[[entity:light.kitch` reads as
  // raw text and looks broken. Trim any unclosed marker at the very
  // end so the bubble shows nothing in its place; once the next chunk
  // completes the marker, it renders as a tile card.
  text = text.replace(/\[\[entit(?:y|ies):[^\]\n]*$/, "");
  // Strip LLM domain-grouping headers like "[Light] (5 total):" or
  // "[Switch] (3):". The tile grid already groups by area; these headers
  // add nothing and look broken in the chat bubble.
  text = text
    .replace(/^\[([A-Za-z_ ]+)\]\s*\(\d+[^)]*\)\s*:?\s*$/gm, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  // Stash code spans before any entity/marker processing. A YAML block
  // showing `entities: [light.kitchen]` must not have its entity_id
  // coalesced into a tile grid, and a model-emitted `[[entity:…]]`
  // inside a fenced snippet must not substitute into a live HA card.
  // Code stays literal — markers and salvage only fire on prose.
  // Sentinels use Unicode Private Use Area (U+E000) so they never
  // collide with real LLM output.
  const STASH_OPEN = "";
  const STASH_CLOSE = "";
  /** @type {string[]} */
  const codeBlocks = [];
  // The language tag (`yaml`, `json`, …) that follows the opening
  // fence is renderer metadata, not content to display. Strip it
  // only when it sits on its OWN line — `` ```yaml\nfoo\n``` `` consumes
  // `yaml` as the lang; `` ```some code``` `` keeps `some code` as
  // body verbatim (no newline after the opener, so the first token
  // is content, not a lang tag).
  text = text.replace(
    /```(?:[ \t]*([a-zA-Z][a-zA-Z0-9_+\-]*)[ \t]*\n)?([\s\S]*?)```/g,
    (_m, _lang, body) => {
      codeBlocks.push(body);
      return `${STASH_OPEN}CB${codeBlocks.length - 1}${STASH_CLOSE}`;
    },
  );
  /** @type {string[]} */
  const inlineCode = [];
  text = text.replace(/`([^`\n]+)`/g, (_m, body) => {
    inlineCode.push(body);
    return `${STASH_OPEN}IC${inlineCode.length - 1}${STASH_CLOSE}`;
  });
  // Run the salvage BEFORE escaping so we can match the raw source —
  // sentinels above ensure it only sees prose.
  text = _coalesceEntityListings(text);
  let escaped = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  // Headings (#### → h6, ### → h5, ## → h4, # → h3) — sized for chat bubbles
  escaped = escaped.replace(
    /^####\s+(.+)$/gm,
    '<div style="font-weight:700;font-size:14px;margin:10px 0 4px;">$1</div>',
  );
  escaped = escaped.replace(
    /^###\s+(.+)$/gm,
    '<div style="font-weight:700;font-size:15px;margin:12px 0 4px;">$1</div>',
  );
  escaped = escaped.replace(
    /^##\s+(.+)$/gm,
    '<div style="font-weight:700;font-size:16px;margin:14px 0 6px;">$1</div>',
  );
  escaped = escaped.replace(
    /^#\s+(.+)$/gm,
    '<div style="font-weight:700;font-size:17px;margin:16px 0 6px;">$1</div>',
  );
  // Entity references — single OR list — both render as a grid of real
  // HA tile cards (hydrated by the chat layer via `loadCardHelpers`).
  // We accept the legacy `[[entity:<id>|<label>]]` form and the
  // `[[entities:<id1>,<id2>,…]]` form; both end up in the same grid
  // placeholder so the rendering is uniform — small singletons become
  // a one-card grid, enumerations become a wrapping multi-card grid.
  // The `<label>` from the legacy form is ignored: HA's tile card
  // already shows the friendly_name from the registry.
  escaped = escaped.replace(
    /\[\[entity:([a-z_]+\.[a-z0-9_\-]+)\|[^\]]+?\]\]/g,
    (_m, id) =>
      `<div class="selora-entity-grid" data-entity-ids="${id}"></div>`,
  );
  escaped = escaped.replace(
    /\[\[entities:([a-z_]+\.[a-z0-9_\-]+(?:,[a-z_]+\.[a-z0-9_\-]+)*)\]\]/g,
    (_m, ids) =>
      `<div class="selora-entity-grid" data-entity-ids="${ids}"></div>`,
  );
  // Bold — inline accent color so it works regardless of theme resolution
  escaped = escaped.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  // Italic (*text*) — require non-word boundaries
  escaped = escaped.replace(
    /(?<!\w)\*([^\s*](?:.*?[^\s*])?)\*(?!\w)/g,
    "<em>$1</em>",
  );
  // Italic (_text_) — underscore style; boundaries avoid matching entity_names
  escaped = escaped.replace(
    /(?<![a-zA-Z0-9_])_([^\s_](?:.*?[^\s_])?)_(?![a-zA-Z0-9_])/g,
    "<em>$1</em>",
  );
  // Numbered lists: lines starting with "1. ", "2. ", etc.
  escaped = escaped.replace(
    /^(\d+)\.\s+(.+)$/gm,
    '<div style="display:flex;gap:6px;margin:2px 0 2px 4px;align-items:baseline;"><span style="opacity:0.55;flex-shrink:0;min-width:18px;">$1.</span><span style="flex:1;">$2</span></div>',
  );
  // Bullet lists: lines starting with "- " or "• "
  escaped = escaped.replace(
    /^[-•]\s+(.+)$/gm,
    '<div style="margin:4px 0 4px 8px;padding-left:12px;border-left:2px solid rgba(251,191,36,0.35);">$1</div>',
  );
  // Line breaks
  escaped = escaped.replace(/\n/g, "<br>");
  // Block-level entity grid divs don't need surrounding <br> — they
  // already carry their own margin. Strip any <br> immediately before
  // the opening tag or immediately after the closing tag so the LLM's
  // newlines don't add extra visual space above/below the tile grid.
  // The opening div carries a `data-entity-ids` attribute after the
  // class — match the whole opening-through-closing-tag with [^>]* so
  // the after-strip fires regardless of attribute order, otherwise
  // <br>'s survive on one side and the tile gets uneven spacing.
  escaped = escaped.replace(/(<br>)+(<div class="selora-entity-grid")/g, "$2");
  escaped = escaped.replace(
    /(<div class="selora-entity-grid"[^>]*><\/div>)(<br>)+/g,
    "$1",
  );

  // Restore stashed code with HTML-escaped bodies wrapped in <pre> / <code>.
  // Done last so prior passes never touched the code contents — entity_ids
  // and `[[entity:…]]` markers inside YAML or other snippets stay literal.
  const escapeCode = (s) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const stashRe = new RegExp(STASH_OPEN + "(CB|IC)(\\d+)" + STASH_CLOSE, "g");
  escaped = escaped.replace(stashRe, (_m, kind, idx) => {
    const body = (kind === "CB" ? codeBlocks : inlineCode)[Number(idx)] ?? "";
    if (kind === "CB") {
      return `<pre style="background:var(--primary-background-color,#18181b);color:var(--primary-text-color,#e4e4e7);padding:10px;border-radius:8px;border:1px solid var(--divider-color,#27272a);font-size:12px;overflow-x:auto;margin:8px 0;">${escapeCode(body)}</pre>`;
    }
    return `<code style="background:var(--secondary-background-color,rgba(255,255,255,0.08));padding:2px 5px;border-radius:4px;font-size:13px;border:1px solid var(--divider-color,rgba(255,255,255,0.06));">${escapeCode(body)}</code>`;
  });

  return escaped;
}
