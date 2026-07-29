// Checkmark that draws its own stroke — the shared "created" signal for
// saved automation and scene cards.
//
// `animate` is opt-in rather than always-on: the saved card renders on every
// session load, and replaying the draw each time would claim something was
// just created when it was created last week. Callers pass animate only when
// the create round-trip landed in this session (see _markJustCreated).

import { html } from "lit";

export function renderCreatedCheck({ animate = false, size = 14 } = {}) {
  return html`
    <svg
      class="created-check${animate ? " drawing" : ""}"
      viewBox="0 0 24 24"
      width=${size}
      height=${size}
      aria-hidden="true"
      focusable="false"
    >
      <path d="M4 12.5 L9.5 18 L20 6.5" />
    </svg>
  `;
}
