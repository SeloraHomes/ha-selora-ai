// Stale automations: detection helpers.
//
// "Stale" = a Selora-authored, enabled automation that hasn't triggered for
// at least `stale_days` (configurable; default 5). Surfaced as a per-card
// pill and a filter chip in the automations list.

const STALE_KEPT_KEY = "selora_stale_kept";

function _staleDays(host) {
  return host._config?.stale_days || 5;
}

function _staleMs(host) {
  return _staleDays(host) * 24 * 60 * 60 * 1000;
}

function _loadKept() {
  try {
    return JSON.parse(localStorage.getItem(STALE_KEPT_KEY) || "{}");
  } catch {
    return {};
  }
}

function _saveKept(kept) {
  localStorage.setItem(STALE_KEPT_KEY, JSON.stringify(kept));
}

export function staleTooltip(host) {
  const days = _staleDays(host);
  return `The following Selora automations haven't triggered in ${days} day${
    days !== 1 ? "s" : ""
  }. You can remove ones you no longer need to free up space for new suggestions.`;
}

export function getStaleAutomations(host) {
  if (!host._automations?.length) return [];
  const now = Date.now();
  const staleMs = _staleMs(host);
  const cutoff = now - staleMs;
  const kept = _loadKept();

  let dirty = false;
  for (const [id, ts] of Object.entries(kept)) {
    if (now - ts > staleMs) {
      delete kept[id];
      dirty = true;
    }
  }
  if (dirty) _saveKept(kept);

  return host._automations.filter((a) => {
    if (!host._automationIsEnabled(a)) return false;
    if (!a.automation_id?.startsWith("selora_ai_")) return false;
    if (kept[a.automation_id]) return false;
    if (!a.last_triggered) {
      if (a.last_updated) {
        const created = new Date(a.last_updated).getTime();
        if (created >= cutoff) return false;
      }
      return true;
    }
    return new Date(a.last_triggered).getTime() < cutoff;
  });
}
