import { html } from "lit";
import { DOMAIN_ICONS, _stateColor } from "./render-chat.js";
import { fmtEntity } from "../shared/formatting.js";
import { toggleYaml } from "./render-automations.js";
import { formatTimeAgo } from "../shared/date-utils.js";

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

  // Older sessions store scene messages with a scene_id but no
  // scene_status field — those are already saved in HA, so default to
  // "saved" instead of falling through to the pending-proposal UI
  // (which would let the user re-Accept and duplicate the scene).
  const status = msg.scene_status || (msg.scene_id ? "saved" : undefined);
  const yamlKey = `scene_${msgIndex}`;
  const yamlOpen = host._yamlOpen && host._yamlOpen[yamlKey];

  if (status === "saved") {
    return html`
      <div class="proposal-card" style="margin-top:12px;">
        <div class="proposal-header">
          <ha-icon icon="mdi:check-circle"></ha-icon>
          Scene Created
        </div>
        <div class="proposal-body">
          <div class="proposal-name">${scene.name}</div>
          <div class="proposal-status saved">
            <ha-icon icon="mdi:check"></ha-icon> Saved to Home Assistant
          </div>
          <div class="proposal-actions">
            <button
              class="btn btn-success"
              @click=${() => {
                // Prefer the resolved entity_id HA assigned at save time —
                // it can differ from scene.<scene_id> when the alias slug
                // wins or a collision suffix is applied.
                const id = msg.entity_id
                  ? msg.entity_id.replace(/^scene\./, "")
                  : msg.scene_id;
                host._activateScene(id, scene.name);
              }}
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

  if (status === "declined") {
    return html`
      <div class="proposal-card" style="margin-top:12px; opacity:0.6;">
        <div class="proposal-header" style="color:var(--secondary-text-color);">
          <ha-icon icon="mdi:close-circle-outline"></ha-icon>
          Scene Declined
        </div>
        <div class="proposal-body">
          <div class="proposal-name">${scene.name}</div>
          <div class="proposal-status declined">
            Dismissed. You can refine it by replying below.
          </div>
        </div>
      </div>
    `;
  }

  if (status === "refining") {
    return html`
      <div style="margin-top:12px;padding:14px 0 0;">
        ${_sceneCardHeader(scene.name, "Being Refined")}
        <div class="proposal-body" style="padding:0;">
          ${_renderEntityList(host, scene.entities || {})}
          <div
            style="font-size:13px;color:var(--secondary-text-color);margin-top:10px;"
          >
            What changes would you like to make?
          </div>
        </div>
      </div>
    `;
  }

  // Pending proposal — full review UI
  return html`
    <div style="margin-top:12px;padding:14px 0 0;">
      ${_sceneCardHeader(scene.name, "Proposal")}
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
            @click=${() => host._acceptScene(msgIndex)}
          >
            <ha-icon icon="mdi:check" style="--mdc-icon-size:14px;"></ha-icon>
            Accept &amp; Save
          </button>
          <button
            class="btn btn-outline"
            @click=${() => host._refineScene(msgIndex)}
          >
            <ha-icon
              icon="mdi:pencil-outline"
              style="--mdc-icon-size:14px;"
            ></ha-icon>
            Refine
          </button>
          <button
            class="btn btn-outline"
            style="color:#ef4444;border-color:rgba(239,68,68,0.3);"
            @click=${() => host._declineScene(msgIndex)}
          >
            <ha-icon icon="mdi:close" style="--mdc-icon-size:14px;"></ha-icon>
            Decline
          </button>
        </div>
      </div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Scenes tab — list of Selora-managed scenes
// ---------------------------------------------------------------------------

function _sceneEntityCount(scene) {
  if (typeof scene.entity_count === "number") return scene.entity_count;
  return Object.keys(scene.entities || {}).length;
}

export function renderScenes(host) {
  const filterText = (host._sceneFilter || "").toLowerCase();
  const sortBy = host._sceneSortBy || "recent";

  let filtered = [...(host._scenes || [])];
  if (filterText) {
    filtered = filtered.filter((s) =>
      (s.name || "").toLowerCase().includes(filterText),
    );
  }

  if (sortBy === "recent") {
    filtered.sort((a, b) => {
      const at = a.updated_at ? new Date(a.updated_at).getTime() : 0;
      const bt = b.updated_at ? new Date(b.updated_at).getTime() : 0;
      return bt - at;
    });
  } else if (sortBy === "alpha") {
    filtered.sort((a, b) => (a.name || "").localeCompare(b.name || ""));
  } else if (sortBy === "size") {
    filtered.sort((a, b) => (b.entity_count || 0) - (a.entity_count || 0));
  }

  return html`
    <div class="scroll-view">
      <div class="section-card">
        <div class="section-card-header">
          <h3>Your Scenes</h3>
        </div>
        ${(host._scenes || []).length > 0
          ? html`
              <div class="filter-row" style="margin-top:12px;">
                <div class="filter-input-wrap" style="flex:0 1 260px;">
                  <ha-icon icon="mdi:magnify"></ha-icon>
                  <input
                    type="text"
                    placeholder="Filter scenes…"
                    .value=${host._sceneFilter || ""}
                    @input=${(e) => {
                      host._sceneFilter = e.target.value;
                    }}
                  />
                  ${host._sceneFilter
                    ? html`<ha-icon
                        icon="mdi:close-circle"
                        style="--mdc-icon-size:16px;cursor:pointer;opacity:0.5;flex-shrink:0;"
                        @click=${() => {
                          host._sceneFilter = "";
                        }}
                      ></ha-icon>`
                    : ""}
                </div>
                <select
                  class="sort-select"
                  .value=${host._sceneSortBy || "recent"}
                  @change=${(e) => {
                    host._sceneSortBy = e.target.value;
                  }}
                >
                  <option value="recent">Recently updated</option>
                  <option value="alpha">Alphabetical</option>
                  <option value="size">Most entities</option>
                </select>
                <div
                  style="margin-left:auto;display:flex;align-items:center;gap:8px;"
                >
                  <button
                    class="btn btn-accent"
                    style="white-space:nowrap;"
                    @click=${() => host._newSceneChat()}
                  >
                    <ha-icon
                      icon="mdi:plus"
                      style="--mdc-icon-size:13px;"
                    ></ha-icon>
                    New Scene
                  </button>
                </div>
              </div>
              <div class="automations-summary">
                ${filtered.length} scene${filtered.length !== 1 ? "s" : ""}
              </div>
              <div class="automations-list">
                ${filtered.map((s) => {
                  const sceneId = s.scene_id;
                  const sceneEntityId = s.entity_id;
                  const entities = s.entities || {};
                  const entityCount = _sceneEntityCount(s);
                  const isExpanded = !!host._expandedScenes?.[sceneId];
                  const yamlOpen = !!host._sceneYamlOpen?.[sceneId];
                  const burgerOpen = host._openSceneBurger === sceneId;
                  const deleting = !!host._deletingScene?.[sceneId];
                  const updated = formatTimeAgo(s.updated_at);
                  const meta = `${entityCount} entit${entityCount === 1 ? "y" : "ies"}${updated ? ` · updated ${updated}` : ""}`;
                  return html`
                    <div
                      class="auto-row${isExpanded ? " expanded" : ""}"
                      data-scene-id="${sceneId}"
                    >
                      <div
                        class="auto-row-main"
                        @click=${(e) => {
                          if (
                            e.target.closest(
                              ".burger-menu-wrapper, .burger-dropdown, .burger-item, .btn",
                            )
                          )
                            return;
                          host._expandedScenes = {
                            ...host._expandedScenes,
                            [sceneId]: !isExpanded,
                          };
                        }}
                      >
                        <ha-icon
                          icon="mdi:palette"
                          style="--mdc-icon-size:18px;color:var(--selora-accent);flex-shrink:0;"
                        ></ha-icon>
                        <div class="auto-row-name">
                          <div class="auto-row-title-row">
                            <span class="auto-row-title">${s.name}</span>
                          </div>
                          <span class="auto-row-desc auto-row-desc--meta-only"
                            >${meta}</span
                          >
                          <span class="auto-row-mobile-meta">
                            <span>${meta}</span>
                            <ha-icon
                              icon="mdi:chevron-down"
                              class="card-chevron ${isExpanded ? "open" : ""}"
                              style="--mdc-icon-size:16px;"
                            ></ha-icon>
                          </span>
                        </div>
                        <button
                          class="btn btn-outline"
                          style="padding:6px 12px;"
                          ?disabled=${!sceneEntityId}
                          @click=${(e) => {
                            e.stopPropagation();
                            const id = sceneEntityId
                              ? sceneEntityId.replace(/^scene\./, "")
                              : sceneId;
                            host._activateScene(id, s.name);
                          }}
                          title="Activate scene"
                        >
                          <ha-icon
                            icon="mdi:play"
                            style="--mdc-icon-size:14px;"
                          ></ha-icon>
                          Activate
                        </button>
                        <div class="burger-menu-wrapper">
                          <button
                            class="burger-btn"
                            @click=${(e) => {
                              e.stopPropagation();
                              host._openSceneBurger = burgerOpen
                                ? null
                                : sceneId;
                            }}
                            title="More actions"
                          >
                            <ha-icon
                              icon="mdi:dots-vertical"
                              style="--mdc-icon-size:16px;"
                            ></ha-icon>
                          </button>
                          ${burgerOpen
                            ? html`
                                <div class="burger-dropdown">
                                  <button
                                    class="burger-item"
                                    @click=${(e) => {
                                      e.stopPropagation();
                                      host._openSceneBurger = null;
                                      host._refineSceneInChat(s);
                                    }}
                                  >
                                    <ha-icon
                                      icon="mdi:chat-processing-outline"
                                      style="--mdc-icon-size:14px;"
                                    ></ha-icon>
                                    Refine in chat
                                  </button>
                                  <button
                                    class="burger-item"
                                    @click=${(e) => {
                                      e.stopPropagation();
                                      host._openSceneBurger = null;
                                      window.history.pushState(
                                        null,
                                        "",
                                        "/config/scene/dashboard",
                                      );
                                      window.dispatchEvent(
                                        new Event("location-changed"),
                                      );
                                    }}
                                  >
                                    <ha-icon
                                      icon="mdi:open-in-new"
                                      style="--mdc-icon-size:14px;"
                                    ></ha-icon>
                                    Open in HA
                                  </button>
                                  <button
                                    class="burger-item danger"
                                    ?disabled=${deleting}
                                    @click=${(e) => {
                                      e.stopPropagation();
                                      host._openSceneBurger = null;
                                      host._deleteSceneConfirmId = sceneId;
                                      host._deleteSceneConfirmName = s.name;
                                    }}
                                  >
                                    <ha-icon
                                      icon="mdi:trash-can-outline"
                                      style="--mdc-icon-size:14px;"
                                    ></ha-icon>
                                    ${deleting ? "Deleting…" : "Delete"}
                                  </button>
                                </div>
                              `
                            : ""}
                        </div>
                      </div>
                      ${isExpanded
                        ? html`
                            <div class="auto-row-expand">
                              ${Object.keys(entities).length
                                ? _renderEntityList(host, entities)
                                : html`<div
                                    style="font-size:12px;opacity:0.6;padding:6px 0;"
                                  >
                                    No entity details available — open the scene
                                    in Home Assistant to inspect it.
                                  </div>`}
                              <div
                                class="yaml-toggle"
                                style="margin-top:10px;"
                                @click=${() => {
                                  host._sceneYamlOpen = {
                                    ...host._sceneYamlOpen,
                                    [sceneId]: !yamlOpen,
                                  };
                                }}
                              >
                                <ha-icon
                                  icon="mdi:code-braces"
                                  style="--mdc-icon-size:14px;"
                                ></ha-icon>
                                ${yamlOpen ? "Hide YAML" : "View YAML"}
                              </div>
                              ${yamlOpen
                                ? html`
                                    <ha-code-editor
                                      mode="yaml"
                                      .value=${s.yaml ||
                                      "# YAML not available — open the scene in Home Assistant to view it."}
                                      read-only
                                      style="--code-mirror-font-size:12px;"
                                    ></ha-code-editor>
                                  `
                                : ""}
                            </div>
                          `
                        : ""}
                    </div>
                  `;
                })}
              </div>
              ${filtered.length === 0 && (host._scenes || []).length > 0
                ? html`<div
                    style="text-align:center;opacity:0.45;padding:24px 0;"
                  >
                    No scenes match "${host._sceneFilter}"
                  </div>`
                : ""}
            `
          : html`<div style="text-align:center;padding:32px 0;">
              <ha-icon
                icon="mdi:palette"
                style="--mdc-icon-size:40px;display:block;margin-bottom:8px;opacity:0.35;"
              ></ha-icon>
              <p style="opacity:0.45;margin:0 0 12px;">
                No scenes yet. Ask Selora to capture a moment.
              </p>
              <button
                class="btn btn-accent"
                @click=${() => host._newSceneChat()}
              >
                <ha-icon
                  icon="mdi:plus"
                  style="--mdc-icon-size:14px;"
                ></ha-icon>
                New Scene
              </button>
            </div>`}
      </div>
      ${renderDeleteSceneModal(host)}
    </div>
  `;
}

function renderDeleteSceneModal(host) {
  if (!host._deleteSceneConfirmId) return "";
  const name = host._deleteSceneConfirmName || "this scene";
  return html`
    <div
      class="modal-overlay"
      @click=${(e) => {
        if (e.target === e.currentTarget) {
          host._deleteSceneConfirmId = null;
          host._deleteSceneConfirmName = null;
        }
      }}
    >
      <div class="modal-content" style="max-width:420px;text-align:center;">
        <div style="font-size:17px;font-weight:600;margin-bottom:8px;">
          Delete Scene
        </div>
        <div style="font-size:13px;opacity:0.7;margin-bottom:20px;">
          Delete <strong>${name}</strong>? This removes the scene from Home
          Assistant and cannot be undone.
        </div>
        <div style="display:flex;gap:10px;justify-content:center;">
          <button
            class="btn btn-outline"
            @click=${() => {
              host._deleteSceneConfirmId = null;
              host._deleteSceneConfirmName = null;
            }}
          >
            Cancel
          </button>
          <button
            class="btn"
            style="background:#ef4444;color:#fff;border-color:#ef4444;"
            @click=${() => host._confirmDeleteScene()}
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  `;
}
