// Developer tools live in the browser console, never in the UI: a panel that
// renders differently for developers misleads them about what users see.
//
// Every page with the panel gets `selora`, announced once with a banner. It
// runs what otherwise waits on a timer (camera and pattern scans, suggestion
// generation), points the panel at another recipes catalog or Selora Cloud,
// and, with `selora.debug(true)`, logs each chat turn's raw response, tool
// calls and reply time.
import { loadHomeSuggestions } from "./home-suggestions.js";

const DEBUG_KEY = "selora-ai-debug";
const CATALOG_URL_KEY = "selora-ai-catalog-url";

const BANNER = [
  "███████╗███████╗██╗      ██████╗ ██████╗  █████╗ ",
  "██╔════╝██╔════╝██║     ██╔═══██╗██╔══██╗██╔══██╗",
  "███████╗█████╗  ██║     ██║   ██║██████╔╝███████║",
  "╚════██║██╔══╝  ██║     ██║   ██║██╔══██╗██╔══██║",
  "███████║███████╗███████╗╚██████╔╝██║  ██║██║  ██║",
  "╚══════╝╚══════╝╚══════╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝",
].join("\n");

const COMMANDS = [
  ["selora.help()", "show this list"],
  [
    "selora.debug(true | false)",
    "log each chat turn's raw response, tool calls and reply time (remembered in this browser)",
  ],
  [
    "selora.scanCameras()",
    "scan the network for cameras now and refresh the chat home's suggestions (otherwise every 30 min)",
  ],
  [
    "selora.scanCameras({ sweep: true })",
    "also ask every local address directly, for cameras that ignore the network-wide probe (otherwise once a day)",
  ],
  ["selora.scanPatterns()", "run the pattern scan behind the suggestions now"],
  [
    "selora.generateSuggestions()",
    "ask the LLM for new automation suggestions now",
  ],
  [
    "selora.catalogUrl(url)",
    'fetch recipes from another catalog, e.g. "http://localhost:1313/api/recipes.json"; "" resets it (this browser only)',
  ],
  [
    "selora.connectUrl(url)",
    'use another Selora Connect / Cloud server; "" resets it to connect.selorahomes.com',
  ],
];

let host = null;
let announced = false;

function _storageGet(key) {
  try {
    return localStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

function _storageSet(key, value) {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch (err) {
    console.debug(`Failed to persist ${key}`, err);
  }
}

/** Whether chat turns are logged to the console (`selora.debug(true)`). */
export function debugEnabled() {
  return _storageGet(DEBUG_KEY) === "1";
}

/** The recipes catalog URL set with `selora.catalogUrl()`, or "". */
export function catalogUrlOverride() {
  return _storageGet(CATALOG_URL_KEY);
}

/** Log a finished chat turn, when `selora.debug(true)` is on. */
export function logChatTurn(response, event, replyMs) {
  if (!debugEnabled()) return;
  const markerCount = (response.match(/\[\[entit(?:y|ies):[^\]]+\]\]/g) || [])
    .length;
  const toolCalls = event.tool_calls || [];
  console.groupCollapsed(
    `Selora chat · ${replyMs} ms · ${toolCalls.length} tool call(s) · ${markerCount} entity marker(s)`,
  );
  if (toolCalls.length) {
    console.table(
      toolCalls.map((tc) => ({
        tool: tc.tool,
        arguments: JSON.stringify(tc.arguments || {}),
      })),
    );
  }
  console.log("raw response:\n" + response);
  console.log("event:", event);
  console.groupEnd();
}

/** Scan the network for cameras now, then refresh the chat home's suggestions. */
export async function scanCameras(panel, { sweep = false } = {}) {
  const result = await panel.hass.callWS({
    type: "selora_ai/frigate/scan",
    ...(sweep ? { sweep: true } : {}),
  });
  await loadHomeSuggestions(panel);
  return result?.cameras || [];
}

function _requireHost() {
  if (!host?.hass) throw new Error("Open the Selora AI panel first.");
  return host;
}

function help() {
  const width = Math.max(...COMMANDS.map(([cmd]) => cmd.length));
  console.info(
    `%c${BANNER}%c\n\nDeveloper tools for Selora AI, from this console:\n\n` +
      COMMANDS.map(([cmd, desc]) => `  ${cmd.padEnd(width)}  ${desc}`).join(
        "\n",
      ),
    "color:#f59e0b;font-weight:bold;line-height:1.1",
    "",
  );
}

const api = {
  help,
  debug(on = true) {
    _storageSet(DEBUG_KEY, on ? "1" : "");
    return on
      ? "Chat turns are now logged here."
      : "Chat turns are no longer logged.";
  },
  async scanCameras(options) {
    const cameras = await scanCameras(_requireHost(), options);
    console.table(cameras);
    return cameras;
  },
  async scanPatterns() {
    await _requireHost()._triggerPatternScan();
  },
  async generateSuggestions() {
    await _requireHost()._triggerGenerateSuggestions();
  },
  catalogUrl(url) {
    if (url === undefined) return catalogUrlOverride() || "(default)";
    _storageSet(CATALOG_URL_KEY, String(url).trim());
    host?._loadRecipesCatalog?.(true);
    return catalogUrlOverride() || "(default)";
  },
  async connectUrl(url) {
    const panel = _requireHost();
    if (url !== undefined) {
      await panel.hass.callWS({
        type: "selora_ai/update_config",
        config: { selora_connect_url: String(url).trim() },
      });
      await panel._loadConfig();
    }
    return panel._config?.selora_connect_url || "(default)";
  },
};

/** Install `selora` on the page for this panel, announcing it once. */
export function installDevConsole(panel) {
  host = panel;
  globalThis.selora = api;
  if (announced) return;
  announced = true;
  help();
}
