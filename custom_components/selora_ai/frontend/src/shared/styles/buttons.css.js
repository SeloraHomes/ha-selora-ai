import { css } from "lit";

export const sharedButtons = css`
  .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    padding: 10px 20px;
    border-radius: 12px;
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    border: 1px solid transparent;
    background: transparent;
    font-family: inherit;
    transition:
      transform 0.18s var(--btn-ease),
      background-color 0.2s,
      border-color 0.2s,
      box-shadow 0.2s,
      opacity 0.2s;
    user-select: none;
    color: var(--primary-text-color);
  }
  .btn:hover {
    opacity: 0.9;
  }
  .btn-primary,
  .btn-accent,
  .btn-warning {
    --btn-ring: var(--selora-accent, #fbbf24);
  }
  .btn-danger {
    --btn-ring: var(--error-color, #ef4444);
  }
  .btn-primary {
    background: var(--selora-accent);
    border-color: var(--selora-accent);
    color: #000;
    font-weight: 500;
  }
  .btn-primary:hover {
    box-shadow: var(--selora-glow);
    background: var(--selora-accent-light);
    border-color: var(--selora-accent-light);
    opacity: 1;
  }
  .btn-success {
    background: var(--success-color, #4caf50);
    border-color: var(--success-color, #4caf50);
    color: white;
  }
  .btn-outline {
    border-color: var(--selora-zinc-700);
    color: var(--selora-btn-outline-text);
    background: var(--selora-section-bg);
  }
  .btn-outline:hover {
    border-color: var(--selora-zinc-600);
    background: var(--secondary-background-color, #3f3f46);
  }
  .btn-danger {
    border-color: rgba(239, 68, 68, 0.4);
    color: var(--error-color, #ef4444);
    background: transparent;
  }
  .btn-danger:hover {
    background: rgba(239, 68, 68, 0.1);
    border-color: var(--error-color, #ef4444);
  }
  .btn-warning {
    border-color: rgba(251, 191, 36, 0.4);
    color: var(--selora-accent-text);
    background: transparent;
  }
  .btn-warning:hover {
    background: rgba(251, 191, 36, 0.08);
    border-color: var(--selora-accent);
  }
  /* Brand-accented pill for primary "create new" actions. Translucent
     gold fill + subtle gold border, matching the header tab visual
     language so the button feels native to the rest of the UI rather
     than the loud filled btn-primary. */
  .btn-accent {
    border-color: rgba(251, 191, 36, 0.35);
    color: var(--selora-accent-text);
    background: rgba(251, 191, 36, 0.08);
    border-radius: 999px;
    padding: 8px 16px;
    font-weight: 500;
  }
  .btn-accent:hover {
    background: rgba(251, 191, 36, 0.14);
    border-color: var(--selora-accent);
    box-shadow: none;
    opacity: 1;
  }
  /* Light mode: translucent gold-on-white is near-invisible. Match the
     filled btn-primary language (solid gold + black text) so it lines up
     with the Accept buttons elsewhere in the UI. Pill radius is kept. */
  :host(:not([dark])) .btn-accent {
    background: var(--selora-accent);
    border-color: var(--selora-accent);
    color: #000;
  }
  :host(:not([dark])) .btn-accent:hover {
    background: var(--selora-accent-light);
    border-color: var(--selora-accent-light);
  }
  .btn-ghost {
    border-color: transparent;
    color: var(--secondary-text-color);
    background: transparent;
    font-size: 11px;
    padding: 4px 8px;
  }
  .btn-ghost:hover {
    color: var(--primary-text-color);
    background: rgba(0, 0, 0, 0.06);
    border-color: var(--divider-color);
  }
  .btn-ghost.active {
    color: var(--selora-accent-text);
    border-color: rgba(251, 191, 36, 0.35);
    background: rgba(251, 191, 36, 0.05);
  }

  /* ── Every button moves the same way: it lifts under the pointer, gives
     under the press, and one ring ripples out of a click (press-ripple.js
     sets data-pressed). ── */
  .btn,
  .action-btn {
    --btn-ease: cubic-bezier(0.22, 1, 0.36, 1);
    position: relative;
  }
  @media (hover: hover) {
    .btn:hover:not([disabled]),
    .action-btn:hover:not([disabled]) {
      transform: translateY(-1px);
    }
  }
  .btn:active:not([disabled]),
  .action-btn:active:not([disabled]) {
    transform: scale(0.97);
  }
  .btn::after,
  .action-btn::after {
    content: "";
    position: absolute;
    inset: -1px;
    border: 2px solid var(--btn-ring, var(--secondary-text-color, #a1a1aa));
    border-radius: inherit;
    opacity: 0;
    pointer-events: none;
  }
  .btn[data-pressed]::after,
  .action-btn[data-pressed]::after {
    animation: btn-ripple 0.7s var(--btn-ease);
  }
  @keyframes btn-ripple {
    0% {
      opacity: 0.9;
      transform: scale(1);
    }
    100% {
      opacity: 0;
      transform: scale(1.18, 1.6);
    }
  }

  /* ── The action button (shared/action-button.js): a pill and a round
     badge whose mark says what the press does. ── */
  .action-btn {
    --btn-ring: var(--secondary-text-color, #a1a1aa);
    display: inline-flex;
    align-items: center;
    gap: 12px;
    max-width: 100%;
    min-width: 0;
    padding: 4px 4px 4px 18px;
    border: 1px solid var(--selora-inner-card-border, var(--divider-color));
    border-radius: 999px;
    background: var(--card-background-color);
    color: var(--primary-text-color);
    font: inherit;
    font-size: 14px;
    font-weight: 500;
    text-align: left;
    cursor: pointer;
    transition:
      transform 0.18s var(--btn-ease),
      border-color 0.15s,
      box-shadow 0.2s,
      opacity 0.3s;
  }
  .action-btn:hover:not([disabled]) {
    border-color: rgba(251, 191, 36, 0.45);
  }
  .action-btn[disabled] {
    cursor: default;
    opacity: 0.5;
  }
  .action-btn--tall {
    border-radius: 20px;
    padding-block: 8px;
  }
  .action-btn-text {
    display: flex;
    flex-direction: column;
    gap: 2px;
    flex: 1;
    min-width: 0;
  }
  .action-btn-label {
    line-height: 1.3;
    overflow-wrap: anywhere;
  }
  .action-btn-desc {
    font-size: 12px;
    line-height: 1.35;
    color: var(--secondary-text-color);
  }
  .action-btn-mark {
    flex: 0 0 32px;
    width: 32px;
    height: 32px;
    border-radius: 50%;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: var(--secondary-background-color, #27272a);
    color: var(--selora-accent, #fbbf24);
    --mdc-icon-size: 16px;
    transition: transform 0.18s var(--btn-ease);
  }
  .action-btn-mark ha-icon {
    display: inline-flex;
    line-height: 0;
  }
  @media (hover: hover) {
    .action-btn:hover:not([disabled]) .action-btn-mark {
      transform: scale(1.08);
    }
    .action-btn:hover:not([disabled]) .action-btn-tick {
      animation: btn-draw 0.35s var(--btn-ease);
    }
    .action-btn:hover:not([disabled]) .action-btn-cross {
      transform: rotate(90deg);
    }
  }
  .action-btn:active:not([disabled]) .action-btn-mark {
    transform: scale(0.9);
  }
  .action-btn[data-pressed] .action-btn-mark {
    animation: btn-pop 0.4s var(--btn-ease);
  }
  .action-btn[data-pressed] .action-btn-tick {
    animation: btn-draw 0.4s 0.12s var(--btn-ease) both;
  }
  .action-btn-svg {
    width: 16px;
    height: 16px;
    overflow: visible;
  }
  .action-btn-svg path {
    fill: none;
    stroke: currentColor;
    stroke-width: 2.6;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .action-btn-tick {
    stroke-dasharray: 22;
  }
  .action-btn-cross {
    transform-origin: 12px 12px;
    transition: transform 0.35s var(--btn-ease);
  }
  /* Gold: the answer the row offers. One per row. */
  .action-btn--primary {
    --btn-ring: var(--selora-accent, #fbbf24);
    border-color: rgba(251, 191, 36, 0.45);
    box-shadow: 0 0 18px -6px rgba(251, 191, 36, 0.5);
  }
  .action-btn--primary:hover:not([disabled]) {
    border-color: rgba(251, 191, 36, 0.8);
    box-shadow: 0 0 22px -4px rgba(251, 191, 36, 0.6);
  }
  .action-btn--primary .action-btn-mark {
    background: linear-gradient(135deg, #fcd34d 0%, #fbbf24 50%, #f59e0b 100%);
    color: #1a1300;
    box-shadow:
      0 1px 2px rgba(0, 0, 0, 0.3),
      inset 0 1px 0 rgba(255, 255, 255, 0.35);
  }
  /* A refusal: grey like the rest, its cross in red. */
  .action-btn--decline {
    --btn-ring: var(--error-color, #ef4444);
  }
  .action-btn--decline .action-btn-mark {
    color: var(--error-color, #ef4444);
  }
  /* Red: the press deletes something. */
  .action-btn--danger {
    --btn-ring: var(--error-color, #ef4444);
    border-color: rgba(239, 68, 68, 0.45);
  }
  .action-btn--danger:hover:not([disabled]) {
    border-color: rgba(239, 68, 68, 0.85);
  }
  .action-btn--danger .action-btn-mark {
    background: var(--error-color, #ef4444);
    color: #fff;
  }
  .action-btn--picked {
    border-color: rgba(251, 191, 36, 0.8);
  }
  .action-btn--danger.action-btn--picked {
    border-color: var(--error-color, #ef4444);
  }
  @keyframes btn-draw {
    from {
      stroke-dashoffset: 22;
    }
    to {
      stroke-dashoffset: 0;
    }
  }
  @keyframes btn-pop {
    0% {
      transform: scale(0.9);
    }
    55% {
      transform: scale(1.15);
    }
    100% {
      transform: scale(1);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .btn,
    .action-btn,
    .action-btn-mark,
    .action-btn-cross {
      transition: none;
    }
    .btn:hover,
    .btn:active,
    .action-btn:hover,
    .action-btn:active,
    .action-btn:hover .action-btn-mark,
    .action-btn:active .action-btn-mark,
    .action-btn:hover .action-btn-cross {
      transform: none;
    }
    .btn[data-pressed]::after,
    .action-btn[data-pressed]::after,
    .action-btn[data-pressed] .action-btn-mark,
    .action-btn[data-pressed] .action-btn-tick,
    .action-btn:hover .action-btn-tick {
      animation: none;
    }
  }
`;
