// Developer mode's shortcuts: things that otherwise wait on a timer.
//
// The network camera scan runs a minute after start and then every half hour,
// a long wait while testing a camera, and the direct sweep of every local
// address only once a day. In developer mode the Settings page has a button
// for the scan, and the browser console has `seloraDev` for both, announced
// once when developer mode is on.
import { loadHomeSuggestions } from "./home-suggestions.js";

let announced = false;

/** Scan the network for cameras now, then refresh the chat home's suggestions. */
export async function scanCameras(host, { sweep = false } = {}) {
  const result = await host.hass.callWS({
    type: "selora_ai/frigate/scan",
    ...(sweep ? { sweep: true } : {}),
  });
  await loadHomeSuggestions(host);
  return result?.cameras || [];
}

/** Install `seloraDev` on the page in developer mode, and remove it otherwise. */
export function syncDevConsole(host) {
  if (!host._config?.developer_mode) {
    delete globalThis.seloraDev;
    return;
  }
  globalThis.seloraDev = {
    async scanCameras(options) {
      const cameras = await scanCameras(host, options);
      console.table(cameras);
      return cameras;
    },
  };
  if (announced) return;
  announced = true;
  console.info(
    "%cSelora AI developer mode%c\n" +
      "seloraDev.scanCameras() — scan the network for cameras now and refresh the chat home's suggestions (otherwise every 30 min).\n" +
      "seloraDev.scanCameras({ sweep: true }) — also ask every local address directly, for cameras that ignore the network-wide probe (otherwise once a day).",
    "font-weight:bold",
    "",
  );
}
