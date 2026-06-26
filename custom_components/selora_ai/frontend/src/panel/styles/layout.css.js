import { css } from "lit";

export const layoutStyles = css`
  :host {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: var(--primary-background-color);
    color: var(--primary-text-color);
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
    position: relative;
  }

  /* ---- Particle band under header ---- */
  .main > selora-particles {
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 220px;
    z-index: 0;
    opacity: 0;
    transition: opacity 1s ease;
    pointer-events: none;
    touch-action: none;
    mask-image: radial-gradient(
      ellipse 70% 90% at top center,
      black 10%,
      transparent 70%
    );
    -webkit-mask-image: radial-gradient(
      ellipse 70% 90% at top center,
      black 10%,
      transparent 70%
    );
  }
  .main > selora-particles.visible {
    opacity: 1;
  }
  /* Hide all particle layers (background + welcome composer) when no
     LLM provider is configured — keeps the pre-setup screen calm. */
  :host([needs-setup]) selora-particles {
    display: none !important;
  }

  /* ---- Scroll view (automations / settings) ---- */
  .scroll-view {
    flex: 1;
    overflow-y: auto;
    padding: 24px 28px;
    max-width: 1200px;
    margin: 0 auto;
    position: relative;
    z-index: 1;
    width: 100%;
    box-sizing: border-box;
  }

  /* ---- Flat page layout ----
     The "flat" pattern (used by Recipes, rolling out to Scenes/Automations):
     a page-level title + lightweight uppercase section subheads, with content
     and cards floating directly on the page background — no outer .section-card
     panel. These are generic so any page can adopt them. */
  .page-root {
    display: flex;
    flex-direction: column;
    gap: 18px;
    max-width: 920px;
  }
  .page-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .page-h1 {
    font-size: 30px;
    font-weight: 700;
    color: var(--primary-text-color);
    margin: 0;
  }
  .page-intro {
    margin: -6px 0 4px;
    font-size: 14px;
    color: var(--secondary-text-color);
    line-height: 1.6;
    max-width: 70ch;
  }
  .page-section-title {
    font-size: 14px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--secondary-text-color);
  }
  /* On a flat page the row-list no longer sits inside a .section-card, so it
     needs its own surface or it vanishes into the dark page background. Scoped
     to .page-root so paneled pages (e.g. Automations until it's flattened)
     keep the transparent list that relies on their panel. */
  .page-root .automations-list {
    background: var(--card-background-color);
    overflow: hidden;
  }
  /* Same reasoning for suggestion cards (they were styled via .section-card
     .card) — restore their inner-card surface on a flat page. */
  .page-root .suggestions-section .card {
    background: var(--selora-inner-card-bg);
    border: 1px solid var(--selora-inner-card-border);
    border-radius: 14px;
  }
  /* A section header row: the .page-section-title on the left, action buttons
     on the right (e.g. Suggested for you → Scan Now / Generate). */
  .page-section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  /* Flat-page tab bar: drop the full-width baseline — it would run under any
     toolbar action on the row (the "line under the button" issue). The active
     tab's accent underline is the indicator. */
  .page-root .filter-tabs-row {
    border-bottom: none;
  }

  /* ---- Section cards ---- */
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
    margin-bottom: 16px;
    /* Allow narrow viewports (dev mode, side panel) to stack the
       title row above the action buttons instead of squeezing the
       heading into a wrapped two-line block. */
    flex-wrap: wrap;
  }
  .section-card-header h3 {
    font-size: 20px;
    margin: 0;
    font-weight: 700;
    line-height: 1.2;
    /* Title gets to grow but never shrink past its content — keeps
       "Suggested for you" on a single line until it has the space
       it needs. */
    flex: 0 0 auto;
    white-space: nowrap;
  }
  /* Title + count badge live together on the left. */
  .section-card-title-group {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    flex: 1 1 auto;
    min-width: 0;
  }
  /* Right-aligned cluster of action buttons. Wraps as a unit so the
     header collapses cleanly: title row, then actions row. */
  .section-card-actions {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    margin-left: auto;
    flex-wrap: wrap;
  }
  .section-card-actions .btn {
    white-space: nowrap;
  }
  .section-card-subtitle {
    font-size: 13px;
    color: var(--secondary-text-color);
    margin: 0 0 24px;
    line-height: 1.5;
  }
  /* When a subtitle directly follows a section header, tighten the gap
     to a deliberate 8px (header has 16px margin-bottom, subtitle pulls
     back 8px) so the heading and its description read as one block. */
  .section-card-header + .section-card-subtitle {
    margin-top: -8px;
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
  .toast.warning {
    background: #b45309;
  }
  .toast-close {
    margin-left: auto;
    cursor: pointer;
    opacity: 0.85;
  }
  .toast-close:hover {
    opacity: 1;
  }

  /* ---- Quota / 429 banner ---- */
  /* Sits above the active tab content, below the header. Red to match
     the alert particles. Auto-dismisses when retry_after elapses. */
  .quota-banner {
    position: relative;
    z-index: 5;
    margin: 12px 28px 0;
    padding: 10px 14px;
    border-radius: 10px;
    background: rgba(239, 68, 68, 0.12);
    border: 1px solid rgba(239, 68, 68, 0.4);
    color: var(--primary-text-color);
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 13px;
    line-height: 1.45;
    animation: quota-banner-in 240ms ease-out;
  }
  .quota-banner ha-icon {
    --mdc-icon-size: 20px;
    color: #ef4444;
    flex-shrink: 0;
  }
  .quota-banner-text {
    flex: 1;
    min-width: 0;
  }
  .quota-banner-close {
    background: none;
    border: none;
    cursor: pointer;
    padding: 4px;
    border-radius: 50%;
    color: var(--secondary-text-color);
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .quota-banner-close:hover {
    background: rgba(0, 0, 0, 0.08);
    color: var(--primary-text-color);
  }
  @keyframes quota-banner-in {
    from {
      transform: translateY(-8px);
      opacity: 0;
    }
    to {
      transform: none;
      opacity: 1;
    }
  }
  .telemetry-consent {
    position: relative;
    z-index: 5;
    margin: 12px 28px 0;
    padding: 12px 16px;
    border-radius: 10px;
    background: var(--secondary-background-color, rgba(127, 127, 127, 0.1));
    border: 1px solid var(--divider-color, rgba(127, 127, 127, 0.3));
    color: var(--primary-text-color);
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 13px;
    line-height: 1.45;
    animation: quota-banner-in 240ms ease-out;
  }
  .telemetry-consent ha-icon {
    --mdc-icon-size: 22px;
    color: var(--primary-color);
    flex-shrink: 0;
  }
  .telemetry-consent-text {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .telemetry-consent-actions {
    display: flex;
    gap: 8px;
    flex-shrink: 0;
  }
  @media (max-width: 600px) {
    .quota-banner {
      margin: 8px 10px 0;
      padding: 8px 10px;
    }
    .telemetry-consent {
      flex-wrap: wrap;
      margin: 8px 10px 0;
    }
    .telemetry-consent-actions {
      width: 100%;
      justify-content: flex-end;
    }
  }
`;
