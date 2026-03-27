// ---------------------------------------------------------------------------
// Shared CSS design tokens
// ---------------------------------------------------------------------------
import { css } from "lit";

export const seloraTokens = css`
  :host {
    --selora-accent: #fbbf24;
    --selora-accent-dark: #f59e0b;
    --selora-accent-light: #fde68a;
    --selora-zinc-900: #18181b;
    --selora-zinc-800: #27272a;
    --selora-zinc-700: #3f3f46;
    --selora-zinc-600: #52525b;
    --selora-zinc-200: #e4e4e7;
    --selora-zinc-400: #a1a1aa;
    --selora-glow: 0 0 20px rgba(251, 191, 36, 0.3);
    --selora-glow-lg: 0 0 40px rgba(251, 191, 36, 0.4);
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
