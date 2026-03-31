import { css } from "lit";

export const sharedAnimations = css`
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
  @keyframes highlightRow {
    0%,
    30% {
      background: rgba(251, 191, 36, 0.15);
    }
    100% {
      background: transparent;
    }
  }
  @keyframes fadeOutCard {
    to {
      opacity: 0;
      transform: scale(0.95);
    }
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
  @keyframes blink {
    50% {
      opacity: 0;
    }
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
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
