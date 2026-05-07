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
    justify-content: center;
  }

  /* Quick-start disclosure on the welcome screen */
  .welcome-quickstart {
    width: 100%;
    margin-top: 20px;
  }
  .welcome-quickstart-summary {
    list-style: none;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    margin: 0 auto 12px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    opacity: 0.5;
    border-radius: 999px;
    transition:
      opacity 0.2s,
      background-color 0.2s;
    user-select: none;
  }
  .welcome-quickstart-summary::-webkit-details-marker {
    display: none;
  }
  .welcome-quickstart-summary:hover {
    opacity: 0.8;
    background-color: rgba(255, 255, 255, 0.04);
  }
  :host(:not([dark])) .welcome-quickstart-summary:hover {
    background-color: rgba(0, 0, 0, 0.04);
  }
  .welcome-quickstart-chevron {
    --mdc-icon-size: 16px;
    transition: transform 0.2s ease;
  }
  .welcome-quickstart[open] .welcome-quickstart-chevron {
    transform: rotate(180deg);
  }
  /* Center the summary itself in the centered welcome layout */
  .welcome-quickstart {
    display: flex;
    flex-direction: column;
    align-items: center;
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
  /* Entity-list grid: hosts real HA hui-tile-card elements. Cards
     bring their own borders, padding, theming, click target — the
     grid only handles layout. Single-entity references render here
     too as a one-card grid; uniform look across all mentions. The
     minmax(240px, 1fr) sizing matches the default cell width HA uses
     on tile-style dashboard sections, so chat tiles don't truncate
     the friendly name (180px was too tight for long room names). */
  .selora-entity-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 8px;
    margin: 12px 0;
    width: 100%;
  }
  .selora-entity-grid > * {
    /* Cards default to 56px tall in tile mode; let them size themselves
       without our own min-height fighting it. */
    min-width: 0;
    /* Lift the card off the chat bubble. Layered shadow (tight inner
       contact + softer ambient drop) reads as physical depth so the
       embedded widget visibly pops rather than sitting flush. The top
       border-color highlight reinforces the upper edge to sell the
       lifted look in dark mode. */
    --ha-card-border-color: var(--selora-zinc-700);
    --ha-card-box-shadow:
      0 1px 2px rgba(0, 0, 0, 0.3), 0 6px 16px rgba(0, 0, 0, 0.35);
  }
  :host(:not([dark])) .selora-entity-grid > * {
    --ha-card-border-color: var(--divider-color);
    --ha-card-box-shadow:
      0 1px 2px rgba(0, 0, 0, 0.06), 0 4px 12px rgba(0, 0, 0, 0.1);
  }
  /* Suppress the stuck hover/focus tint that hui-entities-card paints
     on a row after the user taps it (the more-info dialog closes but
     the row keeps :focus-visible, leaving one card darker than the
     rest). Chat doesn't need a row-level affordance — the toggle/
     control inside the row is the click target. */
  .selora-entity-grid > *::part(content),
  .selora-entity-grid > * div.entity {
    background: transparent !important;
  }
  /* Area sub-headers in multi-area entity grids. The grid-column rule
     spans the header across the full row so the next row of tiles
     starts cleanly under it. Layout matches HA dashboard section
     headers: small uppercase label with the area icon to its left. */
  .selora-area-header {
    grid-column: 1 / -1;
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    font-weight: 600;
    line-height: 1;
    color: var(--secondary-text-color);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-top: 8px;
    margin-bottom: -2px;
  }
  .selora-area-header:first-child {
    margin-top: 0;
  }
  .selora-area-icon {
    /* Match the cap-height of the 12px uppercase label so the icon
       sits flush with the text, not floating above it. ha-icon needs
       both the CSS variable AND an explicit box size — the variable
       controls the SVG glyph, the box prevents the host element from
       reserving its 24px default and pushing the label down. */
    --mdc-icon-size: 14px;
    width: 14px;
    height: 14px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: var(--secondary-text-color);
    flex-shrink: 0;
  }
  /* ---- Stream interruption notice ---- */
  .stream-interrupt {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 12px;
    padding: 8px 10px;
    border-radius: 8px;
    background: rgba(244, 67, 54, 0.08);
    border: 1px solid rgba(244, 67, 54, 0.25);
    color: var(--error-color, #f44336);
    font-size: 13px;
  }
  .stream-interrupt-text {
    flex: 1;
    color: var(--primary-text-color);
  }
  /* Retry link in the bubble-meta — matches "Selora AI · time" rhythm
     but uses the accent colour so the failure state is visible without
     resorting to a button-style chip. */
  .stream-interrupt-retry {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    padding: 0;
    border: none;
    background: none;
    color: var(--selora-accent, #fbbf24);
    font: inherit;
    font-weight: 600;
    cursor: pointer;
    opacity: 0.85;
    transition: opacity 120ms ease;
  }
  .stream-interrupt-retry:hover,
  .stream-interrupt-retry:focus-visible {
    opacity: 1;
    text-decoration: underline;
    outline: none;
  }
  .stream-interrupt-retry ha-icon {
    color: var(--selora-accent, #fbbf24);
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
