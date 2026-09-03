/**
 * Give HA's custom-panel container a definite height, and hand it back.
 *
 * `ha-panel-custom` renders us into its light DOM with `display: block`,
 * `box-sizing: border-box` and safe-area padding, and no height of its own.
 * Being a block box, it is the containing block that our `:host`
 * `height: 100%` resolves against, and a percentage of `auto` is `auto`: the
 * shell then takes the height of whatever the active tab renders. That clips
 * everything positioned against the shell — the app menu, the conversations
 * drawer — at the bottom of the tab's content, so a long Automations list
 * hides the clip while the welcome screen shows it plainly.
 *
 * The container's own containing block (HA's drawer content) IS sized to the
 * viewport, so handing the container `height: 100%` puts a definite chain
 * back. On HA builds that leave the container `display: inline` the
 * declaration has no layout effect, and a height HA sets itself is never
 * overwritten — so this only ever adds the missing link.
 *
 * One `ha-panel-custom` serves EVERY custom panel: HA keeps the element and
 * swaps its child, which is what `_cleanupPanel()`/`_createPanel()` are for.
 * A height left behind therefore follows the next panel in and constrains a
 * container it means to grow, so `releasePanelContainer()` gives it back on
 * disconnect — including the suspend/resume cycle, where the reconnect
 * re-applies it.
 */

/** Containers this module sized, so it never clears one it did not set. */
const sized = new WeakSet();

/**
 * @param {Element | null | undefined} container the panel's parent element
 * @returns {boolean} whether a height was applied
 */
export function sizePanelContainer(container) {
  if (!container || container.localName !== "ha-panel-custom") return false;
  // A height HA declares itself is the authority — don't fight it.
  if (container.style?.height) return false;
  container.style.height = "100%";
  sized.add(container);
  return true;
}

/**
 * @param {Element | null | undefined} container the container passed to
 *   `sizePanelContainer` — read before removal, since a disconnected element
 *   has no parent left to ask.
 * @returns {boolean} whether a height was cleared
 */
export function releasePanelContainer(container) {
  if (!container || !sized.has(container)) return false;
  sized.delete(container);
  // Anything else on it now came from HA (or a later panel) and is not ours
  // to drop.
  if (container.style?.height !== "100%") return false;
  container.style.height = "";
  return true;
}
