// Automation management actions (prototype-assigned to SeloraAIArchitectPanel)

export function _toggleExpandAutomation(key) {
  this._expandedAutomations = {
    ...this._expandedAutomations,
    [key]: !this._expandedAutomations[key],
  };
  this.requestUpdate();
}

export function _getSelectedAutomationIds() {
  return Object.keys(this._selectedAutomationIds || {}).filter(
    (id) => this._selectedAutomationIds[id],
  );
}

export function _automationIsEnabled(automation) {
  if (!automation) return false;
  if (automation.state === "on") return true;
  // "unavailable" means HA could not load the automation (e.g. invalid triggers).
  // Always show as disabled so the UI reflects the real HA state.
  if (automation.state === "unavailable") return false;
  return false;
}

export function _toggleAutomationSelection(automationId, evt) {
  evt.stopPropagation();
  if (!automationId) return;
  const checked = !!evt.target.checked;
  this._selectedAutomationIds = {
    ...this._selectedAutomationIds,
    [automationId]: checked,
  };
  this.requestUpdate();
}

export function _toggleSelectAllFiltered(filteredAutomations, checked) {
  const selectable = (filteredAutomations || []).filter(
    (a) => !a._draft && a.automation_id,
  );
  const next = { ...this._selectedAutomationIds };
  for (const auto of selectable) {
    next[auto.automation_id] = checked;
  }
  this._selectedAutomationIds = next;
  this.requestUpdate();
}

export function _clearAutomationSelection() {
  this._selectedAutomationIds = {};
  this.requestUpdate();
}

export async function _bulkToggleSelected(enable) {
  if (this._bulkActionInProgress) return;
  const selectedIds = this._getSelectedAutomationIds();
  if (!selectedIds.length) return;

  const byId = new Map(this._automations.map((a) => [a.automation_id, a]));
  const targets = selectedIds
    .map((id) => byId.get(id))
    .filter((a) => a && !a._draft && a.automation_id)
    .filter((a) =>
      enable ? !this._automationIsEnabled(a) : this._automationIsEnabled(a),
    );
  const skippedCount = selectedIds.length - targets.length;

  if (!targets.length) {
    this._showToast(
      `Selected automations are already ${enable ? "enabled" : "disabled"}.`,
      "info",
    );
    return;
  }

  this._bulkActionInProgress = true;
  this._bulkActionLabel = `${enable ? "Enabling" : "Disabling"} ${targets.length} automation(s)…`;
  let successCount = 0;
  try {
    for (const auto of targets) {
      try {
        await this.hass.callWS({
          type: "selora_ai/toggle_automation",
          automation_id: auto.automation_id,
          entity_id: auto.entity_id,
          enabled: enable,
        });
        successCount += 1;
      } catch (err) {
        console.error("Bulk toggle failed", auto.automation_id, err);
      }
    }
    await this._loadAutomations();
    const failedCount = targets.length - successCount;
    if (failedCount === 0) {
      const skippedNote =
        skippedCount > 0 ? ` (${skippedCount} already in target state)` : "";
      this._showToast(
        `${enable ? "Enabled" : "Disabled"} ${successCount} automation(s)${skippedNote}.`,
        "success",
      );
    } else {
      this._showToast(
        `${enable ? "Enable" : "Disable"} completed: ${successCount} succeeded, ${failedCount} failed.`,
        "error",
      );
    }
  } finally {
    this._bulkActionInProgress = false;
    this._bulkActionLabel = "";
    this.requestUpdate();
  }
}

export async function _bulkSoftDeleteSelected() {
  if (this._bulkActionInProgress) return;
  const selectedIds = this._getSelectedAutomationIds();
  if (!selectedIds.length) return;

  const byId = new Map(this._automations.map((a) => [a.automation_id, a]));
  const targets = selectedIds
    .map((id) => byId.get(id))
    .filter((a) => a && !a._draft && a.automation_id);

  if (!targets.length) return;
  if (!confirm(`Delete ${targets.length} selected automation(s)?`)) return;

  this._bulkActionInProgress = true;
  this._bulkActionLabel = `Deleting ${targets.length} automation(s)…`;
  let successCount = 0;
  try {
    for (const auto of targets) {
      try {
        await this.hass.callWS({
          type: "selora_ai/delete_automation",
          automation_id: auto.automation_id,
        });
        successCount += 1;
      } catch (err) {
        console.error("Bulk delete failed", auto.automation_id, err);
      }
    }
    this._selectedAutomationIds = {};
    await this._loadAutomations();
    const failedCount = targets.length - successCount;
    if (failedCount === 0) {
      this._showToast(`Deleted ${successCount} automation(s).`, "success");
    } else {
      this._showToast(
        `Delete completed: ${successCount} succeeded, ${failedCount} failed.`,
        "error",
      );
    }
  } finally {
    this._bulkActionInProgress = false;
    this._bulkActionLabel = "";
    this.requestUpdate();
  }
}

export async function _toggleAutomation(entityId, automationId, enabled) {
  try {
    await this.hass.callWS({
      type: "selora_ai/toggle_automation",
      automation_id: automationId,
      entity_id: entityId,
      enabled: !!enabled,
    });
    await this._loadAutomations();
  } catch (err) {
    console.error("Failed to toggle automation", err);
    const message = err?.message || "unknown error";
    this._showToast(`Failed to toggle automation: ${message}`, "error");
  }
}

export function _toggleBurgerMenu(automationId, evt) {
  evt.stopPropagation();
  this._openBurgerMenu =
    this._openBurgerMenu === automationId ? null : automationId;
  this.requestUpdate();
}

export function _closeBurgerMenus() {
  if (this._openBurgerMenu) {
    this._openBurgerMenu = null;
    this.requestUpdate();
  }
}

// -------------------------------------------------------------------------
// Rename automation
// -------------------------------------------------------------------------

export function _startRenameAutomation(automationId, currentAlias) {
  this._editingAlias = automationId;
  this._editingAliasValue = currentAlias || "";
  this._openBurgerMenu = null;
  this.requestUpdate();
  // Focus the input after render
  this.updateComplete.then(() => {
    const input = this.shadowRoot.querySelector(
      `.rename-input[data-id="${automationId}"]`,
    );
    if (input) {
      input.focus();
      input.select();
    }
  });
}

export async function _saveRenameAutomation(automationId) {
  const newAlias = (this._editingAliasValue || "").trim();
  if (!newAlias) {
    this._editingAlias = null;
    return;
  }
  try {
    await this.hass.callWS({
      type: "selora_ai/rename_automation",
      automation_id: automationId,
      alias: newAlias,
    });
    this._editingAlias = null;
    this._showToast("Automation renamed", "success");
    await this._loadAutomations();
  } catch (err) {
    console.error("Failed to rename automation", err);
    this._showToast("Failed to rename: " + err.message, "error");
  }
}

export function _cancelRenameAutomation() {
  this._editingAlias = null;
  this._editingAliasValue = "";
}

// -------------------------------------------------------------------------
// Version history methods
// -------------------------------------------------------------------------

export async function _openVersionHistory(automationId) {
  const isOpen = !!this._versionHistoryOpen[automationId];
  this._versionHistoryOpen = {
    ...this._versionHistoryOpen,
    [automationId]: !isOpen,
  };
  if (!isOpen && !this._versions[automationId]) {
    await this._loadVersionHistory(automationId);
  }
  this.requestUpdate();
}

export async function _loadVersionHistory(automationId) {
  this._loadingVersions = { ...this._loadingVersions, [automationId]: true };
  try {
    const result = await this.hass.callWS({
      type: "selora_ai/get_automation_versions",
      automation_id: automationId,
    });
    const ordered = Array.isArray(result) ? [...result].reverse() : [];
    this._versions = { ...this._versions, [automationId]: ordered };
  } catch (err) {
    console.error("Failed to load version history", err);
    this._showToast("Failed to load version history: " + err.message, "error");
  } finally {
    this._loadingVersions = {
      ...this._loadingVersions,
      [automationId]: false,
    };
  }
  this.requestUpdate();
}

export async function _openDiffViewer(automationId) {
  const versions = this._versions[automationId];
  if (!versions || versions.length < 2)
    await this._loadVersionHistory(automationId);
  const v = this._versions[automationId] || [];
  this._diffAutomationId = automationId;
  this._diffVersionA = v[0]?.version_id || null;
  this._diffVersionB = v[1]?.version_id || null;
  this._diffResult = [];
  this._diffOpen = true;
  if (this._diffVersionA && this._diffVersionB) {
    await this._loadDiff(automationId, this._diffVersionA, this._diffVersionB);
  }
  this.requestUpdate();
}

export async function _loadDiff(automationId, versionAId, versionBId) {
  if (!versionAId || !versionBId) return;
  this._loadingDiff = true;
  this._diffResult = [];
  try {
    const result = await this.hass.callWS({
      type: "selora_ai/get_automation_diff",
      automation_id: automationId,
      version_id_a: versionAId,
      version_id_b: versionBId,
    });
    const diffText = result?.diff || "";
    this._diffResult = diffText ? diffText.split("\n") : [];
  } catch (err) {
    console.error("Failed to load diff", err);
    this._showToast("Failed to load diff: " + err.message, "error");
  } finally {
    this._loadingDiff = false;
  }
  this.requestUpdate();
}

export async function _restoreVersion(automationId, versionId, yamlText) {
  const key = `${automationId}_${versionId}`;
  this._restoringVersion = { ...this._restoringVersion, [key]: true };
  try {
    await this.hass.callWS({
      type: "selora_ai/update_automation_yaml",
      automation_id: automationId,
      yaml_text: yamlText,
      version_message: `Restored from version ${versionId}`,
    });
    this._versionHistoryOpen = {
      ...this._versionHistoryOpen,
      [automationId]: false,
    };
    this._versions = { ...this._versions, [automationId]: null };
    await this._loadAutomations();
    this._showToast("Version restored.", "success");
  } catch (err) {
    console.error("Failed to restore version", err);
    this._showToast("Failed to restore version: " + err.message, "error");
  } finally {
    this._restoringVersion = { ...this._restoringVersion, [key]: false };
  }
  this.requestUpdate();
}

// -------------------------------------------------------------------------
// Delete methods
// -------------------------------------------------------------------------

export async function _deleteAutomation(automationId) {
  if (!confirm("Delete this automation permanently?")) return;
  this._deletingAutomation = {
    ...this._deletingAutomation,
    [automationId]: true,
  };
  try {
    await this.hass.callWS({
      type: "selora_ai/delete_automation",
      automation_id: automationId,
    });
    await this._loadAutomations();
    this._showToast("Automation deleted.", "success");
  } catch (err) {
    console.error("Failed to delete automation", err);
    this._showToast("Failed to delete automation: " + err.message, "error");
  } finally {
    this._deletingAutomation = {
      ...this._deletingAutomation,
      [automationId]: false,
    };
  }
  this.requestUpdate();
}

// -------------------------------------------------------------------------
// Refine in chat
// -------------------------------------------------------------------------

export async function _loadAutomationToChat(automationId) {
  if (!automationId) {
    this._showToast(
      "This automation cannot be refined because it has no automation ID.",
      "error",
    );
    return;
  }
  this._loadingToChat = { ...this._loadingToChat, [automationId]: true };
  try {
    const result = await this.hass.callWS({
      type: "selora_ai/load_automation_to_session",
      automation_id: automationId,
    });
    const sessionId = result?.session_id;
    if (sessionId) {
      this._activeSessionId = sessionId;
      this._activeTab = "chat";
      this._showSidebar = false;
      await this._openSession(sessionId);
      this._showToast("Automation loaded into chat.", "success");
    }
  } catch (err) {
    console.error("Failed to load automation to chat", err);
    this._showToast(
      "Failed to load automation into chat: " + err.message,
      "error",
    );
  } finally {
    this._loadingToChat = { ...this._loadingToChat, [automationId]: false };
  }
  this.requestUpdate();
}
