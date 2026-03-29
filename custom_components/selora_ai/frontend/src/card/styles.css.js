import { css } from "lit";

export const cardStyles = css`
  ha-card {
    overflow: hidden;
  }

  /* ---- Header ---- */
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px;
    cursor: pointer;
    border-bottom: 1px solid var(--divider-color);
    transition: background 0.15s;
  }
  .card-header:hover {
    background: var(--secondary-background-color);
  }
  .header-left {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .header-logo {
    width: 26px;
    height: 26px;
    border-radius: 6px;
  }
  .header-title {
    font-size: 16px;
    font-weight: 600;
  }
  .header-action {
    --mdc-icon-size: 18px;
    opacity: 0.4;
    transition: opacity 0.15s;
  }
  .card-header:hover .header-action {
    opacity: 0.8;
  }

  /* ---- Content ---- */
  .card-content {
    padding: 12px 16px 16px;
  }

  /* ---- Quick Actions ---- */
  .quick-actions {
    display: flex;
    gap: 8px;
    margin-bottom: 16px;
  }
  .action-btn {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 10px 12px;
    border: 1px solid var(--divider-color);
    border-radius: 10px;
    background: var(--card-background-color);
    color: var(--primary-text-color);
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.15s;
    font-family: inherit;
  }
  .action-btn:hover {
    background: rgba(251, 191, 36, 0.06);
    border-color: var(--selora-accent);
    box-shadow: 0 0 10px rgba(251, 191, 36, 0.1);
  }
  .action-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
  .action-btn ha-icon {
    --mdc-icon-size: 18px;
  }
  .new-btn {
    background: var(--selora-accent);
    border-color: var(--selora-accent);
    color: #1a1a1a;
    font-weight: 600;
  }
  .new-btn:hover {
    background: var(--selora-accent-light);
    border-color: var(--selora-accent-light);
    box-shadow: var(--selora-glow);
  }

  /* ---- Sections ---- */
  .section {
    margin-bottom: 12px;
  }
  .section:last-child {
    margin-bottom: 0;
  }
  .section-header {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: normal;
    opacity: 0.7;
    margin-bottom: 8px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--divider-color);
  }
  .section-icon {
    --mdc-icon-size: 16px;
  }
  .badge {
    background: var(--selora-accent);
    color: white;
    font-size: 10px;
    font-weight: 700;
    padding: 1px 6px;
    border-radius: 10px;
    margin-left: auto;
  }

  /* ---- Suggestion Items ---- */
  .suggestion-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 0;
    border-bottom: 1px solid var(--divider-color);
  }
  .suggestion-item:last-child {
    border-bottom: none;
  }
  .suggestion-info {
    flex: 1;
    min-width: 0;
  }
  .suggestion-name {
    font-size: 13px;
    font-weight: 500;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .suggestion-desc {
    font-size: 11px;
    opacity: 0.6;
    margin-top: 2px;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .accept-btn {
    flex-shrink: 0;
    width: 32px;
    height: 32px;
    border-radius: 50%;
    border: 1px solid var(--selora-accent);
    background: transparent;
    color: var(--selora-accent-text);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.15s;
  }
  .accept-btn:hover {
    background: var(--selora-accent);
    color: white;
  }
  .accept-btn ha-icon {
    --mdc-icon-size: 18px;
  }

  /* ---- Automation Items ---- */
  .automation-item {
    border-bottom: 1px solid var(--divider-color);
  }
  .automation-item:last-child {
    border-bottom: none;
  }
  .automation-item.expanded {
    background: rgba(251, 191, 36, 0.04);
    border-radius: 12px;
    margin: 4px -8px;
    padding: 0 8px;
    border-bottom: none;
    border: 1px solid rgba(251, 191, 36, 0.15);
  }
  .automation-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 0;
    cursor: pointer;
  }
  .activity-indicator {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .activity-indicator.active {
    background: var(--selora-accent);
    box-shadow: 0 0 8px rgba(251, 191, 36, 0.6);
  }
  .activity-indicator.inactive {
    background: var(--disabled-text-color, #999);
  }
  .activity-info {
    flex: 1;
    min-width: 0;
  }
  .activity-name {
    font-size: 13px;
    font-weight: 500;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .activity-meta {
    font-size: 11px;
    opacity: 0.5;
    margin-top: 1px;
  }
  .activity-toggle-wrap {
    cursor: pointer;
    flex-shrink: 0;
    padding: 4px;
  }
  .activity-toggle {
    --mdc-icon-size: 24px;
    transition: color 0.15s;
  }
  .activity-toggle.on {
    color: var(--selora-accent-text);
  }
  .activity-toggle.off {
    color: var(--disabled-text-color, #999);
  }

  /* ---- Expanded Details ---- */
  .automation-details {
    padding: 4px 0 10px 18px;
  }
  .detail-desc {
    font-size: 12px;
    opacity: 0.6;
    margin-bottom: 8px;
    font-style: italic;
  }
  .detail-section {
    margin-bottom: 6px;
  }
  .detail-label {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: normal;
    opacity: 0.5;
    margin-bottom: 3px;
  }
  .detail-chip {
    display: inline-block;
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 4px;
    margin: 2px 4px 2px 0;
  }
  .detail-chip.trigger {
    background: rgba(251, 191, 36, 0.1);
    border: 1px solid rgba(251, 191, 36, 0.3);
    color: var(--primary-text-color);
  }
  .detail-chip.action {
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.06));
    border: 1px solid var(--divider-color);
    color: var(--primary-text-color);
  }
  .detail-actions {
    display: flex;
    gap: 8px;
    margin-top: 8px;
  }
  .detail-btn {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 5px 10px;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 500;
    cursor: pointer;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color);
    color: var(--primary-text-color);
    font-family: inherit;
    transition: all 0.15s;
  }
  .detail-btn ha-icon {
    --mdc-icon-size: 14px;
  }
  .open-btn:hover {
    border-color: var(--selora-accent);
    color: var(--selora-accent-text);
  }
  .delete-btn:hover {
    border-color: var(--error-color, #f44336);
    color: var(--error-color, #f44336);
  }

  /* ---- Error banner ---- */
  .error-banner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 8px 12px;
    margin-bottom: 12px;
    border-radius: 8px;
    background: rgba(244, 67, 54, 0.1);
    border: 1px solid var(--error-color, #f44336);
    color: var(--error-color, #f44336);
    font-size: 12px;
    font-weight: 500;
  }
  .error-dismiss {
    --mdc-icon-size: 16px;
    cursor: pointer;
    opacity: 0.7;
    flex-shrink: 0;
  }
  .error-dismiss:hover {
    opacity: 1;
  }

  /* ---- Common ---- */
  .loading-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 12px 0;
    font-size: 12px;
    opacity: 0.6;
  }
  .empty-row {
    padding: 12px 0;
    font-size: 12px;
    opacity: 0.5;
    font-style: italic;
  }
  .more-link {
    text-align: center;
    font-size: 12px;
    color: var(--selora-accent-text);
    cursor: pointer;
    padding: 8px 0 4px;
    font-weight: 500;
  }
  .more-link:hover {
    text-decoration: underline;
  }

  /* ---- Bouncing dots loader ---- */
  .dots-loader {
    display: inline-flex;
    gap: 4px;
    align-items: center;
  }
  .dots-loader span {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--selora-accent);
    animation: bounce 1.2s ease-in-out infinite;
  }
  .dots-loader span:nth-child(2) {
    animation-delay: 0.2s;
  }
  .dots-loader span:nth-child(3) {
    animation-delay: 0.4s;
  }
  @keyframes bounce {
    0%,
    60%,
    100% {
      transform: translateY(0);
      opacity: 0.4;
    }
    30% {
      transform: translateY(-6px);
      opacity: 1;
    }
  }

  /* ---- Spinner (fallback) ---- */
  .spinner {
    display: inline-block;
    width: 16px;
    height: 16px;
    border: 2px solid transparent;
    border-top-color: var(--selora-accent);
    border-left-color: var(--selora-accent);
    border-radius: 50%;
    animation: spin 0.6s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  /* ---- Generating row ---- */
  .generating-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 14px 0;
    font-size: 12px;
    opacity: 0.7;
  }

  /* ---- Modal overlay (matches panel) ---- */
  .modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 10001;
  }
  .modal {
    background: var(--card-background-color, #fff);
    border-radius: 16px;
    border: 1px solid var(--divider-color);
    padding: 24px;
    max-width: 420px;
    width: 90%;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
  }
  .modal-title {
    font-size: 18px;
    font-weight: 700;
    margin: 0 0 16px;
  }
  .modal-label {
    font-size: 13px;
    font-weight: 500;
    display: block;
    margin-bottom: 6px;
  }
  .modal-row {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .modal-input {
    flex: 1;
    padding: 10px 12px;
    border: 1px solid var(--divider-color);
    border-radius: 8px;
    background: var(--card-background-color);
    color: var(--primary-text-color);
    font-size: 14px;
    font-family: inherit;
    outline: none;
    transition: border-color 0.15s;
  }
  .modal-input:focus {
    border-color: var(--selora-accent);
  }
  .modal-input::placeholder {
    opacity: 0.35;
  }
  .modal-input.generating-placeholder {
    display: flex;
    align-items: center;
    gap: 8px;
    border-color: var(--selora-accent);
  }
  .modal-row.generating .modal-magic-btn {
    border-color: var(--selora-accent);
  }
  .modal-magic-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 8px 10px;
    flex-shrink: 0;
    border-radius: 6px;
    border: 1.5px solid var(--divider-color);
    background: var(--card-background-color);
    color: var(--primary-text-color);
    cursor: pointer;
    font-weight: 600;
    transition: opacity 0.15s;
  }
  .modal-magic-btn:hover {
    opacity: 0.85;
    border-color: var(--selora-accent);
    color: var(--selora-accent-text);
  }
  .modal-magic-btn ha-icon {
    --mdc-icon-size: 20px;
  }
  .modal-actions {
    display: flex;
    gap: 8px;
    margin-top: 16px;
    justify-content: flex-end;
  }
  .modal-btn {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 6px 14px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    font-family: inherit;
    border: 1.5px solid transparent;
    background: transparent;
    transition:
      background 0.15s,
      opacity 0.15s;
    user-select: none;
    letter-spacing: normal;
  }
  .modal-btn:hover {
    opacity: 0.85;
  }
  .modal-btn ha-icon {
    --mdc-icon-size: 14px;
  }
  .modal-cancel {
    border-color: var(--divider-color);
    color: var(--primary-text-color);
    background: var(--card-background-color);
  }
  .modal-cancel:hover {
    border-color: var(--selora-accent);
    color: var(--selora-accent-text);
  }
  .modal-create {
    background: var(--selora-accent);
    border-color: var(--selora-accent);
    color: #1a1a1a;
  }
  .modal-create:hover:not(:disabled) {
    box-shadow: var(--selora-glow);
    background: var(--selora-accent-light);
  }
  .modal-create:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
`;
