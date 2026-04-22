// ---------------------------------------------------------------------------
// Unified flow item description (triggers, conditions, actions)
// ---------------------------------------------------------------------------
// Replaces panel's _describeFlowItem and card's _formatTrigger/_formatAction
// with a single shared implementation.
// ---------------------------------------------------------------------------

import {
  humanizeToken,
  fmtEntity,
  fmtEntities,
  fmtState,
  fmtDuration,
  fmtWeekdays,
  fmtNumericValue,
  fmtTime,
} from "./formatting.js";

/**
 * Describe a single HA flow item (trigger, condition, or action) as a human-readable string.
 *
 * Dispatches on three sections:
 *   1. Triggers — keyed on `item.platform` or `item.trigger` (e.g. "time", "state", "sun")
 *   2. Conditions — keyed on `item.condition` (e.g. "state", "time", "sun")
 *   3. Actions — keyed on `item.service` or `item.action`, then structural keys (delay, choose, etc.)
 *
 * Falls back to a readable summary of the item's keys if none of the above match.
 *
 * @param {{ states?: Object }} hass - Home Assistant instance for entity name lookups
 * @param {Object} item - trigger, condition, or action object from an automation
 * @returns {string} human-readable description
 */
export function describeFlowItem(hass, item) {
  if (!item || typeof item !== "object") return String(item ?? "");

  // HA supports both 'platform' (classic) and 'trigger' (new format) keys on trigger objects
  const p = item.platform || item.trigger;

  // ── Triggers ──────────────────────────────────────────────────────────────
  if (p === "time") {
    const raw = item.at;
    if (Array.isArray(raw)) {
      return `When the time is ${raw.map((t) => fmtTime(hass, t)).join(" or ")}`;
    }
    return `When the time is ${fmtTime(hass, raw)}`;
  }
  if (p === "sun") {
    const ev =
      item.event === "sunset"
        ? "sunset"
        : item.event === "sunrise"
          ? "sunrise"
          : humanizeToken(item.event || "sun event").toLowerCase();
    if (item.offset) {
      const neg = item.offset.startsWith("-");
      const raw = neg ? item.offset.slice(1) : item.offset;
      const [h, m, s] = raw.split(":").map(Number);
      const parts = [];
      if (h) parts.push(`${h}h`);
      if (m) parts.push(`${m}min`);
      if (s) parts.push(`${s}s`);
      const label = parts.join(" ") || item.offset;
      return `${label} ${neg ? "before" : "after"} ${ev}`;
    }
    return `When it is ${ev}`;
  }
  if (p === "state") {
    const eid = fmtEntities(hass, item.entity_id);
    const fromState = fmtState(item.from);
    const toState = fmtState(item.to);
    const duration = fmtDuration(item.for);
    const dur = duration ? ` for ${duration}` : "";
    if (toState === "on") return `When ${eid} turns on${dur}`;
    if (toState === "off") return `When ${eid} turns off${dur}`;
    if (toState && fromState)
      return `When ${eid} changes from ${fromState} to ${toState}${dur}`;
    if (toState) return `When ${eid} becomes ${toState}${dur}`;
    return `When ${eid} changes state${dur}`;
  }
  if (p === "numeric_state") {
    const eid = fmtEntities(hass, item.entity_id);
    const above = fmtNumericValue(item.entity_id, item.above);
    const below = fmtNumericValue(item.entity_id, item.below);
    if (item.above != null && item.below != null)
      return `When ${eid} is between ${above} and ${below}`;
    if (item.above != null) return `When ${eid} rises above ${above}`;
    if (item.below != null) return `When ${eid} drops below ${below}`;
    return `When ${eid} value changes`;
  }
  if (p === "homeassistant") {
    const ev =
      item.event === "start"
        ? "starts"
        : item.event === "shutdown"
          ? "shuts down"
          : "changes state";
    return `When Home Assistant ${ev}`;
  }
  if (p === "time_pattern") {
    if (item.seconds != null)
      return `Every ${item.seconds} second${Number(item.seconds) === 1 ? "" : "s"}`;
    if (item.minutes != null)
      return `Every ${item.minutes} minute${Number(item.minutes) === 1 ? "" : "s"}`;
    if (item.hours != null)
      return `Every ${item.hours} hour${Number(item.hours) === 1 ? "" : "s"}`;
    return "On a time pattern";
  }
  if (p === "template") {
    const tmpl = item.value_template || "";
    const entityMatch = tmpl.match(/states\(['"]([^'"]+)['"]\)/);
    if (entityMatch)
      return `When ${fmtEntity(hass, entityMatch[1])} condition is met`;
    return "When a template condition is met";
  }
  if (p === "event") {
    const name = item.event_type
      ? humanizeToken(item.event_type).toLowerCase()
      : "an event";
    return `When ${name} happens`;
  }
  if (p === "device") {
    const triggerType = item.type
      ? humanizeToken(item.type).toLowerCase()
      : "triggered";
    return item.device_id
      ? `When a device ${triggerType}`
      : `When a device is ${triggerType}`;
  }
  if (p === "zone") {
    const eid = fmtEntities(hass, item.entity_id);
    const zone = fmtEntity(hass, item.zone);
    const eventMap = {
      enter: "enters",
      leave: "leaves",
    };
    const rawEvent = String(item.event || "enter");
    const ev = eventMap[rawEvent] || humanizeToken(rawEvent).toLowerCase();
    return `${eid} ${ev} ${zone}`.trim();
  }
  if (p === "mqtt")
    return item.topic
      ? `When a device message arrives (${item.topic})`
      : "When a device message arrives";
  if (p === "webhook") return "When an outside service sends an update";
  if (p === "tag")
    return `When a tag is scanned${item.tag_id ? ` (${item.tag_id})` : ""}`;
  if (p === "geo_location") return "When a location update is received";
  if (p === "calendar") {
    const eventName = item.event
      ? humanizeToken(item.event).toLowerCase()
      : "event";
    const entity = item.entity_id
      ? ` on ${fmtEntity(hass, item.entity_id)}`
      : "";
    return `When a calendar ${eventName} begins${entity}`;
  }
  if (p) return "When this trigger happens";

  // ── Conditions (use 'condition' key) ──────────────────────────────────────
  const cond = item.condition;
  if (cond === "state") {
    const eid = fmtEntities(hass, item.entity_id);
    const st = fmtState(item.state ?? item.to);
    return `${eid} is ${st}`;
  }
  if (cond === "numeric_state") {
    const eid = fmtEntities(hass, item.entity_id);
    if (item.above != null && item.below != null)
      return `${eid} between ${item.above} and ${item.below}`;
    if (item.above != null) return `${eid} above ${item.above}`;
    if (item.below != null) return `${eid} below ${item.below}`;
    return `${eid} numeric check`;
  }
  if (cond === "time") {
    const parts = [];
    if (item.after) parts.push(`after ${fmtTime(hass, item.after)}`);
    if (item.before) parts.push(`before ${fmtTime(hass, item.before)}`);
    if (item.weekday) {
      parts.push(`on ${fmtWeekdays(item.weekday)}`);
    }
    return parts.length ? parts.join(" · ") : "Time window";
  }
  if (cond === "template") return "Template evaluates to true";
  if (cond === "sun") {
    const parts = [];
    if (item.after)
      parts.push(`after ${String(item.after).replace(/_/g, " ")}`);
    if (item.before)
      parts.push(`before ${String(item.before).replace(/_/g, " ")}`);
    return parts.join(", ") || "Sun position";
  }
  if (cond === "and")
    return `All ${(item.conditions || []).length} conditions must be true`;
  if (cond === "or")
    return `Any of ${(item.conditions || []).length} conditions is true`;
  if (cond === "not") return "None of the conditions are true";
  if (cond === "zone") {
    const eid = fmtEntities(hass, item.entity_id);
    return `${eid} is in ${fmtEntity(hass, item.zone) || "zone"}`;
  }
  if (cond === "device")
    return item.type
      ? String(item.type).replace(/_/g, " ")
      : "Device condition";
  if (cond) return String(cond).replace(/_/g, " ");

  // ── Actions ───────────────────────────────────────────────────────────────
  const svc = item.service || item.action;
  if (svc) {
    const svcStr = String(svc);
    const [domain = "", svcName = svc] = svcStr.split(".");

    // Special handling for notification services
    if (
      svcStr === "notify.persistent_notification" ||
      domain === "persistent_notification"
    ) {
      const title = item.data?.title;
      const msg = item.data?.message;
      if (title && msg) return `Notify: "${title}"`;
      if (title) return `Notify: "${title}"`;
      if (msg) {
        const short = msg.length > 60 ? msg.slice(0, 57) + "\u2026" : msg;
        return `Notify: "${short}"`;
      }
      return "Send a notification";
    }
    if (domain === "notify") {
      const target = svcName
        .replace(/_/g, " ")
        .replace(/\b\w/g, (c) => c.toUpperCase());
      const msg = item.data?.message;
      const title = item.data?.title;
      if (title) return `Notify ${target}: "${title}"`;
      if (msg) {
        const short = msg.length > 50 ? msg.slice(0, 47) + "\u2026" : msg;
        return `Notify ${target}: "${short}"`;
      }
      return `Notify via ${target}`;
    }
    if (domain === "tts") {
      const msg = item.data?.message;
      if (msg) {
        const short = msg.length > 50 ? msg.slice(0, 47) + "\u2026" : msg;
        return `Say: "${short}"`;
      }
      return "Text-to-speech";
    }

    const friendlyActions = {
      turn_on: "Turn on",
      turn_off: "Turn off",
      toggle: "Toggle",
      lock: "Lock",
      unlock: "Unlock",
      open_cover: "Open",
      close_cover: "Close",
      set_temperature: "Set temperature for",
      set_value: "Set value for",
      send_command: "Send command to",
      reload: "Reload",
    };
    const name =
      friendlyActions[svcName] ||
      svcName.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
    const targets = item.target?.entity_id ?? item.data?.entity_id;
    const t = fmtEntities(hass, targets);
    const extras = [];
    if (item.data?.brightness_pct != null)
      extras.push(`at ${item.data.brightness_pct}%`);
    if (item.data?.temperature != null)
      extras.push(`to ${item.data.temperature}\u00b0`);
    if (item.data?.color_temp != null)
      extras.push(`color temp ${item.data.color_temp}`);
    // Don't show raw messages with Jinja templates — too noisy
    if (item.data?.message && !String(item.data.message).includes("{{")) {
      const short =
        item.data.message.length > 50
          ? item.data.message.slice(0, 47) + "\u2026"
          : item.data.message;
      extras.push(`"${short}"`);
    }
    if (item.data?.title && !String(item.data.title).includes("{{"))
      extras.push(item.data.title);
    const detail = extras.length ? ` (${extras.join(", ")})` : "";
    return t ? `${name} ${t}${detail}` : `${name}${detail}`;
  }
  if (item.delay) {
    const d = item.delay;
    if (typeof d === "string") return `Wait ${d}`;
    const parts = [];
    if (d.hours) parts.push(`${d.hours}h`);
    if (d.minutes) parts.push(`${d.minutes}m`);
    if (d.seconds) parts.push(`${d.seconds}s`);
    return parts.length ? `Wait ${parts.join(" ")}` : "Wait";
  }
  if (item.wait_template) return "Wait until condition is met";
  if (item.wait_for_trigger) return "Wait for a trigger";
  if (item.scene) return `Activate scene: ${fmtEntity(hass, item.scene)}`;
  if (item.choose)
    return `Choose between ${item.choose.length} option${item.choose.length !== 1 ? "s" : ""}`;
  if (item.repeat) {
    const r = item.repeat;
    if (r.count != null)
      return `Repeat ${r.count} time${r.count !== 1 ? "s" : ""}`;
    if (r.while) return "Repeat while condition holds";
    if (r.until) return "Repeat until condition is met";
    return "Repeat";
  }
  if (item.parallel)
    return `Run ${(item.parallel || []).length} actions in parallel`;
  if (item.sequence)
    return `Run a sequence of ${(item.sequence || []).length} steps`;
  if (item.variables) return "Set variables";
  if (item.stop) return `Stop: ${item.stop}`;
  if (item.event) return `Fire event: ${String(item.event).replace(/_/g, " ")}`;

  // ── Human-readable fallback — never show raw JSON or Jinja ────────────────
  const SKIP = new Set(["id", "enabled", "mode", "alias", "description"]);
  const readable = Object.entries(item)
    .filter(([k, v]) => !SKIP.has(k) && v != null && v !== "")
    .map(([k, v]) => {
      const label = k.replace(/_/g, " ");
      const strVal =
        typeof v === "string"
          ? v
          : Array.isArray(v)
            ? v.map((x) => (typeof x === "object" ? "\u2026" : x)).join(", ")
            : String(v);
      // Hide Jinja templates
      if (strVal.includes("{{") || strVal.includes("{%")) return null;
      return `${label}: ${strVal}`;
    })
    .filter(Boolean)
    .slice(0, 3);
  return readable.length ? readable.join(" \u00b7 ") : "Automation step";
}
