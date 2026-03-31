import { css } from "lit";

export const sidebarStyles = css`
  .sidebar {
    width: 0;
    min-width: 0;
    display: flex;
    flex-direction: column;
    background: var(--sidebar-background-color, var(--card-background-color));
    border-right: 1px solid var(--selora-zinc-800);
    overflow: hidden;
    transition:
      width 0.3s ease,
      min-width 0.3s ease;
  }
  .sidebar.open {
    width: 260px;
    min-width: 260px;
  }
  .sidebar-header {
    padding: 16px;
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: normal;
    opacity: 0.6;
    border-bottom: 1px solid var(--divider-color);
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .session-list {
    flex: 1;
    overflow-y: auto;
  }
  .session-item-wrapper {
    position: relative;
    overflow: hidden;
    border-bottom: 1px solid var(--divider-color);
  }
  .session-item-delete-bg {
    position: absolute;
    top: 0;
    right: 0;
    bottom: 0;
    width: 80px;
    background: var(--error-color, #ef4444);
    display: none;
    align-items: center;
    justify-content: center;
    color: white;
    --mdc-icon-size: 20px;
  }
  .session-item-wrapper.reveal-delete .session-item-delete-bg {
    display: flex;
  }
  .session-item {
    padding: 12px 16px;
    cursor: pointer;
    display: flex;
    align-items: flex-start;
    gap: 8px;
    position: relative;
    transition:
      background 0.15s,
      transform 0.2s ease;
    background: var(--sidebar-background-color, var(--card-background-color));
    z-index: 1;
  }
  .session-item:hover {
    background: var(--secondary-background-color);
  }
  .session-item.active {
    background: rgba(251, 191, 36, 0.1);
    border-left: 3px solid var(--selora-accent);
    box-shadow: inset 0 0 12px rgba(251, 191, 36, 0.06);
  }
  .session-item.swiped {
    transform: translateX(-80px);
  }
  .session-title {
    font-size: 13px;
    font-weight: 500;
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .session-meta {
    font-size: 11px;
    opacity: 0.5;
    margin-top: 2px;
  }
  .session-delete {
    opacity: 0;
    cursor: pointer;
    color: var(--error-color, #f44336);
    transition: opacity 0.15s;
    flex-shrink: 0;
    align-self: center;
  }
  .session-item:hover .session-delete {
    opacity: 0.6;
  }
  .session-delete:hover {
    opacity: 1 !important;
  }
  @media (pointer: coarse) {
    .session-delete {
      display: none;
    }
  }
  .sidebar-select-btn {
    background: transparent;
    border: 1px solid var(--divider-color);
    color: var(--primary-text-color);
    font-size: 11px;
    font-weight: 700;
    padding: 4px 12px;
    border-radius: 6px;
    cursor: pointer;
    transition:
      background 0.15s,
      border-color 0.15s;
  }
  .sidebar-select-btn:hover {
    background: rgba(251, 191, 36, 0.1);
    border-color: var(--selora-accent);
  }
  .select-actions-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 16px;
    border-bottom: 1px solid var(--divider-color);
    background: rgba(251, 191, 36, 0.06);
  }
  .select-all-label {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    cursor: pointer;
    user-select: none;
  }
  .select-all-label input[type="checkbox"] {
    accent-color: var(--selora-accent);
    cursor: pointer;
  }
  .btn-delete-selected {
    display: flex;
    align-items: center;
    gap: 4px;
    background: transparent;
    border: 1px solid var(--error-color, #ef4444);
    color: var(--error-color, #ef4444);
    font-size: 11px;
    font-weight: 500;
    padding: 4px 10px;
    border-radius: 6px;
    cursor: pointer;
    transition:
      background 0.15s,
      color 0.15s;
  }
  .btn-delete-selected:hover:not([disabled]) {
    background: var(--error-color, #ef4444);
    color: #fff;
  }
  .btn-delete-selected[disabled] {
    opacity: 0.35;
    cursor: not-allowed;
  }
  .session-checkbox {
    accent-color: var(--selora-accent);
    cursor: pointer;
    flex-shrink: 0;
    margin-top: 2px;
  }
  .new-chat-btn {
    margin: 12px;
    display: block;
  }
`;
