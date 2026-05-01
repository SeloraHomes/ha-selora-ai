// Automation CRUD actions (prototype-assigned to SeloraAIArchitectPanel)

export function _getRefiningAutomationId(msgIndex = null) {
  const msg = msgIndex == null ? null : this._messages[msgIndex];
  if (msg?.refining_automation_id) return msg.refining_automation_id;
  if (msg?.automation_id) return msg.automation_id;
  if (msg?.automation?.id) return msg.automation.id;

  for (const m of this._messages) {
    if (m.automation_status === "refining") {
      if (m.automation_id) return m.automation_id;
      if (m.automation?.id) return m.automation.id;
    }
  }
  return null;
}

export async function _loadLineage(automationId) {
  this._loadingLineage = { ...this._loadingLineage, [automationId]: true };
  this.requestUpdate();
  try {
    const result = await this.hass.callWS({
      type: "selora_ai/get_automation_lineage",
      automation_id: automationId,
    });
    this._lineage = { ...this._lineage, [automationId]: result };
  } catch (err) {
    console.error("Failed to load lineage", err);
    this._lineage = { ...this._lineage, [automationId]: [] };
  } finally {
    this._loadingLineage = { ...this._loadingLineage, [automationId]: false };
    this.requestUpdate();
  }
}

export async function _acceptAutomation(msgIndex, automation) {
  try {
    const refiningId = this._getRefiningAutomationId(msgIndex);
    if (refiningId) {
      const yamlText = this._messages[msgIndex]?.automation_yaml || "";
      if (yamlText) {
        await this.hass.callWS({
          type: "selora_ai/update_automation_yaml",
          automation_id: refiningId,
          yaml_text: yamlText,
          session_id: this._activeSessionId,
          version_message: "Refined via chat",
        });
      } else {
        await this.hass.callWS({
          type: "selora_ai/create_automation",
          automation: automation,
          session_id: this._activeSessionId,
        });
      }
    } else {
      await this.hass.callWS({
        type: "selora_ai/create_automation",
        automation: automation,
        session_id: this._activeSessionId,
      });
    }
    await this.hass.callWS({
      type: "selora_ai/set_automation_status",
      session_id: this._activeSessionId,
      message_index: msgIndex,
      status: "saved",
    });
    const session = await this.hass.callWS({
      type: "selora_ai/get_session",
      session_id: this._activeSessionId,
    });
    this._messages = session.messages || [];
    await this._removeDraftForSession(this._activeSessionId);
    await this._loadAutomations();

    this._showToast(
      `Automation "${automation.alias}" ${refiningId ? "updated" : "created and enabled"}.`,
      "success",
    );
    this._activeTab = "automations";
  } catch (err) {
    this._showToast("Failed to save automation: " + err.message, "error");
  }
}

export async function _removeDraftForSession(sessionId) {
  if (!sessionId) return;
  try {
    const draft = this._automations.find(
      (a) => a._draft && a._linked_session === sessionId,
    );
    if (draft && draft._draft_id) {
      await this.hass.callWS({
        type: "selora_ai/remove_draft",
        draft_id: draft._draft_id,
      });
    }
  } catch (err) {
    console.error("Failed to remove draft for session", err);
  }
}

export async function _dismissDraft(draftId) {
  if (!draftId) return;
  try {
    await this.hass.callWS({
      type: "selora_ai/remove_draft",
      draft_id: draftId,
    });
    await this._loadAutomations();
    this._showToast("Draft dismissed.", "info");
  } catch (err) {
    console.error("Failed to dismiss draft", err);
    this._showToast("Failed to dismiss draft: " + err.message, "error");
  }
}

export async function _declineAutomation(msgIndex) {
  try {
    await this.hass.callWS({
      type: "selora_ai/set_automation_status",
      session_id: this._activeSessionId,
      message_index: msgIndex,
      status: "declined",
    });
    const session = await this.hass.callWS({
      type: "selora_ai/get_session",
      session_id: this._activeSessionId,
    });
    this._messages = session.messages || [];
  } catch (err) {
    console.error("Failed to decline automation", err);
  }
}

export async function _refineAutomation(msgIndex, automation, description) {
  // Mark the original proposal as "refining" so the card shows it's superseded
  try {
    await this.hass.callWS({
      type: "selora_ai/set_automation_status",
      session_id: this._activeSessionId,
      message_index: msgIndex,
      status: "refining",
    });
    const session = await this.hass.callWS({
      type: "selora_ai/get_session",
      session_id: this._activeSessionId,
    });
    this._messages = session.messages || [];
  } catch (err) {
    console.error("Failed to mark automation as refining", err);
  }

  // Pre-fill with rich context so the user just needs to describe the change
  const ctx = description ? ` (${description})` : "";
  this._input = `Refine "${automation.alias}"${ctx}: `;
  this.shadowRoot.querySelector(".composer-textarea")?.focus();
}

export async function _createAutomationFromSuggestion(automation) {
  try {
    await this.hass.callWS({
      type: "selora_ai/create_automation",
      automation,
    });
    await this._loadAutomations();
    this._showToast(`Automation "${automation.alias}" created.`, "success");
  } catch (err) {
    this._showToast("Failed to create automation: " + err.message, "error");
  }
}

export function _discardSuggestion(suggestion) {
  this._suggestions = this._suggestions.filter((s) => s !== suggestion);
}

// Accept automation — if the user edited the YAML, send the edited version
export async function _acceptAutomationWithEdits(
  msgIndex,
  automation,
  yamlKey,
) {
  const edited = this._editedYaml[yamlKey];
  const msg = this._messages[msgIndex] || {};
  const originalYaml = msg.automation_yaml || "";
  const refiningId = this._getRefiningAutomationId(msgIndex);

  if (edited && edited !== (this._originalYaml?.[yamlKey] ?? originalYaml)) {
    try {
      this._savingYaml = { ...this._savingYaml, [yamlKey]: true };
      this.requestUpdate();

      if (refiningId) {
        await this.hass.callWS({
          type: "selora_ai/update_automation_yaml",
          automation_id: refiningId,
          yaml_text: edited,
          session_id: this._activeSessionId,
          version_message: "Refined via chat (with edits)",
        });
      } else {
        await this.hass.callWS({
          type: "selora_ai/apply_automation_yaml",
          yaml_text: edited,
          session_id: this._activeSessionId,
        });
      }

      await this.hass.callWS({
        type: "selora_ai/set_automation_status",
        session_id: this._activeSessionId,
        message_index: msgIndex,
        status: "saved",
      });
      const session = await this.hass.callWS({
        type: "selora_ai/get_session",
        session_id: this._activeSessionId,
      });
      this._messages = session.messages || [];
      await this._loadAutomations();

      this._showToast(
        `Automation "${automation.alias}" ${refiningId ? "updated" : "created and enabled"}.`,
        "success",
      );
      this._activeTab = "automations";
    } catch (err) {
      this._showToast(
        "Failed to save automation from edited YAML: " + err.message,
        "error",
      );
    } finally {
      this._savingYaml = { ...this._savingYaml, [yamlKey]: false };
      this.requestUpdate();
    }
  } else {
    await this._acceptAutomation(msgIndex, automation);
  }
}

export async function _createSuggestionWithEdits(auto, yamlKey, originalYaml) {
  const edited = this._editedYaml[yamlKey];
  try {
    this._savingYaml = { ...this._savingYaml, [yamlKey]: true };
    this.requestUpdate();
    if (edited && edited !== originalYaml) {
      await this.hass.callWS({
        type: "selora_ai/apply_automation_yaml",
        yaml_text: edited,
      });
    } else {
      await this.hass.callWS({
        type: "selora_ai/create_automation",
        automation: auto,
      });
    }
    this._fadingOutSuggestions = {
      ...this._fadingOutSuggestions,
      [yamlKey]: true,
    };
    await this._loadAutomations();
    this._showToast(`Automation "${auto.alias}" created.`, "success");
    // Wait for fade-out, then remove and scroll
    await new Promise((r) => setTimeout(r, 650));
    this._suggestions = this._suggestions.filter((s) => {
      const a = s.automation || s.automation_data;
      return `sug_${a?.alias}` !== yamlKey;
    });
    this._fadingOutSuggestions = {
      ...this._fadingOutSuggestions,
      [yamlKey]: false,
    };
    this._highlightAndScrollToNew();
  } catch (err) {
    this._showToast("Failed to create automation: " + err.message, "error");
  } finally {
    this._savingYaml = { ...this._savingYaml, [yamlKey]: false };
    this.requestUpdate();
  }
}

export async function _saveActiveAutomationYaml(automationId, yamlKey) {
  const edited = this._editedYaml[yamlKey];
  if (!edited) return;
  try {
    this._savingYaml = { ...this._savingYaml, [yamlKey]: true };
    this.requestUpdate();
    await this.hass.callWS({
      type: "selora_ai/update_automation_yaml",
      automation_id: automationId,
      yaml_text: edited,
    });
    // Clear edits and refresh
    this._editedYaml = { ...this._editedYaml, [yamlKey]: undefined };
    await this._loadAutomations();
    this._showToast("Automation YAML saved.", "success");
  } catch (err) {
    this._showToast("Failed to save changes: " + err.message, "error");
  } finally {
    this._savingYaml = { ...this._savingYaml, [yamlKey]: false };
    this.requestUpdate();
  }
}

export function _initYamlEdit(key, originalYaml) {
  if (this._editedYaml[key] === undefined) {
    this._editedYaml = { ...this._editedYaml, [key]: originalYaml };
  }
}

export function _onYamlInput(key, value) {
  this._editedYaml = { ...this._editedYaml, [key]: value };
  this.requestUpdate();
}
