// Arrival reveal for a freshly-proposed automation card.
//
// The card rises in, its flow nodes stagger in reading order (trigger →
// arrow → actions), and a gold sparkle field fades out over the top so the
// proposal reads as having condensed out of it. Styles live in
// panel/styles/proposals.css.js under .automation-subcard.revealing.

import { html } from "lit";

// Must outlast the longest CSS delay + duration in the .revealing block:
// the last flow node starts at 460ms and runs 300ms. The extra tail lets the
// particle fade finish before the element leaves the template.
export const REVEAL_TOTAL_MS = 1400;

// Play the arrival reveal on a proposal card: the subcard rises in, its flow
// nodes stagger in reading order, and a sparkle field fades out over the top.
// Called from the chat stream's `done` handler, so it fires when a proposal
// arrives and never when history re-renders the same pending card.
//
// The flag is cleared on a timer rather than left set, because it also gates
// whether <selora-particles> is in the template — dropping the element ends
// its requestAnimationFrame loop. Leaving it mounted would keep one canvas
// animating per proposal card for the life of the session.
export function _markProposalRevealing(msgIndex) {
  if (msgIndex == null || msgIndex < 0) return;
  this._revealTimers = this._revealTimers || {};
  if (this._revealTimers[msgIndex]) clearTimeout(this._revealTimers[msgIndex]);
  this._revealingProposals = {
    ...this._revealingProposals,
    [msgIndex]: true,
  };
  this._revealTimers[msgIndex] = setTimeout(() => {
    const { [msgIndex]: _done, ...rest } = this._revealingProposals;
    this._revealingProposals = rest;
    delete this._revealTimers[msgIndex];
    this.requestUpdate();
  }, REVEAL_TOTAL_MS);
}

// Sparkle field for the reveal. Rendered only while the reveal is playing —
// see _markProposalRevealing, which drops it so the engine's rAF loop stops.
// Count is far below the ambient background field: this is a small card and
// the canvas sits behind live text that has to stay readable.
export function renderRevealParticles(host) {
  return html`
    <selora-particles
      class="proposal-reveal-particles"
      .count=${90}
      .color=${host._isDark ? "#fbbf24" : host._primaryColor || "#03a9f4"}
      .maxOpacity=${host._isDark ? 0.5 : 0.4}
      .speed=${2.4}
    ></selora-particles>
  `;
}
