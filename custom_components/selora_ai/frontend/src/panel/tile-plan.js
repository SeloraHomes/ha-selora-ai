/**
 * How a reply's tile grid lays out its tiles, and which mount now.
 *
 * Each tile is a live card, so a big home's inventory would mount hundreds at
 * once. Up to `limit` tiles render directly. Past it, areas holding several
 * tiles each collapse into one row per area ("Living Room · 6") whose tiles
 * mount when the row is opened; otherwise the first `limit` tiles mount and
 * the rest wait behind "Show all" — a row per one-tile area is a single
 * column again.
 *
 * Headers group only when groups hold several tiles each: with about one tile
 * per area every header is a full-width row and the grid degrades to a single
 * column.
 *
 * @param {Array<[string|null, string[]]>} groups  [areaName, entityIds] in display order
 * @param {number} limit
 * @returns {{now: object[], later: object[], sections: object[], tileCount: number}}
 *   `now`/`later` items are `{areaName}` (a header) or `{id}` (a tile);
 *   `sections` are `{areaName, ids}`, set only when the grid collapses
 */
export function planTiles(groups, limit) {
  const tileCount = groups.reduce((n, [, ids]) => n + ids.length, 0);
  const headers = groups.length > 1 && tileCount >= groups.length * 2;
  if (tileCount > limit && headers) {
    return {
      now: [],
      later: [],
      sections: groups.map(([areaName, ids]) => ({ areaName, ids })),
      tileCount,
    };
  }
  const items = [];
  for (const [areaName, ids] of groups) {
    if (headers) items.push({ areaName });
    for (const id of ids) items.push({ id });
  }
  let shown = 0;
  let cut = items.length;
  for (let k = 0; k < items.length; k++) {
    if (shown === limit) {
      cut = k;
      break;
    }
    if ("id" in items[k]) shown += 1;
  }
  return {
    now: items.slice(0, cut),
    later: items.slice(cut),
    sections: [],
    tileCount,
  };
}
