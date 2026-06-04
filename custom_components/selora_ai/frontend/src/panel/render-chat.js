import { html } from "lit";
import { keyed } from "lit/directives/keyed.js";
import { renderMarkdown, stripAutomationBlock } from "../shared/markdown.js";
import { formatTime } from "../shared/date-utils.js";
import { renderDeviceDetail } from "./render-device-detail.js";
import { renderQuickActions } from "./quick-actions.js";
import { renderApprovalCard } from "./render-approval-card.js";
import {
  AUTOCOMPLETE_MIN_CHARS,
  buildSuggestionIndex,
  detectTrigger,
  rankSuggestions,
  findExactMatches,
  listByDomain,
  applySelection,
  pruneStaleSelections,
  stripEntityMarkers,
  findGhostSuggestion,
} from "./chat-autocomplete.js";

const AUTOCOMPLETE_KIND_LABELS = {
  device: "Devices",
  area: "Areas",
  scene: "Scenes",
  automation: "Automations",
};

function _formatReplyMs(ms) {
  if (ms < 1000) return `${ms} ms`;
  const seconds = ms / 1000;
  return seconds < 10 ? `${seconds.toFixed(1)} s` : `${Math.round(seconds)} s`;
}

function _formatToolArgs(args) {
  if (!args || typeof args !== "object" || !Object.keys(args).length) return "";
  const parts = [];
  for (const [k, v] of Object.entries(args)) {
    let val;
    if (v === null || v === undefined) {
      val = "null";
    } else if (typeof v === "string") {
      val =
        v.length > 60
          ? JSON.stringify(v.slice(0, 60) + "…")
          : JSON.stringify(v);
    } else if (typeof v === "object") {
      const json = JSON.stringify(v);
      val = json.length > 60 ? json.slice(0, 60) + "…" : json;
    } else {
      val = String(v);
    }
    parts.push(`${k}=${val}`);
  }
  return parts.join(", ");
}

function renderToolCalls(toolCalls) {
  return html`
    <details
      class="dev-tool-calls"
      style="margin-top:10px;border-radius:6px;background:rgba(255,255,255,0.03);border:1px solid var(--divider-color);font-family:var(--code-font-family,monospace);font-size:11px;"
    >
      <summary
        style="cursor:pointer;padding:6px 10px;color:var(--secondary-text-color);user-select:none;list-style:none;display:flex;align-items:center;gap:6px;"
      >
        <ha-icon
          icon="mdi:wrench-outline"
          style="--mdc-icon-size:14px;"
        ></ha-icon>
        <span>Tools used (${toolCalls.length})</span>
      </summary>
      <div
        style="padding:6px 10px 8px;border-top:1px solid var(--divider-color);color:var(--secondary-text-color);"
      >
        ${toolCalls.map(
          (tc, i) => html`
            <div
              style="padding:2px 0;${i > 0
                ? "border-top:1px dashed var(--divider-color);margin-top:4px;padding-top:6px;"
                : ""}"
            >
              <span style="color:var(--primary-text-color);font-weight:600;"
                >${tc.tool}</span
              >${tc.arguments && Object.keys(tc.arguments).length
                ? html`<span>(${_formatToolArgs(tc.arguments)})</span>`
                : html`<span>()</span>`}
            </div>
          `,
        )}
      </div>
    </details>
  `;
}

const WELCOME_SUGGESTIONS = [
  {
    label: "Turn off all lights at midnight",
    value: "Create an automation that turns off all lights at midnight",
    icon: "mdi:lightbulb-off-outline",
  },
  {
    label: "What devices do I have?",
    value: "What devices do I have and which ones are currently on?",
    icon: "mdi:devices",
  },
  {
    label: "Suggest automations for my home",
    value: "Suggest useful automations based on my devices and usage patterns",
    icon: "mdi:auto-fix",
  },
];

// "AI suggest" affordance shown under the composer in new-automation
// mode. Asks the LLM to invent an automation idea tailored to the
// user's home, drops it into the composer for review, and leaves the
// send button to the user — so they can tweak entities (with
// autocomplete) before committing.
function renderAutomationSuggestButton(host) {
  const busy = !!host._suggestingAutomation;
  return html`
    <button
      class="welcome-suggest-btn"
      ?disabled=${busy || host._loading || host._streaming}
      @click=${() => host._suggestAutomationIdea()}
    >
      ${busy
        ? html`<span class="spinner green"></span>`
        : html`<ha-icon
            icon="mdi:auto-fix"
            style="--mdc-icon-size:14px;"
          ></ha-icon>`}
      <span>${busy ? "Thinking…" : "Suggest one for me"}</span>
    </button>
  `;
}

export function renderChat(host) {
  const isEmpty = host._messages.length === 0;

  if (isEmpty) {
    return html`
      <div class="chat-pane">
        <div class="chat-welcome-center" id="chat-messages">
          ${keyed(
            host._welcomeKey || 0,
            html`
              <div class="welcome-center-content">
                <img
                  src="/api/selora_ai/logo.png"
                  alt="Selora AI"
                  style="width:72px;height:72px;border-radius:16px;margin-bottom:16px;"
                />
                <div style="font-size:26px;font-weight:700;margin-bottom:6px;">
                  ${host._newAutomationMode
                    ? html`New <span class="gold-text">Automation</span>`
                    : html`Welcome to <span class="gold-text">Selora AI</span>`}
                </div>
                <div
                  style="font-size:15px;color:var(--secondary-text-color);margin-bottom:0;"
                >
                  ${host._newAutomationMode
                    ? "Describe what you want to automate — mention the devices, times, or conditions involved."
                    : "Your intelligent home automation architect"}
                </div>

                ${host._llmNeedsSetup
                  ? html`
                      <div
                        style="margin-top:16px;padding:24px;border-radius:14px;background:rgba(251,191,36,0.06);border:1.5px solid rgba(251,191,36,0.25);cursor:pointer;transition:border-color 0.2s,background 0.2s;max-width:380px;"
                        @click=${() => host._goToSettings()}
                      >
                        <ha-icon
                          icon="mdi:rocket-launch-outline"
                          style="--mdc-icon-size:32px;color:#fbbf24;margin-bottom:12px;"
                        ></ha-icon>
                        <div
                          style="font-size:16px;font-weight:700;margin-bottom:6px;"
                        >
                          Get started
                        </div>
                        <div
                          style="font-size:13px;opacity:0.6;margin-bottom:16px;"
                        >
                          Configure your LLM provider in the Settings tab to
                          start chatting with your home.
                        </div>
                        <span
                          style="display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:600;color:#fbbf24;"
                        >
                          Open Settings
                          <ha-icon
                            icon="mdi:arrow-right"
                            style="--mdc-icon-size:16px;"
                          ></ha-icon>
                        </span>
                      </div>
                    `
                  : html`
                      <div class="welcome-composer-area">
                        <selora-particles
                          class="welcome-composer-particles"
                          .count=${260}
                          .color=${host._isDark
                            ? "#fbbf24"
                            : host._primaryColor || "#03a9f4"}
                          .maxOpacity=${host._isDark ? 0.55 : 0.5}
                        ></selora-particles>
                        ${_renderComposer(host, { welcome: true })}
                      </div>

                      ${host._newAutomationMode
                        ? renderAutomationSuggestButton(host)
                        : html`
                            <details class="welcome-quickstart">
                              <summary class="welcome-quickstart-summary">
                                <span>Quick start</span>
                                <ha-icon
                                  icon="mdi:chevron-down"
                                  class="welcome-quickstart-chevron"
                                ></ha-icon>
                              </summary>
                              ${renderQuickActions(host, WELCOME_SUGGESTIONS)}
                            </details>
                          `}
                    `}
              </div>
            `,
          )}
        </div>
      </div>
    `;
  }

  const lastMsg = host._messages[host._messages.length - 1];
  const lastQuickActions =
    lastMsg &&
    lastMsg.role !== "user" &&
    lastMsg.quick_actions &&
    lastMsg.quick_actions.length &&
    // Approval cards render their Allow/Deny row INLINE inside the
    // bubble so the user sees the buttons next to the proposal
    // they're approving. Without this guard the sticky composer row
    // below would render the same four buttons again — 8 buttons
    // on screen with no obvious link back to the card.
    !lastMsg.command_approval
      ? lastMsg
      : null;

  return html`
    <div class="chat-pane">
      <div
        class="chat-messages"
        id="chat-messages"
        @scroll=${host._onChatScroll}
      >
        ${host._messages.map((msg, idx) => renderMessage(host, msg, idx))}
        ${host._deviceDetail ? renderDeviceDetail(host) : ""}
        ${host._loading
          ? html`
              <div class="typing-bubble">
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
              </div>
            `
          : ""}
      </div>

      <div class="chat-input-wrapper">
        ${host._chatScrolledAway && host._messages.length > 0
          ? html`
              <button
                class="chat-jump-bottom"
                @click=${() => host._scrollChatToBottom()}
                title="Go to latest message"
                aria-label="Go to latest message"
              >
                <ha-icon icon="mdi:chevron-down"></ha-icon>
              </button>
            `
          : ""}
        ${lastQuickActions
          ? html`
              <div class="chat-quick-actions">
                ${renderQuickActions(host, lastQuickActions.quick_actions, {
                  used: !!lastQuickActions._qa_used,
                })}
              </div>
            `
          : ""}
        ${_renderComposer(host)}
      </div>
    </div>
  `;
}

function _autoResize(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = Math.min(textarea.scrollHeight, 200) + "px";
}

// Measure the pixel coordinates of the caret inside a textarea by mirroring
// the textarea's content in a hidden div with matching typography, then
// reading the offset of a marker span placed at the caret index. Returns
// coordinates relative to the textarea's bounding rect.
//
// This is the standard "div-mirror" technique — there is no DOM API that
// returns caret pixel position in a textarea. Returns { left, top, height }.
const _MIRROR_COPY_PROPS = [
  "boxSizing",
  "width",
  "height",
  "overflowX",
  "overflowY",
  "borderTopWidth",
  "borderRightWidth",
  "borderBottomWidth",
  "borderLeftWidth",
  "borderStyle",
  "paddingTop",
  "paddingRight",
  "paddingBottom",
  "paddingLeft",
  "fontStyle",
  "fontVariant",
  "fontWeight",
  "fontStretch",
  "fontSize",
  "fontSizeAdjust",
  "lineHeight",
  "fontFamily",
  "textAlign",
  "textTransform",
  "textIndent",
  "textDecoration",
  "letterSpacing",
  "wordSpacing",
  "tabSize",
  "MozTabSize",
  "whiteSpace",
  "wordWrap",
];
function _measureCaretInTextarea(textarea) {
  const value = textarea.value;
  const caret = textarea.selectionStart ?? value.length;
  const mirror = document.createElement("div");
  const style = mirror.style;
  const cs = window.getComputedStyle(textarea);
  for (const p of _MIRROR_COPY_PROPS) style[p] = cs[p];
  style.position = "absolute";
  style.visibility = "hidden";
  style.top = "0";
  style.left = "0";
  style.whiteSpace = "pre-wrap";
  style.wordWrap = "break-word";
  // Use a real text node + Range so the caret position is measured by
  // the layout engine itself — no "zero-width" span hack that some fonts
  // render with non-zero advance, no offsetTop/offsetLeft padding
  // semantics to reason about. getBoundingClientRect gives absolute
  // coords, which we convert back to coords relative to the wrap.
  const textNode = document.createTextNode(value.slice(0, caret) || " ");
  mirror.appendChild(textNode);
  textarea.parentNode.insertBefore(mirror, textarea);
  const range = document.createRange();
  // When slice is empty we inserted a space so the line exists; collapse
  // the range BEFORE that filler so it doesn't add width.
  const len = caret === 0 ? 0 : (value.slice(0, caret) || " ").length;
  range.setStart(textNode, len);
  range.setEnd(textNode, len);
  const rect = range.getBoundingClientRect();
  // Convert absolute coords to coords relative to the textarea's box,
  // then to coords relative to the .composer-textarea-wrap (which is the
  // ghost's positioning context — same as the textarea's box).
  const taRect = textarea.getBoundingClientRect();
  const left = rect.left - taRect.left - textarea.scrollLeft;
  const top = rect.top - taRect.top - textarea.scrollTop;
  const height =
    rect.height || parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.2;
  mirror.remove();
  return { left, top, height };
}

// Recompute the autocomplete dropdown state from the current textarea
// value + caret. Stores the result on `host._autocomplete` so the next
// render can show (or hide) the dropdown. The list is rebuilt fresh on
// every keystroke — at ~hundreds of entities that's cheaper than the
// bookkeeping needed to invalidate a cache.
function _updateAutocomplete(host, textarea) {
  const value = textarea.value;
  const caret = textarea.selectionStart ?? value.length;
  const trigger = detectTrigger(value, caret);
  const closeIfOpen = () => {
    if (host._autocomplete?.open) {
      host._autocomplete = {
        open: false,
        items: [],
        activeIndex: 0,
        trigger: null,
        anchor: null,
      };
    }
  };
  if (!trigger) {
    closeIfOpen();
    return;
  }
  const qLen = trigger.query.trim().length;
  // Kick the full-registry fetch on first use. HA assigns most areas at
  // the device level, so without devices[] the dropdown can't show the
  // area chip that disambiguates "Bed Light" across multiple bedrooms.
  // The first keystroke will render without devices (the fetch is
  // async); the next keystroke after it resolves will be enriched.
  if (!host._autocompleteRegCache && host._ensureFullRegistries) {
    host._autocompleteRegCache = "pending";
    host._ensureFullRegistries().then((reg) => {
      host._autocompleteRegCache = reg || null;
    });
  }
  const cache =
    host._autocompleteRegCache && host._autocompleteRegCache !== "pending"
      ? host._autocompleteRegCache
      : null;
  const index = buildSuggestionIndex(
    host.hass,
    cache?.areas || host.hass?.areas,
    cache?.devices || null,
  );
  // Three paths based on how much the user has narrowed things down:
  //   1. Verb constrains the domain ("unlock the …") — enumerate / rank
  //      against that small pool no matter how short the query is, so an
  //      empty query immediately lists every lock.
  //   2. No domain constraint, query >= MIN_CHARS — fuzzy rank.
  //   3. No domain constraint, short query — only exact-name matches
  //      (lets two-letter devices like "AC" appear without flooding the
  //      panel on every keystroke).
  let items;
  if (trigger.domains) {
    items = qLen
      ? rankSuggestions(
          index,
          trigger.kind,
          trigger.query,
          undefined,
          trigger.domains,
        )
      : listByDomain(index, trigger.kind, trigger.domains);
  } else if (qLen >= AUTOCOMPLETE_MIN_CHARS) {
    items = rankSuggestions(index, trigger.kind, trigger.query);
  } else {
    items = findExactMatches(index, trigger.kind, trigger.query);
  }
  // For generic device verbs / @ we also expose areas — "turn on the
  // bedroom" should be able to resolve to the Bedroom AREA, not just
  // devices that happen to contain "bedroom" in their name. Area picks
  // emit a [[areas:…]] marker so the LLM knows it's a bulk reference.
  if (trigger.includeAreas && qLen >= AUTOCOMPLETE_MIN_CHARS) {
    const areaMatches = rankSuggestions(
      index,
      "area",
      trigger.query,
      3, // cap area rows so devices still dominate the list
    );
    if (areaMatches.length) items = [...items, ...areaMatches];
  }
  if (!items.length) {
    closeIfOpen();
    return;
  }
  // Anchor the dropdown at the START of the user's partial query so the
  // suggestions line up with what they're typing — not at the very tip
  // of the caret (which would drift right as they type more characters).
  const savedCaret = textarea.selectionStart;
  textarea.selectionStart = textarea.selectionEnd = trigger.start;
  const anchor = _measureCaretInTextarea(textarea);
  textarea.selectionStart = textarea.selectionEnd = savedCaret;
  host._autocomplete = { open: true, items, activeIndex: 0, trigger, anchor };
}

// Refresh the ghost-text suggestion for the current textarea state.
// Anchored at the caret's pixel position so the gray suffix lines up
// exactly with where the user's text ends, regardless of wrapping.
function _updateGhost(host, textarea) {
  const value = textarea.value;
  const caret = textarea.selectionStart ?? value.length;
  const hit = findGhostSuggestion(value, caret);
  if (!hit) {
    host._ghost = null;
    return;
  }
  const anchor = _measureCaretInTextarea(textarea);
  host._ghost = { suffix: hit.suffix, anchor };
}

// Insert the ghost-suggestion suffix at the caret. Returns true if a
// suggestion was accepted, false otherwise — callers use this to decide
// whether to fall through to other key handlers (Tab focus, ArrowRight
// caret move).
function _acceptGhost(host, textarea) {
  if (!host._ghost?.suffix) return false;
  const value = textarea.value;
  const caret = textarea.selectionStart ?? value.length;
  const newText =
    value.slice(0, caret) + host._ghost.suffix + value.slice(caret);
  const newCaret = caret + host._ghost.suffix.length;
  host._input = newText;
  host._ghost = null;
  requestAnimationFrame(() => {
    textarea.value = newText;
    textarea.setSelectionRange(newCaret, newCaret);
    textarea.focus();
    _autoResize(textarea);
  });
  return true;
}

// Render the gray suffix that appears past the user's text. We position
// it absolutely at the caret's measured pixel coordinates (using the
// same mirror technique the dropdown uses) so it lands exactly where
// the textarea's caret sits — no font matching required, no risk of
// the overlay collapsing to zero size.
function _renderGhostOverlay(host) {
  const suffix = host._ghost?.suffix;
  if (!suffix) return "";
  const anchor = host._ghost.anchor;
  if (!anchor) return "";
  // anchor.top is already measured from the mirror's outer padding edge
  // (offsetTop counts the parent's padding-top), so it lines up with
  // where the textarea text actually appears — no extra padding offset
  // needed when we place the suffix inside .composer-textarea-wrap.
  return html`
    <span
      class="composer-ghost-suffix"
      aria-hidden="true"
      style="left:${anchor.left}px;top:${anchor.top}px;line-height:${anchor.height}px;"
      >${suffix}</span
    >
  `;
}

// After the active item index changes via keyboard nav, scroll it into
// the visible portion of the dropdown. We wait for Lit's render to
// settle (updateComplete) so the .active class has moved to the new
// row, then compute the exact scrollTop delta manually — scrollIntoView
// with block:"nearest" is too eager to scroll the wrong ancestor.
function _scrollActiveItemIntoView(host) {
  const scroll = () => {
    const list = host.shadowRoot?.querySelector(".composer-autocomplete");
    const active = list?.querySelector(".composer-autocomplete-item.active");
    if (!list || !active) return;
    const listRect = list.getBoundingClientRect();
    const itemRect = active.getBoundingClientRect();
    if (itemRect.bottom > listRect.bottom) {
      list.scrollTop += itemRect.bottom - listRect.bottom;
    } else if (itemRect.top < listRect.top) {
      list.scrollTop -= listRect.top - itemRect.top;
    }
  };
  if (host.updateComplete?.then) {
    host.updateComplete.then(scroll);
  } else {
    requestAnimationFrame(scroll);
  }
}

function _closeAutocomplete(host) {
  if (host._autocomplete?.open) {
    host._autocomplete = {
      open: false,
      items: [],
      activeIndex: 0,
      trigger: null,
    };
  }
}

function _selectAutocompleteItem(host, textarea, item) {
  const trigger = host._autocomplete?.trigger;
  if (!trigger || !item) return;
  const { text, caret } = applySelection(textarea.value, trigger, item);
  host._input = text;
  host._autocompleteSelections = [
    ...(host._autocompleteSelections || []),
    item,
  ];
  _closeAutocomplete(host);
  // Restore caret + resize on the same frame so the textarea visual stays
  // in sync with the new value LitElement is about to paint.
  requestAnimationFrame(() => {
    textarea.value = text;
    textarea.setSelectionRange(caret, caret);
    textarea.focus();
    _autoResize(textarea);
  });
}

function _removeSelection(host, idx) {
  const sels = host._autocompleteSelections || [];
  host._autocompleteSelections = sels.filter((_, i) => i !== idx);
}

function _renderAutocomplete(host) {
  const ac = host._autocomplete;
  if (!ac?.open || !ac.items?.length) return "";
  // The anchor coords are measured INSIDE the textarea; the composer box
  // adds its own padding around it. Adding the textarea's offset within
  // .composer-wrap is cheap and survives layout changes.
  const ta = host.shadowRoot?.querySelector(".composer-textarea");
  const wrap = host.shadowRoot?.querySelector(".composer-wrap");
  let leftPx = 0;
  if (ac.anchor && ta && wrap) {
    const taRect = ta.getBoundingClientRect();
    const wrapRect = wrap.getBoundingClientRect();
    leftPx = ac.anchor.left + (taRect.left - wrapRect.left);
    // Keep the dropdown inside the composer-wrap so it never overflows.
    const maxLeft = Math.max(0, wrapRect.width - 320);
    leftPx = Math.min(Math.max(0, leftPx), maxLeft);
  }
  const positionStyle = ac.anchor
    ? `left:${leftPx}px;right:auto;width:320px;max-width:calc(100% - 8px);`
    : "";
  // Group rows by kind so the dropdown shows two labelled sections
  // (Devices, then Areas) when both are present. We preserve the order
  // _updateAutocomplete produced inside each group so the primary kind
  // (matching the trigger verb) stays on top.
  const groupOrder = [];
  const groups = new Map();
  for (const item of ac.items) {
    if (!groups.has(item.kind)) {
      groups.set(item.kind, []);
      groupOrder.push(item.kind);
    }
    groups.get(item.kind).push(item);
  }
  return html`
    <div class="composer-autocomplete" role="listbox" style=${positionStyle}>
      ${groupOrder.map((kind) => {
        const header = AUTOCOMPLETE_KIND_LABELS[kind] || "Suggestions";
        return html`
          <div class="composer-autocomplete-header">
            <span>${header}</span>
          </div>
          ${groups
            .get(kind)
            .map((item) => _renderAutocompleteRow(host, ac, item))}
        `;
      })}
      <div class="composer-autocomplete-hint">
        ↑↓ navigate · ↵ insert · Esc dismiss
      </div>
    </div>
  `;
}

// Render a single suggestion row. Pulled out of _renderAutocomplete so
// the grouped-by-kind layout doesn't need to repeat the template.
function _renderAutocompleteRow(host, ac, item) {
  const idx = ac.items.indexOf(item);
  return html`<button
    type="button"
    class="composer-autocomplete-item ${idx === ac.activeIndex ? "active" : ""}"
    role="option"
    @mousedown=${(e) => {
      e.preventDefault();
      const ta = host.shadowRoot?.querySelector(".composer-textarea");
      if (ta) _selectAutocompleteItem(host, ta, item);
    }}
    @mouseenter=${() => {
      host._autocomplete = { ...ac, activeIndex: idx };
    }}
  >
    <ha-icon icon=${item.icon}></ha-icon>
    <span class="composer-autocomplete-label">${item.label}</span>
    ${item.area
      ? html`<span class="composer-autocomplete-area">${item.area}</span>`
      : ""}
  </button>`;
}

function _renderSelectionChips(host) {
  const sels = host._autocompleteSelections || [];
  if (!sels.length) return "";
  return html`
    <div class="composer-selections-inline">
      ${sels.map(
        (s, idx) => html`
          <span
            class="composer-selection-chip"
            title=${s.entity_id || s.area_id || ""}
          >
            <ha-icon icon=${s.icon}></ha-icon>
            ${s.label}
            <button
              type="button"
              title="Remove"
              @click=${() => _removeSelection(host, idx)}
            >
              ×
            </button>
          </span>
        `,
      )}
    </div>
  `;
}

function _renderComposer(host, opts = {}) {
  const welcome = !!opts.welcome;
  // The dropdown lives in `.composer-wrap` (a positioned ancestor that
  // doesn't clip) rather than inside `.composer-styled` (which has
  // overflow:hidden to contain the welcome-variant glow). Anchoring it
  // outside the clipping box is what lets the suggestions actually
  // appear above the input.
  return html`
    <div class="composer-wrap">
      ${_renderAutocomplete(host)}
      <div
        class="chat-input composer-styled ${welcome ? "composer-welcome" : ""}"
      >
        <div class="composer-input-col">
          <div class="composer-textarea-wrap">
            ${_renderGhostOverlay(host)}
            <textarea
              class="composer-textarea"
              .value=${host._input}
              @input=${(e) => {
                host._input = e.target.value;
                // Drop chips whose label no longer appears (user deleted the word).
                host._autocompleteSelections = pruneStaleSelections(
                  e.target.value,
                  host._autocompleteSelections || [],
                );
                _autoResize(e.target);
                _updateAutocomplete(host, e.target);
                _updateGhost(host, e.target);
              }}
              @click=${(e) => {
                _updateAutocomplete(host, e.target);
                _updateGhost(host, e.target);
              }}
              @keyup=${(e) => {
                // Arrow keys move the caret without firing @input, so recompute
                // the trigger on caret-only changes too.
                if (
                  e.key === "ArrowLeft" ||
                  e.key === "ArrowRight" ||
                  e.key === "Home" ||
                  e.key === "End"
                ) {
                  _updateAutocomplete(host, e.target);
                  _updateGhost(host, e.target);
                }
              }}
              @blur=${() => {
                // Delay the close so a click on a dropdown item (mousedown fires
                // first, blur after) can still resolve to a selection.
                setTimeout(() => _closeAutocomplete(host), 150);
              }}
              @keydown=${(e) => {
                const ac = host._autocomplete;
                if (ac?.open && ac.items.length) {
                  if (e.key === "ArrowDown") {
                    e.preventDefault();
                    host._autocomplete = {
                      ...ac,
                      activeIndex: (ac.activeIndex + 1) % ac.items.length,
                    };
                    _scrollActiveItemIntoView(host);
                    return;
                  }
                  if (e.key === "ArrowUp") {
                    e.preventDefault();
                    host._autocomplete = {
                      ...ac,
                      activeIndex:
                        (ac.activeIndex - 1 + ac.items.length) %
                        ac.items.length,
                    };
                    _scrollActiveItemIntoView(host);
                    return;
                  }
                  if (e.key === "Enter" || e.key === "Tab") {
                    e.preventDefault();
                    _selectAutocompleteItem(
                      host,
                      e.target,
                      ac.items[ac.activeIndex],
                    );
                    return;
                  }
                  if (e.key === "Escape") {
                    e.preventDefault();
                    _closeAutocomplete(host);
                    return;
                  }
                }
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  host._sendMessage();
                  return;
                }
                // Tab accepts a ghost suggestion if one is pending; otherwise
                // we just swallow it so focus stays in the textarea (Google's
                // address-bar behavior). Shift+Tab is preserved for
                // accessibility (moving back to the previous focusable element).
                if (e.key === "Tab" && !e.shiftKey) {
                  e.preventDefault();
                  _acceptGhost(host, e.target);
                  return;
                }
                // ArrowRight at the end of the input accepts a ghost
                // suggestion — only when the caret has nowhere else to go,
                // so editing earlier in the text isn't disrupted.
                if (
                  e.key === "ArrowRight" &&
                  host._ghost?.suffix &&
                  e.target.selectionStart === e.target.value.length &&
                  e.target.selectionEnd === e.target.value.length
                ) {
                  e.preventDefault();
                  _acceptGhost(host, e.target);
                  return;
                }
                // Shell-style history navigation. ArrowUp walks back
                // through prior user messages; ArrowDown walks forward
                // and finally restores whatever draft the user was
                // typing when they entered history. ``_historyIndex``
                // points into ``userHistory`` from newest=0; ``null``
                // means "not in history mode, ``_input`` is the live
                // draft". Only triggers when the caret is at the
                // start/end of the textarea so editing earlier in a
                // multi-line message still works.
                const userHistory = host._messages
                  .filter((m) => m.role === "user" && m.content)
                  .map((m) => stripEntityMarkers(m.content));
                const ta = e.target;
                const atStart =
                  ta.selectionStart === 0 && ta.selectionEnd === 0;
                const atEnd =
                  ta.selectionStart === ta.value.length &&
                  ta.selectionEnd === ta.value.length;
                const inHistory =
                  host._historyIndex !== null &&
                  host._historyIndex !== undefined;
                const applyHistory = (idx) => {
                  const recalled = userHistory[userHistory.length - 1 - idx];
                  host._historyIndex = idx;
                  host._input = recalled;
                  requestAnimationFrame(() => {
                    ta.value = recalled;
                    ta.setSelectionRange(ta.value.length, ta.value.length);
                    _autoResize(ta);
                  });
                };
                if (
                  e.key === "ArrowUp" &&
                  userHistory.length > 0 &&
                  (inHistory || atStart || !host._input)
                ) {
                  // First step into history saves the in-progress draft
                  // so ArrowDown past the newest entry can restore it.
                  if (!inHistory) {
                    host._historyDraft = host._input || "";
                    e.preventDefault();
                    applyHistory(0);
                    return;
                  }
                  if (host._historyIndex < userHistory.length - 1) {
                    e.preventDefault();
                    applyHistory(host._historyIndex + 1);
                    return;
                  }
                  // Already at oldest — swallow so caret doesn't move.
                  e.preventDefault();
                  return;
                }
                if (e.key === "ArrowDown" && inHistory && atEnd) {
                  e.preventDefault();
                  if (host._historyIndex > 0) {
                    applyHistory(host._historyIndex - 1);
                    return;
                  }
                  // Past the newest entry — restore the in-progress draft.
                  const draft = host._historyDraft || "";
                  host._historyIndex = null;
                  host._historyDraft = "";
                  host._input = draft;
                  requestAnimationFrame(() => {
                    ta.value = draft;
                    ta.setSelectionRange(ta.value.length, ta.value.length);
                    _autoResize(ta);
                  });
                  return;
                }
              }}
              placeholder=${host._newAutomationMode
                ? "Describe the automation you'd like to create…"
                : "Ask Selora AI anything…"}
              ?disabled=${host._loading || host._streaming}
              rows="1"
            ></textarea>
          </div>
          ${_renderSelectionChips(host)}
        </div>
        ${host._streaming
          ? html`<button
              class="composer-send"
              @click=${() => host._stopStreaming()}
              title="Stop generating"
            >
              <ha-icon icon="mdi:stop"></ha-icon>
            </button>`
          : html`<button
              class="composer-send"
              @click=${() => host._sendMessage()}
              ?disabled=${host._loading || !host._input.trim()}
              title="Send"
            >
              <ha-icon icon="mdi:arrow-up"></ha-icon>
            </button>`}
      </div>
    </div>
  `;
}

export function renderMessage(host, msg, idx) {
  const isUser = msg.role === "user";
  // Hide empty streaming messages (typing indicator shown separately)
  if (msg._streaming && !msg.content) return html``;

  // Strip automation/scene JSON blocks from display, show spinner while generating
  let displayContent = msg.content;
  let showAutomationSpinner = false;
  let showSceneSpinner = false;
  if (!isUser) {
    const { text, isPartialBlock, partialBlockType } = stripAutomationBlock(
      msg.content,
    );
    displayContent = text;
    showAutomationSpinner =
      isPartialBlock && msg._streaming && partialBlockType === "automation";
    showSceneSpinner =
      isPartialBlock && msg._streaming && partialBlockType === "scene";
    // Strip the trailing risk-level marker that older command_approval
    // bubbles carried in prose (e.g. "… before I run it [MEDIUM]."). The
    // card now surfaces the level on its own; leaving the marker in the
    // sentence repeats it and (when truncated like "[M…") looks broken.
    // Persisted messages may still contain the marker so this guard is
    // permanent, not just a migration.
    if (msg.command_approval) {
      displayContent = displayContent
        .replace(/\s*\[(?:LOW|MEDIUM|HIGH)\]\s*\.?$/i, "")
        .replace(/\s*\[(?:LOW|MEDIUM|HIGH)\]\s*/gi, " ")
        .trim();
    }
  }

  return html`
    <div class="message-row">
      ${isUser
        ? html`
            <div class="bubble user">
              <span
                class="msg-content"
                .textContent=${stripEntityMarkers(msg.content)}
              ></span>
            </div>
          `
        : html`
            <div
              class="assistant-wrap${msg.command_approval ||
              msg.automation ||
              msg.scene
                ? " assistant-wrap--approval"
                : ""}"
            >
              <div
                class="bubble assistant${msg.command_approval
                  ? " bubble--approval"
                  : ""}"
                style="max-width:100%;align-self:auto;"
              >
                ${msg.command_approval
                  ? ""
                  : html`<span
                      class="msg-content ${msg._streaming
                        ? "streaming-cursor"
                        : ""}"
                      .innerHTML=${renderMarkdown(displayContent)}
                    ></span>`}
                ${showAutomationSpinner
                  ? html`
                      <div
                        style="display:flex;align-items:center;gap:10px;margin-top:12px;padding:12px;border-radius:8px;background:rgba(251,191,36,0.08);border:1px solid rgba(251,191,36,0.15);"
                      >
                        <div
                          class="typing-dot"
                          style="animation:blink 1s infinite;width:8px;height:8px;border-radius:50%;background:#fbbf24;"
                        ></div>
                        <span
                          style="font-size:13px;font-weight:500;color:#fbbf24;"
                          >Building automation...</span
                        >
                      </div>
                    `
                  : ""}
                ${showSceneSpinner
                  ? html`
                      <div
                        style="display:flex;align-items:center;gap:10px;margin-top:12px;padding:12px;border-radius:8px;background:rgba(251,191,36,0.08);border:1px solid rgba(251,191,36,0.15);"
                      >
                        <div
                          class="typing-dot"
                          style="animation:blink 1s infinite;width:8px;height:8px;border-radius:50%;background:#fbbf24;"
                        ></div>
                        <span
                          style="font-size:13px;font-weight:500;color:#fbbf24;"
                          >Building scene...</span
                        >
                      </div>
                    `
                  : ""}
                ${msg.config_issue
                  ? html`
                      <div style="margin-top: 10px;">
                        <mwc-button dense raised @click=${host._goToSettings}
                          >Go to Settings</mwc-button
                        >
                      </div>
                    `
                  : ""}
                ${msg.automation ? host._renderProposalCard(msg, idx) : ""}
                ${msg.scene ? host._renderSceneCard(msg, idx) : ""}
                ${msg.command_approval
                  ? renderApprovalCard(
                      host,
                      msg,
                      msg.command_approval,
                      msg.approval_status,
                    )
                  : ""}
                ${msg._interrupted
                  ? html`
                      <div class="stream-interrupt">
                        <ha-icon
                          icon="mdi:alert-circle-outline"
                          style="--mdc-icon-size:16px;flex-shrink:0;"
                        ></ha-icon>
                        <span class="stream-interrupt-text"
                          >${msg._interruptReason ||
                          "Response was cut short."}</span
                        >
                      </div>
                    `
                  : ""}
                ${host._config?.developer_mode &&
                msg.tool_calls &&
                msg.tool_calls.length
                  ? renderToolCalls(msg.tool_calls)
                  : ""}
              </div>
              ${msg.automation ? host._renderProposalActions(msg, idx) : ""}
              ${msg.quick_actions &&
              msg.quick_actions.length &&
              // Standard quick_actions only render on the latest
              // message — once the conversation has moved on, an old
              // suggestion chip is just clutter. Approval cards are
              // the exception: a pending proposal must stay actionable
              // even if the user typed something else before clicking,
              // otherwise the only way to resolve it is to reload the
              // session.
              (idx === host._messages.length - 1 ||
                (msg.command_approval &&
                  msg.approval_status !== "approved" &&
                  msg.approval_status !== "denied" &&
                  msg.approval_status !== "resolving")) &&
              // Hide approval action cards once the proposal has been
              // resolved (or is mid-resolve). Re-clicking after the
              // status flipped would 404 server-side, and the approved
              // / denied chip already tells the user what happened.
              msg.approval_status !== "approved" &&
              msg.approval_status !== "denied" &&
              msg.approval_status !== "resolving"
                ? renderQuickActions(host, msg.quick_actions, {
                    used: !!msg._qa_used,
                  })
                : ""}
              <div
                class="bubble-meta"
                style="display:flex;justify-content:space-between;align-items:center;width:100%;"
              >
                <span>
                  Selora AI ·
                  ${host._config?.developer_mode &&
                  typeof msg._replyMs === "number"
                    ? _formatReplyMs(msg._replyMs)
                    : formatTime(msg.timestamp)}
                  ${msg._interrupted && msg._retryWith
                    ? html` ·
                        <button
                          class="stream-interrupt-retry"
                          @click=${() =>
                            host._retryMessage(msg._retryWith, msg)}
                        >
                          <ha-icon
                            icon="mdi:refresh"
                            style="--mdc-icon-size:12px;"
                          ></ha-icon>
                          Retry
                        </button>`
                    : ""}
                </span>
                <button
                  class="copy-msg-btn"
                  title="Copy message"
                  @click=${(e) => host._copyMessageText(msg, e.currentTarget)}
                >
                  <ha-icon
                    icon="mdi:content-copy"
                    style="--mdc-icon-size:12px;"
                  ></ha-icon>
                </button>
              </div>
            </div>
          `}
      ${isUser
        ? html` <div class="bubble-meta">
            You · ${formatTime(msg.timestamp)}
          </div>`
        : ""}
    </div>
  `;
}

// ── Device cards ──────────────────────────────────────────────────────

export const DOMAIN_ICONS = {
  light: "mdi:lightbulb",
  switch: "mdi:toggle-switch",
  climate: "mdi:thermostat",
  lock: "mdi:lock",
  cover: "mdi:window-shutter",
  fan: "mdi:fan",
  media_player: "mdi:speaker",
  vacuum: "mdi:robot-vacuum",
  sensor: "mdi:eye",
  binary_sensor: "mdi:motion-sensor",
  water_heater: "mdi:water-boiler",
  humidifier: "mdi:air-humidifier",
  camera: "mdi:cctv",
  device_tracker: "mdi:map-marker",
  person: "mdi:account",
  zone: "mdi:map-marker-radius",
  sun: "mdi:weather-sunny",
  weather: "mdi:weather-partly-cloudy",
  automation: "mdi:robot",
  scene: "mdi:palette",
  script: "mdi:script-text",
  input_boolean: "mdi:toggle-switch-variant",
  input_number: "mdi:numeric",
  input_select: "mdi:form-dropdown",
  input_text: "mdi:form-textbox",
  input_datetime: "mdi:calendar-clock",
  input_button: "mdi:gesture-tap-button",
  timer: "mdi:timer-outline",
  counter: "mdi:counter",
  group: "mdi:google-circles-communities",
  notify: "mdi:bell",
  alarm_control_panel: "mdi:shield-home",
  air_quality: "mdi:air-filter",
  remote: "mdi:remote",
};

export function _stateColor(state) {
  if (!state) return "var(--selora-zinc-400)";
  const s = state.toLowerCase();
  if (
    [
      "on",
      "home",
      "open",
      "unlocked",
      "playing",
      "heating",
      "cooling",
      "cleaning",
    ].includes(s)
  )
    return "var(--selora-accent)";
  if (
    [
      "off",
      "closed",
      "locked",
      "docked",
      "idle",
      "standby",
      "not_home",
      "paused",
    ].includes(s)
  )
    return "var(--selora-zinc-400)";
  if (["unavailable", "unknown", "error", "jammed"].includes(s))
    return "#ef4444";
  return "var(--selora-zinc-200)";
}

// renderYamlEditor renders the YAML view for an automation in chat or
// in the Automations tab. Options:
//   onSave: callback fired when the user clicks Save; if omitted, no
//     save button is rendered.
//   readOnly: if true, the editor is locked — useful for the saved
//     proposal card where typed edits had nowhere to go before and
//     looked like a silent data-loss bug. Read-only also hides the
//     "Unsaved changes" badge, since edits can't happen.
export function renderYamlEditor(
  host,
  key,
  originalYaml,
  onSave = null,
  opts = {},
) {
  const readOnly = !!opts.readOnly;
  host._initYamlEdit(key, originalYaml);
  const current = readOnly
    ? originalYaml
    : (host._editedYaml[key] ?? originalYaml);
  const isDirty = !readOnly && current !== originalYaml;
  const saving = !!host._savingYaml[key];
  return html`
    <ha-code-editor
      mode="yaml"
      .value=${current}
      ?read-only=${readOnly}
      @value-changed=${(e) => {
        if (readOnly) return;
        host._onYamlInput(key, e.detail.value);
      }}
      autocomplete-entities
      style="--code-mirror-font-size:12px;${readOnly ? "opacity:0.95;" : ""}"
    ></ha-code-editor>
    ${isDirty || (onSave && !readOnly)
      ? html`
          <div class="yaml-edit-bar">
            ${isDirty
              ? html`
                  <span class="yaml-unsaved">
                    <ha-icon
                      icon="mdi:circle-edit-outline"
                      style="--mdc-icon-size:13px;"
                    ></ha-icon>
                    Unsaved changes
                  </span>
                `
              : html`<span style="flex:1;"></span>`}
            ${onSave
              ? html`
                  <button
                    class="btn btn-primary"
                    ?disabled=${saving || !isDirty}
                    @click=${() => onSave(key)}
                  >
                    <ha-icon
                      icon="mdi:content-save"
                      style="--mdc-icon-size:13px;"
                    ></ha-icon>
                    ${saving ? "Saving…" : "Save changes"}
                  </button>
                `
              : ""}
          </div>
        `
      : ""}
  `;
}
