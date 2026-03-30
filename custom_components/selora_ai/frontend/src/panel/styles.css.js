import { css } from "lit";

export const panelStyles = css`
  :host {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: var(--primary-background-color);
    color: var(--primary-text-color);
  }

  /* ---- Sidebar (session list) ---- */
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

  /* ---- Main area ---- */
  .body {
    flex: 1;
    display: flex;
    overflow: hidden;
  }
  .main {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .header {
    background: var(--app-header-background-color, #1c1c1e);
    color: var(--app-header-text-color, #e4e4e7);
    box-shadow: none;
    border-bottom: 1px solid var(--divider-color);
    z-index: 2;
    flex-shrink: 0;
  }
  .header-top {
    padding: 14px 24px;
    font-size: 20px;
    font-weight: 500;
    display: flex;
    align-items: center;
    gap: 10px;
    max-width: 1200px;
    margin: 0 auto;
    box-sizing: border-box;
    width: 100%;
  }
  .header-top ha-icon-button {
    margin-right: 4px;
    display: inline-flex;
    opacity: 0.55;
  }
  .tabs {
    display: flex;
    padding: 0 24px;
    max-width: 1200px;
    margin: 0 auto;
    box-sizing: border-box;
    width: 100%;
  }
  .tab {
    padding: 10px 16px;
    cursor: pointer;
    font-weight: 400;
    font-size: 16px;
    opacity: 0.55;
    transition:
      opacity 0.3s,
      color 0.3s;
  }
  .tab:hover {
    opacity: 1;
    color: var(--selora-accent-text);
  }
  .tab.active {
    opacity: 1;
    font-weight: 600;
    color: var(--selora-accent-text);
  }
  .tab:first-child {
    padding-left: 0;
  }
  .tab-inner {
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
  .tab-icon {
    --mdc-icon-size: 16px;
    margin-bottom: 12px;
  }
  .tab-text {
    position: relative;
    padding-bottom: 6px;
  }
  /* Shared underline-from-center effect */
  .tab-text::after,
  .card-tab::after {
    content: "";
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    height: 2px;
    background: var(--selora-accent);
    transform: scaleX(0);
    transform-origin: center;
    transition: transform 0.3s ease;
  }
  .tab:hover .tab-text::after,
  .tab.active .tab-text::after,
  .card-tab.active::after {
    transform: scaleX(1);
  }
  .card-tab:hover::after {
    transform: scaleX(0.6);
  }

  /* ---- Chat ---- */
  .chat-pane {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .chat-messages {
    flex: 1;
    overflow-y: auto;
    padding: 20px 24px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    max-width: 1200px;
    margin: 0 auto;
    box-sizing: border-box;
    width: 100%;
  }
  @keyframes fadeInUp {
    from {
      opacity: 0;
      transform: translateY(18px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }
  @keyframes logoEntrance {
    0% {
      opacity: 0;
      transform: scale(0.6) translateY(12px);
    }
    60% {
      opacity: 1;
      transform: scale(1.06) translateY(-2px);
    }
    100% {
      opacity: 1;
      transform: scale(1) translateY(0);
    }
  }
  .empty-state {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    opacity: 0.45;
    gap: 12px;
    padding: 32px;
    text-align: center;
    animation: fadeInUp 0.5s ease both;
  }
  .empty-state.welcome {
    opacity: 1;
    gap: 0;
    justify-content: flex-start;
    padding: 12px;
  }
  @media (max-width: 600px) {
    .empty-state.welcome {
      padding: 4px;
    }
    .empty-state.welcome .section-card {
      padding: 16px;
    }
  }
  .empty-state.welcome > * {
    animation: fadeInUp 0.5s ease both;
  }
  .empty-state.welcome > img:first-child {
    animation: logoEntrance 0.7s cubic-bezier(0.34, 1.56, 0.64, 1) both;
  }
  .empty-state.welcome > :nth-child(2) {
    animation-delay: 0.15s;
  }
  .empty-state.welcome > :nth-child(3) {
    animation-delay: 0.25s;
  }
  .empty-state.welcome > :nth-child(4) {
    animation-delay: 0.35s;
  }
  .empty-state.welcome > :nth-child(5) {
    animation-delay: 0.4s;
  }
  .empty-state.welcome > :nth-child(6) {
    animation-delay: 0.45s;
  }
  .empty-state.welcome > :nth-child(7) {
    animation-delay: 0.5s;
  }
  .empty-state ha-icon {
    --mdc-icon-size: 56px;
  }
  .welcome-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  }
  .welcome-card:active {
    transform: translateY(0);
  }
  .message-row {
    display: flex;
    flex-direction: column;
  }
  .bubble {
    max-width: 82%;
    padding: 12px 16px;
    border-radius: 16px;
    font-size: 14px;
    line-height: 1.5;
    word-wrap: break-word;
  }
  .bubble.user {
    align-self: flex-end;
    background: var(--selora-zinc-800) !important;
    color: var(--selora-zinc-200) !important;
    border: 1px solid var(--selora-accent) !important;
    border-bottom-right-radius: 4px;
  }
  .bubble.assistant {
    align-self: flex-start;
    background: var(--card-background-color);
    box-shadow: var(--card-box-shadow);
    border: 1px solid var(--selora-zinc-700);
    border-bottom-left-radius: 4px;
  }
  .bubble-meta {
    font-size: 10px;
    opacity: 0.5;
    margin-top: 2px;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .bubble.user + .bubble-meta {
    align-self: flex-end;
  }
  .bubble.assistant + .bubble-meta {
    align-self: flex-start;
  }
  .copy-msg-row {
    display: flex;
    justify-content: flex-end;
    margin-top: 4px;
  }
  .copy-msg-btn {
    background: none;
    border: none;
    padding: 2px 4px;
    cursor: pointer;
    opacity: 0;
    transition:
      opacity 0.15s,
      color 0.15s;
    color: inherit;
    line-height: 1;
    border-radius: 4px;
  }
  .message-row:hover .copy-msg-btn {
    opacity: 0.7;
  }
  .copy-msg-btn:hover {
    opacity: 1 !important;
  }
  .copy-msg-btn.copied {
    opacity: 1 !important;
    color: var(--success-color, #4caf50);
  }
  .bubble.assistant strong {
    color: var(--selora-accent-text);
  }

  /* ---- Automation proposal card ---- */
  .proposal-card {
    margin-top: 12px;
    border: 1px solid rgba(251, 191, 36, 0.25);
    border-radius: 16px;
    overflow: hidden;
    background: var(--primary-background-color);
  }
  .proposal-header {
    background: rgba(251, 191, 36, 0.08);
    padding: 10px 14px;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: normal;
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--selora-accent-text);
  }
  .proposal-body {
    padding: 14px;
  }
  .proposal-body .flow-chart {
    align-items: flex-start;
  }
  .proposal-body .flow-section {
    text-align: left;
  }
  .proposal-name {
    font-weight: 600;
    font-size: 15px;
    margin-bottom: 8px;
  }
  .proposal-description {
    font-size: 13px;
    color: var(--secondary-text-color);
    margin-bottom: 12px;
    line-height: 1.5;
    padding: 10px 12px;
    background: rgba(251, 191, 36, 0.06);
    border-left: 3px solid var(--selora-accent);
    border-radius: 0 8px 8px 0;
  }
  .proposal-description-label {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: normal;
    opacity: 0.6;
    margin-bottom: 4px;
  }
  .yaml-toggle {
    font-size: 12px;
    cursor: pointer;
    opacity: 0.6;
    display: flex;
    align-items: center;
    gap: 4px;
    margin-bottom: 8px;
    user-select: none;
  }
  .yaml-toggle:hover {
    opacity: 1;
  }
  ha-code-editor {
    --code-mirror-font-size: 12px;
    --code-mirror-height: auto;
    font-size: 12px;
  }
  textarea.yaml-editor {
    width: 100%;
    box-sizing: border-box;
    background: var(--selora-zinc-900);
    color: var(--selora-zinc-200);
    padding: 10px 12px;
    border-radius: 8px;
    font-size: 11px;
    font-family: "Fira Code", "Cascadia Code", monospace;
    line-height: 1.5;
    border: 1px solid var(--selora-zinc-800);
    resize: vertical;
    min-height: 140px;
    outline: none;
    transition: border-color 0.3s;
  }
  textarea.yaml-editor:focus {
    border-color: var(--selora-accent);
    background: var(--selora-zinc-800);
  }
  .yaml-edit-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 6px;
    flex-wrap: wrap;
  }
  .yaml-unsaved {
    font-size: 11px;
    color: var(--warning-color, #ff9800);
    display: flex;
    align-items: center;
    gap: 4px;
    flex: 1;
  }
  pre.yaml {
    background: var(--selora-zinc-900);
    color: var(--selora-zinc-200);
    padding: 10px 12px;
    border-radius: 8px;
    border: 1px solid var(--selora-zinc-800);
    font-size: 11px;
    overflow-x: auto;
    font-family: "Fira Code", "Cascadia Code", monospace;
    margin: 0 0 12px;
    max-height: 200px;
    overflow-y: auto;
  }
  .proposal-verify {
    font-size: 12px;
    font-style: italic;
    opacity: 0.65;
    margin-bottom: 10px;
  }
  .proposal-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .proposal-actions mwc-button[raised] {
    --mdc-theme-primary: var(--success-color, #4caf50);
  }

  /* Declined / saved states */
  .proposal-status {
    padding: 8px 12px;
    font-size: 12px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .proposal-status.saved {
    background: rgba(76, 175, 80, 0.12);
    color: var(--success-color, #4caf50);
  }
  .proposal-status.declined {
    background: rgba(158, 158, 158, 0.12);
    color: var(--secondary-text-color);
  }

  /* ---- Automation flowchart ---- */
  .flow-chart {
    display: flex;
    flex-direction: column;
    align-items: center;
    margin: 10px 0 12px;
    font-size: 12px;
  }
  .flow-section {
    width: 100%;
    text-align: center;
  }
  .flow-label {
    font-size: 9px;
    font-weight: 800;
    letter-spacing: normal;
    text-transform: uppercase;
    opacity: 0.5;
    margin-bottom: 4px;
  }
  .flow-node {
    display: inline-block;
    padding: 6px 12px;
    border-radius: 8px;
    margin-bottom: 4px;
    max-width: 100%;
    word-break: break-word;
    font-size: 12px;
    line-height: 1.4;
  }
  .flow-node + .flow-node {
    margin-top: 3px;
  }
  .trigger-node,
  .condition-node,
  .action-node {
    background: rgba(var(--rgb-primary-text-color, 255, 255, 255), 0.06);
    border: 1px solid rgba(var(--rgb-primary-text-color, 255, 255, 255), 0.15);
    color: var(--primary-text-color);
  }
  .flow-arrow {
    font-size: 16px;
    line-height: 1;
    opacity: 0.35;
    padding: 3px 0;
    text-align: center;
  }
  .flow-arrow-sm {
    font-size: 13px;
    line-height: 1;
    opacity: 0.3;
    padding: 2px 0;
    text-align: center;
  }

  /* ---- Toggle switch ---- */
  .toggle-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 10px;
  }
  .toggle-switch {
    position: relative;
    width: 40px;
    height: 22px;
    flex-shrink: 0;
    cursor: pointer;
  }
  .toggle-switch input {
    opacity: 0;
    width: 0;
    height: 0;
    position: absolute;
  }
  .toggle-track {
    position: absolute;
    inset: 0;
    border-radius: 11px;
    background: var(--divider-color);
    border: 1px solid rgba(0, 0, 0, 0.15);
    transition: background 0.2s;
  }
  .toggle-track.on {
    background: var(--selora-accent);
    border-color: var(--selora-accent-dark);
    box-shadow: 0 0 8px rgba(251, 191, 36, 0.35);
  }
  .toggle-thumb {
    position: absolute;
    top: 2px;
    left: 2px;
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: white;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
    transition: left 0.2s;
  }
  .toggle-track.on .toggle-thumb {
    left: 20px;
  }
  .toggle-label {
    font-size: 12px;
    font-weight: 600;
    color: var(--secondary-text-color);
  }
  .toggle-label.on {
    color: var(--selora-accent-text);
  }

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
  .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    padding: 10px 20px;
    border-radius: 12px;
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    border: 1px solid transparent;
    background: transparent;
    font-family: inherit;
    transition: colors 0.2s ease;
    user-select: none;
    color: var(--primary-text-color);
  }
  .btn:hover {
    opacity: 0.9;
  }
  .btn-primary {
    background: var(--selora-accent);
    border-color: var(--selora-accent);
    color: #000;
    font-weight: 500;
  }
  .btn-primary:hover {
    box-shadow: var(--selora-glow);
    background: var(--selora-accent-light);
    border-color: var(--selora-accent-light);
    opacity: 1;
  }
  .btn-success {
    background: var(--success-color, #4caf50);
    border-color: var(--success-color, #4caf50);
    color: white;
  }
  .btn-outline {
    border-color: var(--selora-zinc-700);
    color: var(--selora-btn-outline-text);
    background: var(--selora-section-bg);
  }
  .btn-outline:hover {
    border-color: var(--selora-zinc-600);
    background: var(--secondary-background-color, #3f3f46);
  }
  .btn-danger {
    border-color: rgba(239, 68, 68, 0.4);
    color: var(--error-color, #ef4444);
    background: transparent;
  }
  .btn-danger:hover {
    background: rgba(239, 68, 68, 0.1);
    border-color: var(--error-color, #ef4444);
  }
  .btn-warning {
    border-color: rgba(251, 191, 36, 0.4);
    color: var(--selora-accent-text);
    background: transparent;
  }
  .btn-warning:hover {
    background: rgba(251, 191, 36, 0.08);
    border-color: var(--selora-accent);
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

  /* ---- Filter input ---- */
  .badge {
    background: var(--selora-zinc-700);
    color: var(--selora-zinc-200);
    border-radius: 10px;
    padding: 3px 8px;
    font-size: 12px;
    font-weight: 500;
    min-width: 16px;
    text-align: center;
    line-height: 1;
    display: inline-flex;
    align-items: center;
    transition: all 0.25s ease;
  }
  .section-card {
    background: var(--selora-section-bg);
    color: var(--primary-text-color);
    border: 1px solid var(--selora-section-border);
    border-radius: 20px;
    padding: 28px 32px;
    margin-bottom: 36px;
  }
  .section-card .card {
    background: var(--selora-inner-card-bg);
    border: 1px solid var(--selora-inner-card-border);
    border-radius: 14px;
  }
  .section-card .automations-list {
    border-color: var(--selora-inner-card-border);
  }
  .section-card .auto-row {
    border-color: var(--selora-inner-card-border);
  }
  .section-card-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 8px;
  }
  .section-card-header h3 {
    font-size: 20px;
    margin: 0;
    font-weight: 700;
  }
  .section-card-subtitle {
    font-size: 13px;
    color: var(--secondary-text-color);
    margin-bottom: 24px;
  }
  @media (max-width: 600px) {
    .scroll-view {
      padding: 12px 10px;
    }
    .section-card {
      padding: 14px 12px;
      border-radius: 12px;
      margin-bottom: 16px;
    }
    .section-card .card {
      padding: 12px;
    }
  }
  .suggestions-section {
  }
  .show-more-link {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    color: var(--selora-accent-text);
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    background: none;
    border: none;
    padding: 8px 0;
    font-family: inherit;
  }
  .show-more-link:hover {
    text-decoration: underline;
  }

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
  .auto-row.disabled {
    opacity: 0.5;
  }
  .auto-row.highlighted {
    animation: highlightRow 3s ease;
  }
  @keyframes highlightRow {
    0%,
    30% {
      background: rgba(251, 191, 36, 0.15);
    }
    100% {
      background: transparent;
    }
  }
  .card.fading-out {
    animation: fadeOutCard 0.6s ease forwards;
    pointer-events: none;
  }
  @keyframes fadeOutCard {
    to {
      opacity: 0;
      transform: scale(0.95);
    }
  }
  .suggestions-section .automations-grid .card {
    animation: slideInCard 0.4s ease both;
  }
  @keyframes slideInCard {
    from {
      opacity: 0;
      transform: translateX(30px);
    }
    to {
      opacity: 1;
      transform: translateX(0);
    }
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

  /* ---- Status indicator (inline) ---- */
  .status-indicator {
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 10px;
    flex-shrink: 0;
  }
  .status-indicator.on {
    color: var(--success-color, #4caf50);
    background: rgba(76, 175, 80, 0.12);
  }
  .status-indicator.off {
    color: var(--secondary-text-color);
    background: rgba(158, 158, 158, 0.12);
  }
  .btn-ghost {
    border-color: transparent;
    color: var(--secondary-text-color);
    background: transparent;
    font-size: 11px;
    padding: 4px 8px;
  }
  .btn-ghost:hover {
    color: var(--primary-text-color);
    background: rgba(0, 0, 0, 0.06);
    border-color: var(--divider-color);
  }
  .btn-ghost.active {
    color: var(--selora-accent-text);
    border-color: rgba(251, 191, 36, 0.35);
    background: rgba(251, 191, 36, 0.05);
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

  /* ---- Spinner ---- */
  .spinner {
    display: inline-block;
    width: 18px;
    height: 18px;
    border: 2.5px solid rgba(0, 0, 0, 0.1);
    border-top-color: var(--selora-accent);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }
  .spinner.green {
    border-color: rgba(76, 175, 80, 0.2);
    border-top-color: var(--success-color, #4caf50);
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  /* ---- Modal overlay ---- */
  .modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    z-index: 10001;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .modal-content {
    background: var(--card-background-color, #fff);
    border-radius: 16px;
    border: 1px solid var(--selora-zinc-800);
    padding: 24px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
    width: 90%;
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

  /* ---- Chat input ---- */
  .chat-input-wrapper {
    border-top: 1px solid var(--divider-color);
    flex-shrink: 0;
  }
  .chat-input {
    padding: 16px 24px;
    max-width: 1200px;
    margin: 0 auto;
    box-sizing: border-box;
    width: 100%;
    background: transparent;
    display: flex;
    gap: 10px;
    align-items: center;
  }
  .chat-input ha-textfield {
    --mdc-text-field-fill-color: var(--selora-zinc-800, #27272a);
    --mdc-text-field-ink-color: var(--primary-text-color);
    --mdc-text-field-label-ink-color: var(--secondary-text-color);
    --mdc-text-field-idle-line-color: var(--selora-zinc-700, #3f3f46);
    --mdc-text-field-hover-line-color: var(--selora-accent);
    border-radius: 12px;
    overflow: hidden;
  }
  .chat-input ha-icon-button {
    color: var(--selora-accent-text);
    opacity: 0.7;
    transition: opacity 0.2s;
  }
  .chat-input ha-icon-button:hover {
    opacity: 1;
  }
  .typing-bubble {
    align-self: flex-start;
    background-color: var(--card-background-color);
    box-shadow: var(--card-box-shadow);
    border-radius: 18px;
    border-bottom-left-radius: 4px;
    padding: 16px 22px;
    display: flex;
    align-items: center;
    gap: 5px;
  }
  .typing-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background-color: var(--secondary-text-color);
    animation: typingBounce 1.4s infinite ease-in-out both;
  }
  .typing-dot:nth-child(1) {
    animation-delay: 0s;
  }
  .typing-dot:nth-child(2) {
    animation-delay: 0.2s;
  }
  .typing-dot:nth-child(3) {
    animation-delay: 0.4s;
  }
  @keyframes typingBounce {
    0%,
    80%,
    100% {
      transform: scale(0.6);
      opacity: 0.4;
    }
    40% {
      transform: scale(1);
      opacity: 1;
    }
  }
  .streaming-cursor::after {
    content: "";
    display: inline-block;
    width: 2px;
    height: 1em;
    background-color: var(--primary-text-color);
    margin-left: 2px;
    vertical-align: text-bottom;
    animation: blink 0.7s step-end infinite;
  }
  @keyframes blink {
    50% {
      opacity: 0;
    }
  }

  /* ---- Scroll view (automations / settings) ---- */
  .scroll-view {
    flex: 1;
    overflow-y: auto;
    padding: 24px 28px;
    max-width: 1200px;
    margin: 0 auto;
    width: 100%;
    box-sizing: border-box;
  }
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
  .chip {
    padding: 3px 9px;
    border-radius: 10px;
    font-size: 10px;
    font-weight: 700;
    color: white;
  }
  .chip.ai-managed {
    background: var(--selora-accent);
  }
  .chip.user-managed {
    background: var(--selora-zinc-600);
  }
  .chip.suggestion {
    background: var(--selora-accent);
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

  /* ---- Settings ---- */
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

  /* Narrow overrides — sidebar overlays on small screens */
  :host([narrow]) .body {
    position: relative;
  }
  :host([narrow]) .sidebar {
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    z-index: 10;
    width: 0;
    min-width: 0;
    transform: translateX(-100%);
    transition:
      transform 0.25s ease,
      width 0.25s ease,
      min-width 0.25s ease;
    box-shadow: 2px 0 8px rgba(0, 0, 0, 0.2);
  }
  :host([narrow]) .sidebar.open {
    width: 260px;
    min-width: 260px;
    transform: translateX(0);
  }

  .toast {
    position: fixed;
    right: 16px;
    bottom: 16px;
    z-index: 10050;
    max-width: min(420px, calc(100vw - 32px));
    padding: 10px 12px;
    border-radius: 10px;
    color: #fff;
    font-size: 13px;
    line-height: 1.4;
    box-shadow: 0 10px 28px rgba(0, 0, 0, 0.35);
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .toast.info {
    background: #1f6feb;
  }
  .toast.success {
    background: #198754;
  }
  .toast.error {
    background: #dc3545;
  }
  .toast-close {
    margin-left: auto;
    cursor: pointer;
    opacity: 0.85;
  }
  .toast-close:hover {
    opacity: 1;
  }

  /* ---- Connect-style scrollbar ---- */
  ::-webkit-scrollbar {
    width: 8px;
    height: 8px;
  }
  ::-webkit-scrollbar-track {
    background: transparent;
  }
  ::-webkit-scrollbar-thumb {
    background: var(--selora-accent);
    border-radius: 4px;
  }
  ::-webkit-scrollbar-thumb:hover {
    background: var(--selora-accent-light);
  }
  * {
    scrollbar-width: thin;
    scrollbar-color: var(--selora-accent) transparent;
  }

  /* ---- Gold gradient text (Connect brand) ---- */
  /* ---- Text selection ---- */
  ::selection {
    background: rgba(251, 191, 36, 0.3);
    color: inherit;
  }

  .gold-text {
    background-image: linear-gradient(
      90deg,
      #f59e0b,
      #fbbf24,
      #fde68a,
      #f59e0b
    );
    background-size: 300% 100%;
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
    animation: gold-shift 20s ease-in-out infinite;
  }
  @keyframes gold-shift {
    0%,
    100% {
      background-position: 0% 50%;
    }
    50% {
      background-position: 100% 50%;
    }
  }
`;
