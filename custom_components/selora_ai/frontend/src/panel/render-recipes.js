// Recipes tab (v2 — deterministic pipeline).
//
// Three views, one host element, switched by ``host._recipesView``:
//
//   "list"      — Available + Installed cards.
//   "wizard"    — Inputs form, role-resolution preview, Install button.
//   "result"    — Final outcome: success card or punch list.
//
// The wizard never starts a chat session — every action is a direct WS
// call against the recipes pipeline. The chat tab is unrelated to
// recipe installs in this flow.

import { html } from "lit";
import { unsafeHTML } from "lit/directives/unsafe-html.js";

const _ACCEPTED_SUFFIXES = [".tar.gz", ".tgz", ".zip"];

// ── Entity presentation helpers ────────────────────────────────────
//
// Chips show a domain icon + the entity's friendly name. Reading
// "Bed Light" is faster than parsing "light.bed_light"; the domain
// icon gives a second cue for what kind of device it is.

const _DOMAIN_ICON = {
  light: "mdi:lightbulb",
  switch: "mdi:toggle-switch",
  cover: "mdi:garage",
  lock: "mdi:lock",
  climate: "mdi:thermostat",
  fan: "mdi:fan",
  media_player: "mdi:speaker",
  camera: "mdi:cctv",
  vacuum: "mdi:robot-vacuum",
  binary_sensor: "mdi:gesture-tap-button",
  sensor: "mdi:gauge",
  input_boolean: "mdi:toggle-switch-outline",
  input_number: "mdi:counter",
  input_text: "mdi:form-textbox",
  input_select: "mdi:format-list-bulleted",
  person: "mdi:account",
  device_tracker: "mdi:map-marker",
  zone: "mdi:map",
};
// device_class refinement — overrides the bare-domain icon when the
// entity's role is more specific. Most useful for sensor /
// binary_sensor which can mean a dozen different things.
const _DEVICE_CLASS_ICON = {
  moisture: "mdi:water-alert",
  water: "mdi:water-alert",
  door: "mdi:door",
  window: "mdi:window-open-variant",
  motion: "mdi:motion-sensor",
  smoke: "mdi:smoke-detector",
  temperature: "mdi:thermometer",
  humidity: "mdi:water-percent",
  battery: "mdi:battery",
  power: "mdi:flash",
  illuminance: "mdi:brightness-5",
};

function _domainOf(entityId) {
  const dot = entityId.indexOf(".");
  return dot > 0 ? entityId.slice(0, dot) : "";
}

function _entityIcon(hass, entityId) {
  const state = hass?.states?.[entityId];
  if (state?.attributes?.icon) return state.attributes.icon;
  const dc = state?.attributes?.device_class;
  if (dc && _DEVICE_CLASS_ICON[dc]) return _DEVICE_CLASS_ICON[dc];
  return _DOMAIN_ICON[_domainOf(entityId)] || "mdi:cube-outline";
}

function _entityFriendlyName(hass, entityId) {
  // Prefer the homeowner-set ``friendly_name``; fall back to the
  // entity's bare object_id (``light.bed_light`` → ``bed_light``)
  // when no name has been configured.
  const state = hass?.states?.[entityId];
  const name = state?.attributes?.friendly_name;
  const objectId = entityId.includes(".")
    ? entityId.slice(entityId.indexOf(".") + 1)
    : entityId;
  return name || objectId.replace(/_/g, " ");
}

// Tiny line-based YAML highlighter. We don't need a full parser — the
// preview is generated output, not user-edited code, so the shape is
// predictable: comments, mapping keys, scalar values, list items,
// Jinja-style ``{{ … }}`` interpolations for HA runtime templates.
// The output is HTML escaped first, then wrapped in spans the
// stylesheet's ``.yaml-preview .yk``/``.ys``/etc. selectors paint.
const _YAML_ESCAPE_RE = /[&<>]/g;
const _YAML_ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;" };
function _escape(s) {
  return s.replace(_YAML_ESCAPE_RE, (c) => _YAML_ESC[c]);
}

function _highlightYamlValue(rest) {
  // ``rest`` is the bytes after the ``key:`` on a mapping line, or the
  // whole content after the leading dash on a list line. We tokenise:
  // HA templates first (they win over quotes), then quoted strings,
  // then numbers / booleans, then bare strings.
  const out = [];
  let i = 0;
  while (i < rest.length) {
    // ``{{ … }}`` — Home Assistant runtime template. Don't try to
    // colour inside; just paint the whole expression.
    if (rest[i] === "{" && rest[i + 1] === "{") {
      const end = rest.indexOf("}}", i + 2);
      if (end !== -1) {
        out.push(`<span class="yp">${_escape(rest.slice(i, end + 2))}</span>`);
        i = end + 2;
        continue;
      }
    }
    // Quoted string.
    if (rest[i] === '"' || rest[i] === "'") {
      const quote = rest[i];
      let end = i + 1;
      while (end < rest.length && rest[end] !== quote) end++;
      out.push(`<span class="ys">${_escape(rest.slice(i, end + 1))}</span>`);
      i = end + 1;
      continue;
    }
    out.push(_escape(rest[i]));
    i++;
  }
  // Now scan the accumulated text for unquoted booleans / numbers /
  // null when the whole rest is one bare token (no spaces, didn't
  // contain a quote / template). Simpler post-pass.
  let joined = out.join("");
  // Numbers + booleans + null only when the value is the entire rest
  // (i.e. no inline structure). Test against the raw rest, then
  // re-escape if matched.
  const bareValue = rest.trim();
  if (/^-?\d+(?:\.\d+)?$/.test(bareValue)) {
    joined = joined.replace(
      _escape(bareValue),
      `<span class="yn">${_escape(bareValue)}</span>`,
    );
  } else if (
    bareValue === "true" ||
    bareValue === "false" ||
    bareValue === "null" ||
    bareValue === "~"
  ) {
    joined = joined.replace(
      _escape(bareValue),
      `<span class="yb">${_escape(bareValue)}</span>`,
    );
  }
  return joined;
}

function _highlightYaml(text) {
  if (!text) return "";
  return text
    .split("\n")
    .map((line) => {
      // Whole-line comment.
      const commentIdx = line.indexOf("#");
      const indent = line.match(/^\s*/)[0];
      const body = line.slice(indent.length);

      if (body.startsWith("#")) {
        return _escape(indent) + `<span class="yc">${_escape(body)}</span>`;
      }
      // ``- value`` list item: tag the dash and recurse on the rest.
      if (body.startsWith("- ") || body === "-") {
        const rest = body.slice(2);
        return (
          _escape(indent) +
          `<span class="yd">-</span> ` +
          _highlightYamlValue(rest)
        );
      }
      // ``key: value`` mapping.
      const m = body.match(/^([^\s:#][^:]*?):(\s*)(.*)$/);
      if (m) {
        const [, key, sp, val] = m;
        // Trailing comment on the value side (not in a string).
        let valOut = val;
        let trailing = "";
        const cIdx = val.indexOf("#");
        if (cIdx !== -1 && val.slice(0, cIdx).split("'").length % 2 === 1) {
          valOut = val.slice(0, cIdx).trimEnd();
          trailing =
            " " + `<span class="yc">${_escape(val.slice(cIdx))}</span>`;
        }
        return (
          _escape(indent) +
          `<span class="yk">${_escape(key)}</span>:${sp}` +
          (valOut ? _highlightYamlValue(valOut) : "") +
          trailing
        );
      }
      return _escape(line);
    })
    .join("\n");
}

function _hasAcceptedSuffix(name) {
  const lower = (name || "").toLowerCase();
  return _ACCEPTED_SUFFIXES.some((s) => lower.endsWith(s));
}

const _STYLE = html`
  <style>
    /* Type scale. The --selora-fs-* tokens are referenced throughout
       this stylesheet but were never defined globally, so every
       size silently fell back to the same inherited body size and
       the visual hierarchy collapsed (chips looked the same size as
       prose, "NEEDS YOU" pills looked too big). Defining them once
       at the wizard's outermost containers cascades the scale to
       every descendant. */
    .recipes-root,
    .scroll-view {
      --selora-fs-micro: 10px;
      --selora-fs-xs: 12px;
      --selora-fs-sm: 13px;
      --selora-fs-md: 14px;
      --selora-fs-md-lg: 15px;
      --selora-fs-lg: 17px;
      --selora-fs-xl: 20px;
      --selora-fs-2xl: 24px;
      --selora-fs-3xl: 30px;
    }
    .recipes-root {
      display: flex;
      flex-direction: column;
      gap: 18px;
      max-width: 920px;
    }
    .install-card {
      display: flex;
      flex-direction: column;
      gap: 16px;
      padding: 20px;
      border: 1px solid var(--divider-color);
      border-top: none;
      border-radius: 0 0 14px 14px;
      background: var(--card-background-color);
    }
    /* Secondary "add a recipe from elsewhere" affordance. Collapsed by
       default so the catalog is the focus; expands on click. */
    .install-disclosure {
      border: 1px solid var(--divider-color);
      border-radius: 14px;
      background: var(--card-background-color);
    }
    .install-disclosure[open] {
      background: transparent;
    }
    .install-disclosure-summary {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 16px 20px;
      cursor: pointer;
      list-style: none;
      user-select: none;
      font-size: var(--selora-fs-md-lg);
      font-weight: 600;
      color: var(--primary-text-color);
    }
    .install-disclosure-summary::-webkit-details-marker {
      display: none;
    }
    .install-disclosure-summary > ha-icon:first-child {
      --mdc-icon-size: 18px;
      color: var(--selora-accent);
    }
    .install-disclosure-hint {
      font-size: var(--selora-fs-sm);
      font-weight: 400;
      color: var(--secondary-text-color);
    }
    .install-disclosure-chevron {
      --mdc-icon-size: 20px;
      color: var(--secondary-text-color);
      margin-left: auto;
      transition: transform 150ms ease;
    }
    .install-disclosure[open] .install-disclosure-chevron {
      transform: rotate(180deg);
    }
    @media (max-width: 640px) {
      .install-disclosure-hint {
        display: none;
      }
      /* Stack the card body above its actions so the title/description get
         the full width instead of being squeezed into a sliver beside the
         buttons. */
      .recipe-card-row {
        flex-direction: column;
        align-items: stretch;
      }
      .recipe-card-actions {
        flex-wrap: wrap;
      }
    }
    /* Same stacking when HA reports the panel itself as narrow (side panel),
       independent of viewport width. */
    :host([narrow]) .recipe-card-row {
      flex-direction: column;
      align-items: stretch;
    }
    :host([narrow]) .recipe-card-actions {
      flex-wrap: wrap;
    }
    /* Local-prefixed so this doesn't pick up the panel toolbar's
       golden gradient line — that one is keyed on the global .header
       class and was leaking into this card's title block. */
    .install-card-header {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .install-card-title {
      font-size: var(--selora-fs-lg);
      font-weight: 700;
      color: var(--primary-text-color);
    }
    .install-card-subtitle {
      font-size: var(--selora-fs-sm);
      color: var(--secondary-text-color);
      line-height: 1.5;
    }
    .install-source-label {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: var(--selora-fs-xs);
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--secondary-text-color);
      margin-bottom: 6px;
    }
    .install-source-label ha-icon {
      --mdc-icon-size: 16px;
      color: var(--selora-accent);
    }
    .install-url-row {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .install-url-input {
      flex: 1;
      padding: 10px 12px;
      border: 1px solid var(--selora-inner-card-border);
      border-radius: 10px;
      background: var(--selora-inner-card-bg);
      color: var(--primary-text-color);
      font-size: var(--selora-fs-md);
    }
    .install-url-input:focus {
      outline: none;
      border-color: var(--selora-accent);
    }
    .install-or-divider {
      display: flex;
      align-items: center;
      gap: 12px;
      margin: 2px 0;
    }
    .install-or-divider::before,
    .install-or-divider::after {
      content: "";
      flex: 1;
      height: 1px;
      background: var(--divider-color);
    }
    .install-or-text {
      font-size: var(--selora-fs-micro);
      font-weight: 700;
      letter-spacing: 0.18em;
      color: var(--secondary-text-color);
    }
    .install-dropzone {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 8px;
      padding: 28px 20px;
      border: 1.5px dashed var(--selora-inner-card-border);
      border-radius: 10px;
      background: var(--selora-inner-card-bg);
      text-align: center;
      cursor: pointer;
      transition:
        border-color 120ms ease,
        background 120ms ease,
        transform 120ms ease;
    }
    .install-dropzone:hover {
      border-color: var(--selora-accent);
      background: color-mix(
        in srgb,
        var(--selora-accent) 6%,
        var(--selora-inner-card-bg)
      );
    }
    .install-dropzone.is-drag {
      border-style: solid;
      border-color: var(--selora-accent);
      background: color-mix(
        in srgb,
        var(--selora-accent) 14%,
        var(--selora-inner-card-bg)
      );
      transform: scale(1.005);
    }
    .install-dropzone.is-busy {
      cursor: progress;
      opacity: 0.75;
    }
    .install-dropzone-icon {
      --mdc-icon-size: 30px;
      color: var(--selora-accent);
    }
    .install-dropzone-title {
      font-size: var(--selora-fs-md-lg);
      font-weight: 600;
      color: var(--primary-text-color);
    }
    .install-dropzone-hint {
      font-size: var(--selora-fs-xs);
      color: var(--secondary-text-color);
    }
    .install-dropzone-hint code {
      background: color-mix(in srgb, var(--primary-text-color) 8%, transparent);
      padding: 1px 5px;
      border-radius: 3px;
      font-size: 10.5px;
    }
    .install-error {
      font-size: var(--selora-fs-sm);
      color: var(--error-color, #c62828);
      padding: 10px 12px;
      background: color-mix(
        in srgb,
        var(--error-color, #c62828) 10%,
        transparent
      );
      border: 1px solid
        color-mix(in srgb, var(--error-color, #c62828) 28%, transparent);
      border-radius: 8px;
    }
    .recipes-section-title {
      font-size: var(--selora-fs-md);
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--secondary-text-color);
    }
    .recipes-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .recipes-h1 {
      font-size: var(--selora-fs-3xl);
      font-weight: 700;
      color: var(--primary-text-color);
    }
    .recipes-empty {
      padding: 22px;
      text-align: center;
      font-size: var(--selora-fs-md);
      color: var(--secondary-text-color);
      border: 1px dashed var(--divider-color);
      border-radius: 12px;
    }
    .recipe-card {
      display: flex;
      flex-direction: column;
      gap: 14px;
      padding: 16px 18px;
      border: 1px solid var(--divider-color);
      border-radius: 12px;
      background: var(--card-background-color);
    }
    .recipe-card-row {
      display: flex;
      gap: 16px;
      align-items: flex-start;
    }
    .recipe-card-body {
      flex: 1;
      min-width: 0;
    }
    /* Expandable "what got installed" panel. Native <details> so it
       needs no panel state; the summary row is the toggle. */
    .recipe-details {
      border-top: 1px solid var(--divider-color);
      margin-top: 2px;
    }
    .recipe-details-summary {
      display: flex;
      align-items: center;
      gap: 6px;
      padding-top: 12px;
      list-style: none;
      cursor: pointer;
      user-select: none;
      font-size: var(--selora-fs-sm);
      font-weight: 600;
      color: var(--selora-accent);
    }
    .recipe-details-summary::-webkit-details-marker {
      display: none;
    }
    .recipe-details-summary ha-icon {
      --mdc-icon-size: 16px;
      transition: transform 150ms ease;
    }
    /* Child combinator so an open parent doesn't rotate the nested
       "View package file" chevron too. */
    .recipe-details[open] > .recipe-details-summary ha-icon {
      transform: rotate(180deg);
    }
    .recipe-package-view {
      margin-top: 8px;
    }
    .recipe-package-view[open] > .recipe-details-summary ha-icon {
      transform: rotate(180deg);
    }
    .recipe-package-view .yaml-preview {
      margin-top: 8px;
      max-height: 340px;
      overflow: auto;
    }
    .recipe-details-grid {
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 10px 18px;
      padding: 14px 2px 4px;
      align-items: start;
    }
    .recipe-details-key {
      font-size: var(--selora-fs-xs);
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--secondary-text-color);
      padding-top: 2px;
    }
    .recipe-details-val {
      font-size: var(--selora-fs-sm);
      color: var(--primary-text-color);
      line-height: 1.5;
      min-width: 0;
    }
    .recipe-details-path {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .recipe-details-path code {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: var(--selora-fs-xs);
      background: var(--secondary-background-color);
      border: 1px solid var(--divider-color);
      border-radius: 6px;
      padding: 3px 7px;
      word-break: break-all;
    }
    .recipe-details-copy {
      background: none;
      border: none;
      cursor: pointer;
      color: var(--secondary-text-color);
      padding: 2px;
      display: inline-flex;
    }
    .recipe-details-copy:hover {
      color: var(--selora-accent);
    }
    .recipe-details-copy ha-icon {
      --mdc-icon-size: 15px;
    }
    .recipe-details-binding {
      display: flex;
      flex-direction: column;
      gap: 2px;
      margin-bottom: 6px;
    }
    .recipe-details-binding:last-child {
      margin-bottom: 0;
    }
    .recipe-details-role {
      font-size: var(--selora-fs-xs);
      color: var(--secondary-text-color);
    }
    .recipe-details-entities {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .recipe-details-chip {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 3px 9px;
      border-radius: 999px;
      background: var(--secondary-background-color);
      border: 1px solid var(--divider-color);
      font-size: var(--selora-fs-xs);
      color: var(--primary-text-color);
    }
    .recipe-details-chip ha-icon {
      --mdc-icon-size: 13px;
      color: var(--selora-accent);
    }
    .recipe-details-empty {
      color: var(--secondary-text-color);
      font-style: italic;
    }
    .recipe-card-title {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
      font-size: var(--selora-fs-lg);
      font-weight: 700;
      color: var(--primary-text-color);
      margin-bottom: 2px;
    }
    .recipe-card-meta {
      font-size: var(--selora-fs-xs);
      color: var(--secondary-text-color);
    }
    .recipe-card-desc {
      font-size: var(--selora-fs-md);
      color: var(--primary-text-color);
      margin-top: 8px;
      line-height: 1.45;
    }
    /* Installed cards are compact: the description lives inside the
       expandable Details panel rather than always-on in the card body. */
    .recipe-details-desc {
      margin: 4px 0 12px;
    }
    .recipe-card-actions {
      display: flex;
      gap: 8px;
      flex-shrink: 0;
    }
    /* Recipe-list cards pack several action buttons in a row; the
       shared .btn rule doesn't constrain ha-icon size, so an icon
       button (24px MDC default) ends up visibly taller than a
       text-only one. Pin a consistent height and cap the icon glyph
       to text size. */
    .recipe-card-actions .btn {
      min-height: 38px;
      min-width: 104px;
      justify-content: center;
      padding-top: 8px;
      padding-bottom: 8px;
    }
    .recipe-card-actions .btn ha-icon {
      --mdc-icon-size: 16px;
    }
    /* Catalog (CDN-fetched recipes) — distinct visual treatment from
       locally-bundled "Available" recipes so users can tell them
       apart. Grid of cards with a search box at the top. */
    .catalog-section {
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .icon-spin {
      animation: pi-spin 1s linear infinite;
    }
    .recipes-intro {
      margin: -6px 0 4px;
      font-size: var(--selora-fs-md);
      color: var(--secondary-text-color);
      line-height: 1.6;
      max-width: 70ch;
    }
    .catalog-controls {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .catalog-override-banner {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 8px;
      background: color-mix(in srgb, #06b6d4 14%, transparent);
      color: #06b6d4;
      font-size: 12px;
    }
    .catalog-override-banner ha-icon {
      --mdc-icon-size: 14px;
    }
    .catalog-override-banner code {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      color: var(--primary-text-color);
    }
    .catalog-loading,
    .catalog-error {
      padding: 18px;
      border: 1px dashed var(--divider-color);
      border-radius: 10px;
      background: var(--secondary-background-color);
      color: var(--secondary-text-color);
      font-size: 13px;
    }
    .catalog-error {
      display: flex;
      align-items: center;
      gap: 10px;
      border-color: color-mix(in srgb, #ef4444 32%, transparent);
      color: #ef4444;
    }
    .catalog-error ha-icon {
      --mdc-icon-size: 18px;
    }
    .catalog-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 16px;
      align-items: stretch;
    }
    /* Featured strip: the prominent "highlighted" cards. With 2 items this
       lays out 2-across on a wide panel and stacks on narrow. */
    .catalog-grid-featured {
      grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
    }
    .catalog-card-featured {
      border-color: color-mix(
        in srgb,
        var(--selora-accent) 35%,
        var(--divider-color)
      );
    }
    .catalog-pagination {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 14px;
      margin-top: 18px;
    }
    .catalog-page-indicator {
      font-size: 13px;
      color: var(--secondary-text-color);
      min-width: 84px;
      text-align: center;
    }
    .catalog-card {
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding: 20px;
      border: 1px solid var(--divider-color);
      border-radius: 14px;
      background: var(--card-background-color);
      transition:
        border-color 140ms ease,
        transform 140ms ease,
        box-shadow 140ms ease;
    }
    .catalog-card:hover {
      border-color: color-mix(in srgb, var(--selora-accent) 50%, transparent);
      transform: translateY(-2px);
      box-shadow: 0 6px 20px
        color-mix(in srgb, var(--selora-accent) 10%, transparent);
    }
    .catalog-card-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }
    .catalog-card-icon {
      width: 44px;
      height: 44px;
      border-radius: 11px;
      display: flex;
      align-items: center;
      justify-content: center;
      background: color-mix(in srgb, var(--selora-accent) 16%, transparent);
    }
    .catalog-card-icon ha-icon {
      --mdc-icon-size: 24px;
      color: var(--selora-accent);
    }
    .catalog-card-title {
      font-size: 17px;
      font-weight: 700;
      line-height: 1.25;
      color: var(--primary-text-color);
      margin-top: 2px;
    }
    .catalog-card-category {
      flex-shrink: 0;
      padding: 3px 9px;
      border-radius: 999px;
      background: color-mix(in srgb, #06b6d4 16%, transparent);
      color: #06b6d4;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    .catalog-card-meta {
      font-size: 11px;
      color: var(--secondary-text-color);
    }
    .catalog-card-desc {
      font-size: 13px;
      color: var(--secondary-text-color);
      line-height: 1.55;
      display: -webkit-box;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
    .catalog-card-tags {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
    }
    .catalog-tag {
      padding: 2px 8px;
      border-radius: 999px;
      background: var(--secondary-background-color);
      color: var(--secondary-text-color);
      font-size: 11px;
    }
    .catalog-card-actions {
      display: flex;
      justify-content: flex-end;
      margin-top: auto;
      padding-top: 8px;
    }
    .catalog-install-btn {
      width: 100%;
      justify-content: center;
    }
    .catalog-installed-badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 4px 10px;
      border-radius: 999px;
      background: color-mix(
        in srgb,
        var(--success-color, #2e7d32) 16%,
        transparent
      );
      color: var(--success-color, #2e7d32);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .catalog-installed-badge ha-icon {
      --mdc-icon-size: 12px;
    }
    .recipe-installed-badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 2px 8px;
      border-radius: 999px;
      background: color-mix(
        in srgb,
        var(--success-color, #2e7d32) 14%,
        transparent
      );
      color: var(--success-color, #2e7d32);
      font-size: var(--selora-fs-micro);
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    /* Surfaces an in-progress wizard draft for an uninstalled recipe.
       Uses a cool blue rather than the brand amber so it doesn't get
       confused with the "Installed" badge — they communicate very
       different things. */
    .recipe-draft-badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 2px 8px;
      border-radius: 999px;
      background: color-mix(in srgb, #06b6d4 16%, transparent);
      color: #06b6d4;
      font-size: var(--selora-fs-micro);
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .wizard-back {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--selora-accent);
      cursor: pointer;
      font-size: var(--selora-fs-sm);
      background: none;
      border: none;
      padding: 0;
      align-self: flex-start;
    }
    /* Two-pane layout: main content on the left, "What you need" on
       the right. Stacks on narrow viewports so the rail drops below
       the main pane. */
    .wizard-root {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      gap: 24px;
      align-items: start;
    }
    @media (max-width: 1100px) {
      .wizard-root {
        grid-template-columns: 1fr;
      }
    }
    .wizard-main {
      display: flex;
      flex-direction: column;
      gap: 20px;
      padding: 24px 28px;
      border: 1px solid var(--divider-color);
      border-radius: 14px;
      background: var(--card-background-color);
      min-width: 0;
    }
    .wizard-hero {
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .wizard-eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: var(--selora-fs-xs);
      font-weight: 700;
      letter-spacing: 0.1em;
      color: var(--selora-accent);
      text-transform: uppercase;
    }
    .wizard-eyebrow ha-icon {
      --mdc-icon-size: 16px;
    }
    .wizard-eyebrow-meta {
      color: var(--secondary-text-color);
      font-weight: 600;
      letter-spacing: 0.06em;
    }
    .wizard-hero {
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    /* Eyebrow row mirrors the website: small caps RECIPE pill in
       amber, version + released date in muted grey. */
    .wizard-eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--secondary-text-color);
    }
    .wizard-eyebrow ha-icon {
      --mdc-icon-size: 14px;
      color: var(--selora-accent);
    }
    .wizard-eyebrow-tag {
      color: var(--selora-accent);
    }
    .wizard-eyebrow-sep {
      opacity: 0.5;
    }
    .wizard-eyebrow-meta {
      letter-spacing: 0.08em;
    }
    .wizard-hero-title {
      /* Concrete fallback for the undefined --selora-fs token. Sized
         to match the website detail-page hero so the wizard reads as
         the same product surface. */
      font-size: 38px;
      font-weight: 800;
      color: var(--primary-text-color);
      line-height: 1.1;
      letter-spacing: -0.02em;
      margin: 4px 0 0;
    }
    .wizard-hero-tagline {
      font-size: 16px;
      color: var(--secondary-text-color);
      line-height: 1.5;
      max-width: 60ch;
    }
    .wizard-hero-description {
      font-size: 14px;
      color: var(--primary-text-color);
      line-height: 1.65;
      max-width: 70ch;
      padding-top: 8px;
      border-top: 1px solid var(--divider-color);
    }
    /* First tag (a thematic "primary" category like Safety / Routine)
       lights up in amber with a bookmark icon, matching the website's
       detail-page hero. The rest stay muted to read as secondary tags. */
    .wizard-tag {
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }
    .wizard-tag ha-icon {
      --mdc-icon-size: 12px;
    }
    .wizard-tag.primary {
      background: color-mix(in srgb, var(--selora-accent) 14%, transparent);
      border-color: color-mix(in srgb, var(--selora-accent) 40%, transparent);
      color: var(--selora-accent);
    }
    .wizard-hero-tags {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 4px;
    }
    .wizard-tag {
      padding: 3px 10px;
      border-radius: 999px;
      background: var(--secondary-background-color);
      border: 1px solid var(--divider-color);
      color: var(--secondary-text-color);
      font-size: var(--selora-fs-xs);
      font-weight: 600;
    }
    .wizard-tag.primary {
      background: color-mix(in srgb, var(--selora-accent) 14%, transparent);
      border-color: var(--selora-accent);
      color: var(--selora-accent);
    }
    .wizard-prose {
      font-size: var(--selora-fs-md);
      color: var(--primary-text-color);
      line-height: 1.65;
    }
    .wizard-section-title {
      margin: 0 0 12px;
      font-size: var(--selora-fs-md);
      font-weight: 700;
      color: var(--primary-text-color);
    }
    .wizard-inputs {
      display: flex;
      flex-direction: column;
    }
    .wizard-action-footer {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding-top: 14px;
      border-top: 1px solid var(--divider-color);
    }
    .wizard-action-footer-status {
      font-size: var(--selora-fs-sm);
      color: var(--secondary-text-color);
    }
    /* Wizard layout: main content on the left, vertical progress
       rail on the right. min-width:0 on the main column prevents
       intrinsic-content sizing from leaking up (without it, a wide
       chip row inside the detail panel would push the table wider). */
    .wizard-root {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 240px;
      gap: 24px;
      align-items: start;
      width: 100%;
    }
    @media (max-width: 900px) {
      .wizard-root {
        grid-template-columns: 1fr;
      }
    }
    .wizard-main {
      display: flex;
      flex-direction: column;
      gap: 20px;
      min-width: 0;
      max-width: 100%;
    }
    .wizard-main > * {
      min-width: 0;
      max-width: 100%;
      box-sizing: border-box;
    }
    /* Step 1 (Overview) gets a wider right column for the
       "What you need" rail; the per-requirement cards have prose
       that needs room. The other steps use the slimmer rail width
       set by the default grid-template-columns. */
    .wizard-root-overview {
      grid-template-columns: minmax(0, 1fr) 340px;
    }
    /* On Step 1 the hero is the page itself — no card chrome around
       it. The only card on this screen is the "What you need" rail
       on the right. Matches the selorahomes.com detail-page layout
       where the hero reads as bare text and the buttons sit free
       below it. */
    .wizard-root-overview .wizard-header {
      border: none;
      background: transparent;
      padding: 0 4px;
    }
    /* Step heading rendered at the top of each step's body. Gives the
       user a clear answer to "which step am I on and what's expected
       of me" without needing to glance at the Progress rail. */
    .step-heading {
      display: flex;
      flex-direction: column;
      gap: 4px;
      margin: 4px 0 2px;
    }
    .step-heading-eyebrow {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--secondary-text-color);
    }
    .step-heading-num {
      color: var(--secondary-text-color);
    }
    .step-heading-required,
    .step-heading-optional {
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 10px;
      letter-spacing: 0.1em;
    }
    .step-heading-required {
      background: color-mix(in srgb, var(--selora-accent) 14%, transparent);
      color: var(--selora-accent);
    }
    .step-heading-optional {
      background: var(--secondary-background-color);
      color: var(--secondary-text-color);
    }
    .step-heading-title {
      margin: 0;
      font-size: 22px;
      font-weight: 700;
      color: var(--primary-text-color);
      letter-spacing: -0.01em;
      line-height: 1.2;
    }
    .step-heading-sub {
      margin: 2px 0 0;
      font-size: 14px;
      color: var(--secondary-text-color);
      line-height: 1.55;
      max-width: 64ch;
    }

    /* Compact header for Steps 2-5: one-line strip with back arrow +
       recipe title + version. Free of card chrome so the workspace
       below (table, buckets, etc.) is the visual focus. Specificity
       bump (.wizard-header.wizard-header-compact) so we override the
       base .wizard-header rules that live later in this stylesheet —
       otherwise the card border, background, and column flex direction
       leak back in. */
    .wizard-header.wizard-header-compact {
      display: flex;
      flex-direction: row;
      align-items: center;
      gap: 14px;
      padding: 6px 4px 14px;
      border: none;
      background: transparent;
      border-bottom: 1px solid var(--divider-color);
      border-radius: 0;
      margin-bottom: 4px;
    }
    .wizard-back-compact {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 32px;
      height: 32px;
      border-radius: 8px;
      border: 1px solid var(--divider-color);
      background: transparent;
      color: var(--primary-text-color);
      cursor: pointer;
      flex-shrink: 0;
      transition: border-color 120ms ease;
    }
    .wizard-back-compact:hover {
      border-color: var(--selora-accent);
      color: var(--selora-accent);
    }
    .wizard-back-compact ha-icon {
      --mdc-icon-size: 18px;
    }
    .wizard-compact-meta {
      display: flex;
      align-items: baseline;
      gap: 10px;
      min-width: 0;
    }
    .wizard-compact-title {
      font-size: 18px;
      font-weight: 700;
      color: var(--primary-text-color);
      letter-spacing: -0.01em;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .wizard-compact-version {
      font-size: 12px;
      color: var(--secondary-text-color);
      letter-spacing: 0.04em;
    }
    /* "What you need" rail — one card per integration + role. */
    .need-rail {
      display: flex;
      flex-direction: column;
      gap: 12px;
      padding: 20px;
      border: 1px solid var(--divider-color);
      border-radius: 14px;
      background: var(--card-background-color);
    }
    @media (max-width: 900px) {
      .need-rail {
        position: static;
      }
    }
    .need-rail-title {
      font-size: 17px;
      font-weight: 700;
      color: var(--primary-text-color);
      margin-bottom: 6px;
    }
    .need-rail-list {
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .need-rail-eyebrow {
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--secondary-text-color);
      margin-top: 8px;
    }
    .need-card {
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 12px;
      padding: 14px;
      border: 1px solid var(--divider-color);
      border-radius: 12px;
      background: var(--secondary-background-color);
    }
    .need-card-icon {
      width: 36px;
      height: 36px;
      border-radius: 9px;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }
    .need-card-icon ha-icon {
      --mdc-icon-size: 20px;
    }
    /* Category palette for "What you need" cards. Mirrors the
       selorahomes.com detail page: each kind of requirement has its
       own hue so the homeowner can scan-distinguish them quickly. */
    .need-card-icon--integration {
      background: color-mix(in srgb, var(--selora-accent) 18%, transparent);
    }
    .need-card-icon--integration ha-icon {
      color: var(--selora-accent);
    }
    .need-card-icon--role {
      background: color-mix(in srgb, #06b6d4 18%, transparent);
    }
    .need-card-icon--role ha-icon {
      color: #06b6d4;
    }
    .need-card-icon--pin {
      background: color-mix(in srgb, #10b981 18%, transparent);
    }
    .need-card-icon--pin ha-icon {
      color: #10b981;
    }
    .need-card-icon--region {
      background: color-mix(in srgb, #f43f5e 18%, transparent);
    }
    .need-card-icon--region ha-icon {
      color: #f43f5e;
    }
    .need-card-body {
      min-width: 0;
    }
    .need-card-title {
      font-size: 14px;
      font-weight: 700;
      color: var(--primary-text-color);
      line-height: 1.3;
    }
    .need-card-meta {
      font-size: 11px;
      color: var(--secondary-text-color);
      margin-top: 2px;
      letter-spacing: 0.02em;
    }
    .need-card-desc {
      font-size: 12px;
      color: var(--secondary-text-color);
      line-height: 1.5;
      margin-top: 4px;
    }

    /* Vertical step rail */
    .step-rail {
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding: 18px 16px;
      border: 1px solid var(--divider-color);
      border-radius: 14px;
      background: var(--card-background-color);
      position: sticky;
      top: 12px;
    }
    @media (max-width: 900px) {
      .step-rail {
        position: static;
      }
    }
    .step-rail-title {
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--secondary-text-color);
      padding-left: 8px;
    }
    .step-rail-list {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .step-rail-row {
      display: grid;
      grid-template-columns: auto auto 1fr;
      gap: 10px;
      align-items: center;
      padding: 10px 12px;
      border-radius: 8px;
      border: none;
      background: transparent;
      color: var(--secondary-text-color);
      font-family: inherit;
      font-size: 14px;
      font-weight: 600;
      text-align: left;
      cursor: default;
    }
    .step-rail-row:not([disabled]) {
      cursor: pointer;
    }
    .step-rail-row:not([disabled]):hover {
      background: var(--secondary-background-color);
    }
    .step-rail-icon {
      --mdc-icon-size: 18px;
      color: var(--secondary-text-color);
    }
    .step-rail-num {
      width: 18px;
      text-align: center;
      font-variant-numeric: tabular-nums;
      font-size: 12px;
      opacity: 0.7;
    }
    .step-rail-label {
      color: var(--secondary-text-color);
    }
    .step-current {
      background: color-mix(in srgb, var(--selora-accent) 12%, transparent);
    }
    .step-current .step-rail-icon,
    .step-current .step-rail-label,
    .step-current .step-rail-num {
      color: var(--selora-accent);
      opacity: 1;
    }
    .step-done .step-rail-icon {
      color: var(--success-color, #2e7d32);
    }
    .step-done .step-rail-label,
    .step-done .step-rail-num {
      color: var(--primary-text-color);
      opacity: 0.85;
    }
    .step-future .step-rail-icon,
    .step-future .step-rail-label,
    .step-future .step-rail-num {
      opacity: 0.5;
    }
    .wizard-header {
      display: flex;
      flex-direction: column;
      gap: 14px;
      padding: 32px 36px 30px;
      border: 1px solid var(--divider-color);
      border-radius: 16px;
      background: var(--card-background-color);
      box-sizing: border-box;
    }
    .pi-board {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
    }
    @media (max-width: 1000px) {
      .pi-board {
        grid-template-columns: 1fr;
      }
    }
    .pi-stage {
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding: 14px;
      border-radius: 12px;
      border: 1px solid var(--divider-color);
      background: var(--card-background-color);
      min-width: 0;
    }
    .pi-stage-head {
      font-size: var(--selora-fs-xs);
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--secondary-text-color);
    }
    .pi-stage-ok .pi-stage-head {
      color: var(--success-color, #2e7d32);
    }
    .pi-stage-failed .pi-stage-head {
      color: #ef4444;
    }
    .pi-stage-items {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .pi-row {
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 10px;
      align-items: center;
      padding: 10px 12px;
      border-radius: 8px;
      border: 1px solid transparent;
      background: var(--secondary-background-color);
      cursor: pointer;
      text-align: left;
      font-family: inherit;
      color: var(--primary-text-color);
      transition:
        background 120ms ease,
        border-color 120ms ease;
    }
    .pi-row:hover {
      border-color: var(--divider-color);
    }
    .pi-row.is-active {
      border-color: var(--selora-accent);
      background: color-mix(in srgb, var(--selora-accent) 10%, transparent);
    }
    .pi-status-icon {
      --mdc-icon-size: 18px;
      flex-shrink: 0;
    }
    .pi-row.pi-ok .pi-status-icon {
      color: var(--success-color, #2e7d32);
    }
    .pi-row.pi-failed .pi-status-icon {
      color: #ef4444;
    }
    .pi-row.pi-running .pi-status-icon {
      color: var(--selora-accent);
      animation: pi-spin 1.4s linear infinite;
    }
    .pi-row.pi-needs_input .pi-status-icon {
      color: var(--selora-accent);
    }
    .pi-row.pi-skipped .pi-status-icon {
      color: var(--secondary-text-color);
      opacity: 0.7;
    }
    .pi-row.pi-pending .pi-status-icon {
      color: var(--secondary-text-color);
      opacity: 0.55;
    }
    @keyframes pi-spin {
      from {
        transform: rotate(0deg);
      }
      to {
        transform: rotate(360deg);
      }
    }
    .pi-row-body {
      min-width: 0;
    }
    .pi-row-title {
      font-size: var(--selora-fs-sm);
      font-weight: 600;
      line-height: 1.3;
    }
    .pi-row-detail {
      font-size: var(--selora-fs-xs);
      color: var(--secondary-text-color);
      margin-top: 2px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .pi-row.pi-skipped .pi-row-title,
    .pi-row.pi-skipped .pi-row-detail {
      opacity: 0.6;
    }
    /* Action panel docked under the pipeline board. */
    .pi-action {
      min-height: 100px;
    }
    .panel-shell {
      display: flex;
      flex-direction: column;
      gap: 14px;
      padding: 20px 22px;
      border: 1px solid var(--divider-color);
      border-radius: 12px;
      background: var(--card-background-color);
    }
    .panel-head {
      display: flex;
      align-items: center;
      gap: 10px;
      justify-content: space-between;
    }
    .panel-title {
      font-size: var(--selora-fs-md-lg, 15px);
      font-weight: 700;
      color: var(--primary-text-color);
    }
    .panel-status {
      padding: 3px 8px;
      border-radius: 999px;
      font-size: var(--selora-fs-micro, 10px);
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    .panel-status.ok {
      background: color-mix(
        in srgb,
        var(--success-color, #2e7d32) 14%,
        transparent
      );
      color: var(--success-color, #2e7d32);
    }
    .panel-status.needs_input {
      background: color-mix(in srgb, var(--selora-accent) 14%, transparent);
      color: var(--selora-accent);
    }
    .panel-status.failed {
      background: color-mix(in srgb, #ef4444 14%, transparent);
      color: #ef4444;
    }
    .panel-status.running {
      background: color-mix(in srgb, var(--selora-accent) 14%, transparent);
      color: var(--selora-accent);
    }
    .panel-body {
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .panel-prose {
      margin: 0;
      font-size: var(--selora-fs-sm, 13px);
      color: var(--primary-text-color);
      line-height: 1.55;
    }
    .panel-muted {
      color: var(--secondary-text-color);
    }
    .panel-error {
      padding: 8px 12px;
      border-radius: 8px;
      background: color-mix(in srgb, #ef4444 12%, transparent);
      color: #ef4444;
      font-size: var(--selora-fs-sm);
    }
    .panel-fields {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .panel-field {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .panel-field-label {
      font-size: var(--selora-fs-sm);
      font-weight: 600;
      color: var(--primary-text-color);
    }
    .panel-field-optional {
      color: var(--secondary-text-color);
      font-weight: 400;
      font-style: normal;
      margin-left: 4px;
    }
    .panel-field-error {
      font-size: var(--selora-fs-xs);
      color: #ef4444;
    }
    .panel-field input,
    .panel-field select {
      padding: 8px 10px;
      border: 1px solid var(--selora-inner-card-border);
      border-radius: 10px;
      background: var(--selora-inner-card-bg);
      color: var(--primary-text-color);
      font-size: var(--selora-fs-md);
      font-family: inherit;
      transition: border-color 0.3s;
    }
    .panel-field input:focus,
    .panel-field select:focus {
      outline: none;
      border-color: var(--selora-accent);
    }
    .panel-field input[type="checkbox"] {
      width: 18px;
      height: 18px;
      align-self: flex-start;
    }
    .panel-chips {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      min-width: 0;
      max-width: 100%;
    }
    .role-filter-input {
      width: 100%;
      box-sizing: border-box;
      margin: 4px 0 10px;
      padding: 8px 11px;
      border: 1px solid var(--selora-inner-card-border);
      border-radius: 10px;
      background: var(--selora-inner-card-bg);
      color: var(--primary-text-color);
      font-size: var(--selora-fs-sm);
      font-family: inherit;
    }
    .role-filter-input:focus {
      outline: none;
      border-color: var(--selora-accent);
    }
    .role-show-more {
      margin-top: 10px;
      padding: 6px 12px;
      border: 1px solid var(--divider-color);
      border-radius: 8px;
      background: transparent;
      color: var(--selora-accent);
      font-size: var(--selora-fs-sm);
      font-weight: 600;
      font-family: inherit;
      cursor: pointer;
    }
    .role-show-more:hover {
      border-color: var(--selora-accent);
    }
    .panel-footer {
      display: flex;
      gap: 10px;
      justify-content: flex-end;
      padding-top: 4px;
      border-top: 1px solid var(--divider-color);
      padding-top: 12px;
    }
    .panel-btn {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 8px 16px;
      border-radius: 8px;
      font-size: var(--selora-fs-sm);
      font-weight: 600;
      cursor: pointer;
      transition: background 120ms ease;
      font-family: inherit;
    }
    .panel-btn ha-icon {
      --mdc-icon-size: 15px;
    }
    /* In-button spinner: sized to the label and tinted with the button's
       own text colour so it shows on both primary (dark-on-accent) and
       secondary buttons. */
    .panel-btn .spinner {
      width: 14px;
      height: 14px;
      border-width: 2px;
      border-color: color-mix(in srgb, currentColor 30%, transparent);
      border-top-color: currentColor;
    }
    .panel-btn.primary {
      background: var(--selora-accent);
      border: 1px solid var(--selora-accent);
      color: #000;
    }
    .panel-btn.primary:hover:not([disabled]) {
      background: var(--selora-accent-light, var(--selora-accent));
      border-color: var(--selora-accent-light, var(--selora-accent));
    }
    .panel-btn.secondary {
      background: transparent;
      border: 1px solid var(--divider-color);
      color: var(--primary-text-color);
    }
    .panel-btn.secondary:hover:not([disabled]) {
      border-color: var(--selora-accent);
    }
    .panel-btn[disabled] {
      opacity: 0.55;
      cursor: progress;
    }
    .install-fail-list {
      list-style: none;
      padding: 0;
      margin: 0;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .install-fail-list li {
      display: flex;
      gap: 10px;
      font-size: 13px;
      line-height: 1.5;
      color: var(--primary-text-color);
    }
    .install-fail-stage {
      display: inline-block;
      flex-shrink: 0;
      padding: 2px 8px;
      border-radius: 4px;
      background: color-mix(in srgb, #ef4444 18%, transparent);
      color: #ef4444;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      align-self: flex-start;
    }
    /* Empty-state help block shown in a role-selection panel when
       the home has zero matching candidates — instead of a curt "no
       devices" line, give the user a clear next action and tell
       them the wizard will auto-update when they pair something. */
    .role-empty-help {
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 14px;
      padding: 16px;
      border: 1px dashed var(--divider-color);
      border-radius: 12px;
      background: var(--secondary-background-color);
      width: 100%;
      box-sizing: border-box;
    }
    .role-empty-icon {
      --mdc-icon-size: 28px;
      color: var(--selora-accent);
      margin-top: 2px;
    }
    .role-empty-body {
      min-width: 0;
    }
    .role-empty-title {
      font-size: 14px;
      font-weight: 700;
      color: var(--primary-text-color);
      margin-bottom: 4px;
    }
    .role-empty-prose {
      margin: 0 0 12px;
      font-size: 13px;
      line-height: 1.55;
      color: var(--secondary-text-color);
    }
    .role-empty-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    /* Small detail line under an "integration is set up" panel —
       surfaces the actual config entry title so the user sees what
       got created (lat/lon for NWS, bridge ip for Hue, etc.) instead
       of an opaque "configured" badge. */
    .integration-entry-meta {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 13px;
      padding: 6px 10px;
      border: 1px solid var(--divider-color);
      border-radius: 8px;
      background: var(--secondary-background-color);
      width: fit-content;
    }
    .integration-entry-meta ha-icon {
      --mdc-icon-size: 14px;
      color: var(--secondary-text-color);
    }
    .integration-entry-meta code {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px;
      color: var(--primary-text-color);
    }
    /* ── 4-step wizard chrome ─────────────────────────────────── */
    /* Hero card: stepper at the top as an eyebrow strip, hero copy
       below a hairline divider. Single card to save the vertical
       real estate the user flagged. */
    .wizard-stepper-slot {
      display: flex;
      align-items: center;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--divider-color);
      margin-bottom: 14px;
    }
    /* Step bar — full width, evenly distributed pills with growing
       separators between. One pill per step. Past steps show a
       check, the current step is amber-filled, future steps muted. */
    .step-bar {
      display: flex;
      align-items: center;
      gap: 4px;
      flex: 1;
    }
    .step-pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 12px 6px 6px;
      background: transparent;
      border: none;
      color: var(--secondary-text-color);
      font-family: inherit;
      font-size: var(--selora-fs-sm);
      font-weight: 600;
      cursor: default;
    }
    .step-pill[disabled] {
      cursor: default;
    }
    .step-pill:not([disabled]) {
      cursor: pointer;
    }
    .step-num {
      width: 24px;
      height: 24px;
      border-radius: 50%;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: var(--selora-fs-xs);
      font-weight: 700;
      background: var(--secondary-background-color);
      border: 1px solid var(--divider-color);
      color: var(--secondary-text-color);
    }
    .step-num ha-icon {
      --mdc-icon-size: 14px;
    }
    .step-current .step-num {
      background: var(--selora-accent);
      border-color: var(--selora-accent);
      color: #000;
    }
    .step-current .step-label {
      color: var(--primary-text-color);
    }
    .step-done .step-num {
      background: color-mix(
        in srgb,
        var(--success-color, #2e7d32) 22%,
        transparent
      );
      border-color: var(--success-color, #2e7d32);
      color: var(--success-color, #2e7d32);
    }
    .step-done .step-label {
      color: var(--primary-text-color);
    }
    .step-sep {
      flex: 1;
      height: 2px;
      background: var(--divider-color);
      border-radius: 2px;
    }
    .step-sep.done {
      background: var(--success-color, #2e7d32);
    }
    /* Step pane container — every step renders into this. Same
       min-width:0 trick as the root so content widths don't
       propagate upward. */
    .step-pane {
      display: flex;
      flex-direction: column;
      gap: 16px;
      width: 100%;
      min-width: 0;
    }
    .step-pane > * {
      min-width: 0;
      max-width: 100%;
      box-sizing: border-box;
    }
    .step-prose {
      margin: 0;
      font-size: var(--selora-fs-md);
      color: var(--primary-text-color);
      line-height: 1.6;
    }
    /* Footer: hint on the left, Back + primary CTA on the right. */
    .step-footer {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding-top: 8px;
    }
    .step-footer-hint {
      font-size: var(--selora-fs-sm);
      color: var(--secondary-text-color);
    }
    .step-footer-actions {
      display: flex;
      gap: 10px;
    }

    /* ── Overview + Activate cards ─────────────────────────────── */
    .overview-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
    }
    @media (max-width: 800px) {
      .overview-grid {
        grid-template-columns: 1fr;
      }
    }
    .overview-card {
      padding: 18px 20px;
      border: 1px solid var(--divider-color);
      border-radius: 12px;
      background: var(--card-background-color);
    }
    .overview-card-title {
      margin: 0 0 12px;
      font-size: var(--selora-fs-md);
      font-weight: 700;
      color: var(--primary-text-color);
    }
    .overview-list {
      list-style: none;
      padding: 0;
      margin: 0;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .overview-list.compact {
      gap: 6px;
    }
    .overview-list li {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: var(--selora-fs-sm);
      color: var(--primary-text-color);
      line-height: 1.4;
    }
    .overview-list ha-icon {
      --mdc-icon-size: 16px;
      color: var(--selora-accent);
      flex-shrink: 0;
    }
    .overview-list .panel-muted ha-icon {
      color: var(--secondary-text-color);
    }

    /* ── Step 2: Match table ──────────────────────────────────── */
    /* Single grid container. Each row is a subgrid spanning all
       columns so column widths are computed once for the whole
       table — without that, every row carried its own grid and
       could resize on click (when the detail panel below caused
       intrinsic widths to recompute). */
    .match-table {
      display: grid;
      grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr) minmax(0, 1fr);
      border: 1px solid var(--divider-color);
      border-radius: 12px;
      background: var(--card-background-color);
      overflow: hidden;
    }
    .match-row {
      display: grid;
      grid-template-columns: subgrid;
      grid-column: 1 / -1;
      align-items: center;
      gap: 12px;
      padding: 12px 16px;
      border: none;
      background: transparent;
      text-align: left;
      font-family: inherit;
      color: var(--primary-text-color);
      border-top: 1px solid var(--divider-color);
    }
    .match-row.match-head {
      border-top: none;
      font-size: var(--selora-fs-xs);
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--secondary-text-color);
      background: var(--secondary-background-color);
    }
    .match-row.match-data {
      cursor: pointer;
      transition: background 120ms ease;
    }
    .match-row.match-data:hover {
      background: var(--secondary-background-color);
    }
    .match-row.is-active {
      background: color-mix(in srgb, var(--selora-accent) 8%, transparent);
    }
    .match-cell-item {
      display: flex;
      align-items: center;
      gap: 12px;
      min-width: 0;
    }
    /* Per-row category tile — type icon on a tinted backplate. Same
       colour vocabulary as the "What you need" rail so the homeowner
       sees the same item categorisation in both surfaces. Status is
       conveyed by the STATUS text column (already coloured) — the
       icon doesn't double-encode that signal anymore. */
    .match-icon-wrap {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 32px;
      height: 32px;
      border-radius: 8px;
      flex-shrink: 0;
    }
    .match-icon-wrap ha-icon {
      --mdc-icon-size: 18px;
    }
    .match-icon-integration {
      background: color-mix(in srgb, var(--selora-accent) 16%, transparent);
    }
    .match-icon-integration ha-icon {
      color: var(--selora-accent);
    }
    .match-icon-role {
      background: color-mix(in srgb, #06b6d4 16%, transparent);
    }
    .match-icon-role ha-icon {
      color: #06b6d4;
    }
    .match-icon-pin {
      background: color-mix(in srgb, #10b981 16%, transparent);
    }
    .match-icon-pin ha-icon {
      color: #10b981;
    }
    .match-icon-input {
      background: var(--selora-inner-card-bg);
    }
    .match-icon-input ha-icon {
      color: var(--secondary-text-color);
    }
    /* Status nuance on the tile: skipped rows desaturate, failed rows
       gain a red glyph (the rest keep their category colour). */
    .match-icon-status-skipped {
      opacity: 0.55;
    }
    .match-icon-status-failed {
      background: color-mix(in srgb, #ef4444 16%, transparent);
    }
    .match-icon-status-failed ha-icon {
      color: #ef4444;
    }
    .match-cell-text {
      min-width: 0;
    }
    .match-title {
      font-size: var(--selora-fs-sm);
      font-weight: 600;
    }
    .match-sub {
      font-size: var(--selora-fs-xs);
      color: var(--secondary-text-color);
      margin-top: 2px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    /* Failure reason on a row: red, and allowed to wrap to two lines so
       the homeowner reads the "why" without opening the action panel. */
    .match-sub.is-error {
      color: #ef4444;
      white-space: normal;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
    }
    .match-status {
      font-size: var(--selora-fs-sm);
      font-weight: 600;
    }
    .match-status.pi-ok {
      color: var(--success-color, #2e7d32);
    }
    .match-status.pi-needs_input {
      color: var(--selora-accent);
    }
    .match-status.pi-failed {
      color: #ef4444;
    }
    .match-status.pi-skipped,
    .match-status.pi-pending {
      color: var(--secondary-text-color);
    }
    .match-status.pi-running {
      color: var(--selora-accent);
    }
    .match-selected {
      font-size: var(--selora-fs-sm);
      color: var(--primary-text-color);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .match-empty {
      /* Span all three grid tracks — otherwise it sits in column 1 and
         the text wraps inside a narrow cell, reading as misaligned. */
      grid-column: 1 / -1;
      padding: 18px;
      text-align: center;
      color: var(--secondary-text-color);
      border-top: 1px solid var(--divider-color);
    }
    .match-detail {
      margin-top: 4px;
    }

    /* ── Step 3: Resolve buckets ──────────────────────────────── */
    .bucket {
      padding: 16px 18px;
      border: 1px solid var(--divider-color);
      border-radius: 12px;
      background: var(--card-background-color);
    }
    .bucket-title {
      display: flex;
      align-items: center;
      gap: 8px;
      margin: 0 0 12px;
      font-size: var(--selora-fs-sm);
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--secondary-text-color);
    }
    .bucket-title ha-icon {
      --mdc-icon-size: 18px;
    }
    .bucket-waiting .bucket-title,
    .bucket-waiting .bucket-title ha-icon {
      color: var(--selora-accent);
    }
    .bucket-failed .bucket-title,
    .bucket-failed .bucket-title ha-icon {
      color: #ef4444;
    }
    .bucket-running .bucket-title,
    .bucket-running .bucket-title ha-icon {
      color: var(--selora-accent);
    }
    .bucket-done .bucket-title,
    .bucket-done .bucket-title ha-icon {
      color: var(--success-color, #2e7d32);
    }
    .bucket-item {
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 14px;
      padding: 12px 0;
      border-top: 1px solid var(--divider-color);
      align-items: center;
    }
    .bucket-item:first-of-type {
      border-top: none;
      padding-top: 4px;
    }
    .bucket-item:last-of-type {
      padding-bottom: 4px;
    }
    /* Status-driven tile: 32px backplate + glyph, mirroring the
       Match table icons so install progress reads as the same
       visual vocabulary. Status drives both backplate tint and
       glyph color. */
    .bucket-item-icon-wrap {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 32px;
      height: 32px;
      border-radius: 8px;
      flex-shrink: 0;
    }
    .bucket-item-icon-wrap ha-icon {
      --mdc-icon-size: 18px;
    }
    .bucket-tile-ok {
      background: color-mix(
        in srgb,
        var(--success-color, #2e7d32) 18%,
        transparent
      );
    }
    .bucket-tile-ok ha-icon {
      color: var(--success-color, #2e7d32);
    }
    .bucket-tile-running {
      background: color-mix(in srgb, var(--selora-accent) 18%, transparent);
    }
    .bucket-tile-running ha-icon {
      color: var(--selora-accent);
      animation: pi-spin 1.4s linear infinite;
    }
    .bucket-tile-needs_input {
      background: color-mix(in srgb, var(--selora-accent) 18%, transparent);
    }
    .bucket-tile-needs_input ha-icon {
      color: var(--selora-accent);
    }
    .bucket-tile-failed {
      background: color-mix(in srgb, #ef4444 18%, transparent);
    }
    .bucket-tile-failed ha-icon {
      color: #ef4444;
    }
    .bucket-tile-pending {
      background: var(--secondary-background-color);
    }
    .bucket-tile-pending ha-icon {
      color: var(--secondary-text-color);
      opacity: 0.7;
    }
    .bucket-tile-skipped {
      background: var(--secondary-background-color);
      opacity: 0.6;
    }
    .bucket-tile-skipped ha-icon {
      color: var(--secondary-text-color);
    }
    .bucket-item-title {
      font-size: 14px;
      font-weight: 600;
      line-height: 1.3;
    }
    .bucket-item-detail {
      font-size: 12px;
      color: var(--secondary-text-color);
      margin-top: 2px;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    .bucket-item-action {
      grid-column: 1 / -1;
      margin-top: 8px;
    }

    /* ── Step 4: Activate hero ────────────────────────────────── */
    .activate-hero {
      display: flex;
      align-items: center;
      gap: 14px;
      padding: 20px 22px;
      border-radius: 14px;
      background: color-mix(
        in srgb,
        var(--selora-accent) 10%,
        var(--card-background-color)
      );
      border: 1px solid
        color-mix(in srgb, var(--selora-accent) 36%, transparent);
    }
    .activate-hero ha-icon {
      --mdc-icon-size: 32px;
      color: var(--selora-accent);
    }
    .activate-hero-title {
      font-size: var(--selora-fs-lg, 18px);
      font-weight: 700;
      color: var(--primary-text-color);
    }
    .activate-hero-sub {
      font-size: var(--selora-fs-sm);
      color: var(--secondary-text-color);
      margin-top: 2px;
    }
    .activate-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
    }
    @media (max-width: 1000px) {
      .activate-grid {
        grid-template-columns: 1fr;
      }
    }
    .safety-card .overview-list ha-icon.safety-ok {
      color: var(--success-color, #2e7d32);
    }
    .safety-card .overview-list ha-icon.safety-fail {
      color: #ef4444;
    }
    /* Right rail — sticky on wide viewports so the role status stays
       in view as the user fills out the inputs form. */
    .wizard-rail {
      display: flex;
      flex-direction: column;
      gap: 12px;
      padding: 20px 18px;
      border: 1px solid var(--divider-color);
      border-radius: 14px;
      background: var(--card-background-color);
      position: sticky;
      top: 12px;
      max-height: calc(100vh - 24px);
      overflow-y: auto;
    }
    @media (max-width: 1100px) {
      .wizard-rail {
        position: static;
        max-height: none;
      }
    }
    .wizard-rail-title {
      font-size: var(--selora-fs-md);
      font-weight: 700;
      color: var(--primary-text-color);
      margin-bottom: 4px;
    }
    .wizard-rail-section-title {
      font-size: var(--selora-fs-micro);
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--secondary-text-color);
      margin-top: 6px;
    }
    .wizard-rail-loading {
      padding: 18px 4px;
      text-align: center;
      color: var(--secondary-text-color);
      font-size: var(--selora-fs-sm);
    }
    /* Role card — one per role in the rail. */
    .role-card {
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding: 14px;
      border: 1px solid var(--divider-color);
      border-radius: 10px;
      background: var(--secondary-background-color);
    }
    .role-card.waiting {
      border-color: color-mix(in srgb, #f59e0b 36%, transparent);
    }
    .role-card.select {
      border-color: color-mix(in srgb, var(--selora-accent) 36%, transparent);
    }
    .role-card.missing {
      border-color: color-mix(in srgb, #ef4444 32%, transparent);
    }
    .role-card-head {
      display: grid;
      grid-template-columns: auto 1fr auto;
      gap: 10px;
      align-items: start;
    }
    .role-card-icon {
      --mdc-icon-size: 22px;
      color: var(--secondary-text-color);
      margin-top: 1px;
    }
    .role-card.satisfied .role-card-icon {
      color: var(--selora-accent);
    }
    .role-card.waiting .role-card-icon {
      color: #f59e0b;
    }
    .role-card.select .role-card-icon {
      color: var(--selora-accent);
    }
    .role-card-head-text {
      min-width: 0;
    }
    .role-card-title {
      font-size: var(--selora-fs-md);
      font-weight: 700;
      color: var(--primary-text-color);
    }
    .role-card-desc {
      font-size: var(--selora-fs-xs);
      color: var(--secondary-text-color);
      line-height: 1.45;
      margin-top: 2px;
    }
    .role-card-status {
      font-size: var(--selora-fs-micro);
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      padding: 3px 8px;
      border-radius: 999px;
      white-space: nowrap;
    }
    .role-card-status.satisfied {
      background: color-mix(in srgb, var(--selora-accent) 14%, transparent);
      color: var(--selora-accent);
    }
    .role-card-status.waiting {
      background: color-mix(in srgb, #f59e0b 14%, transparent);
      color: #f59e0b;
    }
    .role-card-status.select {
      background: color-mix(in srgb, var(--selora-accent) 14%, transparent);
      color: var(--selora-accent);
    }
    .role-card-status.missing {
      background: color-mix(in srgb, #ef4444 14%, transparent);
      color: #ef4444;
    }
    .role-card-status.skipped {
      background: var(--secondary-background-color);
      color: var(--secondary-text-color);
    }
    .role-card-chips {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
    }
    .role-card-chips .role-entity-chip {
      margin: 0;
    }
    .role-card-empty {
      font-size: var(--selora-fs-xs);
      color: var(--secondary-text-color);
      line-height: 1.5;
    }
    .role-card-error {
      font-size: var(--selora-fs-xs);
      color: #ef4444;
      line-height: 1.5;
    }
    .wizard-title {
      font-size: var(--selora-fs-2xl);
      font-weight: 700;
      color: var(--primary-text-color);
    }
    .wizard-section {
      padding: 16px 18px;
      border: 1px solid var(--divider-color);
      border-radius: 12px;
      background: var(--card-background-color);
    }
    .wizard-section h3 {
      margin: 0 0 12px;
      font-size: var(--selora-fs-md);
      font-weight: 700;
      color: var(--primary-text-color);
    }
    .wizard-field {
      display: flex;
      flex-direction: column;
      gap: 4px;
      margin-bottom: 12px;
    }
    .wizard-field label {
      font-size: var(--selora-fs-sm);
      color: var(--primary-text-color);
      font-weight: 600;
    }
    .wizard-field .hint {
      font-size: var(--selora-fs-md, 14px);
      line-height: 1.5;
      color: color-mix(in srgb, var(--primary-text-color) 80%, transparent);
      margin-bottom: 2px;
    }
    .wizard-field input,
    .wizard-field select {
      padding: 9px 11px;
      border: 1px solid var(--selora-inner-card-border);
      border-radius: 10px;
      background: var(--selora-inner-card-bg);
      color: var(--primary-text-color);
      font-size: var(--selora-fs-md);
      transition: border-color 0.3s;
    }
    .wizard-field input:focus,
    .wizard-field select:focus {
      outline: none;
      border-color: var(--selora-accent);
    }
    .role-row {
      display: grid;
      grid-template-columns: 1fr auto;
      align-items: start;
      gap: 8px;
      padding: 10px 0;
      border-bottom: 1px solid var(--divider-color);
    }
    .role-row:last-child {
      border-bottom: none;
    }
    .role-row .role-meta {
      font-size: var(--selora-fs-sm);
      color: var(--primary-text-color);
    }
    .role-row .role-desc {
      font-size: var(--selora-fs-xs);
      color: var(--secondary-text-color);
      margin-top: 2px;
    }
    .role-row .role-status {
      font-size: var(--selora-fs-sm);
      font-weight: 600;
      white-space: nowrap;
    }
    .role-status.ok {
      color: var(--success-color, #2e7d32);
    }
    .role-status.missing {
      color: var(--error-color, #c62828);
    }
    .role-entities {
      grid-column: 1 / -1;
      margin-top: 6px;
      font-size: var(--selora-fs-xs);
      color: var(--secondary-text-color);
      word-break: break-all;
    }
    /* Entity picker card. Two-line layout: friendly name on top,
       entity_id muted below. Domain icon sits on a tinted tile on
       the left (matches the Match table / bucket items pattern).
       Wide tap target — minimum 44px tall — so this works on touch. */
    .role-entity-chip {
      display: inline-grid;
      grid-template-columns: auto minmax(0, 1fr);
      align-items: center;
      gap: 10px;
      padding: 8px 14px 8px 8px;
      min-height: 44px;
      border-radius: 10px;
      background: var(--secondary-background-color);
      border: 1px solid var(--divider-color);
      margin: 4px 6px 4px 0;
      font-size: 14px;
      line-height: 1.3;
      color: var(--primary-text-color);
      vertical-align: middle;
      text-align: left;
    }
    .role-entity-chip > .chip-icon-tile {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 32px;
      height: 32px;
      border-radius: 8px;
      background: color-mix(in srgb, #06b6d4 16%, transparent);
      flex-shrink: 0;
    }
    .role-entity-chip > .chip-icon-tile ha-icon {
      --mdc-icon-size: 18px;
      color: #06b6d4;
    }
    .role-entity-chip > .chip-text {
      display: flex;
      flex-direction: column;
      min-width: 0;
    }
    .role-entity-chip > .chip-text .chip-name {
      display: block;
      font-size: 14px;
      font-weight: 600;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .role-entity-chip > .chip-text .chip-id {
      display: block;
      font-size: 11px;
      color: var(--secondary-text-color);
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      margin-top: 1px;
    }
    /* Required-selection chips are buttons the user toggles. Off
       state reads as a regular chip; on state lights up with the
       accent. The check appears only when selected — no
       placeholder-circle clutter for the unselected case. */
    .role-entity-toggle {
      cursor: pointer;
      transition:
        background 120ms ease,
        border-color 120ms ease,
        color 120ms ease;
      font-family: inherit;
    }
    .role-entity-toggle:hover:not([disabled]) {
      border-color: var(--selora-accent);
    }
    .role-entity-toggle[disabled] {
      cursor: progress;
      opacity: 0.6;
    }
    .role-entity-toggle.is-on {
      background: color-mix(in srgb, var(--selora-accent) 14%, transparent);
      border-color: var(--selora-accent);
    }
    .role-entity-toggle.is-on > .chip-icon-tile {
      background: color-mix(in srgb, var(--selora-accent) 22%, transparent);
    }
    .role-entity-toggle.is-on > .chip-icon-tile ha-icon {
      color: var(--selora-accent);
    }
    .role-entity-toggle.is-on > .chip-text .chip-name {
      color: var(--primary-text-color);
    }
    /* Locked pin = manifest pre-bound. Renders like a chip but
       non-interactive; the lock badge tells the user this slot is
       fixed by the installation manifest. */
    .role-entity-chip.is-pinned {
      background: color-mix(
        in srgb,
        var(--secondary-text-color) 10%,
        transparent
      );
      color: var(--primary-text-color);
      cursor: default;
    }
    .role-entity-chip.is-pinned .pin-badge {
      --mdc-icon-size: 12px;
      margin-left: 4px;
      color: var(--secondary-text-color);
      vertical-align: middle;
    }
    .role-entity-chip.is-pinned .chip-name {
      display: inline-flex;
      align-items: center;
    }
    /* Waiting-on card = manifest pin that hasn't resolved yet.
       Renders below the chip row with the device-identity readout so
       the field tech knows what to pair. */
    /* Pair-this-device card. Visual hierarchy mirrors what the
       homeowner reads top-to-bottom:
         1. friendly title   (what the device IS / where it goes)
         2. model            (which hardware to look for)
         3. action prose     (what to do NEXT)
         4. small footer     (integration + entity_id for the techie) */
    .pending-binding {
      display: grid;
      grid-template-columns: 36px 1fr;
      gap: 12px;
      align-items: start;
      padding: 14px 16px;
      margin-top: 10px;
      border-radius: 10px;
      background: color-mix(in srgb, #f59e0b 8%, transparent);
      border: 1px solid color-mix(in srgb, #f59e0b 30%, transparent);
    }
    .pending-binding-icon {
      --mdc-icon-size: 26px;
      color: #f59e0b;
      margin-top: 1px;
    }
    .pending-binding-body {
      min-width: 0;
    }
    .pending-binding-title {
      font-weight: 600;
      color: var(--primary-text-color);
      font-size: var(--selora-fs-md-lg);
      line-height: 1.25;
    }
    .pending-binding-model {
      font-size: var(--selora-fs-sm);
      color: var(--secondary-text-color);
      margin-top: 2px;
    }
    .pending-binding-action {
      font-size: var(--selora-fs-sm);
      color: var(--primary-text-color);
      line-height: 1.55;
      margin-top: 10px;
    }
    .pending-binding-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }
    .pending-binding-cta {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 7px 14px;
      border-radius: 8px;
      font-size: var(--selora-fs-sm);
      font-weight: 600;
      cursor: pointer;
      transition:
        background 120ms ease,
        border-color 120ms ease;
      font-family: inherit;
    }
    .pending-binding-cta ha-icon {
      --mdc-icon-size: 15px;
    }
    .pending-binding-cta.primary {
      background: #f59e0b;
      border: 1px solid #f59e0b;
      color: white;
    }
    .pending-binding-cta.primary:hover:not([disabled]) {
      background: #d88806;
      border-color: #d88806;
    }
    .pending-binding-cta.secondary {
      background: transparent;
      border: 1px solid color-mix(in srgb, #f59e0b 40%, transparent);
      color: var(--primary-text-color);
    }
    .pending-binding-cta.secondary:hover:not([disabled]) {
      border-color: #f59e0b;
      background: color-mix(in srgb, #f59e0b 8%, transparent);
    }
    .pending-binding-cta[disabled] {
      opacity: 0.55;
      cursor: progress;
    }
    .pending-binding-footer {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
      margin-top: 12px;
      padding-top: 10px;
      border-top: 1px dashed color-mix(in srgb, #f59e0b 26%, transparent);
      font-size: var(--selora-fs-xs);
      color: var(--secondary-text-color);
    }
    .pending-binding-pill {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 2px 8px;
      border-radius: 999px;
      background: color-mix(in srgb, #f59e0b 14%, transparent);
      color: #f59e0b;
      font-weight: 600;
    }
    .pending-binding-pill ha-icon {
      --mdc-icon-size: 12px;
    }
    .pending-binding-eid {
      font-family: var(--selora-mono, ui-monospace, Menlo, monospace);
      opacity: 0.7;
      word-break: break-all;
    }
    .punch {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .punch-item {
      display: flex;
      gap: 8px;
      align-items: flex-start;
      padding: 10px 12px;
      border-radius: 8px;
      background: color-mix(
        in srgb,
        var(--error-color, #c62828) 8%,
        transparent
      );
      border: 1px solid
        color-mix(in srgb, var(--error-color, #c62828) 24%, transparent);
      font-size: var(--selora-fs-sm);
    }
    .punch-item .stage-pill {
      flex-shrink: 0;
      padding: 1px 8px;
      border-radius: 999px;
      background: var(--secondary-background-color);
      color: var(--secondary-text-color);
      font-size: var(--selora-fs-micro);
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .package-disclosure {
      padding: 0;
      overflow: hidden;
    }
    .package-disclosure summary {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 14px 18px;
      cursor: pointer;
      list-style: none;
      font-size: var(--selora-fs-md);
      font-weight: 600;
      color: var(--primary-text-color);
      user-select: none;
    }
    .package-disclosure summary::-webkit-details-marker {
      display: none;
    }
    .package-disclosure summary > .filler {
      flex: 1;
    }
    .package-disclosure .chevron {
      --mdc-icon-size: 18px;
      color: var(--secondary-text-color);
      transition: transform 120ms ease;
    }
    .package-disclosure[open] .chevron {
      transform: rotate(90deg);
    }
    .package-disclosure summary ha-icon {
      --mdc-icon-size: 18px;
      color: var(--secondary-text-color);
    }
    .package-disclosure .package-disclosure-hint {
      font-size: var(--selora-fs-micro);
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--secondary-text-color);
      padding: 2px 8px;
      border: 1px solid var(--divider-color);
      border-radius: 999px;
      margin-left: 8px;
    }
    .package-disclosure .yaml-preview {
      border-top: 1px solid var(--divider-color);
      border-radius: 0;
      max-height: 320px;
    }
    .yaml-preview {
      max-height: 360px;
      overflow: auto;
      padding: 12px 14px;
      border-radius: 8px;
      background: var(--code-editor-background-color, #1e1e1e);
      color: var(--code-editor-text-color, #d4d4d4);
      font-family: var(--selora-mono, ui-monospace, Menlo, monospace);
      font-size: 12.5px;
      line-height: 1.45;
      white-space: pre;
    }
    /* Syntax-highlight token colours. The .yk/.ys/etc class names are
       emitted by _highlightYaml in this same file; keep the palette
       desaturated so the preview reads as part of the panel and not
       like a code editor was pasted in. */
    .yaml-preview .yk {
      color: #9cdcfe;
    } /* keys */
    .yaml-preview .ys {
      color: #ce9178;
    } /* strings */
    .yaml-preview .yn {
      color: #b5cea8;
    } /* numbers */
    .yaml-preview .yb {
      color: #569cd6;
    } /* booleans / null */
    .yaml-preview .yc {
      color: #6a9955;
      font-style: italic;
    } /* comments */
    .yaml-preview .yd {
      color: #d4d4d4;
    } /* list dashes */
    .yaml-preview .yp {
      color: #c586c0;
    } /* HA runtime templates */
    /* Uninstall confirmation modal — destructive variant of the
       generic modal button from the panel's shared style. */
    .uninstall-modal {
      max-width: 440px;
      padding: 22px;
      display: flex;
      flex-direction: column;
      gap: 14px;
      border: 1px solid var(--divider-color);
    }
    .uninstall-modal .modal-title {
      margin: 0;
      font-size: var(--selora-fs-lg);
      font-weight: 700;
      color: var(--primary-text-color);
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .uninstall-modal .modal-title-icon {
      --mdc-icon-size: 22px;
      color: #f59e0b;
    }
    .uninstall-modal .modal-body {
      margin: 0;
      font-size: var(--selora-fs-md);
      color: var(--primary-text-color);
      line-height: 1.55;
    }
    /* "Also remove integrations" checkbox list — shown in the
       uninstall confirm modal when the recipe installed any
       integrations via auto_setup. */
    .uninstall-integrations {
      display: flex;
      flex-direction: column;
      gap: 10px;
      margin: 16px 0 4px;
      padding: 14px;
      border: 1px solid var(--divider-color);
      border-radius: 10px;
      background: var(--secondary-background-color);
    }
    .uninstall-integrations-title {
      font-size: 13px;
      font-weight: 700;
      color: var(--primary-text-color);
    }
    .uninstall-integrations-sub {
      margin: 0 0 6px;
      font-size: 12px;
      color: var(--secondary-text-color);
      line-height: 1.5;
    }
    .uninstall-integration-row {
      display: grid;
      grid-template-columns: auto auto 1fr;
      align-items: center;
      gap: 12px;
      padding: 6px 4px;
      cursor: pointer;
      font-family: inherit;
    }
    .uninstall-integration-row input[type="checkbox"] {
      width: 16px;
      height: 16px;
      accent-color: var(--selora-accent);
    }
    .uninstall-integration-brand {
      width: 28px;
      height: 28px;
      object-fit: contain;
      border-radius: 6px;
      background: rgba(255, 255, 255, 0.04);
      padding: 2px;
      flex-shrink: 0;
    }
    .uninstall-integration-name {
      font-size: 13px;
      font-weight: 600;
      color: var(--primary-text-color);
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    .uninstall-integration-warn {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-size: 11px;
      color: #f59e0b;
      margin-top: 2px;
    }
    .uninstall-integration-warn ha-icon {
      --mdc-icon-size: 13px;
    }
    .uninstall-modal .modal-actions {
      display: flex;
      gap: 8px;
      justify-content: flex-end;
    }
    .uninstall-modal .modal-btn-icon {
      --mdc-icon-size: 16px;
    }
    /* Scoped to .uninstall-modal for specificity — the shared
       .modal-btn rules in modals.css.js define transparent
       background and would otherwise win the cascade because they
       load after this style block. */
    .uninstall-modal .modal-destructive {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 8px 16px;
      border-radius: 8px;
      border: 1.5px solid #ef4444;
      background: #ef4444;
      color: white;
      font-size: var(--selora-fs-sm, 13px);
      font-weight: 600;
      cursor: pointer;
    }
    .uninstall-modal .modal-destructive:hover {
      background: #dc2626;
      border-color: #dc2626;
      opacity: 1;
    }
    .uninstall-modal .modal-destructive:focus-visible {
      outline: 2px solid color-mix(in srgb, #ef4444 60%, white);
      outline-offset: 2px;
    }
    .wizard-actions {
      display: flex;
      gap: 10px;
      justify-content: flex-end;
      align-items: center;
      padding-top: 12px;
    }
    .install-success {
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding: 18px;
      border: 1px solid
        color-mix(in srgb, var(--success-color, #2e7d32) 30%, transparent);
      border-radius: 12px;
      background: color-mix(
        in srgb,
        var(--success-color, #2e7d32) 8%,
        transparent
      );
    }
  </style>
`;

// ── Install-source card (URL + upload) ─────────────────────────────

function _renderInstallSourceCard(host) {
  const urlBusy = host._recipesUrlBusy;
  const uploadBusy = host._recipesUploadBusy;
  const dragOver = host._recipesDragOver;
  const error = host._recipesInstallError;

  const onPickFile = () => {
    if (uploadBusy) return;
    host.renderRoot?.querySelector("#selora-recipe-upload-input")?.click();
  };
  const onDragOver = (e) => {
    e.preventDefault();
    if (uploadBusy) return;
    if (!host._recipesDragOver) host._recipesDragOver = true;
  };
  const onDragLeave = (e) => {
    if (e.currentTarget.contains(e.relatedTarget)) return;
    if (host._recipesDragOver) host._recipesDragOver = false;
  };
  const onDrop = (e) => {
    e.preventDefault();
    host._recipesDragOver = false;
    if (uploadBusy) return;
    const file = e.dataTransfer?.files?.[0];
    if (!file) return;
    if (!_hasAcceptedSuffix(file.name)) {
      host._recipesInstallError = `${host._t("recipes_install_unsupported_file_prefix", "Unsupported file:")} ${file.name}. ${host._t("recipes_install_unsupported_file_suffix", "Use a .tar.gz, .tgz, or .zip archive.")}`;
      return;
    }
    host._uploadRecipeArchive(file);
  };
  const onFileChosen = (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (!_hasAcceptedSuffix(file.name)) {
      host._recipesInstallError = `${host._t("recipes_install_unsupported_file_prefix", "Unsupported file:")} ${file.name}. ${host._t("recipes_install_unsupported_file_suffix", "Use a .tar.gz, .tgz, or .zip archive.")}`;
      return;
    }
    host._uploadRecipeArchive(file);
  };

  return html`
    <details class="install-disclosure" ?open=${!!error}>
      <summary class="install-disclosure-summary">
        <ha-icon icon="mdi:package-variant-closed"></ha-icon>
        <span
          >${host._t(
            "recipes_install_source_summary",
            "Install from a URL or file",
          )}</span
        >
        <span class="install-disclosure-hint"
          >${host._t(
            "recipes_install_source_hint",
            "Have a recipe from elsewhere? Add it here.",
          )}</span
        >
        <ha-icon
          class="install-disclosure-chevron"
          icon="mdi:chevron-down"
        ></ha-icon>
      </summary>
      <div class="install-card">
        <div>
          <div class="install-card-subtitle">
            ${host._t(
              "recipes_install_bom_note",
              "The bill of materials is checked against your home before anything is installed.",
            )}
          </div>
        </div>

        <div>
          <div class="install-source-label">
            <ha-icon icon="mdi:link-variant"></ha-icon>
            ${host._t("recipes_install_from_url_label", "Install from URL")}
          </div>
          <div class="install-url-row">
            <input
              class="install-url-input"
              type="text"
              .value=${host._recipesUrl || ""}
              placeholder="https://example.com/recipes/foo.tar.gz"
              @input=${(e) => (host._recipesUrl = e.target.value)}
              @keydown=${(e) => {
                if (e.key === "Enter" && !urlBusy) host._installRecipeFromUrl();
              }}
              ?disabled=${urlBusy}
            />
            <button
              class="btn btn-primary"
              ?disabled=${urlBusy || !host._recipesUrl}
              @click=${() => host._installRecipeFromUrl()}
            >
              ${urlBusy
                ? host._t("recipes_install_fetching", "Fetching…")
                : host._t("recipes_install_fetch_button", "Fetch")}
            </button>
          </div>
        </div>

        <div class="install-or-divider">
          <span class="install-or-text"
            >${host._t("recipes_install_or", "OR")}</span
          >
        </div>

        <div>
          <div class="install-source-label">
            <ha-icon icon="mdi:upload-outline"></ha-icon>
            ${host._t(
              "recipes_install_upload_label",
              "Upload from this device",
            )}
          </div>
          <div
            class="install-dropzone ${dragOver ? "is-drag" : ""} ${uploadBusy
              ? "is-busy"
              : ""}"
            @click=${onPickFile}
            @dragover=${onDragOver}
            @dragleave=${onDragLeave}
            @drop=${onDrop}
          >
            <ha-icon
              class="install-dropzone-icon"
              icon=${uploadBusy
                ? "mdi:progress-upload"
                : "mdi:cloud-upload-outline"}
            ></ha-icon>
            <div class="install-dropzone-title">
              ${uploadBusy
                ? host._t("recipes_install_uploading", "Uploading…")
                : dragOver
                  ? host._t("recipes_install_drop_to_upload", "Drop to upload")
                  : host._t(
                      "recipes_install_drop_here",
                      "Drop a recipe archive here",
                    )}
            </div>
            <div class="install-dropzone-hint">
              ${host._t("recipes_install_or_lower", "or")}
              <strong
                >${host._t(
                  "recipes_install_click_to_choose",
                  "click to choose a file",
                )}</strong
              >
              · ${host._t("recipes_install_accepts", "accepts")}
              <code>.tar.gz</code>
              <code>.tgz</code>
              <code>.zip</code>
            </div>
          </div>
          <input
            id="selora-recipe-upload-input"
            type="file"
            accept=".tar.gz,.tgz,.zip,application/gzip,application/zip,application/x-tar"
            style="display:none;"
            @change=${onFileChosen}
          />
        </div>

        ${error ? html`<div class="install-error">${error}</div>` : ""}
      </div>
    </details>
  `;
}

// ── List view ──────────────────────────────────────────────────────

function _renderRecipeCard(host, manifest, installed) {
  const isInstalled = !!installed;
  // A saved wizard draft promotes the card from "Install" → "Resume"
  // and surfaces a "Start over" affordance that wipes the draft.
  const draftStep = !isInstalled
    ? host._wizardDraftStep?.(manifest.slug) || 0
    : 0;
  const hasDraft = draftStep > 0;
  return html`
    <div class="recipe-card">
      <div class="recipe-card-row">
        <div class="recipe-card-body">
          <div class="recipe-card-title">
            ${manifest.title}
            ${isInstalled
              ? html`
                  <span class="recipe-installed-badge">
                    <ha-icon
                      icon="mdi:check"
                      style="--mdc-icon-size:12px;"
                    ></ha-icon>
                    ${host._t("recipes_card_installed_badge", "Installed")}
                  </span>
                `
              : ""}
            ${hasDraft
              ? html`
                  <span class="recipe-draft-badge">
                    <ha-icon
                      icon="mdi:pencil-outline"
                      style="--mdc-icon-size:12px;"
                    ></ha-icon>
                    ${host._t(
                      "recipes_card_in_progress_step",
                      "In progress · Step",
                    )}
                    ${draftStep}
                  </span>
                `
              : ""}
          </div>
          <div class="recipe-card-meta">
            v${manifest.version}${manifest.author
              ? ` · ${manifest.author}`
              : ""}
          </div>
          ${manifest.description && !isInstalled
            ? html`<div class="recipe-card-desc">${manifest.description}</div>`
            : ""}
        </div>
        <div class="recipe-card-actions">
          ${isInstalled
            ? html`
                <button
                  class="btn btn-outline"
                  @click=${() => host._uninstallRecipe(manifest.slug)}
                  ?disabled=${host._recipesBusy}
                >
                  ${host._t("recipes_card_uninstall_button", "Uninstall")}
                </button>
                ${manifest.binding_mode === "group"
                  ? html`
                      <button
                        class="btn btn-outline"
                        @click=${() => host._openManageDevices(manifest.slug)}
                        ?disabled=${host._recipesBusy}
                        title=${host._t(
                          "recipes_card_manage_devices_title",
                          "Swap or update the devices this recipe uses without re-running the wizard",
                        )}
                      >
                        ${host._t(
                          "recipes_card_manage_devices_button",
                          "Manage devices",
                        )}
                      </button>
                    `
                  : ""}
                <button
                  class="btn btn-primary"
                  @click=${() => host._openRecipeWizard(manifest.slug)}
                >
                  ${host._t("recipes_card_configure_button", "Configure")}
                </button>
              `
            : hasDraft
              ? html`
                  <button
                    class="btn btn-outline"
                    @click=${() => host._discardRecipeDraft(manifest.slug)}
                    ?disabled=${host._recipesBusy}
                    title=${host._t(
                      "recipes_card_start_over_title",
                      "Throw away saved progress and start the wizard from scratch",
                    )}
                  >
                    ${host._t("recipes_card_start_over_button", "Start over")}
                  </button>
                  <button
                    class="btn btn-primary"
                    @click=${() => host._openRecipeWizard(manifest.slug)}
                  >
                    ${host._t("recipes_card_resume_button", "Resume")}
                  </button>
                `
              : html`
                  <button
                    class="btn btn-primary"
                    @click=${() => host._openRecipeWizard(manifest.slug)}
                  >
                    ${host._t("recipes_card_install_button", "Install")}
                  </button>
                `}
        </div>
      </div>
      ${isInstalled
        ? _renderInstalledDetails(host, installed, manifest.description)
        : ""}
    </div>
  `;
}

// Read-only "what got installed" panel for an installed recipe.
// Sourced entirely from the InstallRecord returned by
// ``selora_ai/recipes/list`` — no install re-run, no extra WS call.
function _renderInstalledDetails(host, record, description) {
  if (!record) return "";
  const bindings = record.bindings || {};
  const inputs = record.inputs || {};
  const integrations = record.integrations_installed || {};
  const bindingRoles = Object.keys(bindings).filter(
    (role) => (bindings[role] || []).length > 0,
  );
  const inputKeys = Object.keys(inputs);
  const integrationDomains = Object.keys(integrations);
  // Lazily-loaded package contents (yaml + section counts) — fetched when the
  // panel is expanded (see the <details> @toggle below).
  const pkg = host._recipePackages?.[record.slug];

  let installedOn = record.installed_at || "";
  if (installedOn) {
    const d = new Date(installedOn);
    if (!Number.isNaN(d.getTime())) installedOn = d.toLocaleString();
  }

  const copyPath = () => {
    if (record.package_path && navigator.clipboard) {
      navigator.clipboard.writeText(record.package_path).catch(() => {});
    }
  };

  return html`
    <details
      class="recipe-details"
      @toggle=${(e) => {
        if (e.target.open) host._loadRecipePackage?.(record.slug);
      }}
    >
      <summary class="recipe-details-summary">
        <ha-icon icon="mdi:chevron-down"></ha-icon>
        ${host._t("recipes_details_summary", "Details")}
      </summary>
      ${description
        ? html`<div class="recipe-card-desc recipe-details-desc">
            ${description}
          </div>`
        : ""}
      <div class="recipe-details-grid">
        ${pkg?.counts && Object.keys(pkg.counts).length
          ? html`
              <div class="recipe-details-key">
                ${host._t("recipes_details_creates_key", "Creates")}
              </div>
              <div class="recipe-details-val">
                ${_formatPackageCounts(host, pkg.counts)}
              </div>
            `
          : ""}
        <div class="recipe-details-key">
          ${host._t("recipes_details_version_key", "Version")}
        </div>
        <div class="recipe-details-val">
          v${record.version}${installedOn
            ? ` · ${host._t("recipes_details_installed_on", "installed")} ${installedOn}`
            : ""}
        </div>

        <div class="recipe-details-key">
          ${host._t("recipes_details_where_key", "Where")}
        </div>
        <div class="recipe-details-val">
          <span class="recipe-details-path">
            <code>${record.package_path || "—"}</code>
            ${record.package_path
              ? html`<button
                  class="recipe-details-copy"
                  title=${host._t(
                    "recipes_details_copy_path_title",
                    "Copy path",
                  )}
                  @click=${copyPath}
                >
                  <ha-icon icon="mdi:content-copy"></ha-icon>
                </button>`
              : ""}
          </span>
          ${pkg?.yaml
            ? html`<details class="recipe-package-view">
                <summary class="recipe-details-summary">
                  <ha-icon icon="mdi:chevron-down"></ha-icon>
                  ${host._t(
                    "recipes_details_view_package",
                    "View package file",
                  )}
                </summary>
                ${unsafeHTML(
                  '<div class="yaml-preview">' +
                    _highlightYaml(pkg.yaml) +
                    "</div>",
                )}
              </details>`
            : ""}
        </div>

        <div class="recipe-details-key">
          ${host._t("recipes_details_devices_key", "Devices")}
        </div>
        <div class="recipe-details-val">
          ${bindingRoles.length === 0
            ? html`<span class="recipe-details-empty"
                >${host._t(
                  "recipes_details_no_bound_devices",
                  "No bound devices",
                )}</span
              >`
            : bindingRoles.map((role) => {
                const ents = bindings[role] || [];
                return html`<div class="recipe-details-binding">
                  <div class="recipe-details-role">${_humanizeRole(role)}</div>
                  <div class="recipe-details-entities">
                    ${ents.length === 0
                      ? html`<span class="recipe-details-empty"
                          >${host._t(
                            "recipes_details_none_selected_optional",
                            "None selected (optional)",
                          )}</span
                        >`
                      : ents.map(
                          (id) =>
                            html`<span class="recipe-details-chip">
                              <ha-icon
                                icon=${_entityIcon(host.hass, id)}
                              ></ha-icon>
                              ${_entityFriendlyName(host.hass, id)}
                            </span>`,
                        )}
                  </div>
                </div>`;
              })}
        </div>

        ${inputKeys.length
          ? html`
              <div class="recipe-details-key">
                ${host._t("recipes_details_settings_key", "Settings")}
              </div>
              <div class="recipe-details-val">
                ${inputKeys.map(
                  (k) =>
                    html`<div>
                      <span class="recipe-details-role"
                        >${_humanizeRole(k)}:</span
                      >
                      ${String(inputs[k])}
                    </div>`,
                )}
              </div>
            `
          : ""}
        ${integrationDomains.length
          ? html`
              <div class="recipe-details-key">
                ${host._t("recipes_details_integrations_key", "Integrations")}
              </div>
              <div class="recipe-details-val">
                <div class="recipe-details-entities">
                  ${integrationDomains.map(
                    (dom) =>
                      html`<span class="recipe-details-chip">
                        <ha-icon icon="mdi:puzzle"></ha-icon>
                        ${dom}
                      </span>`,
                  )}
                </div>
              </div>
            `
          : ""}
      </div>
    </details>
  `;
}

// Friendly singular/plural labels for the package sections a recipe creates.
// Falls back to the raw HA domain key + naive plural for anything unmapped.
const _PACKAGE_SECTION_LABELS = {
  automation: ["automation", "automations"],
  script: ["script", "scripts"],
  scene: ["scene", "scenes"],
  group: ["group", "groups"],
  template: ["template", "templates"],
  input_boolean: ["toggle", "toggles"],
  input_number: ["number helper", "number helpers"],
  input_select: ["dropdown helper", "dropdown helpers"],
  input_text: ["text helper", "text helpers"],
  input_datetime: ["date/time helper", "date/time helpers"],
  sensor: ["sensor", "sensors"],
  binary_sensor: ["binary sensor", "binary sensors"],
  timer: ["timer", "timers"],
  counter: ["counter", "counters"],
};

function _formatPackageCounts(host, counts) {
  const parts = Object.entries(counts || {})
    .filter(([, n]) => n > 0)
    .map(([key, n]) => {
      const labels = _PACKAGE_SECTION_LABELS[key] || [key, `${key}s`];
      return `${n} ${n === 1 ? labels[0] : labels[1]}`;
    });
  if (!parts.length)
    return html`<span class="recipe-details-empty"
      >${host._t("recipes_details_creates_nothing", "Nothing")}</span
    >`;
  return parts.join(" · ");
}

// ``shelter_zone`` → ``Shelter zone`` — role/input ids are snake_case.
function _humanizeRole(s) {
  if (!s) return "";
  const spaced = s.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function _renderListView(host) {
  const available = host._recipesList?.available || [];
  const installed = host._recipesList?.installed || [];
  const installedBySlug = Object.fromEntries(installed.map((r) => [r.slug, r]));
  const installedSlugs = new Set(installed.map((r) => r.slug));
  const onlyInstalled = installed.filter(
    (r) => !available.some((a) => a.slug === r.slug),
  );

  return html`
    <div class="recipes-root">
      <div class="recipes-header">
        <div class="recipes-h1">
          ${host._t("recipes_list_title", "Recipes")}
        </div>
        <button
          class="filter-row-action"
          @click=${() => {
            host._loadRecipesList();
            host._loadRecipesCatalog(true);
          }}
          title=${host._t(
            "recipes_list_check_updates_title",
            "Check selorahomes.com for new and updated recipes",
          )}
          ?disabled=${host._recipesBusy || host._recipesCatalogBusy}
        >
          <ha-icon
            class=${host._recipesCatalogBusy ? "icon-spin" : ""}
            icon="mdi:refresh"
          ></ha-icon>
          ${host._recipesCatalogBusy
            ? host._t("recipes_list_checking", "Checking…")
            : host._t("recipes_list_check_updates_button", "Check for updates")}
        </button>
      </div>

      <p class="recipes-intro">
        ${host._t(
          "recipes_list_intro",
          "Recipes are ready-made automations you install in one step — a leak lockdown, a bedtime routine, a tornado alert. Selora checks each recipe against the devices in your home, then wires it up for you. Pick one below to get started.",
        )}
      </p>

      ${_renderCatalogSection(host)}
      ${available.length > 0
        ? html`
            <div class="recipes-section-title">
              ${host._t("recipes_list_on_this_device", "Installed")}
            </div>
            <div style="display:flex;flex-direction:column;gap:10px;">
              ${available.map((m) =>
                _renderRecipeCard(host, m, installedBySlug[m.slug] || null),
              )}
            </div>
          `
        : ""}
      ${onlyInstalled.length > 0
        ? html`
            <div class="recipes-section-title">
              ${host._t(
                "recipes_list_installed_missing_bundle",
                "Installed (bundle missing from disk)",
              )}
            </div>
            <div style="display:flex;flex-direction:column;gap:10px;">
              ${onlyInstalled.map(
                (rec) => html`
                  <div class="recipe-card">
                    <div class="recipe-card-body">
                      <div class="recipe-card-title">
                        ${rec.title}
                        <span class="recipe-installed-badge"
                          >${host._t(
                            "recipes_card_installed_badge",
                            "Installed",
                          )}</span
                        >
                      </div>
                      <div class="recipe-card-meta">
                        v${rec.version} ·
                        ${host._t("recipes_list_package_label", "package:")}
                        ${rec.package_path}
                      </div>
                    </div>
                    <div class="recipe-card-actions">
                      <button
                        class="btn btn-outline"
                        @click=${() => host._uninstallRecipe(rec.slug)}
                        ?disabled=${host._recipesBusy}
                      >
                        ${host._t("recipes_card_uninstall_button", "Uninstall")}
                      </button>
                    </div>
                  </div>
                `,
              )}
            </div>
          `
        : ""}
      ${_renderInstallSourceCard(host)}
    </div>
  `;
}

// "Browse recipes" section sourced from the public catalog
// (selorahomes.com/api/recipes.json). Updates ship without a
// new integration release. Search box filters client-side.
function _renderCatalogSection(host) {
  const cat = host._recipesCatalog;
  // "Installed" reflects actual install records (refreshed on
  // install/uninstall), not on-disk presence — a staged-but-uninstalled
  // bundle must not keep the catalog card flagged INSTALLED.
  const installedSlugs = new Set(
    (host._recipesList?.installed || []).map((r) => r.slug),
  );
  const filtered = host._filteredCatalog
    ? host._filteredCatalog()
    : cat?.recipes || [];
  const currentOverride = host._catalogUrlOverride
    ? host._catalogUrlOverride()
    : "";
  // Dev-mode gate from the integration's Settings tab.
  const isDev = !!host._config?.developer_mode;
  return html`
    <div class="catalog-section">
      <div class="filter-row">
        <div class="filter-input-wrap" style="flex:1 1 260px;">
          <ha-icon icon="mdi:magnify"></ha-icon>
          <input
            type="text"
            placeholder=${host._t(
              "recipes_catalog_search_placeholder",
              "Search recipes…",
            )}
            .value=${host._recipesCatalogSearch || ""}
            @input=${(e) => host._onRecipesCatalogSearch(e.target.value)}
          />
          ${host._recipesCatalogSearch
            ? html`<ha-icon
                icon="mdi:close-circle"
                style="--mdc-icon-size:16px;cursor:pointer;opacity:0.5;flex-shrink:0;"
                @click=${() => host._onRecipesCatalogSearch("")}
              ></ha-icon>`
            : ""}
        </div>
        <div class="catalog-controls">
          ${isDev
            ? html`<button
                class="sort-dir-toggle"
                @click=${() => {
                  const next = window.prompt(
                    host._t(
                      "recipes_catalog_url_prompt",
                      "Catalog URL (leave blank to reset to selorahomes.com):",
                    ),
                    currentOverride,
                  );
                  if (next === null) return;
                  host._setCatalogUrlOverride(next.trim());
                }}
                title=${currentOverride
                  ? `${host._t("recipes_catalog_using_override", "Using override:")} ${currentOverride}`
                  : host._t(
                      "recipes_catalog_set_url_title",
                      "Set a catalog URL (dev / staging)",
                    )}
              >
                <ha-icon icon="mdi:cog-outline"></ha-icon>
              </button>`
            : ""}
        </div>
      </div>
      ${currentOverride && isDev
        ? html`<div class="catalog-override-banner">
            <ha-icon icon="mdi:flask-outline"></ha-icon>
            ${host._t(
              "recipes_catalog_source_overridden",
              "Catalog source overridden:",
            )}
            <code>${currentOverride}</code>
          </div>`
        : ""}
      ${host._recipesCatalogError
        ? html`<div class="catalog-error">
            <ha-icon icon="mdi:cloud-off-outline"></ha-icon>
            ${host._t(
              "recipes_catalog_unreachable",
              "Couldn't reach the recipes catalog:",
            )}
            ${host._recipesCatalogError}
          </div>`
        : !cat
          ? html`<div class="catalog-loading">
              ${host._recipesCatalogBusy
                ? host._t("recipes_catalog_fetching", "Fetching catalog…")
                : host._t(
                    "recipes_catalog_will_load",
                    "Catalog will load here.",
                  )}
            </div>`
          : filtered.length === 0
            ? (host._recipesCatalogSearch || "").trim()
              ? html`<div class="catalog-loading">
                  ${host._t(
                    "recipes_catalog_no_matches_prefix",
                    "No matches for",
                  )}
                  &ldquo;${host._recipesCatalogSearch}&rdquo;.
                </div>`
              : html`<div class="catalog-loading">
                  ${host._t(
                    "recipes_catalog_empty",
                    "No recipes in this catalog yet.",
                  )}
                </div>`
            : _renderCatalogResults(host, filtered, installedSlugs)}
    </div>
  `;
}

// How many catalog recipes show per page below the featured strip.
const _CATALOG_PAGE_SIZE = 6;

// Split the catalog into a featured strip (the 2 newest by release date) and a
// paginated list of the rest. While a search is active there's no featured
// strip — every match is just paginated.
function _renderCatalogResults(host, filtered, installedSlugs) {
  const searching = !!(host._recipesCatalogSearch || "").trim();

  let featured = [];
  let rest = filtered;
  if (!searching) {
    const byDate = [...filtered].sort((a, b) =>
      String(b.released || "").localeCompare(String(a.released || "")),
    );
    featured = byDate.slice(0, 2);
    rest = byDate.slice(2);
  }

  const totalPages = Math.max(1, Math.ceil(rest.length / _CATALOG_PAGE_SIZE));
  const page = Math.min(Math.max(1, host._catalogPage || 1), totalPages);
  const start = (page - 1) * _CATALOG_PAGE_SIZE;
  const pageItems = rest.slice(start, start + _CATALOG_PAGE_SIZE);

  const card = (entry, isFeatured) =>
    _renderCatalogCard(host, entry, installedSlugs.has(entry.slug), isFeatured);

  return html`
    ${featured.length
      ? html`
          <div class="recipes-section-title">
            ${host._t("recipes_catalog_featured", "Featured")}
          </div>
          <div class="catalog-grid catalog-grid-featured">
            ${featured.map((e) => card(e, true))}
          </div>
        `
      : ""}
    ${rest.length
      ? html`
          ${!searching
            ? html`<div class="recipes-section-title">
                ${host._t("recipes_catalog_all", "All recipes")}
              </div>`
            : ""}
          <div class="catalog-grid">
            ${pageItems.map((e) => card(e, false))}
          </div>
          ${totalPages > 1
            ? _renderCatalogPagination(host, page, totalPages)
            : ""}
        `
      : ""}
  `;
}

function _renderCatalogPagination(host, page, totalPages) {
  return html`
    <div class="catalog-pagination">
      <button
        class="btn btn-outline"
        ?disabled=${page <= 1}
        @click=${() => host._setCatalogPage(page - 1)}
      >
        <ha-icon icon="mdi:chevron-left"></ha-icon>
        ${host._t("recipes_catalog_prev", "Previous")}
      </button>
      <span class="catalog-page-indicator">
        ${host._t("recipes_catalog_page", "Page")} ${page} / ${totalPages}
      </span>
      <button
        class="btn btn-outline"
        ?disabled=${page >= totalPages}
        @click=${() => host._setCatalogPage(page + 1)}
      >
        ${host._t("recipes_catalog_next", "Next")}
        <ha-icon icon="mdi:chevron-right"></ha-icon>
      </button>
    </div>
  `;
}

// Category → glyph for the catalog card's icon tile. Falls back to a
// generic chef-hat (recipe) when the category isn't recognised.
const _CATALOG_CATEGORY_ICON = {
  safety: "mdi:shield-home",
  security: "mdi:shield-lock",
  weather: "mdi:weather-partly-snowy-rainy",
  routine: "mdi:calendar-clock",
  routines: "mdi:calendar-clock",
  comfort: "mdi:sofa",
  energy: "mdi:lightning-bolt",
  lighting: "mdi:lightbulb-group",
  presence: "mdi:account-group",
};

function _catalogCategoryIcon(entry) {
  const key = (entry.category || entry.category_title || "").toLowerCase();
  return _CATALOG_CATEGORY_ICON[key] || "mdi:chef-hat";
}

function _renderCatalogCard(host, entry, alreadyInstalled, featured = false) {
  return html`
    <div class="catalog-card ${featured ? "catalog-card-featured" : ""}">
      <div class="catalog-card-top">
        <div class="catalog-card-icon">
          <ha-icon icon=${_catalogCategoryIcon(entry)}></ha-icon>
        </div>
        ${entry.category_title
          ? html`<span class="catalog-card-category"
              >${entry.category_title}</span
            >`
          : ""}
      </div>
      <div class="catalog-card-title">${entry.title}</div>
      <div class="catalog-card-meta">
        v${entry.version}${entry.released ? ` · ${entry.released}` : ""}
      </div>
      ${entry.description
        ? html`<div class="catalog-card-desc">${entry.description}</div>`
        : ""}
      ${entry.tags?.length
        ? html`<div class="catalog-card-tags">
            ${entry.tags.map(
              (t) => html`<span class="catalog-tag">${t}</span>`,
            )}
          </div>`
        : ""}
      <div class="catalog-card-actions">
        ${alreadyInstalled
          ? html`<span class="catalog-installed-badge">
              <ha-icon icon="mdi:check"></ha-icon>
              ${host._t("recipes_card_installed_badge", "Installed")}
            </span>`
          : html`<button
              class="btn btn-primary catalog-install-btn"
              @click=${() => host._installFromCatalogEntry(entry)}
              ?disabled=${host._recipesBusy || host._recipesUrlBusy}
            >
              ${host._t("recipes_card_install_button", "Install")}
            </button>`}
      </div>
    </div>
  `;
}

// ── Wizard view ────────────────────────────────────────────────────

function _renderInputField(host, input) {
  const value =
    host._recipeWizardInputs[input.id] !== undefined
      ? host._recipeWizardInputs[input.id]
      : input.default;
  const onInput = (e) => {
    const raw = e.target.value;
    let v = raw;
    if (input.type === "number") v = raw === "" ? "" : Number(raw);
    if (input.type === "boolean") v = e.target.checked;
    // Route through the panel method so the change gets persisted
    // to localStorage along with the inputs map. Without this, the
    // user reloads mid-Settings and their typed values are gone.
    host._updateRecipeInput(input.id, v);
  };

  if (input.type === "boolean") {
    return html`
      <div class="wizard-field">
        <label style="display:flex;gap:8px;align-items:center;">
          <input type="checkbox" .checked=${!!value} @change=${onInput} />
          ${input.label}
        </label>
        ${input.description
          ? html`<span class="hint">${input.description}</span>`
          : ""}
      </div>
    `;
  }

  if (input.type === "select") {
    return html`
      <div class="wizard-field">
        <label>${input.label}</label>
        ${input.description
          ? html`<span class="hint">${input.description}</span>`
          : ""}
        <select .value=${String(value ?? "")} @change=${onInput}>
          ${(input.choices || []).map(
            (choice) => html`<option value=${choice}>${choice}</option>`,
          )}
        </select>
      </div>
    `;
  }

  return html`
    <div class="wizard-field">
      <label>${input.label}</label>
      ${input.description
        ? html`<span class="hint">${input.description}</span>`
        : ""}
      <input
        type=${input.type === "number" ? "number" : "text"}
        .value=${String(value ?? "")}
        min=${input.min ?? ""}
        max=${input.max ?? ""}
        @input=${onInput}
      />
    </div>
  `;
}

// Integration → homeowner copy + the HA setup page that owns
// "add a device" for that integration. Clicking the primary CTA on a
// pending-binding card sends the user straight there instead of
// burying the instruction in prose and asking them to find their own
// way to Settings → Devices & Services.
function _integrationPairCopy(host) {
  return {
    hue: {
      label: "Philips Hue",
      action: host._t(
        "recipes_pair_hue_action",
        "Pair this bulb to your Hue Bridge using the Hue app or its setup page.",
      ),
      cta: host._t("recipes_pair_hue_cta", "Open Hue setup"),
    },
    zha: {
      label: "Zigbee (ZHA)",
      action: host._t(
        "recipes_pair_zha_action",
        "Open ZHA's setup page and put the coordinator in pairing mode to add this device.",
      ),
      cta: host._t("recipes_pair_zha_cta", "Open ZHA setup"),
    },
    zwave_js: {
      label: "Z-Wave",
      action: host._t(
        "recipes_pair_zwave_action",
        "Start Z-Wave inclusion from its setup page to add this device.",
      ),
      cta: host._t("recipes_pair_zwave_cta", "Open Z-Wave setup"),
    },
    matter: {
      label: "Matter",
      action: host._t(
        "recipes_pair_matter_action",
        "Commission this Matter device with its pairing code from the Matter setup page.",
      ),
      cta: host._t("recipes_pair_matter_cta", "Open Matter setup"),
    },
    mqtt: {
      label: "MQTT",
      action: host._t(
        "recipes_pair_mqtt_action",
        "Bring this MQTT device online so it publishes to the configured topic.",
      ),
      cta: host._t("recipes_pair_mqtt_cta", "Open MQTT setup"),
    },
  };
}

// Deep-link path inside HA. Same shape for every integration —
// ``/config/integrations/integration/<domain>`` lands on that
// integration's HA settings page where the user finds the "Add
// Device" affordance HA itself ships.
function _integrationSetupPath(integration) {
  if (!integration) return "/config/integrations/dashboard";
  return `/config/integrations/integration/${integration}`;
}

// HA is a single-page app. ``history.pushState`` + the
// ``location-changed`` event is the documented way to drive HA's
// router from a custom panel without a hard page reload.
function _navigateInHA(path) {
  history.pushState(null, "", path);
  window.dispatchEvent(new Event("location-changed"));
}

function _humaniseObjectId(entityId) {
  // ``light.bedroom_lamp`` → ``Bedroom lamp``. Used as the card's
  // friendly title fallback when the manifest didn't include a note.
  const obj = entityId.includes(".")
    ? entityId.slice(entityId.indexOf(".") + 1)
    : entityId;
  const spaced = obj.replace(/_/g, " ").trim();
  if (!spaced) return entityId;
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function _renderPunchList(host, items) {
  if (!items || items.length === 0) return "";
  return html`
    <div class="wizard-section">
      <h3>${host._t("recipes_punch_list_title", "Punch list")}</h3>
      <div class="punch">
        ${items.map(
          (item) => html`
            <div class="punch-item">
              <span class="stage-pill">${item.stage}</span>
              <div>
                ${item.target
                  ? html`<strong>${item.target}</strong>: `
                  : ""}${item.message}
              </div>
            </div>
          `,
        )}
      </div>
    </div>
  `;
}

// ── Pipeline view (CI-style three-column install board) ─────────────
//
// Reads ``preview.items`` from the backend (one row per Prepare /
// Configure / Apply step) and renders three columns. The user clicks
// any item to focus it; the action panel below shows the right UI
// (inputs form, role-selection toggles, pair-device card, inline
// integration config flow). One scrollbar on the page itself —
// inside columns we just stack rows.

const _STAGE_ORDER = ["prepare", "configure", "apply"];
function _stageLabel(host, stage) {
  return (
    {
      prepare: host._t("recipes_stage_prepare", "Prepare"),
      configure: host._t("recipes_stage_configure", "Configure"),
      apply: host._t("recipes_stage_apply", "Apply"),
    }[stage] || stage
  );
}

const _STATUS_ICON = {
  ok: "mdi:check-circle",
  failed: "mdi:close-circle",
  running: "mdi:progress-clock",
  needs_input: "mdi:alert-circle",
  skipped: "mdi:circle-slice-8",
  pending: "mdi:circle-outline",
};

function _statusLabel(host, status) {
  return (
    {
      ok: host._t("recipes_status_done", "Done"),
      failed: host._t("recipes_status_failed", "Failed"),
      running: host._t("recipes_status_running", "Running"),
      needs_input: host._t("recipes_status_needs_you", "Needs you"),
      skipped: host._t("recipes_status_skipped", "Skipped"),
      pending: host._t("recipes_status_pending", "Pending"),
    }[status] || status
  );
}

// Item the action panel focuses by default. Priority:
//   1. The user's explicit click (host._recipeActiveItemId)
//   2. The first item still needing input or failed
//   3. The first running item
//   4. The first pending item (so the user can preview what's next)
//   5. The last ok item (so the panel isn't blank when everything's done)
function _activeItem(host, items) {
  if (!items?.length) return null;
  if (host._recipeActiveItemId) {
    const found = items.find((i) => i.id === host._recipeActiveItemId);
    if (found) return found;
  }
  return (
    items.find((i) => i.status === "needs_input" || i.status === "failed") ||
    items.find((i) => i.status === "running") ||
    items.find((i) => i.status === "pending") ||
    items[items.length - 1]
  );
}

function _renderPipelineItem(host, item, active) {
  return html`
    <button
      type="button"
      class="pi-row pi-${item.status} ${active ? "is-active" : ""}"
      @click=${() => {
        host._recipeActiveItemId = item.id;
      }}
    >
      <ha-icon
        class="pi-status-icon"
        icon=${_STATUS_ICON[item.status] || "mdi:circle-outline"}
      ></ha-icon>
      <div class="pi-row-body">
        <div class="pi-row-title">${item.title}</div>
        ${item.detail
          ? html`<div class="pi-row-detail">${item.detail}</div>`
          : ""}
      </div>
    </button>
  `;
}

function _renderStageColumn(host, stage, items, activeId) {
  const stageItems = items.filter((i) => i.stage === stage);
  if (stageItems.length === 0) return "";
  const ok = stageItems.every(
    (i) => i.status === "ok" || i.status === "skipped",
  );
  const failed = stageItems.some((i) => i.status === "failed");
  const cls = failed
    ? "pi-stage-failed"
    : ok
      ? "pi-stage-ok"
      : "pi-stage-active";
  return html`
    <div class="pi-stage ${cls}">
      <div class="pi-stage-head">${_stageLabel(host, stage)}</div>
      <div class="pi-stage-items">
        ${stageItems.map((it) =>
          _renderPipelineItem(host, it, it.id === activeId),
        )}
      </div>
    </div>
  `;
}

// ── Action panels ──────────────────────────────────────────────────
// One panel per item kind. The pipeline view picks an active item and
// passes it to ``_renderActionPanel``, which dispatches by kind.

function _renderActionPanel(host, item) {
  if (!item) return "";
  if (item.kind === "inputs") return _renderInputsPanel(host, item);
  if (item.kind === "role_selection")
    return _renderRoleSelectionPanel(host, item);
  if (item.kind === "pin") return _renderPinPanel(host, item);
  if (item.kind === "integration") return _renderIntegrationPanel(host, item);
  return _renderSystemPanel(host, item);
}

function _panelShell(host, title, statusKind, body, footer) {
  return html`
    <div class="panel-shell">
      <div class="panel-head">
        <div class="panel-title">${title}</div>
        ${statusKind
          ? html`<span class="panel-status ${statusKind}"
              >${_statusLabel(host, statusKind)}</span
            >`
          : ""}
      </div>
      <div class="panel-body">${body}</div>
      ${footer ? html`<div class="panel-footer">${footer}</div>` : ""}
    </div>
  `;
}

function _renderSystemPanel(host, item) {
  // Backend-only items have nothing to interact with — just narrate.
  return _panelShell(
    host,
    item.title,
    item.status,
    item.detail
      ? html`<p class="panel-prose">${item.detail}</p>`
      : html`<p class="panel-prose panel-muted">
          ${host._t(
            "recipes_system_panel_auto",
            "This step runs automatically. No action needed from you.",
          )}
        </p>`,
    null,
  );
}

function _renderInputsPanel(host, item) {
  const inputs = item.payload?.inputs || [];
  return _panelShell(
    host,
    host._t("recipes_inputs_panel_title", "Recipe settings"),
    item.status,
    html`<div class="panel-fields">
      ${inputs.map((input) => _renderInputField(host, input))}
    </div>`,
    null,
  );
}

// Human-friendly description of what the role looks for. Prefer the
// device_class (more specific, e.g. "occupancy sensor") over the bare
// kind ("binary_sensor"). Falls back to the kind when no class is set.
function _humaniseRoleFilter(host, role) {
  const dc = role.device_class;
  if (dc) {
    // Most HA device_class strings are already homeowner-friendly
    // ("occupancy", "moisture", "door"). Just append a noun.
    const noun =
      role.kind === "binary_sensor" || role.kind === "sensor"
        ? host._t("recipes_role_filter_sensor_noun", "sensor")
        : role.kind;
    return `${dc.replace(/_/g, " ")} ${noun}`;
  }
  return (
    role.kind?.replace(/_/g, " ") ||
    host._t("recipes_role_filter_device_noun", "device")
  );
}

function _renderRoleSelectionPanel(host, item) {
  const role = item.payload?.role || {};
  const candidates = item.payload?.candidates || [];
  const pinned = item.payload?.pinned || [];
  const bound = new Set(item.payload?.bound || []);
  const selected = new Set((host._recipeWizardSelections || {})[role.id] || []);
  const filterLabel = _humaniseRoleFilter(host, role);

  // Candidate list controls. Broad roles (e.g. every ``sensor`` in the
  // home) can surface dozens of chips — that's an unscannable wall, so:
  //  - a name filter narrows by friendly name OR entity id, and
  //  - the list is capped at CAP chips with a "Show all" toggle.
  // Selected chips always float to the top so collapsing never hides a
  // pick, and an active filter implicitly expands (no point capping a
  // deliberately-narrowed list).
  const roleId = role.id;
  const filterText = (host._recipeRoleFilters?.[roleId] || "")
    .trim()
    .toLowerCase();
  const candidatesT = item.payload?.candidates || [];
  const matched = filterText
    ? candidatesT.filter((id) => {
        const name = String(_entityFriendlyName(host.hass, id) || "");
        return (
          id.toLowerCase().includes(filterText) ||
          name.toLowerCase().includes(filterText)
        );
      })
    : candidatesT;
  const ordered = [...matched].sort(
    (a, b) => (selected.has(b) ? 1 : 0) - (selected.has(a) ? 1 : 0),
  );
  const CHIP_CAP = 12;
  const expanded = !!host._recipeRoleExpanded?.[roleId] || !!filterText;
  const selectedInMatch = ordered.filter((id) => selected.has(id)).length;
  const shown = expanded
    ? ordered
    : ordered.slice(0, Math.max(CHIP_CAP, selectedInMatch));
  const hiddenCount = ordered.length - shown.length;
  const showFilter = candidatesT.length > CHIP_CAP;

  // Show pinned chips locked, then candidates as toggles.
  return _panelShell(
    host,
    `${host._t("recipes_role_pick_prefix", "Pick:")} ${item.title}`,
    item.status,
    html`
      ${role.description
        ? html`<p class="panel-prose">${role.description}</p>`
        : ""}
      ${pinned.length > 0
        ? html`
            <div class="panel-prose panel-muted">
              ${pinned.length}
              ${host._t(
                "recipes_role_pinned_count_suffix",
                "pinned by the recipe (always included).",
              )}
            </div>
            <div class="panel-chips">
              ${pinned.map(
                (id) => html`
                  <span class="role-entity-chip is-pinned" title=${id}>
                    <span class="chip-icon-tile">
                      <ha-icon icon=${_entityIcon(host.hass, id)}></ha-icon>
                    </span>
                    <span class="chip-text">
                      <span class="chip-name">
                        ${_entityFriendlyName(host.hass, id)}
                        <ha-icon
                          class="pin-badge"
                          icon="mdi:lock-outline"
                        ></ha-icon>
                      </span>
                      <span class="chip-id">${id}</span>
                    </span>
                  </span>
                `,
              )}
            </div>
          `
        : ""}
      ${candidates.length > 0
        ? html`
            <p class="panel-prose panel-muted">
              ${host._t("recipes_role_pick_one_or_more", "Pick one or more")}
              ${filterLabel}${role.max_count
                ? ` (${host._t("recipes_role_up_to", "up to")} ${role.max_count})`
                : ""}.
              ${host._t(
                "recipes_role_run_against",
                "Selora will run the recipe against the ones you tick.",
              )}
            </p>
            ${showFilter
              ? html`<input
                  class="role-filter-input"
                  type="text"
                  .value=${host._recipeRoleFilters?.[roleId] || ""}
                  placeholder=${host._t(
                    "recipes_role_filter_placeholder",
                    "Filter by name…",
                  )}
                  @input=${(e) =>
                    host._setRecipeRoleFilter(roleId, e.target.value)}
                />`
              : ""}
            ${ordered.length === 0
              ? html`<p class="panel-prose panel-muted">
                  ${host._t(
                    "recipes_role_filter_no_matches",
                    "No entities match your filter.",
                  )}
                </p>`
              : html`
                  <div class="panel-chips">
                    ${shown.map((id) => {
                      // Chip state mirrors the user's selection set only —
                      // ``bound`` is what the backend confirmed and lags
                      // by one round-trip, so reading from it would make
                      // chips "stick" after a rolling-window drop.
                      const on = selected.has(id);
                      return html`
                        <button
                          class="role-entity-chip role-entity-toggle ${on
                            ? "is-on"
                            : ""}"
                          type="button"
                          title=${id}
                          @click=${() =>
                            host._toggleRecipeRoleEntity(
                              role.id,
                              id,
                              role.max_count,
                            )}
                          ?disabled=${host._recipesBusy}
                        >
                          <span class="chip-icon-tile">
                            <ha-icon
                              icon=${_entityIcon(host.hass, id)}
                            ></ha-icon>
                          </span>
                          <span class="chip-text">
                            <span class="chip-name">
                              ${_entityFriendlyName(host.hass, id)}
                            </span>
                            <span class="chip-id">${id}</span>
                          </span>
                        </button>
                      `;
                    })}
                  </div>
                  ${hiddenCount > 0
                    ? html`<button
                        class="role-show-more"
                        type="button"
                        @click=${() => host._toggleRecipeRoleExpanded(roleId)}
                      >
                        ${host._t("recipes_role_show_all", "Show all")}
                        (${ordered.length})
                      </button>`
                    : expanded && !filterText && candidatesT.length > CHIP_CAP
                      ? html`<button
                          class="role-show-more"
                          type="button"
                          @click=${() => host._toggleRecipeRoleExpanded(roleId)}
                        >
                          ${host._t("recipes_role_show_less", "Show less")}
                        </button>`
                      : ""}
                `}
          `
        : ""}
    `,
    candidates.length === 0
      ? html`
          <div class="role-empty-help">
            <ha-icon icon="mdi:radar" class="role-empty-icon"></ha-icon>
            <div class="role-empty-body">
              <div class="role-empty-title">
                ${host._t("recipes_role_empty_none_prefix", "No")}
                ${filterLabel}s
                ${host._t("recipes_role_empty_none_suffix", "in your home yet")}
              </div>
              <p class="role-empty-prose">
                ${host._t("recipes_role_empty_pair", "Pair")}
                ${role.min_count > 0
                  ? html`${host._t("recipes_role_empty_at_least", "at least")} `
                  : ""}${host._t(
                  "recipes_role_empty_one_prose",
                  "one and it'll appear here automatically — you can leave this page to add a device and the wizard keeps your progress on Back.",
                )}
              </p>
              <div class="role-empty-actions">
                <button
                  class="panel-btn primary"
                  type="button"
                  @click=${() =>
                    _navigateInHA("/config/integrations/dashboard")}
                >
                  <ha-icon icon="mdi:plus-circle-outline"></ha-icon>
                  ${host._t(
                    "recipes_role_empty_add_device",
                    "Add device in HA",
                  )}
                </button>
                <button
                  class="panel-btn secondary"
                  type="button"
                  ?disabled=${host._recipesBusy}
                  @click=${() => host._refreshRecipePreview()}
                >
                  <ha-icon icon="mdi:refresh"></ha-icon>
                  ${host._t("recipes_role_check_again", "Check again")}
                </button>
              </div>
            </div>
          </div>
        `
      : null,
  );
}

function _renderPinPanel(host, item) {
  const id = item.payload?.identity || {};
  const integration = id.integration || "";
  const copy = _integrationPairCopy(host)[integration];
  const integrationLabel = copy?.label || integration;
  const setupPath = _integrationSetupPath(integration);
  const model = [id.manufacturer, id.model].filter(Boolean).join(" ");
  const action =
    copy?.action ||
    host._t(
      "recipes_pin_default_action",
      "Add this device to Home Assistant. Selora will detect it automatically.",
    );

  return _panelShell(
    host,
    item.title,
    item.status,
    html`
      ${model ? html`<div class="panel-prose panel-muted">${model}</div>` : ""}
      <p class="panel-prose">${action}</p>
      <p class="panel-prose panel-muted">
        ${host._t("recipes_pin_expected_entity", "Expected entity id:")}
        <code>${id.entity_id}</code>
      </p>
      <p class="panel-prose panel-muted">
        ${host._t(
          "recipes_pin_tip",
          "Tip: leave this tab open. When the device pairs, this step ticks itself off — no need to click anything here.",
        )}
      </p>
    `,
    html`
      <button
        class="panel-btn primary"
        type="button"
        @click=${() => _navigateInHA(setupPath)}
      >
        <ha-icon icon="mdi:tools"></ha-icon>
        ${copy?.cta ||
        (integration
          ? `${host._t("recipes_pin_open_setup_prefix", "Open")} ${integrationLabel} ${host._t("recipes_pin_open_setup_suffix", "setup")}`
          : host._t(
              "recipes_pin_open_ha_integrations",
              "Open HA integrations",
            ))}
      </button>
      <button
        class="panel-btn secondary"
        type="button"
        ?disabled=${host._recipesBusy}
        @click=${() => host._refreshRecipePreview()}
      >
        <ha-icon icon="mdi:refresh"></ha-icon>
        ${host._t("recipes_pin_check_now", "Check now")}
      </button>
    `,
  );
}

function _renderIntegrationPanel(host, item) {
  const domain = item.payload?.domain || "";
  const copy = _integrationPairCopy(host)[domain];
  const flow = (host._recipeFlows || {})[domain];

  // Configured — show what got set up so the homeowner can see
  // confirmation of the values (not just an opaque "configured" badge).
  if (item.status === "ok") {
    const entryTitle = item.payload?.entry_title || "";
    const label = copy?.label || domain;
    return _panelShell(
      host,
      item.title,
      item.status,
      html`
        <p class="panel-prose">
          ${label}
          ${host._t("recipes_integration_ready", "is set up and ready to use.")}
        </p>
        ${entryTitle
          ? html`<div class="integration-entry-meta">
              <ha-icon icon="mdi:identifier"></ha-icon>
              <span class="panel-muted"
                >${host._t("recipes_integration_entry_label", "Entry:")}</span
              >
              <code>${entryTitle}</code>
            </div>`
          : ""}
        <p class="panel-prose panel-muted">
          ${host._t(
            "recipes_integration_manage_anytime",
            "Manage this integration anytime from Settings → Devices & Services.",
          )}
        </p>
      `,
      html`
        <button
          class="panel-btn secondary"
          type="button"
          @click=${() => _navigateInHA(_integrationSetupPath(domain))}
        >
          <ha-icon icon="mdi:open-in-new"></ha-icon>
          ${host._t("recipes_integration_open_in_ha", "Open in HA")}
        </button>
      `,
    );
  }

  // No flow started yet — offer to start one inline.
  if (!flow) {
    // Auto-setup recipes drive the flow backend-side with values the
    // recipe already knows (api_key, lat/lon, resolved station, etc.).
    // The homeowner doesn't see a form at all.
    const autoSetup = item.payload?.auto_setup === true;
    return _panelShell(
      host,
      item.title,
      item.status,
      html`
        <p class="panel-prose">
          ${autoSetup
            ? `${item.title} ${host._t("recipes_integration_autosetup_prose", "can be set up automatically using your Home Assistant location. No questions for you to answer.")}`
            : `${item.title} ${host._t("recipes_integration_needs_setup_prose", "needs to be set up before this recipe can install. You can start it without leaving this page.")}`}
        </p>
        ${flow?.error ? html`<div class="panel-error">${flow.error}</div>` : ""}
      `,
      html`
        <button
          class="panel-btn primary"
          type="button"
          ?disabled=${host._recipesBusy}
          @click=${() =>
            autoSetup
              ? host._autoSetupIntegration(domain)
              : host._startIntegrationFlow(domain)}
        >
          ${host._recipesBusy
            ? html`<span class="spinner"></span>`
            : html`<ha-icon
                icon=${autoSetup ? "mdi:auto-fix" : "mdi:play"}
              ></ha-icon>`}
          ${host._recipesBusy
            ? host._t("recipes_setting_up_button", "Setting up…")
            : autoSetup
              ? host._t(
                  "recipes_integration_setup_auto_button",
                  "Set up automatically",
                )
              : `${host._t("recipes_integration_setup_prefix", "Set up")} ${copy?.label || domain}`}
        </button>
        <button
          class="panel-btn secondary"
          type="button"
          @click=${() => _navigateInHA(_integrationSetupPath(domain))}
        >
          <ha-icon icon="mdi:open-in-new"></ha-icon>
          ${host._t(
            "recipes_integration_open_in_ha_settings",
            "Open in HA settings",
          )}
        </button>
      `,
    );
  }

  // A flow is in progress — render its current step.
  if (flow.state === "form") {
    return _renderFlowForm(host, item, flow);
  }
  if (flow.state === "error") {
    return _panelShell(
      host,
      item.title,
      "failed",
      html`<p class="panel-prose">
        ${flow.error ||
        host._t(
          "recipes_integration_setup_failed",
          "Setup failed. Try again or use HA's settings page.",
        )}
      </p>`,
      html`
        <button
          class="panel-btn secondary"
          type="button"
          @click=${() => host._resetIntegrationFlow(domain)}
        >
          ${host._t("recipes_integration_try_again", "Try again")}
        </button>
      `,
    );
  }
  if (flow.state === "complete") {
    return _panelShell(
      host,
      item.title,
      "ok",
      html`<p class="panel-prose">
        ${copy?.label || domain}
        ${host._t(
          "recipes_integration_was_set_up",
          "was set up. Selora will re-check the recipe automatically.",
        )}
      </p>`,
      null,
    );
  }
  return _panelShell(
    host,
    item.title,
    "running",
    html`<p>${host._t("recipes_working", "Working…")}</p>`,
    null,
  );
}

// ── Inline HA config-flow renderer ─────────────────────────────────
// HA's WS API returns a step with a ``data_schema`` array of field
// dicts (name, type, default, required, options…). We render those
// inline as a form, submit via ``config_entries/flow/configure``, and
// loop until the flow reaches ``create_entry`` (success) or ``abort``
// (failure).

function _renderFlowForm(host, item, flow) {
  const fields = flow.step?.data_schema || [];
  const values = flow.values || {};
  const errors = flow.step?.errors || {};
  return _panelShell(
    host,
    item.title,
    "needs_input",
    html`
      ${flow.step?.description
        ? html`<p class="panel-prose">${flow.step.description}</p>`
        : ""}
      ${errors.base ? html`<div class="panel-error">${errors.base}</div>` : ""}
      <div class="panel-fields">
        ${fields.map((f) =>
          _renderFlowField(host, item, flow, f, values[f.name], errors[f.name]),
        )}
      </div>
    `,
    html`
      <button
        class="panel-btn secondary"
        type="button"
        @click=${() => host._abortIntegrationFlow(item.payload.domain)}
      >
        ${host._t("recipes_flow_cancel", "Cancel")}
      </button>
      <button
        class="panel-btn primary"
        type="button"
        ?disabled=${host._recipesBusy}
        @click=${() => host._submitIntegrationFlow(item.payload.domain)}
      >
        ${host._recipesBusy
          ? host._t("recipes_working", "Working…")
          : host._t("recipes_flow_continue", "Continue")}
      </button>
    `,
  );
}

function _renderFlowField(host, item, flow, field, value, error) {
  const update = (v) => {
    const next = { ...(flow.values || {}), [field.name]: v };
    host._recipeFlows = {
      ...(host._recipeFlows || {}),
      [item.payload.domain]: { ...flow, values: next },
    };
  };
  const ftype = field.type || (field.selector ? "select" : "string");
  let control;
  if (ftype === "boolean") {
    control = html`<input
      type="checkbox"
      .checked=${value ?? field.default ?? false}
      @change=${(e) => update(e.target.checked)}
    />`;
  } else if (ftype === "integer" || ftype === "number") {
    control = html`<input
      type="number"
      .value=${String(value ?? field.default ?? "")}
      @input=${(e) =>
        update(e.target.value === "" ? null : Number(e.target.value))}
    />`;
  } else if (field.options || ftype === "select") {
    control = html`<select
      .value=${String(value ?? field.default ?? "")}
      @change=${(e) => update(e.target.value)}
    >
      ${(field.options || []).map(
        (opt) => html`
          <option value=${opt.value ?? opt}>${opt.label ?? opt}</option>
        `,
      )}
    </select>`;
  } else {
    control = html`<input
      type="text"
      .value=${String(value ?? field.default ?? "")}
      @input=${(e) => update(e.target.value)}
    />`;
  }
  return html`
    <label class="panel-field">
      <span class="panel-field-label">
        ${field.description || field.name}
        ${field.required === false
          ? html`<em class="panel-field-optional"
              >${host._t("recipes_field_optional", "(optional)")}</em
            >`
          : ""}
      </span>
      ${control}
      ${error ? html`<span class="panel-field-error">${error}</span>` : ""}
    </label>
  `;
}

// ── 4-step wizard ──────────────────────────────────────────────────
//
// Linear flow: Overview → Match → Resolve → Activate. Each step is a
// dedicated screen with its own purpose. The stepper bar at the top
// shows progress; the footer carries the Back / Next gate. Step 3
// auto-kicks the install_stream and auto-advances to Step 4 on
// success; Step 4's [Activate Recipe] closes the wizard to the
// success view (install + enable were already done in step 3).

function _stepLabels(host) {
  return [
    host._t("recipes_step_overview", "Overview"),
    host._t("recipes_step_settings", "Settings"),
    host._t("recipes_step_match", "Match"),
    host._t("recipes_step_set_up", "Set up"),
    host._t("recipes_step_activate", "Activate"),
  ];
}

function _humaniseSection(host, key, n) {
  // Map HA package top-level keys to user-facing copy.
  const map = {
    automation: [
      host._t("recipes_section_automation_singular", "automation"),
      host._t("recipes_section_automation_plural", "automations"),
    ],
    script: [
      host._t("recipes_section_script_singular", "script"),
      host._t("recipes_section_script_plural", "scripts"),
    ],
    scene: [
      host._t("recipes_section_scene_singular", "scene"),
      host._t("recipes_section_scene_plural", "scenes"),
    ],
    sensor: [
      host._t("recipes_section_sensor_singular", "sensor"),
      host._t("recipes_section_sensor_plural", "sensors"),
    ],
    binary_sensor: [
      host._t("recipes_section_binary_sensor_singular", "binary sensor"),
      host._t("recipes_section_binary_sensor_plural", "binary sensors"),
    ],
    input_boolean: [
      host._t("recipes_section_helper_singular", "helper"),
      host._t("recipes_section_helper_plural", "helpers"),
    ],
    input_number: [
      host._t("recipes_section_helper_singular", "helper"),
      host._t("recipes_section_helper_plural", "helpers"),
    ],
    input_text: [
      host._t("recipes_section_helper_singular", "helper"),
      host._t("recipes_section_helper_plural", "helpers"),
    ],
    input_select: [
      host._t("recipes_section_helper_singular", "helper"),
      host._t("recipes_section_helper_plural", "helpers"),
    ],
    input_datetime: [
      host._t("recipes_section_helper_singular", "helper"),
      host._t("recipes_section_helper_plural", "helpers"),
    ],
    timer: [
      host._t("recipes_section_timer_singular", "timer"),
      host._t("recipes_section_timer_plural", "timers"),
    ],
    counter: [
      host._t("recipes_section_counter_singular", "counter"),
      host._t("recipes_section_counter_plural", "counters"),
    ],
    template: [
      host._t("recipes_section_template_singular", "template entity"),
      host._t("recipes_section_template_plural", "template entities"),
    ],
    group: [
      host._t("recipes_section_group_singular", "group"),
      host._t("recipes_section_group_plural", "groups"),
    ],
    notify: [
      host._t("recipes_section_notify_singular", "notifier"),
      host._t("recipes_section_notify_plural", "notifiers"),
    ],
    light: [
      host._t("recipes_section_light_singular", "light"),
      host._t("recipes_section_light_plural", "lights"),
    ],
    switch: [
      host._t("recipes_section_switch_singular", "switch"),
      host._t("recipes_section_switch_plural", "switches"),
    ],
    cover: [
      host._t("recipes_section_cover_singular", "cover"),
      host._t("recipes_section_cover_plural", "covers"),
    ],
    climate: [
      host._t("recipes_section_climate_singular", "climate entity"),
      host._t("recipes_section_climate_plural", "climate entities"),
    ],
    media_player: [
      host._t("recipes_section_media_player_singular", "media player"),
      host._t("recipes_section_media_player_plural", "media players"),
    ],
    rest_command: [
      host._t("recipes_section_rest_command_singular", "REST command"),
      host._t("recipes_section_rest_command_plural", "REST commands"),
    ],
    shell_command: [
      host._t("recipes_section_shell_command_singular", "shell command"),
      host._t("recipes_section_shell_command_plural", "shell commands"),
    ],
    homeassistant: [
      host._t("recipes_section_customisation_singular", "customisation"),
      host._t("recipes_section_customisation_plural", "customisations"),
    ],
  };
  const [singular, plural] = map[key] || [key, key];
  return `${n} ${n === 1 ? singular : plural}`;
}

// Icon for one of our supported role kinds. Falls back to a generic
// device icon when we don't have a specific match — kind is already
// validated by the manifest schema, so we don't need to handle every
// HA domain.
function _roleIconForKind(role) {
  const k = role.kind || "";
  const dc = role.device_class || "";
  // device_class overrides kind when relevant (e.g. binary_sensor +
  // door vs binary_sensor + occupancy land on different icons).
  const byClass = {
    door: "mdi:door",
    window: "mdi:window-closed-variant",
    motion: "mdi:motion-sensor",
    occupancy: "mdi:radar",
    presence: "mdi:radar",
    moisture: "mdi:water-alert",
    smoke: "mdi:smoke-detector",
    siren: "mdi:bullhorn",
    temperature: "mdi:thermometer",
    humidity: "mdi:water-percent",
    illuminance: "mdi:brightness-5",
    sound: "mdi:volume-high",
  };
  if (dc && byClass[dc]) return byClass[dc];
  return (
    {
      light: "mdi:lightbulb-outline",
      switch: "mdi:toggle-switch-outline",
      sensor: "mdi:gauge",
      binary_sensor: "mdi:radiobox-blank",
      media_player: "mdi:speaker",
      lock: "mdi:lock-outline",
      cover: "mdi:window-shutter",
      climate: "mdi:thermostat",
      fan: "mdi:fan",
      vacuum: "mdi:robot-vacuum",
      camera: "mdi:cctv",
      person: "mdi:account",
      device_tracker: "mdi:crosshairs-gps",
      zone: "mdi:map-marker-radius",
    }[k] || "mdi:devices"
  );
}

// "What you need" rail on Step 1. Mirrors selorahomes.com's recipe
// detail page: per-requirement cards (one per integration, one per
// role) instead of two stacked summary cards.
function _renderWhatYouNeedRail(host, manifest) {
  const integrations = manifest.integrations || [];
  const roles = manifest.roles || [];
  const required = roles.filter((r) => (r.min_count || 0) > 0);
  const optional = roles.filter((r) => (r.min_count || 0) === 0);
  // Roles with manifest pins are specific devices (Connect / installer
  // manifests pin a Hue bulb, an Aqara FP2, etc.) — colour them green
  // like the website's "Aqara Presence Sensor FP2" card. Roles without
  // pins are open device classes the homeowner can satisfy with any
  // matching entity in their home.
  const hasPin = (role) => Boolean((manifest.bindings || {})[role.id]?.length);
  return html`
    <aside class="need-rail">
      <div class="need-rail-title">
        ${host._t("recipes_what_you_need_title", "What you need")}
      </div>
      <div class="need-rail-list">
        ${integrations.map(
          (i) => html`
            <div class="need-card">
              <div class="need-card-icon need-card-icon--integration">
                <ha-icon icon="mdi:puzzle-outline"></ha-icon>
              </div>
              <div class="need-card-body">
                <div class="need-card-title">${i.title || i.domain}</div>
                ${i.title && i.title !== i.domain
                  ? html`<div class="need-card-meta">${i.domain}</div>`
                  : ""}
              </div>
            </div>
          `,
        )}
        ${required.map((r) => _renderNeedRoleCard(r, hasPin(r)))}
        ${optional.length
          ? html`
              <div class="need-rail-eyebrow">
                ${host._t("recipes_optional_eyebrow", "Optional")}
              </div>
              ${optional.map((r) => _renderNeedRoleCard(r, hasPin(r)))}
            `
          : ""}
      </div>
    </aside>
  `;
}

function _renderNeedRoleCard(role, pinned) {
  const count = role.min_count > 1 ? `${role.min_count}+ ` : "";
  // ``pinned`` roles tie to a specific device (manifest binding) and
  // get the green "specific device" treatment; open roles get teal.
  const variant = pinned ? "pin" : "role";
  return html`
    <div class="need-card">
      <div class="need-card-icon need-card-icon--${variant}">
        <ha-icon icon=${_roleIconForKind(role)}></ha-icon>
      </div>
      <div class="need-card-body">
        <div class="need-card-title">${count}${role.title || role.id}</div>
        ${role.description
          ? html`<div class="need-card-desc">${role.description}</div>`
          : ""}
      </div>
    </div>
  `;
}

function _renderWizardStepper(host) {
  // Vertical progress rail. Sits in the right column of the wizard
  // grid, sticky on tall viewports so it stays visible as the user
  // scrolls step content. Each row: status icon + label. Done rows
  // are clickable to jump back; future rows are disabled.
  const current = host._recipeWizardStep || 1;
  return html`
    <aside class="step-rail">
      <div class="step-rail-title">
        ${host._t("recipes_progress_title", "Progress")}
      </div>
      <div class="step-rail-list">
        ${_stepLabels(host).map((label, idx) => {
          const step = idx + 1;
          const state =
            step < current ? "done" : step === current ? "current" : "future";
          const clickable = step < current;
          const icon =
            state === "done"
              ? "mdi:check-circle"
              : state === "current"
                ? "mdi:circle-slice-8"
                : "mdi:circle-outline";
          return html`
            <button
              class="step-rail-row step-${state}"
              type="button"
              ?disabled=${!clickable}
              @click=${() => clickable && host._jumpToRecipeStep(step)}
            >
              <ha-icon class="step-rail-icon" icon=${icon}></ha-icon>
              <span class="step-rail-num">${step}</span>
              <span class="step-rail-label">${label}</span>
            </button>
          `;
        })}
      </div>
    </aside>
  `;
}

function _renderWizardFooter(host, opts) {
  const current = host._recipeWizardStep || 1;
  const { primary, primaryDisabled, hint, hideBack, hideSecondary } =
    opts || {};
  return html`
    <div class="step-footer">
      <div class="step-footer-hint">${hint || ""}</div>
      <div class="step-footer-actions">
        ${hideSecondary
          ? ""
          : current > 1 && !hideBack
            ? html`<button
                class="panel-btn secondary"
                type="button"
                @click=${() => host._retreatRecipeStep()}
              >
                ${host._t("recipes_footer_back", "Back")}
              </button>`
            : html`<button
                class="panel-btn secondary"
                type="button"
                @click=${() => host._closeRecipeWizard()}
              >
                ${host._t("recipes_footer_cancel", "Cancel")}
              </button>`}
        ${primary
          ? html`<button
              class="panel-btn primary"
              type="button"
              ?disabled=${primaryDisabled}
              @click=${primary.onClick}
            >
              ${primary.label}
              ${primary.icon
                ? html`<ha-icon icon=${primary.icon}></ha-icon>`
                : ""}
            </button>`
          : ""}
      </div>
    </div>
  `;
}

function _renderWizardHero(host, manifest, opts) {
  // Two modes:
  // - ``full`` (Step 1): mirrors the website detail page — eyebrow,
  //   big title, tagline, tags, description prose. The hero IS the
  //   page on Step 1.
  // - ``compact`` (Steps 2-5): one-line header so the workspace
  //   isn't dominated by the recipe identity. Just the title + a
  //   small version meta + a back link to Step 1 so the user can
  //   return to the overview without losing wizard state.
  const compact = opts?.compact === true;
  if (compact) {
    return html`
      <div class="wizard-header wizard-header-compact">
        <button
          class="wizard-back-compact"
          type="button"
          title=${host._t("recipes_back_to_overview", "Back to overview")}
          @click=${() => host._jumpToRecipeStep(1)}
        >
          <ha-icon icon="mdi:arrow-left"></ha-icon>
        </button>
        <div class="wizard-compact-meta">
          <div class="wizard-compact-title">${manifest.title}</div>
          ${manifest.version
            ? html`<div class="wizard-compact-version">
                v${manifest.version}
              </div>`
            : ""}
        </div>
      </div>
    `;
  }
  const released = manifest.released
    ? new Date(manifest.released).toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      })
    : null;
  return html`
    <div class="wizard-header">
      <div class="wizard-hero">
        <div class="wizard-eyebrow">
          <ha-icon icon="mdi:book-open-page-variant-outline"></ha-icon>
          <span class="wizard-eyebrow-tag"
            >${host._t("recipes_eyebrow_recipe", "RECIPE")}</span
          >
          ${manifest.version
            ? html`<span class="wizard-eyebrow-sep">·</span>
                <span class="wizard-eyebrow-meta">v${manifest.version}</span>`
            : ""}
          ${released
            ? html`<span class="wizard-eyebrow-sep">·</span>
                <span class="wizard-eyebrow-meta"
                  >${host._t("recipes_eyebrow_released", "Released")}
                  ${released}</span
                >`
            : ""}
        </div>
        <div class="wizard-hero-title">${manifest.title}</div>
        ${manifest.tagline
          ? html`<div class="wizard-hero-tagline">${manifest.tagline}</div>`
          : ""}
        ${manifest.tags?.length
          ? html`
              <div class="wizard-hero-tags">
                ${manifest.tags.map(
                  (t, idx) =>
                    html`<span class="wizard-tag ${idx === 0 ? "primary" : ""}">
                      ${idx === 0
                        ? html`<ha-icon icon="mdi:bookmark"></ha-icon>`
                        : ""}
                      ${t}
                    </span>`,
                )}
              </div>
            `
          : ""}
        ${manifest.description
          ? html`<div class="wizard-hero-description">
              ${manifest.description}
            </div>`
          : ""}
      </div>
    </div>
  `;
}

// ── Step 1: Overview ──────────────────────────────────────────────
// Read-only screen explaining what the recipe will do. Pulls "this
// recipe will:" bullets from the rendered package's created_counts
// (parsed server-side from the preview YAML). Required roles and
// integrations come straight from the manifest.

function _renderStep1Overview(host) {
  const { manifest } = host._recipeWizardDetail;
  const preview = host._recipeWizardPreview;
  const counts = preview?.preview?.created_counts || {};
  const bullets = Object.entries(counts)
    .filter(([, n]) => n > 0)
    .map(([k, n]) => _humaniseSection(host, k, n));

  return html`
    <div class="step-pane">
      ${bullets.length
        ? html`
            <section class="overview-card">
              <h3 class="overview-card-title">
                ${host._t("recipes_this_recipe_creates", "This recipe creates")}
              </h3>
              <ul class="overview-list">
                ${bullets.map(
                  (b) =>
                    html`<li>
                      <ha-icon icon="mdi:check-circle-outline"></ha-icon>
                      ${b[0].toUpperCase() + b.slice(1)}
                    </li>`,
                )}
              </ul>
            </section>
          `
        : ""}
      ${_renderWizardFooter(host, {
        primary: {
          label: host._t("recipes_start_setup_button", "Start setup"),
          icon: "mdi:arrow-right",
          onClick: () => host._advanceRecipeStep(),
        },
        primaryDisabled: !host._canAdvanceFromStep(1) || host._recipesBusy,
        hint: host._recipesBusy
          ? host._t("recipes_loading_recipe", "Loading recipe…")
          : "",
      })}
    </div>
  `;
}

// ── Step 2: Match devices & services ──────────────────────────────
// Table view: one row per recipe role + one row per integration.
// Clicking a row expands a detail panel below using the existing
// action-panel helpers (entity picker, integration config flow,
// pair-device card).

// ── Step 2: Settings (recipe inputs) ────────────────────────────
// Dedicated step for the manifest's declared inputs. Pulled out of
// the Match table because settings are usually pre-filled with sane
// defaults — the user just glances and clicks Continue. Keeps the
// Match table focused on "which devices in your home plug into this
// recipe" without an unrelated row about bedtime / dim duration.

// Compact step heading rendered at the top of each step body.
// Shows the step number, name, and a one-line description so the
// user knows where they are in the flow and what's expected — the
// Progress rail on the right confirms it but the main column also
// needs an anchor. ``required`` adds a "REQUIRED" eyebrow chip when
// the step needs user input; omit it for read-only / optional steps.
function _renderStepHeading(host, stepNum, label, subline, required) {
  return html`
    <header class="step-heading">
      <div class="step-heading-eyebrow">
        <span class="step-heading-num"
          >${host._t("recipes_step_label", "Step")} ${stepNum}
          ${host._t("recipes_step_of_5", "of 5")}</span
        >
        ${required === false
          ? html`<span class="step-heading-optional"
              >${host._t("recipes_optional_eyebrow", "Optional")}</span
            >`
          : required === true
            ? html`<span class="step-heading-required"
                >${host._t("recipes_required_eyebrow", "Required")}</span
              >`
            : ""}
      </div>
      <h2 class="step-heading-title">${label}</h2>
      ${subline ? html`<p class="step-heading-sub">${subline}</p>` : ""}
    </header>
  `;
}

function _renderStep2Settings(host) {
  const { manifest } = host._recipeWizardDetail;
  // Skip inputs flagged with a backend ``resolver`` — those are
  // computed from HA config / external APIs at preview time, the
  // homeowner never types them. Mirrors the same filter in the
  // ``derive_items`` payload on the backend.
  const inputs = (manifest.inputs || []).filter((i) => !i.resolver);
  const required = inputs.some((i) => i.required !== false);
  return html`
    <div class="step-pane">
      ${_renderStepHeading(
        host,
        2,
        host._t("recipes_step2_title", "Recipe settings"),
        inputs.length === 0
          ? host._t(
              "recipes_step2_sub_empty",
              "This recipe has no settings to configure — just click Continue.",
            )
          : host._t(
              "recipes_step2_sub",
              "Recipe-wide preferences. Defaults are pre-filled; adjust only if you want to change something, then Continue.",
            ),
        inputs.length === 0 ? false : required,
      )}
      ${inputs.length === 0
        ? ""
        : html`
            <section class="overview-card">
              <div class="panel-fields">
                ${inputs.map((input) => _renderInputField(host, input))}
              </div>
            </section>
          `}
      ${_renderWizardFooter(host, {
        primary: {
          label: host._t("recipes_continue_button", "Continue"),
          icon: "mdi:arrow-right",
          onClick: () => host._advanceRecipeStep(),
        },
        primaryDisabled: !host._canAdvanceFromStep(2) || host._recipesBusy,
        hint: "",
      })}
    </div>
  `;
}

// Explain WHY Continue is blocked on Step 3. The old hint always said
// "finish the rows that say Needs setup" — a dead end when no row needs
// setup (the real blocker was a backend punch the user never saw). We
// now surface: the needs-setup prod when a row genuinely needs a pick,
// otherwise the first actionable punch message, otherwise a clear
// "couldn't prepare" fallback so the user isn't stuck with no reason.
function _step3BlockReason(host, preview, needsAction) {
  if (needsAction) {
    return host._t(
      "recipes_step3_hint_finish",
      "Finish the rows that say “Needs setup” to continue.",
    );
  }
  // Pending device pairs (binding_pending) are handled in Step 4, not
  // blockers here — skip them when picking a reason to show.
  const punch = (preview?.punch_list || []).find(
    (p) => p.code !== "binding_pending" && p.message,
  );
  if (punch?.message) return punch.message;
  return host._t(
    "recipes_step3_hint_blocked",
    "This recipe can't be prepared yet — check the recipe for errors, then try again.",
  );
}

function _renderStep3Match(host) {
  const preview = host._recipeWizardPreview;
  const items = preview?.items || [];
  // Settings (inputs) live on Step 2 — keep Match focused on
  // device/integration mapping only.
  const matchItems = items.filter(
    (it) =>
      it.stage === "configure" &&
      (it.kind === "role_selection" ||
        it.kind === "integration" ||
        it.kind === "pin"),
  );
  const active = _activeItem(host, matchItems);
  const canAdvance = host._canAdvanceFromStep(3);
  // Step 3 is only "Required" when something here actually blocks — a
  // row needing a pick, or a failed/unmet item. An all-optional recipe
  // (every row "Optional") is not required; showing "REQUIRED" then is
  // the confusing mismatch the user hit.
  const needsAction = matchItems.some(
    (it) => it.status === "needs_input" || it.status === "failed",
  );

  return html`
    <div class="step-pane">
      ${_renderStepHeading(
        host,
        3,
        host._t("recipes_step3_title", "Match devices"),
        host._t(
          "recipes_step3_sub",
          "Pair each item below with an entity from your home. Click any row to set it up.",
        ),
        needsAction,
      )}

      <div class="match-table">
        <div class="match-row match-head">
          <div>${host._t("recipes_match_col_item", "Item")}</div>
          <div>${host._t("recipes_match_col_status", "Status")}</div>
          <div>${host._t("recipes_match_col_selected", "Selected")}</div>
        </div>
        ${matchItems.length === 0
          ? html`<div class="match-empty">
              ${host._recipesBusy
                ? host._t("recipes_match_scanning", "Scanning your home…")
                : host._t(
                    "recipes_match_nothing",
                    "Nothing to match — this recipe runs without device setup.",
                  )}
            </div>`
          : matchItems.map((it) =>
              _renderMatchRow(host, it, it.id === active?.id),
            )}
      </div>

      ${active
        ? html`<div class="match-detail">
            ${_renderActionPanel(host, active)}
          </div>`
        : ""}
      ${_renderWizardFooter(host, {
        primary: {
          label: host._t("recipes_continue_button", "Continue"),
          icon: "mdi:arrow-right",
          onClick: () => host._advanceRecipeStep(),
        },
        primaryDisabled: !canAdvance || host._recipesBusy,
        hint: canAdvance
          ? host._t("recipes_step3_hint_ready", "Looks good — ready to set up.")
          : _step3BlockReason(host, preview, needsAction),
      })}
    </div>
  `;
}

// Per-row icon + category variant for the Match table. Matches the
// "What you need" rail palette so a homeowner glancing at either
// surface sees the same category-to-colour mapping.
function _matchRowVisual(item) {
  if (item.kind === "integration") {
    return { icon: "mdi:puzzle-outline", variant: "integration" };
  }
  if (item.kind === "inputs") {
    return { icon: "mdi:cog-outline", variant: "input" };
  }
  if (item.kind === "pin") {
    // Pin items target a specific device — emerald like the rail.
    return { icon: "mdi:link-variant", variant: "pin" };
  }
  if (item.kind === "role_selection") {
    const role = item.payload?.role;
    return {
      icon: role ? _roleIconForKind(role) : "mdi:devices",
      variant: "role",
    };
  }
  return { icon: "mdi:circle-outline", variant: "role" };
}

function _renderMatchRow(host, item, active) {
  // Map the pipeline-item status to user-facing labels for the table.
  const statusCopy = {
    ok: host._t("recipes_match_status_ready", "Ready"),
    needs_input: host._t("recipes_match_status_needs_setup", "Needs setup"),
    failed: host._t("recipes_match_status_error", "Error"),
    skipped: host._t("recipes_match_status_optional", "Optional"),
    pending: host._t("recipes_match_status_waiting", "Waiting"),
    running: host._t("recipes_match_status_working", "Working"),
  };
  // A client-side integration flow can fail (e.g. auto-setup rejected:
  // "NWS doesn't cover this location") without the server preview
  // flipping ``item.status`` away from ``needs_input``. Promote that
  // flow error to a row-level ``failed`` so the table shows red "Error"
  // instead of amber "Needs setup", and surface the reason inline — the
  // detached action panel below shouldn't be the only place it appears.
  let displayStatus = item.status;
  let flowError = null;
  if (item.kind === "integration") {
    const flow = (host._recipeFlows || {})[item.payload?.domain];
    if (flow?.state === "error") {
      displayStatus = "failed";
      flowError = flow.error || null;
    }
  }
  const selected = _matchRowSelected(host, item);
  const visual = _matchRowVisual(item);
  return html`
    <button
      type="button"
      class="match-row match-data ${active ? "is-active" : ""}"
      @click=${() => {
        // Commit, don't toggle. With a toggle, clicking the row that's
        // already the implicit "default active" item cleared the id
        // back to null — and then any chip pick caused _activeItem's
        // fallback to jump to the last optional row once the chosen
        // role flipped to ok. Setting an explicit id keeps the user
        // anchored on their chosen role for as long as they're picking.
        host._recipeActiveItemId = item.id;
      }}
    >
      <div class="match-cell-item">
        <div
          class="match-icon-wrap match-icon-${visual.variant} match-icon-status-${displayStatus}"
        >
          <ha-icon icon=${visual.icon}></ha-icon>
        </div>
        <div class="match-cell-text">
          <div class="match-title">${item.title}</div>
          ${flowError
            ? html`<div class="match-sub is-error" title=${flowError}>
                ${flowError}
              </div>`
            : item.detail
              ? html`<div class="match-sub">${item.detail}</div>`
              : ""}
        </div>
      </div>
      <div class="match-status pi-${displayStatus}">
        ${statusCopy[displayStatus] || displayStatus}
      </div>
      <div class="match-selected">${selected}</div>
    </button>
  `;
}

function _matchRowSelected(host, item) {
  if (item.kind === "role_selection") {
    const bound = item.payload?.bound || [];
    const pinned = item.payload?.pinned || [];
    const total = bound.length + pinned.length;
    if (total === 0)
      return html`<span class="panel-muted"
        >${host._t("recipes_selected_none", "None")}</span
      >`;
    if (total === 1)
      return _entityFriendlyName(host.hass, bound[0] || pinned[0]);
    return `${total} ${host._t("recipes_selected_entities_suffix", "entities")}`;
  }
  if (item.kind === "pin") {
    const id = item.payload?.identity || {};
    const label = [id.manufacturer, id.model].filter(Boolean).join(" ");
    return (
      label ||
      html`<span class="panel-muted"
        >${host._t("recipes_selected_awaiting_pair", "Awaiting pair")}</span
      >`
    );
  }
  if (item.kind === "integration") {
    // No entity to "select" for an integration — STATUS already says
    // Ready / Needs setup, and the entry detail sits under the name.
    // A value here ("Configured" / "Not set up") just duplicates STATUS.
    return html`<span class="panel-muted">—</span>`;
  }
  if (item.kind === "inputs") {
    const n = item.payload?.inputs?.length || 0;
    return `${n} ${n === 1 ? host._t("recipes_selected_setting_singular", "setting") : host._t("recipes_selected_setting_plural", "settings")}`;
  }
  return "";
}

// ── Step 3: Resolve / live orchestration ──────────────────────────
// Three buckets reflecting what's happening right now. The install
// stream populates these in real time. Auto-advances to step 4 when
// the stream's final ``result`` event reports success.

function _renderStep4Resolve(host) {
  const items = host._recipeWizardPreview?.items || [];
  const result = host._recipeWizardResult;
  const installOk = !!result && result.ok === true;
  const installFailed = !!result && result.ok === false;
  // The install can finish before every live ``apply`` event lands
  // (or a re-render resets the preview baseline), leaving rows stuck
  // at ``pending``. The authoritative signal is the result payload —
  // when it reports ok, the apply steps all ran, so present them as
  // done regardless of the last status we saw streamed.
  const apply = items
    .filter((it) => it.stage === "apply")
    .map((it) =>
      installOk && it.status !== "failed" ? { ...it, status: "ok" } : it,
    );
  const interrupts = items.filter(
    (it) => it.stage === "configure" && it.status === "needs_input",
  );
  const completed = apply.filter((it) => it.status === "ok");
  const running = apply.filter((it) => it.status === "running");
  const upcoming = apply.filter((it) => it.status === "pending");
  const failed = apply.filter((it) => it.status === "failed");

  const allDone =
    installOk || (apply.length > 0 && completed.length === apply.length);
  const errorPunch = installFailed ? result.punch_list || [] : [];

  return html`
    <div class="step-pane">
      ${_renderStepHeading(
        host,
        4,
        host._t("recipes_step4_title", "Setting up"),
        host._recipesBusy
          ? host._t(
              "recipes_step4_sub_busy",
              "Selora is installing your recipe. Sit tight — this only takes a few seconds.",
            )
          : installFailed
            ? `${host._t("recipes_step4_sub_halted_prefix", "Install halted at the")} ${result.stage_reached || host._t("recipes_stage_unknown", "unknown")} ${host._t("recipes_step4_sub_halted_suffix", "stage. Go back to fix the issues below.")}`
            : allDone
              ? host._t(
                  "recipes_step4_sub_done",
                  "All done. Click Continue to review what was installed.",
                )
              : host._t("recipes_step4_sub_starting", "Starting setup…"),
        host._recipesBusy || !allDone,
      )}
      ${installFailed
        ? html`
            <section class="bucket bucket-failed">
              <h3 class="bucket-title">
                <ha-icon icon="mdi:close-circle-outline"></ha-icon>
                ${host._t("recipes_bucket_install_failed", "Install failed")}
              </h3>
              ${errorPunch.length
                ? html`<ul class="install-fail-list">
                    ${errorPunch.map(
                      (p) =>
                        html`<li>
                          <span class="install-fail-stage">${p.stage}</span>
                          ${p.message}
                        </li>`,
                    )}
                  </ul>`
                : html`<p class="panel-prose panel-muted">
                    ${host._t(
                      "recipes_bucket_no_details",
                      "No details available. Check the Home Assistant log for the underlying error.",
                    )}
                  </p>`}
            </section>
          `
        : ""}
      ${interrupts.length
        ? html`
            <section class="bucket bucket-waiting">
              <h3 class="bucket-title">
                <ha-icon icon="mdi:hand-back-right-outline"></ha-icon>
                ${host._t("recipes_bucket_waiting_for_you", "Waiting for you")}
              </h3>
              ${interrupts.map((it) => _renderBucketItem(host, it, true))}
            </section>
          `
        : ""}
      ${failed.length
        ? html`
            <section class="bucket bucket-failed">
              <h3 class="bucket-title">
                <ha-icon icon="mdi:close-circle-outline"></ha-icon>
                ${host._t("recipes_bucket_failed", "Failed")}
              </h3>
              ${failed.map((it) => _renderBucketItem(host, it, false))}
            </section>
          `
        : ""}
      ${running.length
        ? html`
            <section class="bucket bucket-running">
              <h3 class="bucket-title">
                <ha-icon icon="mdi:cog-sync-outline"></ha-icon>
                ${host._t("recipes_bucket_in_progress", "In progress")}
              </h3>
              ${running.map((it) => _renderBucketItem(host, it, false))}
            </section>
          `
        : ""}
      ${upcoming.length
        ? html`
            <section class="bucket bucket-upcoming">
              <h3 class="bucket-title">
                <ha-icon icon="mdi:tray-arrow-down"></ha-icon>
                ${host._t("recipes_bucket_up_next", "Up next")}
              </h3>
              ${upcoming.map((it) => _renderBucketItem(host, it, false))}
            </section>
          `
        : ""}
      ${completed.length
        ? html`
            <section class="bucket bucket-done">
              <h3 class="bucket-title">
                <ha-icon icon="mdi:check-circle-outline"></ha-icon>
                ${host._t("recipes_bucket_completed", "Completed")}
              </h3>
              ${completed.map((it) => _renderBucketItem(host, it, false))}
            </section>
          `
        : ""}
      ${_renderWizardFooter(host, {
        primary: {
          label: allDone
            ? host._t("recipes_continue_button", "Continue")
            : host._recipesBusy
              ? host._t("recipes_working", "Working…")
              : installFailed
                ? host._t("recipes_install_failed_button", "Install failed")
                : host._t("recipes_setting_up_button", "Setting up…"),
          icon: allDone ? "mdi:arrow-right" : null,
          onClick: () => host._advanceRecipeStep(),
        },
        primaryDisabled: host._recipesBusy || !host._canAdvanceFromStep(4),
        hideBack: host._recipesBusy,
        hint: installFailed
          ? host._t(
              "recipes_step4_hint_failed",
              "Hit Back to revise your selections, then try again.",
            )
          : failed.length
            ? host._t(
                "recipes_step4_hint_something_failed",
                "Something failed — go back and try again.",
              )
            : "",
      })}
    </div>
  `;
}

function _renderBucketItem(host, item, interactive) {
  // Tile-style icon (32px backplate + glyph) matching the Match
  // table so the install-progress view reads as the same surface.
  // Status drives the tile color; the glyph stays per-status.
  const icon = _STATUS_ICON[item.status] || "mdi:circle-outline";
  return html`
    <div class="bucket-item">
      <div class="bucket-item-icon-wrap bucket-tile-${item.status}">
        <ha-icon icon=${icon}></ha-icon>
      </div>
      <div class="bucket-item-body">
        <div class="bucket-item-title">${item.title}</div>
        ${item.detail
          ? html`<div class="bucket-item-detail">${item.detail}</div>`
          : ""}
      </div>
      ${interactive
        ? html`<div class="bucket-item-action">
            ${_renderActionPanel(host, item)}
          </div>`
        : ""}
    </div>
  `;
}

// ── Step 4: Review & Activate ─────────────────────────────────────
// Post-install screen. The install already ran in step 3, so this
// step is a confirmation: show what was created, devices linked,
// and safety checks. [Activate Recipe] closes to the success view.

// Step 5 dashboard-card offer. The install no longer auto-inserts — the
// card is placed here, after the recipe is live and its helper exists.
// Three states: already placed (success), no writable dashboards (manual
// note), or a picker + "Add card" action when writable dashboards exist.
// All placement is deterministic (HA Lovelace API) — there is no LLM in
// this path.
function _renderDashboardOutcome(host) {
  const manifest = host._recipeWizardDetail?.manifest;
  if (!manifest?.dashboard) return "";
  const card = host._recipeWizardResult?.record?.dashboard_card || {};
  const placed = card.ok === true;
  const dashboards = host._recipeDashboards || [];
  const title = host._t("recipes_step5_dashboard_title", "Dashboard card");

  // Already placed → success.
  if (placed) {
    return html`
      <section class="overview-card">
        <h3 class="overview-card-title">${title}</h3>
        <p class="step-prose">
          <ha-icon icon="mdi:check-circle" class="safety-ok"></ha-icon>
          ${host._t(
            "recipes_step5_dashboard_added",
            "A card was added to your dashboard.",
          )}
        </p>
      </section>
    `;
  }

  // No writable (storage-mode) dashboards → can't place a card
  // automatically; tell the user to add it from their dashboard's edit
  // mode.
  if (!dashboards.length) {
    return html`
      <section class="overview-card">
        <h3 class="overview-card-title">${title}</h3>
        <p class="step-prose panel-muted">
          ${host._t(
            "recipes_step5_dashboard_none",
            "No editable dashboards found. Add a card yourself from your dashboard's edit mode.",
          )}
        </p>
      </section>
    `;
  }

  // Writable dashboards exist → picker + Add action. Default the selection
  // to the first writable dashboard so the visible choice matches what
  // ``_insertRecipeDashboardCard`` writes when the user hasn't changed it.
  const current = host._recipeDashboardTarget;
  const firstValue =
    dashboards[0].url_path == null ? "" : dashboards[0].url_path;
  const selected =
    current === undefined ? firstValue : current == null ? "" : current;
  return html`
    <section class="overview-card">
      <h3 class="overview-card-title">${title}</h3>
      <p class="step-prose panel-muted">
        ${host._t(
          "recipes_step5_dashboard_offer",
          "Drop a card for this recipe onto a dashboard so you can tap it.",
        )}
      </p>
      <select
        class="role-filter-input"
        .value=${selected}
        @change=${(e) => host._setRecipeDashboardTarget(e.target.value)}
        ?disabled=${host._recipesBusy}
      >
        ${dashboards.map(
          (d) =>
            html`<option value=${d.url_path == null ? "" : d.url_path}>
              ${d.title}
            </option>`,
        )}
      </select>
      <div class="step5-dashboard-actions">
        <button
          class="panel-btn primary"
          type="button"
          @click=${() => host._insertRecipeDashboardCard()}
          ?disabled=${host._recipesBusy}
        >
          ${host._t("recipes_step5_dashboard_add", "Add card")}
          <ha-icon icon="mdi:view-dashboard-outline"></ha-icon>
        </button>
      </div>
    </section>
  `;
}

function _renderStep5Activate(host) {
  const result = host._recipeWizardResult;
  const counts = result?.preview?.created_counts || {};
  const bindings = result?.bindings || {};
  const allBoundIds = Object.values(bindings).flat();
  const uniqueBound = [...new Set(allBoundIds)];

  const safety = [
    {
      ok: result?.ok === true,
      label:
        result?.ok === true
          ? host._t(
              "recipes_safety_installed_ok",
              "Recipe installed successfully",
            )
          : host._t("recipes_safety_install_incomplete", "Install incomplete"),
    },
    {
      ok: (result?.punch_list?.length || 0) === 0,
      label:
        (result?.punch_list?.length || 0) === 0
          ? host._t("recipes_safety_no_issues", "No outstanding issues")
          : `${result.punch_list.length} ${host._t("recipes_safety_issues_to_address", "issue(s) to address")}`,
    },
    {
      ok: Object.keys(counts).length > 0,
      label:
        Object.keys(counts).length > 0
          ? host._t(
              "recipes_safety_automations_generated",
              "Automations generated",
            )
          : host._t("recipes_safety_no_artifacts", "No artifacts created"),
    },
  ];

  const summaryBullets = Object.entries(counts)
    .filter(([, n]) => n > 0)
    .map(([k, n]) => _humaniseSection(host, k, n));

  return html`
    <div class="step-pane">
      ${_renderStepHeading(
        host,
        5,
        host._t("recipes_step5_title", "Review & finish"),
        host._t(
          "recipes_step5_sub",
          "Your recipe is installed and Home Assistant has been reloaded. Review what was created below, then click Finish.",
        ),
        false,
      )}

      <div class="activate-grid">
        <section class="overview-card">
          <h3 class="overview-card-title">
            ${host._t("recipes_step5_created_title", "This recipe created")}
          </h3>
          ${summaryBullets.length
            ? html`<ul class="overview-list">
                ${summaryBullets.map(
                  (b) =>
                    html`<li>
                      <ha-icon icon="mdi:plus-circle-outline"></ha-icon>
                      ${b[0].toUpperCase() + b.slice(1)}
                    </li>`,
                )}
              </ul>`
            : html`<p class="step-prose panel-muted">
                ${host._t(
                  "recipes_step5_no_entries",
                  "No entries were generated.",
                )}
              </p>`}
        </section>

        <section class="overview-card">
          <h3 class="overview-card-title">
            ${host._t("recipes_step5_devices_linked", "Devices linked")}
          </h3>
          ${uniqueBound.length
            ? html`<ul class="overview-list compact">
                ${uniqueBound.slice(0, 8).map(
                  (id) =>
                    html`<li>
                      <ha-icon icon=${_entityIcon(host.hass, id)}></ha-icon>
                      ${_entityFriendlyName(host.hass, id)}
                    </li>`,
                )}
                ${uniqueBound.length > 8
                  ? html`<li class="panel-muted">
                      + ${uniqueBound.length - 8}
                      ${host._t("recipes_step5_more_suffix", "more")}
                    </li>`
                  : ""}
              </ul>`
            : html`<p class="step-prose panel-muted">
                ${host._t(
                  "recipes_step5_no_devices",
                  "No devices are tied to this recipe.",
                )}
              </p>`}
        </section>

        <section class="overview-card safety-card">
          <h3 class="overview-card-title">
            ${host._t("recipes_step5_safety_checks", "Safety checks")}
          </h3>
          <ul class="overview-list compact">
            ${safety.map(
              (s) =>
                html`<li>
                  <ha-icon
                    icon=${s.ok ? "mdi:check-circle" : "mdi:alert-circle"}
                    class=${s.ok ? "safety-ok" : "safety-fail"}
                  ></ha-icon>
                  ${s.label}
                </li>`,
            )}
          </ul>
        </section>
        ${_renderDashboardOutcome(host)}
      </div>

      ${_renderWizardFooter(host, {
        primary: {
          label: host._t("recipes_finish_button", "Finish"),
          icon: "mdi:check",
          onClick: () => host._closeRecipeWizard(),
        },
        primaryDisabled: false,
        hideBack: true,
        hideSecondary: true,
        hint: host._t(
          "recipes_step5_hint",
          "Home Assistant has been reloaded — your recipe is live.",
        ),
      })}
    </div>
  `;
}

// ── Wizard dispatcher ─────────────────────────────────────────────

function _renderWizardView(host) {
  const detail = host._recipeWizardDetail;
  if (!detail) {
    return html`<div class="recipes-empty">
      ${host._t("recipes_loading", "Loading…")}
    </div>`;
  }
  const step = host._recipeWizardStep || 1;
  const body =
    step === 1
      ? _renderStep1Overview(host)
      : step === 2
        ? _renderStep2Settings(host)
        : step === 3
          ? _renderStep3Match(host)
          : step === 4
            ? _renderStep4Resolve(host)
            : _renderStep5Activate(host);
  // Step 1 is the recipe's "detail page" — read-only, mirrors the
  // selorahomes.com layout. We swap the Progress rail for a "What you
  // need" rail there so the homeowner can scan requirements without
  // wizard chrome competing for attention. Steps 2-5 are the actual
  // wizard flow and get the Progress rail.
  const rail =
    step === 1
      ? _renderWhatYouNeedRail(host, detail.manifest)
      : _renderWizardStepper(host);
  return html`
    <div class="wizard-root ${step === 1 ? "wizard-root-overview" : ""}">
      <div class="wizard-main">
        ${_renderWizardHero(host, detail.manifest, { compact: step !== 1 })}
        ${body}
      </div>
      ${rail}
    </div>
  `;
}

// ── Result view ────────────────────────────────────────────────────

function _renderResultView(host) {
  const result = host._recipeWizardResult;
  if (!result) return "";
  return html`
    <div class="recipes-root">
      <button
        class="wizard-back"
        @click=${() => {
          host._recipeWizardResult = null;
          host._recipesView = "list";
          host._recipeWizardSlug = null;
          // Drop the ``/recipes/<slug>`` deep-link so a later
          // location-changed can't reopen the wizard for this recipe.
          host._setRecipeWizardUrl?.(null);
          host._loadRecipesList();
        }}
      >
        <ha-icon icon="mdi:arrow-left"></ha-icon> ${host._t(
          "recipes_back_to_recipes",
          "Back to recipes",
        )}
      </button>
      ${result.ok
        ? html`
            <div class="install-success">
              <div
                style="font-size:var(--selora-fs-xl);font-weight:700;display:flex;align-items:center;gap:8px;"
              >
                <ha-icon icon="mdi:check-circle"></ha-icon>
                ${host._t(
                  "recipes_result_install_complete",
                  "Installation complete",
                )}
              </div>
              ${result.record
                ? html`
                    <div style="font-size:var(--selora-fs-md);line-height:1.6;">
                      ${result.record.title} v${result.record.version}
                      ${host._t(
                        "recipes_result_installed_reloaded",
                        "was installed and Home Assistant has been reloaded. Package file:",
                      )}
                      <code>${result.record.package_path}</code>
                    </div>
                  `
                : ""}
            </div>
            ${result.preview?.yaml
              ? html`
                  <details class="wizard-section package-disclosure">
                    <summary>
                      <ha-icon
                        class="chevron"
                        icon="mdi:chevron-right"
                      ></ha-icon>
                      <ha-icon icon="mdi:file-document-outline"></ha-icon>
                      ${host._t(
                        "recipes_result_view_yaml",
                        "View generated package YAML",
                      )}
                      <span class="filler"></span>
                      <span class="package-disclosure-hint"
                        >${host._t("recipes_result_advanced", "advanced")}</span
                      >
                    </summary>
                    ${unsafeHTML(
                      '<div class="yaml-preview">' +
                        _highlightYaml(result.preview.yaml) +
                        "</div>",
                    )}
                  </details>
                `
              : ""}
          `
        : html`
            <div class="wizard-section">
              <h3 style="color:var(--error-color,#c62828);">
                ${host._t(
                  "recipes_result_halted_at_stage",
                  "Installation halted at stage:",
                )}
                ${result.stage_reached}
              </h3>
              <p
                style="font-size:var(--selora-fs-md);color:var(--secondary-text-color);"
              >
                ${host._t(
                  "recipes_result_fix_retry",
                  "Fix the items below, then re-open the recipe to retry.",
                )}
              </p>
            </div>
            ${_renderPunchList(host, result.punch_list)}
          `}
    </div>
  `;
}

// ── Uninstall confirm modal ────────────────────────────────────────

function _renderUninstallModal(host) {
  const slug = host._recipeUninstallPending;
  if (!slug) return "";
  const record = (host._recipesList?.installed || []).find(
    (r) => r.slug === slug,
  );
  const title = record?.title || slug;
  // Integrations this recipe installed (auto_setup tracked them on
  // create_entry). Each row in the modal becomes an opt-in checkbox.
  const installedIntegrations = record?.integrations_installed || {};
  const integrationDomains = Object.keys(installedIntegrations);
  const selectedEntries = host._recipeUninstallEntries || {};

  // Keydown handler — Enter confirms, Escape cancels. The destructive
  // button gets ``autofocus`` so the browser focuses it on mount; with
  // the listener on the modal root, both buttons forward keys here.
  const onKey = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      host._confirmRecipeUninstall();
    } else if (e.key === "Escape") {
      e.preventDefault();
      host._cancelRecipeUninstall();
    }
  };

  return html`
    <div
      class="modal-overlay"
      @click=${(e) => {
        if (e.target === e.currentTarget) host._cancelRecipeUninstall();
      }}
      @keydown=${onKey}
    >
      <div
        class="modal-content uninstall-modal"
        @click=${(e) => e.stopPropagation()}
      >
        <h3 class="modal-title">
          <ha-icon
            icon="mdi:delete-alert-outline"
            class="modal-title-icon"
          ></ha-icon>
          ${host._t("recipes_uninstall_confirm_prefix", "Uninstall")}
          &ldquo;${title}&rdquo;?
        </h3>
        <p class="modal-body">
          ${host._t(
            "recipes_uninstall_body",
            "The package file will be deleted and Home Assistant will reload. The automations this recipe created will be removed.",
          )}
        </p>
        ${integrationDomains.length
          ? html`
              <div class="uninstall-integrations">
                <div class="uninstall-integrations-title">
                  ${host._t(
                    "recipes_uninstall_integrations_title",
                    "Integrations this recipe installed",
                  )}
                </div>
                <p class="uninstall-integrations-sub">
                  ${host._t(
                    "recipes_uninstall_integrations_sub",
                    "Tick any that should be removed along with the recipe. Anything you leave unchecked stays in Home Assistant.",
                  )}
                </p>
                ${integrationDomains.map((domain) => {
                  const entryId = installedIntegrations[domain];
                  const checked = !!selectedEntries[entryId];
                  const others = host._otherUsersOfDomain(domain, slug);
                  // HA brand icon CDN — same pattern HA's own
                  // Integrations page uses. ``onerror`` hides the
                  // img tag silently when no brand exists.
                  const iconUrl = `https://brands.home-assistant.io/_/${domain}/icon@2x.png`;
                  return html`
                    <label class="uninstall-integration-row">
                      <input
                        type="checkbox"
                        .checked=${checked}
                        @change=${() => host._toggleUninstallEntry(entryId)}
                      />
                      <img
                        class="uninstall-integration-brand"
                        src=${iconUrl}
                        alt=""
                        loading="lazy"
                        @error=${(e) => {
                          e.target.style.display = "none";
                        }}
                      />
                      <div class="uninstall-integration-text">
                        <div class="uninstall-integration-name">${domain}</div>
                        ${others.length
                          ? html`<div
                              class="uninstall-integration-warn"
                              title=${host._t(
                                "recipes_uninstall_warn_title",
                                "Removing this integration will break those recipes.",
                              )}
                            >
                              <ha-icon icon="mdi:alert-outline"></ha-icon>
                              ${host._t(
                                "recipes_uninstall_still_used_by",
                                "Still used by",
                              )}
                              ${others.join(", ")}
                            </div>`
                          : ""}
                      </div>
                    </label>
                  `;
                })}
              </div>
            `
          : ""}
        <div class="modal-actions">
          <button
            class="modal-btn modal-cancel"
            @click=${() => host._cancelRecipeUninstall()}
          >
            ${host._t("recipes_footer_cancel", "Cancel")}
          </button>
          <button
            class="modal-btn modal-destructive"
            autofocus
            @click=${() => host._confirmRecipeUninstall()}
          >
            <ha-icon icon="mdi:delete-outline" class="modal-btn-icon"></ha-icon>
            ${host._t("recipes_card_uninstall_button", "Uninstall")}
          </button>
        </div>
      </div>
    </div>
  `;
}

// ── v3 prototype: Manage Devices modal ───────────────────────────
// Pops over the recipe list when the user clicks "Manage devices" on
// an installed v3 recipe. Lets them swap which entities back each
// role; on save, calls the rebind WS which rewrites only the group
// block of the installed package — no re-render, no wizard re-run.

function _renderManageDevicesModal(host) {
  const slug = host._recipeManageSlug;
  if (!slug) return "";
  const detail = host._recipeManageDetail;
  return html`
    <div
      class="modal-overlay"
      @click=${(e) => {
        if (e.target === e.currentTarget) host._closeManageDevices();
      }}
    >
      <div
        class="modal-content manage-modal"
        @click=${(e) => e.stopPropagation()}
      >
        <h3 class="modal-title">
          <ha-icon icon="mdi:swap-horizontal"></ha-icon>
          ${host._t(
            "recipes_card_manage_devices_button",
            "Manage devices",
          )}${detail ? ` — ${detail.manifest.title}` : ""}
        </h3>
        ${host._recipeManageError
          ? html`<div class="panel-error">${host._recipeManageError}</div>`
          : ""}
        ${!detail
          ? html`<p class="panel-prose panel-muted">
              ${host._recipeManageBusy
                ? host._t("recipes_loading", "Loading…")
                : host._t("recipes_manage_no_detail", "No detail available.")}
            </p>`
          : html`
              <p class="panel-prose">
                ${host._t(
                  "recipes_manage_intro",
                  "Update which entities back each role. Saves immediately — automations are not re-rendered, only the group memberships change.",
                )}
              </p>
              ${detail.manifest.roles.map((role) =>
                _renderManageRoleRow(host, role),
              )}
            `}
        <div class="modal-actions">
          <button
            class="modal-btn modal-cancel"
            @click=${() => host._closeManageDevices()}
            ?disabled=${host._recipeManageBusy}
          >
            ${host._t("recipes_footer_cancel", "Cancel")}
          </button>
          <button
            class="modal-btn modal-create"
            @click=${() => host._saveManageDevices()}
            ?disabled=${host._recipeManageBusy || !detail}
          >
            ${host._recipeManageBusy
              ? host._t("recipes_manage_saving", "Saving…")
              : host._t("recipes_manage_save", "Save")}
          </button>
        </div>
      </div>
    </div>
  `;
}

function _renderManageRoleRow(host, role) {
  const selected = new Set(host._recipeManageSelections[role.id] || []);
  // Enumerate every entity of the role's kind that's currently in HA.
  const states = host.hass?.states || {};
  const candidates = Object.keys(states).filter((id) => {
    if (!id.startsWith(`${role.kind}.`)) return false;
    if (role.device_class) {
      const dc = states[id]?.attributes?.device_class;
      if (dc !== role.device_class) return false;
    }
    return true;
  });
  return html`
    <div class="manage-role">
      <div class="manage-role-head">
        <div class="manage-role-title">${role.title || role.id}</div>
        ${role.description
          ? html`<div class="manage-role-desc">${role.description}</div>`
          : ""}
      </div>
      <div class="panel-chips">
        ${candidates.length === 0
          ? html`<p class="panel-prose panel-muted">
              ${host._t("recipes_manage_no_entities_prefix", "No")} ${role.kind}
              ${host._t(
                "recipes_manage_no_entities_suffix",
                "entities found in this home.",
              )}
            </p>`
          : candidates.map((id) => {
              const on = selected.has(id);
              return html`
                <button
                  type="button"
                  class="role-entity-chip role-entity-toggle ${on
                    ? "is-on"
                    : ""}"
                  title=${id}
                  @click=${() => host._toggleManageEntity(role.id, id)}
                  ?disabled=${host._recipeManageBusy}
                >
                  <span class="chip-icon-tile">
                    <ha-icon icon=${_entityIcon(host.hass, id)}></ha-icon>
                  </span>
                  <span class="chip-text">
                    <span class="chip-name">
                      ${_entityFriendlyName(host.hass, id)}
                    </span>
                    <span class="chip-id">${id}</span>
                  </span>
                </button>
              `;
            })}
      </div>
    </div>
  `;
}

// ── Top-level dispatch ─────────────────────────────────────────────

export function renderRecipesV2(host) {
  let body;
  if (host._recipesView === "wizard") body = _renderWizardView(host);
  else if (host._recipesView === "result") body = _renderResultView(host);
  else body = _renderListView(host);
  return html`
    <div class="scroll-view">${_STYLE} ${body}</div>
    ${_renderManageDevicesModal(host)} ${_renderUninstallModal(host)}
  `;
}
