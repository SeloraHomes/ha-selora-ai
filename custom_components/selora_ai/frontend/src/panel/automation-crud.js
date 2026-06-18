// Automation CRUD actions (prototype-assigned to SeloraAIArchitectPanel)

// Build the post-create toast. Used by the Suggestions flow (and any
// future non-chat creation path) — the chat accept/save flow has its
// own inline workflow row and no longer fires a toast. Every new
// automation is written disabled per project policy; the toast
// points the user at the Automations tab toggle, which is the
// universal control across every creation entry point (the inline
// chat "Enable" button only exists when the proposal lives in a
// chat bubble).
export function _createdToast(alias, result) {
  if (result && result.risk_level === "elevated") {
    return {
      message:
        `Automation "${alias}" created (DISABLED) — uses elevated-risk ` +
        "actions (shell_command, python_script, webhook, etc.). Review " +
        "carefully before enabling it from the Automations tab.",
      type: "warning",
    };
  }
  return {
    message:
      `Automation "${alias}" created (disabled) — enable it from the ` +
      "Automations tab when you're ready.",
    type: "info",
  };
}

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
    const msg = this._messages[msgIndex] || {};
    // The backend session can be ahead of (or shifted vs.) the local
    // _messages array — streaming placeholders, retries, and session
    // pruning all desync indices. Prefer the canonical index the chat
    // handler stamped on the message, fall back only if absent.
    const backendIndex = msg.automation_message_index ?? msgIndex;
    const refiningId = this._getRefiningAutomationId(msgIndex);
    let createResult = null;
    let resolvedAutomationId = refiningId || null;
    if (refiningId) {
      const yamlText = msg.automation_yaml || "";
      if (yamlText) {
        await this.hass.callWS({
          type: "selora_ai/update_automation_yaml",
          automation_id: refiningId,
          yaml_text: yamlText,
          session_id: this._activeSessionId,
          version_message: "Refined via chat",
        });
      } else {
        createResult = await this.hass.callWS({
          type: "selora_ai/create_automation",
          automation: automation,
          session_id: this._activeSessionId,
        });
        resolvedAutomationId = createResult?.automation_id || null;
      }
    } else {
      createResult = await this.hass.callWS({
        type: "selora_ai/create_automation",
        automation: automation,
        session_id: this._activeSessionId,
      });
      resolvedAutomationId = createResult?.automation_id || null;
    }
    await this.hass.callWS({
      type: "selora_ai/set_automation_status",
      session_id: this._activeSessionId,
      message_index: backendIndex,
      status: "saved",
      ...(resolvedAutomationId ? { automation_id: resolvedAutomationId } : {}),
    });
    const session = await this.hass.callWS({
      type: "selora_ai/get_session",
      session_id: this._activeSessionId,
    });
    this._messages = session.messages || [];
    await this._removeDraftForSession(this._activeSessionId);
    await this._loadAutomations();

    // Refinements preserve the existing automation's enabled state;
    // only auto-enable when this turn actually created a new one.
    if (createResult) {
      await this._autoEnableAfterAccept(
        resolvedAutomationId,
        createResult,
        msg,
      );
    }
  } catch (err) {
    this._showToast(
      this._t("automation_crud_save_failed", "Failed to save automation:") +
        " " +
        err.message,
      "error",
    );
  }
}

// Shared between _acceptAutomation and _acceptAutomationWithEdits.
// After the create+save round-trip lands, flip the new automation on
// automatically — the user's Accept click was the review gate, so a
// second "Enable automation" step is friction we explicitly removed.
// Only elevated-risk automations stay disabled (the backend
// async_create_automation forces initial_state=False for those, so
// we just skip the toggle).
//
// The optimistic state patch is the important bit: HA's state machine
// can lag the `automation.turn_on` service call by a few hundred ms,
// so a fresh _loadAutomations after the toggle sometimes still
// returns state:"off" for the brand-new entity. That left the saved
// card rendering the green "Enable automation" CTA — exactly the
// extra step we promised to remove. We patch state:"on" locally as
// soon as the WS toggle resolves, then let the follow-up
// _loadAutomations reconcile when HA catches up.
export async function _autoEnableAfterAccept(automationId, createResult, msg) {
  if (!automationId) return;
  const elevated =
    (createResult && createResult.risk_level === "elevated") ||
    msg?.risk_assessment?.level === "elevated";
  if (elevated) return;

  const created = (this._automations || []).find(
    (a) => a.automation_id === automationId,
  );
  if (!created?.entity_id) {
    // Race: the create's automation.reload hasn't surfaced the new
    // entity yet. One short retry usually resolves it (HA's reload
    // is blocking server-side, but the WS round-trip for the
    // following _loadAutomations can land microseconds before HA
    // updates the entity registry view).
    await new Promise((r) => setTimeout(r, 250));
    await this._loadAutomations();
  }
  const target = (this._automations || []).find(
    (a) => a.automation_id === automationId,
  );
  if (!target?.entity_id) {
    console.warn("Auto-enable: couldn't resolve entity_id for", automationId);
    this._showToast(
      this._t(
        "automation_crud_entity_not_surfaced",
        "Automation saved, but Home Assistant hasn't surfaced the entity yet — toggle it on from the Automations tab once it appears.",
      ),
      "warning",
    );
    return;
  }
  // Patch state="on" before awaiting the toggle so the saved card
  // doesn't flash its "Enable automation" CTA during the round-trip.
  // We don't refetch after the toggle either: HA's state machine
  // lags the turn_on service and would race in with state="off",
  // clobbering the patch.
  this._automations = (this._automations || []).map((a) =>
    a.automation_id === automationId ? { ...a, state: "on" } : a,
  );
  this.requestUpdate();
  try {
    await this.hass.callWS({
      type: "selora_ai/toggle_automation",
      automation_id: automationId,
      entity_id: target.entity_id,
      enabled: true,
    });
  } catch (err) {
    this._automations = (this._automations || []).map((a) =>
      a.automation_id === automationId ? { ...a, state: "off" } : a,
    );
    this.requestUpdate();
    console.error("Failed to auto-enable new automation", err);
    this._showToast(
      this._t(
        "automation_crud_auto_enable_failed_prefix",
        "Automation saved but couldn't be enabled automatically:",
      ) +
        " " +
        (err?.message ||
          this._t("automation_crud_unknown_error", "unknown error")) +
        this._t(
          "automation_crud_auto_enable_failed_suffix",
          ". Use the Enable button on the card to try again.",
        ),
      "warning",
    );
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
    this._showToast(
      this._t("automation_crud_draft_dismissed", "Draft dismissed."),
      "info",
    );
  } catch (err) {
    console.error("Failed to dismiss draft", err);
    this._showToast(
      this._t(
        "automation_crud_dismiss_draft_failed",
        "Failed to dismiss draft:",
      ) +
        " " +
        err.message,
      "error",
    );
  }
}

export async function _declineAutomation(msgIndex) {
  try {
    const msg = this._messages[msgIndex] || {};
    const backendIndex = msg.automation_message_index ?? msgIndex;
    await this.hass.callWS({
      type: "selora_ai/set_automation_status",
      session_id: this._activeSessionId,
      message_index: backendIndex,
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
    const msg = this._messages[msgIndex] || {};
    const backendIndex = msg.automation_message_index ?? msgIndex;
    await this.hass.callWS({
      type: "selora_ai/set_automation_status",
      session_id: this._activeSessionId,
      message_index: backendIndex,
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
    const result = await this.hass.callWS({
      type: "selora_ai/create_automation",
      automation,
    });
    await this._loadAutomations();
    const toast = _createdToast(automation.alias, result);
    this._showToast(toast.message, toast.type);
  } catch (err) {
    this._showToast(
      this._t("automation_crud_create_failed", "Failed to create automation:") +
        " " +
        err.message,
      "error",
    );
  }
}

export function _discardSuggestion(suggestion) {
  this._suggestions = this._suggestions.filter((s) => s !== suggestion);
}

// Duration of the Accept-button exit animation in ms. Mirrored by the
// CSS keyframes in chat.css.js — if you change one, change the other,
// or the button will either jump (anim shorter than wait) or leave a
// blank stub frame in the DOM (anim longer than wait).
const ACCEPT_ANIM_MS = 240;

// Accept automation — if the user edited the YAML, send the edited version
export async function _acceptAutomationWithEdits(
  msgIndex,
  automation,
  yamlKey,
) {
  // Kick off the button's exit animation BEFORE any WS work, so the
  // user sees instant feedback when they click. The actual save fires
  // after the animation completes (still well under a second total),
  // and the saved chat card mounts with its own enter animation —
  // together that swap reads as a smooth transition instead of an
  // abrupt UI replacement.
  this._acceptAnimating = { ...this._acceptAnimating, [msgIndex]: true };
  this.requestUpdate();
  await new Promise((r) => setTimeout(r, ACCEPT_ANIM_MS));

  const edited = this._editedYaml[yamlKey];
  const msg = this._messages[msgIndex] || {};
  const originalYaml = msg.automation_yaml || "";
  const refiningId = this._getRefiningAutomationId(msgIndex);
  // See note in _acceptAutomation — use the canonical backend index.
  const backendIndex = msg.automation_message_index ?? msgIndex;

  if (edited && edited !== (this._originalYaml?.[yamlKey] ?? originalYaml)) {
    try {
      this._savingYaml = { ...this._savingYaml, [yamlKey]: true };
      this.requestUpdate();

      let createResult = null;
      let resolvedAutomationId = refiningId || null;
      if (refiningId) {
        await this.hass.callWS({
          type: "selora_ai/update_automation_yaml",
          automation_id: refiningId,
          yaml_text: edited,
          session_id: this._activeSessionId,
          version_message: "Refined via chat (with edits)",
        });
      } else {
        createResult = await this.hass.callWS({
          type: "selora_ai/apply_automation_yaml",
          yaml_text: edited,
          session_id: this._activeSessionId,
        });
        resolvedAutomationId = createResult?.automation_id || null;
      }

      await this.hass.callWS({
        type: "selora_ai/set_automation_status",
        session_id: this._activeSessionId,
        message_index: backendIndex,
        status: "saved",
        ...(resolvedAutomationId
          ? { automation_id: resolvedAutomationId }
          : {}),
      });
      const session = await this.hass.callWS({
        type: "selora_ai/get_session",
        session_id: this._activeSessionId,
      });
      this._messages = session.messages || [];
      await this._loadAutomations();
      if (createResult) {
        await this._autoEnableAfterAccept(
          resolvedAutomationId,
          createResult,
          msg,
        );
      }
    } catch (err) {
      this._showToast(
        this._t(
          "automation_crud_save_edited_yaml_failed",
          "Failed to save automation from edited YAML:",
        ) +
          " " +
          err.message,
        "error",
      );
    } finally {
      this._savingYaml = { ...this._savingYaml, [yamlKey]: false };
      this._acceptAnimating = {
        ...this._acceptAnimating,
        [msgIndex]: false,
      };
      this.requestUpdate();
    }
  } else {
    await this._acceptAutomation(msgIndex, automation);
    this._acceptAnimating = {
      ...this._acceptAnimating,
      [msgIndex]: false,
    };
    this.requestUpdate();
  }
}

export async function _createSuggestionWithEdits(auto, yamlKey, originalYaml) {
  const edited = this._editedYaml[yamlKey];
  try {
    this._savingYaml = { ...this._savingYaml, [yamlKey]: true };
    this.requestUpdate();
    let createResult;
    if (edited && edited !== originalYaml) {
      createResult = await this.hass.callWS({
        type: "selora_ai/apply_automation_yaml",
        yaml_text: edited,
      });
    } else {
      createResult = await this.hass.callWS({
        type: "selora_ai/create_automation",
        automation: auto,
      });
    }
    this._fadingOutSuggestions = {
      ...this._fadingOutSuggestions,
      [yamlKey]: true,
    };
    await this._loadAutomations();
    const toast = _createdToast(auto.alias, createResult);
    this._showToast(toast.message, toast.type);
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
    this._showToast(
      this._t("automation_crud_create_failed", "Failed to create automation:") +
        " " +
        err.message,
      "error",
    );
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
    this._showToast(
      this._t("automation_crud_yaml_saved", "Automation YAML saved."),
      "success",
    );
  } catch (err) {
    this._showToast(
      this._t(
        "automation_crud_save_changes_failed",
        "Failed to save changes:",
      ) +
        " " +
        err.message,
      "error",
    );
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
