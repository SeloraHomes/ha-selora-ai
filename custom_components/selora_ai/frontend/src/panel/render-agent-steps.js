import { html } from "lit";

// Agent-activity timeline — the PostHog-style "what's happening" rail shown
// ABOVE an assistant reply (not crammed inside the answer card). Steps arrive
// over the chat stream as {type:"step"} events (see __init__._emit_step /
// agent_steps.py), are accumulated on the message as msg.steps, and restored
// on session reload.
//
// Each step: { id, kind, label, status, detail?, icon? }
//   kind:   tool | draft | validate | correct | info | done | error
//   status: active | done | warn | error

const STATUS_ICON = {
  active: "mdi:loading",
  done: "mdi:check-circle-outline",
  warn: "mdi:alert-circle-outline",
  error: "mdi:close-circle-outline",
};

// Fallback glyph by kind when a step carries no explicit icon and its status
// doesn't dictate one.
const KIND_ICON = {
  tool: "mdi:cog-outline",
  draft: "mdi:pencil-outline",
  validate: "mdi:shield-check-outline",
  correct: "mdi:autorenew",
  info: "mdi:information-outline",
};

const STATUS_SEVERITY = { done: 0, active: 1, warn: 2, error: 3 };

function _stepColor(status) {
  // Only attention states get colour. Completed/neutral steps stay in the
  // secondary text colour so the timeline reads as a quiet trail.
  if (status === "warn") return "var(--warning-color, #f59e0b)";
  if (status === "error") return "var(--error-color, #ef4444)";
  return "var(--secondary-text-color)";
}

function _stepIcon(step) {
  // Attention states own the glyph (spinner / alert) regardless of the step's
  // own icon, so a warning still reads as a warning.
  if (step.status === "active") return STATUS_ICON.active;
  if (step.status === "warn") return STATUS_ICON.warn;
  if (step.status === "error") return STATUS_ICON.error;
  // Otherwise prefer the backend-supplied per-tool icon (magnifier for a
  // search, eye for a state read…), falling back to a kind icon.
  return step.icon || KIND_ICON[step.kind] || STATUS_ICON.done;
}

// Collapse repeats: the model often calls the same read tool across several
// rounds ("Listed your devices" ×3). Keep one row per (kind+label), in
// first-seen order, carrying the most severe status seen for that key so a
// later warning isn't hidden by an earlier success.
function _dedupeSteps(steps) {
  const byKey = new Map();
  for (const step of steps) {
    if (!step || !step.label) continue;
    const key = `${step.kind || ""}::${step.label}`;
    const prev = byKey.get(key);
    if (!prev) {
      byKey.set(key, { ...step });
      continue;
    }
    if (
      (STATUS_SEVERITY[step.status] ?? 0) > (STATUS_SEVERITY[prev.status] ?? 0)
    ) {
      prev.status = step.status;
      if (step.detail) prev.detail = step.detail;
    }
  }
  return [...byKey.values()];
}

// Render the activity timeline for a message, or "" when there are no steps.
// A thin vertical rail connects the icon nodes so it reads as a process trail.
export function renderAgentSteps(host, steps) {
  if (!Array.isArray(steps) || steps.length === 0) return "";
  const items = _dedupeSteps(steps);
  if (items.length === 0) return "";
  const lastIndex = items.length - 1;
  return html`
    <div
      class="agent-steps"
      style="display:flex;flex-direction:column;gap:7px;margin:2px 2px 10px;"
    >
      ${items.map((step, i) => {
        const color = _stepColor(step.status);
        const spinning = step.status === "active";
        const emphasised = step.status === "warn" || step.status === "error";
        // A plain flex row with align-items:center lets flexbox centre the
        // icon against the text line directly — robust to ha-icon's internal
        // SVG box and font metrics, where a fixed-height wrapper drifted. A
        // relatively-positioned icon cell carries a thin connector to the
        // next node (absolute, so it never disturbs the centring).
        const showRail = i !== lastIndex;
        return html`
          <div
            class="agent-step"
            style="display:flex;align-items:center;gap:9px;"
            title=${step.detail || ""}
          >
            <div
              style="position:relative;width:16px;height:16px;flex-shrink:0;display:flex;align-items:center;justify-content:center;"
            >
              ${
                showRail
                  ? html`<span
                      style="position:absolute;left:50%;top:15px;height:11px;width:1px;background:var(--divider-color);transform:translateX(-50%);"
                    ></span>`
                  : ""
              }
              <ha-icon
                icon=${_stepIcon(step)}
                class=${spinning ? "agent-step-spin" : ""}
                style="--mdc-icon-size:16px;color:${color};"
              ></ha-icon>
            </div>
            <span
              style="font-size:12px;line-height:1.3;color:${
                emphasised ? color : "var(--secondary-text-color)"
              };${emphasised ? "" : "opacity:0.9;"}"
              >${step.label}</span
            >
          </div>
        `;
      })}
    </div>
  `;
}
