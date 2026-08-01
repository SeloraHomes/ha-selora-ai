// Build identity baked in by build.js (a hash of the sources this bundle was
// built from). Reported to `selora_ai/version_status`, which compares it with
// the `panel.build.json` deployed next to the bundle: a mismatch means this
// browser is running an older panel than the one on disk. Empty when the bundle
// wasn't produced by build.js (unit tests) — the backend then skips that check.
export const PANEL_BUILD =
  typeof __SELORA_BUILD__ !== "undefined" ? __SELORA_BUILD__ : "";

// A restart is the only fix for a Python-side skew, so this stand-in never
// claims the bundle is stale — that half of the check can't run here.
const RESTART_ONLY = { restart_required: true, panel_reload_required: false };

export async function _loadVersionStatus() {
  try {
    this._versionStatus = await this.hass.callWS({
      type: "selora_ai/version_status",
      panel_build: PANEL_BUILD,
    });
  } catch (err) {
    // This bundle ships with the command, so a backend that doesn't know it is
    // last-deploy's process still in memory — the very skew we're detecting,
    // and the one case where the check can't answer for itself. That's the
    // first no-restart upgrade onto this feature: the panel is new, the
    // registered websocket commands are the old set. Treat it as
    // restart-required rather than staying silent.
    //
    // Every other failure stays silent: `unauthorized` (a non-admin opening
    // the panel) and a dropped connection say nothing about deployed code.
    this._versionStatus = err?.code === "unknown_command" ? RESTART_ONLY : null;
  }
}

export function _dismissStaleCodeNotice() {
  this._staleCodeDismissed = true;
}
