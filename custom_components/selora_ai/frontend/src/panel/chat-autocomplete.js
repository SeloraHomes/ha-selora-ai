// Chat composer autocomplete: suggest devices / areas / scenes / automations
// inline as the user types, then surface the chosen entity IDs to the backend
// via [[entity:…]] / [[entities:…]] markers so the LLM no longer has to guess
// which device the user meant.
//
// Pure helpers in this file (no DOM, no Lit) — the composer in render-chat.js
// is the only consumer. Unit tests cover trigger detection and ranking.

// Minimum chars typed after a trigger phrase before the dropdown opens
// for FUZZY ranking. Short queries below this threshold still open the
// dropdown if they EXACTLY match a device name (so 2-letter devices like
// "AC", "TV", "PC" are reachable without the panel firing on every key).
export const AUTOCOMPLETE_MIN_CHARS = 3;
export const AUTOCOMPLETE_MAX_RESULTS = 6;

// User-facing entity domains worth offering as device autocompletes. Sensors
// and binary_sensors are intentionally excluded — they bloat the dropdown
// and are rarely what users address by name in chat.
const DEVICE_DOMAINS = new Set([
  "light",
  "switch",
  "lock",
  "cover",
  "fan",
  "media_player",
  "climate",
  "vacuum",
  "camera",
  "humidifier",
  "water_heater",
  "input_boolean",
  "input_select",
  "input_number",
  "input_button",
  "remote",
  "lawn_mower",
]);

const DOMAIN_ICONS = {
  light: "mdi:lightbulb",
  switch: "mdi:toggle-switch",
  lock: "mdi:lock",
  cover: "mdi:window-shutter",
  fan: "mdi:fan",
  media_player: "mdi:speaker",
  climate: "mdi:thermostat",
  vacuum: "mdi:robot-vacuum",
  camera: "mdi:cctv",
  humidifier: "mdi:air-humidifier",
  water_heater: "mdi:water-boiler",
  remote: "mdi:remote",
  lawn_mower: "mdi:mower",
  input_boolean: "mdi:toggle-switch-outline",
  input_select: "mdi:form-dropdown",
  input_number: "mdi:numeric",
  input_button: "mdi:gesture-tap-button",
  scene: "mdi:palette",
  automation: "mdi:robot",
  script: "mdi:script-text",
  area: "mdi:floor-plan",
};

// Trigger phrases mapped to the suggestion kind they should open.
// Each pattern must end at the caret (`$`) and be preceded by a word
// boundary (`\b`).
//
// Triggers are intentionally INTENT-DRIVEN: bare "the " / "my " are not
// triggers because they appear in too much general prose ("tell me the
// weather", "what's the time"). Devices only open when the user has
// typed an actuating verb that signals they're addressing a controllable
// thing. Power users who want to bypass natural-language heuristics can
// use the explicit `@` trigger to query any kind.
const TRIGGERS = [
  // Universal explicit trigger — `@` opens devices regardless of context.
  // `includeAreas` adds the area registry to the same dropdown so the
  // user can pick "Bedroom" the area as easily as "Bed Light" the device.
  { kind: "device", pattern: /(?:^|\s)@$/, includeAreas: true },

  // Areas — "in the kitchen", "in kitchen", "lights of the kitchen"
  { kind: "area", pattern: /\bin (?:the |a )?$/i },
  { kind: "area", pattern: /\bof (?:the |a )?$/i },

  // Scenes — "activate movie night", "scene cozy", "set the scene cozy"
  { kind: "scene", pattern: /\bactivate $/i },
  { kind: "scene", pattern: /\bset (?:the )?scene $/i },
  { kind: "scene", pattern: /\bscene $/i },

  // Automations — "run morning routine", "trigger lights off"
  { kind: "automation", pattern: /\brun $/i },
  { kind: "automation", pattern: /\btrigger $/i },
  { kind: "automation", pattern: /\bexecute (?:the )?automation $/i },

  // Devices — verb-led only, grouped by the domains each verb actually
  // operates on. "lock the …" shouldn't suggest sensors or lights;
  // "dim the …" shouldn't suggest doors. Domain hints keep the
  // dropdown short and unambiguous. `turn on/off` and `set` stay
  // unconstrained because they apply to virtually any actuating entity.
  {
    kind: "device",
    pattern: /\b(?:lock|unlock) (?:the |my )?$/i,
    domains: ["lock"],
  },
  {
    kind: "device",
    pattern: /\b(?:dim|brighten) (?:the |my )?$/i,
    domains: ["light"],
  },
  {
    kind: "device",
    pattern: /\b(?:open|close) (?:the |my )?$/i,
    domains: ["cover", "lock"],
  },
  {
    kind: "device",
    pattern: /\b(?:play|pause|resume|mute|unmute) (?:the |my )?$/i,
    domains: ["media_player"],
  },
  {
    kind: "device",
    pattern: /\b(?:start|stop) (?:the |my )?$/i,
    domains: ["vacuum", "lawn_mower", "media_player", "fan"],
  },
  // Generic actuation — no domain constraint. Also surface areas so
  // "turn on the bedroom" can disambiguate into the Bedroom area
  // (i.e. all devices in it) instead of forcing the LLM to guess.
  {
    kind: "device",
    pattern: /\b(?:turn (?:on|off)|set) (?:the |my )?$/i,
    includeAreas: true,
  },
  // Bare "the " / "my " as a fallback. These fire in plenty of regular
  // prose ("tell me the weather"), so we lean on the 3-char minimum
  // and the no-match guard to stay quiet: the dropdown only opens when
  // a typed-3+chars query actually matches a device or area name.
  // That covers natural phrasings like "Create an automation with the
  // kitchen's ceiling lights" without a verb up front.
  { kind: "device", pattern: /\bthe $/i, includeAreas: true },
  { kind: "device", pattern: /\bmy $/i, includeAreas: true },
];

// Stop chars that end the "query being typed" — newline and sentence
// terminators only. Spaces are allowed because friendly names like
// "Living Room Lamp" contain them.
const QUERY_STOP_RE = /[\n.?!]/;
const MAX_QUERY_LEN = 40;

// ── Ghost-text completion ─────────────────────────────────────────────
//
// Inline suggestion of common words the user types when chatting with
// Selora. As the user types "Create an auto", the remainder "mation"
// renders in a lighter color after the caret; pressing Tab accepts it.
// This is independent of the entity dropdown — ghost text covers
// *vocabulary*, the dropdown covers *named things in the home*.
//
// Words are sorted by length ASC so the matching pass picks the
// SHORTEST viable completion first — "auto" suggests "automation",
// not "automations", because the shorter target is the more common
// intent. The longer form is still reachable: once the user has
// typed "automation", the matcher returns null (already complete);
// typing one more "s" past it bypasses the ghost entirely.
const GHOST_VOCABULARY = [
  "automation",
  "automations",
  "trigger",
  "triggers",
  "condition",
  "conditions",
  "action",
  "actions",
  "scene",
  "scenes",
  "script",
  "scripts",
  "device",
  "devices",
  "entity",
  "entities",
  "schedule",
  "weekday",
  "weekdays",
  "weekend",
  "weekends",
  "midnight",
  "morning",
  "afternoon",
  "evening",
  "sunrise",
  "sunset",
  "minutes",
  "hours",
  "seconds",
  "temperature",
  "brightness",
  "thermostat",
  "lights",
  "lighting",
  "bedroom",
  "bathroom",
  "kitchen",
  "living",
  "garage",
  "office",
  "hallway",
  "basement",
  "downstairs",
  "upstairs",
  "outside",
  "create",
  "suggest",
  "notify",
  "notification",
  "between",
  "before",
  "after",
  "during",
  "while",
  "everyone",
  "nobody",
].sort((a, b) => a.length - b.length);

const GHOST_MIN_PREFIX = 3;

// Find the partial word ending at the caret. Returns { word, start } or null.
function _partialWordAt(text, caret) {
  if (caret <= 0) return null;
  let i = caret;
  while (i > 0 && /\w/.test(text[i - 1])) i--;
  const word = text.slice(i, caret);
  if (!word) return null;
  return { word, start: i };
}

// Return the suffix that completes the partial word into a known common
// word, or null. Match is case-insensitive but the suffix is emitted in
// the canonical (lowercase) form of the vocabulary entry — which is
// what users typing "Create an Auto" expect to see continued as
// "mation" (lowercase, since that's how they tend to type the tail).
export function findGhostSuggestion(text, caret) {
  if (typeof text !== "string") return null;
  // Ignore when the next char is a word char — we're mid-word, not
  // at a place where extending makes sense.
  if (caret < text.length && /\w/.test(text[caret])) return null;
  const part = _partialWordAt(text, caret);
  if (!part || part.word.length < GHOST_MIN_PREFIX) return null;
  const lower = part.word.toLowerCase();
  for (const w of GHOST_VOCABULARY) {
    if (w === lower) return null; // already complete
    if (w.startsWith(lower)) {
      return { suffix: w.slice(lower.length), word: w, start: part.start };
    }
  }
  return null;
}

// Article words that the verb-led device triggers optionally consume.
// If, after matching, the query turns out to be one of these (because
// the user has typed "turn on the" but not yet a device name), we
// suppress the dropdown until they type a real word. Without this the
// dropdown pops up the instant the article is complete, even though
// no device has been named yet.
const ARTICLE_WORDS = new Set(["the", "my", "a", "an"]);

// ── Trigger detection ─────────────────────────────────────────────────

// Inspect the text up to the caret and return the active trigger context,
// or null if none. Returns { kind, query, start, end } where [start, end)
// is the slice of `text` that should be REPLACED when the user picks a
// suggestion (i.e. the partial query, NOT the trigger phrase itself).
export function detectTrigger(text, caret) {
  if (typeof text !== "string" || caret == null || caret < 0) return null;
  const before = text.slice(0, caret);

  // The query is whatever the user has typed since the last whitespace/
  // newline-bounded trigger phrase. We scan back from the caret to find the
  // longest contiguous run that doesn't contain a query-terminator, then
  // look for a trigger phrase ending right where that run starts.
  //
  // Example: "turn on the kit"
  //   queryStart = 12 ("kit"), prefix = "turn on the " → matches "the "
  //   → returns { kind: 'device', query: 'kit', start: 12, end: 15 }

  // Walk back over characters that could be part of a friendly name. We
  // accept letters, digits, spaces, hyphen, underscore, apostrophe. A
  // newline, period, ? or ! terminates the run.
  let queryStart = caret;
  while (queryStart > 0) {
    const ch = before[queryStart - 1];
    if (QUERY_STOP_RE.test(ch)) break;
    queryStart -= 1;
    if (caret - queryStart > MAX_QUERY_LEN) break;
  }

  // Try every position in the candidate run as a possible trigger
  // boundary. The LATEST match (closest to the caret) wins — this is what
  // lets longer phrases like "in the " beat the shorter "in " they
  // contain, and it keeps the resulting `query` tight (just "kit" rather
  // than "the kit"). Friendly names with spaces still work because the
  // earlier trigger word ("the ") consumes only its own characters.
  let best = null;
  for (let qs = queryStart; qs <= caret; qs++) {
    const prefix = before.slice(0, qs);
    for (const trig of TRIGGERS) {
      if (trig.pattern.test(prefix)) {
        best = {
          kind: trig.kind,
          query: before.slice(qs, caret),
          start: qs,
          end: caret,
          domains: trig.domains || null,
          includeAreas: !!trig.includeAreas,
        };
        break;
      }
    }
  }
  if (!best) return null;
  // Empty query is allowed ONLY when the verb already narrowed to a small
  // domain set (e.g. "unlock the " → list every lock). For unconstrained
  // triggers we still wait for at least one typed character — otherwise
  // we'd dump every device in the home.
  if (!best.query.trim() && !best.domains) return null;
  // If the user has typed only an article ("turn on the"), the verb-led
  // trigger matched the shorter "turn on " form and treated "the" as the
  // query. Wait until they start typing the device name.
  if (ARTICLE_WORDS.has(best.query.trim().toLowerCase())) return null;
  return best;
}

// ── Suggestion index ──────────────────────────────────────────────────

// Build a single flat list of suggestion items for ALL kinds, tagged with
// `kind` so the caller can filter. Recomputed lazily by the composer when
// the trigger fires; the panel doesn't bother memoising because hass.states
// is already an object reference the panel re-reads each render.
//
// Items shape:
//   { kind, entity_id, label, area_id, area, icon, _lowerLabel }
//
// `_lowerLabel` is precomputed because ranking calls toLowerCase() on every
// candidate for every keystroke — doing it once here is cheaper.
export function buildSuggestionIndex(hass, areas, devices = null) {
  const items = [];
  if (!hass?.states) return items;

  const areaById = {};
  if (areas && typeof areas === "object") {
    for (const [id, a] of Object.entries(areas)) {
      areaById[id] = a?.name || a?.area_id || id;
    }
  }

  // hass.entities (display registry) gives us area_id for each entity —
  // the state object itself doesn't carry it. Most HA setups assign
  // areas at the DEVICE level (entity inherits via device_id), so the
  // optional `devices` map lets us fall back to the device's area when
  // the entity row has none of its own. Without this, "Bed Light" in
  // five different bedrooms would render with no area chip and be
  // indistinguishable.
  const entReg = hass.entities || {};

  for (const [entityId, state] of Object.entries(hass.states)) {
    const domain = entityId.split(".")[0];
    // Skip entities with no real friendly_name. A device the user hasn't
    // named (state.attributes.friendly_name is unset) renders as the raw
    // entity_id in the dropdown — visual noise next to properly-named
    // siblings. The chat input still accepts the entity_id directly for
    // power users; we just don't surface it as a suggestion.
    const friendly = state?.attributes?.friendly_name;
    if (!friendly) continue;
    const entry = entReg[entityId];
    let areaId = entry?.area_id || null;
    if (!areaId && entry?.device_id && devices) {
      areaId = devices[entry.device_id]?.area_id || null;
    }
    const areaName = areaId ? areaById[areaId] || null : null;

    if (DEVICE_DOMAINS.has(domain)) {
      items.push({
        kind: "device",
        domain,
        entity_id: entityId,
        label: friendly,
        area_id: areaId,
        area: areaName,
        icon: DOMAIN_ICONS[domain] || "mdi:devices",
        _lowerLabel: friendly.toLowerCase(),
      });
    } else if (domain === "scene") {
      items.push({
        kind: "scene",
        domain,
        entity_id: entityId,
        label: friendly,
        area_id: areaId,
        area: areaName,
        icon: DOMAIN_ICONS.scene,
        _lowerLabel: friendly.toLowerCase(),
      });
    } else if (domain === "automation") {
      items.push({
        kind: "automation",
        domain,
        entity_id: entityId,
        label: friendly,
        area_id: null,
        area: null,
        icon: DOMAIN_ICONS.automation,
        _lowerLabel: friendly.toLowerCase(),
      });
    } else if (domain === "script") {
      // Surface scripts under the "run X" trigger too — users think of
      // scripts and automations interchangeably when speaking aloud.
      items.push({
        kind: "automation",
        domain,
        entity_id: entityId,
        label: friendly,
        area_id: null,
        area: null,
        icon: DOMAIN_ICONS.script,
        _lowerLabel: friendly.toLowerCase(),
      });
    }
  }

  for (const [areaId, name] of Object.entries(areaById)) {
    items.push({
      kind: "area",
      entity_id: null,
      area_id: areaId,
      label: name,
      area: null,
      icon: DOMAIN_ICONS.area,
      _lowerLabel: name.toLowerCase(),
    });
  }

  return items;
}

// ── Ranking ───────────────────────────────────────────────────────────

// Score how well `item` matches `lowerQuery`. Higher = better.
// 0 means "no match, drop it".
//
// Heuristics (highest first):
//   * any word in the label EXACTLY equals the query  → 1500
//     (so "bed" → "Bed Light" beats "Bedroom" because
//      "bed" is a whole word in the former but only a
//      prefix-of-a-word in the latter)
//   * prefix match on the whole label                 → 1000
//   * any word in the label starts with the query     →  500
//   * substring match anywhere in the label           →  100
//   * fuzzy: every char of query appears in order     →   10
// Ties broken by shorter labels (more specific) and alphabetical.
function _scoreItem(item, lowerQuery) {
  const label = item._lowerLabel;
  if (!label) return 0;
  const words = label.split(/\s+/);
  for (const w of words) {
    if (w === lowerQuery) return 1500;
  }
  if (label.startsWith(lowerQuery)) return 1000;
  for (const w of words) {
    if (w.startsWith(lowerQuery)) return 500;
  }
  if (label.includes(lowerQuery)) return 100;
  // Subsequence match (each character of the query appears in order)
  let qi = 0;
  for (let i = 0; i < label.length && qi < lowerQuery.length; i++) {
    if (label[i] === lowerQuery[qi]) qi += 1;
  }
  if (qi === lowerQuery.length) return 10;
  return 0;
}

// Find items whose label is an EXACT case-insensitive match for the query.
// Used to surface short device names (e.g. "AC") before the fuzzy
// AUTOCOMPLETE_MIN_CHARS threshold kicks in — typing the full name of a
// 2-letter device is unambiguous and worth a chip.
// Return every item in the requested kind/domains, sorted alphabetically.
// Used when the user has typed a domain-constraining verb but no query
// yet ("unlock the ") — at that point the candidate set is small enough
// to just enumerate.
export function listByDomain(
  items,
  kind,
  domains,
  max = AUTOCOMPLETE_MAX_RESULTS,
) {
  if (!items?.length || !domains?.length) return [];
  const domainSet = new Set(domains);
  const out = [];
  for (const it of items) {
    if (it.kind !== kind) continue;
    if (!domainSet.has(it.domain)) continue;
    out.push(it);
  }
  out.sort((a, b) => a.label.localeCompare(b.label));
  return out.slice(0, max);
}

// `domains`, when provided, is an array of HA domains (e.g. ["lock"]).
// Only items belonging to one of those domains pass through. Pass null
// or undefined to skip the filter (unconstrained verb / `@` shortcut).
export function findExactMatches(items, kind, query, domains = null) {
  if (!items?.length || !query) return [];
  const lowerQuery = query.trim().toLowerCase();
  if (!lowerQuery) return [];
  const domainSet = domains ? new Set(domains) : null;
  const out = [];
  for (const it of items) {
    if (it.kind !== kind) continue;
    if (domainSet && !domainSet.has(it.domain)) continue;
    if (it._lowerLabel === lowerQuery) out.push(it);
  }
  return out;
}

export function rankSuggestions(
  items,
  kind,
  query,
  max = AUTOCOMPLETE_MAX_RESULTS,
  domains = null,
) {
  if (!items?.length || !query) return [];
  const lowerQuery = query.trim().toLowerCase();
  if (!lowerQuery) return [];
  const domainSet = domains ? new Set(domains) : null;
  const scored = [];
  for (const it of items) {
    if (it.kind !== kind) continue;
    if (domainSet && !domainSet.has(it.domain)) continue;
    const score = _scoreItem(it, lowerQuery);
    if (score > 0) scored.push({ item: it, score });
  }
  scored.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (a.item.label.length !== b.item.label.length) {
      return a.item.label.length - b.item.label.length;
    }
    return a.item.label.localeCompare(b.item.label);
  });
  return scored.slice(0, max).map((s) => s.item);
}

// ── Selection / marker emission ───────────────────────────────────────

// Replace the partial query in `text` with the chosen item's friendly label
// and return the new text + caret position. Pure — no DOM side effects.
export function applySelection(text, trigger, item) {
  const before = text.slice(0, trigger.start);
  const after = text.slice(trigger.end);
  const insert = item.label;
  // Add a trailing space so the user can keep typing without re-positioning.
  const needsSpace = !after.startsWith(" ");
  const inserted = needsSpace ? insert + " " : insert;
  const newText = before + inserted + after;
  const newCaret = trigger.start + inserted.length;
  return { text: newText, caret: newCaret };
}

// Build the [[entities:…]] suffix that gets appended to the outgoing user
// message so the backend can resolve devices/scenes/automations without
// fuzzy matching. Areas don't have entity IDs, so we emit them as a
// human-readable hint instead.
//
// `selections` is the list of items the user explicitly picked from the
// dropdown during this composition (in the order they were picked).
// Duplicates are de-duped on entity_id / area_id.
export function buildEntityMarker(selections) {
  if (!selections?.length) return "";
  const seenEntities = new Set();
  const seenAreas = new Set();
  const entityIds = [];
  const areaNames = [];
  for (const sel of selections) {
    if (sel.entity_id) {
      if (seenEntities.has(sel.entity_id)) continue;
      seenEntities.add(sel.entity_id);
      entityIds.push(sel.entity_id);
    } else if (sel.kind === "area" && sel.area_id) {
      if (seenAreas.has(sel.area_id)) continue;
      seenAreas.add(sel.area_id);
      areaNames.push(sel.label);
    }
  }
  const parts = [];
  if (entityIds.length === 1) {
    parts.push(`[[entity:${entityIds[0]}]]`);
  } else if (entityIds.length > 1) {
    parts.push(`[[entities:${entityIds.join(",")}]]`);
  }
  if (areaNames.length) {
    parts.push(`[[areas:${areaNames.join(",")}]]`);
  }
  return parts.length ? "\n\n" + parts.join(" ") : "";
}

// Strip [[entity:…]] / [[entities:…]] / [[areas:…]] markers from a user
// message before it's rendered into a bubble. The markers are only meant
// for the LLM payload — when a chat session is reloaded from the backend
// the stored content includes them, and without this scrub they'd leak
// into the visible message.
export function stripEntityMarkers(text) {
  if (typeof text !== "string" || !text) return text;
  return text
    .replace(/\s*\[\[(?:entity|entities|areas):[^\]]+\]\]/g, "")
    .trimEnd();
}

// Drop selections whose friendly label no longer appears as a whole word
// in the message body — e.g. the user typed "the kitchen lamp", selected
// it, then rewrote the prompt. A naive substring check would keep a
// "AC" selection alive when the user replaces it with "back door"
// (because "ac" is a substring of "back"), causing _sendMessage() to
// emit a stale [[entity:…]] marker. Word-boundary matching avoids that
// while still tolerating punctuation around the label.
function _escapeRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
export function pruneStaleSelections(text, selections) {
  if (!selections?.length) return selections;
  return selections.filter((s) => {
    if (!s.label) return false;
    const escaped = _escapeRegex(s.label);
    // \b only fires between a word char and a non-word char, so labels
    // that begin or end with a non-word char (rare) can't anchor; fall
    // back to plain substring in that case.
    const startWord = /^\w/.test(s.label);
    const endWord = /\w$/.test(s.label);
    const pattern = (startWord ? "\\b" : "") + escaped + (endWord ? "\\b" : "");
    return new RegExp(pattern, "i").test(text);
  });
}
