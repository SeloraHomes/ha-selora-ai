import { html } from "lit";
import { toggleYaml } from "./render-automations.js";
import { burgerMenuAnchor } from "./automation-management.js";
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

function _entityArea(host, entityId) {
  const entReg = host.hass?.entities?.[entityId];
  const areaId =
    entReg?.area_id || host.hass?.devices?.[entReg?.device_id]?.area_id || null;
  return areaId ? host.hass?.areas?.[areaId]?.name || null : null;
}

// One row, two real HA tiles for the same entity:
//   - left ("Now"): the live entity — interactive, reflects real state.
//   - right ("Scene sets"): the same tile forced to the scene's target
//     state via data-scene-states, rendered read-only (pointer-events
//     disabled in CSS) so it's a preview, not a second control.
function _renderTargetRow(host, entityId, stateData, editSceneId) {
  // The right tile shows the (possibly edited) target. When the scene is
  // editable, its callService is intercepted (see _hydrateEntityChips)
  // via data-scene-edit-id so dragging the tile changes the scene, not
  // the device. Non-editable rows (chat proposals) stay read-only.
  const target =
    editSceneId && host._sceneEditedEntities(editSceneId)?.[entityId] != null
      ? host._sceneEditedEntities(editSceneId)[entityId]
      : stateData;
  const single = JSON.stringify({ [entityId]: target });

  return html`
    <div class="scene-ent-row">
      <div
        class="selora-entity-grid scene-ent-tile"
        data-entity-ids=${entityId}
      ></div>
      <ha-icon class="scene-ent-arrow" icon="mdi:arrow-right"></ha-icon>
      <div
        class="selora-entity-grid scene-ent-tile ${editSceneId
          ? "scene-ent-tile--edit"
          : "scene-ent-tile--forced"}"
        data-entity-ids=${entityId}
        data-scene-states=${single}
        data-scene-edit-id=${editSceneId || ""}
      ></div>
    </div>
  `;
}

function _renderEntityList(host, entities, editSceneId = null) {
  const ids = Object.keys(entities || {});
  if (!ids.length) return "";

  // When editing, render rows from the working (edited) entity set so
  // newly adjusted entities reflect immediately.
  const source = editSceneId
    ? host._sceneEditedEntities(editSceneId)
    : entities;

  // Group by area so multi-room scenes read cleanly; named areas first,
  // unassigned last.
  const groups = new Map();
  for (const id of ids) {
    const area = _entityArea(host, id);
    if (!groups.has(area)) groups.set(area, []);
    groups.get(area).push(id);
  }
  const sorted = [...groups.entries()].sort((a, b) => {
    if (!a[0]) return 1;
    if (!b[0]) return -1;
    return a[0].localeCompare(b[0]);
  });
  const showHeaders = groups.size > 1;

  return html`
    ${editSceneId
      ? html`<div class="scene-ent-hint">
          <ha-icon icon="mdi:gesture-tap"></ha-icon>
          <span
            >Adjust each entity's desired state on the <strong>right</strong>.
            Edits don't touch your devices until you <strong>Test</strong> or
            activate the scene.</span
          >
        </div>`
      : ""}
    <div class="scene-ent-list">
      <div class="scene-ent-head">
        <span>Now</span>
        <span></span>
        <span class="scene-ent-cap--target">Scene sets</span>
      </div>
      ${sorted.map(
        ([area, areaIds]) => html`
          ${showHeaders
            ? html`<div class="scene-ent-area">
                <ha-icon icon="mdi:floor-plan"></ha-icon>
                <span>${area || "Unassigned"}</span>
              </div>`
            : ""}
          ${areaIds.map((id) =>
            _renderTargetRow(host, id, source[id], editSceneId),
          )}
        `,
      )}
    </div>
    ${editSceneId && host._sceneIsDirty(editSceneId)
      ? html`<div class="scene-edit-bar">
          <span class="scene-edit-bar-msg">
            <ha-icon icon="mdi:pencil"></ha-icon> Unsaved changes to this scene
          </span>
          <span class="scene-edit-bar-actions">
            <button
              class="btn btn-outline"
              ?disabled=${host._savingScene?.[editSceneId] ||
              host._testingScene?.[editSceneId]}
              title="Apply these states to your devices now, without saving"
              @click=${() => host._testSceneEdits(editSceneId)}
            >
              <ha-icon
                icon="mdi:flask-outline"
                style="--mdc-icon-size:14px;"
              ></ha-icon>
              ${host._testingScene?.[editSceneId] ? "Testing…" : "Test"}
            </button>
            <button
              class="btn btn-outline"
              ?disabled=${host._savingScene?.[editSceneId]}
              @click=${() => host._discardSceneEdits(editSceneId)}
            >
              Discard
            </button>
            <button
              class="btn btn-success"
              ?disabled=${host._savingScene?.[editSceneId]}
              @click=${() => host._saveSceneEdits(editSceneId)}
            >
              <ha-icon
                icon="mdi:content-save"
                style="--mdc-icon-size:14px;"
              ></ha-icon>
              ${host._savingScene?.[editSceneId] ? "Saving…" : "Save changes"}
            </button>
          </span>
        </div>`
      : ""}
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
      <div style="margin-top:12px;padding:14px 0 0;">
        <div class="scene-saved-head">
          <ha-icon icon="mdi:check-circle" class="scene-saved-icon"></ha-icon>
          <span class="scene-saved-name">${scene.name}</span>
          <span class="scene-saved-tag">
            ${host._t("scenes_card_saved_status", "Saved to Home Assistant")}
          </span>
        </div>
        <div class="proposal-body" style="padding:0;">
          ${_renderEntityList(host, scene.entities || {})}
          ${msg.scene_yaml
            ? html`<div
                  class="yaml-toggle"
                  style="margin-top:10px;margin-bottom:0;"
                  @click=${() => toggleYaml(host, yamlKey)}
                >
                  <ha-icon
                    icon="mdi:code-braces"
                    style="--mdc-icon-size:14px;"
                  ></ha-icon>
                  ${yamlOpen
                    ? host._t("scenes_hide_yaml", "Hide YAML")
                    : host._t("scenes_view_yaml", "View YAML")}
                </div>
                ${yamlOpen
                  ? html`<ha-code-editor
                      mode="yaml"
                      .value=${msg.scene_yaml}
                      read-only
                      style="--code-mirror-font-size:12px;margin-top:10px;"
                    ></ha-code-editor>`
                  : ""}`
            : ""}
          <div class="proposal-actions" style="margin-top:14px;">
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
              ${host._t("scenes_card_test_button", "Test Scene")}
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
              ${host._t("scenes_card_view_in_ha_button", "View in HA")}
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
          ${host._t("scenes_card_declined_title", "Scene Declined")}
        </div>
        <div class="proposal-body">
          <div class="proposal-name">${scene.name}</div>
          <div class="proposal-status declined">
            ${host._t(
              "scenes_card_declined_message",
              "Dismissed. You can refine it by replying below.",
            )}
          </div>
        </div>
      </div>
    `;
  }

  if (status === "refining") {
    return html`
      <div style="margin-top:12px;padding:14px 0 0;">
        ${_sceneCardHeader(
          scene.name,
          host._t("scenes_card_refining_badge", "Being Refined"),
        )}
        <div class="proposal-body" style="padding:0;">
          ${_renderEntityList(host, scene.entities || {})}
          ${msg.scene_yaml
            ? html`<div
                class="yaml-toggle"
                style="margin-top:10px;margin-bottom:0;"
                @click=${() => toggleYaml(host, yamlKey)}
              >
                <ha-icon
                  icon="mdi:code-braces"
                  style="--mdc-icon-size:14px;"
                ></ha-icon>
                ${yamlOpen
                  ? host._t("scenes_hide_yaml", "Hide YAML")
                  : host._t("scenes_view_yaml", "View YAML")}
              </div>`
            : ""}
          ${yamlOpen && msg.scene_yaml
            ? html`
                <ha-code-editor
                  mode="yaml"
                  .value=${msg.scene_yaml}
                  read-only
                  style="--code-mirror-font-size:12px;margin-top:10px;"
                ></ha-code-editor>
              `
            : ""}
        </div>
      </div>
    `;
  }

  // Pending proposal — full review UI
  return html`
    <div style="margin-top:12px;padding:14px 0 0;">
      ${_sceneCardHeader(
        scene.name,
        host._t("scenes_card_proposal_badge", "Proposal"),
      )}
      <div class="proposal-body" style="padding:0;">
        ${_renderEntityList(host, scene.entities || {})}

        <div
          class="yaml-toggle"
          style="margin-top:12px;"
          @click=${() => toggleYaml(host, yamlKey)}
        >
          <ha-icon
            icon="mdi:code-braces"
            style="--mdc-icon-size:14px;"
          ></ha-icon>
          ${yamlOpen
            ? host._t("scenes_hide_yaml", "Hide YAML")
            : host._t("scenes_view_yaml", "View YAML")}
        </div>
        ${yamlOpen && msg.scene_yaml
          ? html`
              <ha-code-editor
                mode="yaml"
                .value=${msg.scene_yaml}
                read-only
                style="--code-mirror-font-size:12px;margin-top:6px;"
              ></ha-code-editor>
            `
          : ""}
        <div style="display:flex;justify-content:flex-end;margin-top:12px;">
          <button
            class="btn btn-success"
            @click=${() => host._acceptScene(msgIndex)}
          >
            <ha-icon icon="mdi:check" style="--mdc-icon-size:14px;"></ha-icon>
            ${host._t("scenes_card_accept_save_button", "Accept & Save")}
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
  const sortDir = host._sceneSortDir || "desc";
  const statusFilter = host._sceneStatusFilter || "all";

  const allScenes = host._scenes || [];
  const seloraCount = allScenes.filter((s) => s.source === "selora").length;
  const manualCount = allScenes.length - seloraCount;

  let filtered = [...allScenes];
  if (statusFilter === "selora") {
    filtered = filtered.filter((s) => s.source === "selora");
  } else if (statusFilter === "manual") {
    filtered = filtered.filter((s) => s.source !== "selora");
  }
  if (filterText) {
    filtered = filtered.filter((s) =>
      (s.name || "").toLowerCase().includes(filterText),
    );
  }

  const naturalDir = { recent: "desc", alpha: "asc", size: "desc" };
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
  if (sortDir !== naturalDir[sortBy]) {
    filtered.reverse();
  }

  return html`
    <div class="scroll-view">
      <div class="page-root">
        <div class="page-header">
          <h1 class="page-h1">
            ${host._t("scenes_section_title", "Your Scenes")}
          </h1>
          ${(host._scenes || []).length > 0
            ? html`<button
                class="filter-row-action"
                ?disabled=${host._llmNeedsSetup}
                title=${host._llmNeedsSetup
                  ? host._t(
                      "scenes_llm_needs_setup_tooltip",
                      "Configure an LLM provider first",
                    )
                  : ""}
                @click=${() => host._newSceneChat()}
              >
                <ha-icon
                  icon="mdi:plus"
                  style="--mdc-icon-size:13px;"
                ></ha-icon>
                ${host._t("scenes_new_scene_button", "New Scene")}
              </button>`
            : ""}
        </div>
        ${(host._scenes || []).length > 0
          ? html`
              <div class="filter-tabs-row" style="margin-top:12px;">
                <div class="filter-tabs" role="tablist">
                  <button
                    role="tab"
                    aria-selected=${statusFilter === "all"}
                    class="filter-tab ${statusFilter === "all" ? "active" : ""}"
                    @click=${() => {
                      host._sceneStatusFilter = "all";
                    }}
                  >
                    ${host._t("scenes_status_tab_all", "All")}
                  </button>
                  ${seloraCount > 0 && manualCount > 0
                    ? html`
                        <button
                          role="tab"
                          aria-selected=${statusFilter === "selora"}
                          class="filter-tab ${statusFilter === "selora"
                            ? "active"
                            : ""}"
                          @click=${() => {
                            host._sceneStatusFilter = "selora";
                          }}
                        >
                          <ha-icon
                            icon="mdi:creation"
                            style="--mdc-icon-size:14px;color:var(--selora-accent);display:block;"
                          ></ha-icon>
                          <span
                            >${host._t("scenes_status_tab_selora", "Selora AI")}
                            (${seloraCount})</span
                          >
                        </button>
                        <button
                          role="tab"
                          aria-selected=${statusFilter === "manual"}
                          class="filter-tab ${statusFilter === "manual"
                            ? "active"
                            : ""}"
                          @click=${() => {
                            host._sceneStatusFilter = "manual";
                          }}
                        >
                          ${host._t("scenes_status_tab_manual", "Manual")}
                          (${manualCount})
                        </button>
                      `
                    : ""}
                </div>
              </div>
              <div class="filter-row">
                <div class="filter-input-wrap" style="flex:1 1 260px;">
                  <ha-icon icon="mdi:magnify"></ha-icon>
                  <input
                    type="text"
                    placeholder=${host._t(
                      "scenes_filter_placeholder",
                      "Filter scenes…",
                    )}
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
                <div class="sort-group">
                  <select
                    class="sort-select"
                    .value=${host._sceneSortBy || "recent"}
                    @change=${(e) => {
                      host._sceneSortBy = e.target.value;
                    }}
                  >
                    <option value="recent">
                      ${host._t("scenes_sort_recent", "Recently updated")}
                    </option>
                    <option value="alpha">
                      ${host._t("scenes_sort_alpha", "Alphabetical")}
                    </option>
                    <option value="size">
                      ${host._t("scenes_sort_size", "Most entities")}
                    </option>
                  </select>
                  <button
                    class="sort-dir-toggle"
                    title=${sortDir === "desc"
                      ? "Sort descending (click for ascending)"
                      : "Sort ascending (click for descending)"}
                    @click=${() => {
                      host._sceneSortDir = sortDir === "desc" ? "asc" : "desc";
                    }}
                  >
                    <ha-icon
                      icon=${sortDir === "desc"
                        ? "mdi:sort-descending"
                        : "mdi:sort-ascending"}
                      style="--mdc-icon-size:18px;"
                    ></ha-icon>
                  </button>
                </div>
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
                  const loadingChat = !!host._loadingToChat?.[sceneId];
                  const updated = formatTimeAgo(s.updated_at);
                  const meta = `${entityCount} entit${entityCount === 1 ? "y" : "ies"}${updated ? ` · updated ${updated}` : ""}`;
                  const isSelora = s.source === "selora";
                  const recipeTitle = s.recipe_title || "";
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
                              ".burger-menu-wrapper, .burger-dropdown, .burger-item, .row-action-btn, .btn",
                            )
                          )
                            return;
                          host._expandedScenes = {
                            ...host._expandedScenes,
                            [sceneId]: !isExpanded,
                          };
                        }}
                      >
                        <div
                          style="display:flex;flex-direction:column;align-items:center;gap:4px;flex-shrink:0;"
                        >
                          <ha-icon
                            icon="mdi:palette"
                            style="--mdc-icon-size:18px;color:var(--selora-accent);"
                          ></ha-icon>
                          ${!isSelora && !recipeTitle && host.narrow
                            ? html`<span
                                style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;background:var(--secondary-background-color);color:var(--secondary-text-color);padding:1px 4px;border-radius:3px;"
                                >HA</span
                              >`
                            : ""}
                        </div>
                        <div class="auto-row-name">
                          <div class="auto-row-title-row">
                            <span class="auto-row-title">${s.name}</span>
                            ${recipeTitle
                              ? html`<span
                                  class="recipe-pill"
                                  title=${host._t(
                                    "automations_recipe_pill_tooltip",
                                    "Installed by a Selora recipe — manage it from the Recipes tab.",
                                  )}
                                >
                                  <ha-icon
                                    icon="mdi:book-open-variant"
                                  ></ha-icon>
                                  <span class="recipe-pill-name"
                                    >${recipeTitle}</span
                                  >
                                </span>`
                              : !isSelora && !host.narrow
                                ? html`<span
                                    style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;background:var(--secondary-background-color);color:var(--secondary-text-color);padding:2px 6px;border-radius:4px;flex-shrink:0;"
                                    >HA</span
                                  >`
                                : ""}
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
                        <div
                          style="display:flex;align-items:center;gap:8px;flex-shrink:0;"
                        >
                          <button
                            class="row-action-btn"
                            ?disabled=${!sceneEntityId}
                            @click=${(e) => {
                              e.stopPropagation();
                              const id = sceneEntityId
                                ? sceneEntityId.replace(/^scene\./, "")
                                : sceneId;
                              host._activateScene(id, s.name);
                            }}
                            title=${host._t(
                              "scenes_activate_button",
                              "Activate",
                            )}
                          >
                            <ha-icon
                              icon="mdi:play"
                              style="--mdc-icon-size:16px;"
                            ></ha-icon>
                          </button>
                          <div class="burger-menu-wrapper">
                            <button
                              class="burger-btn"
                              @click=${(e) => {
                                e.stopPropagation();
                                if (burgerOpen) {
                                  host._openSceneBurger = null;
                                  return;
                                }
                                host._openBurgerMenuStyle = burgerMenuAnchor(
                                  e.currentTarget,
                                );
                                host._openSceneBurger = sceneId;
                              }}
                              title=${host._t(
                                "scenes_more_actions_tooltip",
                                "More actions",
                              )}
                            >
                              <ha-icon
                                icon="mdi:dots-vertical"
                                style="--mdc-icon-size:16px;"
                              ></ha-icon>
                            </button>
                            ${burgerOpen
                              ? html`
                                  <div
                                    class="burger-dropdown"
                                    style=${host._openBurgerMenuStyle}
                                  >
                                    <button
                                      class="burger-item"
                                      ?disabled=${loadingChat}
                                      @click=${(e) => {
                                        e.stopPropagation();
                                        host._openSceneBurger = null;
                                        host._loadSceneToChat(sceneId);
                                      }}
                                    >
                                      <ha-icon
                                        icon="mdi:chat-processing-outline"
                                        style="--mdc-icon-size:14px;"
                                      ></ha-icon>
                                      ${loadingChat
                                        ? host._t(
                                            "scenes_loading_label",
                                            "Loading…",
                                          )
                                        : host._t(
                                            "scenes_refine_in_chat_button",
                                            "Refine in chat",
                                          )}
                                    </button>
                                    <button
                                      class="burger-item"
                                      @click=${(e) => {
                                        e.stopPropagation();
                                        host._openSceneBurger = null;
                                        if (sceneEntityId) {
                                          host.dispatchEvent(
                                            new CustomEvent("hass-more-info", {
                                              bubbles: true,
                                              composed: true,
                                              detail: {
                                                entityId: sceneEntityId,
                                              },
                                            }),
                                          );
                                        } else {
                                          window.history.pushState(
                                            null,
                                            "",
                                            "/config/scene/dashboard",
                                          );
                                          window.dispatchEvent(
                                            new Event("location-changed"),
                                          );
                                        }
                                      }}
                                    >
                                      <ha-icon
                                        icon="mdi:open-in-new"
                                        style="--mdc-icon-size:14px;"
                                      ></ha-icon>
                                      ${host._t(
                                        "scenes_open_in_ha_button",
                                        "Open in HA",
                                      )}
                                    </button>
                                    ${isSelora
                                      ? html`<button
                                          class="burger-item danger"
                                          ?disabled=${deleting}
                                          @click=${(e) => {
                                            e.stopPropagation();
                                            host._openSceneBurger = null;
                                            host._deleteSceneConfirmId =
                                              sceneId;
                                            host._deleteSceneConfirmName =
                                              s.name;
                                          }}
                                        >
                                          <ha-icon
                                            icon="mdi:trash-can-outline"
                                            style="--mdc-icon-size:14px;"
                                          ></ha-icon>
                                          ${deleting
                                            ? host._t(
                                                "scenes_deleting_label",
                                                "Deleting…",
                                              )
                                            : host._t(
                                                "scenes_delete_button",
                                                "Delete",
                                              )}
                                        </button>`
                                      : ""}
                                  </div>
                                `
                              : ""}
                          </div>
                        </div>
                      </div>
                      ${isExpanded
                        ? html`
                            <div class="auto-row-expand">
                              ${Object.keys(entities).length
                                ? _renderEntityList(
                                    host,
                                    entities,
                                    isSelora ? sceneId : null,
                                  )
                                : html`<div
                                    style="font-size:12px;opacity:0.6;padding:6px 0;"
                                  >
                                    ${host._t(
                                      "scenes_no_entity_details",
                                      "No entity details available — open the scene in Home Assistant to inspect it.",
                                    )}
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
                                ${yamlOpen
                                  ? host._t("scenes_hide_yaml", "Hide YAML")
                                  : host._t("scenes_view_yaml", "View YAML")}
                              </div>
                              ${yamlOpen
                                ? html`
                                    <ha-code-editor
                                      mode="yaml"
                                      .value=${isSelora &&
                                      host._sceneIsDirty(sceneId)
                                        ? host._sceneEditYaml(sceneId, s.name)
                                        : s.yaml ||
                                          host._t(
                                            "scenes_yaml_unavailable_comment",
                                            "# YAML not available — open the scene in Home Assistant to view it.",
                                          )}
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
                ${host._t(
                  "scenes_empty_state",
                  "No scenes found. Ask Selora to create one.",
                )}
              </p>
              <button
                class="btn btn-accent"
                ?disabled=${host._llmNeedsSetup}
                title=${host._llmNeedsSetup
                  ? host._t(
                      "scenes_llm_needs_setup_tooltip",
                      "Configure an LLM provider first",
                    )
                  : ""}
                @click=${() => host._newSceneChat()}
              >
                <ha-icon
                  icon="mdi:plus"
                  style="--mdc-icon-size:14px;"
                ></ha-icon>
                ${host._t("scenes_new_scene_button", "New Scene")}
              </button>
            </div>`}
      </div>
      ${renderDeleteSceneModal(host)}
    </div>
  `;
}

function renderDeleteSceneModal(host) {
  if (!host._deleteSceneConfirmId) return "";
  const name =
    host._deleteSceneConfirmName ||
    host._t("scenes_delete_modal_fallback_name", "this scene");
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
          ${host._t("scenes_delete_modal_title", "Delete Scene")}
        </div>
        <div style="font-size:13px;opacity:0.7;margin-bottom:20px;">
          ${host._t("scenes_delete_modal_prefix", "Delete")}
          <strong>${name}</strong>${host._t(
            "scenes_delete_modal_suffix",
            "? This removes the scene from Home Assistant and cannot be undone.",
          )}
        </div>
        <div style="display:flex;gap:10px;justify-content:center;">
          <button
            class="btn btn-outline"
            @click=${() => {
              host._deleteSceneConfirmId = null;
              host._deleteSceneConfirmName = null;
            }}
          >
            ${host._t("scenes_delete_modal_cancel_button", "Cancel")}
          </button>
          <button
            class="btn"
            style="background:#ef4444;color:#fff;border-color:#ef4444;"
            @click=${() => host._confirmDeleteScene()}
          >
            ${host._t("scenes_delete_modal_confirm_button", "Delete")}
          </button>
        </div>
      </div>
    </div>
  `;
}
