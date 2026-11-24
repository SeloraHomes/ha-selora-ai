import { css } from "lit";

// Temporary browser-code access (render-file-access.js): the header indicator
// and the dialog, a standard modal.
export const fileAccessStyles = css`
  /* Header indicator: the header's own 44px icon button (header.css.js),
     gold in dark mode and HA's text colour in light mode like its
     neighbours. A label (the end time) widens it into a pill. */
  .file-access-indicator {
    position: relative;
    background: none;
    border: none;
    cursor: pointer;
    min-width: 44px;
    height: 44px;
    padding: 0 10px;
    border-radius: 999px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
    flex-shrink: 0;
    font-family: inherit;
    font-size: 13px;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
    color: var(--selora-accent-text, #f59e0b);
    --mdc-icon-size: 22px;
    transition: background 0.2s;
  }
  .file-access-indicator:hover {
    background: rgba(251, 191, 36, 0.12);
  }
  :host(:not([dark])) .file-access-indicator {
    color: var(--primary-text-color);
  }
  :host(:not([dark])) .file-access-indicator:hover {
    background: rgba(0, 0, 0, 0.06);
  }
  .file-access-indicator--ended {
    color: var(
      --sidebar-icon-color,
      var(--app-header-text-color, var(--primary-text-color))
    );
  }
  .file-access-indicator-dot {
    position: absolute;
    top: 9px;
    right: 9px;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--warning-color, #ffa726);
    animation: file-access-pulse 1.6s ease-in-out infinite;
  }
  @keyframes file-access-pulse {
    50% {
      opacity: 0.35;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .file-access-indicator-dot {
      animation: none;
    }
  }

  .file-access-dialog {
    max-width: 520px;
    width: calc(100% - 32px);
    max-height: 85vh;
    overflow-y: auto;
    text-align: left;
  }
  .file-access-reason {
    margin: 8px 0 0;
    padding: 4px 14px;
    border-left: 3px solid var(--divider-color, rgba(127, 127, 127, 0.3));
    font-size: 14px;
    line-height: 1.5;
    font-style: italic;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .file-access-status,
  .file-access-empty {
    margin: 14px 0 0;
    font-size: 13px;
    line-height: 1.5;
  }
  .file-access-risk {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    margin-top: 12px;
    padding: 10px 14px;
    border-radius: 12px;
    background: rgba(255, 167, 38, 0.14);
    font-size: 13px;
    line-height: 1.45;
  }
  .file-access-risk ha-icon {
    --mdc-icon-size: 20px;
    flex: none;
    color: var(--warning-color, #ffa726);
  }
  .file-access-summary {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 8px;
    font-size: 13px;
    color: var(--secondary-text-color);
  }
  .file-access-summary ha-icon {
    --mdc-icon-size: 20px;
    color: var(--success-color, #43a047);
  }
  .file-access-points {
    list-style: none;
    margin: 8px 0 0;
    padding: 4px 0;
    border-radius: 12px;
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.08));
  }
  .file-access-points li {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 14px;
    font-size: 13px;
  }
  .file-access-points ha-icon {
    --mdc-icon-size: 20px;
    flex: none;
    color: var(--secondary-text-color);
  }
  .file-access-via {
    margin: 10px 0 0;
    font-size: 12px;
    color: var(--secondary-text-color);
  }
  .file-access-footer {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-top: 20px;
  }
  .file-access-duration {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 13px;
    color: var(--secondary-text-color);
  }
  .file-access-footer-buttons {
    display: flex;
    gap: 10px;
    margin-left: auto;
  }
  /* A choice, not an action: one gold button per dialog, and it is Allow. */
  .file-access-segments {
    display: inline-flex;
    padding: 3px;
    gap: 2px;
    border-radius: 10px;
    border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.3));
  }
  .file-access-segment {
    min-height: 32px;
    padding: 0 14px;
    border: 0;
    border-radius: 7px;
    background: transparent;
    color: var(--secondary-text-color);
    font: inherit;
    font-size: 13px;
    cursor: pointer;
  }
  .file-access-segment:hover {
    color: var(--primary-text-color);
  }
  .file-access-segment[aria-checked="true"] {
    background: rgba(127, 127, 127, 0.22);
    color: var(--primary-text-color);
    font-weight: 600;
  }
  .file-access-segment:focus-visible {
    outline: 2px solid var(--primary-color);
    outline-offset: 1px;
  }
  .file-access-actions {
    display: flex;
    justify-content: flex-end;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 20px;
  }

  .file-access-section-label {
    margin: 22px 0 0;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    opacity: 0.55;
  }
  .file-access-file {
    margin-top: 10px;
    padding: 14px 16px;
    border-radius: 12px;
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.08));
  }
  .file-access-file-head {
    display: flex;
    align-items: flex-start;
    gap: 12px;
  }
  .file-access-file-icon {
    flex: none;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 36px;
    height: 36px;
    border-radius: 9px;
    background: rgba(127, 127, 127, 0.14);
  }
  .file-access-file-icon ha-icon {
    --mdc-icon-size: 20px;
  }
  .file-access-file-title {
    min-width: 0;
  }
  .file-access-chips-label {
    margin-top: 8px;
    font-size: 11px;
    opacity: 0.75;
  }
  .file-access-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 4px;
  }
  .file-access-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 3px 10px 3px 8px;
    border-radius: 999px;
    background: rgba(127, 127, 127, 0.16);
    color: var(--primary-text-color);
    font-size: 12px;
  }
  .file-access-chip ha-icon {
    --mdc-icon-size: 16px;
  }
  .file-access-file-name {
    font-size: 15px;
    font-weight: 600;
    overflow-wrap: anywhere;
  }
  .file-access-write,
  .file-access-clean {
    margin-top: 4px;
    font-size: 12px;
    color: var(--secondary-text-color);
    overflow-wrap: anywhere;
  }
  .file-access-file-use {
    margin: 10px 0 0;
    font-size: 13px;
    line-height: 1.5;
  }
  .file-access-checks-label {
    margin-top: 14px;
    font-size: 12px;
    color: var(--secondary-text-color);
  }
  .file-access-findings {
    list-style: none;
    margin: 6px 0 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .file-access-finding {
    padding: 8px 12px;
    border-radius: 8px;
    font-size: 12px;
    background: rgba(127, 127, 127, 0.1);
  }
  .file-access-finding--high {
    background: rgba(239, 68, 68, 0.14);
    color: var(--error-color, #ef4444);
  }
  .file-access-finding--medium {
    background: rgba(255, 167, 38, 0.16);
  }
  .file-access-finding-kind {
    font-size: 13px;
    font-weight: 600;
  }
  .file-access-finding-body {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 6px 8px;
    margin-top: 4px;
  }
  .file-access-finding-earlier {
    font-style: italic;
    opacity: 0.75;
  }
  .file-access-finding-line {
    opacity: 0.7;
  }
  .file-access-finding-note {
    margin-top: 8px;
    opacity: 0.75;
  }
  .file-access-timer {
    margin: 18px 0 0;
  }
  .file-access-timer-bar {
    height: 4px;
    border-radius: 2px;
    background: rgba(127, 127, 127, 0.2);
    overflow: hidden;
  }
  .file-access-timer-bar span {
    display: block;
    height: 100%;
    border-radius: inherit;
    background: var(--success-color, #43a047);
    transition: width 0.6s ease;
  }
  .file-access-timer-row {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin-bottom: 8px;
  }
  .file-access-timer-left {
    font-size: 28px;
    font-weight: 500;
    line-height: 1;
    font-variant-numeric: tabular-nums;
    color: var(--success-color, #43a047);
  }
  .file-access-timer-note {
    font-size: 12px;
    color: var(--secondary-text-color);
  }
  @media (prefers-reduced-motion: reduce) {
    .file-access-timer-bar span {
      transition: none;
    }
  }
  .file-access-finding code {
    min-width: 0;
    overflow-wrap: anywhere;
    color: var(--primary-text-color);
  }
`;
