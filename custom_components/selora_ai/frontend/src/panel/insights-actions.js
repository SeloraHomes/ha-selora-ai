// Health data-loading + actions (prototype-assigned to SeloraAIPanel). The
// assessment (checks + score) comes from _loadAudit; this just resolves whether
// Health is enabled and whether host export is opted in.

export async function _loadInsights() {
  try {
    const res = await this.hass.callWS({ type: "selora_ai/insights/list" });
    this._insightsEnabled = res.enabled !== false;
    this._insightsExportEnabled = !!res.export_enabled;
  } catch (err) {
    console.error("Failed to load insights", err);
  }
}

function _applyAudit(host, audit) {
  host._auditStatus = audit?.status || "pending";
  host._auditResponse = audit?.response || "";
  host._auditRecommendations = audit?.recommendations || [];
  // Per-check results for the checklist (every check + its clear/issues status).
  host._auditChecks = audit?.checks || [];
  // Deterministic 0-100 health score + A-F band (null until a run completes).
  host._auditScore = typeof audit?.score === "number" ? audit.score : null;
  host._auditBand = audit?.band || "";
  // Per-finding point attribution behind the score ("why this score").
  host._auditBreakdown = audit?.score_breakdown || null;
  host._auditQuickActions = audit?.quick_actions || [];
  host._auditGeneratedAt = audit?.generated_at || null;
  host._auditError = audit?.error || null;
  // Home still booting — devices are reconnecting, so the score would
  // over-count offline devices. The panel shows a spinner instead.
  host._auditSettling = audit?.settling === true;
}

export async function _loadAudit() {
  // Cancel any pending settle re-check so retries never stack up.
  if (this._settleRetryTimer) {
    clearTimeout(this._settleRetryTimer);
    this._settleRetryTimer = null;
  }
  // Fallback cadence if the request fails before we learn the server's hint —
  // a transient WS/server error must not strand the page on the spinner.
  let retryAfter = 15;
  try {
    const audit = await this.hass.callWS({ type: "selora_ai/insights/audit" });
    _applyAudit(this, audit);
    if (Number(audit?.retry_after) > 0) retryAfter = Number(audit.retry_after);
  } catch (err) {
    console.error("Failed to load home audit", err);
  } finally {
    // First fetch resolved (success or not) — stop showing the loading state.
    this._auditLoaded = true;
    // Keep polling while the home is still settling, INCLUDING after a failed
    // fetch (settling stays true from the prior load), so the real score swaps
    // in on its own and the spinner can never get stuck. A settled/failed-first
    // load leaves _auditSettling false → no retry.
    if (this._auditSettling) {
      this._settleRetryTimer = setTimeout(
        () => this._loadAudit(),
        retryAfter * 1000 + 2000,
      );
    }
  }
}

export async function _rerunAudit() {
  this._auditRunning = true;
  try {
    const audit = await this.hass.callWS({
      type: "selora_ai/insights/audit_rerun",
    });
    if (audit?.status === "ok") {
      _applyAudit(this, audit);
      this._showToast(
        this._t("insights_audit_done", "Home audit updated"),
        "success",
      );
    } else if (audit?.status === "no_llm") {
      _applyAudit(this, audit);
      this._showToast(
        this._t(
          "insights_audit_no_llm_toast",
          "Configure an LLM to run audits",
        ),
        "info",
      );
    } else if (audit?.status === "unsupported") {
      _applyAudit(this, audit);
      this._showToast(
        this._t(
          "insights_audit_unsupported_toast",
          "Home audits need a cloud LLM provider",
        ),
        "info",
      );
    } else {
      // The run failed (e.g. transient Cloud 502). The server preserves the
      // last good audit, so reload it instead of replacing the cards with an
      // error state — just tell the user the refresh didn't take.
      this._showToast(
        this._t(
          "insights_audit_failed",
          "Home audit failed — showing the last one",
        ),
        "error",
      );
      await this._loadAudit();
    }
  } catch (err) {
    console.error("Home audit failed", err);
    this._showToast(
      this._t(
        "insights_audit_failed",
        "Home audit failed — showing the last one",
      ),
      "error",
    );
    await this._loadAudit();
  }
  this._auditRunning = false;
}

// Mute something from device health via the "Selora exclude" HA label (the
// same label the suggestion ignore-list uses). Applies the label to the
// device (or the referenced entities), then rescans so the now-excluded
// signals resolve, and reloads. Undo it in Settings → Ignore list.
async function _applyExclude(host, { device_id, entity_ids }) {
  if (device_id) {
    await host.hass.callWS({
      type: "selora_ai/apply_exclude_label",
      device_id,
    });
  } else {
    for (const eid of entity_ids || []) {
      await host.hass.callWS({
        type: "selora_ai/apply_exclude_label",
        entity_id: eid,
      });
    }
  }
  await host.hass.callWS({ type: "selora_ai/insights/rescan" });
  await Promise.all([host._loadInsights(), host._loadAudit()]);
  host._showToast(
    host._t("insights_ignored", "Ignored — undo it in Settings → Ignore list"),
    "success",
  );
}

export async function _ignoreFix(fix) {
  try {
    await _applyExclude(this, {
      device_id: fix.device_id,
      entity_ids: fix.entities || [],
    });
  } catch (err) {
    console.error("Failed to ignore fix", err);
    this._showToast(
      this._t("insights_ignore_failed", "Couldn't ignore that"),
      "error",
    );
  }
}

// Open the delete confirmation for a long-offline device. The finding carries
// everything the prompt needs (device name + how long it's been down), so the
// modal never has to re-derive it from the templated title.
export function _promptDeleteDevice(fix) {
  if (!fix?.device_id) return;
  this._deleteDeviceTarget = {
    device_id: fix.device_id,
    name: fix.device_name || "",
    offline_seconds: fix.offline_seconds || 0,
  };
}

export async function _confirmDeleteDevice() {
  const target = this._deleteDeviceTarget;
  if (!target?.device_id || this._deletingDevice) return;
  this._deletingDevice = true;
  try {
    const res = await this.hass.callWS({
      type: "selora_ai/insights/delete_device",
      device_id: target.device_id,
    });
    this._deleteDeviceTarget = null;
    const name = res?.name || target.name;
    this._showToast(
      name
        ? `${name} ${this._t("insights_device_deleted_suffix", "deleted")}`
        : this._t("insights_device_deleted", "Device deleted"),
      "success",
    );
    // The device is gone but its signals are still active — rescan resolves
    // them, then reload so the card (and the score) drop it.
    await this.hass.callWS({ type: "selora_ai/insights/rescan" });
    await Promise.all([this._loadInsights(), this._loadAudit()]);
  } catch (err) {
    // The backend re-checks live state, so the usual refusal here is a device
    // that came back since the card was drawn. Surface its reason rather than a
    // generic failure, and reload — the finding the button sits on is the stale
    // thing, so leaving it up invites the same click again.
    console.error("Failed to delete device", err);
    this._deleteDeviceTarget = null;
    this._showToast(
      err?.message ||
        this._t("insights_device_delete_failed", "Couldn't delete that device"),
      "error",
    );
    await Promise.all([this._loadInsights(), this._loadAudit()]);
  } finally {
    this._deletingDevice = false;
  }
}

export async function _openInsights() {
  // A delete prompt left open when the user navigated away must not pop back up
  // on a tab they just re-entered — and its finding is about to be re-fetched.
  this._deleteDeviceTarget = null;
  // Called when the Insights tab activates — load the cached audit + signals.
  await Promise.all([this._loadAudit(), this._loadInsights()]);
  // No audit cached yet (first visit before the background run finished) —
  // kick one off now so the user sees the spinner resolve to a result in this
  // session instead of having to reload. The runner is lock-guarded and
  // fingerprint-throttled, so this is cheap even if a background run is racing.
  if (this._auditStatus === "pending" && !this._auditRunning) {
    this._rerunAudit();
  }
}
