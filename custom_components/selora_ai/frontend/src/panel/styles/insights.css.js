import { css } from "lit";

export const insightsStyles = css`
  .page-sub {
    font-size: 12px;
    opacity: 0.6;
    margin-top: 4px;
  }
  .btn-sm {
    padding: 4px 10px;
    font-size: 12px;
    /* Constrain the icon: without this ha-icon defaults to 24px, dwarfing the
       12px label and clashing with the 13px Re-run button icon. */
    --mdc-icon-size: 15px;
  }

  .insights-tiles {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
    margin: 16px 0;
  }
  .insights-tile {
    background: var(--card-background-color, rgba(255, 255, 255, 0.04));
    border-radius: 10px;
    padding: 14px 12px;
    text-align: center;
  }
  .insights-tile-value {
    font-size: 26px;
    font-weight: 700;
    line-height: 1;
  }
  .insights-tile-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    opacity: 0.7;
    margin-top: 6px;
  }

  .insights-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .insight-card {
    display: flex;
    background: var(--card-background-color, rgba(255, 255, 255, 0.04));
    border-radius: 10px;
    overflow: hidden;
    transition: opacity 0.2s ease;
  }
  .insight-card.acknowledged {
    opacity: 0.6;
  }
  .insight-card-severity {
    flex: 0 0 4px;
  }
  .insight-card-body {
    flex: 1 1 auto;
    padding: 12px 14px;
    min-width: 0;
  }
  .insight-card-head {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .insight-card-title {
    font-weight: 600;
    font-size: 14px;
    flex: 1 1 auto;
    min-width: 0;
  }
  .insight-kind-badge {
    flex: 0 0 auto;
    text-transform: uppercase;
    font-size: 10px;
    letter-spacing: 0.04em;
  }
  .insight-card-detail {
    font-size: 13px;
    opacity: 0.8;
    margin-top: 6px;
    line-height: 1.4;
  }
  .insight-card-actions {
    display: flex;
    gap: 8px;
    margin-top: 10px;
    align-items: center;
  }
  .insight-ack-note {
    font-size: 12px;
    opacity: 0.6;
  }

  /* ── Audit card (primary) ── */
  .insight-audit-card {
    background: var(--card-background-color, rgba(255, 255, 255, 0.04));
    border-radius: 12px;
    padding: 18px 20px;
    margin: 16px 0;
  }
  .insight-audit-md {
    font-size: 14px;
    line-height: 1.55;
  }
  .insight-audit-md h1,
  .insight-audit-md h2,
  .insight-audit-md h3,
  .insight-audit-md h4 {
    font-size: 15px;
    font-weight: 600;
    margin: 16px 0 6px;
  }
  .insight-audit-md ul {
    margin: 6px 0;
    padding-left: 18px;
  }
  .insight-audit-md li {
    margin: 3px 0;
  }
  .insight-audit-md .selora-entity-grid {
    margin: 6px 0;
  }
  .insight-audit-actions {
    margin-top: 16px;
  }
  .insight-audit-status {
    font-size: 13px;
    opacity: 0.75;
    padding: 8px 0;
  }
  .insight-audit-err {
    font-size: 12px;
    opacity: 0.6;
    margin-top: 6px;
    font-family: var(--code-font-family, monospace);
  }
  .insight-audit-running {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .insight-audit-hint {
    font-size: 12px;
    opacity: 0.55;
    margin-top: 2px;
  }

  /* ── Audit recommendation cards ── */
  .audit-cards {
    display: flex;
    flex-direction: column;
    gap: 10px;
    margin: 16px 0;
  }
  .audit-card-tiles {
    margin: 8px 0 2px;
  }
  .insights-section-label {
    margin: 20px 0 -4px;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    opacity: 0.55;
  }
  .page-header-spacer {
    flex: 1 1 auto;
  }
  /* ── Health score gauge (hero) ── */
  .health-gauge {
    position: relative;
    width: 148px;
    height: 148px;
    margin: 8px auto 20px;
  }
  .health-gauge-svg {
    width: 100%;
    height: 100%;
    display: block;
  }
  .health-gauge-track,
  .health-gauge-value {
    fill: none;
    stroke-width: 9;
    stroke-linecap: round;
    /* Start at 3 o'clock; rotate so the 90° gap sits centred at the bottom. */
    transform: rotate(135deg);
    transform-box: fill-box;
    transform-origin: center;
  }
  .health-gauge-track {
    stroke: var(--divider-color, rgba(255, 255, 255, 0.1));
  }
  .health-gauge-value {
    stroke: var(--gauge-color, #22c55e);
    filter: drop-shadow(
      0 0 5px color-mix(in srgb, var(--gauge-color) 55%, transparent)
    );
    animation: health-gauge-sweep 1.15s cubic-bezier(0.22, 1, 0.36, 1) forwards;
  }
  @keyframes health-gauge-sweep {
    from {
      stroke-dashoffset: var(--gauge-len);
    }
    to {
      stroke-dashoffset: 0;
    }
  }
  .health-gauge-center {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    line-height: 1;
    animation: health-gauge-pop 0.6s ease-out both;
  }
  @keyframes health-gauge-pop {
    from {
      opacity: 0;
      transform: scale(0.8);
    }
    to {
      opacity: 1;
      transform: scale(1);
    }
  }
  .health-gauge-score {
    font-size: 40px;
    font-weight: 700;
    color: var(--gauge-color, #22c55e);
    letter-spacing: -0.02em;
  }
  .health-gauge-max {
    margin-top: 2px;
    font-size: 13px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    opacity: 0.5;
  }
  @media (prefers-reduced-motion: reduce) {
    .health-gauge-value {
      animation: none;
      stroke-dashoffset: 0;
    }
    .health-gauge-center {
      animation: none;
    }
  }
  .check-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  /* Each check is a bounded row-card so the title and its status read as one
     unit instead of a label and a far-away word across empty space. */
  .check-item {
    background: var(--card-background-color, rgba(255, 255, 255, 0.04));
    border-radius: 10px;
    padding: 12px 14px;
    transition: background 0.12s ease;
    /* Anchored from the breakdown links — offset the scroll target so the row
       doesn't land flush under the sticky page chrome. */
    scroll-margin-top: 16px;
  }
  /* Brief highlight when a breakdown link jumps here, so the eye lands on the
     right row instead of hunting the list. */
  .check-item-flash {
    animation: check-item-flash 1.2s ease-out;
  }
  @keyframes check-item-flash {
    0%,
    30% {
      background: color-mix(
        in srgb,
        var(--warning-color, #ffa600) 22%,
        var(--card-background-color, rgba(255, 255, 255, 0.04))
      );
    }
    100% {
      background: var(--card-background-color, rgba(255, 255, 255, 0.04));
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .check-item-flash {
      animation: none;
    }
  }
  /* Only the collapsed (clear) rows brighten on hover — a whole-card tint on an
     expanded issue row would wash out the finding sub-cards nested inside it. */
  .check-item:not(.check-item-issues):hover {
    background: color-mix(
      in srgb,
      var(--primary-text-color, #fff) 7%,
      var(--card-background-color, rgba(255, 255, 255, 0.04))
    );
  }
  .check-head {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .check-icon {
    --mdc-icon-size: 18px;
    flex: 0 0 auto;
  }
  .check-clear {
    color: var(--success-color, #43a047);
  }
  .check-issues {
    color: var(--warning-color, #ffa600);
  }
  /* A check that failed to run — muted, distinct from clear (green) so it never
     reads as "assessed and healthy". */
  .check-error {
    color: var(--secondary-text-color, #9e9e9e);
  }
  .check-title {
    font-weight: 600;
    font-size: 14px;
    flex: 1 1 auto;
    min-width: 0;
  }
  /* Color-coded status chip — the color echoes the row icon so the outcome is
     legible at a glance without tracing across the row. */
  .check-badge {
    flex: 0 0 auto;
    font-size: 11px;
    font-weight: 600;
    padding: 3px 9px;
    border-radius: 999px;
    white-space: nowrap;
  }
  .check-badge.clear {
    color: var(--success-color, #43a047);
    background: color-mix(
      in srgb,
      var(--success-color, #43a047) 14%,
      transparent
    );
  }
  .check-badge.issues {
    color: var(--warning-color, #ffa600);
    background: color-mix(
      in srgb,
      var(--warning-color, #ffa600) 16%,
      transparent
    );
  }
  .check-badge.error {
    color: var(--secondary-text-color, #9e9e9e);
    background: color-mix(
      in srgb,
      var(--secondary-text-color, #9e9e9e) 14%,
      transparent
    );
  }
  .check-ai {
    font-size: 10px;
  }
  .check-findings {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin: 12px 0 0;
  }
  /* Finding cards sit inside the (same-coloured) row-card, so lift them a
     shade to stay distinct. Use a text-on-card mix rather than
     --secondary-background-color, which is a heavy mid-grey in light mode; this
     stays a subtle tint in both themes (slightly darker on light, lighter on
     dark). */
  .check-findings .audit-card {
    background: color-mix(
      in srgb,
      var(--primary-text-color, #fff) 5%,
      var(--card-background-color, transparent)
    );
  }
  .audit-card {
    display: flex;
    background: var(--card-background-color, rgba(255, 255, 255, 0.04));
    border-radius: 10px;
    overflow: hidden;
  }
  .audit-card-bar {
    flex: 0 0 4px;
  }
  .audit-card-body {
    flex: 1 1 auto;
    padding: 14px 16px;
    min-width: 0;
  }
  .audit-card-head {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .audit-card-title {
    font-weight: 600;
    font-size: 14px;
    flex: 1 1 auto;
    min-width: 0;
  }
  .audit-card-badge {
    flex: 0 0 auto;
    text-transform: uppercase;
    font-size: 10px;
    letter-spacing: 0.04em;
  }
  .audit-card-detail {
    font-size: 13px;
    opacity: 0.82;
    line-height: 1.5;
    margin-top: 6px;
  }
  .audit-card-entities {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 10px;
  }
  .audit-entity-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    background: var(--secondary-background-color, rgba(255, 255, 255, 0.07));
    border: none;
    border-radius: 12px;
    padding: 3px 10px 3px 8px;
    font-size: 12px;
    color: inherit;
    cursor: pointer;
  }
  .audit-entity-chip ha-icon {
    --mdc-icon-size: 14px;
    opacity: 0.7;
  }
  .audit-entity-chip:hover {
    background: var(--divider-color, rgba(255, 255, 255, 0.12));
  }
  .audit-card-actions {
    margin-top: 12px;
  }

  /* ── Device-health signal groups ── */
  .ts-ignore {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    margin-top: 8px;
    padding: 4px 8px;
    background: none;
    border: none;
    color: var(--secondary-text-color, #a1a1aa);
    font-size: 12px;
    cursor: pointer;
    opacity: 0.7;
    --mdc-icon-size: 15px;
  }
  .ts-ignore:hover {
    opacity: 1;
  }
  .ts-groups {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-top: 4px;
  }
  .ts-group {
    background: var(--card-background-color, rgba(255, 255, 255, 0.03));
    border-radius: 8px;
    padding: 10px 12px;
  }
  .ts-group-head {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    background: none;
    border: none;
    padding: 0;
    margin: 0;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .ts-group-summary {
    margin-left: auto;
    font-size: 11px;
    opacity: 0.6;
    white-space: nowrap;
  }
  .ts-group-chevron {
    --mdc-icon-size: 16px;
    opacity: 0.5;
    flex: 0 0 auto;
  }
  .ts-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex: 0 0 auto;
  }
  .ts-group-name {
    font-weight: 600;
    font-size: 13px;
  }
  .ts-group-area {
    font-size: 11px;
    opacity: 0.55;
  }
  .ts-group-count {
    margin-left: auto;
    font-size: 11px;
    opacity: 0.6;
  }
  .ts-group-items {
    margin-top: 6px;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .ts-item {
    display: flex;
    align-items: baseline;
    gap: 8px;
    font-size: 12px;
  }
  .ts-item-link {
    background: none;
    border: none;
    padding: 0;
    color: var(--primary-color, #03a9f4);
    cursor: pointer;
    font-size: 12px;
    text-align: left;
  }
  .ts-item-link:hover {
    text-decoration: underline;
  }
  .ts-item-desc {
    opacity: 0.6;
  }
  /* ── Boot-settle hero (home still starting up) ── */
  .insights-settling {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    gap: 14px;
    padding: 72px 24px;
    min-height: 40vh;
  }
  .insights-settling .spinner {
    width: 34px;
    height: 34px;
    border-width: 3px;
  }
  .insights-settling-title {
    font-size: 16px;
    font-weight: 600;
  }
  .insights-settling-sub {
    font-size: 13px;
    opacity: 0.6;
    max-width: 320px;
    line-height: 1.5;
  }

  /* ── Score breakdown ("why this score") ── */
  .score-breakdown {
    background: var(--card-background-color, rgba(255, 255, 255, 0.04));
    border-radius: 12px;
    padding: 14px 16px 16px;
    margin: 0 0 16px;
    /* Fade the whole card up as it enters, echoing the gauge pop above it. */
    animation: sb-card-in 0.45s ease-out both;
  }
  @keyframes sb-card-in {
    from {
      opacity: 0;
      transform: translateY(8px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }
  .sb-head {
    margin-bottom: 12px;
  }
  .sb-title {
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    opacity: 0.6;
  }
  .sb-rows {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  /* name | bar | count | points | arrow — the bar flexes, the rest hold their
     width so the numbers stay in a clean right-aligned column across rows. */
  .sb-row {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 3px 6px;
    margin: -3px -6px;
    border-radius: 8px;
    background: none;
    border: none;
    color: inherit;
    font: inherit;
    text-align: left;
    /* Staggered fade-in (delay set inline per row) so the chart builds in from
       the top instead of appearing all at once. */
    animation: sb-row-in 0.4s ease-out both;
  }
  @keyframes sb-row-in {
    from {
      opacity: 0;
      transform: translateX(-6px);
    }
    to {
      opacity: 1;
      transform: translateX(0);
    }
  }
  /* Rows that link down to a checklist section are buttons — brighten and slide
     the chevron in on hover so the affordance reads without cluttering rest. */
  .sb-row-link {
    cursor: pointer;
    transition: background 0.12s ease;
  }
  .sb-row-link:hover,
  .sb-row-link:focus-visible {
    background: color-mix(
      in srgb,
      var(--primary-text-color, #fff) 7%,
      transparent
    );
    outline: none;
  }
  .sb-row-link:hover .sb-row-name {
    text-decoration: underline;
    text-underline-offset: 2px;
  }
  .sb-row-arrow {
    flex: 0 0 auto;
    --mdc-icon-size: 18px;
    opacity: 0.35;
    transform: translateX(-3px);
    transition:
      opacity 0.12s ease,
      transform 0.12s ease;
  }
  .sb-row-link:hover .sb-row-arrow,
  .sb-row-link:focus-visible .sb-row-arrow {
    opacity: 0.85;
    transform: translateX(0);
  }
  .sb-row-arrow-spacer {
    flex: 0 0 18px;
  }
  .sb-row-name {
    flex: 0 0 34%;
    max-width: 34%;
    font-size: 13px;
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .sb-track {
    flex: 1 1 auto;
    height: 8px;
    border-radius: 999px;
    background: var(--divider-color, rgba(255, 255, 255, 0.1));
    overflow: hidden;
  }
  .sb-fill {
    height: 100%;
    border-radius: 999px;
    min-width: 4px;
    /* Grow out from zero to the target width (--sb-w, set inline) as it enters,
       delayed per row so bars fill in one after another under the gauge sweep. */
    animation: sb-fill-grow 0.7s cubic-bezier(0.22, 1, 0.36, 1) both;
  }
  @keyframes sb-fill-grow {
    from {
      width: 0;
    }
    to {
      width: var(--sb-w);
    }
  }
  .sb-row-count {
    flex: 0 0 auto;
    font-size: 12px;
    opacity: 0.55;
    white-space: nowrap;
  }
  .sb-row-pts {
    flex: 0 0 auto;
    min-width: 34px;
    text-align: right;
    font-size: 13px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  @media (prefers-reduced-motion: reduce) {
    /* No entrance motion — snap to final state (bars keep their inline width). */
    .score-breakdown,
    .sb-row,
    .sb-fill {
      animation: none;
    }
  }
  @media (max-width: 480px) {
    /* Drop the "N issues" column on narrow screens — the count is already on
       each check row below; keep name, bar, and points. */
    .sb-row-count {
      display: none;
    }
    .sb-row-name {
      flex-basis: 40%;
      max-width: 40%;
    }
  }
`;
