import { describeFlowItem } from "../shared/flow-description.js";
import { fmtEntity } from "../shared/formatting.js";
import { interpolate, pickLocale } from "../shared/i18n.js";

// One sentence saying what a stored version changed.
//
// The version's `message` says where a change came FROM ("Refined via chat"),
// the same line on every refinement. What changed is worked out by the backend
// when the version is saved and stored on it as `changes`
// (`automation_changes.py`); this only phrases that list, in the viewer's
// language, which is the one part that cannot be stored.
//
// It names the change itself — "action changed from “Wait 5 minutes” to “Wait
// 2 minutes”" — rather than which sections moved, which is true of every
// refinement and so says nothing.

const SECTION_FALLBACK = {
  triggers: { one: "trigger", part: "triggers" },
  conditions: { one: "condition", part: "conditions" },
  actions: { one: "action", part: "actions" },
};

// A clause names one change; past this the rest is "other changes" — a
// sentence listing seven edits is the YAML diff again, worse.
const MAX_CLAUSES = 3;
const MAX_ITEM_CHARS = 90;
const MAX_VALUE_CHARS = 40;
const TIME_UNITS = new Set(["hours", "minutes", "seconds", "milliseconds"]);
const ENTITY_ID_RE = /^[a-z_]+\.[a-z0-9_]+$/;

function clip(text, max) {
  const s = String(text ?? "").trim();
  return s.length > max ? `${s.slice(0, max - 1).trimEnd()}…` : s;
}

function humanize(key) {
  return String(key).replace(/_/g, " ");
}

function joinList(hass, parts) {
  try {
    return new Intl.ListFormat(pickLocale(hass), {
      style: "long",
      type: "conjunction",
    }).format(parts);
  } catch {
    return parts.join(", ");
  }
}

function capitalize(s) {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

function fmtValue(hass, value) {
  if (Array.isArray(value)) {
    return clip(
      value.map((v) => fmtValue(hass, v)).join(", "),
      MAX_VALUE_CHARS,
    );
  }
  if (value && typeof value === "object") {
    return clip(JSON.stringify(value), MAX_VALUE_CHARS);
  }
  const s = String(value ?? "");
  // An entity id reads as its name, the way the Flow tab shows it.
  if (ENTITY_ID_RE.test(s) && hass?.states?.[s]) return fmtEntity(hass, s);
  return clip(s, MAX_VALUE_CHARS);
}

// "delay (minutes)" for a duration part, otherwise the key itself: the path
// above the leaf is mostly containers (`data`, `target`, list indices) that
// mean nothing to someone reading the history.
function leafLabel(path) {
  const keys = (path || []).filter((p) => typeof p === "string");
  const last = keys[keys.length - 1];
  if (!last) return "";
  const parent = keys[keys.length - 2];
  if (TIME_UNITS.has(last) && parent) return `${humanize(parent)} (${last})`;
  return humanize(last);
}

function valueClause(t, hass, field, before, after) {
  if (after === undefined || after === null) {
    return interpolate(t("version_summary_value_removed", "{field} removed"), {
      field,
    });
  }
  if (before === undefined || before === null) {
    return interpolate(t("version_summary_value_set", "{field} set to {to}"), {
      field,
      to: fmtValue(hass, after),
    });
  }
  return interpolate(
    t("version_summary_value_changed", "{field} changed from {from} to {to}"),
    { field, from: fmtValue(hass, before), to: fmtValue(hass, after) },
  );
}

/**
 * Phrase a version's stored `changes` as one sentence.
 *
 * @param {object} host - the panel host (`hass`, `_t`)
 * @param {object[]|null|undefined} changes - the version's stored change list
 * @returns {string} one sentence, or "" when there is nothing to say
 */
export function phraseVersionChanges(host, changes) {
  if (!Array.isArray(changes) || !changes.length) return "";
  const hass = host.hass;
  const t = (key, fallback) =>
    typeof host._t === "function" ? host._t(key, fallback) : fallback;
  const sectionLabel = (section) =>
    t(
      `version_summary_section_${section}`,
      SECTION_FALLBACK[section]?.one || humanize(section),
    );
  // One character over the cap is kept so "too long to quote" stays visible.
  const describe = (item) =>
    clip(describeFlowItem(hass, item, {}), MAX_ITEM_CHARS + 1);

  const clauses = [];
  const pendingTwins = new Map();
  let descriptionChanged = false;
  for (const change of changes) {
    const section = change.section;
    switch (change.kind) {
      case "item_changed": {
        const from = change.before != null ? describe(change.before) : "";
        const to = change.after != null ? describe(change.after) : "";
        if (
          from &&
          to &&
          from !== to &&
          from.length <= MAX_ITEM_CHARS &&
          to.length <= MAX_ITEM_CHARS
        ) {
          clauses.push(
            interpolate(
              t(
                "version_summary_item_changed",
                "{section} changed from “{from}” to “{to}”",
              ),
              { section: sectionLabel(section), from, to },
            ),
          );
          break;
        }
        // The Flow wording does not show the field that changed (or the item
        // is too long to quote), so name the values themselves.
        const details = change.details || [];
        const count = change.detail_count ?? details.length;
        if (details.length && count <= details.length) {
          for (const d of details) {
            const label = leafLabel(d.path) || sectionLabel(section);
            // "Heatpump temperature", not a bare "temperature": the number
            // means nothing until it is attached to the device it sets.
            const field = d.entity
              ? interpolate(
                  t("version_summary_entity_field", "{entity} {field}"),
                  { entity: fmtEntity(hass, d.entity), field: label },
                )
              : label;
            clauses.push(valueClause(t, hass, field, d.before, d.after));
          }
          break;
        }
        clauses.push(
          interpolate(t("version_summary_item_updated", "{section} updated"), {
            section: sectionLabel(section),
          }),
        );
        break;
      }
      case "item_added":
      case "item_removed": {
        const added = change.kind === "item_added";
        const item = added ? change.after : change.before;
        const text =
          item != null
            ? clip(describeFlowItem(hass, item, {}), MAX_ITEM_CHARS)
            : "";
        // A removal and an addition that read the same are one item edited
        // in a way the wording does not show — "trigger removed: 07:00,
        // trigger added: 07:00" says nothing, "trigger updated" says that.
        const twinKey = `${section}|${added ? "removed" : "added"}|${text}`;
        const twin = text ? pendingTwins.get(twinKey) : undefined;
        if (twin !== undefined) {
          pendingTwins.delete(twinKey);
          clauses[twin] = interpolate(
            t("version_summary_item_updated", "{section} updated"),
            { section: sectionLabel(section) },
          );
          break;
        }
        if (text) {
          pendingTwins.set(
            `${section}|${added ? "added" : "removed"}|${text}`,
            clauses.length,
          );
        }
        if (item != null) {
          clauses.push(
            interpolate(
              added
                ? t("version_summary_item_added", "{section} added: “{item}”")
                : t(
                    "version_summary_item_removed",
                    "{section} removed: “{item}”",
                  ),
              {
                section: sectionLabel(section),
                item: text,
              },
            ),
          );
        } else {
          clauses.push(
            interpolate(
              added
                ? t("version_summary_added", "{section} added")
                : t("version_summary_removed", "{section} removed"),
              { section: sectionLabel(section) },
            ),
          );
        }
        break;
      }
      case "reordered":
        clauses.push(
          interpolate(t("version_summary_reordered", "{parts} reordered"), {
            parts: t(
              `version_summary_part_${section}`,
              SECTION_FALLBACK[section]?.part || humanize(section),
            ),
          }),
        );
        break;
      case "field_changed":
        if (change.field === "description") {
          descriptionChanged = true;
        } else if (change.field === "alias" && change.after) {
          clauses.push(
            interpolate(t("version_summary_renamed", "renamed to “{name}”"), {
              name: clip(change.after, MAX_ITEM_CHARS),
            }),
          );
        } else {
          const field =
            change.field === "mode"
              ? t("version_summary_field_mode", "run mode")
              : humanize(change.field);
          clauses.push(
            valueClause(t, hass, field, change.before, change.after),
          );
        }
        break;
      default:
        break;
    }
  }

  // The description narrates the automation, so beside a real change it
  // only restates it. It is the summary only when nothing else moved.
  if (!clauses.length && descriptionChanged) {
    clauses.push(
      t("version_summary_description_updated", "description updated"),
    );
  }
  if (!clauses.length) return "";
  const shown =
    clauses.length > MAX_CLAUSES
      ? [
          ...clauses.slice(0, MAX_CLAUSES - 1),
          t("version_summary_other_changes", "other changes"),
        ]
      : clauses;
  return capitalize(
    interpolate(t("version_summary_sentence", "{clauses}."), {
      clauses: joinList(hass, shown),
    }),
  );
}

// The phrased summary of one version, cached.
//
// The host re-renders on every state change in the home and the stored list
// never changes, so phrasing it on every render is wasted work. Values are
// worded with the home's current entity names, and a rename replaces the
// registry object HA hands the panel — so the cache is dropped whenever
// `hass.entities` or `hass.devices` is a different object, but not on
// `hass.states`, which is replaced on every state change.
export function versionSummary(host, automationId, version) {
  if (!version) return "";
  // The sentence the LLM wrote at save time says what the edit MEANS, which
  // the change list cannot. It is in the home's language, so a viewer reading
  // the panel in another one gets the change list phrased in theirs instead.
  if (
    version.summary &&
    String(version.summary_language || "").toLowerCase() ===
      pickLocale(host.hass).split("-")[0]
  ) {
    return version.summary;
  }
  const entities = host.hass?.entities;
  const devices = host.hass?.devices;
  const cache = host._versionSummaryCache;
  if (!cache || cache.entities !== entities || cache.devices !== devices) {
    host._versionSummaryCache = { entities, devices, summaries: new Map() };
  }
  const summaries = host._versionSummaryCache.summaries;
  const key = `${automationId}_${version.version_id}_${pickLocale(host.hass)}`;
  let summary = summaries.get(key);
  if (summary === undefined) {
    summary = phraseVersionChanges(host, version.changes);
    summaries.set(key, summary);
  }
  return summary;
}
