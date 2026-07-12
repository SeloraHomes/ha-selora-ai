import { css } from "lit";

export const cardElementStyles = css`
  /* ---- Card action buttons ---- */
  .card-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px solid var(--divider-color);
  }

  /* ---- Burger menu ---- */
  .burger-menu-wrapper {
    position: relative;
  }
  /* Square icon buttons on a list row: the kebab menu and the primary action
     (Run / Activate) share one size so they line up as a matched pair. */
  .burger-btn,
  .row-action-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 34px;
    height: 34px;
    border-radius: 8px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color);
    cursor: pointer;
    color: var(--secondary-text-color);
    transition: background 0.15s;
    --mdc-icon-size: 18px;
  }
  .burger-btn:hover:not(:disabled),
  .row-action-btn:hover:not(:disabled) {
    background: rgba(0, 0, 0, 0.06);
    color: var(--primary-text-color);
  }
  /* Externally-managed rows (e.g. recipe packages) show the menu disabled,
     matching the disabled toggle, with an explanatory tooltip. */
  .burger-btn:disabled,
  .row-action-btn:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
  /* Positioned fixed to the viewport (top/bottom/right set inline per-open via
     burgerMenuAnchor) so it escapes the nested overflow:hidden/auto containers
     the row lives in — otherwise it's clipped near the list edges. */
  .burger-dropdown {
    position: fixed;
    background: var(--card-background-color);
    border: 1px solid var(--divider-color);
    border-radius: 10px;
    box-shadow: 0 6px 20px rgba(0, 0, 0, 0.2);
    z-index: 1000;
    min-width: 180px;
    overflow: hidden;
  }
  .burger-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 16px;
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    color: var(--primary-text-color);
    border: none;
    background: none;
    width: 100%;
    text-align: left;
  }
  .burger-item ha-icon {
    --mdc-icon-size: 18px;
    flex-shrink: 0;
  }
  .burger-item:hover {
    background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.08);
  }
  .burger-item.danger {
    color: var(--error-color, #f44336);
  }
  .burger-item.danger:hover {
    background: rgba(244, 67, 54, 0.08);
  }
  .rename-input {
    flex: 1;
    font-size: 14px;
    font-weight: 600;
    border: 1px solid var(--selora-accent);
    border-radius: 8px;
    padding: 4px 8px;
    outline: none;
    background: var(--card-background-color, #fff);
    color: var(--primary-text-color);
    min-width: 0;
    transition: border-color 0.3s;
  }
  .rename-save-btn {
    background: var(--selora-accent);
    border: none;
    border-radius: 8px;
    color: #fff;
    cursor: pointer;
    padding: 4px 6px;
    margin-left: 4px;
    line-height: 1;
    display: flex;
    align-items: center;
    transition: background 0.3s;
  }
  .rename-save-btn:hover {
    background: #d97706;
    box-shadow: var(--selora-glow);
  }

  /* ---- Card inline tabs (Flow / YAML / History) ---- */
  .card-tabs {
    display: flex;
    align-items: center;
    gap: 0;
    margin: 8px 0 0;
    border-top: 1px solid var(--divider-color);
    padding-top: 8px;
    padding-bottom: 8px;
    font-size: 12px;
  }
  .card-tabs .label {
    font-size: 11px;
    opacity: 0.5;
    margin-right: 8px;
    white-space: nowrap;
  }
  .card-tab {
    padding: 6px 12px;
    border: none;
    background: none;
    font-size: 14px;
    font-weight: 500;
    color: var(--secondary-text-color);
    cursor: pointer;
    position: relative;
    transition: color 0.3s ease;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .card-tab:hover,
  .card-tab.active {
    color: var(--selora-accent-text);
  }
  .card-chevron {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    transition: transform 0.25s ease;
    cursor: pointer;
    opacity: 0.5;
    --mdc-icon-size: 16px;
    flex-shrink: 0;
  }
  .card-chevron:hover {
    opacity: 0.8;
  }
  .card-chevron.open {
    transform: rotate(180deg);
  }
  .card-tab-sep {
    color: var(--divider-color);
    font-size: 14px;
    user-select: none;
  }

  .expand-toggle {
    font-size: 11px;
    opacity: 0.55;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 4px;
    user-select: none;
    padding: 4px 0;
  }
  .expand-toggle:hover {
    opacity: 1;
  }

  /* ---- Version history timeline ----
     A vertical rail with a dot per version, plus a proper card-style
     entry to the right of each dot. Replaces the old cramped layout
     that crushed version label / time / badge into one row and
     squeezed the action buttons under the message with no breathing
     room. The current revision gets the gold dot + ring; older ones
     fade to the muted divider color. */
  .version-history {
    margin: 10px 0 4px;
    padding: 16px 18px 18px;
    border-radius: 12px;
    background: var(--secondary-background-color);
    border: 1px solid var(--divider-color);
  }
  .version-history-empty {
    opacity: 0.55;
    font-size: 13px;
    padding: 4px 0;
  }
  .version-list {
    list-style: none;
    margin: 0;
    padding: 0 0 0 22px;
    position: relative;
  }
  .version-list::before {
    content: "";
    position: absolute;
    left: 7px;
    top: 6px;
    bottom: 6px;
    width: 2px;
    background: var(--divider-color);
    border-radius: 2px;
  }
  .version-entry {
    position: relative;
    padding: 0 0 18px 14px;
  }
  .version-entry:last-child {
    padding-bottom: 0;
  }
  .version-entry-dot {
    position: absolute;
    left: -22px;
    top: 14px;
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: var(--divider-color);
    border: 2px solid var(--secondary-background-color);
    box-shadow: 0 0 0 1px var(--divider-color);
  }
  .version-entry.current .version-entry-dot {
    background: var(--selora-accent, #fbbf24);
    box-shadow: 0 0 0 2px rgba(251, 191, 36, 0.35);
  }
  .version-entry-card {
    background: var(--card-background-color, rgba(255, 255, 255, 0.03));
    border: 1px solid var(--divider-color);
    border-radius: 10px;
    padding: 12px 14px;
    transition: border-color 0.15s;
  }
  .version-entry.current .version-entry-card {
    border-color: rgba(251, 191, 36, 0.35);
    background: rgba(251, 191, 36, 0.04);
  }
  .version-entry-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .version-entry-title {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }
  .version-entry-num {
    font-size: 15px;
    font-weight: 700;
    color: var(--primary-text-color);
    letter-spacing: 0.01em;
  }
  .version-entry-badge {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    background: var(--selora-accent, #fbbf24);
    color: #000;
    border-radius: 999px;
    padding: 2px 8px;
    line-height: 1.4;
  }
  .version-entry-time {
    font-size: 12px;
    color: var(--secondary-text-color);
    opacity: 0.8;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .version-entry-message {
    margin: 8px 0 0;
    font-size: 13px;
    line-height: 1.45;
    color: var(--primary-text-color);
    opacity: 0.85;
  }
  .version-entry-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 12px;
  }
  .version-entry-btn {
    font-size: 12px;
    padding: 5px 12px;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .version-entry-yaml {
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px dashed var(--divider-color);
  }

  /* ---- Card base ---- */
  .card {
    background: var(--selora-zinc-800);
    color: var(--primary-text-color);
    border-radius: 16px;
    padding: 24px;
    margin-bottom: 14px;
    box-shadow: none;
    border: 1px solid var(--selora-zinc-700);
    transition: border-color 0.3s ease;
  }
  .card:hover {
    border-color: rgba(251, 191, 36, 0.3);
  }
  .card-row2 {
    position: relative;
  }
  .card .card-desc {
    transition: opacity 0.2s;
  }
  .card .card-actions-row {
    position: absolute;
    top: 50%;
    transform: translateY(-50%);
    left: 0;
    right: 0;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.2s;
  }
  .card:hover .card-desc {
    opacity: 0;
  }
  .card:hover .card-actions-row,
  .card .card-actions-row.visible {
    opacity: 1;
    pointer-events: auto;
  }
  .card.expanded .card-desc {
    opacity: 0;
  }
  @media (max-width: 600px) {
    .card-row2 {
      position: static !important;
      flex: none !important;
    }
    .card .card-desc {
      opacity: 1 !important;
      height: auto !important;
    }
    .card:hover .card-desc {
      opacity: 1 !important;
    }
    .card.expanded .card-desc {
      display: none;
    }
    .card .card-actions-row {
      position: static;
      top: auto;
      transform: none;
      opacity: 1;
      pointer-events: auto;
      margin-top: 10px;
    }
    .card .card-chevron {
      --mdc-icon-size: 22px;
      padding: 6px;
    }
    .card .burger-btn {
      width: 36px;
      height: 36px;
    }
    .card .card-actions-row > div:last-child {
      gap: 14px !important;
    }
    .card .refine-btn {
      font-size: 14px !important;
      padding: 10px 16px !important;
    }
    .burger-dropdown {
      min-width: 180px;
    }
    .burger-item {
      padding: 14px 18px;
      font-size: 15px;
      gap: 10px;
    }
    .burger-item ha-icon {
      --mdc-icon-size: 18px;
    }
  }
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 10px;
    gap: 10px;
  }
  .card h3 {
    margin: 0;
    font-size: 16px;
  }
  .card p {
    margin: 6px 0;
    color: var(--secondary-text-color);
    font-size: 13px;
  }
  pre {
    background: var(--selora-zinc-900);
    color: var(--selora-zinc-200);
    padding: 10px;
    border-radius: 8px;
    border: 1px solid var(--selora-zinc-800);
    font-size: 11px;
    overflow-x: auto;
  }
`;
