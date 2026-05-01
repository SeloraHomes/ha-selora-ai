import { css } from "lit";

export const chatStyles = css`
  .chat-pane {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    position: relative;
    z-index: 1;
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

  /* ---- Welcome: composer-centered layout ---- */
  .chat-welcome-center {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow-y: auto;
    padding: 24px;
  }
  .welcome-center-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    max-width: 560px;
    width: 100%;
    animation: fadeInUp 0.5s ease both;
  }
  .welcome-center-content > img {
    animation: logoEntrance 0.7s cubic-bezier(0.34, 1.56, 0.64, 1) both;
  }
  .welcome-center-content .chat-input {
    width: 100%;
  }
  .welcome-center-content .qa-group {
    width: 100%;
  }

  /* Particle field surrounding the welcome composer */
  .welcome-composer-area {
    position: relative;
    width: 100%;
    padding: 56px 0;
    margin-top: 24px;
    box-sizing: border-box;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .welcome-composer-particles {
    position: absolute;
    top: 0;
    bottom: 0;
    left: -20%;
    right: -20%;
    pointer-events: none;
    opacity: 0;
    transition: opacity 1.2s ease;
    mask-image: radial-gradient(
      ellipse 70% 80% at center,
      black 25%,
      rgba(0, 0, 0, 0.6) 55%,
      transparent 85%
    );
    -webkit-mask-image: radial-gradient(
      ellipse 70% 80% at center,
      black 25%,
      rgba(0, 0, 0, 0.6) 55%,
      transparent 85%
    );
  }
  .welcome-composer-particles.visible {
    opacity: 1;
  }

  /* Particles above docked composer in ongoing chat */
  .chat-input-wrapper {
    position: relative;
    flex-shrink: 0;
    padding-bottom: env(safe-area-inset-bottom, 0px);
    background: var(--primary-background-color);
  }
  .composer-dock-particles {
    position: absolute;
    top: -20px;
    left: 0;
    right: 0;
    height: 20px;
    z-index: 0;
    opacity: 0;
    transition: opacity 1s ease;
    mask-image: linear-gradient(to top, black, transparent);
    -webkit-mask-image: linear-gradient(to top, black, transparent);
  }
  .composer-dock-particles.visible {
    opacity: 1;
  }

  @media (max-width: 600px) {
    .chat-welcome-center {
      padding: 16px 12px;
    }
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
    color: var(--primary-text-color);
    font-weight: 700;
  }

  /* ---- Chat input ---- */
  .chat-input {
    max-width: 1200px;
    margin: 0 auto;
    box-sizing: border-box;
    width: 100%;
    background: transparent;
    display: block;
    padding: 0;
  }

  .composer-styled {
    position: relative;
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 56px;
    border: 1px solid rgba(251, 191, 36, 0.5);
    border-radius: 18px;
    padding: 10px 10px 10px 24px;
    background: var(--card-background-color, #27272a);
    box-sizing: border-box;
    overflow: hidden;
    box-shadow:
      0 1px 2px rgba(0, 0, 0, 0.18),
      inset 0 1px 0 rgba(255, 255, 255, 0.03);
    transition:
      border-color 0.25s ease,
      box-shadow 0.25s ease;
  }
  .composer-styled:focus-within {
    border-color: rgba(251, 191, 36, 0.55);
    box-shadow:
      0 0 0 1px rgba(251, 191, 36, 0.14),
      0 10px 30px rgba(0, 0, 0, 0.18),
      inset 0 1px 0 rgba(255, 255, 255, 0.04);
  }
  /* Welcome variant: contained input with top and bottom glow lines. */
  .composer-welcome {
    position: relative;
    z-index: 1;
    width: 100%;
    max-width: 640px;
  }
  /* Top edge: 1px gradient line, brightest in the middle */
  .composer-welcome::before {
    content: "";
    position: absolute;
    top: 0;
    left: 24px;
    right: 24px;
    height: 1px;
    background: linear-gradient(
      90deg,
      transparent 0%,
      rgba(245, 158, 11, 0.85) 50%,
      transparent 100%
    );
    pointer-events: none;
    z-index: 0;
  }
  /* Bottom edge: matching 1px gradient line */
  .composer-welcome::after {
    content: "";
    position: absolute;
    bottom: 0;
    left: 24px;
    right: 24px;
    height: 1px;
    background: linear-gradient(
      90deg,
      transparent 0%,
      rgba(245, 158, 11, 0.85) 50%,
      transparent 100%
    );
    pointer-events: none;
    z-index: 0;
  }
  /* Soft halos sitting above and below the composer, centered on the glow line */
  .welcome-composer-area::before,
  .welcome-composer-area::after {
    content: "";
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    width: 70%;
    max-width: 520px;
    height: 32px;
    background: radial-gradient(
      ellipse 50% 100% at center,
      rgba(251, 191, 36, 0.55) 0%,
      rgba(245, 158, 11, 0.22) 35%,
      rgba(245, 158, 11, 0.06) 65%,
      transparent 100%
    );
    filter: blur(5px);
    pointer-events: none;
    z-index: 0;
  }
  .welcome-composer-area::before {
    top: calc(50% - 27px - 16px);
  }
  .welcome-composer-area::after {
    top: calc(50% + 27px - 16px);
  }
  .composer-welcome:focus-within {
    border-color: rgba(251, 191, 36, 0.55);
  }
  .composer-welcome:focus-within::before,
  .composer-welcome:focus-within::after {
    background: linear-gradient(
      90deg,
      transparent 0%,
      rgba(251, 191, 36, 1) 50%,
      transparent 100%
    );
  }
  .welcome-center-content .composer-styled {
    margin: 0;
  }
  .chat-input-wrapper .composer-styled {
    margin: 10px auto;
    max-width: calc(1200px - 48px);
    width: calc(100% - 48px);
  }
  @media (max-width: 600px) {
    .chat-input-wrapper .composer-styled {
      margin: 8px auto;
      width: calc(100% - 24px);
    }
  }

  .composer-textarea {
    position: relative;
    z-index: 1;
    flex: 1 1 auto;
    min-width: 0;
    width: auto;
    min-height: 36px;
    resize: none;
    border: none;
    outline: none;
    background: transparent;
    color: var(--primary-text-color);
    font-family: inherit;
    font-size: 15px;
    line-height: 22px;
    padding: 7px 0;
    margin: 0;
    max-height: 200px;
    overflow-y: auto;
    box-sizing: border-box;
    display: block;
    vertical-align: middle;
  }
  .composer-textarea::placeholder {
    color: var(--secondary-text-color);
    opacity: 0.7;
  }
  .composer-textarea:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .composer-send {
    position: relative;
    z-index: 1;
    flex: 0 0 36px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: linear-gradient(135deg, #fcd34d 0%, #fbbf24 50%, #f59e0b 100%);
    border: none;
    cursor: pointer;
    width: 36px;
    height: 36px;
    border-radius: 50%;
    color: #1a1300;
    --mdc-icon-size: 18px;
    margin: 0;
    padding: 0;
    box-shadow:
      0 1px 2px rgba(0, 0, 0, 0.35),
      0 0 12px -2px rgba(251, 191, 36, 0.55),
      inset 0 1px 0 rgba(255, 255, 255, 0.35);
    transition:
      transform 0.15s ease,
      box-shadow 0.2s ease,
      opacity 0.15s ease;
  }
  .composer-send ha-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    line-height: 0;
  }
  .composer-send:hover {
    transform: scale(1.06);
    box-shadow:
      0 2px 4px rgba(0, 0, 0, 0.4),
      0 0 18px -2px rgba(251, 191, 36, 0.7),
      inset 0 1px 0 rgba(255, 255, 255, 0.4);
  }
  .composer-send:active {
    transform: scale(0.96);
  }
  .composer-send:disabled {
    cursor: default;
    transform: none;
    opacity: 0.7;
    box-shadow:
      0 1px 2px rgba(0, 0, 0, 0.25),
      inset 0 1px 0 rgba(255, 255, 255, 0.2);
  }
  .composer-send:disabled:hover {
    transform: none;
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
`;
