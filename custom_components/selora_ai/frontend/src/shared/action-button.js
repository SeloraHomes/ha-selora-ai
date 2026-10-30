// The one button every choice in the chat is drawn with: a pill with the
// label, and a round badge whose mark says what the press does: an arrow
// sends a message to Selora, a tick agrees, a cross declines. Styles are in
// shared/styles/buttons.css.js (`.action-btn`); the click ripple is armed
// once for the whole panel by press-ripple.js.
import { html, svg } from "lit";

// Drawn as strokes, so hovering can redraw the tick and turn the cross.
const DRAWN_MARKS = {
  "mdi:check": svg`<path class="action-btn-tick" d="M5 12.5 L10 17.5 L19.5 7" />`,
  "mdi:close": svg`<path class="action-btn-cross" d="M7 7 L17 17 M17 7 L7 17" />`,
};

function renderMark(icon) {
  const drawn = DRAWN_MARKS[icon];
  return drawn
    ? html`<svg class="action-btn-svg" viewBox="0 0 24 24">${drawn}</svg>`
    : html`<ha-icon icon=${icon}></ha-icon>`;
}

/**
 * @param {object} opts
 * @param {string} opts.label
 * @param {Function} opts.onClick
 * @param {string} [opts.description] a second, quieter line under the label
 * @param {string} [opts.mark] mdi icon in the badge (default: the send arrow)
 * @param {"primary"|"quiet"|"decline"|"danger"} [opts.tone] gold, grey, grey
 *   with a red mark (a refusal), or red (a delete); one primary per row, so
 *   the default is plain
 * @param {boolean} [opts.picked] the answer given, lit after the row is used
 * @param {boolean} [opts.disabled]
 * @param {string} [opts.title]
 */
export function renderActionButton({
  label,
  onClick,
  description = "",
  mark = "mdi:arrow-up",
  tone = "quiet",
  picked = false,
  disabled = false,
  title = "",
}) {
  const cls = [
    "action-btn",
    `action-btn--${tone}`,
    description ? "action-btn--tall" : "",
    picked ? "action-btn--picked" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return html`
    <button
      class=${cls}
      title=${title || label}
      ?disabled=${disabled}
      @click=${onClick}
    >
      <span class="action-btn-text">
        <span class="action-btn-label">${label}</span>
        ${
          description
            ? html`<span class="action-btn-desc">${description}</span>`
            : ""
        }
      </span>
      <span class="action-btn-mark" aria-hidden="true"
        >${renderMark(mark)}</span
      >
    </button>
  `;
}
