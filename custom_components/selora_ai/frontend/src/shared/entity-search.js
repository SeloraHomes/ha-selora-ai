// Text search over automations and scenes.
//
// The list boxes filtered on the alias/name alone, which meant the one thing a
// homeowner most often starts from — "which automations use this device?" —
// could not be asked at all: an automation's title rarely names every entity it
// touches, and a device's own name appears nowhere in the record the panel
// holds. So a query is matched against the rule's own text AND against every
// entity, device, area, floor and label it REFERENCES, resolved through the
// registries the panel already has.
//
// A ref hit is reported back as a `reason`, because a row whose title and
// description contain none of the typed words otherwise reads as a bug. The
// caller renders it under the row: "matches MYGGSPRAY wrlss mtn sensor".

// ── Normalisation ────────────────────────────────────────────────────────

// Casefold and strip diacritics so "lumieres" finds "Lumières" and vice versa —
// the panel ships in 13 locales and a user typing from memory (or from a
// keyboard without the accent) must still find their own device. Underscores
// and dots become spaces so "kitchen light" matches the entity_id
// `light.kitchen_light` as well as the friendly name.
export function normalizeSearch(value) {
  if (value == null) return "";
  return String(value)
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[_.]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

// Split a query into terms. Every term has to match something (AND), which is
// what makes "basement light" narrower than either word alone. A "quoted
// phrase" is kept whole so a multi-word device name can be pinned.
//
// The closing quote is OPTIONAL and a bare quote is dropped rather than kept in
// a term. Someone typing a phrase is mid-keystroke at `"game area` on every
// character between the two quotes, and a literal `"` inside the term matches
// nothing — so the list would empty out while they type and refill on the last
// character, which reads as the search being broken.
export function searchTerms(query) {
  const raw = String(query ?? "");
  const terms = [];
  for (const m of raw.matchAll(/"([^"]*)"?|([^\s"]+)/g)) {
    const term = normalizeSearch(m[1] ?? m[2]);
    if (term) terms.push(term);
  }
  return terms;
}

// ── Registry lookups ─────────────────────────────────────────────────────

function _deviceLabel(device) {
  if (!device) return "";
  return String(device.name_by_user || device.name || "").trim();
}

/**
 * Wrap whatever registries the panel currently holds into O(1) lookups.
 *
 * Resolution is per id rather than a prebuilt index: an install has thousands
 * of entities and only the handful an automation references is ever asked
 * about, so building a map per keystroke would cost far more than it saves.
 *
 * `full` holds the registries the `hass` object cannot answer from: the full
 * entity registry (the display one omits `device_id`, so without it an entity
 * cannot reach its device or the area it inherits from it) plus the floor and
 * label registries, which are not on `hass` at all. It is OPTIONAL and the
 * lookups degrade rather than fail without it — an entity still yields its name
 * and its own area, and a floor or label still matches by its slug.
 *
 * `hass` may be a getter. The panel replaces its whole `hass` object on every
 * state change, and the per-record field cache below keys on THIS wrapper's
 * identity — so a wrapper rebuilt per hass would throw that cache away several
 * times a second in a busy home. Reading through a getter keeps the wrapper
 * stable while the states it reads stay live.
 *
 * @param {Object|function(): Object} hass
 * @param {{entities?: Object, devices?: Object, areas?: Object,
 *          floors?: Object, labels?: Object}} [full]
 */
export function buildSearchRegistry(hass, full = null) {
  const hassOf = typeof hass === "function" ? hass : () => hass;

  // The LIVE collection wins. `full` is fetched once and never refreshed, so
  // preferring it would keep answering with a device's old name for the rest of
  // the panel's lifetime after a rename; it is here to supply what the live
  // collections lack (the entity→device link) and to stand in for the two
  // registries `hass` does not carry at all (floors, labels). An empty live
  // collection is an HA that does not ship it, not an empty home.
  const pick = (key) => {
    const live = hassOf()?.[key];
    if (live && Object.keys(live).length) return live;
    return full?.[key] || {};
  };

  const area = (areaId) => {
    if (!areaId) return "";
    return String(pick("areas")[areaId]?.name || "").trim();
  };

  // Floors and labels are targetable but neither is on the `hass` object the
  // panel receives, so these resolve only once the full registries land. The id
  // is a slug of the name (`ground_floor`, `holiday`), so a query still matches
  // through the raw id in the meantime.
  const floor = (floorId) => {
    if (!floorId) return "";
    return String(pick("floors")[floorId]?.name || "").trim();
  };

  const label = (labelId) => {
    if (!labelId) return "";
    return String(pick("labels")[labelId]?.name || "").trim();
  };

  const device = (deviceId) => {
    if (!deviceId) return null;
    const d = pick("devices")[deviceId];
    if (!d) return null;
    return { name: _deviceLabel(d), area: area(d.area_id) };
  };

  const entity = (entityId) => {
    if (!entityId) return null;
    const h = hassOf();
    const state = h?.states?.[entityId];
    // Read each field from whichever registry actually carries it, live copy
    // first: the display registry is current but omits `device_id`, the full
    // one has it but was fetched once.
    const live = h?.entities?.[entityId];
    const stored = full?.entities?.[entityId];
    if (!state && !live && !stored) return null;
    const name =
      state?.attributes?.friendly_name ||
      live?.name ||
      live?.original_name ||
      stored?.name ||
      stored?.original_name ||
      "";
    const dev = device(live?.device_id || stored?.device_id);
    // Most homes assign the area on the DEVICE and let entities inherit, so an
    // entity with no area_id of its own is not arealess.
    const areaName = area(live?.area_id || stored?.area_id) || dev?.area || "";
    return {
      entity_id: entityId,
      name: String(name || "").trim(),
      area: areaName,
      device: dev?.name || "",
    };
  };

  return { entity, device, area, floor, label };
}

// ── Reference collection ─────────────────────────────────────────────────

const ENTITY_ID_RE = /^[a-z0-9_]+\.[a-z0-9_]+$/;

// The five things an HA action can be targeted at. A search that knows only
// three of them reports "no automations" for a rule the user can see targeting
// their Ground Floor.
const _ID_BUCKET = {
  device_id: "devices",
  area_id: "areas",
  floor_id: "floors",
  label_id: "labels",
};

// A scalar id field may be a comma-separated list in hand-written YAML
// (`entity_id: light.a, light.b`), which HA still accepts.
function _idParts(value) {
  const raw =
    typeof value === "string" ? [value] : Array.isArray(value) ? value : [];
  const out = [];
  for (const v of raw) {
    if (typeof v !== "string") continue;
    for (const part of v.split(",")) {
      const trimmed = part.trim();
      if (trimmed) out.push(trimmed);
    }
  }
  return out;
}

/**
 * Walk a trigger/condition/action tree collecting every entity, device, area,
 * floor and label it names. Recursive rather than top-level-only: the ids that
 * matter are routinely nested inside `choose` branches, `sequence`s and
 * `target` blocks, and a flat scan finds none of them.
 */
export function collectConfigRefs(node, acc = null) {
  const out = acc || {
    entities: new Set(),
    devices: new Set(),
    areas: new Set(),
    floors: new Set(),
    labels: new Set(),
  };
  if (node == null) return out;
  if (Array.isArray(node)) {
    for (const item of node) collectConfigRefs(item, out);
    return out;
  }
  if (typeof node !== "object") return out;
  for (const [key, value] of Object.entries(node)) {
    // `entity_id` plus every service-specific spelling of it — `tts.speak`
    // addresses its speaker as `media_player_entity_id`, and each of those is
    // the one reference the automation makes to that device.
    if (key === "entity_id" || key.endsWith("_entity_id")) {
      // A templated target (`{{ … }}`) or the `all` sentinel is not an id.
      for (const v of _idParts(value)) {
        if (ENTITY_ID_RE.test(v)) out.entities.add(v);
      }
    } else if (_ID_BUCKET[key]) {
      for (const v of _idParts(value)) out[_ID_BUCKET[key]].add(v);
    }
    if (value && typeof value === "object") collectConfigRefs(value, out);
  }
  return out;
}

// ── Searchable fields ────────────────────────────────────────────────────

// A ref is ONE named thing, and its searchable text covers only that thing.
// Folding an entity's device and area into the entity's own blob made the
// reported reason a lie: searching "basement" over a kitchen light whose hub
// happens to live there answered "matches Kitchen Light". An entity's device
// and its area are emitted as their own refs instead, so whatever matched is
// what gets named.
function _refFields(label, blob) {
  return { label, norm: normalizeSearch(blob) };
}

function _pushRef(out, seen, ref) {
  if (!ref.norm || seen.has(ref.norm)) return;
  seen.add(ref.norm);
  out.push(ref);
}

function _buildRefs(reg, refs) {
  const out = [];
  const seen = new Set();
  // A name is the ref's label; the id rides in its searchable text so a user
  // who typed (or pasted) an entity_id still finds it, and so a floor or label
  // stays findable by its slug before the registries that name it resolve.
  const add = (label, ...extra) =>
    _pushRef(out, seen, _refFields(label, [label, ...extra].join(" ")));

  for (const id of refs.entities) {
    const info = reg.entity(id);
    add(info?.name || id, id);
    // Derived refs: a device and an area are things in their own right, and
    // deduping by text means one area shared by ten members is named once.
    if (info?.device) add(info.device);
    if (info?.area) add(info.area);
  }
  for (const id of refs.devices) {
    const info = reg.device(id);
    add(info?.name || id, id);
    if (info?.area) add(info.area);
  }
  for (const id of refs.areas) add(reg.area(id) || id, id);
  for (const id of refs.floors || []) add(reg.floor(id) || id, id);
  for (const id of refs.labels || []) add(reg.label(id) || id, id);
  return out;
}

// Cache the derived fields on the record object. The lists are rebuilt
// wholesale by every websocket refresh, so a WeakMap keyed on the record is
// self-invalidating; the registry is compared by identity too, because a
// late-resolving full registry changes what a ref resolves to.
//
// The registry wrapper therefore has to be REBUILT whenever a name a ref
// resolved could have changed — see `_searchRegistry()` in panel.js, which
// keys its memo on the registry objects rather than on `hass`. Rebuilding per
// `hass` instead would drop this cache several times a second; never
// rebuilding would leave a renamed device findable only under its old name.
const _fieldCache = new WeakMap();

function _cached(record, reg, build) {
  if (!record || typeof record !== "object") return build();
  const hit = _fieldCache.get(record);
  if (hit && hit.reg === reg) return hit.fields;
  const fields = build();
  _fieldCache.set(record, { reg, fields });
  return fields;
}

/**
 * Searchable text for one automation: its own words, plus one ref per entity /
 * device / area it references.
 */
export function automationSearchFields(automation, reg) {
  return _cached(automation, reg, () => {
    const primary = normalizeSearch(
      [
        automation?.alias,
        automation?.description,
        automation?.entity_id,
        automation?.recipe_title,
      ]
        .filter(Boolean)
        .join(" "),
    );
    const refs = collectConfigRefs({
      triggers: automation?.triggers,
      conditions: automation?.conditions,
      actions: automation?.actions,
    });
    return { primary, refs: _buildRefs(reg, refs) };
  });
}

/**
 * Searchable text for one scene. A scene's members live as the KEYS of its
 * `entities` mapping, so there is no config tree to walk.
 */
export function sceneSearchFields(scene, reg) {
  return _cached(scene, reg, () => {
    const primary = normalizeSearch(
      [scene?.name, scene?.entity_id, scene?.scene_id]
        .filter(Boolean)
        .join(" "),
    );
    const memberIds =
      scene?.entities && typeof scene.entities === "object"
        ? Object.keys(scene.entities).filter((id) => ENTITY_ID_RE.test(id))
        : [];
    // A scene names no area or device directly, but `_buildRefs` derives both
    // from each member — which is what "scenes in the basement" means to the
    // person typing it.
    const refs = _buildRefs(reg, {
      entities: new Set(memberIds),
      devices: new Set(),
      areas: new Set(),
    });
    return { primary, refs };
  });
}

// ── Matching ─────────────────────────────────────────────────────────────

/**
 * Match `fields` against `terms`, all of which must hit something.
 *
 * @returns {{match: boolean, reasons: string[]}} `reasons` names the
 *   referenced things a term matched that the row's own text does not mention —
 *   what the caller shows to explain a non-obvious hit.
 */
export function matchesSearchFields(fields, terms) {
  if (!terms.length) return { match: true, reasons: [] };
  const reasons = [];
  const seen = new Set();
  for (const term of terms) {
    if (fields.primary.includes(term)) continue;
    let hit = false;
    for (const ref of fields.refs) {
      if (!ref.norm.includes(term)) continue;
      hit = true;
      if (ref.label && !seen.has(ref.label)) {
        seen.add(ref.label);
        reasons.push(ref.label);
      }
    }
    if (!hit) return { match: false, reasons: [] };
  }
  return { match: true, reasons };
}
