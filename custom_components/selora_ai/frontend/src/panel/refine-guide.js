// Guidance while the user refines a loaded automation: which refinement is
// active, suggested edits derived from the automation itself, and prefilling
// the composer from a suggestion or a clicked flowchart step.
import { html } from "lit";
import { interpolate } from "../shared/i18n.js";
import { asArray, normalizeCondition } from "../shared/flow-description.js";
import { fmtDuration, fmtTime } from "../shared/formatting.js";

// Statuses that end a refinement when met walking back from the newest
// message. Same terminators as `_find_refining_automation_id` in the backend,
// which decides whether the next turn is sent as an edit.
export const REFINEMENT_TERMINATORS = new Set(["pending", "saved", "declined"]);

const MAX_SUGGESTIONS = 4;

// The refinement still in effect: the newest `refining` message with no
// terminator after it, or null. The first proposal ends it — that card now
// carries the target — so this covers the "what should I change?" step.
export function activeRefinement(messages) {
  const list = messages || [];
  for (let i = list.length - 1; i >= 0; i--) {
    const m = list[i] || {};
    const status = m.automation_status;
    if (REFINEMENT_TERMINATORS.has(status)) return null;
    if (status === "refining" && m.automation_yaml) return { index: i };
  }
  return null;
}

// Every leaf trigger / condition / action that runs, through and/or/not
// groups and if / choose / parallel / sequence / repeat blocks. A step with
// `enabled: false` is skipped with everything under it: HA never runs it, so
// it must neither be offered as an edit nor hide an addition it would cover.
const _disabled = (node) => node?.enabled === false;

function _leaves(automation) {
  const out = [];
  const visitCondition = (raw) => {
    const c = normalizeCondition(raw);
    if (!c || typeof c !== "object" || _disabled(c)) return;
    if (["and", "or", "not"].includes(c.condition)) {
      asArray(c.conditions).forEach(visitCondition);
      return;
    }
    out.push({ kind: "condition", item: c });
  };
  const visitAction = (a) => {
    if (!a || typeof a !== "object" || _disabled(a)) return;
    if (a.if != null || a.choose != null || a.parallel != null) {
      asArray(a.if).forEach(visitCondition);
      asArray(a.then).forEach(visitAction);
      asArray(a.else).forEach(visitAction);
      for (const option of asArray(a.choose)) {
        asArray(option?.conditions).forEach(visitCondition);
        asArray(option?.sequence).forEach(visitAction);
      }
      asArray(a.default).forEach(visitAction);
      asArray(a.parallel).forEach(visitAction);
      return;
    }
    if (a.sequence != null) return asArray(a.sequence).forEach(visitAction);
    if (a.repeat != null) {
      return asArray(a.repeat?.sequence).forEach(visitAction);
    }
    out.push({ kind: "action", item: a });
  };
  asArray(automation?.triggers ?? automation?.trigger).forEach((t) => {
    if (t && typeof t === "object" && !_disabled(t)) {
      out.push({ kind: "trigger", item: t });
    }
  });
  asArray(automation?.conditions ?? automation?.condition).forEach(
    visitCondition,
  );
  asArray(automation?.actions ?? automation?.action).forEach(visitAction);
  return out;
}

const _triggerType = (t) => t.trigger ?? t.platform;
const _service = (a) => String(a.action ?? a.service ?? "");
const _isNumber = (v) => typeof v === "number" || /^-?\d+(\.\d+)?$/.test(v);

// Suggested edits for this automation, most specific first: the values it
// already uses (a threshold, a delay, a time, a brightness), then common
// additions it lacks. Deterministic, so they appear instantly and never
// propose something the automation does not have.
export function refineSuggestions(host, automation) {
  const leaves = _leaves(automation);
  const t = (key, fallback, values) =>
    interpolate(host._t(key, fallback), values || {});
  const out = [];
  const add = (icon, label, prefill) => out.push({ icon, label, prefill });

  const threshold = leaves.find(
    ({ item }) =>
      (_triggerType(item) === "numeric_state" ||
        item.condition === "numeric_state") &&
      (_isNumber(item.below) || _isNumber(item.above)),
  );
  if (threshold) {
    const value = String(threshold.item.below ?? threshold.item.above);
    add(
      "mdi:tune-vertical",
      t("refine_suggest_threshold", "Change the threshold ({value})", {
        value,
      }),
      t("refine_prefill_threshold", "Change the threshold from {value} to", {
        value,
      }),
    );
  }

  const delayed = leaves.find(
    ({ kind, item }) =>
      (kind !== "action" && item.for != null) ||
      (kind === "action" && item.delay != null),
  );
  const delay = delayed && fmtDuration(delayed.item.for ?? delayed.item.delay);
  // A templated delay has no value worth quoting back.
  if (delay && !delay.includes("{")) {
    add(
      "mdi:timer-outline",
      t("refine_suggest_delay", "Change the delay ({value})", {
        value: delay,
      }),
      t("refine_prefill_delay", "Change the delay from {value} to", {
        value: delay,
      }),
    );
  }

  const timed = leaves.find(
    ({ kind, item }) =>
      kind === "trigger" &&
      _triggerType(item) === "time" &&
      typeof item.at === "string" &&
      // Not an input_datetime / sensor reference or a template.
      !/[.{]/.test(item.at),
  );
  if (timed) {
    const value = fmtTime(host.hass, timed.item.at);
    add(
      "mdi:clock-outline",
      t("refine_suggest_time", "Change the time ({value})", { value }),
      t("refine_prefill_time", "Change the time from {value} to", { value }),
    );
  }

  const dimmed = leaves.find(
    ({ kind, item }) =>
      kind === "action" &&
      _isNumber((item.data ?? item.service_data)?.brightness_pct),
  );
  if (dimmed) {
    const value = String(
      (dimmed.item.data ?? dimmed.item.service_data).brightness_pct,
    );
    add(
      "mdi:brightness-6",
      t("refine_suggest_brightness", "Change the brightness ({value}%)", {
        value,
      }),
      t("refine_prefill_brightness", "Change the brightness from {value}% to", {
        value,
      }),
    );
  }

  const hasTimeWindow = leaves.some(
    ({ kind, item }) =>
      (kind === "condition" && item.condition === "time") ||
      (kind === "trigger" && _triggerType(item) === "time"),
  );
  if (!hasTimeWindow) {
    add(
      "mdi:calendar-clock",
      t("refine_suggest_hours", "Only between certain hours"),
      t("refine_prefill_hours", "Only run it between"),
    );
  }

  const notifies = leaves.some(
    ({ kind, item }) =>
      kind === "action" &&
      (_service(item).startsWith("notify.") ||
        _service(item) === "persistent_notification.create"),
  );
  if (!notifies) {
    add(
      "mdi:bell-outline",
      t("refine_suggest_notify", "Also notify me"),
      t("refine_prefill_notify", "Also send me a notification when it runs"),
    );
  }

  return out.slice(0, MAX_SUGGESTIONS);
}

// What clicking a flowchart step puts in the composer.
export function stepPrefill(host, description) {
  return interpolate(host._t("refine_prefill_step", 'Change "{step}" to'), {
    step: description,
  });
}

// Put *text* in the composer with the caret at the end, ready for the user to
// finish the sentence. Never sends: an edit almost always needs a value.
export async function prefillComposer(host, text) {
  host._input = `${text} `;
  host.requestUpdate?.();
  await host.updateComplete;
  const ta = host.shadowRoot?.querySelector(".composer-textarea");
  if (!ta) return;
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);
}

// A card of its own above the loaded automation's bubble: what is being
// edited, how to go about it, and that nothing is written yet. Only for the
// message the active refinement points at.
export function renderRefineHeading(host, msgIndex) {
  if (activeRefinement(host._messages)?.index !== msgIndex) return "";
  const alias = host._messages[msgIndex]?.automation?.alias || "";
  return html`
    <div class="refine-heading">
      <div class="refine-heading-icon">
        <ha-icon icon="mdi:pencil-outline"></ha-icon>
      </div>
      <div class="refine-heading-text">
        <div class="refine-heading-title">
          ${interpolate(host._t("refine_heading", "Editing {name}"), {
            name: alias,
          })}
        </div>
        <div class="refine-heading-hint">
          ${host._t(
            "refine_heading_hint",
            "Describe what to change below, or click any step. Nothing changes until you accept the new version.",
          )}
        </div>
      </div>
    </div>
  `;
}

// The suggestion row above the composer. Shown only while the refinement is
// waiting on its first description — like the composer glow, it gives way as
// soon as the user types.
export function renderRefineSuggestions(host) {
  if (host._input || host._loading || host._streaming) return "";
  const active = activeRefinement(host._messages);
  if (!active) return "";
  const automation = host._messages[active.index]?.automation;
  const suggestions = refineSuggestions(host, automation);
  if (!suggestions.length) return "";
  return html`
    <div class="chat-quick-actions refine-suggestions">
      <div class="qa-group">
        ${suggestions.map(
          (s) => html`
            <button
              class="qa-suggestion"
              @click=${() => prefillComposer(host, s.prefill)}
            >
              <span class="qa-glow-track" aria-hidden="true">
                <span class="qa-glow-spot"></span>
              </span>
              <ha-icon class="qa-suggestion-lead" icon=${s.icon}></ha-icon>
              <span class="qa-suggestion-label">${s.label}</span>
            </button>
          `,
        )}
      </div>
    </div>
  `;
}
