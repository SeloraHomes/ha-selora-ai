import { html } from "lit";
import { keyed } from "lit/directives/keyed.js";
import { renderMarkdown, stripAutomationBlock } from "../shared/markdown.js";
import { formatTime } from "../shared/date-utils.js";
import { renderDeviceDetail } from "./render-device-detail.js";
import { renderQuickActions } from "./quick-actions.js";

function _formatReplyMs(ms) {
  if (ms < 1000) return `${ms} ms`;
  const seconds = ms / 1000;
  return seconds < 10 ? `${seconds.toFixed(1)} s` : `${Math.round(seconds)} s`;
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

export function renderNewAutomationDialog(host) {
  if (!host._showNewAutoDialog) return "";
  return html`
    <div
      class="modal-overlay"
      @click=${() => {
        host._showNewAutoDialog = false;
      }}
    >
      <div
        class="modal-content"
        style="max-width:420px;"
        @click=${(e) => e.stopPropagation()}
      >
        <h3 style="margin:0 0 16px;">New Automation</h3>
        <label
          style="font-size:13px;font-weight:500;display:block;margin-bottom:6px;"
          >Automation name</label
        >
        <div style="display:flex;gap:8px;align-items:center;">
          <input
            type="text"
            placeholder="e.g. Turn off lights at midnight"
            style="flex:1;padding:10px 12px;border:1px solid var(--divider-color);border-radius:8px;font-size:14px;background:var(--card-background-color);color:var(--primary-text-color);box-sizing:border-box;"
            .value=${host._newAutoName}
            @input=${(e) => {
              host._newAutoName = e.target.value;
            }}
            @keydown=${(e) => {
              if (e.key === "Enter") host._newAutomationChat(host._newAutoName);
            }}
          />
          <button
            class="btn btn-outline"
            style="padding:8px 10px;flex-shrink:0;"
            title="AI Suggest"
            ?disabled=${host._suggestingName}
            @click=${() => host._suggestAutomationName()}
          >
            ${host._suggestingName
              ? html`<span class="spinner green"></span>`
              : html`<ha-icon
                  icon="mdi:auto-fix"
                  style="--mdc-icon-size:18px;"
                ></ha-icon>`}
          </button>
        </div>
        ${host._suggestingName
          ? html`<div
              style="font-size:12px;color:var(--secondary-text-color);margin-top:6px;"
            >
              Asking AI for a suggestion…
            </div>`
          : ""}
        <div
          style="display:flex;justify-content:flex-end;gap:8px;margin-top:16px;"
        >
          <button
            class="btn btn-outline"
            @click=${() => {
              host._showNewAutoDialog = false;
            }}
          >
            Cancel
          </button>
          <button
            class="btn btn-primary"
            ?disabled=${!host._newAutoName?.trim()}
            @click=${() => host._newAutomationChat(host._newAutoName)}
          >
            <ha-icon
              icon="mdi:chat-processing-outline"
              style="--mdc-icon-size:14px;"
            ></ha-icon>
            Create in Chat
          </button>
        </div>
      </div>
    </div>
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
                  Welcome to
                  <span class="gold-text">Selora AI</span>
                </div>
                <div
                  style="font-size:15px;color:var(--secondary-text-color);margin-bottom:0;"
                >
                  Your intelligent home automation architect
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
              </div>
            `,
          )}
        </div>
      </div>
    `;
  }

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
        ${_renderComposer(host)}
      </div>
    </div>
  `;
}

function _autoResize(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = Math.min(textarea.scrollHeight, 200) + "px";
}

function _renderComposer(host, opts = {}) {
  const welcome = !!opts.welcome;
  return html`
    <div
      class="chat-input composer-styled ${welcome ? "composer-welcome" : ""}"
    >
      <textarea
        class="composer-textarea"
        .value=${host._input}
        @input=${(e) => {
          host._input = e.target.value;
          _autoResize(e.target);
        }}
        @keydown=${(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            host._sendMessage();
            return;
          }
          if (e.key === "ArrowUp" && !host._input) {
            const lastUser = [...host._messages]
              .reverse()
              .find((m) => m.role === "user" && m.content);
            if (lastUser) {
              e.preventDefault();
              host._input = lastUser.content;
              const ta = e.target;
              requestAnimationFrame(() => {
                ta.value = lastUser.content;
                ta.setSelectionRange(ta.value.length, ta.value.length);
                _autoResize(ta);
              });
            }
          }
        }}
        placeholder="Ask Selora AI anything…"
        ?disabled=${host._loading || host._streaming}
        rows="1"
      ></textarea>
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
  }

  return html`
    <div class="message-row">
      ${isUser
        ? html`
            <div class="bubble user">
              <span class="msg-content" .textContent=${msg.content}></span>
            </div>
          `
        : html`
            <div
              style="display:inline-flex;flex-direction:column;max-width:82%;align-self:flex-start;"
            >
              <div
                class="bubble assistant"
                style="max-width:100%;align-self:auto;"
              >
                <span
                  class="msg-content ${msg._streaming
                    ? "streaming-cursor"
                    : ""}"
                  .innerHTML=${renderMarkdown(displayContent)}
                ></span>
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
              </div>
              ${msg.quick_actions &&
              msg.quick_actions.length &&
              idx === host._messages.length - 1
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
                          @click=${() => host._retryMessage(msg._retryWith)}
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

export function renderYamlEditor(host, key, originalYaml, onSave = null) {
  host._initYamlEdit(key, originalYaml);
  const current = host._editedYaml[key] ?? originalYaml;
  const isDirty = current !== originalYaml;
  const saving = !!host._savingYaml[key];
  return html`
    <ha-code-editor
      mode="yaml"
      .value=${current}
      @value-changed=${(e) => {
        host._onYamlInput(key, e.detail.value);
      }}
      autocomplete-entities
      style="--code-mirror-font-size:12px;"
    ></ha-code-editor>
    ${isDirty || onSave
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
