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

  /* Scene entity tiles — HA-tile-card-like presentation, but the right
     column is unambiguously the *target* state the scene applies on
     activation (badge label + percentage bar), not the live state. */
  .scene-tiles {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin: 8px 0 12px;
  }
  .scene-tile {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 12px 14px;
    border-radius: 14px;
    background: var(--selora-inner-card-bg);
    border: 1px solid var(--selora-inner-card-border);
  }
  .scene-tile-icon {
    flex-shrink: 0;
    width: 40px;
    height: 40px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    background: color-mix(in srgb, var(--tile-accent) 18%, transparent);
    color: var(--tile-accent);
  }
  .scene-tile-icon ha-icon {
    --mdc-icon-size: 22px;
  }
  .scene-tile-body {
    flex: 1 1 auto;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .scene-tile-name {
    font-size: 14px;
    font-weight: 600;
    color: var(--primary-text-color);
    line-height: 1.25;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .scene-tile-subtitle {
    font-size: 12px;
    color: var(--secondary-text-color);
    line-height: 1.2;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .scene-tile-arrow {
    flex-shrink: 0;
    --mdc-icon-size: 18px;
    color: var(--secondary-text-color);
    opacity: 0.5;
  }
  .scene-tile-target {
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 6px;
    min-width: 110px;
  }
  .scene-tile-state {
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.02em;
    padding: 4px 10px;
    border-radius: 999px;
    white-space: nowrap;
    line-height: 1;
  }
  .scene-tile-state.active {
    background: color-mix(in srgb, var(--tile-accent) 18%, transparent);
    color: var(--tile-accent);
    border: 1px solid color-mix(in srgb, var(--tile-accent) 35%, transparent);
  }
  .scene-tile-state.inactive {
    background: transparent;
    color: var(--secondary-text-color);
    border: 1px solid var(--divider-color);
  }
  .scene-tile-bar {
    width: 130px;
    height: 6px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--tile-accent) 18%, transparent);
    overflow: hidden;
  }
  .scene-tile-bar-fill {
    height: 100%;
    background: var(--tile-accent);
    border-radius: 999px;
    transition: width 0.25s ease;
  }
  @media (max-width: 600px) {
    .scene-tile-target {
      min-width: 90px;
    }
    .scene-tile-bar {
      width: 90px;
    }
  }

  /* ---- Automation flowchart ----
     Sizes are deliberately closer to the surrounding chat bubble copy
     (14px) than the tiny 12px the chart originally used, since the
     flowchart is the primary signal of "here's what this automation
     does". The label stays smaller so the hierarchy reads
     "section heading → content" rather than two competing rows of
     equal-weight text. */
  .flow-chart {
    display: flex;
    flex-direction: column;
    align-items: center;
    margin: 12px 0 14px;
    font-size: 14px;
  }
  .flow-section {
    width: 100%;
    display: flex;
    flex-wrap: wrap;
    gap: 6px 8px;
    justify-content: center;
    text-align: center;
  }
  .flow-section > .flow-label {
    flex-basis: 100%;
  }
  /* Triggers and conditions are independent — multiple in the same
     section mean "any of these" or "all of these" rather than a
     sequence — so they flow side-by-side with the section's
     default wrap behavior.
     Actions, by contrast, run one after the other. We stack them
     vertically with downward arrows between, so the rendered card
     mirrors the YAML's top-to-bottom action list. Without this
     column override they wrapped horizontally and the small arrows
     between them ended up stranded between columns instead of
     between sequential steps. */
  .flow-section--stacked {
    flex-direction: column;
    align-items: center;
    gap: 8px;
  }
  .flow-label {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    opacity: 0.55;
    margin-bottom: 6px;
  }
  .flow-node {
    display: inline-block;
    padding: 10px 14px;
    border-radius: 10px;
    max-width: 100%;
    word-break: break-word;
    font-size: 14px;
    line-height: 1.5;
  }
  .trigger-node,
  .condition-node,
  .action-node {
    background: rgba(var(--rgb-primary-text-color, 255, 255, 255), 0.06);
    border: 1px solid rgba(var(--rgb-primary-text-color, 255, 255, 255), 0.15);
    color: var(--primary-text-color);
  }
  .flow-entity-link {
    display: inline-flex;
    align-items: baseline;
    gap: 4px;
    background: none;
    border: none;
    padding: 0;
    margin: 0;
    font: inherit;
    color: var(--selora-accent, #fbbf24);
    cursor: pointer;
    vertical-align: baseline;
    max-width: 100%;
  }
  .flow-entity-link > span {
    text-decoration: underline;
    text-decoration-color: rgba(251, 191, 36, 0.5);
    text-underline-offset: 3px;
    text-decoration-thickness: 1px;
    word-break: break-word;
    overflow-wrap: anywhere;
  }
  .flow-entity-link:hover > span {
    text-decoration-color: var(--selora-accent, #fbbf24);
  }
  .flow-entity-link:focus-visible {
    outline: 2px solid var(--selora-accent, #fbbf24);
    outline-offset: 2px;
    border-radius: 2px;
  }
  .flow-entity-link ha-icon {
    --mdc-icon-size: 16px;
    color: var(--selora-accent, #fbbf24);
    flex-shrink: 0;
    align-self: center;
    transform: translateY(-2px);
  }
  /* Light mode: gold text on the light flow-node reads poorly (and we avoid
     gold text in light mode). Switch the link + icon to the readable primary
     text color; the underline keeps the clickable affordance. */
  :host(:not([dark])) .flow-entity-link,
  :host(:not([dark])) .flow-entity-link ha-icon {
    color: var(--primary-text-color);
  }
  :host(:not([dark])) .flow-entity-link > span {
    text-decoration-color: rgba(var(--rgb-primary-text-color, 0, 0, 0), 0.4);
  }
  :host(:not([dark])) .flow-entity-link:hover > span {
    text-decoration-color: var(--primary-text-color);
  }
  .flow-duration {
    white-space: nowrap;
    color: var(--primary-text-color);
  }
  .flow-duration ha-icon {
    --mdc-icon-size: 14px;
    color: var(--secondary-text-color);
    vertical-align: middle;
    position: relative;
    top: -1px;
    margin-right: 3px;
  }
  /* Branching action structure: each 'choose' branch, each
     'parallel' / 'sequence' block, and each 'repeat' body gets
     its own bordered panel with a small uppercase label ("If",
     "Else if", "Otherwise", "In parallel", "Repeat 3 times"). Reads
     like the YAML structure but human-friendly, so the user can
     verify the rule without scrolling down to the YAML pane. */
  /* Choose actions render their branches SIDE BY SIDE so the user
     immediately reads them as alternative paths the automation
     picks between, not as sequential steps. Each column carries
     its own uppercase label ("If", "Else if", "Otherwise"). On
     narrow viewports we stack vertically to keep flow-node text
     legible without word-wrapping inside cramped columns. */
  .flow-choose {
    display: flex;
    flex-direction: row;
    flex-wrap: wrap;
    align-items: stretch;
    justify-content: center;
    gap: 10px;
    width: 100%;
  }
  .flow-choose > .flow-branch {
    flex: 1 1 200px;
    min-width: 0;
  }
  @media (max-width: 600px) {
    .flow-choose {
      flex-direction: column;
    }
  }
  .flow-branch {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    padding: 10px 12px 12px;
    border: 1px dashed rgba(var(--rgb-primary-text-color, 255, 255, 255), 0.22);
    border-radius: 10px;
    background: rgba(var(--rgb-primary-text-color, 255, 255, 255), 0.025);
  }
  .flow-branch-label {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    opacity: 0.6;
    align-self: center;
    margin-bottom: 2px;
  }
  /* Nested branches inside a parent — slightly tighter borders so
     two levels of nesting still read clearly. */
  .flow-branch .flow-branch {
    background: rgba(var(--rgb-primary-text-color, 255, 255, 255), 0.04);
  }
  .flow-arrow {
    font-size: 18px;
    line-height: 1;
    opacity: 0.4;
    padding: 4px 0;
    text-align: center;
  }
  .flow-arrow-sm {
    font-size: 14px;
    line-height: 1;
    opacity: 0.35;
    padding: 3px 0;
    text-align: center;
  }

  /* ---- Toggle switch ---- */
  .toggle-row {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 10px;
  }
  .toggle-switch {
    position: relative;
    width: 48px;
    height: 26px;
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
    border-radius: 13px;
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
    width: 20px;
    height: 20px;
    border-radius: 50%;
    background: white;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
    transition: left 0.2s;
  }
  .toggle-track.on .toggle-thumb {
    left: 24px;
  }
  .toggle-label {
    font-size: 14px;
    font-weight: 600;
    color: var(--secondary-text-color);
  }
  .toggle-label.on {
    color: var(--selora-accent-text);
  }
`;
