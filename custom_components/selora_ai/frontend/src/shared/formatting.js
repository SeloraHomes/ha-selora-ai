// ---------------------------------------------------------------------------
// Shared formatting utilities
// ---------------------------------------------------------------------------
// Pure functions — pass `hass` explicitly where needed.
// panel.js imports from here to keep formatting logic in one place.
// ---------------------------------------------------------------------------

/** @param {*} value @returns {string} title-cased, underscores replaced with spaces */
export function humanizeToken(value) {
  if (value == null || value === "") return "";
  return String(value)
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** @param {{ states?: Object }} hass @param {string} id @returns {string} friendly name or humanized entity name */
export function fmtEntity(hass, id) {
  if (!id) return "";
  const eid = String(id);
  const stateObj = hass?.states?.[eid];
  if (stateObj?.attributes?.friendly_name)
    return stateObj.attributes.friendly_name;
  const parts = eid.split(".");
  const raw = (parts.length > 1 ? parts.slice(1).join(".") : parts[0]).replace(
    /_/g,
    " ",
  );
  return raw.replace(/\b\w/g, (c) => c.toUpperCase());
}

// Locale connectors for fmtEntities. `last` is the two-item connector
// ("A and B"); `oxford` is the Oxford-comma form for 3+ items (", and ").
// Missing locales fall back to English.
const _LIST_CONNECTORS = {
  en: { last: " and ", oxford: ", and " },
  fr: { last: " et ", oxford: " et " },
  de: { last: " und ", oxford: " und " },
  es: { last: " y ", oxford: " y " },
  it: { last: " e ", oxford: " e " },
  nl: { last: " en ", oxford: " en " },
  hu: { last: " és ", oxford: " és " },
};

function _langKey(language, table) {
  const base = String(language || "en")
    .toLowerCase()
    .split("-")[0];
  return table[base] ? base : "en";
}

/** @param {{ states?: Object }} hass @param {string|string[]} val @param {string} [language] @returns {string} comma-separated friendly names with locale-aware connector */
export function fmtEntities(hass, val, language) {
  if (!val) return "";
  const arr = Array.isArray(val) ? val : [val];
  if (arr.length === 1) return fmtEntity(hass, arr[0]);
  const c = _LIST_CONNECTORS[_langKey(language, _LIST_CONNECTORS)];
  if (arr.length === 2)
    return `${fmtEntity(hass, arr[0])}${c.last}${fmtEntity(hass, arr[1])}`;
  return (
    arr
      .slice(0, -1)
      .map((e) => fmtEntity(hass, e))
      .join(", ") +
    c.oxford +
    fmtEntity(hass, arr[arr.length - 1])
  );
}

// Per-locale state-name tables. Falls back to humanized token then
// raw value when a state isn't listed (e.g. integration-specific
// custom states). Adding a locale: copy the EN block.
const _STATE_NAMES = {
  en: {
    on: "on",
    off: "off",
    home: "home",
    not_home: "away",
    open: "open",
    closed: "closed",
    locked: "locked",
    unlocked: "unlocked",
    playing: "playing",
    paused: "paused",
    idle: "idle",
    unavailable: "unavailable",
    unknown: "unknown",
  },
  fr: {
    on: "allumé",
    off: "éteint",
    home: "à la maison",
    not_home: "absent",
    open: "ouvert",
    closed: "fermé",
    locked: "verrouillé",
    unlocked: "déverrouillé",
    playing: "en lecture",
    paused: "en pause",
    idle: "inactif",
    unavailable: "indisponible",
    unknown: "inconnu",
  },
  de: {
    on: "eingeschaltet",
    off: "ausgeschaltet",
    home: "zu Hause",
    not_home: "abwesend",
    open: "offen",
    closed: "geschlossen",
    locked: "verriegelt",
    unlocked: "entriegelt",
    playing: "wiedergegeben",
    paused: "pausiert",
    idle: "inaktiv",
    unavailable: "nicht verfügbar",
    unknown: "unbekannt",
  },
  es: {
    on: "encendido",
    off: "apagado",
    home: "en casa",
    not_home: "fuera",
    open: "abierto",
    closed: "cerrado",
    locked: "bloqueado",
    unlocked: "desbloqueado",
    playing: "en reproducción",
    paused: "en pausa",
    idle: "inactivo",
    unavailable: "no disponible",
    unknown: "desconocido",
  },
  it: {
    on: "acceso",
    off: "spento",
    home: "a casa",
    not_home: "fuori",
    open: "aperto",
    closed: "chiuso",
    locked: "bloccato",
    unlocked: "sbloccato",
    playing: "in riproduzione",
    paused: "in pausa",
    idle: "inattivo",
    unavailable: "non disponibile",
    unknown: "sconosciuto",
  },
  nl: {
    on: "aan",
    off: "uit",
    home: "thuis",
    not_home: "afwezig",
    open: "open",
    closed: "gesloten",
    locked: "vergrendeld",
    unlocked: "ontgrendeld",
    playing: "aan het afspelen",
    paused: "gepauzeerd",
    idle: "inactief",
    unavailable: "niet beschikbaar",
    unknown: "onbekend",
  },
  hu: {
    on: "bekapcsolva",
    off: "kikapcsolva",
    home: "otthon",
    not_home: "távol",
    open: "nyitva",
    closed: "zárva",
    locked: "zárolva",
    unlocked: "feloldva",
    playing: "lejátszás alatt",
    paused: "szüneteltetve",
    idle: "tétlen",
    unavailable: "nem elérhető",
    unknown: "ismeretlen",
  },
};

/** @param {string|null} state @param {string} [language] @returns {string|null} human-friendly state name */
export function fmtState(state, language) {
  if (state == null) return null;
  const s = String(state);
  const table = _STATE_NAMES[_langKey(language, _STATE_NAMES)];
  return table[s] || s.replace(/_/g, " ");
}

/** @param {string|{hours?:number,minutes?:number,seconds?:number}|*} value @returns {string} e.g. "1h 30m" */
export function fmtDuration(value) {
  if (!value) return "";
  if (typeof value === "string") return value;
  if (typeof value !== "object") return String(value);
  const parts = [
    value.hours ? `${value.hours}h` : "",
    value.minutes ? `${value.minutes}m` : "",
    value.seconds ? `${value.seconds}s` : "",
  ].filter(Boolean);
  if (parts.length) return parts.join(" ");
  return String(value);
}

// Per-locale weekday abbreviations. Keep short — these render in
// inline flow descriptions so a 3-char abbrev keeps the chip tight.
const _WEEKDAYS = {
  en: {
    mon: "Mon",
    tue: "Tue",
    wed: "Wed",
    thu: "Thu",
    fri: "Fri",
    sat: "Sat",
    sun: "Sun",
  },
  fr: {
    mon: "lun",
    tue: "mar",
    wed: "mer",
    thu: "jeu",
    fri: "ven",
    sat: "sam",
    sun: "dim",
  },
  de: {
    mon: "Mo",
    tue: "Di",
    wed: "Mi",
    thu: "Do",
    fri: "Fr",
    sat: "Sa",
    sun: "So",
  },
  es: {
    mon: "lun",
    tue: "mar",
    wed: "mié",
    thu: "jue",
    fri: "vie",
    sat: "sáb",
    sun: "dom",
  },
  it: {
    mon: "lun",
    tue: "mar",
    wed: "mer",
    thu: "gio",
    fri: "ven",
    sat: "sab",
    sun: "dom",
  },
  nl: {
    mon: "ma",
    tue: "di",
    wed: "wo",
    thu: "do",
    fri: "vr",
    sat: "za",
    sun: "zo",
  },
  hu: {
    mon: "h",
    tue: "k",
    wed: "sze",
    thu: "cs",
    fri: "p",
    sat: "szo",
    sun: "v",
  },
};

/** @param {string|string[]} value - e.g. ["mon","tue"] @param {string} [language] @returns {string} e.g. "Mon, Tue" */
export function fmtWeekdays(value, language) {
  if (!value) return "";
  const dayMap = _WEEKDAYS[_langKey(language, _WEEKDAYS)];
  const days = Array.isArray(value) ? value : [value];
  return days.map((d) => dayMap[String(d)] || humanizeToken(d)).join(", ");
}

/** @param {string} entityId @param {*} value @returns {string} value with % appended for battery entities */
export function fmtNumericValue(entityId, value) {
  if (value == null || value === "") return "";
  const raw = String(value).trim();
  const batteryLike = String(entityId || "")
    .toLowerCase()
    .includes("battery");
  if (batteryLike && /^-?\d+(\.\d+)?$/.test(raw) && !raw.includes("%")) {
    return `${raw}%`;
  }
  return raw;
}

/** @param {{ states?: Object }} hass @param {*} val - HH:MM, seconds, Jinja template, or entity ref @returns {string} 12-hour formatted time */
export function fmtTime(hass, val) {
  if (val == null) return String(val);
  const s = String(val).trim();
  // Jinja template — extract entity and show friendly name
  if (s.includes("{{") || s.includes("{%")) {
    const m = s.match(/states\(['"]([^'"]+)['"]\)/);
    if (m) return fmtEntity(hass, m[1]);
    const m2 = s.match(/state_attr\(['"]([^'"]+)['"]/);
    if (m2) return fmtEntity(hass, m2[1]);
    return "a calculated time";
  }
  // Handle raw seconds (e.g. 43200 => "12:00")
  const num = Number(s);
  if (!isNaN(num) && num >= 0 && num <= 86400 && !s.includes(":")) {
    const h = Math.floor(num / 3600);
    const m = Math.floor((num % 3600) / 60);
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
  }
  // Handle HH:MM:SS or HH:MM (e.g. "23:30:00" => "23:30")
  const parts = s.split(":");
  if (parts.length >= 2) {
    const h = parseInt(parts[0], 10);
    const m = parseInt(parts[1], 10);
    if (!isNaN(h) && !isNaN(m)) {
      return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
    }
  }
  // Entity reference like input_datetime.xxx
  if (s.startsWith("input_datetime.") || s.startsWith("sensor."))
    return fmtEntity(hass, s);
  return s;
}
