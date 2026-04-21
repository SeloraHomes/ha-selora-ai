import { css } from "lit";

export const chatStyles = css`
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
    color: var(--primary-text-color);
    font-weight: 700;
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
