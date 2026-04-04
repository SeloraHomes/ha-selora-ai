import { css } from "lit";

export const headerStyles = css`
  .header {
    background: var(--app-header-background-color, #1c1c1e);
    color: var(--app-header-text-color, #e4e4e7);
    box-shadow: none;
    border-bottom: 1px solid var(--divider-color);
    z-index: 2;
    flex-shrink: 0;
  }
  .header-top {
    padding: 14px 24px;
    font-size: 20px;
    font-weight: 500;
    display: flex;
    align-items: center;
    gap: 10px;
    max-width: 1200px;
    margin: 0 auto;
    box-sizing: border-box;
    width: 100%;
  }
  .header-top ha-icon-button {
    margin-right: 4px;
    display: inline-flex;
    opacity: 0.55;
  }
  .feedback-link {
    margin-left: auto;
    background: none;
    border: none;
    color: var(--primary-text-color);
    opacity: 0.45;
    font-size: 12px;
    cursor: pointer;
    padding: 4px 0;
    font-family: inherit;
    transition: opacity 0.15s;
  }
  .feedback-link:hover {
    opacity: 0.8;
    text-decoration: underline;
  }
  .header-icon-link {
    color: var(--primary-text-color);
    opacity: 0.45;
    transition: opacity 0.15s;
    display: flex;
    align-items: center;
  }
  .header-icon-link:hover {
    opacity: 0.8;
  }
  .tabs {
    display: flex;
    padding: 0 24px;
    max-width: 1200px;
    margin: 0 auto;
    box-sizing: border-box;
    width: 100%;
  }
  .tab {
    padding: 10px 16px;
    cursor: pointer;
    font-weight: 400;
    font-size: 16px;
    opacity: 0.55;
    transition:
      opacity 0.3s,
      color 0.3s;
  }
  .tab:hover {
    opacity: 1;
    color: var(--selora-accent-text);
  }
  .tab.active {
    opacity: 1;
    font-weight: 600;
    color: var(--selora-accent-text);
  }
  .tab:first-child {
    padding-left: 0;
  }
  .tab-inner {
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
  .tab-icon {
    --mdc-icon-size: 16px;
    margin-bottom: 12px;
  }
  .tab-text {
    position: relative;
    padding-bottom: 6px;
  }
  /* Shared underline-from-center effect */
  .tab-text::after,
  .card-tab::after {
    content: "";
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    height: 2px;
    background: var(--selora-accent);
    transform: scaleX(0);
    transform-origin: center;
    transition: transform 0.3s ease;
  }
  .tab:hover .tab-text::after,
  .tab.active .tab-text::after,
  .card-tab.active::after {
    transform: scaleX(1);
  }
  .card-tab:hover::after {
    transform: scaleX(0.6);
  }
`;
