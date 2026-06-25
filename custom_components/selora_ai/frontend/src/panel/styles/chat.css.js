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

  .chat-jump-bottom {
    position: absolute;
    right: 24px;
    bottom: calc(100% + 8px);
    width: 36px;
    height: 36px;
    border-radius: 50%;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color);
    color: var(--primary-text-color);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
    transition:
      background-color 0.15s,
      transform 0.15s;
    z-index: 2;
  }
  .chat-jump-bottom:hover {
    background: var(--secondary-background-color);
    transform: translateY(-1px);
  }
  .chat-jump-bottom ha-icon {
    --mdc-icon-size: 22px;
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

  /* Embossed sub-card used by chat-driven automation proposals (and
     saved / refining states). Mirrors the layered shadow used by the
     entity tiles in .selora-entity-grid so an automation summary
     visually "lifts" out of the chat bubble the same way an entity
     card does. The action button (Accept & Save, Enable) sits OUTSIDE
     this wrapper, on the chat bubble itself. */
  .automation-subcard {
    margin-top: 12px;
    padding: 14px 16px;
    border-radius: 12px;
    background: var(--card-background-color, rgba(255, 255, 255, 0.02));
    border: 1px solid var(--selora-zinc-700);
    box-shadow:
      inset 0 1px 0 rgba(255, 255, 255, 0.04),
      0 1px 2px rgba(0, 0, 0, 0.3),
      0 6px 16px rgba(0, 0, 0, 0.35);
  }
  :host(:not([dark])) .automation-subcard {
    border-color: var(--divider-color);
    box-shadow:
      inset 0 1px 0 rgba(255, 255, 255, 0.6),
      0 1px 2px rgba(0, 0, 0, 0.06),
      0 4px 12px rgba(0, 0, 0, 0.1);
  }
  .automation-subcard-header {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--divider-color);
  }
  .automation-subcard-body {
    padding-top: 12px;
  }
  .automation-card-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 12px;
  }

  /* Accept-button exit animation. Triggered the moment the user
     clicks Accept, BEFORE the WS save round-trip — so the button
     dissolving down feels like the cause of the chat card flipping
     to its saved state. Duration mirrors ACCEPT_ANIM_MS in
     automation-crud.js (240ms). The fill-mode keeps the button
     invisible during the brief gap between the animation ending and
     the saved card mounting. */
  @keyframes accept-button-exit {
    from {
      opacity: 1;
      transform: translateY(0) scale(1);
    }
    to {
      opacity: 0;
      transform: translateY(8px) scale(0.96);
    }
  }
  .automation-card-actions.exiting {
    animation: accept-button-exit 240ms cubic-bezier(0.4, 0, 0.6, 1) forwards;
    pointer-events: none;
  }
  /* Entrance animation for the saved-state workflow row. Slides up
     from below and fades in, taking visual ownership of the spot
     the Accept button just vacated. Slightly longer than the exit
     so it lands settled (240 exit + 280 enter ≈ half-second total). */
  @keyframes workflow-enter {
    from {
      opacity: 0;
      transform: translateY(10px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }
  .automation-workflow {
    animation: workflow-enter 280ms cubic-bezier(0.16, 1, 0.3, 1) both;
  }
  /* Enable → Enabled swap: the Enabled status text fades in when the
     toggle completes (the button vanishes the moment _loadAutomations
     resolves; this softens the hand-off). */
  @keyframes workflow-done-enter {
    from {
      opacity: 0;
      transform: scale(0.95);
    }
    to {
      opacity: 1;
      transform: scale(1);
    }
  }
  .automation-workflow-done {
    animation: workflow-done-enter 220ms cubic-bezier(0.16, 1, 0.3, 1) both;
  }

  /* Two-step lifecycle row shown beneath the saved automation
     sub-card: Accepted ✓  →  Enable automation. The completed step
     reads as a quiet checkmark chip on the left, an arrow points
     across to the active step (the green CTA on the right). Once the
     user clicks Enable, the right side flips to its own completed
     chip so the whole row reads "Accepted → Enabled". Replaces the
     louder yellow info banner that read like an error message. */
  .automation-workflow {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 10px;
    margin-top: 14px;
    flex-wrap: wrap;
  }
  .automation-workflow-arrow {
    color: var(--secondary-text-color);
    opacity: 0.6;
    flex-shrink: 0;
  }
  /* "Accepted" / "Enabled" status indicator on the workflow row. NOT
     a button — just a quiet check + label that reads as state, with
     the live action (Enable automation) staying a standard
     btn-success so the row matches the rest of the app's
     button language. */
  .automation-workflow-done {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    font-weight: 600;
    color: var(--success-color, #4caf50);
    line-height: 1.2;
  }
  .automation-workflow-done ha-icon {
    color: inherit;
    flex-shrink: 0;
  }
  /* Edge case: created-disabled state where we haven't resolved the
     freshly-created automation yet (between WS create and the next
     _loadAutomations). Just a muted "Disabled" label, not a CTA. */
  .automation-workflow-done.muted {
    color: var(--secondary-text-color);
  }
  /* CSS-only hover tooltip. Used on the "Accepted" chip to explain
     why the automation lands disabled — the chip alone is succinct,
     the tooltip carries the safety rationale for users who want to
     understand the default. Positioned above the host, fades in on
     hover, and stays out of the click target with pointer-events
     none so it never blocks the underlying button beneath it. */
  .has-tooltip {
    position: relative;
  }
  .has-tooltip::after {
    content: attr(data-tooltip);
    position: absolute;
    bottom: calc(100% + 8px);
    left: 50%;
    transform: translateX(-50%) translateY(4px);
    width: max-content;
    max-width: 280px;
    padding: 8px 12px;
    background: var(--card-background-color, #1f1f1f);
    color: var(--primary-text-color);
    border: 1px solid var(--divider-color);
    border-radius: 8px;
    font-size: 12px;
    font-weight: 500;
    line-height: 1.4;
    white-space: normal;
    text-align: center;
    box-shadow: 0 6px 20px rgba(0, 0, 0, 0.35);
    pointer-events: none;
    opacity: 0;
    transition:
      opacity 0.18s ease,
      transform 0.18s ease;
    z-index: 20;
  }
  .has-tooltip::before {
    content: "";
    position: absolute;
    bottom: calc(100% + 2px);
    left: 50%;
    transform: translateX(-50%) translateY(4px);
    border: 6px solid transparent;
    border-top-color: var(--divider-color);
    pointer-events: none;
    opacity: 0;
    transition:
      opacity 0.18s ease,
      transform 0.18s ease;
    z-index: 20;
  }
  .has-tooltip:hover::after,
  .has-tooltip:focus-visible::after,
  .has-tooltip:hover::before,
  .has-tooltip:focus-visible::before {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }

  /* Optional one-line caveat printed under the workflow row when the
     automation uses elevated-risk actions. Subtle warning tint —
     much smaller footprint than the previous banner. */
  .automation-workflow-note {
    display: flex;
    align-items: center;
    gap: 6px;
    justify-content: flex-end;
    margin: 8px 0 0;
    font-size: 12px;
    color: var(--warning-color, #ff9800);
    opacity: 0.85;
  }

  /* "Suggest one for me" button shown under the composer in
     new-automation mode. Sits below the particle field, doesn't
     wrap, and uses a subtle outlined treatment so the composer
     still owns the visual focus. */
  .welcome-suggest-btn {
    /* Pulled up into the bottom of the composer's particle padding so
       it visually hugs the composer. Must claim its own stacking
       context (position+z-index) — without it the sibling
       .welcome-composer-area (position:relative) renders on top of
       the overlapping band and swallows clicks on everything but the
       text/border edges that poke above the overlap. */
    position: relative;
    z-index: 2;
    margin-top: -24px;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 14px;
    border-radius: 999px;
    background: transparent;
    border: 1px solid var(--divider-color);
    color: var(--secondary-text-color);
    font-size: 13px;
    font-family: inherit;
    cursor: pointer;
    white-space: nowrap;
    transition:
      border-color 0.15s,
      color 0.15s,
      background 0.15s;
  }
  .welcome-suggest-btn:hover:not(:disabled) {
    border-color: var(--selora-accent, #fbbf24);
    color: var(--primary-text-color);
    background: rgba(251, 191, 36, 0.06);
  }
  .welcome-suggest-btn:disabled {
    opacity: 0.55;
    cursor: progress;
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
  .assistant-wrap {
    display: inline-flex;
    flex-direction: column;
    max-width: 82%;
    align-self: flex-start;
  }
  /* Approval / proposal cards bring their own card chrome. On narrow
     viewports the 82% cap leaves a wasteful right gutter and crunches
     the flowchart, so let the proposal stretch to the full chat
     column. Desktop keeps the standard bubble width. */
  @media (max-width: 870px) {
    .assistant-wrap--approval {
      display: flex;
      max-width: 100%;
      width: 100%;
      align-self: stretch;
    }
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
  /* User messages are inserted verbatim via textContent, so newlines
     are real text nodes. Preserve them (and wrap long lines) instead of
     collapsing the message to a single line. */
  .bubble.user .msg-content {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .bubble.assistant {
    align-self: flex-start;
    background: var(--card-background-color);
    box-shadow: var(--card-box-shadow);
    border: 1px solid var(--selora-zinc-700);
    border-bottom-left-radius: 4px;
  }
  /* When the assistant bubble's only content is an approval card, the
     card already provides its own border + background — wrapping it
     in the standard bubble shell would nest two cards. Strip the
     bubble chrome so the card sits flush against the chat column. */
  .bubble.assistant.bubble--approval {
    background: transparent;
    box-shadow: none;
    border: none;
    padding: 0;
  }
  .bubble-meta {
    font-size: 10px;
    opacity: 0.5;
    margin-top: 0;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  /* The meta row only adds space above itself when it actually shows
     something: the streaming label (a bare span), a REAL action group
     (proposal/quick chips actually rendered — not an empty .msg-quick
     wrapper), a hovered feedback cluster, or a chosen/just-copied state.
     Keyed on the rendered group classes, not .msg-quick, because that
     wrapper is present even when renderProposalActions yields nothing —
     which would otherwise pad empty space above the next message. */
  .bubble-meta:has(> span),
  .bubble-meta:has(.qa-group),
  .bubble-meta:has(.automation-card-actions),
  .bubble-meta:has(.automation-workflow),
  .bubble-meta:has(.msg-actions) {
    margin-top: 8px;
  }
  /* The action group inside msg-quick brings its own top margin meant
     for when it sat on the bubble; the meta row now owns that spacing. */
  .msg-quick .automation-card-actions,
  .msg-quick .automation-workflow,
  .msg-quick .qa-group {
    margin-top: 0;
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
  /* The feedback cluster always reserves its vertical space (the row keeps
     its natural height whether or not the buttons are visible), so hovering
     only fades the buttons in — it never grows the layout and shifts the
     messages below. Hidden via opacity alone, not max-height/display, so
     the space stays claimed and the fade still animates. */
  .msg-actions {
    display: flex;
    align-items: center;
    gap: 2px;
    opacity: 0;
    transition: opacity 0.15s ease;
    /* Linger on mouse-leave so the cursor can travel down to a button
       without the row losing :hover snatching it away mid-click. */
    transition-delay: 0.6s;
  }
  .message-row:hover .msg-actions,
  .msg-actions:has(.active),
  .msg-actions:has(.copied) {
    opacity: 1;
    transition-delay: 0s;
  }
  /* Touch devices have no real hover — reveal the buttons permanently so
     they're reachable without a hover state to trigger them. */
  @media (hover: none) {
    .msg-actions {
      opacity: 1;
      transition-delay: 0s;
    }
  }
  /* Quick-action chips share the row with the feedback buttons, pinned
     right. Drop the chip group's standalone top margin so it lines up
     with the buttons; on a narrow screen the meta row wraps and this
     block keeps its own gap consistent. */
  .msg-quick {
    margin-left: auto;
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 8px;
  }
  .msg-quick .qa-group {
    margin-top: 0;
    justify-content: flex-end;
  }
  .msg-action-btn {
    background: none;
    border: none;
    padding: 4px;
    cursor: pointer;
    opacity: 0.6;
    transition:
      opacity 0.15s,
      color 0.15s,
      background 0.15s;
    color: inherit;
    line-height: 1;
    border-radius: 6px;
    display: inline-flex;
    align-items: center;
  }
  .msg-action-btn:hover {
    opacity: 1;
    background: var(--secondary-background-color, rgba(0, 0, 0, 0.06));
  }
  .msg-action-btn:active {
    transform: scale(0.85);
  }
  .msg-action-btn.active {
    opacity: 1;
    color: var(--selora-accent, #fbbf24);
  }
  .msg-action-btn.copied {
    opacity: 1;
    color: var(--success-color, #4caf50);
  }
  /* Click feedback: a quick pop on the icon. Applied via a transient
     .pulse class (re-added on every click) so repeated clicks re-fire. */
  .msg-action-btn.pulse ha-icon,
  .selora-code-copy.pulse svg {
    animation: msg-action-pop 0.3s ease;
  }
  @keyframes msg-action-pop {
    0% {
      transform: scale(1);
    }
    40% {
      transform: scale(1.35);
    }
    100% {
      transform: scale(1);
    }
  }
  /* Per-fence copy button overlaid top-right of a code block. The
     button lives inside an innerHTML blob so it has no lit listener —
     clicks bubble to the message span (see _onCodeCopyClick). */
  .selora-code-block {
    position: relative;
  }
  .selora-code-block .selora-code-copy {
    position: absolute;
    top: 6px;
    right: 6px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 4px;
    border: none;
    border-radius: 6px;
    background: var(--secondary-background-color, rgba(255, 255, 255, 0.1));
    color: var(--primary-text-color, #e4e4e7);
    cursor: pointer;
    opacity: 0;
    line-height: 0;
    transition:
      opacity 0.15s,
      color 0.15s;
  }
  .selora-code-block:hover .selora-code-copy {
    opacity: 0.7;
  }
  .selora-code-block .selora-code-copy:hover {
    opacity: 1;
  }
  .selora-code-block .selora-code-copy:active {
    transform: scale(0.85);
  }
  .selora-code-block .selora-code-copy.copied {
    opacity: 1;
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
    /* minmax(240px, 280px) caps tile width at 280px even in a row
       with empty cells, so a single-tile row does not blow up the
       tile to the full bubble width while a multi-tile row keeps
       its cells at ~260px. Without the upper cap (previously 1fr),
       a lone tile in a wide bubble stretched 2x wider than a tile
       in a denser row, and the inconsistency read as a bug.
       align-items: stretch (default) still matches tiles to the
       tallest item in their row. Rows size to their natural
       content — we deliberately do NOT set grid-auto-rows: 1fr
       because that also stretches the area-header rows. */
    grid-template-columns: repeat(auto-fill, minmax(240px, 280px));
    gap: 8px;
    margin: 12px 0;
    width: 100%;
  }
  .selora-entity-grid > * {
    /* Let each tile size itself to its natural content height. A
       previous version forced height: 100% so feature-less tiles
       would match the height of light tiles in the same row, but
       HA's light-brightness feature interprets the extra row
       height as room to grow and renders a comically oversized
       slider. Natural sizing keeps every feature rendering at HA's
       intended thickness, even if it means a switch tile is
       shorter than a neighbouring light tile in the same row. */
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
  .chat-quick-actions {
    margin: 0 auto;
    padding-top: 8px;
    max-width: calc(1200px - 48px);
    width: calc(100% - 48px);
  }
  .chat-quick-actions .qa-group {
    margin-top: 0;
  }
  @media (max-width: 600px) {
    .chat-input-wrapper .composer-styled {
      margin: 8px auto;
      width: calc(100% - 24px);
    }
    .chat-quick-actions {
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

  /* ---- Composer autocomplete ---- */
  /* Positioned ancestor for the dropdown. The composer-styled element
     itself has overflow:hidden for the welcome-variant glow, which
     would clip the suggestions if anchored to it. */
  .composer-wrap {
    position: relative;
    width: 100%;
  }
  .composer-autocomplete {
    position: absolute;
    bottom: calc(100% + 6px);
    left: 0;
    right: 0;
    z-index: 10;
    background: var(--card-background-color, #27272a);
    border: 1px solid var(--divider-color);
    border-radius: 12px;
    box-shadow:
      0 8px 24px rgba(0, 0, 0, 0.3),
      0 1px 2px rgba(0, 0, 0, 0.18);
    /* overflow shorthand sets both axes in one go — clip horizontally,
       scroll vertically. Previous ordering ("overflow: hidden" then
       "overflow-y: auto") was overriding in some browsers so the body
       couldn't scroll when results spilled past max-height. */
    overflow: hidden auto;
    max-height: 320px;
    /* Round the inner content corners too so the scroll thumb doesn't
       paint over the dropdown's border radius. */
    overscroll-behavior: contain;
  }
  .composer-autocomplete-header {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 12px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--secondary-text-color);
    background: rgba(255, 255, 255, 0.02);
    border-bottom: 1px solid var(--divider-color);
  }
  /* Add breathing room and a clear divider before secondary section
     headers (Areas after Devices) so the two groups read as distinct
     instead of a single run of rows under a single label. */
  .composer-autocomplete-header:not(:first-child) {
    margin-top: 6px;
    border-top: 1px solid var(--divider-color);
  }
  .composer-autocomplete-header ha-icon {
    --mdc-icon-size: 14px;
  }
  .composer-autocomplete-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 12px;
    cursor: pointer;
    font-size: 14px;
    color: var(--primary-text-color);
    border: none;
    background: transparent;
    width: 100%;
    text-align: left;
    font-family: inherit;
  }
  .composer-autocomplete-item:hover,
  .composer-autocomplete-item.active {
    background: rgba(251, 191, 36, 0.1);
  }
  .composer-autocomplete-item ha-icon {
    --mdc-icon-size: 18px;
    color: var(--secondary-text-color);
    flex-shrink: 0;
  }
  .composer-autocomplete-label {
    flex: 1;
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .composer-autocomplete-area {
    font-size: 11px;
    color: var(--secondary-text-color);
    flex-shrink: 0;
  }
  .composer-autocomplete-hint {
    padding: 4px 12px 6px;
    font-size: 10px;
    color: var(--secondary-text-color);
    text-align: right;
    opacity: 0.7;
  }

  /* Wrap textarea + chips in a column so chips sit inside the rounded
     composer border instead of overflowing the page edge. */
  .composer-input-col {
    flex: 1 1 auto;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  /* The textarea sits inside this relative wrapper so the ghost-text
     overlay can be absolutely positioned over the same area without
     affecting layout. */
  .composer-textarea-wrap {
    position: relative;
    display: flex;
  }
  .composer-textarea-wrap > .composer-textarea {
    flex: 1 1 auto;
    position: relative;
    z-index: 1;
    background: transparent;
  }
  /* Ghost suffix: absolutely positioned at the caret's measured pixel
     coordinates by _renderGhostOverlay (left/top/line-height inline).
     Pointer events pass through so clicks still focus the textarea. */
  .composer-ghost-suffix {
    position: absolute;
    pointer-events: none;
    z-index: 2;
    white-space: pre;
    font-family: inherit;
    font-size: 15px;
    color: var(--secondary-text-color);
    opacity: 0.5;
  }
  /* Inline chips showing resolved entity selections, rendered just above
     the typed text inside the composer box. */
  .composer-selections-inline {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .composer-selection-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 8px;
    font-size: 11px;
    border-radius: 10px;
    background: rgba(251, 191, 36, 0.12);
    color: var(--primary-text-color);
    border: 1px solid rgba(251, 191, 36, 0.25);
  }
  .composer-selection-chip ha-icon {
    --mdc-icon-size: 12px;
    color: rgba(251, 191, 36, 0.9);
  }
  .composer-selection-chip button {
    background: none;
    border: none;
    cursor: pointer;
    color: var(--secondary-text-color);
    padding: 0 2px;
    font-size: 13px;
    line-height: 1;
  }
  .composer-selection-chip button:hover {
    color: var(--primary-text-color);
  }
`;
