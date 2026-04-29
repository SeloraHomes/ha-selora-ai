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
  .settings-doc-banner {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 14px 18px;
    border-radius: 12px;
    background: rgba(251, 191, 36, 0.06);
    border: 1px solid rgba(251, 191, 36, 0.15);
    color: var(--primary-text-color);
    text-decoration: none;
    transition:
      background 0.15s,
      border-color 0.15s;
  }
  .settings-doc-banner:hover {
    background: rgba(251, 191, 36, 0.1);
    border-color: rgba(251, 191, 36, 0.3);
  }
  .settings-doc-banner strong {
    font-size: 13px;
    font-weight: 600;
  }
  .settings-doc-banner span {
    display: block;
    font-size: 12px;
    color: var(--secondary-text-color);
    margin-top: 2px;
  }
  .section-subtitle {
    font-size: 13px;
    color: var(--secondary-text-color);
    margin: 4px 0 0;
    font-weight: 400;
  }
  .settings-section-title {
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.05em;
    color: var(--secondary-text-color);
    margin: 20px 0 12px;
  }
  .service-label-group {
    flex: 1;
    min-width: 0;
  }
  .service-label-group label {
    font-size: 14px;
    font-weight: 500;
    color: var(--primary-text-color);
  }
  .service-desc {
    display: block;
    font-size: 12px;
    color: var(--secondary-text-color);
    margin-top: 1px;
  }
  .settings-connect-block {
    padding-bottom: 12px;
    margin-bottom: 4px;
    border-bottom: 1px solid var(--selora-zinc-700);
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
    display: inline-flex;
    align-items: center;
    margin-top: 4px;
  }
  .key-hint.key-set {
    border-color: color-mix(
      in srgb,
      var(--success-color, #22c55e) 40%,
      transparent
    );
    background: color-mix(
      in srgb,
      var(--success-color, #22c55e) 6%,
      var(--selora-zinc-900)
    );
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
  /* Fixed-width right column for consistent alignment */
  .service-row ha-switch,
  .service-row > ha-icon-button,
  .mcp-token-row > ha-icon-button {
    flex-shrink: 0;
    width: 48px;
    display: flex;
    justify-content: center;
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
  .advanced-section[open] {
    padding-bottom: 20px;
  }
  .advanced-toggle {
    display: flex;
    align-items: center;
    gap: 12px;
    cursor: pointer;
    font-size: 20px;
    font-weight: 700;
    line-height: 1.2;
    color: var(--primary-text-color);
    list-style: none;
    padding: 22px 28px;
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
    --mdc-icon-size: 20px;
    transition: transform 0.2s;
    opacity: 0.5;
  }
  .advanced-section[open] > .advanced-toggle .advanced-chevron {
    transform: rotate(90deg);
  }
  .advanced-section[open] > .advanced-toggle {
    margin-bottom: 4px;
  }
  .advanced-section .service-row {
    border-bottom: none !important;
    padding: 8px 28px;
  }
  .advanced-section .service-details {
    padding: 0 28px;
  }
  .advanced-section .settings-section-title {
    padding: 0 28px;
  }
  .advanced-section .service-row:first-of-type {
    padding-top: 8px;
  }
  .advanced-section .service-group {
    padding-bottom: 12px;
    margin-bottom: 4px;
    border-bottom: 1px solid var(--selora-zinc-700);
  }
  .advanced-section .service-group:last-of-type {
    border-bottom: none;
    padding-bottom: 0;
    margin-bottom: 0;
  }
  .advanced-section > .card-save-bar {
    margin: 16px 0 0;
    padding: 0 28px;
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
  .card-save-bar {
    display: flex;
    justify-content: flex-end;
    margin-top: 16px;
  }
  .save-feedback {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    margin-top: 8px;
    padding: 8px 12px;
    border-radius: 8px;
  }
  .save-feedback--success {
    color: var(--success-color, #22c55e);
    background: color-mix(
      in srgb,
      var(--success-color, #22c55e) 8%,
      transparent
    );
  }
  .save-feedback--error {
    color: var(--error-color, #ef4444);
    background: color-mix(in srgb, var(--error-color, #ef4444) 8%, transparent);
  }
  .key-hint-btn {
    cursor: pointer;
    transition:
      border-color 0.15s,
      background 0.15s;
  }
  .key-hint-btn:hover {
    border-color: var(--selora-accent);
    background: color-mix(
      in srgb,
      var(--success-color, #22c55e) 10%,
      var(--selora-zinc-900)
    );
  }
  .key-hint-action {
    --mdc-icon-size: 13px;
    opacity: 0.45;
    margin-left: 8px;
    color: var(--secondary-text-color);
    transition: opacity 0.15s;
  }
  .key-hint-btn:hover .key-hint-action {
    opacity: 0.8;
  }

  /* ── MCP Token Management ───────────────────────────── */

  .mcp-token-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 0 0 8px;
  }
  .mcp-token-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .mcp-token-info {
    flex: 1;
    min-width: 0;
  }
  .mcp-token-name {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 14px;
    font-weight: 500;
    color: var(--primary-text-color);
  }
  .mcp-token-meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin-top: 2px;
    font-size: 12px;
    color: var(--secondary-text-color);
  }
  .mcp-token-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 600;
    text-transform: capitalize;
  }
  .mcp-token-badge--read_only {
    background: rgba(59, 130, 246, 0.15);
    color: #60a5fa;
  }
  .mcp-token-badge--admin {
    background: rgba(251, 191, 36, 0.15);
    color: var(--selora-accent);
  }
  .mcp-token-badge--custom {
    background: rgba(168, 85, 247, 0.15);
    color: #c084fc;
  }

  /* Tool checklist in create dialog */
  .mcp-tool-checklist {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 4px;
    max-height: 240px;
    overflow-y: auto;
    padding: 8px;
    background: var(--selora-zinc-900);
    border: 1px solid var(--selora-zinc-700);
    border-radius: 8px;
  }
  .mcp-tool-check {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--primary-text-color);
    padding: 4px 6px;
    border-radius: 4px;
    cursor: pointer;
  }
  .mcp-tool-check:hover {
    background: var(--selora-zinc-800);
  }
  .mcp-tool-check input[type="checkbox"] {
    accent-color: var(--selora-accent);
  }
`;
