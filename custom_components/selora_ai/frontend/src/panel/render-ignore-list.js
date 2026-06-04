// Settings → "Ignore in suggestions" section.
//
// Single source of truth: the "Selora exclude" HA label. Adding from the
// picker applies the label to the chosen entity / device / area via a
// websocket command; removing a chip removes the label. Items tagged
// directly in HA (Settings → Labels, or entity / device / area pages)
// show up here automatically.

import { html } from "lit";

import {
  AUTOCOMPLETE_MAX_RESULTS,
  rankSuggestions,
} from "./chat-autocomplete.js";

const MIN_QUERY = 1;

// Suggestion-index `kind`s. The ignore picker uses its OWN entity index
// rather than the chat composer's so it can reach sensor / binary_sensor /
// person / device_tracker / etc. — domains the backend mines for patterns
// but the chat composer deliberately omits.
const KIND_ENTITY = "entity";
const KIND_HA_DEVICE = "ha_device";
const KIND_AREA = "area";

// Mirrors COLLECTOR_DOMAINS on the backend (entity_capabilities.py) plus a
// few user-actuator domains that pattern detection doesn't collect but a
// user may still want to silence in suggestions. If COLLECTOR_DOMAINS gains
// a domain server-side, add it here too.
const IGNORE_ENTITY_DOMAINS = new Set([
  "light",
  "switch",
  "lock",
  "cover",
  "fan",
  "media_player",
  "climate",
  "vacuum",
  "camera",
  "humidifier",
  "water_heater",
  "input_boolean",
  "input_select",
  "input_number",
  "input_button",
  "remote",
  "lawn_mower",
  "sensor",
  "binary_sensor",
  "device_tracker",
  "person",
  "scene",
  "automation",
  "script",
]);

const IGNORE_DOMAIN_ICONS = {
  light: "mdi:lightbulb",
  switch: "mdi:toggle-switch",
  lock: "mdi:lock",
  cover: "mdi:window-shutter",
  fan: "mdi:fan",
  media_player: "mdi:speaker",
  climate: "mdi:thermostat",
  vacuum: "mdi:robot-vacuum",
  camera: "mdi:cctv",
  humidifier: "mdi:air-humidifier",
  water_heater: "mdi:water-boiler",
  remote: "mdi:remote",
  lawn_mower: "mdi:mower",
  input_boolean: "mdi:toggle-switch-outline",
  input_select: "mdi:form-dropdown",
  input_number: "mdi:numeric",
  input_button: "mdi:gesture-tap-button",
  sensor: "mdi:gauge",
  binary_sensor: "mdi:checkbox-marked-circle-outline",
  device_tracker: "mdi:crosshairs-gps",
  person: "mdi:account",
  scene: "mdi:palette",
  automation: "mdi:robot",
  script: "mdi:script-text",
};

function _config(host) {
  return host._config || {};
}

function _labelTagged(host) {
  return _config(host).label_tagged || { entities: [], devices: [], areas: [] };
}

// Refresh just the ignore-list slice of config after a label change. We
// only need `label_tagged` from the response, so a full _loadConfig is
// overkill — but using the existing helper is simpler and keeps the
// panel state consistent if the user toggles other settings in flight.
async function _refresh(host) {
  await host._loadConfig?.();
}

async function _applyLabel(host, payload) {
  try {
    await host.hass.callWS({
      type: "selora_ai/apply_exclude_label",
      ...payload,
    });
    await _refresh(host);
  } catch (err) {
    host._showToast?.(`Failed to apply label: ${err.message || err}`, "error");
  }
}

async function _removeLabel(host, payload) {
  try {
    await host.hass.callWS({
      type: "selora_ai/remove_exclude_label",
      ...payload,
    });
    await _refresh(host);
  } catch (err) {
    host._showToast?.(`Failed to remove label: ${err.message || err}`, "error");
  }
}

// Look up a friendly label for an already-tagged entity so chips don't
// render raw IDs. Falls back to the ID if the entity isn't in the registry
// (deleted device, etc.) so the user can still remove it.
function _entityLabel(host, entityId) {
  const state = host.hass?.states?.[entityId];
  const friendly = state?.attributes?.friendly_name;
  return friendly || entityId;
}

function _entityIcon(entityId) {
  const domain = (entityId || "").split(".")[0];
  return IGNORE_DOMAIN_ICONS[domain] || "mdi:devices";
}

function _cachedRegistries(host) {
  if (
    host._autocompleteRegCache === undefined &&
    typeof host._ensureFullRegistries === "function"
  ) {
    host._autocompleteRegCache = "pending";
    host._ensureFullRegistries().then((reg) => {
      host._autocompleteRegCache = reg || null;
      host.requestUpdate();
    });
  }
  return host._autocompleteRegCache && host._autocompleteRegCache !== "pending"
    ? host._autocompleteRegCache
    : null;
}

function _deviceLabel(host, deviceId) {
  const cache = _cachedRegistries(host);
  const dev = cache?.devices?.[deviceId];
  return dev?.name_by_user || dev?.name || deviceId;
}

function _areaLabel(host, areaId) {
  const cache = _cachedRegistries(host);
  const area = cache?.areas?.[areaId] || host.hass?.areas?.[areaId];
  return area?.name || areaId;
}

// Walk hass.states and emit one item per entity in IGNORE_ENTITY_DOMAINS,
// plus one item per area. Mirrors chat-autocomplete's buildSuggestionIndex
// shape (kind / label / area / icon / _lowerLabel) so rankSuggestions can
// score it identically — but with a broader domain set so sensors,
// binary_sensors, persons, and device_trackers are reachable from the
// picker. Without this, those entities can only be excluded by leaving
// the panel and labelling them in HA's UI.
function _buildEntityIndex(hass, areasMap, devicesMap, entitiesMap) {
  const items = [];
  if (!hass?.states) return items;

  const areaById = {};
  if (areasMap && typeof areasMap === "object") {
    for (const [id, a] of Object.entries(areasMap)) {
      areaById[id] = a?.name || id;
    }
  }
  // Prefer the loaded full entity registry (config/entity_registry/list) so we
  // can follow entity → device → area for entities that inherit the area from
  // their device. ``hass.entities`` is the display registry and omits
  // ``device_id``, so falling back to it disables area-derived
  // disambiguation — common-name entities like "Lamp" would collapse to a
  // single, area-less row in the picker.
  const entReg = entitiesMap || hass.entities || {};

  for (const [entityId, state] of Object.entries(hass.states)) {
    const domain = entityId.split(".")[0];
    if (!IGNORE_ENTITY_DOMAINS.has(domain)) continue;
    const friendly = state?.attributes?.friendly_name;
    if (!friendly) continue;
    const entry = entReg[entityId];
    let areaId = entry?.area_id || null;
    if (!areaId && entry?.device_id && devicesMap) {
      areaId = devicesMap[entry.device_id]?.area_id || null;
    }
    const areaName = areaId ? areaById[areaId] || null : null;
    items.push({
      kind: KIND_ENTITY,
      domain,
      entity_id: entityId,
      label: friendly,
      area_id: areaId,
      area: areaName,
      icon: IGNORE_DOMAIN_ICONS[domain] || "mdi:devices",
      _lowerLabel: friendly.toLowerCase(),
    });
  }

  for (const [areaId, name] of Object.entries(areaById)) {
    items.push({
      kind: KIND_AREA,
      area_id: areaId,
      label: name,
      icon: "mdi:floor-plan",
      _lowerLabel: name.toLowerCase(),
    });
  }

  return items;
}

function _buildDeviceIndex(devicesMap, areasMap) {
  if (!devicesMap) return [];
  const items = [];
  for (const [id, dev] of Object.entries(devicesMap)) {
    const name = dev?.name_by_user || dev?.name;
    if (!name) continue;
    const areaId = dev.area_id || null;
    const areaName = areaId ? areasMap?.[areaId]?.name || null : null;
    items.push({
      kind: KIND_HA_DEVICE,
      device_id: id,
      label: name,
      area_id: areaId,
      area: areaName,
      icon: "mdi:chip",
      _lowerLabel: name.toLowerCase(),
    });
  }
  return items;
}

// Round-robin merge of pre-ranked per-kind lists. Each kind takes its turn
// contributing the next-best result until we hit ``max``, so a single
// matching area can't be drowned out by a flood of device matches even
// when devices alphabetically dominate the query (the "Kitchen" case).
function _interleave(lists, max) {
  const out = [];
  let i = 0;
  while (out.length < max) {
    let added = false;
    for (const list of lists) {
      if (i < list.length && out.length < max) {
        out.push(list[i]);
        added = true;
      }
    }
    if (!added) break;
    i++;
  }
  return out;
}

function _computeDropdownItems(host, query) {
  const cache = _cachedRegistries(host);
  const areasMap = cache?.areas || host.hass?.areas;
  const entityIndex = _buildEntityIndex(
    host.hass,
    areasMap,
    cache?.devices,
    cache?.entities,
  );
  const deviceIndex = _buildDeviceIndex(cache?.devices, areasMap);

  const q = (query || "").trim();
  if (q.length < MIN_QUERY) return [];

  const tagged = _labelTagged(host);
  const taggedEntities = new Set(tagged.entities);
  const taggedDevices = new Set(tagged.devices);
  const taggedAreas = new Set(tagged.areas);

  const entities = rankSuggestions(
    entityIndex,
    KIND_ENTITY,
    q,
    AUTOCOMPLETE_MAX_RESULTS,
    null,
  ).filter((it) => !taggedEntities.has(it.entity_id));
  const devices = rankSuggestions(
    deviceIndex,
    KIND_HA_DEVICE,
    q,
    AUTOCOMPLETE_MAX_RESULTS,
    null,
  ).filter((it) => !taggedDevices.has(it.device_id));
  const areas = rankSuggestions(
    entityIndex,
    KIND_AREA,
    q,
    AUTOCOMPLETE_MAX_RESULTS,
    null,
  ).filter((it) => !taggedAreas.has(it.area_id));

  // Areas come first in each round so an area match for an ambiguous
  // query like "Kitchen" (one area, many devices) actually shows up
  // instead of being pushed off the visible list.
  return _interleave([areas, devices, entities], AUTOCOMPLETE_MAX_RESULTS);
}

function _selectItem(host, item) {
  if (!item) return;
  host._ignoreInput = "";
  host._ignoreDropdownOpen = false;
  host._ignoreDropdownIndex = 0;
  host.requestUpdate();
  if (item.kind === KIND_AREA) {
    _applyLabel(host, { area_id: item.area_id });
  } else if (item.kind === KIND_HA_DEVICE) {
    _applyLabel(host, { device_id: item.device_id });
  } else {
    _applyLabel(host, { entity_id: item.entity_id });
  }
}

function _openEntity(host, entityId) {
  host.dispatchEvent(
    new CustomEvent("hass-more-info", {
      bubbles: true,
      composed: true,
      detail: { entityId },
    }),
  );
}

function _navigate(path) {
  window.history.pushState(null, "", path);
  window.dispatchEvent(new Event("location-changed"));
}

function _renderChip({ icon, label, kindLabel, title, onOpen, onRemove }) {
  return html`
    <span class="composer-selection-chip" title=${title || label}>
      <button
        type="button"
        @click=${onOpen}
        style="display:inline-flex;align-items:center;gap:4px;background:none;border:none;color:inherit;font:inherit;cursor:pointer;padding:0;"
      >
        <ha-icon icon=${icon}></ha-icon>
        ${label}
        ${kindLabel
          ? html`<span
              style="font-size:10px;text-transform:uppercase;letter-spacing:0.5px;color:var(--secondary-text-color);"
              >${kindLabel}</span
            >`
          : ""}
      </button>
      <button
        type="button"
        title="Remove label"
        @click=${(e) => {
          e.stopPropagation();
          onRemove();
        }}
      >
        ×
      </button>
    </span>
  `;
}

function _renderDropdown(host, items, activeIndex) {
  if (!items.length) return "";
  return html`
    <div
      style="position:absolute;top:100%;left:0;right:0;z-index:10;margin-top:4px;border-radius:10px;border:1px solid var(--divider-color);background:var(--card-background-color);box-shadow:0 4px 12px rgba(0,0,0,0.15);overflow:hidden;max-height:240px;overflow-y:auto;"
    >
      ${items.map((item, idx) => {
        let kindLabel = "";
        if (item.kind === KIND_AREA) kindLabel = "Area";
        else if (item.kind === KIND_HA_DEVICE) kindLabel = "Device";
        const active = idx === activeIndex;
        return html`
          <button
            type="button"
            data-ignore-row=${idx}
            style="display:flex;align-items:center;gap:8px;width:100%;text-align:left;padding:10px 12px;border:none;background:${active
              ? "var(--secondary-background-color)"
              : "transparent"};color:var(--primary-text-color);font-size:13px;cursor:pointer;"
            @mouseenter=${() => {
              host._ignoreDropdownIndex = idx;
              host.requestUpdate();
            }}
            @mousedown=${(e) => {
              e.preventDefault();
              _selectItem(host, item);
            }}
          >
            <ha-icon
              icon=${item.icon}
              style="--mdc-icon-size:16px;color:var(--secondary-text-color);"
            ></ha-icon>
            <span style="flex:1;">${item.label}</span>
            ${kindLabel
              ? html`<span
                  style="font-size:11px;color:var(--secondary-text-color);"
                  >${kindLabel}</span
                >`
              : item.area
                ? html`<span
                    style="font-size:11px;color:var(--secondary-text-color);"
                    >${item.area}</span
                  >`
                : ""}
          </button>
        `;
      })}
    </div>
  `;
}

function _renderInfoCallout(labelName) {
  return html`
    <details
      style="margin-top:6px;border:1px solid var(--divider-color);border-radius:8px;background:var(--card-background-color);overflow:hidden;"
    >
      <summary
        style="display:flex;align-items:center;gap:8px;padding:8px 12px;cursor:pointer;font-size:13px;color:var(--secondary-text-color);list-style:none;"
      >
        <ha-icon
          icon="mdi:information-outline"
          style="--mdc-icon-size:16px;color:var(--secondary-text-color);"
        ></ha-icon>
        How does this work?
      </summary>
      <div
        style="padding:0 12px 10px 36px;font-size:13px;color:var(--secondary-text-color);line-height:1.45;"
      >
        Selora tags items with the
        <strong>${labelName}</strong> HA label to skip them in proactive
        suggestions. Add anything here, or apply the label directly from Home
        Assistant — entity / device / area pages or Settings → Labels — for the
        same effect. Tagging a device hides all its entities in one go.
      </div>
    </details>
  `;
}

// Read state straight off `host` so we always see the latest open/index/items.
// The previous closure-captured `items` array could fall out of sync with
// what the dropdown was rendering, which left ArrowUp/Down falling through
// to the input's native cursor handling.
function _onInputKeydown(host, e) {
  if (!["ArrowDown", "ArrowUp", "Enter", "Escape"].includes(e.key)) return;

  if (!host._ignoreDropdownOpen) {
    if (e.key === "Escape") {
      e.preventDefault();
    }
    return;
  }

  const items = _computeDropdownItems(host, host._ignoreInput || "");
  if (!items.length) {
    if (e.key === "Escape") {
      e.preventDefault();
      host._ignoreDropdownOpen = false;
      host.requestUpdate();
    }
    return;
  }

  // For every key we handle, suppress the input's default behaviour
  // BEFORE doing any state mutation — otherwise ArrowUp jumps the caret
  // to column 0 before we get to call preventDefault on it.
  e.preventDefault();
  e.stopPropagation();

  const idx = host._ignoreDropdownIndex ?? 0;
  if (e.key === "ArrowDown") {
    host._ignoreDropdownIndex = (idx + 1) % items.length;
  } else if (e.key === "ArrowUp") {
    host._ignoreDropdownIndex = (idx - 1 + items.length) % items.length;
  } else if (e.key === "Enter") {
    _selectItem(host, items[idx]);
    return;
  } else if (e.key === "Escape") {
    host._ignoreDropdownOpen = false;
  }
  host.requestUpdate();
}

export function renderIgnoreList(host) {
  const query = host._ignoreInput || "";
  const open = host._ignoreDropdownOpen && query.trim().length >= MIN_QUERY;
  const items = open ? _computeDropdownItems(host, query) : [];
  // Clamp the active index whenever the candidate set shrinks.
  if (
    host._ignoreDropdownIndex == null ||
    host._ignoreDropdownIndex >= items.length
  ) {
    host._ignoreDropdownIndex = 0;
  }
  const activeIndex = items.length ? host._ignoreDropdownIndex : -1;

  const tagged = _labelTagged(host);
  const total =
    tagged.entities.length + tagged.devices.length + tagged.areas.length;
  const labelName = _config(host).exclude_label_name || "Selora exclude";

  return html`
    <div class="section-card settings-section">
      <div class="section-card-header">
        <h3>Ignore in suggestions</h3>
      </div>

      ${_renderInfoCallout(labelName)}

      <div style="position:relative;margin-top:12px;">
        <input
          class="form-select"
          type="text"
          .value=${query}
          placeholder="Search an entity, device, or area…"
          style="width:100%;box-sizing:border-box;"
          @input=${(e) => {
            host._ignoreInput = e.target.value;
            host._ignoreDropdownOpen = true;
            host._ignoreDropdownIndex = 0;
            host.requestUpdate();
          }}
          @focus=${() => {
            host._ignoreDropdownOpen = true;
            host.requestUpdate();
          }}
          @blur=${() => {
            // Delay so a mousedown on a dropdown row still resolves.
            setTimeout(() => {
              host._ignoreDropdownOpen = false;
              host.requestUpdate();
            }, 150);
          }}
          @keydown=${(e) => _onInputKeydown(host, e)}
        />
        ${_renderDropdown(host, items, activeIndex)}
      </div>

      ${total === 0
        ? html`<div
            style="font-size:13px;color:var(--secondary-text-color);padding:12px 0 4px;"
          >
            Nothing ignored yet.
          </div>`
        : html`
            <div
              class="composer-selections-inline"
              style="margin-top:12px;gap:6px;"
            >
              ${tagged.devices.map((did) =>
                _renderChip({
                  icon: "mdi:chip",
                  label: _deviceLabel(host, did),
                  kindLabel: "device",
                  title: `Open device · ${did}`,
                  onOpen: () => _navigate(`/config/devices/device/${did}`),
                  onRemove: () => _removeLabel(host, { device_id: did }),
                }),
              )}
              ${tagged.entities.map((eid) =>
                _renderChip({
                  icon: _entityIcon(eid),
                  label: _entityLabel(host, eid),
                  title: `Open ${eid}`,
                  onOpen: () => _openEntity(host, eid),
                  onRemove: () => _removeLabel(host, { entity_id: eid }),
                }),
              )}
              ${tagged.areas.map((aid) =>
                _renderChip({
                  icon: "mdi:floor-plan",
                  label: _areaLabel(host, aid),
                  kindLabel: "area",
                  title: `Open area · ${aid}`,
                  onOpen: () => _navigate(`/config/areas/area/${aid}`),
                  onRemove: () => _removeLabel(host, { area_id: aid }),
                }),
              )}
            </div>
          `}
    </div>
  `;
}
