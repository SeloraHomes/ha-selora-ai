import { html } from "lit";
import { keyed } from "lit/directives/keyed.js";
import { renderMarkdown, stripAutomationBlock } from "../shared/markdown.js";
import { formatTime } from "../shared/date-utils.js";

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
  return html`
    <div class="chat-pane">
      <div class="chat-messages" id="chat-messages">
        ${host._messages.length === 0
          ? keyed(
              host._welcomeKey || 0,
              html`
                <div
                  class="empty-state welcome"
                  style="max-width:560px;margin:0 auto;padding:24px;"
                >
                  <div class="section-card" style="text-align:center;">
                    <img
                      src="/api/selora_ai/logo.png"
                      alt="Selora AI"
                      style="width:56px;height:56px;border-radius:12px;margin-bottom:12px;"
                    />
                    <div
                      style="font-size:20px;font-weight:700;margin-bottom:6px;"
                    >
                      Welcome to
                      <span class="gold-text">Selora AI</span>
                    </div>
                    <div class="section-card-subtitle">
                      Your intelligent home automation architect. I analyze your
                      devices, detect patterns, and help you build automations
                      using natural language.
                    </div>
                    <div
                      style="display:grid;grid-template-columns:1fr 1fr;gap:12px;text-align:left;margin-bottom:24px;"
                    >
                      <div
                        class="welcome-card"
                        style="background:var(--selora-inner-card-bg);border:1px solid var(--selora-inner-card-border);border-radius:12px;padding:14px;cursor:pointer;transition:border-color 0.2s;"
                        @click=${() =>
                          host._quickStart("Create an automation for my home")}
                      >
                        <div
                          style="display:flex;align-items:center;gap:8px;margin-bottom:6px;"
                        >
                          <ha-icon
                            icon="mdi:lightning-bolt"
                            style="--mdc-icon-size:18px;color:#fbbf24;"
                          ></ha-icon>
                          <div style="font-size:13px;font-weight:600;">
                            Create Automations
                          </div>
                        </div>
                        <div style="font-size:12px;opacity:0.6;">
                          Describe what you want in plain English
                        </div>
                      </div>
                      <div
                        class="welcome-card"
                        style="background:var(--selora-inner-card-bg);border:1px solid var(--selora-inner-card-border);border-radius:12px;padding:14px;cursor:pointer;transition:border-color 0.2s;"
                        @click=${() =>
                          host._quickStart(
                            "Analyze my device usage patterns and suggest automations",
                          )}
                      >
                        <div
                          style="display:flex;align-items:center;gap:8px;margin-bottom:6px;"
                        >
                          <ha-icon
                            icon="mdi:magnify-scan"
                            style="--mdc-icon-size:18px;color:#3b82f6;"
                          ></ha-icon>
                          <div style="font-size:13px;font-weight:600;">
                            Detect Patterns
                          </div>
                        </div>
                        <div style="font-size:12px;opacity:0.6;">
                          AI spots your routines and suggests automations
                        </div>
                      </div>
                      <div
                        class="welcome-card"
                        style="background:var(--selora-inner-card-bg);border:1px solid var(--selora-inner-card-border);border-radius:12px;padding:14px;cursor:pointer;transition:border-color 0.2s;"
                        @click=${() =>
                          host._quickStart(
                            "What devices do I have and how are they organized?",
                          )}
                      >
                        <div
                          style="display:flex;align-items:center;gap:8px;margin-bottom:6px;"
                        >
                          <ha-icon
                            icon="mdi:home-search-outline"
                            style="--mdc-icon-size:18px;color:#22c55e;"
                          ></ha-icon>
                          <div style="font-size:13px;font-weight:600;">
                            Manage Devices
                          </div>
                        </div>
                        <div style="font-size:12px;opacity:0.6;">
                          Discover, organize, and control your smart home
                        </div>
                      </div>
                      <div
                        class="welcome-card"
                        style="background:var(--selora-inner-card-bg);border:1px solid var(--selora-inner-card-border);border-radius:12px;padding:14px;cursor:pointer;transition:border-color 0.2s;"
                        @click=${() =>
                          host._quickStart("What can you help me with?")}
                      >
                        <div
                          style="display:flex;align-items:center;gap:8px;margin-bottom:6px;"
                        >
                          <ha-icon
                            icon="mdi:chat-question-outline"
                            style="--mdc-icon-size:18px;color:#a855f7;"
                          ></ha-icon>
                          <div style="font-size:13px;font-weight:600;">
                            Ask Anything
                          </div>
                        </div>
                        <div style="font-size:12px;opacity:0.6;">
                          Get answers about your home setup
                        </div>
                      </div>
                    </div>
                    <div
                      class="section-card-subtitle"
                      style="margin-bottom:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;opacity:0.4;"
                    >
                      Quick start
                    </div>
                    <div
                      style="display:flex;flex-direction:column;gap:8px;width:100%;"
                    >
                      <button
                        class="btn btn-outline"
                        style="width:100%;justify-content:flex-start;gap:8px;padding:12px 16px;font-size:13px;"
                        @click=${() =>
                          host._quickStart(
                            "Create an automation that turns off all lights at midnight",
                          )}
                      >
                        <ha-icon
                          icon="mdi:lightbulb-off-outline"
                          style="--mdc-icon-size:16px;"
                        ></ha-icon>
                        Turn off all lights at midnight
                      </button>
                      <button
                        class="btn btn-outline"
                        style="width:100%;justify-content:flex-start;gap:8px;padding:12px 16px;font-size:13px;"
                        @click=${() =>
                          host._quickStart(
                            "What devices do I have and which ones are currently on?",
                          )}
                      >
                        <ha-icon
                          icon="mdi:devices"
                          style="--mdc-icon-size:16px;"
                        ></ha-icon>
                        What devices do I have?
                      </button>
                      <button
                        class="btn btn-outline"
                        style="width:100%;justify-content:flex-start;gap:8px;padding:12px 16px;font-size:13px;"
                        @click=${() =>
                          host._quickStart(
                            "Suggest useful automations based on my devices and usage patterns",
                          )}
                      >
                        <ha-icon
                          icon="mdi:auto-fix"
                          style="--mdc-icon-size:16px;"
                        ></ha-icon>
                        Suggest automations for my home
                      </button>
                    </div>
                  </div>
                </div>
              `,
            )
          : host._messages.map((msg, idx) => renderMessage(host, msg, idx))}
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
        <div class="chat-input">
          <ha-textfield
            .value=${host._input}
            @input=${(e) => (host._input = e.target.value)}
            @keydown=${(e) =>
              e.key === "Enter" && !e.shiftKey && host._sendMessage()}
            placeholder="Describe an automation or ask a question…"
            ?disabled=${host._loading || host._streaming}
            style="flex:1;"
          ></ha-textfield>
          ${host._streaming
            ? html` <ha-icon-button
                @click=${() => host._stopStreaming()}
                title="Stop generating"
                style="color:#fbbf24;"
              >
                <ha-icon icon="mdi:stop-circle"></ha-icon>
              </ha-icon-button>`
            : html` <ha-icon-button
                @click=${() => host._sendMessage()}
                ?disabled=${host._loading || !host._input.trim()}
                title="Send"
              >
                <ha-icon icon="mdi:send"></ha-icon>
              </ha-icon-button>`}
        </div>
      </div>
    </div>
  `;
}

export function renderMessage(host, msg, idx) {
  const isUser = msg.role === "user";
  // Hide empty streaming messages (typing indicator shown separately)
  if (msg._streaming && !msg.content) return html``;

  // Strip automation JSON blocks from display, show spinner while generating
  let displayContent = msg.content;
  let showAutomationSpinner = false;
  if (!isUser) {
    const { text, isPartialBlock } = stripAutomationBlock(msg.content);
    displayContent = text;
    showAutomationSpinner = isPartialBlock && msg._streaming;
  }

  return html`
    <div class="message-row">
      ${isUser
        ? html`
            <div class="bubble user">
              <span class="msg-content" .innerHTML=${msg.content}></span>
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
              </div>
              <div
                class="bubble-meta"
                style="display:flex;justify-content:space-between;align-items:center;width:100%;"
              >
                <span>Selora AI · ${formatTime(msg.timestamp)}</span>
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
