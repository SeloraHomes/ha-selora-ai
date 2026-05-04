import { css } from "lit";

export const proposalStyles = css`
  /* ---- Automation proposal card ---- */
  .proposal-card {
    margin-top: 12px;
    border: 1px solid rgba(251, 191, 36, 0.25);
    border-radius: 16px;
    overflow: hidden;
    background: var(--primary-background-color);
  }
  .proposal-header {
    background: transparent;
    border-bottom: 1px solid var(--divider-color);
    padding: 10px 14px;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: normal;
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--primary-text-color);
  }
  .proposal-body {
    padding: 14px;
  }
  .proposal-body .flow-chart {
    align-items: center;
  }
  .proposal-body .flow-section {
    text-align: center;
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
    color: var(--secondary-text-color);
    display: flex;
    align-items: center;
    gap: 4px;
    margin-bottom: 8px;
    user-select: none;
  }
  .yaml-toggle:hover {
    color: var(--primary-text-color);
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

  /* ---- Scene entity list ---- */
  .scene-entity-list {
    margin: 8px 0 12px;
    border: 1px solid var(--divider-color);
    border-radius: 8px;
    overflow: hidden;
  }
  .scene-entity-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 10px;
    font-size: 12px;
    border-bottom: 1px solid var(--divider-color);
  }
  .scene-entity-row:last-child {
    border-bottom: none;
  }
  .scene-entity-name {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--primary-text-color);
    min-width: 0;
    overflow: hidden;
  }
  .scene-entity-name > span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .scene-entity-state {
    display: flex;
    align-items: center;
    gap: 8px;
    font-weight: 600;
    flex-shrink: 0;
  }
  .scene-entity-attr {
    font-size: 11px;
    opacity: 0.6;
    font-weight: 400;
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
`;
