import { css } from "lit";

export const headerStyles = css`
  .header {
    background: var(--app-header-background-color);
    border-bottom: var(--app-header-border-bottom, none);
    z-index: 2;
    flex-shrink: 0;
    height: var(--header-height, 56px);
    box-sizing: border-box;
  }
  .header-toolbar {
    position: relative;
    display: flex;
    align-items: center;
    height: var(--header-height, 56px);
    padding: 0 12px;
    box-sizing: border-box;
    width: 100%;
    font-family: var(--ha-font-family-body, Roboto, Noto, sans-serif);
    color: var(--app-header-text-color, var(--primary-text-color));
    -webkit-font-smoothing: var(--ha-font-smoothing, antialiased);
    -moz-osx-font-smoothing: var(--ha-moz-osx-font-smoothing, grayscale);
  }
  .header-logo {
    width: 20px;
    height: 20px;
    margin-left: 8px;
    flex-shrink: 0;
  }
  .header-title {
    margin-inline-start: var(--ha-space-6, 24px);
    margin-right: 12px;
    flex-shrink: 0;
    white-space: nowrap;
    font-size: var(--ha-font-size-xl, 20px);
    font-weight: var(--ha-font-weight-normal, 400);
  }
  .tabs-center {
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    align-items: center;
    height: 100%;
    pointer-events: auto;
  }
  @media (max-width: 600px) {
    .header-title,
    .header-logo {
      display: none;
    }
    .tabs-center {
      position: static;
      transform: none;
      flex: 1;
      justify-content: center;
    }
  }
  .tab {
    position: relative;
    padding: 0 10px;
    height: var(--header-height, 56px);
    cursor: pointer;
    font-size: 16px;
    font-weight: 400;
    color: var(--app-header-text-color, var(--primary-text-color));
    opacity: 0.55;
    transition:
      opacity 0.3s,
      color 0.3s;
    white-space: nowrap;
    user-select: none;
    display: flex;
    align-items: center;
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
  /* Light mode: keep text dark, only underline is gold */
  :host(:not([dark])) .tab:hover,
  :host(:not([dark])) .tab.active {
    color: var(--primary-text-color);
  }
  /* Underline pinned to bottom of tab */
  .tab::after {
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
  .tab:hover::after,
  .tab.active::after {
    transform: scaleX(1);
  }
  .tab-inner {
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
  .tab-icon {
    --mdc-icon-size: 16px;
  }
  .header-spacer {
    flex: 1;
  }
  /* Overflow (3-dot) menu */
  .overflow-btn-wrap {
    position: relative;
  }
  .overflow-btn {
    background: none;
    border: none;
    cursor: pointer;
    width: 48px;
    height: 48px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(
      --sidebar-icon-color,
      var(--app-header-text-color, var(--primary-text-color))
    );
    --mdc-icon-size: 24px;
  }
  .overflow-menu {
    position: absolute;
    top: 100%;
    right: 0;
    min-width: 200px;
    background: var(--card-background-color, #fff);
    border-radius: 4px;
    box-shadow:
      0 2px 4px -1px rgba(0, 0, 0, 0.2),
      0 4px 5px rgba(0, 0, 0, 0.14),
      0 1px 10px rgba(0, 0, 0, 0.12);
    padding: 8px 0;
    z-index: 10;
  }
  .overflow-item {
    display: flex;
    align-items: center;
    gap: 16px;
    width: 100%;
    padding: 0 16px;
    height: 48px;
    background: none;
    border: none;
    cursor: pointer;
    font-size: var(--ha-font-size-m, 14px);
    font-family: var(--ha-font-family-body, Roboto, Noto, sans-serif);
    color: var(--primary-text-color);
    text-decoration: none;
    transition: background 0.1s;
    box-sizing: border-box;
    --mdc-icon-size: 24px;
  }
  .overflow-item:hover {
    background: rgba(128, 128, 128, 0.12);
  }
  .overflow-item ha-icon {
    color: var(--secondary-text-color);
  }
  /* card-tab underline used elsewhere — keep */
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
  .card-tab.active::after {
    transform: scaleX(1);
  }
  .card-tab:hover::after {
    transform: scaleX(0.6);
  }
`;
