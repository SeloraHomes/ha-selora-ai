import { html } from "lit";
import {
  describeFlowItem,
  collectFlowEntityIds,
  collectFlowDeviceRefs,
  displayTriggers,
  asArray,
} from "../shared/flow-description.js";
import { fmtEntity } from "../shared/formatting.js";
import { formatTimeAgo } from "../shared/date-utils.js";
import { renderSuggestionsSection } from "./render-suggestions.js";
import { getStaleAutomations, staleTooltip } from "./stale-automations.js";
import { DOMAIN_ICONS } from "./render-chat.js";

// Click target for entities mentioned in a trigger/condition/action. Opens
// HA's built-in more-info dialog via the standard `hass-more-info` event
// so users can dive into the entity without leaving chat. Rendered as an
// inline link rather than a pill so prose like "Turn off Living Room Fan"
// reads as a sentence instead of a chip-stuffed UI fragment.
function renderFlowEntityLink(host, entityId) {
  const stateObj = host.hass?.states?.[entityId];
  const friendly = stateObj?.attributes?.friendly_name || entityId;
  const icon =
    stateObj?.attributes?.icon ||
    DOMAIN_ICONS[entityId.split(".")[0]] ||
    "mdi:circle-medium";
  return html`<button
    type="button"
    class="flow-entity-link"
    title=${`Open ${friendly} (${entityId})`}
    @click=${(e) => {
      e.stopPropagation();
      host.dispatchEvent(
        new CustomEvent("hass-more-info", {
          bubbles: true,
          composed: true,
          detail: { entityId },
        }),
      );
    }}
  >
    <ha-icon icon=${icon}></ha-icon><span>${friendly}</span>
  </button>`;
}

// Click target for a device referenced by a device trigger/condition.
// Devices have no more-info dialog, so this navigates to the device's
// config page the same way HA's own UI does — history.pushState plus a
// location-changed event so the SPA router picks it up without a reload.
function renderFlowDeviceLink(host, deviceId, name, domain) {
  const icon = (domain && DOMAIN_ICONS[domain]) || "mdi:devices";
  return html`<button
    type="button"
    class="flow-entity-link"
    title=${`Open ${name}`}
    @click=${(e) => {
      e.stopPropagation();
      window.history.pushState(null, "", `/config/devices/device/${deviceId}`);
      window.dispatchEvent(new Event("location-changed"));
    }}
  >
    <ha-icon icon=${icon}></ha-icon><span>${name}</span>
  </button>`;
}

// Match the compact durations that `fmtDuration` emits ("10m", "1h 30m",
// "45s", "1h 5m 30s"). Anchored on word boundary so "5m" inside a
// friendly name like "AM5m" can't accidentally chip itself.
const DURATION_RE =
  /\b(?:\d+\s*h(?:\s+\d+\s*m)?(?:\s+\d+\s*s)?|\d+\s*m(?:\s+\d+\s*s)?|\d+\s*s)\b/g;

function expandDurationAbbrev(s) {
  return s
    .replace(/(\d+)\s*h\b/g, "$1 hr")
    .replace(/(\d+)\s*m\b/g, "$1 min")
    .replace(/(\d+)\s*s\b/g, "$1 sec");
}

function renderFlowDuration(raw) {
  return html`<span class="flow-duration"
    ><ha-icon icon="mdi:clock-outline"></ha-icon>${expandDurationAbbrev(
      raw,
    )}</span
  >`;
}

// Split a plain text segment into a mix of strings and duration chips.
function splitDurations(text) {
  const out = [];
  let last = 0;
  for (const m of text.matchAll(DURATION_RE)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push({ duration: m[0] });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// Build a Lit template that inlines clickable entity chips at every spot
// where the description string mentions a referenced entity's friendly
// name. This avoids the "Turn off Decorative Lights [Decorative Lights]"
// duplication of an external chip row — each entity now appears exactly
// once, and it's interactive.
//
// Algorithm: longest-name-first greedy split. We scan the description
// for the earliest occurrence of any referenced entity's friendly name,
// emit the text leading up to it, then a chip, and repeat. If a name
// isn't found (rare — e.g. the LLM used an alias or the entity has no
// friendly_name and we fell back to a humanized object_id mismatch),
// we silently leave the plain text in place. This stays robust for
// every flow-item case `describeFlowItem` handles without us having to
// rewrite each case to template-style.
function renderFlowDescription(host, item, ctx) {
  const description = describeFlowItem(host.hass, item, ctx);
  if (!description) return html`${description}`;

  // Map each referenced name to its link target once, longest first so
  // multi-word names match before any single-word substrings they happen
  // to contain. Entities open the more-info dialog; devices (device
  // triggers/conditions have no entity_id) navigate to their config page.
  const lookups = [
    ...collectFlowEntityIds(item).map((eid) => ({
      name: fmtEntity(host.hass, eid),
      link: { entity: eid },
    })),
    ...collectFlowDeviceRefs(host.hass, item).map((d) => ({
      name: d.name,
      link: { device: d },
    })),
  ]
    .filter((l) => l.name)
    .sort((a, b) => b.name.length - a.name.length);

  // Pass 1: split description on referenced names → text + link segments.
  const segments = [];
  let remaining = description;
  // Cap iterations defensively — a pathological input could otherwise
  // loop if a match returned zero length, which shouldn't happen but
  // we'd rather degrade than freeze the render.
  let safety = 32;
  while (remaining && safety-- > 0) {
    let bestIdx = -1;
    let bestMatch = null;
    for (const l of lookups) {
      const idx = remaining.indexOf(l.name);
      if (idx >= 0 && (bestIdx === -1 || idx < bestIdx)) {
        bestIdx = idx;
        bestMatch = l;
      }
    }
    if (!bestMatch) {
      segments.push(remaining);
      break;
    }
    if (bestIdx > 0) segments.push(remaining.slice(0, bestIdx));
    segments.push({ link: bestMatch.link });
    remaining = remaining.slice(bestIdx + bestMatch.name.length);
  }
  if (remaining && safety <= 0) segments.push(remaining);

  // Pass 2: within each plain-text segment, lift compact durations
  // ("10m", "1h 30m") out as their own chips so "clear for 10 min" reads
  // as prose + a small badge instead of bare ASCII.
  const final = [];
  for (const seg of segments) {
    if (typeof seg !== "string") {
      final.push(seg);
      continue;
    }
    for (const piece of splitDurations(seg)) {
      final.push(piece);
    }
  }

  return html`${final.map((s) => {
    if (typeof s === "string") return s;
    if (s.link?.entity) return renderFlowEntityLink(host, s.link.entity);
    if (s.link?.device) {
      const d = s.link.device;
      return renderFlowDeviceLink(host, d.deviceId, d.name, d.domain);
    }
    if (s.duration) return renderFlowDuration(s.duration);
    return "";
  })}`;
}

function renderFlowNode(host, item, kind, ctx) {
  return html`<div class="flow-node ${kind}-node">
    ${renderFlowDescription(host, item, ctx)}
  </div>`;
}

// Render a condition, unwrapping logical groups so users see the actual
// checks instead of an opaque "All N conditions must be true" summary.
//
// `implicitAll` tracks whether the enclosing context already means "all of
// these must hold" — true for the top-level Condition section and each IF
// branch's condition list. An `and` group only renders flat when that
// holds; nested inside an `or` / `not` (implicitAll = false) it MUST keep
// an explicit "All of the following" group, or `(A and B) or C` would
// read as "any of A, B, C" and invert the logic. `or` / `not` always draw
// a labeled group, and their children are no longer in an all-of context.
function renderConditionItem(host, cond, ctx, implicitAll = true) {
  if (cond && typeof cond === "object") {
    const type = cond.condition;
    if (type === "and") {
      const children = asArray(cond.conditions).map((c) =>
        renderConditionItem(host, c, ctx, true),
      );
      if (implicitAll) return html`${children}`;
      return html`<div class="flow-branch">
        <div class="flow-branch-label">
          ${host._t("automations_flow_group_all_of", "All of the following:")}
        </div>
        ${children}
      </div>`;
    }
    if (type === "or" || type === "not") {
      const label =
        type === "or"
          ? host._t("automations_flow_group_any_of", "Any of the following:")
          : host._t("automations_flow_group_none_of", "None of the following:");
      return html`<div class="flow-branch">
        <div class="flow-branch-label">${label}</div>
        ${asArray(cond.conditions).map((c) =>
          renderConditionItem(host, c, ctx, false),
        )}
      </div>`;
    }
  }
  return renderFlowNode(host, cond, "condition", ctx);
}

// Expand control-flow action blocks (choose / parallel / sequence /
// repeat) inline instead of collapsing them to "Choose between 2
// options". The original collapsed form was lossless in the YAML but
// useless visually — the user had no way to verify the rule without
// reading raw YAML below. The expanded form mirrors the YAML's
// branching structure: each ``choose`` branch is shown as
// "IF <conditions> THEN <sequence>", with a final "OTHERWISE
// <default>" panel when one is present. The same pattern handles
// ``parallel`` / ``sequence`` lists.
function renderActionItem(host, action, ctx) {
  if (action && typeof action === "object" && Array.isArray(action.choose)) {
    return html`<div class="flow-choose">
      ${action.choose.map(
        (branch, i) => html`
          <div class="flow-branch">
            <div class="flow-branch-label">
              ${i === 0
                ? host._t("automations_flow_branch_if", "If")
                : host._t("automations_flow_branch_else_if", "Else if")}
            </div>
            ${asArray(branch.conditions).map((c) =>
              renderConditionItem(host, c, ctx),
            )}
            <div class="flow-arrow-sm">↓</div>
            ${asArray(branch.sequence).map((s) =>
              renderActionItem(host, s, ctx),
            )}
          </div>
        `,
      )}
      ${Array.isArray(action.default) && action.default.length
        ? html`<div class="flow-branch">
            <div class="flow-branch-label">
              ${host._t("automations_flow_branch_otherwise", "Otherwise")}
            </div>
            ${action.default.map((s) => renderActionItem(host, s, ctx))}
          </div>`
        : ""}
    </div>`;
  }
  if (action && typeof action === "object" && Array.isArray(action.parallel)) {
    return html`<div class="flow-branch">
      <div class="flow-branch-label">
        ${host._t("automations_flow_branch_in_parallel", "In parallel")}
      </div>
      ${action.parallel.map((s) => renderActionItem(host, s, ctx))}
    </div>`;
  }
  if (action && typeof action === "object" && Array.isArray(action.sequence)) {
    return html`<div class="flow-branch">
      <div class="flow-branch-label">
        ${host._t("automations_flow_branch_in_sequence", "In sequence")}
      </div>
      ${action.sequence.map((s) => renderActionItem(host, s, ctx))}
    </div>`;
  }
  if (action && typeof action === "object" && action.repeat) {
    const inner = action.repeat.sequence || action.repeat.actions || [];
    const repeatLabel = (() => {
      const r = action.repeat;
      if (r.count != null)
        return `Repeat ${r.count} time${r.count !== 1 ? "s" : ""}`;
      if (r.while)
        return host._t(
          "automations_flow_repeat_while",
          "Repeat while condition holds",
        );
      if (r.until)
        return host._t(
          "automations_flow_repeat_until",
          "Repeat until condition is met",
        );
      return host._t("automations_flow_repeat", "Repeat");
    })();
    return html`<div class="flow-branch">
      <div class="flow-branch-label">${repeatLabel}</div>
      ${(Array.isArray(inner) ? inner : [inner]).map((s) =>
        renderActionItem(host, s, ctx),
      )}
    </div>`;
  }
  return renderFlowNode(host, action, "action", ctx);
}

// ---------------------------------------------------------------------------
// Shared card header (used by proposal + refining cards)
// ---------------------------------------------------------------------------

// Shared icon + alias + description block used by the automations list rows and
// the chat proposal/refining cards so they stay visually identical.
export function renderAutomationIdentity(alias, description, opts = {}) {
  const {
    badge = "",
    titleSuffix = null,
    nameOverride = null,
    tail = null,
    isSelora = true,
  } = opts;
  const cleanedDescription = (description || "").replace(
    /^\[Selora AI\]\s*/,
    "",
  );
  return html`
    <ha-icon
      icon="mdi:robot"
      style="--mdc-icon-size:18px;color:var(--primary-text-color);flex-shrink:0;"
    ></ha-icon>
    <div class="auto-row-name">
      ${nameOverride
        ? nameOverride
        : html`<div class="auto-row-title-row">
            <span class="auto-row-title">${alias}</span>
            ${isSelora && !badge
              ? html`<ha-icon
                  class="selora-ai-mark"
                  icon="mdi:creation"
                  title="Created by Selora AI"
                ></ha-icon>`
              : ""}
            ${titleSuffix || ""}
            ${badge
              ? html`<span
                  style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;background:var(--selora-accent);color:#000;padding:2px 8px;border-radius:4px;flex-shrink:0;"
                  >${badge}</span
                >`
              : ""}
          </div>`}
      ${cleanedDescription
        ? html`<span class="auto-row-desc">${cleanedDescription}</span>`
        : ""}
      ${tail || ""}
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Automation flowchart renderer
// ---------------------------------------------------------------------------

export function renderAutomationFlowchart(host, auto) {
  if (!auto) return html``;
  const triggers = (() => {
    const t = auto.triggers ?? auto.trigger ?? [];
    return Array.isArray(t) ? t : [t];
  })();
  const conditions = (() => {
    const c = auto.conditions ?? auto.condition ?? [];
    return Array.isArray(c) ? c : [c];
  })().filter(Boolean);
  const actions = (() => {
    const a = auto.actions ?? auto.action ?? [];
    return Array.isArray(a) ? a : [a];
  })();
  if (!triggers.length && !actions.length) return html``;
  // Conditions inside the chart may reference triggers by id
  // (condition: trigger), so every node renders with the trigger list
  // in scope.
  const ctx = { triggers };
  // Triggers whose only job is re-evaluating the conditions below, or that
  // a branch already quotes as "Triggered by …", add nothing — hide the
  // whole section when every trigger is such a duplicate.
  const shownTriggers = displayTriggers(triggers, conditions, actions);
  return html`
    <div class="flow-chart">
      ${shownTriggers.length
        ? html`<div class="flow-section flow-section--inline">
            <div class="flow-label">
              ${shownTriggers.length > 1
                ? host._t(
                    "automations_flow_label_trigger_any",
                    "Trigger (any of these)",
                  )
                : host._t("automations_flow_label_trigger", "Trigger")}
            </div>
            ${shownTriggers.map((t) => renderFlowNode(host, t, "trigger", ctx))}
          </div>`
        : ""}
      ${conditions.length
        ? html`
            ${shownTriggers.length ? html`<div class="flow-arrow">↓</div>` : ""}
            <div class="flow-section flow-section--inline">
              <div class="flow-label">
                ${host._t("automations_flow_label_condition", "Condition")}
              </div>
              ${conditions.map((c) => renderConditionItem(host, c, ctx))}
            </div>
          `
        : ""}
      ${shownTriggers.length || conditions.length
        ? html`<div class="flow-arrow">↓</div>`
        : ""}
      <div class="flow-section flow-section--stacked">
        <div class="flow-label">
          ${host._t("automations_flow_label_actions", "Actions")}
        </div>
        ${actions.map((a) => renderActionItem(host, a, ctx))}
      </div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Proposal card (chat automation proposals)
// ---------------------------------------------------------------------------

export function renderProposalCard(host, msg, msgIndex) {
  const status = msg.automation_status;
  const automation = msg.automation;
  const yaml = msg.automation_yaml || "";
  const risk = msg.risk_assessment || automation?.risk_assessment || null;
  const scrutinyTags = risk?.scrutiny_tags || [];

  if (status === "saved") {
    const isEnabled = _savedIsEnabled(host, msg);
    const yamlKey = `saved_${msgIndex}`;
    const yamlOpen = host._yamlOpen && host._yamlOpen[msgIndex];
    return html`
      <div class="automation-subcard">
        <div class="automation-subcard-header">
          ${renderAutomationIdentity(automation.alias, msg.description, {
            badge: isEnabled
              ? host._t("automations_badge_enabled", "Enabled")
              : host._t("automations_badge_saved", "Saved"),
          })}
        </div>
        <div class="automation-subcard-body">
          ${renderAutomationFlowchart(host, automation)}
          ${yaml
            ? html`
                <div
                  class="yaml-toggle"
                  style="margin-top:12px;"
                  @click=${() => toggleYaml(host, msgIndex)}
                >
                  <ha-icon
                    icon="mdi:code-braces"
                    style="--mdc-icon-size:14px;"
                  ></ha-icon>
                  ${yamlOpen
                    ? host._t("automations_yaml_toggle_hide", "Hide YAML")
                    : host._t("automations_yaml_toggle_view", "View YAML")}
                </div>
                ${yamlOpen
                  ? html`<div style="margin-top:6px;">
                      ${host._renderYamlEditor(yamlKey, yaml, null, {
                        readOnly: true,
                      })}
                    </div>`
                  : ""}
              `
            : ""}
        </div>
      </div>
    `;
  }

  if (status === "declined") {
    return html`
      <div class="proposal-card" style="margin-top:12px; opacity:0.6;">
        <div class="proposal-header" style="color:var(--secondary-text-color);">
          <ha-icon icon="mdi:close-circle-outline"></ha-icon>
          ${host._t(
            "automations_proposal_declined_title",
            "Automation Declined",
          )}
        </div>
        <div class="proposal-body">
          <div class="proposal-name">${automation.alias}</div>
          <div class="proposal-status declined">
            ${host._t(
              "automations_proposal_declined_body",
              "Dismissed. You can refine it by replying below.",
            )}
          </div>
        </div>
      </div>
    `;
  }

  if (status === "refining") {
    return html`
      <div class="automation-subcard">
        <div class="automation-subcard-header">
          ${renderAutomationIdentity(
            automation.alias,
            msg.description || automation.description,
            {
              badge: host._t(
                "automations_badge_being_refined",
                "Being Refined",
              ),
            },
          )}
        </div>
        <div class="automation-subcard-body">
          ${renderAutomationFlowchart(host, automation)}
        </div>
      </div>
    `;
  }

  // Pending proposal — full review UI
  const yamlOpen = host._yamlOpen && host._yamlOpen[msgIndex];
  const yamlKey = `proposal_${msgIndex}`;
  const hasEdits =
    host._editedYaml[yamlKey] !== undefined &&
    host._editedYaml[yamlKey] !== yaml;
  return html`
    <div class="automation-subcard">
      <div class="automation-subcard-header">
        ${renderAutomationIdentity(automation.alias, msg.description, {
          badge: host._t("automations_badge_proposal", "Proposal"),
        })}
      </div>
      <div class="automation-subcard-body">
        ${risk?.level === "elevated"
          ? html`
              <div
                class="proposal-status"
                style="background:rgba(255,152,0,0.12); color:var(--warning-color,#ff9800); border:1px solid rgba(255,152,0,0.25);"
              >
                <ha-icon icon="mdi:alert-outline"></ha-icon>
                <div>
                  <strong
                    >${host._t(
                      "automations_proposal_elevated_risk",
                      "Elevated risk review recommended.",
                    )}</strong
                  >
                  <div style="margin-top:4px;">${risk.summary}</div>
                  ${risk.reasons?.length
                    ? html`<div style="margin-top:6px; font-size:12px;">
                        ${risk.reasons.join(" ")}
                      </div>`
                    : ""}
                </div>
              </div>
            `
          : ""}
        ${renderAutomationFlowchart(host, automation)}

        <div
          class="yaml-toggle"
          style="margin-top:12px;"
          @click=${() => toggleYaml(host, msgIndex)}
        >
          <ha-icon
            icon="mdi:code-braces"
            style="--mdc-icon-size:14px;"
          ></ha-icon>
          ${yamlOpen
            ? host._t("automations_yaml_toggle_hide", "Hide YAML")
            : host._t("automations_yaml_toggle_edit", "Edit YAML")}
        </div>
        ${yamlOpen
          ? html`<div style="margin-top:6px;">
              ${host._renderYamlEditor(yamlKey, yaml)}
              ${hasEdits
                ? html`<div class="proposal-verify">
                    ${host._t(
                      "automations_proposal_yaml_edits_note",
                      "Your YAML edits will be used when you accept.",
                    )}
                  </div>`
                : ""}
            </div>`
          : ""}
      </div>
    </div>
  `;
}

// Helper for renderProposalCard / renderProposalActions: resolve
// whether the saved automation is currently enabled. Both functions
// need the same answer; isolating it here keeps them in sync.
function _savedIsEnabled(host, msg) {
  const savedAutomationId = msg.automation_id || null;
  if (!savedAutomationId) return false;
  const created = (host._automations || []).find(
    (a) => a.automation_id === savedAutomationId,
  );
  return created ? host._automationIsEnabled(created) : false;
}

// The action row beneath an automation proposal — rendered OUTSIDE
// the chat bubble (like normal quick-actions) so the buttons read as
// "next-step suggestions from Selora" rather than "options inside the
// message". Returns one of three layouts depending on lifecycle:
//
//   - pending: a single "Accept & Save" button with the exit
//     animation hook
//   - saved + enabled: the Run / Trace / Edit qa-suggestion chips
//   - saved + disabled (only happens for elevated-risk automations
//     that the backend forced off): an explicit Enable button + a
//     short risk caveat
//   - refining / declined / no-automation: nothing
export function renderProposalActions(host, msg, msgIndex) {
  if (!msg?.automation) return "";
  const status = msg.automation_status;
  const automation = msg.automation;
  const risk = msg.risk_assessment || automation?.risk_assessment || null;

  if (status === "saved") {
    const savedAutomationId = msg.automation_id || null;
    const created = savedAutomationId
      ? (host._automations || []).find(
          (a) => a.automation_id === savedAutomationId,
        )
      : null;
    if (!created) return "";
    const isEnabled = host._automationIsEnabled(created);
    const toggling = !!(host._togglingAutomation || {})[savedAutomationId];
    const elevated = risk?.level === "elevated";

    if (isEnabled) {
      return html`<div class="qa-group automation-card-actions">
        <button
          class="qa-suggestion"
          ?disabled=${!!(host._runningAutomation || {})[savedAutomationId]}
          title=${host._t(
            "automations_action_run_tooltip",
            "Trigger the actions now to verify they work",
          )}
          @click=${() =>
            host._runAutomation(created.entity_id, savedAutomationId)}
        >
          <span class="qa-glow-track" aria-hidden="true">
            <span class="qa-glow-spot"></span>
          </span>
          <ha-icon class="qa-suggestion-lead" icon="mdi:play"></ha-icon>
          <span class="qa-suggestion-label"
            >${(host._runningAutomation || {})[savedAutomationId]
              ? host._t("automations_action_running", "Running…")
              : host._t("automations_action_run_now", "Run now")}</span
          >
        </button>
        <button
          class="qa-suggestion"
          title=${host._t(
            "automations_action_open_in_ha_tooltip",
            "Open this automation in Home Assistant",
          )}
          @click=${() => host._openAutomationInHA(savedAutomationId)}
        >
          <span class="qa-glow-track" aria-hidden="true">
            <span class="qa-glow-spot"></span>
          </span>
          <ha-icon class="qa-suggestion-lead" icon="mdi:open-in-new"></ha-icon>
          <span class="qa-suggestion-label"
            >${host._t("automations_action_view_in_ha", "View in HA")}</span
          >
        </button>
      </div>`;
    }
    return html`
      <div class="automation-card-actions">
        <button
          class="btn btn-success"
          ?disabled=${toggling}
          @click=${() =>
            host._enableSavedAutomation(created.entity_id, savedAutomationId)}
        >
          <ha-icon
            icon=${toggling ? "mdi:loading" : "mdi:toggle-switch-outline"}
            style="--mdc-icon-size:14px;"
          ></ha-icon>
          ${toggling
            ? host._t("automations_action_enabling", "Enabling…")
            : host._t(
                "automations_action_enable_automation",
                "Enable automation",
              )}
        </button>
      </div>
      ${elevated
        ? html`<p class="automation-workflow-note elevated">
            <ha-icon
              icon="mdi:shield-alert-outline"
              style="--mdc-icon-size:14px;"
            ></ha-icon>
            ${host._t(
              "automations_elevated_risk_note",
              "Uses elevated-risk actions — review the flow and YAML before enabling.",
            )}
          </p>`
        : ""}
    `;
  }

  if (status !== "pending" && status !== undefined && status !== null) {
    return "";
  }

  // Pending — Accept & Save. The yamlKey mirrors the pending-card key
  // built in renderProposalCard so an edited YAML round-trip lines up.
  const yamlKey = `proposal_${msgIndex}`;
  return html`<div
    class="automation-card-actions ${(host._acceptAnimating || {})[msgIndex]
      ? "exiting"
      : ""}"
  >
    <button
      class="btn btn-success"
      ?disabled=${(host._acceptAnimating || {})[msgIndex]}
      @click=${() =>
        host._acceptAutomationWithEdits(msgIndex, automation, yamlKey)}
    >
      <ha-icon icon="mdi:check" style="--mdc-icon-size:14px;"></ha-icon>
      ${host._t("automations_action_accept_and_save", "Accept & Save")}
    </button>
  </div>`;
}

// ---------------------------------------------------------------------------
// Toggle YAML visibility
// ---------------------------------------------------------------------------

export function toggleYaml(host, msgIndex) {
  host._yamlOpen = {
    ...(host._yamlOpen || {}),
    [msgIndex]: !(host._yamlOpen || {})[msgIndex],
  };
  host.requestUpdate();
}

// ---------------------------------------------------------------------------
// Masonry column layout utility
// ---------------------------------------------------------------------------

export function masonryColumns(cards, cols = 3, firstColFooter = null) {
  const w = window.innerWidth;
  const numCols = w <= 600 ? 1 : w <= 1000 ? 2 : cols;
  const buckets = Array.from({ length: numCols }, () => []);
  cards.forEach((c, i) => buckets[i % numCols].push(c));
  return buckets.map(
    (col, i) =>
      html`<div class="masonry-col">
        ${col}${i === 0 && firstColFooter ? firstColFooter : ""}
      </div>`,
  );
}

// ---------------------------------------------------------------------------
// Automations tab — main render
// ---------------------------------------------------------------------------

export function renderAutomations(host) {
  const filterText = (host._automationFilter || "").toLowerCase();
  const statusFilter = host._statusFilter || "all";
  const sortBy = host._sortBy || "recent";
  const sortDir = host._sortDir || "desc";

  let filteredAutomations = [...host._automations];

  const staleList = getStaleAutomations(host);
  const staleSet = new Set(staleList.map((a) => a.automation_id));

  // Status filter
  if (statusFilter === "enabled") {
    filteredAutomations = filteredAutomations.filter((a) =>
      host._automationIsEnabled(a),
    );
  } else if (statusFilter === "disabled") {
    filteredAutomations = filteredAutomations.filter(
      (a) => !host._automationIsEnabled(a),
    );
  } else if (statusFilter === "stale") {
    filteredAutomations = filteredAutomations.filter((a) =>
      staleSet.has(a.automation_id),
    );
  }

  // Text filter
  if (filterText) {
    filteredAutomations = filteredAutomations.filter((a) =>
      (a.alias || "").toLowerCase().includes(filterText),
    );
  }

  // Sort. Each `sortBy` has a natural direction (recent: desc by time,
  // alpha: A→Z asc, enabled_first: enabled→disabled). The toggle button
  // flips that natural order — reverse the array when dir doesn't match
  // the natural one, so the comparators stay simple.
  const naturalDir = { recent: "desc", alpha: "asc", enabled_first: "asc" };
  if (sortBy === "recent") {
    filteredAutomations.sort((a, b) => {
      const aTime = a.last_triggered ? new Date(a.last_triggered).getTime() : 0;
      const bTime = b.last_triggered ? new Date(b.last_triggered).getTime() : 0;
      return bTime - aTime;
    });
  } else if (sortBy === "alpha") {
    filteredAutomations.sort((a, b) =>
      (a.alias || "").localeCompare(b.alias || ""),
    );
  } else if (sortBy === "enabled_first") {
    filteredAutomations.sort((a, b) => {
      const aOn = host._automationIsEnabled(a) ? 0 : 1;
      const bOn = host._automationIsEnabled(b) ? 0 : 1;
      return aOn - bOn;
    });
  }
  if (sortDir !== naturalDir[sortBy]) {
    filteredAutomations.reverse();
  }

  const perPage = host._autosPerPage || 10;
  const totalAutoPages = Math.max(
    1,
    Math.ceil(filteredAutomations.length / perPage),
  );
  const safeAutoPage = Math.min(host._automationsPage, totalAutoPages);
  const pagedAutomations = filteredAutomations.slice(
    (safeAutoPage - 1) * perPage,
    safeAutoPage * perPage,
  );
  const selectableAutomations = filteredAutomations.filter(
    (a) => !a._draft && a.automation_id,
  );
  const selectableIds = selectableAutomations.map((a) => a.automation_id);
  const selectedIds = host._getSelectedAutomationIds();
  const selectedVisibleCount = selectableIds.filter(
    (id) => host._selectedAutomationIds[id],
  ).length;
  const allVisibleSelected =
    selectableIds.length > 0 && selectedVisibleCount === selectableIds.length;
  const partiallyVisibleSelected =
    selectedVisibleCount > 0 && !allVisibleSelected;
  const hiddenSelectedCount = Math.max(
    0,
    selectedIds.length - selectedVisibleCount,
  );
  const bulkDisabled = selectedIds.length === 0 || host._bulkActionInProgress;

  return html`
    <div class="scroll-view" @click=${() => host._closeBurgerMenus()}>
      <div class="page-root">
        <div class="page-header">
          <h1 class="page-h1">
            ${host._t("automations_page_title", "Automations")}
          </h1>
          ${host._automations.length > 0
            ? html`<button
                class="filter-row-action"
                ?disabled=${host._llmNeedsSetup}
                title=${host._llmNeedsSetup
                  ? host._t(
                      "automations_llm_setup_required_tooltip",
                      "Configure an LLM provider first",
                    )
                  : ""}
                @click=${() => host._startNewAutomationChat()}
              >
                <ha-icon
                  icon="mdi:plus"
                  style="--mdc-icon-size:13px;"
                ></ha-icon>
                ${host._t(
                  "automations_new_automation_button",
                  "New Automation",
                )}
              </button>`
            : ""}
        </div>
        ${renderSuggestionsSection(host)}
        <div class="page-section-title">
          ${host._t("automations_section_title", "Your Automations")}
        </div>
        ${host._automations.length > 0
          ? html`
              <div class="filter-tabs-row" style="margin-top:12px;">
                <div class="filter-tabs" role="tablist">
                  ${["all", "enabled", "disabled"].map(
                    (s) => html`
                      <button
                        role="tab"
                        aria-selected=${host._statusFilter === s}
                        class="filter-tab ${host._statusFilter === s
                          ? "active"
                          : ""}"
                        @click=${() => {
                          host._statusFilter = s;
                          host._automationsPage = 1;
                        }}
                      >
                        ${host._t(
                          `automations_status_tab_${s}`,
                          s.charAt(0).toUpperCase() + s.slice(1),
                        )}
                      </button>
                    `,
                  )}
                  ${staleSet.size > 0
                    ? html`<button
                        role="tab"
                        aria-selected=${host._statusFilter === "stale"}
                        class="filter-tab ${host._statusFilter === "stale"
                          ? "active"
                          : ""}"
                        title=${staleTooltip(host)}
                        @click=${() => {
                          host._statusFilter = "stale";
                          host._automationsPage = 1;
                        }}
                      >
                        <ha-icon
                          icon="mdi:alert-outline"
                          style="--mdc-icon-size:14px;color:#f59e0b;display:block;"
                        ></ha-icon>
                        <span
                          >${host._t("automations_status_tab_stale", "Stale")}
                          (${staleSet.size})</span
                        >
                      </button>`
                    : ""}
                </div>
                <div class="filter-tabs-actions">
                  ${host._bulkEditMode
                    ? html`
                        <label class="bulk-select-all">
                          <input
                            type="checkbox"
                            ?checked=${allVisibleSelected}
                            .indeterminate=${partiallyVisibleSelected}
                            ?disabled=${selectableIds.length === 0 ||
                            host._bulkActionInProgress}
                            @change=${(e) =>
                              host._toggleSelectAllFiltered(
                                filteredAutomations,
                                e.target.checked,
                              )}
                          />
                          <span
                            >${host._t(
                              "automations_bulk_select_all",
                              "Select all",
                            )}</span
                          >
                        </label>
                        <button
                          class="filter-row-secondary"
                          @click=${() => {
                            host._bulkEditMode = false;
                            host._clearAutomationSelection();
                          }}
                        >
                          ${host._t("automations_bulk_done", "Done")}
                        </button>
                      `
                    : html`
                        <button
                          class="filter-row-secondary"
                          @click=${() => {
                            host._bulkEditMode = true;
                          }}
                        >
                          <ha-icon
                            icon="mdi:checkbox-multiple-outline"
                            style="--mdc-icon-size:14px;"
                          ></ha-icon>
                          ${host._t("automations_bulk_edit", "Bulk edit")}
                        </button>
                      `}
                </div>
              </div>
              <div class="filter-row">
                <div class="filter-input-wrap" style="flex:1 1 260px;">
                  <ha-icon icon="mdi:magnify"></ha-icon>
                  <input
                    type="text"
                    placeholder=${host._t(
                      "automations_filter_placeholder",
                      "Filter automations…",
                    )}
                    .value=${host._automationFilter}
                    @input=${(e) => {
                      host._automationFilter = e.target.value;
                      host._automationsPage = 1;
                    }}
                  />
                  ${host._automationFilter
                    ? html`<ha-icon
                        icon="mdi:close-circle"
                        style="--mdc-icon-size:16px;cursor:pointer;opacity:0.5;flex-shrink:0;"
                        @click=${() => {
                          host._automationFilter = "";
                          host._automationsPage = 1;
                        }}
                      ></ha-icon>`
                    : ""}
                </div>
                <div class="sort-group">
                  <select
                    class="sort-select"
                    .value=${host._sortBy}
                    @change=${(e) => {
                      host._sortBy = e.target.value;
                    }}
                  >
                    <option value="recent">
                      ${host._t("automations_sort_recent", "Recent activity")}
                    </option>
                    <option value="alpha">
                      ${host._t("automations_sort_alpha", "Alphabetical")}
                    </option>
                    <option value="enabled_first">
                      ${host._t(
                        "automations_sort_enabled_first",
                        "Enabled first",
                      )}
                    </option>
                  </select>
                  <button
                    class="sort-dir-toggle"
                    title=${sortDir === "desc"
                      ? "Sort descending (click for ascending)"
                      : "Sort ascending (click for descending)"}
                    @click=${() => {
                      host._sortDir = sortDir === "desc" ? "asc" : "desc";
                    }}
                  >
                    <ha-icon
                      icon=${sortDir === "desc"
                        ? "mdi:sort-descending"
                        : "mdi:sort-ascending"}
                      style="--mdc-icon-size:18px;"
                    ></ha-icon>
                  </button>
                </div>
              </div>
              ${host._bulkEditMode && selectedIds.length > 0
                ? html`
                    <div class="bulk-actions-row">
                      <div class="left">
                        ${selectedIds.length}
                        selected${hiddenSelectedCount > 0
                          ? html` <span style="opacity:0.65;font-weight:500;"
                              >(${hiddenSelectedCount} hidden by filter)</span
                            >`
                          : ""}
                        ${host._bulkActionInProgress
                          ? html`<span style="opacity:0.75;font-weight:500;">
                              · ${host._bulkActionLabel}</span
                            >`
                          : ""}
                      </div>
                      <div class="actions">
                        <button
                          class="btn btn-outline"
                          ?disabled=${bulkDisabled}
                          @click=${() => host._bulkToggleSelected(true)}
                        >
                          ${host._bulkActionInProgress
                            ? host._t("automations_bulk_working", "Working…")
                            : host._t(
                                "automations_bulk_enable_all",
                                "Enable all",
                              )}
                        </button>
                        <button
                          class="btn btn-outline"
                          ?disabled=${bulkDisabled}
                          @click=${() => host._bulkToggleSelected(false)}
                        >
                          ${host._bulkActionInProgress
                            ? host._t("automations_bulk_working", "Working…")
                            : host._t(
                                "automations_bulk_disable_all",
                                "Disable all",
                              )}
                        </button>
                        <button
                          class="btn btn-outline btn-danger"
                          ?disabled=${bulkDisabled}
                          @click=${() => host._bulkSoftDeleteSelected()}
                        >
                          ${host._bulkActionInProgress
                            ? host._t("automations_bulk_working", "Working…")
                            : host._t(
                                "automations_bulk_delete_selected",
                                "Delete selected",
                              )}
                        </button>
                        <button
                          class="btn btn-ghost"
                          ?disabled=${host._bulkActionInProgress}
                          @click=${() => host._clearAutomationSelection()}
                        >
                          ${host._t("automations_bulk_clear", "Clear")}
                        </button>
                      </div>
                    </div>
                  `
                : ""}
              <div class="automations-list">
                ${pagedAutomations.map((a) => {
                  const isDraft = !!a._draft;
                  const isOn = host._automationIsEnabled(a);
                  const isUnavailable = a.state === "unavailable";
                  const automationId = a.automation_id || "";
                  const hasAutomationId = !!automationId;
                  const canToggle =
                    hasAutomationId && !host._bulkActionInProgress;
                  const deleting = host._deletingAutomation[automationId];
                  const loadingChat = host._loadingToChat[automationId];
                  const runKey = automationId || a.entity_id;
                  const running = !!host._runningAutomation?.[runKey];
                  const burgerOpen = host._openBurgerMenu === automationId;
                  const cardExpanded = !!host._cardActiveTab[a.entity_id];
                  const ago = formatTimeAgo(a.last_triggered);
                  const lastRun = ago
                    ? ago
                    : !isOn
                      ? host._t("automations_last_run_disabled", "Disabled")
                      : host._t("automations_last_run_never", "Never");

                  return html`
                    <div
                      class="auto-row${cardExpanded
                        ? " expanded"
                        : ""}${!isDraft && !isOn
                        ? " disabled"
                        : ""}${host._highlightedAutomation === a.entity_id
                        ? " highlighted"
                        : ""}"
                      data-entity-id="${a.entity_id}"
                    >
                      <div
                        class="auto-row-main"
                        @click=${(e) => {
                          if (
                            e.target.closest(
                              ".toggle-switch, .burger-menu-wrapper, .burger-dropdown, .burger-item, .row-action-btn, .card-select, .rename-input, .rename-save-btn, .btn",
                            )
                          )
                            return;
                          const current = host._cardActiveTab[a.entity_id];
                          if (current) {
                            host._cardActiveTab = {
                              ...host._cardActiveTab,
                              [a.entity_id]: null,
                            };
                          } else {
                            const defaultTab =
                              (a.triggers ?? a.trigger)?.length ||
                              (a.actions ?? a.action)?.length
                                ? "flow"
                                : a.yaml_text
                                  ? "yaml"
                                  : hasAutomationId
                                    ? "history"
                                    : null;
                            host._cardActiveTab = {
                              ...host._cardActiveTab,
                              [a.entity_id]: defaultTab,
                            };
                          }
                        }}
                      >
                        ${host._bulkEditMode && hasAutomationId
                          ? html`
                              <label class="card-select">
                                <input
                                  type="checkbox"
                                  .checked=${!!host._selectedAutomationIds[
                                    automationId
                                  ]}
                                  ?disabled=${host._bulkActionInProgress}
                                  @click=${(e) => e.stopPropagation()}
                                  @change=${(e) =>
                                    host._toggleAutomationSelection(
                                      automationId,
                                      e,
                                    )}
                                />
                              </label>
                            `
                          : ""}
                        ${renderAutomationIdentity(a.alias, a.description, {
                          isSelora: !!a.is_selora,
                          titleSuffix: html`
                            ${a.recipe_title
                              ? html`<span
                                  class="recipe-pill"
                                  title=${host._t(
                                    "automations_recipe_pill_tooltip",
                                    "Installed by a Selora recipe — manage it from the Recipes tab.",
                                  )}
                                >
                                  <ha-icon
                                    icon="mdi:book-open-variant"
                                  ></ha-icon>
                                  <span class="recipe-pill-name"
                                    >${a.recipe_title}</span
                                  >
                                </span>`
                              : ""}
                            ${isUnavailable
                              ? html`<span
                                  class="needs-attention-pill"
                                  @click=${(e) => {
                                    e.stopPropagation();
                                    host._unavailableAutoId = automationId;
                                    host._unavailableAutoName = a.alias;
                                  }}
                                  >${host._t(
                                    "automations_needs_attention_pill",
                                    "Needs attention",
                                  )}</span
                                >`
                              : ""}
                            ${staleSet.has(automationId)
                              ? html`<span
                                  class="stale-pill"
                                  title=${staleTooltip(host)}
                                >
                                  <ha-icon
                                    icon="mdi:alert-outline"
                                    style="--mdc-icon-size:12px;"
                                  ></ha-icon>
                                  Stale
                                </span>`
                              : ""}
                          `,
                          nameOverride:
                            host._editingAlias === automationId
                              ? html`
                                  <input
                                    class="rename-input"
                                    data-id="${automationId}"
                                    .value=${host._editingAliasValue}
                                    @input=${(e) => {
                                      host._editingAliasValue = e.target.value;
                                    }}
                                    @click=${(e) => e.stopPropagation()}
                                    @keydown=${(e) => {
                                      if (e.key === "Enter")
                                        host._saveRenameAutomation(
                                          automationId,
                                        );
                                      if (e.key === "Escape")
                                        host._cancelRenameAutomation();
                                    }}
                                  />
                                  <button
                                    class="rename-save-btn"
                                    title=${host._t(
                                      "automations_rename_save_tooltip",
                                      "Save",
                                    )}
                                    @click=${() =>
                                      host._saveRenameAutomation(automationId)}
                                  >
                                    <ha-icon
                                      icon="mdi:check"
                                      style="--mdc-icon-size:16px;"
                                    ></ha-icon>
                                  </button>
                                `
                              : null,
                          tail: html`<span class="auto-row-mobile-meta">
                            <span
                              >${host._t(
                                "automations_last_run_prefix",
                                "Last run:",
                              )}
                              ${lastRun}</span
                            >
                            <ha-icon
                              icon="mdi:chevron-down"
                              class="card-chevron ${cardExpanded ? "open" : ""}"
                              style="--mdc-icon-size:16px;"
                            ></ha-icon>
                          </span>`,
                        })}
                        <span class="auto-row-last-run"
                          ><span class="last-run-prefix"
                            >${host._t(
                              "automations_last_run_prefix_inline",
                              "Last run:",
                            )} </span
                          >${lastRun}${a.last_triggered
                            ? html`<span class="setting-tooltip"
                                >Last run:
                                ${new Date(
                                  a.last_triggered,
                                ).toLocaleString()}</span
                              >`
                            : ""}
                        </span>
                        <label
                          class="toggle-switch"
                          title="${canToggle
                            ? isOn
                              ? host._t("automations_toggle_enabled", "Enabled")
                              : host._t(
                                  "automations_toggle_disabled",
                                  "Disabled",
                                )
                            : host._t(
                                "automations_toggle_unavailable",
                                "Unavailable",
                              )}"
                          style="flex-shrink:0;${canToggle
                            ? ""
                            : "opacity:0.45;cursor:not-allowed;"}"
                          @click=${(e) => {
                            e.stopPropagation();
                            if (!canToggle) {
                              host._showToast(
                                host._t(
                                  "automations_toast_toggle_unresolved",
                                  "Unable to toggle: automation id was not resolved. Reload and try again.",
                                ),
                                "error",
                              );
                            }
                          }}
                        >
                          <input
                            type="checkbox"
                            .checked=${isOn}
                            ?disabled=${!canToggle}
                            @click=${(e) => e.stopPropagation()}
                            @change=${(e) => {
                              if (!canToggle) return;
                              host._toggleAutomation(
                                a.entity_id,
                                automationId,
                                e.target.checked,
                              );
                            }}
                          />
                          <div class="toggle-track ${isOn ? "on" : ""}">
                            <div class="toggle-thumb"></div>
                          </div>
                        </label>
                        ${!isDraft && a.entity_id
                          ? html`
                              <button
                                class="row-action-btn"
                                ?disabled=${running || isUnavailable}
                                @click=${(e) => {
                                  e.stopPropagation();
                                  if (running || isUnavailable) return;
                                  host._runAutomation(
                                    a.entity_id,
                                    automationId,
                                  );
                                }}
                                title=${host._t(
                                  "automations_run_tooltip",
                                  "Run Automation",
                                )}
                              >
                                <ha-icon
                                  icon="mdi:play"
                                  style="--mdc-icon-size:16px;"
                                ></ha-icon>
                              </button>
                            `
                          : ""}
                        ${hasAutomationId
                          ? html`
                              <div class="burger-menu-wrapper">
                                <button
                                  class="burger-btn"
                                  @click=${(e) =>
                                    host._toggleBurgerMenu(automationId, e)}
                                  ?disabled=${host._bulkActionInProgress}
                                  title=${host._t(
                                    "automations_more_actions_tooltip",
                                    "More actions",
                                  )}
                                >
                                  <ha-icon
                                    icon="mdi:dots-vertical"
                                    style="--mdc-icon-size:16px;"
                                  ></ha-icon>
                                </button>
                                ${burgerOpen
                                  ? html`
                                      <div
                                        class="burger-dropdown"
                                        style=${host._openBurgerMenuStyle}
                                      >
                                        <button
                                          class="burger-item"
                                          @click=${(e) => {
                                            e.stopPropagation();
                                            host._openBurgerMenu = null;
                                            host._loadAutomationToChat(
                                              automationId,
                                            );
                                          }}
                                          ?disabled=${loadingChat}
                                        >
                                          <ha-icon
                                            icon="mdi:chat-processing-outline"
                                            style="--mdc-icon-size:14px;"
                                          ></ha-icon>
                                          ${loadingChat
                                            ? host._t(
                                                "automations_burger_loading",
                                                "Loading…",
                                              )
                                            : host._t(
                                                "automations_burger_refine_in_chat",
                                                "Refine in chat",
                                              )}
                                        </button>
                                        <button
                                          class="burger-item"
                                          @click=${(e) => {
                                            e.stopPropagation();
                                            host._startRenameAutomation(
                                              automationId,
                                              a.alias,
                                            );
                                          }}
                                        >
                                          <ha-icon
                                            icon="mdi:pencil-outline"
                                            style="--mdc-icon-size:14px;"
                                          ></ha-icon>
                                          ${host._t(
                                            "automations_burger_rename",
                                            "Rename",
                                          )}
                                        </button>
                                        <button
                                          class="burger-item"
                                          @click=${(e) => {
                                            e.stopPropagation();
                                            host._openBurgerMenu = null;
                                            window.history.pushState(
                                              null,
                                              "",
                                              `/config/automation/edit/${automationId}`,
                                            );
                                            window.dispatchEvent(
                                              new Event("location-changed"),
                                            );
                                          }}
                                        >
                                          <ha-icon
                                            icon="mdi:open-in-new"
                                            style="--mdc-icon-size:14px;"
                                          ></ha-icon>
                                          ${host._t(
                                            "automations_burger_view_in_ha",
                                            "View in HA",
                                          )}
                                        </button>
                                        <button
                                          class="burger-item danger"
                                          ?disabled=${deleting}
                                          @click=${(e) => {
                                            e.stopPropagation();
                                            host._openBurgerMenu = null;
                                            host._deleteAutomation(
                                              automationId,
                                            );
                                          }}
                                        >
                                          <ha-icon
                                            icon="mdi:trash-can-outline"
                                            style="--mdc-icon-size:14px;"
                                          ></ha-icon>
                                          ${deleting
                                            ? host._t(
                                                "automations_burger_deleting",
                                                "Deleting…",
                                              )
                                            : host._t(
                                                "automations_burger_delete",
                                                "Delete",
                                              )}
                                        </button>
                                      </div>
                                    `
                                  : ""}
                              </div>
                            `
                          : isDraft
                            ? ""
                            : html`
                                <div class="burger-menu-wrapper">
                                  <button
                                    class="burger-btn"
                                    disabled
                                    title=${host._t(
                                      "automations_more_actions_external",
                                      "Managed outside Selora AI — edit it where it's defined, e.g. an installed recipe.",
                                    )}
                                  >
                                    <ha-icon
                                      icon="mdi:dots-vertical"
                                      style="--mdc-icon-size:16px;"
                                    ></ha-icon>
                                  </button>
                                </div>
                              `}
                      </div>
                      ${cardExpanded
                        ? html`
                            <div class="auto-row-expand">
                              <div class="card-tabs" style="margin-top:0;">
                                ${(a.triggers ?? a.trigger)?.length ||
                                (a.actions ?? a.action)?.length
                                  ? html`
                                      <button
                                        class="card-tab ${host._cardActiveTab[
                                          a.entity_id
                                        ] === "flow"
                                          ? "active"
                                          : ""}"
                                        @click=${() => {
                                          host._cardActiveTab = {
                                            ...host._cardActiveTab,
                                            [a.entity_id]:
                                              host._cardActiveTab[
                                                a.entity_id
                                              ] === "flow"
                                                ? null
                                                : "flow",
                                          };
                                        }}
                                      >
                                        <ha-icon
                                          icon="mdi:sitemap-outline"
                                          style="--mdc-icon-size:16px;"
                                        ></ha-icon>
                                        ${host._t(
                                          "automations_card_tab_flow",
                                          "Flow",
                                        )}
                                      </button>
                                      <span class="card-tab-sep">|</span>
                                    `
                                  : ""}
                                ${a.yaml_text
                                  ? html`
                                      <button
                                        class="card-tab ${host._cardActiveTab[
                                          a.entity_id
                                        ] === "yaml"
                                          ? "active"
                                          : ""}"
                                        @click=${() => {
                                          host._cardActiveTab = {
                                            ...host._cardActiveTab,
                                            [a.entity_id]:
                                              host._cardActiveTab[
                                                a.entity_id
                                              ] === "yaml"
                                                ? null
                                                : "yaml",
                                          };
                                        }}
                                      >
                                        <ha-icon
                                          icon="mdi:code-braces"
                                          style="--mdc-icon-size:16px;"
                                        ></ha-icon>
                                        ${host._t(
                                          "automations_card_tab_yaml",
                                          "YAML",
                                        )}
                                      </button>
                                      <span class="card-tab-sep">|</span>
                                    `
                                  : ""}
                                ${hasAutomationId
                                  ? html`
                                      <button
                                        class="card-tab ${host._cardActiveTab[
                                          a.entity_id
                                        ] === "history"
                                          ? "active"
                                          : ""}"
                                        @click=${() => {
                                          const isActive =
                                            host._cardActiveTab[a.entity_id] ===
                                            "history";
                                          host._cardActiveTab = {
                                            ...host._cardActiveTab,
                                            [a.entity_id]: isActive
                                              ? null
                                              : "history",
                                          };
                                          if (
                                            !isActive &&
                                            !host._versions[automationId]
                                          ) {
                                            host._versionHistoryOpen = {
                                              ...host._versionHistoryOpen,
                                              [automationId]: true,
                                            };
                                            host._loadVersionHistory(
                                              automationId,
                                            );
                                          }
                                        }}
                                      >
                                        ${host._t(
                                          "automations_card_tab_history",
                                          "History",
                                        )}
                                      </button>
                                    `
                                  : ""}
                              </div>
                              ${host._cardActiveTab[a.entity_id] === "flow" &&
                              ((a.triggers ?? a.trigger)?.length ||
                                (a.actions ?? a.action)?.length)
                                ? renderAutomationFlowchart(host, a)
                                : ""}
                              ${host._cardActiveTab[a.entity_id] === "yaml" &&
                              a.yaml_text
                                ? host._renderYamlEditor(
                                    `yaml_${a.entity_id}`,
                                    a.yaml_text,
                                    (key) =>
                                      host._saveActiveAutomationYaml(
                                        a.automation_id,
                                        key,
                                      ),
                                  )
                                : ""}
                              ${host._cardActiveTab[a.entity_id] ===
                                "history" && hasAutomationId
                                ? host._renderVersionHistoryDrawer(a)
                                : ""}
                            </div>
                          `
                        : ""}
                    </div>
                  `;
                })}
              </div>
              ${totalAutoPages > 1
                ? html`
                    <div class="pagination">
                      <button
                        class="btn btn-outline"
                        ?disabled=${safeAutoPage <= 1}
                        @click=${() => {
                          host._automationsPage = safeAutoPage - 1;
                        }}
                      >
                        ${host._t("automations_pagination_prev", "‹ Prev")}
                      </button>
                      <span class="page-info"
                        >Page ${safeAutoPage} of ${totalAutoPages} ·
                        ${filteredAutomations.length} automations</span
                      >
                      <label class="per-page-label"
                        >${host._t(
                          "automations_pagination_per_page",
                          "Per page:",
                        )}
                        <select
                          class="per-page-select"
                          .value=${String(host._autosPerPage)}
                          @change=${(e) => {
                            host._autosPerPage = Number(e.target.value);
                            host._automationsPage = 1;
                          }}
                        >
                          <option value="10">10</option>
                          <option value="20">20</option>
                          <option value="50">50</option>
                        </select>
                      </label>
                      <button
                        class="btn btn-outline"
                        ?disabled=${safeAutoPage >= totalAutoPages}
                        @click=${() => {
                          host._automationsPage = safeAutoPage + 1;
                        }}
                      >
                        ${host._t("automations_pagination_next", "Next ›")}
                      </button>
                    </div>
                  `
                : ""}
              ${filteredAutomations.length === 0 && host._automations.length > 0
                ? html`<div
                    style="text-align:center;opacity:0.45;padding:24px 0;"
                  >
                    No automations match "${host._automationFilter}"
                  </div>`
                : ""}
            `
          : html`<div style="text-align:center;padding:32px 0;">
              <ha-icon
                icon="mdi:robot-vacuum-variant"
                style="--mdc-icon-size:40px;display:block;margin-bottom:8px;opacity:0.35;"
              ></ha-icon>
              <p style="opacity:0.45;margin:0 0 12px;">
                ${host._t("automations_empty_state", "No automations yet.")}
              </p>
              <button
                class="btn btn-accent"
                ?disabled=${host._llmNeedsSetup}
                title=${host._llmNeedsSetup
                  ? host._t(
                      "automations_llm_setup_required_tooltip",
                      "Configure an LLM provider first",
                    )
                  : ""}
                @click=${() => host._startNewAutomationChat()}
              >
                <ha-icon
                  icon="mdi:plus"
                  style="--mdc-icon-size:14px;"
                ></ha-icon>
                ${host._t(
                  "automations_new_automation_button",
                  "New Automation",
                )}
              </button>
            </div>`}
      </div>
      ${host._renderDiffViewer()} ${renderUnavailableModal(host)}
    </div>
  `;
}

// ---------------------------------------------------------------------------
// "Needs attention" modal — explains unavailable state and links to HA
// ---------------------------------------------------------------------------

function renderUnavailableModal(host) {
  if (!host._unavailableAutoId) return "";
  return html`
    <div
      class="modal-overlay"
      @click=${() => {
        host._unavailableAutoId = null;
        host._unavailableAutoName = null;
      }}
    >
      <div
        class="modal-content"
        style="max-width:440px;border:1px solid var(--selora-accent);"
        @click=${(e) => e.stopPropagation()}
      >
        <h3 class="modal-title">
          <ha-icon
            icon="mdi:alert-circle-outline"
            style="--mdc-icon-size:22px;color:#ef4444;vertical-align:middle;margin-right:6px;"
          ></ha-icon>
          ${host._t(
            "automations_unavailable_modal_title",
            "Automation Unavailable",
          )}
        </h3>
        <p
          style="font-size:14px;line-height:1.6;margin:0 0 8px;color:var(--primary-text-color);"
        >
          <strong
            >${host._unavailableAutoName ||
            host._t(
              "automations_unavailable_default_name",
              "This automation",
            )}</strong
          >
          ${host._t(
            "automations_unavailable_modal_intro",
            "is marked as unavailable by Home Assistant. This usually means:",
          )}
        </p>
        <ul
          style="font-size:13px;line-height:1.8;margin:0 0 16px;padding-left:20px;color:var(--secondary-text-color);"
        >
          <li>
            ${host._t(
              "automations_unavailable_reason_entity",
              "A trigger or condition references an entity that no longer exists",
            )}
          </li>
          <li>
            ${host._t(
              "automations_unavailable_reason_yaml",
              "The automation YAML has a configuration error",
            )}
          </li>
          <li>
            ${host._t(
              "automations_unavailable_reason_integration",
              "A required integration was removed or is not loaded",
            )}
          </li>
        </ul>
        <p
          style="font-size:13px;margin:0 0 16px;color:var(--secondary-text-color);"
        >
          ${host._t(
            "automations_unavailable_modal_advice",
            "Open the automation in Home Assistant Settings to review and fix the configuration.",
          )}
        </p>
        <div class="modal-actions" style="justify-content:center;gap:12px;">
          <button
            class="modal-btn modal-cancel"
            @click=${() => {
              host._unavailableAutoId = null;
              host._unavailableAutoName = null;
            }}
          >
            ${host._t("automations_unavailable_modal_close", "Close")}
          </button>
          <a
            class="modal-btn modal-create"
            href="/developer-tools/state"
            style="text-decoration:none;"
            @click=${() => {
              host._unavailableAutoId = null;
              host._unavailableAutoName = null;
            }}
          >
            <ha-icon
              icon="mdi:code-tags"
              style="--mdc-icon-size:14px;"
            ></ha-icon>
            ${host._t(
              "automations_unavailable_modal_edit_states",
              "Edit States",
            )}
          </a>
          <a
            class="modal-btn modal-create"
            href="/config/automation/dashboard"
            style="text-decoration:none;"
            @click=${() => {
              host._unavailableAutoId = null;
              host._unavailableAutoName = null;
            }}
          >
            <ha-icon icon="mdi:robot" style="--mdc-icon-size:14px;"></ha-icon>
            ${host._t(
              "automations_unavailable_modal_open_in_automations",
              "Open in Automations",
            )}
          </a>
        </div>
      </div>
    </div>
  `;
}
