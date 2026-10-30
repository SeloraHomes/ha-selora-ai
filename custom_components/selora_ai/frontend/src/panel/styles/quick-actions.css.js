import { css } from "lit";

// The rows quick actions sit in. The buttons themselves are the shared
// action button (shared/styles/buttons.css.js).
export const quickActionStyles = css`
  .qa-group {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 10px;
  }
  /* Answers to a message sit under it, at its right edge. */
  .qa-group--answers {
    justify-content: flex-end;
    gap: 10px;
    margin-top: 14px;
  }
  @media (max-width: 600px) {
    .qa-group--answers {
      flex-direction: column;
      align-items: stretch;
    }
    .qa-group--answers .action-btn {
      justify-content: space-between;
    }
  }

  /* ── After a selection ── */
  .qa-group--used {
    opacity: 0.45;
    pointer-events: none;
  }
  /* The answer given stays lit; the others dim and step back. */
  .qa-group--picked {
    opacity: 1;
  }
  .qa-group--picked .action-btn:not(.action-btn--picked) {
    opacity: 0.45;
    transform: translateY(2px);
  }
`;
