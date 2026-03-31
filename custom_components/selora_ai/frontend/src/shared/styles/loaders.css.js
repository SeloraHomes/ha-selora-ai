import { css } from "lit";

export const sharedLoaders = css`
  .spinner {
    display: inline-block;
    width: 18px;
    height: 18px;
    border: 2.5px solid rgba(0, 0, 0, 0.1);
    border-top-color: var(--selora-accent);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }
  .spinner.green {
    border-color: rgba(76, 175, 80, 0.2);
    border-top-color: var(--success-color, #4caf50);
  }
  .dots-loader {
    display: inline-flex;
    gap: 4px;
    align-items: center;
  }
  .dots-loader span {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--selora-accent);
    animation: bounce 1.2s ease-in-out infinite;
  }
  .dots-loader span:nth-child(2) {
    animation-delay: 0.2s;
  }
  .dots-loader span:nth-child(3) {
    animation-delay: 0.4s;
  }
  .loading-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 12px 0;
    font-size: 12px;
    opacity: 0.6;
  }
  .empty-row {
    padding: 12px 0;
    font-size: 12px;
    opacity: 0.5;
    font-style: italic;
  }
  .generating-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 14px 0;
    font-size: 12px;
    opacity: 0.7;
  }
`;
