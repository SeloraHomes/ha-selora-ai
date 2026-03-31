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
  .burger-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border-radius: 6px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color);
    cursor: pointer;
    color: var(--secondary-text-color);
    transition: background 0.15s;
  }
  .burger-btn:hover {
    background: rgba(0, 0, 0, 0.06);
    color: var(--primary-text-color);
  }
  .burger-dropdown {
    position: absolute;
    right: 0;
    top: 32px;
    background: var(--card-background-color);
    border: 1px solid var(--divider-color);
    border-radius: 8px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
    z-index: 100;
    min-width: 140px;
    overflow: hidden;
  }
  .burger-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 14px;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    color: var(--primary-text-color);
    border: none;
    background: none;
    width: 100%;
    text-align: left;
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
    padding: 4px 10px;
    border: none;
    background: none;
    font-size: 12px;
    font-weight: 500;
    color: var(--secondary-text-color);
    cursor: pointer;
    position: relative;
    transition: color 0.3s ease;
    display: inline-flex;
    align-items: center;
    gap: 4px;
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
    font-size: 12px;
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
