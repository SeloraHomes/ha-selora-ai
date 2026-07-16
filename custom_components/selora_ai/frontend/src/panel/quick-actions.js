import { html } from "lit";

// Map approval-scope → presentation. Persisted messages from before the
// choice-card switch carry mode="confirmation" with no icon/tone, so we
// normalise every approval action here regardless of what the backend
// stored. Detection is by sentinel value prefix ``approve:<scope>:…``
// — that's the contract between backend and frontend, so it's stable
// across versions.
function _approvalPresentation(host) {
  return {
    once: {
      label: host._t("quick_actions_approve_once_label", "Allow once"),
      icon: "mdi:check",
      tone: "approve",
      description: host._t(
        "quick_actions_approve_once_desc",
        "Just this one request",
      ),
    },
    session: {
      label: host._t(
        "quick_actions_approve_session_label",
        "For this conversation",
      ),
      icon: "mdi:check-all",
      tone: "approve",
      description: host._t(
        "quick_actions_approve_session_desc",
        "Allow for the rest of this conversation",
      ),
    },
    always: {
      label: host._t("quick_actions_approve_always_label", "Always"),
      icon: "mdi:shield-check",
      tone: "approve",
      description: host._t(
        "quick_actions_approve_always_desc",
        "Remember this approval",
      ),
    },
    deny: {
      label: host._t("quick_actions_deny_label", "Deny"),
      icon: "mdi:close",
      tone: "deny",
      description: host._t(
        "quick_actions_deny_desc",
        "Do not run this request",
      ),
    },
  };
}

function _approvalScope(value) {
  if (typeof value !== "string" || !value.startsWith("approve:")) return null;
  return value.split(":", 2)[1] || null;
}

// Return a copy of *actions* where any approval-scope action is upgraded
// to the choice-card presentation (icon + tone + description). Untouched
// when no approval sentinels are present so existing AI-suggested
// choices keep their original payload.
function _normalizeApprovalActions(host, actions) {
  let touched = false;
  const presentation = _approvalPresentation(host);
  const out = actions.map((a) => {
    const scope = _approvalScope(a?.value);
    if (!scope) return a;
    const preset = presentation[scope];
    if (!preset) return a;
    touched = true;
    return {
      ...a,
      // Override label too — older persisted messages may have shipped
      // "Session" / "Allow once" wording, but we want the new copy
      // ("For this conversation") to appear consistently.
      label: preset.label,
      mode: "choice",
      icon: a.icon || preset.icon,
      tone: a.tone || preset.tone,
      description: a.description || preset.description,
    };
  });
  return touched ? out : actions;
}

function _isApprovalGroup(actions) {
  return actions.some((a) => _approvalScope(a?.value));
}

/**
 * Render quick-action buttons below a chat message or in the welcome screen.
 *
 * Each action object:
 *   { label, value?, description?, icon?, mode?, primary? }
 *
 * Modes (auto-detected from the first item if omitted):
 *   - "suggestion"    → full-width chip (welcome quick-start)
 *   - "choice"        → grid card with title + description
 *   - "confirmation"  → inline button row (Apply / Modify / Cancel)
 *
 * @param {object}   host     Panel element (provides _selectQuickAction)
 * @param {object[]} actions  List of quick-action objects
 * @param {object}   [opts]   { used: boolean } — dims the group after selection
 */
export function renderQuickActions(host, actions, opts = {}) {
  if (!actions || !actions.length) return "";
  actions = _normalizeApprovalActions(host, actions);

  const mode = _detectMode(actions);
  const usedClass = opts.used ? " qa-group--used" : "";

  if (mode === "choice") {
    // Approval rows pack into a 2-column grid even on narrow widths
    // (qa-group--approval) so all four scopes fit in two rows. AI
    // option cards keep the wider minmax for legibility.
    const approvalClass = _isApprovalGroup(actions)
      ? " qa-group--approval"
      : "";
    return html`
      <div class="qa-group qa-group--choices${approvalClass}${usedClass}">
        ${actions.map((a) => _renderChoice(host, a))}
      </div>
    `;
  }

  if (mode === "confirmation") {
    return html`
      <div class="qa-group qa-group--confirmations${usedClass}">
        ${actions.map((a) => _renderConfirmation(host, a))}
      </div>
    `;
  }

  // Default: suggestion chips
  return html`
    <div class="qa-group${usedClass}">
      ${actions.map((a) => _renderSuggestion(host, a))}
    </div>
  `;
}

function _detectMode(actions) {
  const first = actions[0];
  if (first.mode) return first.mode;
  if (actions.some((a) => a.primary !== undefined)) return "confirmation";
  if (actions.some((a) => a.description)) return "choice";
  return "suggestion";
}

function _onSelect(host, action) {
  host._selectQuickAction(action);
}

function _renderSuggestion(host, action) {
  const leadingIcon = action.icon || "mdi:auto-fix";
  return html`
    <button class="qa-suggestion" @click=${() => _onSelect(host, action)}>
      <span class="qa-glow-track" aria-hidden="true">
        <span class="qa-glow-spot"></span>
      </span>
      <ha-icon class="qa-suggestion-lead" icon=${leadingIcon}></ha-icon>
      <span class="qa-suggestion-label">${action.label}</span>
      <ha-icon class="qa-suggestion-trail" icon="mdi:chevron-right"></ha-icon>
    </button>
  `;
}

// Override the default amber comet/border colour via --qa-spot-color
// for cards that want a semantic accent (green = approve, red = deny).
// We also paint a faint matching border so the colour is visible even
// before hover, and on browsers that don't run the comet animation.
const _TONE_OVERRIDES = {
  approve: {
    "--qa-spot-color": "#10b981",
    "--qa-border-color": "rgba(16, 185, 129, 0.35)",
    "--qa-bg-hover": "rgba(16, 185, 129, 0.10)",
    "--qa-border-hover": "rgba(16, 185, 129, 0.85)",
  },
  deny: {
    "--qa-spot-color": "#ef4444",
    "--qa-border-color": "rgba(239, 68, 68, 0.35)",
    "--qa-bg-hover": "rgba(239, 68, 68, 0.10)",
    "--qa-border-hover": "rgba(239, 68, 68, 0.85)",
  },
};

function _toneStyle(tone) {
  const vars = _TONE_OVERRIDES[tone];
  if (!vars) return "";
  return Object.entries(vars)
    .map(([k, v]) => `${k}:${v};`)
    .join("");
}

function _renderChoice(host, action) {
  const leadingIcon = action.icon || "mdi:auto-fix";
  const toneStyle = _toneStyle(action.tone);
  // For toned cards the trailing chevron is redundant — the colour
  // already signals the outcome — so swap it for the action's own
  // icon (check / close) at the right edge, which doubles the visual
  // cue without adding a third coloured element.
  const trailingIcon = action.tone
    ? action.tone === "deny"
      ? "mdi:close"
      : "mdi:check"
    : "mdi:chevron-right";
  // Toned choice cards (approval row) hide the description in a tooltip
  // so all four cards stay the same height. Untoned choice cards keep
  // showing the description inline — that's the original AI-suggested
  // option layout where the description carries real information.
  const tooltipDescription = action.tone && action.description;
  const inlineDescription = !action.tone && action.description;
  const cardTitle = tooltipDescription
    ? `${action.label} — ${action.description}`
    : action.label;
  return html`
    <div
      class="qa-choice"
      style=${toneStyle}
      title=${cardTitle}
      @click=${() => _onSelect(host, action)}
    >
      <span class="qa-glow-track" aria-hidden="true">
        <span class="qa-glow-spot"></span>
      </span>
      <div class="qa-choice-row">
        <ha-icon class="qa-choice-lead" icon=${leadingIcon}></ha-icon>
        <div class="qa-choice-text">
          <span class="qa-choice-label" title=${action.label}
            >${action.label}</span
          >
          ${
            inlineDescription
              ? html`<span class="qa-choice-desc">${action.description}</span>`
              : ""
          }
        </div>
        <ha-icon class="qa-choice-trail" icon=${trailingIcon}></ha-icon>
      </div>
    </div>
  `;
}

function _renderConfirmation(host, action) {
  // "tone" lets the card-emitter (e.g. command approval row) pre-classify
  // the chip as approve/deny so we can colour the icon and border without
  // shipping per-call inline styles. Falls back to the generic primary
  // styling when unset, preserving behaviour for existing callers.
  const tone = action.tone || (action.primary ? "primary" : null);
  const toneClass = tone ? ` qa-confirm--${tone}` : "";
  const cls = `qa-confirm${toneClass}`;
  const iconStyle =
    tone === "approve"
      ? "color:#10b981;"
      : tone === "deny"
        ? "color:#ef4444;"
        : "";
  return html`
    <button class=${cls} @click=${() => _onSelect(host, action)}>
      ${
        action.icon
          ? html`<ha-icon
              icon=${action.icon}
              style="--mdc-icon-size:16px;${iconStyle}"
            ></ha-icon>`
          : ""
      }
      <span style="display:flex;flex-direction:column;align-items:flex-start;">
        <span class="qa-confirm-label">${action.label}</span>
        ${
          action.description
            ? html`<span class="qa-confirm-desc">${action.description}</span>`
            : ""
        }
      </span>
    </button>
  `;
}
