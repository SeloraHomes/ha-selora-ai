import { css } from "lit";

export const headerStyles = css`
  .header {
    background: var(--app-header-background-color);
    border-bottom: var(--app-header-border-bottom, none);
    /* Must outrank the narrow-mode conversations drawer (z-index: 10
       in layout.css.js). The header creates a stacking context, so the
       overflow menu rendered inside it inherits this ceiling — without
       this bump the menu reopens hidden behind the drawer on mobile. */
    z-index: 11;
    flex-shrink: 0;
    height: var(--header-height, 56px);
    box-sizing: border-box;
    position: relative;
  }
  /* Suppress decorative glow when no LLM is configured — keeps the
     pre-setup screen calm. */
  :host([needs-setup]) .header::after,
  :host([needs-setup]) .header::before {
    display: none;
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
  .menu-btn {
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
    flex-shrink: 0;
  }
  .header-logo {
    width: 22px;
    height: 22px;
    margin-inline-start: var(--ha-space-6, 24px);
    flex-shrink: 0;
  }
  .header-title {
    margin-left: 10px;
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
  /* Narrow layout: hide centered tabs, they live in the Selora menu instead */
  :host([narrow]) .tabs-center {
    display: none;
  }
  :host([narrow]) .header-logo {
    margin-inline-start: 4px;
    width: 20px;
    height: 20px;
  }
  :host([narrow]) .header-title {
    font-size: var(--ha-font-size-l, 18px);
    margin-left: 8px;
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
  /* New-chat header button (visible when there's a chat to leave behind).
     Mobile: icon-only circle. Desktop: pill with icon + "New chat" label. */
  .header-new-chat {
    background: none;
    border: none;
    cursor: pointer;
    height: 40px;
    border-radius: 999px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 0 14px;
    flex-shrink: 0;
    width: auto;
    font-family: inherit;
    font-size: 13px;
    font-weight: 500;
    line-height: 1;
    white-space: nowrap;
    color: var(
      --sidebar-icon-color,
      var(--app-header-text-color, var(--primary-text-color))
    );
    --mdc-icon-size: 20px;
    transition:
      background 0.2s,
      color 0.2s;
  }
  .header-new-chat:hover {
    background: rgba(251, 191, 36, 0.12);
    color: var(--selora-accent-text, #f59e0b);
  }
  :host(:not([dark])) .header-new-chat:hover {
    background: rgba(0, 0, 0, 0.06);
    color: var(--primary-text-color);
  }
  .header-new-chat-label {
    white-space: nowrap;
  }
  /* Narrow: collapse back to a 44×44 icon-only circle */
  :host([narrow]) .header-new-chat {
    width: 44px;
    height: 44px;
    border-radius: 50%;
    padding: 0;
    --mdc-icon-size: 22px;
  }
  :host([narrow]) .header-new-chat-label {
    display: none;
  }

  /* Selora (right-side) menu — gold-accented to differentiate from HA burger */
  .overflow-btn-wrap {
    position: relative;
  }
  .overflow-btn {
    background: none;
    border: none;
    cursor: pointer;
    width: 44px;
    height: 44px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(
      --sidebar-icon-color,
      var(--app-header-text-color, var(--primary-text-color))
    );
    --mdc-icon-size: 22px;
    transition:
      background 0.2s,
      color 0.2s,
      box-shadow 0.2s;
  }
  .selora-menu-btn {
    color: var(--selora-accent-text, #f59e0b);
  }
  .selora-menu-btn:hover {
    background: rgba(251, 191, 36, 0.12);
    box-shadow: 0 0 14px rgba(251, 191, 36, 0.25);
  }
  :host(:not([dark])) .selora-menu-btn {
    color: var(--primary-text-color);
  }
  :host(:not([dark])) .selora-menu-btn:hover {
    background: rgba(0, 0, 0, 0.06);
    box-shadow: none;
  }
  .overflow-menu {
    position: absolute;
    top: calc(100% + 4px);
    right: 0;
    min-width: 220px;
    background: var(--card-background-color, #fff);
    border-radius: 12px;
    box-shadow:
      0 8px 24px rgba(0, 0, 0, 0.35),
      0 2px 8px rgba(0, 0, 0, 0.18);
    padding: 6px;
    /* Sit above the narrow-mode conversations drawer (z-index: 10);
       otherwise the menu reopens behind the sidebar after navigating
       to Conversations on mobile and is invisible. */
    z-index: 20;
  }
  .selora-menu {
    border: 1px solid rgba(251, 191, 36, 0.25);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
  }
  :host([dark]) .selora-menu {
    background: rgba(20, 20, 22, 0.92);
    box-shadow:
      0 12px 32px rgba(0, 0, 0, 0.55),
      0 0 24px rgba(251, 191, 36, 0.08);
  }
  :host(:not([dark])) .selora-menu {
    border-color: rgba(0, 0, 0, 0.1);
  }
  .overflow-section {
    display: flex;
    flex-direction: column;
  }
  /* Mobile-only nav section inside the menu (Automations / Scenes) */
  .overflow-section.narrow-only {
    display: none;
  }
  :host([narrow]) .overflow-section.narrow-only {
    display: flex;
  }
  .overflow-item {
    display: flex;
    align-items: center;
    gap: 14px;
    width: 100%;
    padding: 0 14px;
    height: 44px;
    background: none;
    border: none;
    border-radius: 8px;
    cursor: pointer;
    font-size: var(--ha-font-size-m, 14px);
    font-family: var(--ha-font-family-body, Roboto, Noto, sans-serif);
    color: var(--primary-text-color);
    text-decoration: none;
    transition:
      background 0.15s,
      color 0.15s;
    box-sizing: border-box;
    --mdc-icon-size: 20px;
  }
  .overflow-item:hover {
    background: rgba(251, 191, 36, 0.08);
  }
  :host(:not([dark])) .overflow-item:hover {
    background: rgba(0, 0, 0, 0.05);
  }
  .overflow-item ha-icon {
    color: var(--secondary-text-color);
  }
  .overflow-item-label {
    flex: 1;
    text-align: left;
  }
  .overflow-item-external {
    --mdc-icon-size: 14px;
    opacity: 0.5;
  }
  .overflow-item.active {
    color: var(--selora-accent-text, #f59e0b);
    background: rgba(251, 191, 36, 0.1);
    font-weight: 600;
  }
  .overflow-item.active ha-icon {
    color: var(--selora-accent-text, #f59e0b);
  }
  :host(:not([dark])) .overflow-item.active {
    color: var(--primary-text-color);
    background: rgba(0, 0, 0, 0.08);
  }
  :host(:not([dark])) .overflow-item.active ha-icon {
    color: var(--primary-text-color);
  }
  .overflow-divider {
    height: 1px;
    margin: 6px 4px;
    background: var(--divider-color, rgba(0, 0, 0, 0.12));
  }
  :host([dark]) .overflow-divider {
    background: rgba(251, 191, 36, 0.15);
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
