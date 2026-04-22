import { css } from "lit";

export const headerStyles = css`
  .header {
    background: var(--app-header-background-color);
    border-bottom: var(--app-header-border-bottom, none);
    z-index: 2;
    flex-shrink: 0;
    height: var(--header-height, 56px);
    box-sizing: border-box;
    position: relative;
  }
  /* Golden glow line at bottom of header (dark mode only) */
  :host([dark]) .header::after {
    content: "";
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    height: 1px;
    background: linear-gradient(
      90deg,
      transparent 5%,
      #f59e0b 50%,
      transparent 95%
    );
    z-index: 3;
  }
  :host([dark]) .header::before {
    content: "";
    position: absolute;
    bottom: -24px;
    left: 10%;
    right: 10%;
    height: 24px;
    background: radial-gradient(
      ellipse 40% 100% at center top,
      rgba(251, 191, 36, 0.6) 0%,
      rgba(245, 158, 11, 0.3) 30%,
      rgba(245, 158, 11, 0.08) 60%,
      transparent 100%
    );
    filter: blur(4px);
    z-index: 3;
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
    gap: 6px;
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
    padding: 8px 16px;
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    color: var(--app-header-text-color, var(--primary-text-color));
    opacity: 0.55;
    background: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 999px;
    transition:
      opacity 0.25s,
      background 0.25s,
      border-color 0.25s,
      color 0.25s;
    white-space: nowrap;
    user-select: none;
    display: flex;
    align-items: center;
  }
  .tab:hover {
    opacity: 0.85;
    background: rgba(255, 255, 255, 0.1);
    border-color: rgba(255, 255, 255, 0.12);
  }
  .tab.active {
    opacity: 1;
    font-weight: 600;
    color: var(--selora-accent-text);
    background: rgba(251, 191, 36, 0.1);
    border-color: rgba(251, 191, 36, 0.25);
  }
  /* Light mode */
  :host(:not([dark])) .tab {
    background: rgba(0, 0, 0, 0.05);
    border-color: rgba(0, 0, 0, 0.1);
  }
  :host(:not([dark])) .tab:hover {
    background: rgba(0, 0, 0, 0.08);
    border-color: rgba(0, 0, 0, 0.15);
  }
  :host(:not([dark])) .tab.active {
    color: var(--primary-text-color);
    background: rgba(0, 0, 0, 0.08);
    border-color: rgba(0, 0, 0, 0.2);
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
