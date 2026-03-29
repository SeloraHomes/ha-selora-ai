// ---------------------------------------------------------------------------
// Shared CSS design tokens
// ---------------------------------------------------------------------------
import { css } from "lit";

export const seloraTokens = css`
  :host {
    color-scheme: light dark;
    --selora-accent: #fbbf24;
    --selora-accent-text: light-dark(#18181b, #fbbf24);
    --selora-accent-dark: #f59e0b;
    --selora-accent-light: #fde68a;
    --selora-zinc-900: var(--primary-background-color, #18181b);
    --selora-zinc-800: var(--card-background-color, #27272a);
    --selora-zinc-700: var(--divider-color, #3f3f46);
    --selora-zinc-600: var(--secondary-text-color, #52525b);
    --selora-zinc-200: var(--primary-text-color, #e4e4e7);
    --selora-zinc-400: var(--secondary-text-color, #a1a1aa);
    --selora-glow: 0 0 20px rgba(251, 191, 36, 0.3);
    --selora-glow-lg: 0 0 40px rgba(251, 191, 36, 0.4);
    /* Section card = HA card bg, Inner card = HA page bg */
    --selora-section-bg: var(--card-background-color, #27272a);
    --selora-section-border: var(--divider-color, #3f3f46);
    --selora-inner-card-bg: var(--primary-background-color, #18181b);
    --selora-inner-card-border: var(--divider-color, #3f3f46);
    --selora-btn-outline-border: var(--divider-color, #3f3f46);
    --selora-btn-outline-text: var(--primary-text-color, #e4e4e7);
    font-family:
      Inter,
      system-ui,
      -apple-system,
      BlinkMacSystemFont,
      "Segoe UI",
      Roboto,
      sans-serif;
  }
  * {
    font-family: inherit;
  }
`;
