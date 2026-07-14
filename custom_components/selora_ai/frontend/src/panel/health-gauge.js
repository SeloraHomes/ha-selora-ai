import { html } from "lit";

// Animated radial health gauge for the Health page hero.
//   A 270° arc that sweeps from empty to the deterministic score on load,
//   coloured by the A–F band, with the score + band shown in the centre.
//   Pure CSS animation (stroke-dashoffset keyframe) — no JS timers — so it
//   replays whenever the score element is (re)created by a fresh audit.

const R = 52; // arc radius within the 120×120 viewBox
const CIRC = 2 * Math.PI * R; // full circumference
const SPAN = 0.75; // fraction of the circle the arc covers (270°, gap at bottom)
const TRACK_LEN = CIRC * SPAN;

// Band → arc colour. Greens for healthy, amber/orange for degraded, red for
// failing — the same semantic ramp as the finding severities.
const _BAND_COLOR = {
  A: "#22c55e",
  B: "#4ade80",
  C: "#f59e0b",
  D: "#f97316",
  F: "#ef4444",
};

function _bandColor(band) {
  return _BAND_COLOR[band] || _BAND_COLOR.A;
}

export function renderHealthGauge(host) {
  const score = host._auditScore;
  if (typeof score !== "number") return "";
  const band = host._auditBand || "A";
  const color = _bandColor(band);
  const pct = Math.max(0, Math.min(100, score)) / 100;
  const valueLen = TRACK_LEN * pct;
  // Re-key the SVG on the score so a new audit rebuilds the node and the
  // sweep animation runs again instead of snapping to the new value.
  return html`
    <div
      class="health-gauge"
      style="--gauge-color:${color};"
      title=${host._t("insights_score_title", "Deterministic health score")}
    >
      <svg viewBox="0 0 120 120" class="health-gauge-svg" aria-hidden="true">
        <circle
          class="health-gauge-track"
          cx="60"
          cy="60"
          r=${R}
          stroke-dasharray="${TRACK_LEN} ${CIRC}"
        ></circle>
        <circle
          class="health-gauge-value"
          cx="60"
          cy="60"
          r=${R}
          stroke-dasharray="${valueLen} ${CIRC}"
          style="--gauge-len:${valueLen};"
        ></circle>
      </svg>
      <div class="health-gauge-center">
        <span class="health-gauge-score">${Math.round(score)}</span>
        <span class="health-gauge-max">/100</span>
      </div>
    </div>
  `;
}
