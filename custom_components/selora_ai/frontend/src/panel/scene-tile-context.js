// Lit context provider for scene-target tile grids.
//
// HA card features (the tile's brightness slider, cover arrows, …) read
// entity state and callService from Lit context — the "states" and
// "hassApi" keys provided at HA's root — not from the `hass` handed to the
// card. A scene grid's pinned states and intercepted callService therefore
// reach only the tile's own name and label: the slider showed the live
// device and drove it. Answering those context requests on the grid itself
// makes the features see the same forced hass the card does, since the
// nearest provider wins.

const STATES = "states";
const API = "hassApi";

function _apiFrom(hass) {
  // Delegate through the panel's hass so methods stay bound to it.
  return {
    callService: (...args) => hass.callService(...args),
    callApi: (...args) => hass.callApi(...args),
    callApiRaw: (...args) => hass.callApiRaw?.(...args),
    fetchWithAuth: (...args) => hass.fetchWithAuth(...args),
    sendWS: (...args) => hass.sendWS(...args),
    callWS: (...args) => hass.callWS(...args),
  };
}

function _onContextRequest(ev) {
  const ctx = ev.currentTarget.__seloraTileContext;
  if (!ctx || !(ev.context in ctx.values)) return;
  ev.stopPropagation();
  const key = ev.context;
  if (!ev.subscribe) {
    ev.callback(ctx.values[key]);
    return;
  }
  // HA's consumer drops a subscription whose disposer changes between
  // callbacks, so each subscriber keeps the one it was first given.
  const subs = ctx.subscribers[key];
  if (!subs.has(ev.callback)) {
    const cb = ev.callback;
    subs.set(cb, () => subs.delete(cb));
  }
  ev.callback(ctx.values[key], subs.get(ev.callback));
}

// Provide (or update) the grid's states and api contexts from the hass its
// tiles are given. Call it before the tiles connect — features request
// context when they attach — and again whenever the forced state changes.
export function provideSceneTileContext(grid, gridHass) {
  let ctx = grid.__seloraTileContext;
  if (!ctx) {
    ctx = {
      values: {},
      subscribers: { [STATES]: new Map(), [API]: new Map() },
    };
    grid.__seloraTileContext = ctx;
    grid.addEventListener("context-request", _onContextRequest);
  }
  ctx.values = {
    [STATES]: gridHass.states,
    [API]: _apiFrom(gridHass),
  };
  for (const [key, subs] of Object.entries(ctx.subscribers)) {
    for (const [cb, dispose] of subs) cb(ctx.values[key], dispose);
  }
}
