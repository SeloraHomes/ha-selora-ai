import { css } from "lit";

export const usageStyles = css`
  .usage-view {
    max-width: 720px;
    margin: 0 auto;
    padding: 16px 0 24px;
    display: flex;
    flex-direction: column;
    gap: 18px;
  }

  /* Breadcrumb-style back link sits above the title, muted, no chrome.
     Click target keeps a comfortable 36px height for touch. */
  .usage-crumb {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    padding: 6px 0;
    margin: 0 0 -4px;
    font-size: 13px;
    color: var(--secondary-text-color);
    text-decoration: none;
    align-self: flex-start;
    transition: color 0.15s;
  }
  .usage-crumb:hover {
    color: var(--primary-text-color);
  }
  .usage-crumb ha-icon {
    --mdc-icon-size: 18px;
    margin-left: -4px;
  }

  .usage-title-row {
    display: flex;
    align-items: baseline;
    gap: 14px;
    flex-wrap: wrap;
  }
  .usage-title-row h2 {
    font-size: 24px;
    font-weight: 700;
    margin: 0;
    letter-spacing: -0.01em;
  }
  .usage-subtitle {
    font-size: 13px;
    color: var(--secondary-text-color);
    font-variant-numeric: tabular-nums;
  }

  .usage-tile-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px;
    margin-top: 4px;
  }
  .usage-tile {
    padding: 14px 16px;
    border-radius: 12px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color);
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }
  .usage-tile-head {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--secondary-text-color);
    font-size: 12px;
    font-weight: 500;
  }
  .usage-tile-label {
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .usage-tile-value {
    font-size: 24px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    color: var(--primary-text-color);
  }
  .usage-tile-sub {
    font-size: 11px;
    color: var(--secondary-text-color);
  }

  .usage-period-row {
    display: flex;
    align-items: baseline;
    gap: 12px;
    padding: 10px 0;
    border-bottom: 1px solid var(--divider-color);
    font-variant-numeric: tabular-nums;
  }
  .usage-period-row:last-of-type {
    border-bottom: none;
  }
  .usage-period-title {
    font-size: 13px;
    color: var(--secondary-text-color);
    width: 110px;
    flex-shrink: 0;
  }
  .usage-period-cost {
    font-size: 16px;
    font-weight: 600;
    color: var(--primary-text-color);
  }
  .usage-period-tokens {
    font-size: 12px;
    color: var(--secondary-text-color);
    margin-left: auto;
  }
  .usage-period-empty,
  .usage-period-loading {
    font-size: 13px;
    color: var(--secondary-text-color);
    font-style: italic;
  }
  .usage-period-note {
    margin-top: 12px;
    padding: 10px 12px;
    border-radius: 8px;
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.04));
    font-size: 12px;
    color: var(--secondary-text-color);
    line-height: 1.5;
  }

  .usage-help {
    font-size: 13px;
    color: var(--secondary-text-color);
    line-height: 1.55;
    margin: 0 0 8px;
  }
  .usage-help:last-child {
    margin-bottom: 0;
  }
  .usage-help a {
    color: var(--primary-color);
    text-decoration: none;
  }
  .usage-help a:hover {
    text-decoration: underline;
  }
  .usage-help code {
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.06));
    padding: 1px 6px;
    border-radius: 4px;
    font-size: 12px;
  }

  .usage-empty {
    display: flex;
    align-items: flex-start;
    gap: 12px;
  }
  .usage-empty p {
    margin: 4px 0 0;
    font-size: 13px;
    color: var(--secondary-text-color);
    line-height: 1.5;
  }

  .usage-section-sub {
    font-size: 11px;
    color: var(--secondary-text-color);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    line-height: 1.2;
  }
  /* Heading next to a small caption: align on the text baseline so the
     two label rows read as one. The default 'align-items: center' for
     section headers centers line boxes, which leaves big+small text
     visually misaligned. */
  .section-card-header:has(.usage-section-sub) {
    align-items: baseline;
  }

  .usage-breakdown {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .usage-breakdown-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .usage-breakdown-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    font-variant-numeric: tabular-nums;
  }
  .usage-breakdown-label {
    font-size: 14px;
    font-weight: 600;
    color: var(--primary-text-color);
  }
  .usage-breakdown-cost {
    font-size: 14px;
    font-weight: 600;
    color: var(--primary-text-color);
  }
  .usage-breakdown-bar {
    height: 6px;
    border-radius: 999px;
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.06));
    overflow: hidden;
  }
  .usage-breakdown-bar-fill {
    height: 100%;
    background: linear-gradient(
      90deg,
      rgba(251, 191, 36, 0.85),
      rgba(184, 134, 11, 0.85)
    );
    border-radius: 999px;
    transition: width 0.4s ease;
  }
  .usage-breakdown-meta {
    display: flex;
    gap: 8px;
    font-size: 12px;
    color: var(--secondary-text-color);
    font-variant-numeric: tabular-nums;
  }
  .usage-breakdown-intents {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 2px;
  }
  .usage-intent-pill {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 999px;
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.06));
    color: var(--secondary-text-color);
    font-variant-numeric: tabular-nums;
  }

  .usage-recent-list {
    display: flex;
    flex-direction: column;
  }
  .usage-recent-row {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 10px 0;
    border-bottom: 1px solid var(--divider-color);
  }
  .usage-recent-row:last-child {
    border-bottom: none;
  }
  .usage-recent-main {
    display: flex;
    align-items: baseline;
    gap: 8px;
    flex-wrap: wrap;
  }
  .usage-recent-kind {
    font-weight: 600;
    color: var(--primary-text-color);
    font-size: 13px;
  }
  .usage-recent-intent {
    font-size: 12px;
    color: var(--secondary-text-color);
  }
  .usage-recent-time {
    margin-left: auto;
    font-size: 11px;
    color: var(--secondary-text-color);
    font-variant-numeric: tabular-nums;
  }
  .usage-recent-details {
    display: flex;
    align-items: baseline;
    gap: 10px;
    font-size: 12px;
    color: var(--secondary-text-color);
    font-variant-numeric: tabular-nums;
  }
  .usage-recent-model {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .usage-recent-tokens {
    text-align: right;
    min-width: 64px;
  }
  .usage-recent-cost {
    color: var(--primary-text-color);
    font-weight: 500;
    text-align: right;
    min-width: 60px;
  }

  /* Right-aligned action link inside a section-card-header. Used for
     drill-downs from Settings → sub-page (e.g. token usage). Designed to
     sit alongside the header title without competing with primary CTAs. */
  .section-card-header--with-action {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .section-card-action {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 8px;
    margin-right: -8px;
    border: none;
    background: transparent;
    color: var(--secondary-text-color);
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
    border-radius: 6px;
    transition:
      color 0.15s,
      background 0.15s;
  }
  .section-card-action:hover {
    color: var(--primary-text-color);
    background: var(--secondary-background-color, rgba(255, 255, 255, 0.04));
  }
  .section-card-action ha-icon {
    --mdc-icon-size: 16px;
  }
  .section-card-action-chevron {
    --mdc-icon-size: 16px !important;
    opacity: 0.6;
    margin-left: -2px;
  }

  /* Pricing override: side-by-side input/output cells with a default hint
     line beneath each value. Edit row drops to a column on narrow screens
     so the textfields stay legible. */
  .usage-pricing-row {
    display: flex;
    gap: 12px;
    margin: 8px 0 12px;
    flex-wrap: wrap;
  }
  .usage-pricing-cell {
    flex: 1;
    min-width: 140px;
    padding: 10px 14px;
    border-radius: 10px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color);
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .usage-pricing-label {
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--secondary-text-color);
  }
  .usage-pricing-value {
    font-size: 18px;
    font-weight: 600;
    color: var(--primary-text-color);
    font-variant-numeric: tabular-nums;
  }
  .usage-pricing-default {
    font-size: 11px;
    color: var(--secondary-text-color);
  }
  .usage-pricing-edit {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: flex-end;
    margin: 4px 0 12px;
  }
  .usage-pricing-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }

  .usage-sensor-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin: 8px 0 4px;
    padding: 10px 14px;
    border-radius: 10px;
    border: 1px solid var(--divider-color);
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.03));
  }
  .usage-sensor-row {
    display: flex;
    align-items: baseline;
    gap: 10px;
    font-size: 12px;
    min-width: 0;
  }
  .usage-sensor-row code {
    font-size: 11px;
    background: var(--card-background-color);
    color: var(--primary-text-color);
    padding: 2px 6px;
    border-radius: 4px;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .usage-sensor-name {
    color: var(--secondary-text-color);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
`;
