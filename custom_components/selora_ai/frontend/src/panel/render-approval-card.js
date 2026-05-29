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
import { actionIcon, describeCall } from "./action-format.js";

const RISK_LEVEL_STYLES = {
  low: {
    accent: "#3b82f6",
    icon: "mdi:information-outline",
    explainer:
      "Low risk: minor or fully reversible impact (sound, notifications, " +
      "vacuum start/stop).",
  },
  medium: {
    accent: "#f59e0b",
    icon: "mdi:alert-outline",
    explainer:
      "Medium risk: noticeable side effects you may not want to undo " +
      "(arming the alarm, locking a door, running a user script).",
  },
  high: {
    accent: "#ef4444",
    icon: "mdi:shield-alert-outline",
    explainer:
      "High risk: physical access, security, or host-level impact " +
      "(unlocking a door, disarming the alarm, running shell commands).",
  },
};

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
  const target = call?.target?.entity_id;
  const ids = Array.isArray(target) ? target : target ? [target] : [];
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
      ${reason
        ? html`<div
            style="font-size:12px;color:var(--secondary-text-color);line-height:1.4;"
          >
            ${reason}
          </div>`
        : ""}
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
    const t = call?.target?.entity_id;
    const list = Array.isArray(t) ? t : t ? [t] : [];
    for (const eid of list) {
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
    return "All matching entities";
  }
  if (entityIds.length === 1) {
    const friendly =
      host?.hass?.states?.[entityIds[0]]?.attributes?.friendly_name ||
      entityIds[0];
    return `Just ${friendly}`;
  }
  return "Just these entities";
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
export function renderApprovalCard(host, msg, approval, approvalStatus) {
  if (!approval) return "";
  const level = (approval.risk_level || "low").toLowerCase();
  const { accent, icon, explainer } =
    RISK_LEVEL_STYLES[level] || RISK_LEVEL_STYLES.low;
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
        <span>${approvalStatus === "approved" ? "Approved" : "Denied"}</span>
      </div>
    `;
  }

  if (approvalStatus === "resolving") {
    return html`
      <div
        style="margin-top:10px;display:flex;align-items:center;gap:8px;font-size:12px;color:var(--secondary-text-color);"
      >
        <span class="spinner" style="width:14px;height:14px;"></span>
        <span>Working…</span>
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
        <span>Approval required</span>
        <span
          title=${explainer}
          style="margin-left:auto;font-size:10px;font-weight:700;letter-spacing:0.06em;padding:2px 6px;border-radius:4px;color:${accent};border:1px solid ${accent};line-height:1.2;cursor:help;"
          >${level.toUpperCase()}</span
        >
      </div>
      <div style="display:flex;flex-direction:column;">
        ${calls.map((c, i) => _renderCallRow(host, c, reasonFor(i)))}
      </div>
      ${entityIds.length
        ? html`
            <div
              style="margin-top:10px;padding-top:10px;border-top:1px solid var(--divider-color);display:flex;align-items:center;gap:8px;font-size:12px;color:var(--secondary-text-color);"
            >
              <span>For Session / Always:</span>
              <button
                @click=${() => host._toggleApprovalScope?.(msg)}
                title="Click to switch between scoping the grant to just this entity, or to all entities of this service."
                style="display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;border:1px solid var(--divider-color);background:transparent;color:var(--primary-text-color);font-size:12px;cursor:pointer;"
              >
                <ha-icon
                  icon=${scope === "all" ? "mdi:select-group" : "mdi:target"}
                  style="--mdc-icon-size:14px;color:${scope === "all"
                    ? "#f59e0b"
                    : "#10b981"};"
                ></ha-icon>
                <span>${_scopeLabel(host, scope, entityIds)}</span>
                <ha-icon
                  icon="mdi:chevron-down"
                  style="--mdc-icon-size:14px;opacity:0.6;"
                ></ha-icon>
              </button>
            </div>
          `
        : ""}
    </div>
  `;
}
