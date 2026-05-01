// Chat and messaging actions (prototype-assigned to SeloraAIArchitectPanel)

export function _quickStart(message) {
  this._input = message;
  this._sendMessage();
}

export function _selectQuickAction(action) {
  const text = action.value || action.label;
  // Mark the originating message's actions as used
  for (const msg of this._messages) {
    if (msg.quick_actions && msg.quick_actions.includes(action)) {
      msg._qa_used = true;
      break;
    }
  }
  this._quickStart(text);
}

export async function _sendMessage() {
  if (!this._input.trim() || this._loading) return;
  const userMsg = this._input;
  this._messages = [...this._messages, { role: "user", content: userMsg }];
  this._input = "";
  this._loading = true;
  // Reset textarea height after clearing input
  const ta = this.shadowRoot?.querySelector(".composer-textarea");
  if (ta) ta.style.height = "auto";

  const assistantMsg = { role: "assistant", content: "", _streaming: true };
  this._messages = [...this._messages, assistantMsg];

  try {
    const subscribePayload = {
      type: "selora_ai/chat_stream",
      message: userMsg,
    };
    if (this._activeSessionId) {
      subscribePayload.session_id = this._activeSessionId;
    }

    this._streaming = true;
    this._streamUnsub = await this.hass.connection.subscribeMessage((event) => {
      if (event.type === "token") {
        assistantMsg.content += event.text;
        this._messages = [...this._messages];
        this._loading = false;
        this._requestScrollChat();
      } else if (event.type === "done") {
        assistantMsg.content = event.response || assistantMsg.content;
        assistantMsg.automation = event.automation || null;
        assistantMsg.automation_yaml = event.automation_yaml || null;
        assistantMsg.automation_status = event.automation ? "pending" : null;
        assistantMsg.automation_message_index =
          event.automation_message_index ?? null;
        assistantMsg.refining_automation_id =
          event.refining_automation_id || null;
        assistantMsg.devices = event.devices || null;
        assistantMsg.scene = event.scene || null;
        assistantMsg.scene_yaml = event.scene_yaml || null;
        assistantMsg.scene_status = event.scene_status || null;
        assistantMsg.scene_message_index = event.scene_message_index ?? null;
        assistantMsg.refine_scene_id = event.refine_scene_id || null;
        assistantMsg.quick_actions = event.quick_actions || null;
        assistantMsg._streaming = false;
        this._messages = [...this._messages];
        this._loading = false;
        this._streaming = false;
        this._streamUnsub = null;
        if (event.validation_error) {
          const label =
            event.validation_target === "scene" ? "Scene" : "Automation";
          this._showToast(
            `${label} validation failed: ${event.validation_error}`,
            "error",
          );
        }

        // Update session tracking
        if (event.session_id) {
          if (event.session_id !== this._activeSessionId) {
            this._activeSessionId = event.session_id;
          }
          this._loadSessions();
        }
      } else if (event.type === "error") {
        assistantMsg.content =
          "Sorry, I encountered an error: " + event.message;
        assistantMsg._streaming = false;
        this._messages = [...this._messages];
        this._loading = false;
        this._streaming = false;
        this._streamUnsub = null;
      }
    }, subscribePayload);
  } catch (err) {
    assistantMsg.content = "Sorry, I encountered an error: " + err.message;
    assistantMsg._streaming = false;
    this._messages = [...this._messages];
    this._loading = false;
    this._streaming = false;
    this._streamUnsub = null;
  }
}

export function _stopStreaming() {
  if (this._streamUnsub) {
    this._streamUnsub();
    this._streamUnsub = null;
  }
  this._streaming = false;
  this._loading = false;
  // Mark the last assistant message as done
  const lastMsg = this._messages[this._messages.length - 1];
  if (lastMsg && lastMsg._streaming) {
    lastMsg._streaming = false;
    this._messages = [...this._messages];
  }
}

export function _requestScrollChat() {
  if (!this._scrollPending) {
    this._scrollPending = true;
    requestAnimationFrame(() => {
      this._scrollPending = false;
      const container = this.shadowRoot.getElementById("chat-messages");
      if (container) container.scrollTop = container.scrollHeight;
    });
  }
}

export async function _copyMessageText(msg, btn) {
  try {
    const text = msg.content || "";
    await navigator.clipboard.writeText(text);
    btn.classList.add("copied");
    const icon = btn.querySelector("ha-icon");
    if (icon) icon.setAttribute("icon", "mdi:check");
    setTimeout(() => {
      btn.classList.remove("copied");
      if (icon) icon.setAttribute("icon", "mdi:content-copy");
    }, 1500);
  } catch (_) {
    /* clipboard not available */
  }
}
