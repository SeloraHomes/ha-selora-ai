// Links to the entities and devices a card names, as the automation flow
// draws them: an entity opens Home Assistant's more-info dialog, a device its
// settings page, so the user can check what Selora is talking about without
// leaving the panel.
import { html } from "lit";

export const DOMAIN_ICONS = {
  light: "mdi:lightbulb",
  switch: "mdi:toggle-switch",
  climate: "mdi:thermostat",
  lock: "mdi:lock",
  cover: "mdi:window-shutter",
  fan: "mdi:fan",
  media_player: "mdi:speaker",
  vacuum: "mdi:robot-vacuum",
  sensor: "mdi:eye",
  binary_sensor: "mdi:motion-sensor",
  water_heater: "mdi:water-boiler",
  humidifier: "mdi:air-humidifier",
  camera: "mdi:cctv",
  device_tracker: "mdi:map-marker",
  person: "mdi:account",
  zone: "mdi:map-marker-radius",
  sun: "mdi:weather-sunny",
  weather: "mdi:weather-partly-cloudy",
  automation: "mdi:robot",
  scene: "mdi:palette",
  script: "mdi:script-text",
  input_boolean: "mdi:toggle-switch-variant",
  input_number: "mdi:numeric",
  input_select: "mdi:form-dropdown",
  input_text: "mdi:form-textbox",
  input_datetime: "mdi:calendar-clock",
  input_button: "mdi:gesture-tap-button",
  timer: "mdi:timer-outline",
  counter: "mdi:counter",
  group: "mdi:google-circles-communities",
  notify: "mdi:bell",
  mobile_app: "mdi:cellphone",
  alarm_control_panel: "mdi:shield-home",
  air_quality: "mdi:air-filter",
  remote: "mdi:remote",
};

// Click target for entities mentioned in a trigger/condition/action. Opens
// HA's built-in more-info dialog via the standard `hass-more-info` event
// so users can dive into the entity without leaving chat. Rendered as an
// inline link rather than a pill so prose like "Turn off Living Room Fan"
// reads as a sentence instead of a chip-stuffed UI fragment.
export function renderFlowEntityLink(host, entityId) {
  const stateObj = host.hass?.states?.[entityId];
  const friendly = stateObj?.attributes?.friendly_name || entityId;
  const icon =
    stateObj?.attributes?.icon ||
    DOMAIN_ICONS[entityId.split(".")[0]] ||
    "mdi:circle-medium";
  return html`<button
    type="button"
    class="flow-entity-link"
    title=${`Open ${friendly} (${entityId})`}
    @click=${(e) => {
      e.stopPropagation();
      host.dispatchEvent(
        new CustomEvent("hass-more-info", {
          bubbles: true,
          composed: true,
          detail: { entityId },
        }),
      );
    }}
  >
    <ha-icon icon=${icon}></ha-icon><span>${friendly}</span>
  </button>`;
}

// Click target for a device referenced by a device trigger/condition.
// Devices have no more-info dialog, so this navigates to the device's
// config page the same way HA's own UI does — history.pushState plus a
// location-changed event so the SPA router picks it up without a reload.
export function renderFlowDeviceLink(host, deviceId, name, domain) {
  const icon = (domain && DOMAIN_ICONS[domain]) || "mdi:devices";
  return html`<button
    type="button"
    class="flow-entity-link"
    title=${`Open ${name}`}
    @click=${(e) => {
      e.stopPropagation();
      window.history.pushState(null, "", `/config/devices/device/${deviceId}`);
      window.dispatchEvent(new Event("location-changed"));
    }}
  >
    <ha-icon icon=${icon}></ha-icon><span>${name}</span>
  </button>`;
}
