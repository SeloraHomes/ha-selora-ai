// ---------------------------------------------------------------------------
// Shared formatting utilities
// ---------------------------------------------------------------------------
// Pure functions — pass `hass` explicitly where needed.
// Both panel.js and card.js import from here to avoid duplication.
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

/** @param {{ states?: Object }} hass @param {string|string[]} val @returns {string} comma-separated friendly names with Oxford comma */
export function fmtEntities(hass, val) {
  if (!val) return "";
  const arr = Array.isArray(val) ? val : [val];
  if (arr.length === 1) return fmtEntity(hass, arr[0]);
  if (arr.length === 2)
    return `${fmtEntity(hass, arr[0])} and ${fmtEntity(hass, arr[1])}`;
  return (
    arr
      .slice(0, -1)
      .map((e) => fmtEntity(hass, e))
      .join(", ") +
    ", and " +
    fmtEntity(hass, arr[arr.length - 1])
  );
}

/** @param {string|null} state @returns {string|null} human-friendly state name */
export function fmtState(state) {
  if (state == null) return null;
  const s = String(state);
  const friendly = {
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
  };
  return friendly[s] || s.replace(/_/g, " ");
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

/** @param {string|string[]} value - e.g. ["mon","tue"] @returns {string} e.g. "Mon, Tue" */
export function fmtWeekdays(value) {
  if (!value) return "";
  const dayMap = {
    mon: "Mon",
    tue: "Tue",
    wed: "Wed",
    thu: "Thu",
    fri: "Fri",
    sat: "Sat",
    sun: "Sun",
  };
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
  // Handle raw seconds (e.g. 43200 => "12:00 PM")
  const num = Number(s);
  if (!isNaN(num) && num >= 0 && num <= 86400 && !s.includes(":")) {
    const h = Math.floor(num / 3600);
    const m = Math.floor((num % 3600) / 60);
    const ampm = h >= 12 ? "PM" : "AM";
    const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
    return `${h12}:${String(m).padStart(2, "0")} ${ampm}`;
  }
  // Handle HH:MM:SS or HH:MM (e.g. "12:00:00" => "12:00 PM")
  const parts = s.split(":");
  if (parts.length >= 2) {
    const h = parseInt(parts[0], 10);
    const m = parseInt(parts[1], 10);
    if (!isNaN(h) && !isNaN(m)) {
      const ampm = h >= 12 ? "PM" : "AM";
      const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
      return `${h12}:${String(m).padStart(2, "0")} ${ampm}`;
    }
  }
  // Entity reference like input_datetime.xxx
  if (s.startsWith("input_datetime.") || s.startsWith("sensor."))
    return fmtEntity(hass, s);
  return s;
}
