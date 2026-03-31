import { css } from "lit";

export const sharedScrollbar = css`
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
`;
