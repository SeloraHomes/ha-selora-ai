import { html } from "lit";
import { describeFlowItem } from "../shared/flow-description.js";
import { formatTimeAgo } from "../shared/date-utils.js";

// ---------------------------------------------------------------------------
// Automation flowchart renderer
// ---------------------------------------------------------------------------

export function renderAutomationFlowchart(host, auto) {
  if (!auto) return html``;
  const triggers = (() => {
    const t = auto.triggers ?? auto.trigger ?? [];
    return Array.isArray(t) ? t : [t];
  })();
  const conditions = (() => {
    const c = auto.conditions ?? auto.condition ?? [];
    return Array.isArray(c) ? c : [c];
  })().filter(Boolean);
  const actions = (() => {
    const a = auto.actions ?? auto.action ?? [];
    return Array.isArray(a) ? a : [a];
  })();
  if (!triggers.length && !actions.length) return html``;
  return html`
    <div class="flow-chart">
      <div class="flow-section">
        <div class="flow-label">Trigger</div>
        ${triggers.map(
          (t) =>
            html`<div class="flow-node trigger-node">
              ${describeFlowItem(host.hass, t)}
            </div>`,
        )}
      </div>
      ${conditions.length
        ? html`
            <div class="flow-arrow">↓</div>
            <div class="flow-section">
              <div class="flow-label">Condition</div>
              ${conditions.map(
                (c) =>
                  html`<div class="flow-node condition-node">
                    ${describeFlowItem(host.hass, c)}
                  </div>`,
              )}
            </div>
          `
        : ""}
      <div class="flow-arrow">↓</div>
      <div class="flow-section">
        <div class="flow-label">Actions</div>
        ${actions.map(
          (a, i) => html`
            ${i > 0 ? html`<div class="flow-arrow-sm">↓</div>` : ""}
            <div class="flow-node action-node">
              ${describeFlowItem(host.hass, a)}
            </div>
          `,
        )}
      </div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Proposal card (chat automation proposals)
// ---------------------------------------------------------------------------

export function renderProposalCard(host, msg, msgIndex) {
  const status = msg.automation_status;
  const automation = msg.automation;
  const yaml = msg.automation_yaml || "";
  const risk = msg.risk_assessment || automation?.risk_assessment || null;
  const scrutinyTags = risk?.scrutiny_tags || [];

  if (status === "saved") {
    return html`
      <div class="proposal-card" style="margin-top:12px;">
        <div class="proposal-header">
          <ha-icon icon="mdi:check-circle"></ha-icon>
          Automation Created
        </div>
        <div class="proposal-body">
          <div class="proposal-name">${automation.alias}</div>
          <div class="proposal-status saved">
            <ha-icon icon="mdi:check"></ha-icon> Saved and enabled
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
          Automation Declined
        </div>
        <div class="proposal-body">
          <div class="proposal-name">${automation.alias}</div>
          <div class="proposal-status declined">
            Dismissed. You can refine it by replying below.
          </div>
        </div>
      </div>
    `;
  }

  if (status === "refining") {
    return html`
      <div class="proposal-card" style="margin-top:12px; opacity:0.75;">
        <div class="proposal-header" style="color:var(--selora-accent);">
          <ha-icon icon="mdi:pencil-circle-outline"></ha-icon>
          Being Refined
        </div>
        <div class="proposal-body">
          <div class="proposal-name">${automation.alias}</div>
          <div
            class="proposal-status"
            style="background:var(--selora-zinc-800); color:var(--selora-accent); border:1px solid var(--selora-zinc-700); border-radius:8px; padding:8px 12px;"
          >
            <ha-icon icon="mdi:arrow-down"></ha-icon>
            Refinement requested — see the updated proposal below.
          </div>
        </div>
      </div>
    `;
  }

  // Pending proposal — full review UI
  const yamlOpen = host._yamlOpen && host._yamlOpen[msgIndex];
  const yamlKey = `proposal_${msgIndex}`;
  const hasEdits =
    host._editedYaml[yamlKey] !== undefined &&
    host._editedYaml[yamlKey] !== yaml;
  return html`
    <div class="proposal-card">
      <div class="proposal-header">
        <ha-icon icon="mdi:lightning-bolt"></ha-icon>
        Automation Proposal
      </div>
      <div class="proposal-body">
        <div class="proposal-name">${automation.alias}</div>

        ${msg.description
          ? html`
              <div class="proposal-description-label">
                What this automation does
              </div>
              <div class="proposal-description">${msg.description}</div>
            `
          : ""}
        ${risk?.level === "elevated"
          ? html`
              <div
                class="proposal-status"
                style="background:rgba(255,152,0,0.12); color:var(--warning-color,#ff9800); border:1px solid rgba(255,152,0,0.25);"
              >
                <ha-icon icon="mdi:alert-outline"></ha-icon>
                <div>
                  <strong>Elevated risk review recommended.</strong>
                  <div style="margin-top:4px;">${risk.summary}</div>
                  ${risk.reasons?.length
                    ? html`<div style="margin-top:6px; font-size:12px;">
                        ${risk.reasons.join(" ")}
                      </div>`
                    : ""}
                </div>
              </div>
            `
          : ""}
        ${renderAutomationFlowchart(host, automation)}

        <div class="yaml-toggle" @click=${() => toggleYaml(host, msgIndex)}>
          <ha-icon
            icon="mdi:code-braces"
            style="--mdc-icon-size:14px;"
          ></ha-icon>
          ${yamlOpen ? "Hide YAML" : "Edit YAML"}
        </div>
        ${yamlOpen ? host._renderYamlEditor(yamlKey, yaml) : ""}

        <div class="proposal-verify">
          ${hasEdits
            ? "Your YAML edits will be used when you accept."
            : "Does the flow above match what you intended?"}
        </div>

        <div class="proposal-actions">
          <button
            class="btn btn-success"
            @click=${() =>
              host._acceptAutomationWithEdits(msgIndex, automation, yamlKey)}
          >
            <ha-icon icon="mdi:check" style="--mdc-icon-size:14px;"></ha-icon>
            Accept &amp; Save
          </button>
          <button
            class="btn btn-outline"
            @click=${() =>
              host._refineAutomation(msgIndex, automation, msg.description)}
          >
            <ha-icon icon="mdi:pencil" style="--mdc-icon-size:14px;"></ha-icon>
            Refine
          </button>
          <button
            class="btn btn-danger"
            @click=${() => host._declineAutomation(msgIndex)}
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
// Toggle YAML visibility
// ---------------------------------------------------------------------------

export function toggleYaml(host, msgIndex) {
  host._yamlOpen = {
    ...(host._yamlOpen || {}),
    [msgIndex]: !(host._yamlOpen || {})[msgIndex],
  };
  host.requestUpdate();
}

// ---------------------------------------------------------------------------
// Masonry column layout utility
// ---------------------------------------------------------------------------

export function masonryColumns(cards, cols = 3, firstColFooter = null) {
  const w = window.innerWidth;
  const numCols = w <= 600 ? 1 : w <= 1000 ? 2 : cols;
  const buckets = Array.from({ length: numCols }, () => []);
  cards.forEach((c, i) => buckets[i % numCols].push(c));
  return buckets.map(
    (col, i) =>
      html`<div class="masonry-col">
        ${col}${i === 0 && firstColFooter ? firstColFooter : ""}
      </div>`,
  );
}

// ---------------------------------------------------------------------------
// Automations tab — main render
// ---------------------------------------------------------------------------

export function renderAutomations(host) {
  const filterText = (host._automationFilter || "").toLowerCase();
  const statusFilter = host._statusFilter || "all";
  const sortBy = host._sortBy || "recent";

  let filteredAutomations = [...host._automations];

  // Status filter — "all" hides deleted unless explicitly filtered
  if (statusFilter === "all") {
    filteredAutomations = filteredAutomations.filter((a) => !a.is_deleted);
  } else if (statusFilter === "enabled") {
    filteredAutomations = filteredAutomations.filter(
      (a) => !a.is_deleted && host._automationIsEnabled(a),
    );
  } else if (statusFilter === "disabled") {
    filteredAutomations = filteredAutomations.filter(
      (a) => !a.is_deleted && !host._automationIsEnabled(a),
    );
  } else if (statusFilter === "deleted") {
    filteredAutomations = filteredAutomations.filter((a) => a.is_deleted);
  }

  // Text filter
  if (filterText) {
    filteredAutomations = filteredAutomations.filter((a) =>
      (a.alias || "").toLowerCase().includes(filterText),
    );
  }

  // Sort
  if (sortBy === "recent") {
    filteredAutomations.sort((a, b) => {
      const aTime = a.last_triggered ? new Date(a.last_triggered).getTime() : 0;
      const bTime = b.last_triggered ? new Date(b.last_triggered).getTime() : 0;
      return bTime - aTime;
    });
  } else if (sortBy === "alpha") {
    filteredAutomations.sort((a, b) =>
      (a.alias || "").localeCompare(b.alias || ""),
    );
  } else if (sortBy === "enabled_first") {
    filteredAutomations.sort((a, b) => {
      const aOn = host._automationIsEnabled(a) ? 0 : 1;
      const bOn = host._automationIsEnabled(b) ? 0 : 1;
      return aOn - bOn;
    });
  }

  // Summary counts
  const enabledCount = host._automations.filter((a) =>
    host._automationIsEnabled(a),
  ).length;
  const disabledCount = host._automations.filter(
    (a) => !host._automationIsEnabled(a) && !a.is_deleted,
  ).length;
  const deletedCount = host._automations.filter((a) => a.is_deleted).length;
  const perPage = host._autosPerPage || 10;
  const totalAutoPages = Math.max(
    1,
    Math.ceil(filteredAutomations.length / perPage),
  );
  const safeAutoPage = Math.min(host._automationsPage, totalAutoPages);
  const pagedAutomations = filteredAutomations.slice(
    (safeAutoPage - 1) * perPage,
    safeAutoPage * perPage,
  );
  const selectableAutomations = filteredAutomations.filter(
    (a) => !a._draft && a.automation_id,
  );
  const selectableIds = selectableAutomations.map((a) => a.automation_id);
  const selectedIds = host._getSelectedAutomationIds();
  const selectedVisibleCount = selectableIds.filter(
    (id) => host._selectedAutomationIds[id],
  ).length;
  const allVisibleSelected =
    selectableIds.length > 0 && selectedVisibleCount === selectableIds.length;
  const partiallyVisibleSelected =
    selectedVisibleCount > 0 && !allVisibleSelected;
  const hiddenSelectedCount = Math.max(
    0,
    selectedIds.length - selectedVisibleCount,
  );
  const bulkDisabled = selectedIds.length === 0 || host._bulkActionInProgress;

  return html`
    <div class="scroll-view" @click=${() => host._closeBurgerMenus()}>
      <div class="sub-tabs">
        <button
          class="sub-tab ${host._automationsSubTab === "my_automations"
            ? "active"
            : ""}"
          @click=${() => {
            host._automationsSubTab = "my_automations";
          }}
        >
          <span class="sub-tab-text">My Automations</span>
        </button>
        <button
          class="sub-tab ${host._automationsSubTab === "suggestions"
            ? "active"
            : ""}"
          @click=${() => {
            host._automationsSubTab = "suggestions";
          }}
        >
          <span class="sub-tab-text">Suggestions</span>
          ${(() => {
            const qualCount =
              (host._proactiveSuggestions || []).filter(
                (s) => (s.confidence || 0) >= 0.8,
              ).length + (host._suggestions || []).length;
            return qualCount > 0
              ? html`<span class="badge">${qualCount} new</span>`
              : "";
          })()}
        </button>
      </div>
      ${host._automationsSubTab === "my_automations"
        ? html`
            ${host._automations.length > 0
              ? html`
                  <div class="filter-row">
                    <div class="filter-input-wrap" style="flex:0 1 260px;">
                      <ha-icon icon="mdi:magnify"></ha-icon>
                      <input
                        type="text"
                        placeholder="Filter automations…"
                        .value=${host._automationFilter}
                        @input=${(e) => {
                          host._automationFilter = e.target.value;
                          host._automationsPage = 1;
                        }}
                      />
                      ${host._automationFilter
                        ? html`<ha-icon
                            icon="mdi:close-circle"
                            style="--mdc-icon-size:16px;cursor:pointer;opacity:0.5;flex-shrink:0;"
                            @click=${() => {
                              host._automationFilter = "";
                              host._automationsPage = 1;
                            }}
                          ></ha-icon>`
                        : ""}
                    </div>
                    <div class="status-pills">
                      ${["all", "enabled", "disabled", "deleted"].map(
                        (s) => html`
                          <button
                            class="status-pill ${host._statusFilter === s
                              ? "active"
                              : ""}"
                            @click=${() => {
                              host._statusFilter = s;
                              host._automationsPage = 1;
                            }}
                          >
                            ${s.charAt(0).toUpperCase() + s.slice(1)}
                          </button>
                        `,
                      )}
                    </div>
                    <select
                      class="sort-select"
                      .value=${host._sortBy}
                      @change=${(e) => {
                        host._sortBy = e.target.value;
                      }}
                    >
                      <option value="recent">Recent activity</option>
                      <option value="alpha">Alphabetical</option>
                      <option value="enabled_first">Enabled first</option>
                    </select>
                    <div
                      style="margin-left:auto;display:flex;align-items:center;gap:8px;"
                    >
                      <button
                        class="btn btn-primary"
                        style="white-space:nowrap;"
                        @click=${() => {
                          host._newAutoName = "";
                          host._showNewAutoDialog = true;
                        }}
                      >
                        <ha-icon
                          icon="mdi:plus"
                          style="--mdc-icon-size:13px;"
                        ></ha-icon>
                        New Automation
                      </button>
                    </div>
                  </div>
                  <div
                    class="automations-summary"
                    style="display:flex;align-items:center;justify-content:space-between;"
                  >
                    <span>
                      ${filteredAutomations.length}
                      automation${filteredAutomations.length !== 1 ? "s" : ""}
                      (${enabledCount} enabled, ${disabledCount}
                      disabled${deletedCount > 0
                        ? `, ${deletedCount} deleted`
                        : ""})
                    </span>
                    ${host._bulkEditMode
                      ? html`
                          <div
                            style="display:flex;align-items:center;gap:10px;"
                          >
                            <label class="bulk-select-all">
                              <input
                                type="checkbox"
                                ?checked=${allVisibleSelected}
                                .indeterminate=${partiallyVisibleSelected}
                                ?disabled=${selectableIds.length === 0 ||
                                host._bulkActionInProgress}
                                @change=${(e) =>
                                  host._toggleSelectAllFiltered(
                                    filteredAutomations,
                                    e.target.checked,
                                  )}
                              />
                              <span>Select all</span>
                            </label>
                            <button
                              class="btn btn-outline"
                              @click=${() => {
                                host._bulkEditMode = false;
                                host._clearAutomationSelection();
                              }}
                            >
                              Done
                            </button>
                          </div>
                        `
                      : html`
                          <button
                            class="btn btn-outline"
                            @click=${() => {
                              host._bulkEditMode = true;
                            }}
                          >
                            <ha-icon
                              icon="mdi:checkbox-multiple-outline"
                              style="--mdc-icon-size:14px;"
                            ></ha-icon>
                            Bulk edit
                          </button>
                        `}
                  </div>
                  ${host._bulkEditMode && selectedIds.length > 0
                    ? html`
                        <div class="bulk-actions-row">
                          <div class="left">
                            ${selectedIds.length}
                            selected${hiddenSelectedCount > 0
                              ? html` <span
                                  style="opacity:0.65;font-weight:500;"
                                  >(${hiddenSelectedCount} hidden by
                                  filter)</span
                                >`
                              : ""}
                            ${host._bulkActionInProgress
                              ? html`<span
                                  style="opacity:0.75;font-weight:500;"
                                >
                                  · ${host._bulkActionLabel}</span
                                >`
                              : ""}
                          </div>
                          <div class="actions">
                            <button
                              class="btn btn-outline"
                              ?disabled=${bulkDisabled}
                              @click=${() => host._bulkToggleSelected(true)}
                            >
                              ${host._bulkActionInProgress
                                ? "Working…"
                                : "Enable all"}
                            </button>
                            <button
                              class="btn btn-outline"
                              ?disabled=${bulkDisabled}
                              @click=${() => host._bulkToggleSelected(false)}
                            >
                              ${host._bulkActionInProgress
                                ? "Working…"
                                : "Disable all"}
                            </button>
                            <button
                              class="btn btn-outline btn-danger"
                              ?disabled=${bulkDisabled}
                              @click=${() => host._bulkSoftDeleteSelected()}
                            >
                              ${host._bulkActionInProgress
                                ? "Working…"
                                : "Soft-delete selected"}
                            </button>
                            <button
                              class="btn btn-ghost"
                              ?disabled=${host._bulkActionInProgress}
                              @click=${() => host._clearAutomationSelection()}
                            >
                              Clear
                            </button>
                          </div>
                        </div>
                      `
                    : ""}
                  <div class="automations-grid">
                    ${masonryColumns(
                      pagedAutomations.map((a) => {
                        const isDraft = !!a._draft;
                        const expanded =
                          !!host._expandedAutomations[a.entity_id];
                        const isOn = host._automationIsEnabled(a);
                        const automationId = a.automation_id || "";
                        const hasAutomationId = !!automationId;
                        const canToggle =
                          hasAutomationId && !host._bulkActionInProgress;
                        const versionCount = a.version_count || null;
                        const deleting = host._deletingAutomation[automationId];
                        const loadingChat = host._loadingToChat[automationId];
                        const burgerOpen =
                          host._openBurgerMenu === automationId;
                        const cardExpanded = !!host._cardActiveTab[a.entity_id];
                        return html`
                          <div
                            class="card${cardExpanded ? " expanded" : ""}"
                            style="padding:16px 18px;cursor:pointer;${!isDraft &&
                            !isOn
                              ? "opacity:0.5;background:transparent;border-color:var(--selora-zinc-800);"
                              : ""}"
                            @click=${(e) => {
                              if (
                                e.target.closest(
                                  ".toggle-switch, .burger-menu-wrapper, .burger-dropdown, .burger-item, .card-select, .rename-input, .rename-save-btn, .btn, .card-tab, .card-chevron",
                                )
                              )
                                return;
                              const current = host._cardActiveTab[a.entity_id];
                              if (current) {
                                host._cardActiveTab = {
                                  ...host._cardActiveTab,
                                  [a.entity_id]: null,
                                };
                              } else {
                                const defaultTab =
                                  a.trigger?.length || a.action?.length
                                    ? "flow"
                                    : a.yaml_text
                                      ? "yaml"
                                      : hasAutomationId
                                        ? "history"
                                        : null;
                                host._cardActiveTab = {
                                  ...host._cardActiveTab,
                                  [a.entity_id]: defaultTab,
                                };
                              }
                            }}
                          >
                            <!-- Row 1: Title + Toggle -->
                            <div class="card-header" style="margin-bottom:0;">
                              ${host._bulkEditMode && hasAutomationId
                                ? html`
                                    <label class="card-select">
                                      <input
                                        type="checkbox"
                                        .checked=${!!host
                                          ._selectedAutomationIds[automationId]}
                                        ?disabled=${host._bulkActionInProgress}
                                        @click=${(e) => e.stopPropagation()}
                                        @change=${(e) =>
                                          host._toggleAutomationSelection(
                                            automationId,
                                            e,
                                          )}
                                      />
                                    </label>
                                  `
                                : ""}
                              ${host._editingAlias === automationId
                                ? html`
                                    <input
                                      class="rename-input"
                                      data-id="${automationId}"
                                      .value=${host._editingAliasValue}
                                      @input=${(e) => {
                                        host._editingAliasValue =
                                          e.target.value;
                                      }}
                                      @keydown=${(e) => {
                                        if (e.key === "Enter")
                                          host._saveRenameAutomation(
                                            automationId,
                                          );
                                        if (e.key === "Escape")
                                          host._cancelRenameAutomation();
                                      }}
                                    />
                                    <button
                                      class="rename-save-btn"
                                      title="Save"
                                      @click=${() =>
                                        host._saveRenameAutomation(
                                          automationId,
                                        )}
                                    >
                                      <ha-icon
                                        icon="mdi:check"
                                        style="--mdc-icon-size:16px;"
                                      ></ha-icon>
                                    </button>
                                  `
                                : html`
                                    <h3 style="flex:1;font-size:14px;margin:0;">
                                      ${a.alias}
                                    </h3>
                                  `}
                              <label
                                class="toggle-switch"
                                title="${canToggle
                                  ? isOn
                                    ? "Enabled"
                                    : "Disabled"
                                  : "Unavailable"}"
                                style="flex-shrink:0;${canToggle
                                  ? ""
                                  : "opacity:0.45;cursor:not-allowed;"}"
                                @click=${() => {
                                  if (!canToggle) {
                                    host._showToast(
                                      "Unable to toggle: automation id was not resolved. Reload and try again.",
                                      "error",
                                    );
                                  }
                                }}
                              >
                                <input
                                  type="checkbox"
                                  .checked=${isOn}
                                  ?disabled=${!canToggle}
                                  @click=${(e) => e.stopPropagation()}
                                  @change=${(e) => {
                                    if (!canToggle) return;
                                    host._toggleAutomation(
                                      a.entity_id,
                                      automationId,
                                      e.target.checked,
                                    );
                                  }}
                                />
                                <div class="toggle-track ${isOn ? "on" : ""}">
                                  <div class="toggle-thumb"></div>
                                </div>
                              </label>
                            </div>

                            <!-- Row 2: Description / Actions swap -->
                            <div
                              class="card-row2"
                              style="margin-top:12px;flex:1;position:relative;"
                            >
                              <div
                                class="card-desc"
                                style="font-size:12px;color:var(--secondary-text-color);line-height:1.5;display:flex;flex-direction:column;height:100%;"
                              >
                                ${a.description
                                  ? html`<div
                                      style="display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;"
                                    >
                                      ${a.description.replace(
                                        /^\[Selora AI\]\s*/,
                                        "",
                                      )}
                                    </div>`
                                  : ""}
                                <div
                                  style="font-size:11px;opacity:0.5;margin-top:auto;padding-top:6px;"
                                >
                                  ${(() => {
                                    const ago = formatTimeAgo(a.last_triggered);
                                    if (ago) return `Last run: ${ago}`;
                                    if (!host._automationIsEnabled(a))
                                      return "Disabled";
                                    return "Never ran";
                                  })()}
                                </div>
                              </div>
                              <div
                                class="card-actions-row ${cardExpanded
                                  ? "visible"
                                  : ""}"
                                style="display:flex;align-items:center;gap:8px;"
                              >
                                ${isDraft
                                  ? html` <button
                                        class="btn btn-primary"
                                        style="font-size:12px;padding:6px 12px;"
                                        @click=${() => {
                                          host._activeSessionId =
                                            a._linked_session;
                                          host._activeTab = "chat";
                                          host._openSession(a._linked_session);
                                        }}
                                      >
                                        <ha-icon
                                          icon="mdi:chat-processing-outline"
                                          style="--mdc-icon-size:14px;"
                                        ></ha-icon>
                                        Define in Chat
                                      </button>
                                      <button
                                        class="btn btn-outline"
                                        style="font-size:12px;padding:6px 12px;"
                                        @click=${() =>
                                          host._dismissDraft(a._draft_id)}
                                      >
                                        <ha-icon
                                          icon="mdi:close"
                                          style="--mdc-icon-size:14px;"
                                        ></ha-icon>
                                        Dismiss
                                      </button>`
                                  : html` <button
                                      class="btn btn-outline refine-btn"
                                      style="font-size:12px;padding:6px 12px;"
                                      ?disabled=${!hasAutomationId ||
                                      loadingChat ||
                                      host._bulkActionInProgress}
                                      @click=${() =>
                                        host._loadAutomationToChat(
                                          automationId,
                                        )}
                                    >
                                      <ha-icon
                                        icon="mdi:chat-processing-outline"
                                        style="--mdc-icon-size:14px;"
                                      ></ha-icon>
                                      ${loadingChat
                                        ? "Loading…"
                                        : "Refine in chat"}
                                    </button>`}
                                <div
                                  style="margin-left:auto;display:flex;align-items:center;gap:6px;"
                                >
                                  <ha-icon
                                    icon="mdi:chevron-down"
                                    class="card-chevron ${cardExpanded
                                      ? "open"
                                      : ""}"
                                    title="Expand details"
                                    @click=${(e) => {
                                      e.stopPropagation();
                                      const current =
                                        host._cardActiveTab[a.entity_id];
                                      if (current) {
                                        host._cardActiveTab = {
                                          ...host._cardActiveTab,
                                          [a.entity_id]: null,
                                        };
                                      } else {
                                        const defaultTab =
                                          a.trigger?.length || a.action?.length
                                            ? "flow"
                                            : a.yaml_text
                                              ? "yaml"
                                              : hasAutomationId
                                                ? "history"
                                                : null;
                                        host._cardActiveTab = {
                                          ...host._cardActiveTab,
                                          [a.entity_id]: defaultTab,
                                        };
                                      }
                                    }}
                                  ></ha-icon>
                                  ${hasAutomationId
                                    ? html`
                                        <div class="burger-menu-wrapper">
                                          <button
                                            class="burger-btn"
                                            @click=${(e) =>
                                              host._toggleBurgerMenu(
                                                automationId,
                                                e,
                                              )}
                                            ?disabled=${host._bulkActionInProgress}
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
                                                      host._startRenameAutomation(
                                                        automationId,
                                                        a.alias,
                                                      );
                                                    }}
                                                  >
                                                    <ha-icon
                                                      icon="mdi:pencil-outline"
                                                      style="--mdc-icon-size:14px;"
                                                    ></ha-icon>
                                                    Rename
                                                  </button>
                                                  <button
                                                    class="burger-item"
                                                    @click=${(e) => {
                                                      e.stopPropagation();
                                                      host._openBurgerMenu =
                                                        null;
                                                      window.history.pushState(
                                                        null,
                                                        "",
                                                        `/config/automation/edit/${automationId}`,
                                                      );
                                                      window.dispatchEvent(
                                                        new Event(
                                                          "location-changed",
                                                        ),
                                                      );
                                                    }}
                                                  >
                                                    <ha-icon
                                                      icon="mdi:open-in-new"
                                                      style="--mdc-icon-size:14px;"
                                                    ></ha-icon>
                                                    Edit in HA
                                                  </button>
                                                  <button
                                                    class="burger-item danger"
                                                    ?disabled=${deleting}
                                                    @click=${(e) => {
                                                      e.stopPropagation();
                                                      host._openBurgerMenu =
                                                        null;
                                                      host._softDeleteAutomation(
                                                        automationId,
                                                      );
                                                    }}
                                                  >
                                                    <ha-icon
                                                      icon="mdi:trash-can-outline"
                                                      style="--mdc-icon-size:14px;"
                                                    ></ha-icon>
                                                    ${deleting
                                                      ? "Deleting…"
                                                      : "Delete"}
                                                  </button>
                                                </div>
                                              `
                                            : ""}
                                        </div>
                                      `
                                    : ""}
                                </div>
                              </div>
                            </div>
                            <!-- Expand section: View tabs + content -->
                            ${cardExpanded
                              ? html`<div
                                    class="card-tabs"
                                    style="margin-top:12px;"
                                  >
                                    <span class="label">View:</span>
                                    ${a.trigger?.length || a.action?.length
                                      ? html`
                                          <button
                                            class="card-tab ${host
                                              ._cardActiveTab[a.entity_id] ===
                                            "flow"
                                              ? "active"
                                              : ""}"
                                            @click=${() => {
                                              host._cardActiveTab = {
                                                ...host._cardActiveTab,
                                                [a.entity_id]:
                                                  host._cardActiveTab[
                                                    a.entity_id
                                                  ] === "flow"
                                                    ? null
                                                    : "flow",
                                              };
                                            }}
                                          >
                                            <ha-icon
                                              icon="mdi:sitemap-outline"
                                              style="--mdc-icon-size:14px;"
                                            ></ha-icon>
                                            Flow
                                          </button>
                                          <span class="card-tab-sep">|</span>
                                        `
                                      : ""}
                                    ${a.yaml_text
                                      ? html`
                                          <button
                                            class="card-tab ${host
                                              ._cardActiveTab[a.entity_id] ===
                                            "yaml"
                                              ? "active"
                                              : ""}"
                                            @click=${() => {
                                              host._cardActiveTab = {
                                                ...host._cardActiveTab,
                                                [a.entity_id]:
                                                  host._cardActiveTab[
                                                    a.entity_id
                                                  ] === "yaml"
                                                    ? null
                                                    : "yaml",
                                              };
                                            }}
                                          >
                                            <ha-icon
                                              icon="mdi:code-braces"
                                              style="--mdc-icon-size:14px;"
                                            ></ha-icon>
                                            YAML
                                          </button>
                                          <span class="card-tab-sep">|</span>
                                        `
                                      : ""}
                                    ${hasAutomationId
                                      ? html`
                                          <button
                                            class="card-tab ${host
                                              ._cardActiveTab[a.entity_id] ===
                                            "history"
                                              ? "active"
                                              : ""}"
                                            @click=${() => {
                                              const isActive =
                                                host._cardActiveTab[
                                                  a.entity_id
                                                ] === "history";
                                              host._cardActiveTab = {
                                                ...host._cardActiveTab,
                                                [a.entity_id]: isActive
                                                  ? null
                                                  : "history",
                                              };
                                              if (
                                                !isActive &&
                                                !host._versions[automationId]
                                              ) {
                                                host._versionHistoryOpen = {
                                                  ...host._versionHistoryOpen,
                                                  [automationId]: true,
                                                };
                                                host._loadVersionHistory(
                                                  automationId,
                                                );
                                              }
                                            }}
                                          >
                                            History
                                          </button>
                                        `
                                      : ""}
                                  </div>
                                  ${host._cardActiveTab[a.entity_id] ===
                                    "flow" &&
                                  (a.trigger?.length || a.action?.length)
                                    ? renderAutomationFlowchart(host, a)
                                    : ""}
                                  ${host._cardActiveTab[a.entity_id] ===
                                    "yaml" && a.yaml_text
                                    ? host._renderYamlEditor(
                                        `yaml_${a.entity_id}`,
                                        a.yaml_text,
                                        (key) =>
                                          host._saveActiveAutomationYaml(
                                            a.automation_id,
                                            key,
                                          ),
                                      )
                                    : ""}
                                  ${host._cardActiveTab[a.entity_id] ===
                                    "history" && hasAutomationId
                                    ? host._renderVersionHistoryDrawer(a)
                                    : ""}`
                              : ""}
                          </div>
                        `;
                      }),
                      3,
                    )}
                  </div>
                  ${totalAutoPages > 1
                    ? html`
                        <div class="pagination">
                          <button
                            class="btn btn-outline"
                            ?disabled=${safeAutoPage <= 1}
                            @click=${() => {
                              host._automationsPage = safeAutoPage - 1;
                            }}
                          >
                            ‹ Prev
                          </button>
                          <span class="page-info"
                            >Page ${safeAutoPage} of ${totalAutoPages} ·
                            ${filteredAutomations.length} automations</span
                          >
                          <label class="per-page-label"
                            >Per page:
                            <select
                              class="per-page-select"
                              .value=${String(host._autosPerPage)}
                              @change=${(e) => {
                                host._autosPerPage = Number(e.target.value);
                                host._automationsPage = 1;
                              }}
                            >
                              <option value="10">10</option>
                              <option value="20">20</option>
                              <option value="50">50</option>
                            </select>
                          </label>
                          <button
                            class="btn btn-outline"
                            ?disabled=${safeAutoPage >= totalAutoPages}
                            @click=${() => {
                              host._automationsPage = safeAutoPage + 1;
                            }}
                          >
                            Next ›
                          </button>
                        </div>
                      `
                    : ""}
                  ${filteredAutomations.length === 0 &&
                  host._automations.length > 0
                    ? html`<div
                        style="text-align:center;opacity:0.45;padding:24px 0;"
                      >
                        No automations match "${host._automationFilter}"
                      </div>`
                    : ""}
                `
              : html`<div style="text-align:center;padding:32px 0;">
                  <ha-icon
                    icon="mdi:robot-vacuum-variant"
                    style="--mdc-icon-size:40px;display:block;margin-bottom:8px;opacity:0.35;"
                  ></ha-icon>
                  <p style="opacity:0.45;margin:0 0 12px;">
                    No automations yet.
                  </p>
                  <button
                    class="btn btn-primary"
                    @click=${() => {
                      host._newAutoName = "";
                      host._showNewAutoDialog = true;
                    }}
                  >
                    <ha-icon
                      icon="mdi:plus"
                      style="--mdc-icon-size:14px;"
                    ></ha-icon>
                    New Automation
                  </button>
                </div>`}
          `
        : ""}
      ${host._automationsSubTab === "suggestions"
        ? html`
            ${host._renderUnifiedSuggestions()}
            ${host._suggestions.length > 0
              ? html`
                  <div class="automations-grid">
                    ${masonryColumns(
                      host._suggestions.map((item) => {
                        const auto = item.automation || item.automation_data;
                        const risk =
                          item.risk_assessment || auto?.risk_assessment || null;
                        const cardKey = `sug_${auto.alias}`;
                        const origYaml = item.automation_yaml || "";
                        const editedYaml = host._editedYaml[cardKey];
                        const displayYaml =
                          editedYaml !== undefined ? editedYaml : origYaml;
                        const hasFlow =
                          auto &&
                          (auto.trigger?.length ||
                            auto.triggers?.length ||
                            auto.action?.length ||
                            auto.actions?.length);
                        const defaultTab = hasFlow ? "flow" : "yaml";
                        const activeTab =
                          host._cardActiveTab[cardKey] !== undefined
                            ? host._cardActiveTab[cardKey]
                            : defaultTab;
                        return html`
                          <div
                            class="card"
                            style="padding:24px;text-align:center;"
                          >
                            <div
                              class="card-header"
                              style="margin-bottom:6px;justify-content:center;"
                            >
                              <h3 style="font-size:14px;margin:0;">
                                ${auto.alias}
                              </h3>
                            </div>
                            ${auto.description
                              ? html`<div
                                  style="font-size:11px;opacity:0.55;margin-bottom:6px;"
                                >
                                  ${auto.description}
                                </div>`
                              : ""}
                            ${risk?.level === "elevated"
                              ? html`
                                  <div
                                    class="proposal-status"
                                    style="background:rgba(255,152,0,0.12); color:var(--warning-color,#ff9800); border:1px solid rgba(255,152,0,0.25); margin-bottom:8px;font-size:12px;"
                                  >
                                    <ha-icon icon="mdi:alert-outline"></ha-icon>
                                    <span>${risk.summary}</span>
                                  </div>
                                `
                              : ""}

                            <div
                              style="display:flex;align-items:center;gap:6px;margin-bottom:2px;"
                            >
                              <button
                                class="btn btn-primary"
                                style="flex:1;font-size:11px;padding:5px 8px;justify-content:center;"
                                ?disabled=${!!host._savingYaml[cardKey]}
                                @click=${() =>
                                  host._createSuggestionWithEdits(
                                    auto,
                                    cardKey,
                                    origYaml,
                                  )}
                              >
                                <ha-icon
                                  icon="mdi:check"
                                  style="--mdc-icon-size:13px;"
                                ></ha-icon>
                                ${host._savingYaml[cardKey]
                                  ? "Creating…"
                                  : "Accept"}
                              </button>
                              <button
                                class="btn btn-outline"
                                style="flex:1;font-size:11px;padding:5px 8px;justify-content:center;"
                                @click=${() => host._discardSuggestion(item)}
                              >
                                <ha-icon
                                  icon="mdi:close"
                                  style="--mdc-icon-size:13px;"
                                ></ha-icon>
                                Discard
                              </button>
                            </div>

                            <div class="card-tabs">
                              <span class="label">View:</span>
                              ${hasFlow
                                ? html`
                                    <button
                                      class="card-tab ${activeTab === "flow"
                                        ? "active"
                                        : ""}"
                                      @click=${() => {
                                        host._cardActiveTab = {
                                          ...host._cardActiveTab,
                                          [cardKey]:
                                            activeTab === "flow"
                                              ? null
                                              : "flow",
                                        };
                                      }}
                                    >
                                      <ha-icon
                                        icon="mdi:sitemap-outline"
                                        style="--mdc-icon-size:14px;"
                                      ></ha-icon>
                                      Flow
                                    </button>
                                    <span class="card-tab-sep">|</span>
                                  `
                                : ""}
                              <button
                                class="card-tab ${activeTab === "yaml"
                                  ? "active"
                                  : ""}"
                                @click=${() => {
                                  host._cardActiveTab = {
                                    ...host._cardActiveTab,
                                    [cardKey]:
                                      activeTab === "yaml" ? null : "yaml",
                                  };
                                }}
                              >
                                <ha-icon
                                  icon="mdi:code-braces"
                                  style="--mdc-icon-size:14px;"
                                ></ha-icon>
                                YAML
                              </button>
                              <ha-icon
                                icon="mdi:chevron-down"
                                class="card-chevron ${activeTab ? "open" : ""}"
                                style="margin-left:auto;"
                                @click=${() => {
                                  host._cardActiveTab = {
                                    ...host._cardActiveTab,
                                    [cardKey]: activeTab
                                      ? null
                                      : hasFlow
                                        ? "flow"
                                        : "yaml",
                                  };
                                }}
                              ></ha-icon>
                            </div>

                            ${activeTab === "flow" && hasFlow
                              ? renderAutomationFlowchart(host, auto)
                              : ""}
                            ${activeTab === "yaml"
                              ? html`
                                  <div style="margin-top:6px;">
                                    <textarea
                                      class="yaml-editor"
                                      style="width:100%;font-family:monospace;font-size:12px;background:var(--primary-background-color);color:var(--primary-text-color);border:1px solid var(--divider-color);border-radius:6px;padding:8px;resize:none;overflow:hidden;field-sizing:content;"
                                      .value=${displayYaml}
                                      @input=${(e) => {
                                        host._editedYaml = {
                                          ...host._editedYaml,
                                          [cardKey]: e.target.value,
                                        };
                                        e.target.style.height = "auto";
                                        e.target.style.height =
                                          e.target.scrollHeight + "px";
                                      }}
                                      @focus=${(e) => {
                                        e.target.style.height = "auto";
                                        e.target.style.height =
                                          e.target.scrollHeight + "px";
                                      }}
                                    >
                                    </textarea>
                                  </div>
                                `
                              : ""}
                          </div>
                        `;
                      }),
                    )}
                  </div>
                `
              : ""}
          `
        : ""}
      ${host._renderDiffViewer()} ${host._renderNewAutomationDialog()}
    </div>
  `;
}
