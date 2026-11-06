// List sort preferences: the order a user picks for a list survives a reload.
//
// Per browser, not per HA user: an order is a viewing habit, not something
// another account on the same browser must not see. A stored value the list
// no longer offers is ignored, so removing a sort option never strands a
// user on an order the select can't show.

const KEY_PREFIX = "selora_ai_sort:";

export const SORT_LISTS = {
  automations: {
    by: ["recent", "alpha", "enabled_first"],
    byProp: "_sortBy",
    dirProp: "_sortDir",
  },
  scenes: {
    by: ["recent", "alpha", "size"],
    byProp: "_sceneSortBy",
    dirProp: "_sceneSortDir",
  },
};

const DIRS = ["asc", "desc"];

function _storage() {
  try {
    return globalThis.localStorage || null;
  } catch {
    return null;
  }
}

// Set each list's saved sort on the host, leaving its default where nothing
// valid was saved.
export function loadSortPrefs(host) {
  const storage = _storage();
  if (!storage) return;
  for (const [list, spec] of Object.entries(SORT_LISTS)) {
    let saved = null;
    try {
      saved = JSON.parse(storage.getItem(KEY_PREFIX + list) || "null");
    } catch {
      continue;
    }
    if (!saved || typeof saved !== "object") continue;
    if (spec.by.includes(saved.by)) host[spec.byProp] = saved.by;
    if (DIRS.includes(saved.dir)) host[spec.dirProp] = saved.dir;
  }
}

// Record the list's current sort from the host.
export function saveSortPref(host, list) {
  const spec = SORT_LISTS[list];
  const storage = _storage();
  if (!spec || !storage) return;
  try {
    storage.setItem(
      KEY_PREFIX + list,
      JSON.stringify({ by: host[spec.byProp], dir: host[spec.dirProp] }),
    );
  } catch {
    // Quota or private mode: the order just won't outlive the page.
  }
}
