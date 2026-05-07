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
    transition: colors 0.2s ease;
    user-select: none;
    color: var(--primary-text-color);
  }
  .btn:hover {
    opacity: 0.9;
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
`;
