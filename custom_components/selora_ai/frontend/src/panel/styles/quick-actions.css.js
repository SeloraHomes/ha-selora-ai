import { css } from "lit";

export const quickActionStyles = css`
  /* ── Shared quick-action container ── */
  .qa-group {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 10px;
  }

  /* ────────────────────────────────────────────────────────────────────
   * Animated comet that travels along the chip's border perimeter.
   *
   * Technique (port of the React/Tailwind reference):
   *   - .qa-glow-track is a transparent-bordered overlay sitting at -1px on
   *     the chip. A two-layer mask shows ONLY the border ring of the chip
   *     (mask-composite: intersect with linear-gradient(transparent) on
   *     padding-box and linear-gradient(#000) on border-box).
   *   - .qa-glow-spot inside it follows offset-path: rect(0 auto auto 0
   *     round Npx) — the rectangular perimeter — animated via offset-distance
   *     0% → 100%, 5s linear infinite. The spot has a horizontal gradient
   *     fading from transparent to brand color, giving a comet trail look.
   *
   * Brand color: --selora-accent (gold) in dark, HA --primary-color in light.
   * ──────────────────────────────────────────────────────────────────── */

  @keyframes qa-spot-travel {
    to {
      offset-distance: 100%;
    }
  }

  .qa-suggestion,
  .qa-choice {
    --qa-spot-color: var(--selora-accent, #fbbf24);
    /* Match the header tab fill/border (translucent ghost pill) */
    --qa-bg: rgba(255, 255, 255, 0.06);
    --qa-border-color: rgba(255, 255, 255, 0.08);
    --qa-bg-hover: rgba(255, 255, 255, 0.1);
    --qa-border-hover: rgba(255, 255, 255, 0.12);
    --qa-radius: 999px;
    --qa-spot-size: 24px;
    --qa-spot-duration: 5s;

    position: relative;
    isolation: isolate;
    cursor: pointer;
    color: var(--primary-text-color);
    border: 1px solid var(--qa-border-color);
    border-radius: var(--qa-radius);
    background: var(--qa-bg);
  }

  /* Light mode: match light-mode tabs */
  :host(:not([dark])) .qa-suggestion,
  :host(:not([dark])) .qa-choice {
    --qa-spot-color: var(--primary-color, #03a9f4);
    --qa-bg: rgba(0, 0, 0, 0.05);
    --qa-border-color: rgba(0, 0, 0, 0.1);
    --qa-bg-hover: rgba(0, 0, 0, 0.08);
    --qa-border-hover: rgba(0, 0, 0, 0.15);
  }

  /* Comet track — masked so its contents only paint on the border ring */
  .qa-glow-track {
    position: absolute;
    inset: -1px;
    border-radius: inherit;
    border: 2px solid transparent;
    pointer-events: none;
    z-index: 0;
    -webkit-mask:
      linear-gradient(transparent, transparent), linear-gradient(#000, #000);
    -webkit-mask-clip: padding-box, border-box;
    -webkit-mask-composite: source-in;
    mask:
      linear-gradient(transparent, transparent), linear-gradient(#000, #000);
    mask-clip: padding-box, border-box;
    mask-composite: intersect;
  }

  /* The traveling spot. offset-path traces the perimeter; the comet trail
     is a horizontal gradient (transparent → spot color) sized to the spot. */
  .qa-glow-spot {
    position: absolute;
    width: var(--qa-spot-size);
    height: var(--qa-spot-size);
    background: linear-gradient(
      to right,
      transparent 0%,
      var(--qa-spot-color) 100%
    );
    offset-path: rect(0 auto auto 0 round var(--qa-radius));
    offset-distance: 0%;
    animation: qa-spot-travel var(--qa-spot-duration) linear infinite;
  }

  @media (prefers-reduced-motion: reduce) {
    .qa-glow-spot {
      animation: none;
    }
  }

  /* On touch devices, chips stack vertically and have full row width, so
     drop label truncation entirely and let titles wrap to as many lines
     as needed. Desktop keeps the ellipsis/line-clamp behavior. */
  @media (hover: none) {
    .qa-suggestion-label {
      white-space: normal;
      overflow: visible;
      text-overflow: clip;
    }
    .qa-choice-label {
      display: block;
      -webkit-line-clamp: unset;
      line-clamp: unset;
      white-space: normal;
      overflow: visible;
      text-overflow: clip;
    }
  }

  /* ── Suggestion chips (welcome / quick-start, scene suggestions) ── */
  .qa-suggestion {
    --qa-radius: 12px;
    display: inline-flex;
    align-items: center;
    gap: 10px;
    padding: 12px 14px 12px 16px;
    min-width: 0;
    font-size: 13px;
    font-weight: 500;
    text-align: left;
    line-height: 1.3;
    transition:
      background-color 0.15s,
      border-color 0.15s,
      transform 0.15s,
      box-shadow 0.2s;
  }
  .qa-suggestion:hover {
    border-color: var(--qa-border-hover);
    background-color: var(--qa-bg-hover);
  }
  .qa-suggestion:hover .qa-suggestion-trail {
    color: var(--qa-spot-color);
    transform: translateX(2px);
  }
  .qa-suggestion:active {
    transform: translateY(0) scale(0.99);
  }
  .qa-suggestion ha-icon {
    --mdc-icon-size: 18px;
    flex-shrink: 0;
    position: relative;
    z-index: 1;
  }
  .qa-suggestion-lead {
    color: var(--qa-spot-color);
    opacity: 0.85;
  }
  .qa-suggestion-label {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    position: relative;
    z-index: 1;
  }
  .qa-suggestion-trail {
    --mdc-icon-size: 16px !important;
    color: var(--secondary-text-color);
    opacity: 0.5;
    transition:
      color 0.15s,
      transform 0.15s,
      opacity 0.15s;
  }

  /* ── Choice cards (AI-offered options) ── */
  .qa-group--choices {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 10px;
  }
  .qa-choice {
    --qa-radius: 12px;
    display: flex;
    padding: 12px 14px;
    text-align: left;
    transition:
      background-color 0.15s,
      border-color 0.15s,
      transform 0.15s,
      box-shadow 0.2s;
  }
  .qa-choice:hover {
    border-color: var(--qa-border-hover);
    background-color: var(--qa-bg-hover);
  }
  .qa-choice:hover .qa-choice-trail {
    color: var(--qa-spot-color);
    transform: translateX(2px);
  }
  .qa-choice:active {
    transform: translateY(0) scale(0.99);
  }
  .qa-choice > *:not(.qa-glow-track) {
    position: relative;
    z-index: 1;
  }
  .qa-choice-row {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    min-width: 0;
  }
  .qa-choice ha-icon {
    --mdc-icon-size: 18px;
    flex-shrink: 0;
  }
  .qa-choice-lead {
    color: var(--qa-spot-color);
    opacity: 0.85;
  }
  .qa-choice-text {
    display: flex;
    flex-direction: column;
    gap: 2px;
    flex: 1;
    min-width: 0;
  }
  .qa-choice-label {
    font-size: 13px;
    font-weight: 600;
    line-height: 1.3;
    /* Wrap up to 2 lines, then ellipsis (line-clamp) */
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    word-break: break-word;
  }
  .qa-choice-desc {
    font-size: 12px;
    opacity: 0.6;
    line-height: 1.35;
  }
  .qa-choice-trail {
    --mdc-icon-size: 16px !important;
    color: var(--secondary-text-color);
    opacity: 0.5;
    transition:
      color 0.15s,
      transform 0.15s,
      opacity 0.15s;
  }

  /* ── Confirmation buttons (Apply / Modify / Cancel) ── */
  .qa-group--confirmations {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }
  .qa-confirm {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    border-radius: 8px;
    transition:
      background 0.15s,
      border-color 0.15s;
    white-space: nowrap;
    border: 1px solid
      var(--selora-inner-card-border, var(--divider-color, #3f3f46));
    background: transparent;
    color: var(--primary-text-color);
  }
  .qa-confirm:hover {
    border-color: var(--selora-accent);
  }
  .qa-confirm--primary {
    background: var(--selora-accent);
    color: #000;
    border-color: var(--selora-accent);
  }
  .qa-confirm--primary:hover {
    background: #f59e0b;
    border-color: #f59e0b;
  }
  .qa-confirm ha-icon {
    --mdc-icon-size: 14px;
  }

  /* ── Disabled state (after selection) ── */
  .qa-group--used {
    opacity: 0.45;
    pointer-events: none;
  }
`;
