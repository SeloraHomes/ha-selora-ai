import { css } from "lit";

export const automationsStyles = css`
  /* Automations list (table-like rows) */
  .automations-list {
    border: 1px solid var(--selora-zinc-700);
    border-radius: 12px;
    overflow: hidden;
  }
  .auto-row {
    border-bottom: 1px solid var(--selora-zinc-700);
  }
  .auto-row:last-child {
    border-bottom: none;
  }
  .auto-row.disabled > .auto-row-main > :not(.burger-menu-wrapper) {
    opacity: 0.5;
  }
  .auto-row.highlighted {
    animation: highlightRow 3s ease;
  }
  .card.fading-out {
    animation: fadeOutCard 0.6s ease forwards;
    pointer-events: none;
  }
  .suggestions-section .automations-grid .card {
    animation: slideInCard 0.4s ease both;
  }
  .suggestions-section .automations-grid .card.fading-out {
    animation: fadeOutCard 0.6s ease forwards;
  }
  .auto-row-main {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 16px;
    cursor: pointer;
    transition: background 0.15s;
  }
  .auto-row-main:hover {
    background: var(--secondary-background-color);
  }
  .auto-row-name {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .auto-row-title {
    font-size: 14px;
    font-weight: 500;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .auto-row-desc {
    font-size: 12px;
    color: var(--secondary-text-color);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .auto-row-last-run {
    font-size: 12px;
    color: var(--secondary-text-color);
    white-space: nowrap;
    flex-shrink: 0;
    position: relative;
    cursor: default;
  }
  .auto-row-last-run .setting-tooltip {
    display: none;
    position: absolute;
    bottom: calc(100% + 8px);
    right: 0;
    left: auto;
    transform: none;
    width: auto;
    white-space: nowrap;
    padding: 10px 12px;
    background: var(--card-background-color, #1e1e1e);
    color: var(--primary-text-color);
    font-size: 12px;
    font-weight: 400;
    line-height: 1.5;
    border-radius: 8px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
    z-index: 10;
    pointer-events: none;
  }
  .auto-row-last-run .setting-tooltip::after {
    content: "";
    position: absolute;
    top: 100%;
    left: auto;
    right: 12px;
    transform: none;
    border: 6px solid transparent;
    border-top-color: var(--card-background-color, #1e1e1e);
  }
  .auto-row-last-run:hover .setting-tooltip {
    display: block;
  }
  .auto-row-expand {
    padding: 0 16px 16px;
  }
  .last-run-prefix {
    display: none;
  }
  .auto-row-mobile-meta {
    display: none;
  }
  @media (max-width: 600px) {
    .auto-row-main {
      align-items: flex-start;
    }
    .auto-row-title {
      white-space: normal;
    }
    .auto-row-desc {
      white-space: normal;
    }
    .auto-row-last-run {
      display: none;
    }
    .auto-row-mobile-meta {
      display: flex;
      align-items: center;
      font-size: 11px;
      opacity: 0.45;
      color: var(--secondary-text-color);
      margin-top: 6px;
    }
    .auto-row-mobile-meta .card-chevron {
      position: absolute;
      right: 16px;
      bottom: 12px;
    }
    .auto-row-main {
      position: relative;
      padding-bottom: 8px;
    }
  }
  .filter-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 12px;
    flex-wrap: wrap;
  }
  @media (max-width: 600px) {
    .filter-row {
      gap: 8px;
    }
    .filter-row .filter-input-wrap {
      flex: 1 1 100% !important;
    }
    .filter-row .status-pills {
      flex: 1;
    }
    .filter-row .sort-select {
      flex: 1;
    }
    .automations-summary span:first-child {
      display: none;
    }
  }
  .status-pills {
    display: inline-flex;
    gap: 2px;
    background: var(--selora-zinc-900);
    border: 1px solid var(--selora-zinc-700);
    border-radius: 8px;
    padding: 2px;
  }
  .status-pill {
    padding: 4px 12px;
    border: none;
    background: transparent;
    font-size: 12px;
    font-weight: 500;
    font-family: inherit;
    color: var(--secondary-text-color);
    cursor: pointer;
    border-radius: 6px;
    transition: all 0.2s ease;
  }
  .status-pill:hover {
    color: var(--primary-text-color);
    background: var(--secondary-background-color);
  }
  .status-pill.active {
    background: var(--selora-zinc-700);
    color: var(--primary-text-color);
    font-weight: 600;
  }
  .sort-select {
    font-size: 12px;
    font-weight: 500;
    font-family: inherit;
    padding: 6px 10px;
    border-radius: 8px;
    border: 1px solid var(--selora-zinc-700);
    background: var(--selora-zinc-900);
    color: var(--primary-text-color);
    cursor: pointer;
    transition: border-color 0.3s;
  }
  .sort-select:hover {
    border-color: rgba(251, 191, 36, 0.5);
  }
  .automations-summary {
    font-size: 12px;
    color: var(--secondary-text-color);
    margin-bottom: 12px;
  }
  .filter-input-wrap {
    display: flex;
    align-items: center;
    gap: 6px;
    background: var(--selora-inner-card-bg);
    border: 1px solid var(--selora-inner-card-border);
    border-radius: 10px;
    padding: 6px 12px;
    flex: 0 1 400px;
    transition: border-color 0.3s;
  }
  .filter-input-wrap:focus-within {
    border-color: var(--selora-accent);
  }
  .filter-input-wrap input {
    border: none;
    background: transparent;
    color: var(--primary-text-color);
    font-size: 13px;
    font-family: inherit;
    outline: none;
    flex: 1;
    min-width: 0;
  }
  .filter-input-wrap ha-icon {
    --mdc-icon-size: 16px;
    color: var(--secondary-text-color);
    flex-shrink: 0;
  }
  .bulk-select-all {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--secondary-text-color);
  }
  .bulk-select-all input {
    width: 16px;
    height: 16px;
    margin: 0;
    accent-color: var(--selora-accent);
    cursor: pointer;
  }
  .bulk-actions-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin: -2px 0 12px;
    padding: 8px 10px;
    border: 1px solid var(--divider-color);
    border-radius: 8px;
    background: var(--secondary-background-color);
  }
  .bulk-actions-row .left {
    font-size: 12px;
    font-weight: 600;
  }
  .bulk-actions-row .actions {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }
  .card-select {
    display: inline-flex;
    align-items: center;
    margin-right: 6px;
  }
  .card-select input {
    width: 16px;
    height: 16px;
    margin: 0;
    accent-color: var(--selora-accent);
    cursor: pointer;
  }

  /* ---- Automations grid (flex columns for independent heights) ---- */
  .automations-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    align-items: stretch;
    gap: 20px;
    margin-bottom: 16px;
  }
  .automations-grid .masonry-col {
    display: contents;
  }
  @media (max-width: 900px) {
    .automations-grid {
      grid-template-columns: repeat(2, 1fr);
    }
  }
  @media (max-width: 600px) {
    .automations-grid {
      grid-template-columns: 1fr;
    }
  }
  .pagination {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 12px;
    padding: 12px 0;
  }
  .page-info {
    font-size: 12px;
    opacity: 0.6;
  }
  .per-page-label {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    font-weight: 500;
    white-space: nowrap;
    color: var(--secondary-text-color);
  }
  .per-page-select {
    font-size: 13px;
    font-weight: 500;
    font-family: inherit;
    padding: 6px 10px;
    border-radius: 10px;
    border: 1px solid var(--selora-zinc-700);
    background: transparent;
    color: var(--primary-text-color);
    cursor: pointer;
    transition: border-color 0.3s;
  }
  .per-page-select:hover {
    border-color: rgba(251, 191, 36, 0.5);
  }
  .automations-grid .card {
    margin-bottom: 0;
    padding: 16px 18px;
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .automations-grid .card-header {
    margin-bottom: 0;
    align-items: center;
  }
  .automations-grid .card h3 {
    font-size: 13px;
    line-height: 1.3;
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .automations-grid .card-meta {
    font-size: 11px;
    color: var(--secondary-text-color);
    opacity: 0.7;
  }

  /* ---- Automation detail drawer (below grid) ---- */
  .automation-detail-drawer {
    background: var(--card-background-color);
    border: 1px solid var(--divider-color);
    border-radius: 10px;
    padding: 16px;
    margin-bottom: 14px;
    box-shadow: var(--card-box-shadow);
  }
  .automation-detail-drawer .detail-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;
  }
  .automation-detail-drawer .detail-header h3 {
    margin: 0;
    font-size: 16px;
  }
`;
