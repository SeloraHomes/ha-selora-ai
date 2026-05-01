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
  }
  .section-card-header h3 {
    font-size: 20px;
    margin: 0;
    font-weight: 700;
    line-height: 1.2;
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
  .toast-close {
    margin-left: auto;
    cursor: pointer;
    opacity: 0.85;
  }
  .toast-close:hover {
    opacity: 1;
  }
`;
