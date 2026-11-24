// Temporary browser-code access: the websocket calls behind the approval
// dialog and the banner (render-file-access.js). Mixed into the panel.
//
// An agent asks over MCP to write files a browser runs into one www/ folder;
// an admin answers here, from a real login session (the backend refuses a
// long-lived token, so an agent holding one cannot approve itself). The phone
// notification deep-links to `?file_access=<request_id>` (session-actions.js).
//
// Every call is built field by field — never a spread of what the dialog or a
// list row holds — so nothing unexpected rides into a privileged command.

export const FILE_ACCESS_EVENT = "selora_ai_file_access_updated";
export const FILE_ACCESS_MINUTES = [15, 30, 60];

/** The dialog's default duration: what the agent asked for, capped at an hour. */
export function defaultMinutes(requested) {
  const asked = Number(requested) || 60;
  const capped = Math.min(asked, 60);
  return FILE_ACCESS_MINUTES.find((m) => m >= capped) ?? 60;
}

/**
 * What the dialog shows for `target` ({kind, id}) against the loaded state:
 * `{mode: "request" | "grant" | "gone" | "loading", item}`.
 */
export function resolveFileAccessTarget(state, target) {
  if (!target) return null;
  if (!state) return { mode: "loading", item: null };
  const requests = state.requests || [];
  const grants = state.grants || [];
  if (target.kind === "request") {
    const req = requests.find((r) => r?.id === target.id);
    if (req) return { mode: "request", item: req };
  }
  const grant = grants.find((g) => g?.id === target.id);
  if (grant) return { mode: "grant", item: grant };
  return { mode: "gone", item: null };
}

/**
 * Time left as h:mm ("0:42", "1:00"), rounded UP so the last minute reads
 * "0:01", never "0:00" while access still lasts. Minutes, not seconds: a
 * ticking second hand in the header would pull the eye for an hour.
 */
export function formatRemaining(expiresAt, now = Date.now()) {
  const end = Date.parse(expiresAt);
  if (!Number.isFinite(end)) return "";
  const mins = Math.max(0, Math.ceil((end - now) / 60000));
  return `${Math.floor(mins / 60)}:${String(mins % 60).padStart(2, "0")}`;
}

/** How much of a grant's time is left, 0..1, for its bar. */
export function remainingFraction(grant, now = Date.now()) {
  const start = Date.parse(grant?.approved_at);
  const end = Date.parse(grant?.expires_at);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start)
    return 0;
  return Math.min(1, Math.max(0, (end - now) / (end - start)));
}

const TICK_MS = 15000;

// Re-render while a grant runs, so its time left counts down; stopped once
// none does. The backend's own timer ends the grant and the event reloads.
export function _syncFileAccessTick() {
  const live = (this._fileAccess?.grants || []).some((g) => g && !g.ended_at);
  if (live && !this._fileAccessTick) {
    this._fileAccessTick = setInterval(() => this.requestUpdate(), TICK_MS);
  } else if (!live && this._fileAccessTick) {
    clearInterval(this._fileAccessTick);
    this._fileAccessTick = null;
  }
}

export async function _loadFileAccess() {
  if (!this.hass) return;
  try {
    const result = await this.hass.callWS({
      type: "selora_ai/file_access/list",
    });
    this._fileAccess = {
      requests: Array.isArray(result?.requests) ? result.requests : [],
      grants: Array.isArray(result?.grants) ? result.grants : [],
    };
  } catch (_err) {
    // A non-admin, or a backend that predates the command: nothing to show.
    this._fileAccess = { requests: [], grants: [] };
  }
  this._syncFileAccessTick();
}

export function _ensureFileAccessSubscription() {
  if (this._fileAccessUnsub || this._fileAccessSubPending) return;
  if (!this.hass?.connection) return;
  this._fileAccessSubPending = true;
  this.hass.connection
    .subscribeEvents(() => this._loadFileAccess(), FILE_ACCESS_EVENT)
    .then((unsub) => {
      this._fileAccessSubPending = false;
      if (!this.isConnected) {
        try {
          unsub();
        } catch (_e) {
          /* best-effort */
        }
        return;
      }
      this._fileAccessUnsub = unsub;
    })
    .catch(() => {
      this._fileAccessSubPending = false;
    });
}

export function _dropFileAccessSubscription() {
  if (this._fileAccessUnsub) {
    try {
      this._fileAccessUnsub();
    } catch (_e) {
      /* best-effort */
    }
  }
  this._fileAccessUnsub = null;
  this._fileAccessSubPending = false;
  if (this._fileAccessTick) {
    clearInterval(this._fileAccessTick);
    this._fileAccessTick = null;
  }
}

export function _openFileAccess(id, kind = "request") {
  this._fileAccessDialog = { kind, id: String(id) };
  this._fileAccessMinutes = null;
  this._loadFileAccess();
}

export function _closeFileAccess() {
  if (this._fileAccessBusy) return;
  this._fileAccessDialog = null;
  this._fileAccessMinutes = null;
}

function _errorText(host, err) {
  if (err?.code === "login_session_required") {
    return host._t(
      "file_access_login_required",
      "Approve this from the Home Assistant app or a browser where you are signed in — not with an access token.",
    );
  }
  return err?.message || String(err);
}

export async function _decideFileAccess(requestId, approve, minutes) {
  if (this._fileAccessBusy) return;
  const msg = {
    type: "selora_ai/file_access/decide",
    request_id: String(requestId),
    approve: Boolean(approve),
  };
  if (approve) msg.minutes = Number(minutes);
  this._fileAccessBusy = true;
  try {
    const result = await this.hass.callWS(msg);
    if (approve && result?.grant?.id) {
      this._fileAccessDialog = { kind: "grant", id: String(result.grant.id) };
    } else {
      this._fileAccessDialog = null;
    }
    this._showToast?.(
      approve
        ? this._t("file_access_allowed_toast", "Access allowed")
        : this._t("file_access_denied_toast", "Request denied"),
      "success",
    );
  } catch (err) {
    if (err?.code === "not_found" || err?.code === "expired") {
      // Answered elsewhere or lapsed: the dialog shows that state.
      this._fileAccessDialog = { kind: "gone", id: String(requestId) };
    } else {
      this._showToast?.(_errorText(this, err), "error");
    }
  } finally {
    this._fileAccessBusy = false;
    this._loadFileAccess();
  }
}

export async function _revokeFileAccess(grantId) {
  if (this._fileAccessBusy) return;
  this._fileAccessBusy = true;
  try {
    await this.hass.callWS({
      type: "selora_ai/file_access/revoke",
      grant_id: String(grantId),
    });
    this._showToast?.(
      this._t("file_access_revoked_toast", "Access revoked"),
      "success",
    );
  } catch (err) {
    this._showToast?.(_errorText(this, err), "error");
  } finally {
    this._fileAccessBusy = false;
    this._loadFileAccess();
  }
}

export async function _dismissFileAccess(grantId) {
  if (this._fileAccessBusy) return;
  this._fileAccessBusy = true;
  try {
    await this.hass.callWS({
      type: "selora_ai/file_access/dismiss",
      grant_id: String(grantId),
    });
    if (this._fileAccessDialog?.id === String(grantId)) {
      this._fileAccessDialog = null;
    }
  } catch (err) {
    this._showToast?.(_errorText(this, err), "error");
  } finally {
    this._fileAccessBusy = false;
    this._loadFileAccess();
  }
}
