import { html } from "lit";

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

  const mode = _detectMode(actions);
  const usedClass = opts.used ? " qa-group--used" : "";

  if (mode === "choice") {
    return html`
      <div class="qa-group qa-group--choices${usedClass}">
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
  return html`
    <button class="qa-suggestion" @click=${() => _onSelect(host, action)}>
      ${action.icon ? html`<ha-icon icon=${action.icon}></ha-icon>` : ""}
      ${action.label}
    </button>
  `;
}

function _renderChoice(host, action) {
  return html`
    <div class="qa-choice" @click=${() => _onSelect(host, action)}>
      <span class="qa-choice-label">${action.label}</span>
      ${action.description
        ? html`<span class="qa-choice-desc">${action.description}</span>`
        : ""}
    </div>
  `;
}

function _renderConfirmation(host, action) {
  const cls = action.primary ? "qa-confirm qa-confirm--primary" : "qa-confirm";
  return html`
    <button class=${cls} @click=${() => _onSelect(host, action)}>
      ${action.icon ? html`<ha-icon icon=${action.icon}></ha-icon>` : ""}
      ${action.label}
    </button>
  `;
}
