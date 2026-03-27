// Suggestion and data-loading actions (prototype-assigned to SeloraAIArchitectPanel)

export async function _loadSuggestions() {
  try {
    const suggestions = await this.hass.callWS({
      type: "selora_ai/get_suggestions",
    });
    this._suggestions = suggestions || [];
  } catch (err) {
    console.error("Failed to load suggestions", err);
  }
}

export async function _triggerGenerateSuggestions() {
  this._generatingSuggestions = true;
  try {
    const newSuggestions = await this.hass.callWS({
      type: "selora_ai/generate_suggestions",
    });
    // Merge new suggestions with existing — don't replace
    const existingAliases = new Set(
      (this._suggestions || []).map((s) => {
        const a = s.automation || s.automation_data || {};
        return (a.alias || "").toLowerCase();
      }),
    );
    const added = [];
    for (const s of newSuggestions || []) {
      const a = s.automation || s.automation_data || {};
      const alias = (a.alias || "").toLowerCase();
      if (!existingAliases.has(alias)) {
        added.push(s);
        existingAliases.add(alias);
      }
    }
    this._suggestions = [...added, ...this._suggestions];
    // Reload proactive suggestions to stay in sync
    await this._loadProactiveSuggestions();
    if (added.length > 0) {
      this._showToast(
        `Generated ${added.length} new recommendation(s)`,
        "success",
      );
    } else {
      this._showToast(
        "Analysis complete — no new suggestions at this time",
        "info",
      );
    }
  } catch (err) {
    console.error("Failed to generate suggestions", err);
    this._showToast(
      "Failed to generate suggestions: " + (err.message || "unknown error"),
      "error",
    );
  } finally {
    this._generatingSuggestions = false;
  }
}

export async function _loadAutomations() {
  try {
    const automations = await this.hass.callWS({
      type: "selora_ai/get_automations",
      include_deleted: true,
    });
    this._automations = (automations || []).reverse();
    const validIds = new Set(
      this._automations.map((a) => a.automation_id).filter(Boolean),
    );
    this._selectedAutomationIds = Object.fromEntries(
      Object.entries(this._selectedAutomationIds || {}).filter(
        ([id, selected]) => selected && validIds.has(id),
      ),
    );
  } catch (err) {
    console.error("Failed to load automations", err);
  }
  // Also load proactive suggestions
  this._loadProactiveSuggestions();
}

export async function _loadProactiveSuggestions() {
  this._loadingProactive = true;
  try {
    const suggestions = await this.hass.callWS({
      type: "selora_ai/get_proactive_suggestions",
      status: "pending",
    });
    // Deduplicate by description
    const seen = new Set();
    this._proactiveSuggestions = (suggestions || []).filter((s) => {
      const key = (s.description || "").toLowerCase().trim();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  } catch (err) {
    console.error("Failed to load proactive suggestions", err);
    this._proactiveSuggestions = [];
  }
  this._loadingProactive = false;
}

export async function _acceptProactiveSuggestion(suggestionId, editedYaml) {
  this._acceptingProactive = {
    ...this._acceptingProactive,
    [suggestionId]: true,
  };
  try {
    if (editedYaml) {
      await this.hass.callWS({
        type: "selora_ai/accept_suggestion_with_edits",
        suggestion_id: suggestionId,
        automation_yaml: editedYaml,
      });
    } else {
      await this.hass.callWS({
        type: "selora_ai/update_proactive_suggestion",
        suggestion_id: suggestionId,
        action: "accepted",
      });
    }
    this._showToast("Suggestion accepted — automation created", "success");
    await this._loadAutomations();
  } catch (err) {
    console.error("Failed to accept suggestion", err);
    this._showToast("Failed to accept suggestion", "error");
  }
  this._acceptingProactive = {
    ...this._acceptingProactive,
    [suggestionId]: false,
  };
}

export async function _dismissProactiveSuggestion(suggestionId) {
  this._dismissingProactive = {
    ...this._dismissingProactive,
    [suggestionId]: true,
  };
  try {
    await this.hass.callWS({
      type: "selora_ai/update_proactive_suggestion",
      suggestion_id: suggestionId,
      action: "dismissed",
    });
    this._proactiveSuggestions = this._proactiveSuggestions.filter(
      (s) => s.suggestion_id !== suggestionId,
    );
    this._showToast("Suggestion dismissed", "info");
  } catch (err) {
    console.error("Failed to dismiss suggestion", err);
  }
  this._dismissingProactive = {
    ...this._dismissingProactive,
    [suggestionId]: false,
  };
}

export async function _snoozeProactiveSuggestion(suggestionId) {
  try {
    await this.hass.callWS({
      type: "selora_ai/update_proactive_suggestion",
      suggestion_id: suggestionId,
      action: "snoozed",
    });
    this._proactiveSuggestions = this._proactiveSuggestions.filter(
      (s) => s.suggestion_id !== suggestionId,
    );
    this._showToast("Suggestion snoozed for 24h", "info");
  } catch (err) {
    console.error("Failed to snooze suggestion", err);
  }
}

export async function _triggerPatternScan() {
  this._loadingProactive = true;
  try {
    const result = await this.hass.callWS({
      type: "selora_ai/trigger_pattern_scan",
    });
    this._showToast(
      `Scan complete — ${result.patterns_found} patterns found`,
      "success",
    );
    await this._loadProactiveSuggestions();
  } catch (err) {
    console.error("Pattern scan failed", err);
    this._showToast("Pattern scan failed", "error");
  }
  this._loadingProactive = false;
}
