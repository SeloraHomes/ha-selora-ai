// Arrival reveal for a freshly-proposed automation card.
//
// The card rises in, then builds itself one piece at a time in reading order
// — header, each trigger / condition / action node and the arrows between
// them (nested branches included), the YAML footer, and finally the
// Accept & Save row — while a gold sparkle field fades out over the top.
// Styles live in panel/styles/proposals.css.js under "Proposal arrival
// reveal".

import { html } from "lit";

// What builds, besides the card's header and footer. Branch containers are
// pieces too, so an if/choose box appears before the steps inside it.
const FLOW_PIECES = [
  ".proposal-status",
  ".flow-label",
  ".flow-node",
  ".flow-arrow",
  ".flow-arrow-sm",
  ".flow-branch",
  ".flow-branch-label",
  ".flow-off-wrap",
  ".flow-off-wrap-label",
].join(",");

// The first piece waits for the card to start rising. Pieces then follow one
// step apart, but a long automation compresses its steps so the last piece
// still starts within BUILD_SPAN_MS — the user is waiting on this card.
export const BUILD_START_MS = 220;
export const BUILD_STEP_MS = 140;
export const BUILD_SPAN_MS = 1500;
// Mirrors the animation duration on [data-build] in proposals.css.js.
export const BUILD_PIECE_MS = 450;

// Must outlast the last piece's delay + duration. The extra tail lets the
// particle fade finish before the element leaves the template.
export const REVEAL_TOTAL_MS =
  BUILD_START_MS + BUILD_SPAN_MS + BUILD_PIECE_MS + 250;

// Delay for each of `count` pieces, in order.
export function buildDelays(count) {
  if (count <= 0) return [];
  const step =
    count > 1 ? Math.min(BUILD_STEP_MS, BUILD_SPAN_MS / (count - 1)) : 0;
  return Array.from({ length: count }, (_, i) =>
    Math.round(BUILD_START_MS + i * step),
  );
}

// Tag a rendered proposal card's pieces with their build delay. The pieces
// are found in the DOM rather than numbered in the templates because the
// flowchart renders recursively — a counter threaded through every nested
// branch would touch each template for a purely visual concern. Returns the
// tagged elements so the caller can untag them once the build is over.
export function stageProposalBuild(card) {
  if (!card) return [];
  const pieces = [
    card.querySelector(":scope > .automation-subcard-header"),
    ...card.querySelectorAll(FLOW_PIECES),
    card.querySelector(":scope > .automation-subcard-footer"),
    // Accept & Save sits outside the card, in the bubble's action row; it
    // lands last, once there is something to accept.
    card.closest(".assistant-wrap")?.querySelector(":scope > .bubble-meta"),
  ].filter(Boolean);
  buildDelays(pieces.length).forEach((delay, i) => {
    pieces[i].setAttribute("data-build", "");
    pieces[i].style.setProperty("--build-delay", `${delay}ms`);
  });
  return pieces;
}

// Untag the pieces so a later re-render (or a reopened session) never
// replays the build. Their animations have finished on their natural state,
// so dropping the attribute changes nothing on screen.
export function clearProposalBuild(pieces) {
  for (const el of pieces || []) {
    el.removeAttribute("data-build");
    el.style.removeProperty("--build-delay");
  }
}

// Play the arrival reveal on a proposal card. Called from the chat stream's
// `done` handler, so it fires when a proposal arrives and never when history
// re-renders the same pending card.
//
// The flag is cleared on a timer rather than left set, because it also gates
// whether <selora-particles> is in the template — dropping the element ends
// its requestAnimationFrame loop. Leaving it mounted would keep one canvas
// animating per proposal card for the life of the session.
export function _markProposalRevealing(msgIndex) {
  if (msgIndex == null || msgIndex < 0) return;
  this._revealTimers = this._revealTimers || {};
  this._revealPieces = this._revealPieces || {};
  if (this._revealTimers[msgIndex]) clearTimeout(this._revealTimers[msgIndex]);
  clearProposalBuild(this._revealPieces[msgIndex]);
  this._revealingProposals = {
    ...this._revealingProposals,
    [msgIndex]: true,
  };
  // Setting the flag schedules the render that draws the card, and
  // updateComplete resolves after it but before the browser paints, so the
  // pieces are tagged before any of them shows.
  Promise.resolve(this.updateComplete).then(() => {
    if (!this._revealingProposals?.[msgIndex]) return;
    this._revealPieces[msgIndex] = stageProposalBuild(
      this.shadowRoot?.querySelector(
        `.automation-subcard[data-reveal="${msgIndex}"]`,
      ),
    );
  });
  this._revealTimers[msgIndex] = setTimeout(() => {
    const { [msgIndex]: _done, ...rest } = this._revealingProposals;
    this._revealingProposals = rest;
    clearProposalBuild(this._revealPieces[msgIndex]);
    delete this._revealPieces[msgIndex];
    delete this._revealTimers[msgIndex];
    this.requestUpdate();
  }, REVEAL_TOTAL_MS);
}

// Teardown for a panel leaving the page mid-reveal: cancel the timers and
// untag the pieces, or reconnecting would replay their build.
export function _clearProposalReveals() {
  for (const timer of Object.values(this._revealTimers || {})) {
    clearTimeout(timer);
  }
  for (const pieces of Object.values(this._revealPieces || {})) {
    clearProposalBuild(pieces);
  }
  this._revealTimers = {};
  this._revealPieces = {};
  this._revealingProposals = {};
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
