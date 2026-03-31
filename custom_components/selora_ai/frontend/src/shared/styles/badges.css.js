import { css } from "lit";

export const sharedBadges = css`
  .badge {
    background: var(--selora-zinc-700);
    color: var(--selora-zinc-200);
    border-radius: 10px;
    padding: 3px 8px;
    font-size: 12px;
    font-weight: 500;
    min-width: 16px;
    text-align: center;
    line-height: 1;
    display: inline-flex;
    align-items: center;
    transition: all 0.25s ease;
  }
  .chip {
    padding: 3px 9px;
    border-radius: 10px;
    font-size: 10px;
    font-weight: 700;
    color: white;
  }
  .chip.ai-managed {
    background: var(--selora-accent);
  }
  .chip.user-managed {
    background: var(--selora-zinc-600);
  }
  .chip.suggestion {
    background: var(--selora-accent);
  }
  .status-indicator {
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 10px;
    flex-shrink: 0;
  }
  .status-indicator.on {
    color: var(--success-color, #4caf50);
    background: rgba(76, 175, 80, 0.12);
  }
  .status-indicator.off {
    color: var(--secondary-text-color);
    background: rgba(158, 158, 158, 0.12);
  }
`;
