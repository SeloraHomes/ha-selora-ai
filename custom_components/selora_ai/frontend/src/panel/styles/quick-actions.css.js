import { css } from "lit";

export const quickActionStyles = css`
  /* ── Shared quick-action container ── */
  .qa-group {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 10px;
  }

  /* ── Suggestion chips (welcome / quick-start) ── */
  .qa-suggestion {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 12px 16px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    border: 1px solid
      var(--selora-inner-card-border, var(--divider-color, #3f3f46));
    border-radius: 10px;
    background: var(
      --selora-inner-card-bg,
      var(--primary-background-color, #18181b)
    );
    color: var(--primary-text-color);
    transition:
      border-color 0.15s,
      background 0.15s;
    text-align: left;
    line-height: 1.4;
  }
  .qa-suggestion:hover {
    border-color: var(--selora-accent);
    background: rgba(251, 191, 36, 0.04);
  }
  .qa-suggestion:active {
    background: rgba(251, 191, 36, 0.08);
  }
  .qa-suggestion ha-icon {
    --mdc-icon-size: 16px;
    flex-shrink: 0;
    color: var(--secondary-text-color);
  }

  /* ── Choice cards (AI-offered options) ── */
  .qa-group--choices {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 10px;
  }
  .qa-choice {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 12px 14px;
    cursor: pointer;
    border: 1px solid
      var(--selora-inner-card-border, var(--divider-color, #3f3f46));
    border-radius: 12px;
    background: var(
      --selora-inner-card-bg,
      var(--primary-background-color, #18181b)
    );
    color: var(--primary-text-color);
    transition:
      border-color 0.15s,
      transform 0.15s,
      box-shadow 0.15s;
    text-align: left;
  }
  .qa-choice:hover {
    border-color: var(--selora-accent);
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  }
  .qa-choice:active {
    transform: translateY(0);
  }
  .qa-choice-label {
    font-size: 13px;
    font-weight: 600;
  }
  .qa-choice-desc {
    font-size: 12px;
    opacity: 0.6;
    line-height: 1.35;
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
