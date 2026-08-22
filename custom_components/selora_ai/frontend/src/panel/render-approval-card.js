// Render the "Approval required" card.
//
// Each REVIEW call is laid out as:
//
//   ┌───────┐         ┌──────────────────────┐
//   │ 🔒    │   →    │  [HA entity tile]    │
//   │ Lock  │         │  Front Door · locked │
//   └───────┘         └──────────────────────┘
//   Engages a physical lock.
//
// The right-hand tile is a real HA `hui-tile-card`, hydrated from
// ``<div class="selora-entity-grid" data-entity-ids="…">`` by the
// panel's MutationObserver — same mechanism that renders entity
// markers in chat prose.

import { html } from "lit";

import { resolveClientActions } from "./client-actions.js";
import { renderConfirmChip } from "./quick-actions.js";
import {
  actionIcon,
  callTargetEntityIds,
  describeCall,
} from "./action-format.js";

const RISK_LEVEL_STYLES = {
  low: {
    accent: "#3b82f6",
    icon: "mdi:information-outline",
    explainerKey: "approval_risk_explainer_low",
    explainerFallback:
      "Low risk: minor or fully reversible impact (sound, notifications, " +
      "vacuum start/stop).",
  },
  medium: {
    accent: "#f59e0b",
    icon: "mdi:alert-outline",
    explainerKey: "approval_risk_explainer_medium",
    explainerFallback:
      "Medium risk: noticeable side effects you may not want to undo " +
      "(arming the alarm, locking a door, running a user script).",
  },
  high: {
    accent: "#ef4444",
    icon: "mdi:shield-alert-outline",
    explainerKey: "approval_risk_explainer_high",
    explainerFallback:
      "High risk: physical access, security, or host-level impact " +
      "(unlocking a door, disarming the alarm, running shell commands).",
  },
};

const _DELETE_KIND_LABELS = {
  automation: "automation",
  scene: "scene",
  group: "group",
  area: "area",
  script: "script",
  label: "label",
  entity: "entity",
  device: "device",
};

const _DELETE_KIND_ICONS = {
  scene: "mdi:palette-outline",
  group: "mdi:google-circles-communities",
  automation: "mdi:robot-outline",
  area: "mdi:floor-plan",
  script: "mdi:script-text-outline",
  label: "mdi:label-outline",
  entity: "mdi:shape-outline",
  device: "mdi:devices",
};

// The confirmation shapes. All of them use the same card — layout, head, rows,
// terminal states — and a variant carries only what differs, because calling a
// disable or a rename "Delete this?" would describe the wrong action on the one
// screen where the user is deciding whether to let it happen.
//
// `accent`, `headIcon`, `rows` and `renderRow` are part of that: a client action
// is not destructive, so it must not borrow the warning red, and it lists
// proposed actions rather than targets. What it must NOT do is bring its own
// card — a second hand-rolled one drifts from this one on the next change to
// either, and the first version of it did exactly that, shipping classes no
// stylesheet defined.
const _CONFIRM_VARIANTS = {
  delete: {
    accent: "#ef4444",
    headIcon: "mdi:alert-outline",
    rows: (approval) => [
      ...(approval.deletes || []),
      ...(approval.actions || []),
    ],
    renderRow: (host, row) => _renderDeleteRow(host, row),
    doneIcon: "mdi:trash-can-outline",
    doneKey: "approval_status_deleted",
    doneFallback: "Deleted",
    titleKey: "delete_approval_title",
    titleFallback: "Delete this?",
    titlePluralKey: "delete_approval_title_plural",
    titlePluralFallback: "Delete these?",
    warningKey: "delete_approval_warning",
    warningFallback: "This permanently removes it and can't be undone.",
  },
  destructive: {
    accent: "#ef4444",
    headIcon: "mdi:alert-outline",
    rows: (approval) => [
      ...(approval.deletes || []),
      ...(approval.actions || []),
    ],
    renderRow: (host, row) => _renderDeleteRow(host, row),
    doneIcon: "mdi:check-circle-outline",
    doneKey: "approval_status_applied",
    doneFallback: "Applied",
    titleKey: "destructive_approval_title",
    titleFallback: "Apply this change?",
    titlePluralKey: "destructive_approval_title_plural",
    titlePluralFallback: "Apply these changes?",
    warningKey: "destructive_approval_warning",
    warningFallback: "This can't be undone from chat.",
  },
  // Work the PANEL performs. The only shape with its own button: the others
  // resolve server-side and get their Allow / Deny from `msg.quick_actions`,
  // while this one has no server-side resolver to call — the press is what
  // makes the privileged websocket command the signed-in user's own.
  client_action: {
    accent: "var(--selora-accent)",
    // What the CARD is — a thing waiting on the user — the way the delete
    // card's head says "destructive". The row below says what the thing is,
    // so repeating its icon here rendered the same glyph twice.
    headIcon: "mdi:gesture-tap",
    rows: (approval) => approval.client_actions || [],
    renderRow: (host, row) => _renderClientActionRow(host, row),
    doneIcon: "mdi:check-circle-outline",
    doneKey: "client_action_done",
    doneFallback: "Done.",
    cancelledKey: "client_action_failed",
    cancelledFallback: "That did not work.",
    titleKey: "client_action_title",
    titleFallback: "Needs your confirmation",
    titlePluralKey: "client_action_title",
    titlePluralFallback: "Needs your confirmation",
    confirm: {
      // The same quiet approve chip the risk card's Allow uses. Its styles
      // exist so confirmation buttons "stay visually quiet next to the risk
      // card" — a filled button here would shout where Allow murmurs.
      tone: "approve",
      icon: "mdi:plus",
      labelKey: "client_action_confirm",
      labelFallback: "Create",
      busyKey: "client_action_working",
      busyFallback: "Working…",
      run: (host, msg, approval) => resolveClientActions(host, msg, approval),
    },
  },
};

// Render the delete-confirmation card. Destructive accent (red), a row per
// target showing its friendly label + entity_id, and the terminal
// approved/denied/resolving states. The Delete / Cancel buttons themselves
// come from ``msg.quick_actions`` (rendered in the composer row).
function renderConfirmationCard(host, msg, approval, approvalStatus, variant) {
  // Delete wording only when the card is purely deletions — otherwise the
  // neutral copy, which describes a mixed card honestly. Any other variant
  // means what it says.
  const mixedDelete =
    variant === "delete" && (approval.actions || []).length > 0;
  const copy = mixedDelete
    ? _CONFIRM_VARIANTS.destructive
    : _CONFIRM_VARIANTS[variant];
  const accent = copy.accent;
  // Every row is shown: a change the user was not shown is a change they
  // cannot refuse.
  const rows = copy.rows(approval);

  if (approvalStatus === "approved" || approvalStatus === "denied") {
    const resolved = approvalStatus === "approved";
    return html`
      <div
        style="margin-top:10px;display:flex;align-items:center;gap:8px;font-size:12px;color:var(--secondary-text-color);"
      >
        <ha-icon
          icon=${resolved ? copy.doneIcon : "mdi:close-circle-outline"}
          style="--mdc-icon-size:16px;flex-shrink:0;"
        ></ha-icon>
        <span
          >${
            resolved
              ? host._t(copy.doneKey, copy.doneFallback)
              : host._t(
                  copy.cancelledKey || "approval_status_cancelled",
                  copy.cancelledFallback || "Cancelled",
                )
          }</span
        >
      </div>
    `;
  }

  if (approvalStatus === "resolving") {
    return html`
      <div
        style="margin-top:10px;display:flex;align-items:center;gap:8px;font-size:12px;color:var(--secondary-text-color);"
      >
        <span class="spinner" style="width:14px;height:14px;"></span>
        <span
          >${host._t(
            copy.confirm?.busyKey || "approval_working",
            copy.confirm?.busyFallback || "Working…",
          )}</span
        >
      </div>
    `;
  }

  return html`
    <div
      style="margin-top:12px;border:1px solid var(--divider-color);border-left:3px solid ${accent};border-radius:8px;padding:12px 14px;background:var(--card-background-color, rgba(255,255,255,0.02));"
    >
      <div
        style="display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:var(--primary-text-color);padding-bottom:4px;"
      >
        <ha-icon
          icon=${copy.headIcon}
          style="--mdc-icon-size:16px;color:${accent};flex-shrink:0;"
        ></ha-icon>
        <span
          >${
            rows.length > 1
              ? host._t(copy.titlePluralKey, copy.titlePluralFallback)
              : host._t(copy.titleKey, copy.titleFallback)
          }</span
        >
      </div>
      <div style="display:flex;flex-direction:column;">
        ${rows.map((row) => copy.renderRow(host, row))}
      </div>
      ${
        approval.remaining_intent
          ? html`<div
              style="margin-top:6px;display:flex;align-items:center;gap:8px;font-size:12px;color:var(--secondary-text-color);"
            >
              <ha-icon
                icon="mdi:arrow-right-bottom"
                style="--mdc-icon-size:14px;flex-shrink:0;"
              ></ha-icon>
              <span
                >${host._t("approval_then", "then")}
                ${approval.remaining_intent}</span
              >
            </div>`
          : ""
      }
      ${
        copy.warningKey
          ? html`<div
              style="margin-top:8px;font-size:12px;color:var(--secondary-text-color);line-height:1.4;"
            >
              ${host._t(copy.warningKey, copy.warningFallback)}
            </div>`
          : ""
      }
      ${
        copy.confirm
          ? html`<div class="qa-group qa-group--confirmations">
              ${renderConfirmChip(
                host,
                {
                  label: host._t(
                    copy.confirm.labelKey,
                    copy.confirm.labelFallback,
                  ),
                  icon: copy.confirm.icon,
                  tone: copy.confirm.tone,
                },
                () => copy.confirm.run(host, msg, approval),
              )}
            </div>`
          : ""
      }
    </div>
  `;
}

function _renderDeleteRow(host, d) {
  const label = d.label || d.entity_id || d.target_id || "";
  const entityId = d.entity_id || "";
  const kind = _DELETE_KIND_LABELS[d.kind] || "";
  return html`
    <div
      style="padding:10px 0;border-top:1px solid var(--divider-color);display:flex;align-items:center;gap:10px;"
    >
      <ha-icon
        icon=${_DELETE_KIND_ICONS[d.kind] || "mdi:robot-outline"}
        style="--mdc-icon-size:22px;color:var(--secondary-text-color);flex-shrink:0;"
      ></ha-icon>
      <div style="display:flex;flex-direction:column;min-width:0;">
        <span
          style="font-size:13px;font-weight:600;color:var(--primary-text-color);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
          title=${label}
          >${label}</span
        >
        <span
          style="font-size:11px;color:var(--secondary-text-color);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
          >${entityId || kind}</span
        >
      </div>
    </div>
  `;
}

function _renderActionTile(call) {
  const service = call?.service || "";
  const icon = actionIcon(service);
  // Verb shown inside the action tile — pull from the shared formatter
  // so the wording matches the Done bubble after the user approves.
  const { verb } = describeCall({ hass: { states: {} } }, call);
  return html`
    <div
      style="display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;padding:12px 10px;min-width:88px;border-radius:8px;background:var(--card-background-color, rgba(255,255,255,0.04));border:1px solid var(--divider-color);"
      title=${service}
    >
      <ha-icon
        icon=${icon}
        style="--mdc-icon-size:24px;color:var(--secondary-text-color);"
      ></ha-icon>
      <span
        style="font-size:12px;font-weight:600;color:var(--primary-text-color);text-align:center;line-height:1.2;"
        >${verb}</span
      >
    </div>
  `;
}

function _renderCallRow(host, call, reason) {
  const ids = callTargetEntityIds(call);
  const { targetText } = describeCall(host, call);

  // Right side: a real HA tile if we have entity targets, otherwise a
  // plain label (for notify/script/shell_command — no entity to show).
  const rightSide = ids.length
    ? html`
        <div
          class="selora-entity-grid"
          data-entity-ids=${ids.join(",")}
          data-no-features="true"
          style="flex:1;min-width:0;margin:0;"
        ></div>
      `
    : html`
        <div
          style="flex:1;min-width:0;padding:12px;border-radius:8px;background:var(--card-background-color, rgba(255,255,255,0.04));border:1px solid var(--divider-color);font-size:13px;color:var(--primary-text-color);"
        >
          ${targetText}
        </div>
      `;

  return html`
    <div
      style="padding:10px 0;border-top:1px solid var(--divider-color);display:flex;flex-direction:column;gap:8px;"
    >
      <div style="display:flex;align-items:center;gap:10px;">
        ${_renderActionTile(call)}
        <ha-icon
          icon="mdi:arrow-right"
          style="--mdc-icon-size:18px;color:var(--secondary-text-color);flex-shrink:0;"
        ></ha-icon>
        ${rightSide}
      </div>
      ${
        reason
          ? html`<div
              style="font-size:12px;color:var(--secondary-text-color);line-height:1.4;"
            >
              ${reason}
            </div>`
          : ""
      }
    </div>
  `;
}

// Collect distinct entity targets across all calls. Used to label the
// scope chip and to decide whether the chip should even appear (it's
// hidden for targetless services like notify/script/shell_command —
// there's nothing to scope to, and the Always grant is the wildcard
// either way).
function _proposalEntityIds(approval) {
  const seen = new Set();
  const ids = [];
  for (const call of approval?.calls || []) {
    for (const eid of callTargetEntityIds(call)) {
      if (typeof eid === "string" && !seen.has(eid)) {
        seen.add(eid);
        ids.push(eid);
      }
    }
  }
  return ids;
}

function _domainOfEntity(entityId) {
  return (entityId || "").split(".", 1)[0];
}

// Label used inside the chip when entity scope is "all". Singular
// domain wins ("All locks"); mixed-domain proposals fall back to a
// generic phrase. Always exact "Just <Friendly Name>" for the single
// entity case so the user sees which device they're actually granting.
function _scopeLabel(host, scope, entityIds) {
  if (!entityIds.length) return null;
  if (scope === "all") {
    const domains = new Set(entityIds.map(_domainOfEntity));
    if (domains.size === 1) {
      const d = [...domains][0];
      return `All ${d}s`;
    }
    return host._t("approval_scope_all_matching", "All matching entities");
  }
  if (entityIds.length === 1) {
    const friendly =
      host?.hass?.states?.[entityIds[0]]?.attributes?.friendly_name ||
      entityIds[0];
    return `Just ${friendly}`;
  }
  return host._t("approval_scope_just_these", "Just these entities");
}

/**
 * Render the approval card.
 *
 * @param {object} host             Panel element (for hass.states lookup)
 * @param {object} msg              The chat message this card belongs to
 *                                  (we stash _entityScope on it so the
 *                                  scope chip survives re-renders).
 * @param {object} approval         proposal payload from backend
 * @param {string} approvalStatus   "pending" | "resolving" | "approved" | "denied" | null
 */
/**
 * A card for work the panel performs itself.
 *
 * Deliberately a button rather than an automatic execution: this is a
 * privileged websocket command running under the signed-in user's account, and
 * the press is what makes it theirs. It is also what keeps Selora from
 * announcing a dashboard that does not exist yet — the reply is written before
 * the panel has done anything.
 */
/**
 * What the card says the action will do.
 *
 * Composed here rather than using the descriptor's `label`: that string is
 * built server-side in English, and rendering it directly would leave every
 * non-English panel with one English line in an otherwise translated card.
 * The descriptor carries the parts; the wording belongs to the frontend.
 */
function _actionLabel(host, action) {
  if (action.kind === "create_dashboard") {
    // `_t` does not interpolate, so the placeholders are filled here — the
    // same shape localizePlural uses for {count}.
    return host
      ._t(
        "client_action_create_dashboard",
        "Create the {title} dashboard at /{url}",
      )
      .replace("{title}", action.title || "")
      .replace("{url}", action.url_path || "");
  }
  return action.label || action.kind;
}

const _CLIENT_ACTION_ICONS = {
  create_dashboard: "mdi:view-dashboard-outline",
};

/** One proposed client action, in the shared card's row shape. */
function _renderClientActionRow(host, action) {
  return html`
    <div style="padding:8px 0;display:flex;align-items:center;gap:10px;">
      <ha-icon
        icon=${_CLIENT_ACTION_ICONS[action.kind] || "mdi:cog-outline"}
        style="--mdc-icon-size:22px;color:var(--secondary-text-color);flex-shrink:0;"
      ></ha-icon>
      <span
        style="font-size:13px;font-weight:600;color:var(--primary-text-color);min-width:0;overflow:hidden;text-overflow:ellipsis;"
        >${_actionLabel(host, action)}</span
      >
    </div>
  `;
}

export function renderApprovalCard(host, msg, approval, approvalStatus) {
  if (!approval) return "";
  // Confirmation shapes — no service calls, no risk level, no scope chip —
  // share one card and differ by variant. A client action is one of them: the
  // only difference that matters is that its button lives on the card, because
  // there is no server-side resolver for the panel's own work to call.
  if (_CONFIRM_VARIANTS[approval.approval_kind]) {
    return renderConfirmationCard(
      host,
      msg,
      approval,
      approvalStatus,
      approval.approval_kind,
    );
  }
  const level = (approval.risk_level || "low").toLowerCase();
  const { accent, icon, explainerKey, explainerFallback } =
    RISK_LEVEL_STYLES[level] || RISK_LEVEL_STYLES.low;
  const explainer = host._t(explainerKey, explainerFallback);
  const reasons = approval.risk_reasons || [];
  const calls = approval.calls || [];
  const entityIds = _proposalEntityIds(approval);
  // Default to "this" — least-privilege. An explicit click broadens
  // to "all", and that broadening should never happen by accident.
  const scope = msg?._entityScope === "all" ? "all" : "this";

  if (approvalStatus === "approved" || approvalStatus === "denied") {
    const resolvedColor =
      approvalStatus === "approved" ? "#10b981" : "var(--secondary-text-color)";
    const resolvedIcon =
      approvalStatus === "approved"
        ? "mdi:check-circle-outline"
        : "mdi:close-circle-outline";
    return html`
      <div
        style="margin-top:10px;display:flex;align-items:center;gap:8px;font-size:12px;color:${resolvedColor};"
      >
        <ha-icon
          icon=${resolvedIcon}
          style="--mdc-icon-size:16px;flex-shrink:0;"
        ></ha-icon>
        <span
          >${
            approvalStatus === "approved"
              ? host._t("approval_status_approved", "Approved")
              : host._t("approval_status_denied", "Denied")
          }</span
        >
      </div>
    `;
  }

  if (approvalStatus === "resolving") {
    return html`
      <div
        style="margin-top:10px;display:flex;align-items:center;gap:8px;font-size:12px;color:var(--secondary-text-color);"
      >
        <span class="spinner" style="width:14px;height:14px;"></span>
        <span>${host._t("approval_working", "Working…")}</span>
      </div>
    `;
  }

  // Strictly per-index. A mixed proposal (SAFE calls bundled with the
  // REVIEW one that triggered the card) ships empty strings for the
  // SAFE positions; falling back to the last reason would tag the
  // SAFE row with the REVIEW call's "physical access risk" copy.
  const reasonFor = (i) => reasons[i] || "";

  return html`
    <div
      style="margin-top:12px;border:1px solid var(--divider-color);border-left:3px solid ${accent};border-radius:8px;padding:12px 14px;background:var(--card-background-color, rgba(255,255,255,0.02));"
    >
      <div
        style="display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:var(--primary-text-color);padding-bottom:10px;"
      >
        <ha-icon
          icon=${icon}
          style="--mdc-icon-size:16px;color:${accent};flex-shrink:0;"
        ></ha-icon>
        <span>${host._t("approval_required_title", "Approval required")}</span>
        <span
          title=${explainer}
          style="margin-left:auto;font-size:10px;font-weight:700;letter-spacing:0.06em;padding:2px 6px;border-radius:4px;color:${accent};border:1px solid ${accent};line-height:1.2;cursor:help;"
          >${level.toUpperCase()}</span
        >
      </div>
      <div style="display:flex;flex-direction:column;">
        ${calls.map((c, i) => _renderCallRow(host, c, reasonFor(i)))}
      </div>
      ${
        entityIds.length
          ? html`
              <div
                style="margin-top:10px;padding-top:10px;border-top:1px solid var(--divider-color);display:flex;align-items:center;gap:8px;font-size:12px;color:var(--secondary-text-color);"
              >
                <span
                  >${host._t(
                    "approval_scope_label",
                    "For Session / Always:",
                  )}</span
                >
                <button
                  @click=${() => host._toggleApprovalScope?.(msg)}
                  title=${host._t(
                    "approval_scope_button_title",
                    "Click to switch between scoping the grant to just this entity, or to all entities of this service.",
                  )}
                  style="display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;border:1px solid var(--divider-color);background:transparent;color:var(--primary-text-color);font-size:12px;cursor:pointer;"
                >
                  <ha-icon
                    icon=${scope === "all" ? "mdi:select-group" : "mdi:target"}
                    style="--mdc-icon-size:14px;color:${
                      scope === "all" ? "#f59e0b" : "#10b981"
                    };"
                  ></ha-icon>
                  <span>${_scopeLabel(host, scope, entityIds)}</span>
                  <ha-icon
                    icon="mdi:chevron-down"
                    style="--mdc-icon-size:14px;opacity:0.6;"
                  ></ha-icon>
                </button>
              </div>
            `
          : ""
      }
    </div>
  `;
}
