import { css } from "lit";

export const settingsStyles = css`
  .settings-form {
    max-width: 640px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }
  .settings-section {
    /* inherits from .section-card */
  }
  .settings-section-title {
    font-size: 13px;
    font-weight: 500;
    color: var(--secondary-text-color);
    margin: 0 0 16px;
  }
  .form-group {
    margin-bottom: 18px;
  }
  .form-group:last-child {
    margin-bottom: 0;
  }
  .form-group label {
    display: block;
    margin-bottom: 6px;
    font-weight: 500;
    font-size: 13px;
    color: var(--secondary-text-color);
  }
  .form-select {
    width: 100%;
    padding: 10px 12px;
    border-radius: 10px;
    background: var(--selora-zinc-900);
    color: var(--primary-text-color);
    border: 1px solid var(--selora-zinc-700);
    font-size: 14px;
    appearance: none;
    -webkit-appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23a1a1aa' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 12px center;
    cursor: pointer;
    transition: border-color 0.2s;
  }
  .form-select:focus {
    outline: none;
    border-color: var(--selora-accent);
  }
  .key-hint {
    font-size: 12px;
    color: var(--selora-zinc-400);
    font-family: monospace;
    padding: 6px 10px;
    background: var(--selora-zinc-900);
    border: 1px solid var(--selora-zinc-700);
    border-radius: 8px;
    display: inline-block;
    margin-top: 4px;
  }
  .key-not-set {
    font-size: 12px;
    color: var(--selora-zinc-400);
    font-style: italic;
    margin-top: 4px;
  }
  .service-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 0;
  }
  .service-row:not(:last-child) {
    border-bottom: 1px solid var(--selora-zinc-700);
  }
  .service-row label {
    font-size: 14px;
    font-weight: 500;
    color: var(--primary-text-color);
    flex: 1;
  }
  .service-details {
    padding: 16px 0 0 0;
    margin-bottom: 12px;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .advanced-section {
    padding: 0;
    overflow: hidden;
  }
  .advanced-toggle {
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
    font-size: 15px;
    font-weight: 500;
    color: var(--primary-text-color);
    list-style: none;
    padding: 16px 20px;
    transition: background 0.15s;
  }
  .advanced-toggle::-webkit-details-marker {
    display: none;
  }
  .advanced-toggle::marker {
    display: none;
    content: "";
  }
  .advanced-toggle:hover {
    background: var(--secondary-background-color);
  }
  .advanced-chevron {
    --mdc-icon-size: 18px;
    transition: transform 0.2s;
    opacity: 0.5;
  }
  .advanced-section[open] > .advanced-toggle .advanced-chevron {
    transform: rotate(90deg);
  }
  .advanced-section[open] > .advanced-toggle {
    border-bottom: 1px solid var(--divider-color);
  }
  .advanced-section .service-row:first-of-type {
    padding-top: 16px;
  }
  .advanced-section .service-row,
  .advanced-section .service-details,
  .advanced-section .settings-section-title,
  .advanced-section .settings-separator {
    margin-left: 20px;
    margin-right: 20px;
  }
  .advanced-section .service-row:last-of-type {
    padding-bottom: 16px;
  }
  .settings-form ha-switch {
    --switch-checked-color: var(--selora-accent);
    --switch-checked-button-color: var(--selora-accent);
    --switch-checked-track-color: var(--selora-accent-dark);
    --mdc-theme-secondary: var(--selora-accent);
  }
  .service-row label {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .setting-help {
    position: relative;
    cursor: help;
    --mdc-icon-size: 16px;
    color: var(--secondary-text-color);
    flex-shrink: 0;
  }
  .setting-help:hover {
    color: var(--primary-text-color);
  }
  .setting-help .setting-tooltip {
    display: none;
    position: absolute;
    bottom: calc(100% + 8px);
    left: 50%;
    transform: translateX(-50%);
    width: 240px;
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
  .setting-help .setting-tooltip::after {
    content: "";
    position: absolute;
    top: 100%;
    left: 50%;
    transform: translateX(-50%);
    border: 6px solid transparent;
    border-top-color: var(--card-background-color, #1e1e1e);
  }
  .setting-help:hover .setting-tooltip {
    display: block;
  }
  .settings-separator {
    border: none;
    border-top: 1px solid var(--selora-zinc-700);
    margin: 16px 0 4px;
  }
  .save-bar {
    display: flex;
    justify-content: flex-end;
  }
`;
