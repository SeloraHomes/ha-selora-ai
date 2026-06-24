import { css } from "lit";

export const automationsStyles = css`
  /* Automations list (table-like rows) */
  .automations-list {
    border: 1px solid var(--selora-zinc-700);
    border-radius: 12px;
  }
  .auto-row:first-child .auto-row-main {
    border-radius: 12px 12px 0 0;
  }
  .auto-row:last-child .auto-row-main {
    border-radius: 0 0 12px 12px;
  }
  .auto-row:only-child .auto-row-main {
    border-radius: 12px;
  }
  .auto-row {
    border-bottom: 1px solid var(--selora-zinc-700);
  }
  .auto-row:last-child {
    border-bottom: none;
  }
  .auto-row.disabled
    > .auto-row-main
    > :not(.burger-menu-wrapper):not(.auto-row-name):not(ha-icon) {
    opacity: 0.5;
  }
  .auto-row.disabled .auto-row-desc,
  .auto-row.disabled .auto-row-mobile-meta {
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
  .auto-row-title-row {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }
  .needs-attention-pill {
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 500;
    border-radius: 12px;
    background: #d32f2f;
    color: #fff;
    white-space: nowrap;
    flex-shrink: 0;
    cursor: pointer;
  }
  .needs-attention-pill:hover {
    background: #c62828;
  }
  .stale-pill {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 500;
    line-height: 1;
    border-radius: 12px;
    background: transparent;
    color: #f59e0b;
    border: 1px solid rgba(245, 158, 11, 0.4);
    white-space: nowrap;
    flex-shrink: 0;
    cursor: help;
  }
  .stale-pill ha-icon {
    --mdc-icon-size: 12px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 12px;
    height: 12px;
  }
  .selora-ai-mark {
    --mdc-icon-size: 12px;
    color: var(--selora-accent);
    flex-shrink: 0;
    opacity: 0.9;
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
  /* Scene desired-state list: each row = the entity's real HA tile
     (left, rendered with the scene's target state) + the final desired
     state spelled out (right). */
  .scene-ent-hint {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    margin-bottom: 10px;
    border-radius: 8px;
    background: var(--selora-accent-soft, rgba(245, 184, 64, 0.1));
    border: 1px solid var(--selora-accent-border, rgba(245, 184, 64, 0.25));
    font-size: 12px;
    color: var(--primary-text-color);
  }
  .scene-ent-hint ha-icon {
    --mdc-icon-size: 16px;
    color: var(--selora-accent);
    flex-shrink: 0;
  }
  .scene-edit-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
    margin-top: 12px;
    padding: 10px 12px;
    border-radius: 8px;
    background: var(--card-background-color, rgba(255, 255, 255, 0.03));
    border: 1px solid var(--selora-accent-border, rgba(245, 184, 64, 0.3));
  }
  .scene-edit-bar-msg {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    font-weight: 600;
    color: var(--primary-text-color);
  }
  .scene-edit-bar-msg ha-icon {
    --mdc-icon-size: 16px;
    color: var(--selora-accent);
  }
  .scene-edit-bar-actions {
    display: flex;
    gap: 8px;
  }
  .scene-ent-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .scene-ent-area {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 2px 2px 0;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--secondary-text-color);
  }
  .scene-ent-area:not(:first-child) {
    margin-top: 6px;
  }
  .scene-ent-area ha-icon {
    --mdc-icon-size: 14px;
    width: 14px;
    height: 14px;
  }
  .scene-ent-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 24px minmax(0, 1fr);
    align-items: center;
    gap: 16px;
  }
  /* Column headers — same grid template as the rows so "Now" sits over
     the live tiles and "Scene sets" over the forced tiles. Rendered once
     at the top of the list, not repeated per row. */
  .scene-ent-head {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 24px minmax(0, 1fr);
    gap: 16px;
    margin-bottom: 2px;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--secondary-text-color);
  }
  .scene-ent-cap--target {
    color: var(--selora-accent);
  }
  /* Each tile is a single-entity .selora-entity-grid. Override the chat
     grid's auto-fill columns + vertical margin so the lone tile fills
     its cell instead of capping at 280px. */
  .scene-ent-tile {
    min-width: 0;
    margin: 0;
    grid-template-columns: minmax(0, 1fr);
  }
  /* The forced (scene-target) tile is a read-only preview — block taps
     so the user can't drive the real device from it; the live "Now"
     tile on the left is the control. */
  .scene-ent-tile--forced {
    pointer-events: none;
  }
  .scene-ent-tile:empty {
    display: block;
    min-height: 56px;
    border-radius: 12px;
    border: 1px solid var(--selora-zinc-700);
    background: var(--card-background-color, rgba(255, 255, 255, 0.03));
    animation: scene-skel-pulse 1.2s ease-in-out infinite;
  }
  @keyframes scene-skel-pulse {
    0%,
    100% {
      opacity: 0.45;
    }
    50% {
      opacity: 0.85;
    }
  }
  .scene-ent-arrow {
    --mdc-icon-size: 20px;
    color: var(--secondary-text-color);
    justify-self: center;
  }
  @media (max-width: 600px) {
    .scene-ent-row {
      gap: 8px;
    }
    .scene-ent-arrow {
      --mdc-icon-size: 16px;
    }
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
    /* Hide scene rows' duplicate desc — the same text already renders in
       .auto-row-mobile-meta below. Automations keep their actual
       description visible (different content from the mobile meta). */
    .auto-row-desc--meta-only {
      display: none;
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
  /* Row 1 — tabs (status filters) on the left, primary action on the
     right. Tabs use underline-style; the action button keeps its pill
     look. Borders bottom of the row gives the underline-tabs effect. */
  .filter-tabs-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-top: 12px;
    margin-bottom: 12px;
    border-bottom: 1px solid var(--divider-color);
    flex-wrap: wrap;
  }
  .filter-tabs {
    display: flex;
    align-items: center;
    gap: 2px;
    flex-wrap: wrap;
  }
  .filter-tab {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 14px;
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    font-family: inherit;
    font-size: 13px;
    font-weight: 500;
    color: var(--secondary-text-color);
    cursor: pointer;
    line-height: 1;
    margin-bottom: -1px;
    transition:
      color 0.2s,
      border-color 0.2s;
  }
  .filter-tab:hover {
    color: var(--primary-text-color);
  }
  .filter-tab.active {
    color: var(--selora-accent-text);
    border-bottom-color: var(--selora-accent);
  }
  :host(:not([dark])) .filter-tab.active {
    color: var(--primary-text-color);
  }
  /* Row 2 — filter input + sort select. */
  .filter-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 12px;
    flex-wrap: wrap;
  }
  /* Unified 36px control height across row 2. */
  .filter-row .filter-input-wrap,
  .filter-row .sort-select {
    box-sizing: border-box;
    height: 36px;
  }
  .filter-row .filter-input-wrap {
    padding: 0 12px;
  }
  .filter-row .sort-select {
    padding: 0 34px 0 14px;
  }
  .filter-tabs-actions {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 0;
    flex-shrink: 0;
  }
  .filter-row-secondary {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    height: 36px;
    padding: 0 14px;
    border-radius: 10px;
    border: 1px solid var(--selora-inner-card-border);
    background: var(--selora-inner-card-bg);
    color: var(--primary-text-color);
    font-size: 13px;
    font-weight: 500;
    font-family: inherit;
    line-height: 1;
    cursor: pointer;
    white-space: nowrap;
    box-sizing: border-box;
  }
  .filter-row-secondary:hover {
    border-color: var(--selora-accent);
  }
  .sort-group {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .sort-dir-toggle {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    box-sizing: border-box;
    width: 36px;
    height: 36px;
    border-radius: 10px;
    border: 1px solid var(--selora-inner-card-border);
    background: var(--selora-inner-card-bg);
    color: var(--primary-text-color);
    cursor: pointer;
    flex-shrink: 0;
    padding: 0;
  }
  .sort-dir-toggle:hover {
    border-color: var(--selora-accent);
  }
  .filter-row-action {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    height: 36px;
    padding: 0 16px;
    border-radius: 999px;
    border: 1px solid rgba(251, 191, 36, 0.35);
    background: rgba(251, 191, 36, 0.08);
    color: var(--selora-accent-text);
    font-size: 13px;
    font-weight: 500;
    font-family: inherit;
    line-height: 1;
    cursor: pointer;
    white-space: nowrap;
  }
  .filter-row-action:hover:not(:disabled) {
    background: rgba(251, 191, 36, 0.14);
    border-color: var(--selora-accent);
  }
  .filter-row-action:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  :host(:not([dark])) .filter-row-action {
    background: var(--selora-accent);
    border-color: var(--selora-accent);
    color: #000;
  }
  :host(:not([dark])) .filter-row-action:hover:not(:disabled) {
    background: var(--selora-accent-light);
    border-color: var(--selora-accent-light);
  }
  @media (max-width: 600px) {
    /* Row 1 — tabs scroll horizontally if they don't fit; the actions
       group (Bulk edit + New Automation) drops below the tab strip and
       spans full width with the buttons sharing the row. */
    .filter-tabs-row {
      gap: 8px;
    }
    .filter-tabs {
      flex: 1 1 100%;
      overflow-x: auto;
      flex-wrap: nowrap;
    }
    .filter-tab {
      flex: 0 0 auto;
    }
    .filter-tabs-actions {
      flex: 1 1 100%;
      justify-content: flex-end;
    }
    /* Row 2 — input on its own line, sort group below. */
    .filter-row {
      gap: 8px;
    }
    .filter-row .filter-input-wrap {
      flex: 1 1 100% !important;
    }
    .sort-group {
      flex: 1 1 100%;
    }
    .sort-group .sort-select {
      flex: 1 1 auto;
      min-width: 0;
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
    font-size: 13px;
    font-weight: 500;
    font-family: inherit;
    line-height: 1.2;
    padding: 9px 34px 9px 14px;
    border-radius: 10px;
    border: 1px solid var(--selora-inner-card-border);
    background-color: var(--selora-inner-card-bg);
    color: var(--primary-text-color);
    cursor: pointer;
    transition:
      border-color 0.2s,
      background-color 0.2s;
    /* Hide the native chevron and draw our own so the select looks like
       the rest of the UI in both light and dark modes. */
    appearance: none;
    -webkit-appearance: none;
    -moz-appearance: none;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path fill='%23a1a1aa' d='M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6 1.41-1.41z'/></svg>");
    background-repeat: no-repeat;
    background-position: right 10px center;
    background-size: 16px 16px;
  }
  .sort-select:hover {
    border-color: var(--selora-accent);
  }
  .sort-select:focus {
    outline: none;
    border-color: var(--selora-accent);
  }
  :host(:not([dark])) .sort-select {
    border-color: var(--selora-inner-card-border);
    background-color: var(--selora-inner-card-bg);
  }
  :host(:not([dark])) .sort-select:hover {
    border-color: var(--selora-accent);
  }
  .automations-summary {
    font-size: 12px;
    color: var(--secondary-text-color);
    margin-bottom: 12px;
  }
  /* Bulk edit (and Done) buttons inside the summary row use the same
     inner-card background as filter input and sort select so the three
     controls read as one visual family. */
  .automations-summary .btn-outline,
  .automations-summary .btn-outline:hover {
    box-sizing: border-box;
    height: 36px;
    padding: 0 14px;
    font-size: 13px;
    line-height: 1;
    border-radius: 10px;
    background: var(--selora-inner-card-bg);
    border-color: var(--selora-inner-card-border);
    color: var(--primary-text-color);
  }
  .automations-summary .btn-outline:hover {
    border-color: var(--selora-accent);
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

  /* ---- Suggestions/automations grid ----
     Auto-fill with a minimum card width so the grid drops columns based on
     its actual rendered width, not window.innerWidth. This avoids the
     case where HA's sidebar is open and the panel container is narrow
     while the window is wide — we'd render 3 columns and the action
     buttons inside cards would overflow. 280px is the minimum width that
     keeps the Accept/Dismiss button row from spilling. */
  .automations-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    align-items: start;
    gap: 20px;
    margin-bottom: 16px;
  }
  .automations-grid .masonry-col {
    display: contents;
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
    /* Wrap up to 2 lines on desktop (avoid layout breakage in the masonry
       grid). Native title attribute / DOM tooltip reveals the full text. */
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    word-break: break-word;
  }
  /* On touch devices the suggestion cards stack full-width, so let the
     title wrap freely instead of clamping. */
  @media (hover: none) {
    .automations-grid .card h3 {
      -webkit-line-clamp: unset;
      line-clamp: unset;
      display: block;
      overflow: visible;
    }
  }
  /* Suggestion click-to-expand: subtitle shares the 2-line clamp, and the
     .expanded modifier unclamps both the subtitle and the h3 rule above. */
  .automations-grid .card .clamp-2 {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    word-break: break-word;
  }
  .automations-grid .card h3.expanded,
  .automations-grid .card .clamp-2.expanded {
    display: block;
    -webkit-line-clamp: unset;
    line-clamp: unset;
    overflow: visible;
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
