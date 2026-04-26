import { html } from "lit";
import { DOMAIN_ICONS, _stateColor } from "./render-chat.js";
import { fmtEntity } from "../shared/formatting.js";
import { toggleYaml } from "./render-automations.js";

// ---------------------------------------------------------------------------
// Scene card (chat scene confirmations)
// ---------------------------------------------------------------------------

function _sceneCardHeader(name, badge) {
  return html`
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
      <ha-icon
        icon="mdi:palette"
        style="color:var(--primary-text-color);--mdc-icon-size:18px;display:flex;flex-shrink:0;"
      ></ha-icon>
      <span
        style="font-weight:700;font-size:14px;color:var(--primary-text-color);"
        >${name}</span
      >
      <span
        style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;background:var(--selora-accent);color:#000;padding:2px 8px;border-radius:4px;"
        >${badge}</span
      >
    </div>
  `;
}

function _formatBrightness(val) {
  if (val == null) return null;
  const num = Number(val);
  if (isNaN(num)) return null;
  return `${Math.round((num / 255) * 100)}%`;
}

function _formatPosition(val) {
  if (val == null) return null;
  const num = Number(val);
  if (isNaN(num)) return null;
  return `${Math.round(num)}%`;
}

function _formatEntityAttrs(stateData) {
  const parts = [];
  const brightness = _formatBrightness(stateData.brightness);
  if (brightness) parts.push(brightness);
  if (stateData.color_temp != null)
    parts.push(`${stateData.color_temp} mireds`);
  if (stateData.temperature != null) parts.push(`${stateData.temperature}°`);
  const position = _formatPosition(
    stateData.position ?? stateData.current_position,
  );
  if (position) parts.push(position);
  const fanSpeed = _formatPosition(stateData.percentage);
  if (fanSpeed) parts.push(fanSpeed);
  if (stateData.volume_level != null)
    parts.push(`vol ${Math.round(stateData.volume_level * 100)}%`);
  if (stateData.source != null) parts.push(stateData.source);
  return parts.join(" · ");
}

function _renderEntityList(host, entities) {
  const entries = Object.entries(entities);
  if (!entries.length) return "";

  return html`
    <div class="scene-entity-list">
      ${entries.map(([entityId, stateData]) => {
        const domain = entityId.split(".")[0];
        const icon = DOMAIN_ICONS[domain] || "mdi:devices";
        const state = stateData.state || "unknown";
        const attrs = _formatEntityAttrs(stateData);
        const name = fmtEntity(host.hass, entityId);

        return html`
          <div class="scene-entity-row">
            <div class="scene-entity-name">
              <ha-icon
                icon=${icon}
                style="--mdc-icon-size:16px;color:var(--selora-accent);"
              ></ha-icon>
              <span>${name}</span>
            </div>
            <div class="scene-entity-state">
              ${attrs
                ? html`<span class="scene-entity-attr">${attrs}</span>`
                : ""}
              <span style="color:${_stateColor(state)};">${state}</span>
            </div>
          </div>
        `;
      })}
    </div>
  `;
}

export function renderSceneCard(host, msg, msgIndex) {
  const scene = msg.scene;
  if (!scene) return "";

  const yamlKey = `scene_${msgIndex}`;
  const yamlOpen = host._yamlOpen && host._yamlOpen[yamlKey];

  return html`
    <div style="margin-top:12px;padding:14px 0 0;">
      ${_sceneCardHeader(scene.name, "Scene Saved")}
      <div class="proposal-body" style="padding:0;">
        ${_renderEntityList(host, scene.entities || {})}

        <div class="yaml-toggle" @click=${() => toggleYaml(host, yamlKey)}>
          <ha-icon
            icon="mdi:code-braces"
            style="--mdc-icon-size:14px;"
          ></ha-icon>
          ${yamlOpen ? "Hide YAML" : "View YAML"}
        </div>
        ${yamlOpen && msg.scene_yaml
          ? html`
              <ha-code-editor
                mode="yaml"
                .value=${msg.scene_yaml}
                read-only
                style="--code-mirror-font-size:12px;"
              ></ha-code-editor>
            `
          : ""}

        <div class="proposal-actions">
          <button
            class="btn btn-success"
            @click=${() => host._activateScene(msg.scene_id, scene.name)}
          >
            <ha-icon icon="mdi:play" style="--mdc-icon-size:14px;"></ha-icon>
            Activate
          </button>
          <button
            class="btn btn-outline"
            @click=${() => {
              window.history.pushState(null, "", "/config/scene/dashboard");
              window.dispatchEvent(new Event("location-changed"));
            }}
          >
            <ha-icon
              icon="mdi:open-in-new"
              style="--mdc-icon-size:14px;"
            ></ha-icon>
            View in HA
          </button>
        </div>
      </div>
    </div>
  `;
}
