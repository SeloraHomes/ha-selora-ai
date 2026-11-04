// Data-loading actions (prototype-assigned to SeloraAIArchitectPanel)

import { loadHomeSuggestions } from "./home-suggestions.js";
import { invalidateProposalPreviews } from "./render-proposal-diff.js";
import { loadTrash } from "./trash.js";

export async function _loadAutomations() {
  // Refreshed with the list, which reloads after every delete and restore.
  loadTrash(this);
  try {
    const automations = await this.hass.callWS({
      type: "selora_ai/get_automations",
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
    // Automations were just re-read, so any proposal card's write preview may
    // describe a document that has since changed on disk.
    invalidateProposalPreviews(this);
    // …and any open History tab may be missing the version just written.
    this._refreshStaleVersionHistories();
  } catch (err) {
    console.error("Failed to load automations", err);
  }
}

export async function _triggerPatternScan() {
  try {
    const result = await this.hass.callWS({
      type: "selora_ai/trigger_pattern_scan",
    });
    this._showToast(
      `Scan complete — ${result.patterns_found} patterns found`,
      "success",
    );
    // What the scan found reaches the user as chat home suggestions.
    await loadHomeSuggestions(this);
  } catch (err) {
    console.error("Pattern scan failed", err);
    this._showToast(
      this._t("suggestions_pattern_scan_failed", "Pattern scan failed"),
      "error",
    );
  }
}
