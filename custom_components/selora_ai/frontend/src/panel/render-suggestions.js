import { html } from "lit";
import { directive, Directive } from "lit/directive.js";
import { renderAutomationFlowchart } from "./render-automations.js";

// Sets cursor:pointer only when the element's text is actually clamped
// (scrollHeight exceeds clientHeight), so non-truncated cards keep the
// default cursor. `force` keeps the pointer while expanded (collapse hint).
class ClampCursorDirective extends Directive {
  update(part, [force]) {
    const el = part.element;
    if (force) {
      el.style.cursor = "pointer";
    } else {
      requestAnimationFrame(() => {
        el.style.cursor =
          el.scrollHeight > el.clientHeight + 1 ? "pointer" : "";
      });
    }
    return this.render(force);
  }
  render() {}
}
const clampCursor = directive(ClampCursorDirective);

const MIN_CONF = 0.8;
const COLLAPSED_COUNT = 3;

function normalizeProactive(s) {
  return {
    type: "proactive",
    cardKey: `proactive_${s.suggestion_id}`,
    title: s.description,
    subtitle: s.evidence_summary || null,
    risk: null,
    automationYaml: s.automation_yaml || "",
    automationData: s.automation_data || null,
    _original: s,
    _suggestionId: s.suggestion_id,
  };
}

function normalizeLLM(item) {
  const auto = item.automation || item.automation_data;
  return {
    type: "llm",
    cardKey: `sug_${auto.alias}`,
    title: auto.alias,
    subtitle: auto.description || null,
    risk: item.risk_assessment || auto?.risk_assessment || null,
    automationYaml: item.automation_yaml || "",
    automationData: auto,
    _original: item,
    _auto: auto,
  };
}

function buildQualified(host) {
  const seenKeys = new Set();
  const qualified = [];

  for (const s of host._proactiveSuggestions || []) {
    if ((s.confidence || 0) < MIN_CONF) continue;
    const key = (s.description || "").toLowerCase().trim();
    if (seenKeys.has(key)) continue;
    seenKeys.add(key);
    qualified.push(normalizeProactive(s));
  }

  for (const item of host._suggestions || []) {
    const auto = item.automation || item.automation_data;
    if (!auto) continue;
    const key = (auto.alias || "").toLowerCase().trim();
    if (seenKeys.has(key)) continue;
    seenKeys.add(key);
    qualified.push(normalizeLLM(item));
  }

  return qualified;
}

function applyFilters(host, qualified) {
  const filterText = (host._suggestionFilter || "").toLowerCase().trim();
  const sourceFilter = host._suggestionSourceFilter || "all";
  const sortBy = host._suggestionSortBy || "recent";

  const filtered = qualified.filter((item) => {
    if (filterText) {
      const text = `${item.title} ${item.subtitle || ""}`.toLowerCase();
      if (!text.includes(filterText)) return false;
    }
    if (sourceFilter === "pattern" && item.type !== "proactive") return false;
    if (sourceFilter === "ai" && item.type !== "llm") return false;
    return true;
  });

  if (sortBy === "alpha") {
    filtered.sort((a, b) => (a.title || "").localeCompare(b.title || ""));
  } else {
    // Default: AI-generated first, then by confidence descending
    filtered.sort((a, b) => {
      if (a.type !== b.type) return a.type === "llm" ? -1 : 1;
      const confA = a._original?.confidence || 0;
      const confB = b._original?.confidence || 0;
      return confB - confA;
    });
  }

  return filtered;
}

function renderSuggestionCard(host, item, bulkMode = false, selectedKeys = {}) {
  const { cardKey, automationData } = item;
  const editedYaml = host._editedYaml[cardKey];
  const displayYaml =
    editedYaml !== undefined ? editedYaml : item.automationYaml;
  const hasFlow =
    automationData &&
    ((automationData.triggers ?? automationData.trigger)?.length ||
      (automationData.actions ?? automationData.action)?.length);
  const activeTab =
    host._cardActiveTab[cardKey] !== undefined
      ? host._cardActiveTab[cardKey]
      : null;

  const isProactive = item.type === "proactive";
  const accepting = isProactive
    ? !!host._acceptingProactive[item._suggestionId]
    : !!host._savingYaml[cardKey];
  const dismissing = isProactive
    ? !!host._dismissingProactive[item._suggestionId]
    : false;

  const fadingOut = !!(host._fadingOutSuggestions || {})[cardKey];
  const expandedText = !!(host._expandedSuggestions || {})[cardKey];
  const toggleText = () => {
    host._expandedSuggestions = {
      ...(host._expandedSuggestions || {}),
      [cardKey]: !expandedText,
    };
  };
  const expandedClass = expandedText ? "expanded" : "";

  return html`
    <div
      class="card${fadingOut ? " fading-out" : ""}"
      style="padding:16px 18px;display:flex;flex-direction:column;"
    >
      <div class="card-header" style="margin-bottom:0;">
        ${bulkMode
          ? html`
              <label class="card-select">
                <input
                  type="checkbox"
                  .checked=${!!selectedKeys[cardKey]}
                  @change=${(e) => {
                    host._selectedSuggestionKeys = {
                      ...host._selectedSuggestionKeys,
                      [cardKey]: e.target.checked,
                    };
                  }}
                />
              </label>
            `
          : ""}
        <h3
          class=${expandedClass}
          style="flex:1;font-size:14px;margin:0;"
          title=${expandedText ? "" : item.title}
          @click=${toggleText}
          ${clampCursor(expandedText)}
        >
          ${item.title}
        </h3>
      </div>

      ${item.subtitle
        ? html`
            <div
              class="clamp-2 ${expandedClass}"
              style="font-size:12px;color:var(--secondary-text-color);line-height:1.5;margin-top:8px;"
              title=${expandedText ? "" : item.subtitle}
              @click=${toggleText}
              ${clampCursor(expandedText)}
            >
              ${item.subtitle}
            </div>
          `
        : ""}
      ${item.risk?.level === "elevated"
        ? html`
            <div
              class="proposal-status"
              style="background:rgba(255,152,0,0.12); color:var(--warning-color,#ff9800); border:1px solid rgba(255,152,0,0.25); margin-top:8px;font-size:12px;"
            >
              <ha-icon icon="mdi:alert-outline"></ha-icon>
              <span>${item.risk.summary}</span>
            </div>
          `
        : ""}

      <div class="card-tabs" style="margin-top:12px;">
        ${hasFlow
          ? html`
              <button
                class="card-tab ${activeTab === "flow" ? "active" : ""}"
                @click=${() => {
                  host._cardActiveTab = {
                    ...host._cardActiveTab,
                    [cardKey]: activeTab === "flow" ? null : "flow",
                  };
                }}
              >
                <ha-icon
                  icon="mdi:sitemap-outline"
                  style="--mdc-icon-size:14px;"
                ></ha-icon>
                ${host._t("suggestions_tab_flow", "Flow")}
              </button>
              <span class="card-tab-sep">|</span>
            `
          : ""}
        <button
          class="card-tab ${activeTab === "yaml" ? "active" : ""}"
          @click=${() => {
            host._cardActiveTab = {
              ...host._cardActiveTab,
              [cardKey]: activeTab === "yaml" ? null : "yaml",
            };
          }}
        >
          <ha-icon
            icon="mdi:code-braces"
            style="--mdc-icon-size:14px;"
          ></ha-icon>
          ${host._t("suggestions_tab_yaml", "YAML")}
        </button>
        <ha-icon
          icon="mdi:chevron-down"
          class="card-chevron ${activeTab ? "open" : ""}"
          style="margin-left:auto;"
          @click=${() => {
            host._cardActiveTab = {
              ...host._cardActiveTab,
              [cardKey]: activeTab ? null : hasFlow ? "flow" : "yaml",
            };
          }}
        ></ha-icon>
      </div>

      ${activeTab === "flow" && hasFlow
        ? renderAutomationFlowchart(host, automationData)
        : ""}
      ${activeTab === "yaml"
        ? html`
            <div style="margin-top:6px;">
              <ha-code-editor
                mode="yaml"
                .value=${displayYaml}
                @value-changed=${(e) => {
                  host._editedYaml = {
                    ...host._editedYaml,
                    [cardKey]: e.detail.value,
                  };
                }}
                autocomplete-entities
                style="--code-mirror-font-size:12px;"
              ></ha-code-editor>
            </div>
          `
        : ""}

      <div
        style="display:flex;align-items:center;gap:6px;margin-top:auto;padding-top:12px;"
      >
        <button
          class="btn btn-primary"
          style="flex:1;justify-content:center;"
          ?disabled=${accepting}
          @click=${() =>
            isProactive
              ? host._acceptProactiveSuggestion(item._suggestionId, editedYaml)
              : host._createSuggestionWithEdits(
                  item._auto,
                  cardKey,
                  item.automationYaml,
                )}
        >
          <ha-icon icon="mdi:check" style="--mdc-icon-size:13px;"></ha-icon>
          ${accepting
            ? host._t("suggestions_btn_creating", "Creating…")
            : host._t("suggestions_btn_accept", "Accept")}
        </button>
        <button
          class="btn btn-outline"
          style="flex:1;justify-content:center;"
          ?disabled=${dismissing}
          @click=${() =>
            isProactive
              ? host._dismissProactiveSuggestion(item._suggestionId)
              : host._discardSuggestion(item._original)}
        >
          <ha-icon icon="mdi:close" style="--mdc-icon-size:13px;"></ha-icon>
          ${dismissing
            ? host._t("suggestions_btn_dismissing", "Dismissing…")
            : host._t("suggestions_btn_dismiss", "Dismiss")}
        </button>
      </div>
    </div>
  `;
}

export function renderSuggestionsSection(host) {
  const qualified = buildQualified(host);
  const filtered = applyFilters(host, qualified);
  const totalCount = qualified.length;
  const isDev = !!host._config?.developer_mode;
  const visibleCount = host._suggestionsVisibleCount || COLLAPSED_COUNT;
  const visibleItems = filtered.slice(0, visibleCount);
  const remainingCount = filtered.length - visibleCount;
  const expanded = visibleCount > COLLAPSED_COUNT;
  const bulkMode = !!host._suggestionBulkMode;
  const selectedKeys = host._selectedSuggestionKeys || {};
  const selectedCount = Object.values(selectedKeys).filter(Boolean).length;

  return html`
    <div class="section-card suggestions-section">
      <div class="section-card-header">
        <div class="section-card-title-group">
          <h3>${host._t("suggestions_section_title", "Suggested for you")}</h3>
          ${totalCount > 0
            ? html`<span class="badge"
                >${totalCount} ${host._t("suggestions_badge_new", "new")}</span
              >`
            : ""}
        </div>
        ${isDev
          ? html`
              <div class="section-card-actions">
                <button
                  class="btn"
                  ?disabled=${host._loadingProactive || host._llmNeedsSetup}
                  title=${host._llmNeedsSetup
                    ? host._t(
                        "suggestions_llm_setup_required",
                        "Configure an LLM provider first",
                      )
                    : ""}
                  @click=${() => host._triggerPatternScan()}
                >
                  <ha-icon
                    icon="mdi:refresh"
                    style="--mdc-icon-size:14px;"
                  ></ha-icon>
                  ${host._loadingProactive
                    ? host._t("suggestions_btn_scanning", "Scanning…")
                    : host._t("suggestions_btn_scan_now", "Scan Now")}
                </button>
                <button
                  class="btn btn-primary"
                  ?disabled=${host._generatingSuggestions ||
                  host._llmNeedsSetup}
                  title=${host._llmNeedsSetup
                    ? host._t(
                        "suggestions_llm_setup_required",
                        "Configure an LLM provider first",
                      )
                    : ""}
                  @click=${() => host._triggerGenerateSuggestions()}
                >
                  ${host._generatingSuggestions
                    ? html`<span
                        class="spinner"
                        style="width:14px;height:14px;border-width:2px;vertical-align:middle;"
                      ></span>`
                    : html`<ha-icon
                        icon="mdi:auto-fix"
                        style="--mdc-icon-size:14px;"
                      ></ha-icon>`}
                  ${host._generatingSuggestions
                    ? host._t("suggestions_btn_analyzing", "Analyzing…")
                    : host._t("suggestions_btn_generate", "Generate")}
                </button>
              </div>
            `
          : ""}
      </div>

      <div class="section-card-subtitle">
        ${host._t(
          "suggestions_section_subtitle",
          "Based on observed patterns and AI analysis in your home.",
        )}
      </div>

      ${totalCount === 0
        ? html`
            <p style="opacity:0.45;margin:0;font-size:13px;">
              ${host._t(
                "suggestions_empty_state",
                'No suggestions yet. Tap "Generate" to analyze your home.',
              )}
            </p>
          `
        : html`
            ${expanded
              ? html`<div class="filter-row" style="margin-bottom:12px;">
                  <div class="filter-input-wrap" style="flex:0 1 260px;">
                    <ha-icon icon="mdi:magnify"></ha-icon>
                    <input
                      type="text"
                      placeholder=${host._t(
                        "suggestions_filter_placeholder",
                        "Filter suggestions…",
                      )}
                      .value=${host._suggestionFilter}
                      @input=${(e) => {
                        host._suggestionFilter = e.target.value;
                        host._suggestionsVisibleCount = COLLAPSED_COUNT;
                      }}
                    />
                    ${host._suggestionFilter
                      ? html`<ha-icon
                          icon="mdi:close-circle"
                          style="--mdc-icon-size:16px;cursor:pointer;opacity:0.5;flex-shrink:0;"
                          @click=${() => {
                            host._suggestionFilter = "";
                            host._suggestionsVisibleCount = COLLAPSED_COUNT;
                          }}
                        ></ha-icon>`
                      : ""}
                  </div>
                  ${isDev
                    ? html`
                        <div class="status-pills">
                          ${[
                            ["all", host._t("suggestions_filter_all", "All")],
                            [
                              "pattern",
                              host._t(
                                "suggestions_filter_patterns",
                                "Patterns",
                              ),
                            ],
                            ["ai", host._t("suggestions_filter_ai", "AI")],
                          ].map(
                            ([val, label]) => html`
                              <button
                                class="status-pill ${(host._suggestionSourceFilter ||
                                  "all") === val
                                  ? "active"
                                  : ""}"
                                @click=${() => {
                                  host._suggestionSourceFilter = val;
                                  host._suggestionsVisibleCount =
                                    COLLAPSED_COUNT;
                                }}
                              >
                                ${label}
                              </button>
                            `,
                          )}
                        </div>
                      `
                    : ""}
                  <select
                    class="sort-select"
                    .value=${host._suggestionSortBy || "recent"}
                    @change=${(e) => {
                      host._suggestionSortBy = e.target.value;
                    }}
                  >
                    <option value="recent">
                      ${host._t("suggestions_sort_recent", "Recent")}
                    </option>
                    <option value="alpha">
                      ${host._t("suggestions_sort_alpha", "Alphabetical")}
                    </option>
                  </select>
                  <div
                    style="margin-left:auto;display:flex;align-items:center;gap:8px;"
                  >
                    ${bulkMode
                      ? html`
                          <span style="font-size:12px;opacity:0.7;">
                            ${selectedCount}
                            ${host._t("suggestions_bulk_selected", "selected")}
                          </span>
                          <button
                            class="btn btn-primary"
                            ?disabled=${selectedCount === 0}
                            @click=${() => {
                              for (const item of filtered) {
                                if (selectedKeys[item.cardKey]) {
                                  if (item.type === "proactive") {
                                    host._acceptProactiveSuggestion(
                                      item._suggestionId,
                                    );
                                  } else {
                                    host._createSuggestionWithEdits(
                                      item._auto,
                                      item.cardKey,
                                      item.automationYaml,
                                    );
                                  }
                                }
                              }
                              host._selectedSuggestionKeys = {};
                              host._suggestionBulkMode = false;
                            }}
                          >
                            ${host._t(
                              "suggestions_bulk_accept_selected",
                              "Accept selected",
                            )}
                          </button>
                          <button
                            class="btn btn-outline"
                            ?disabled=${selectedCount === 0}
                            @click=${() => {
                              for (const item of filtered) {
                                if (selectedKeys[item.cardKey]) {
                                  if (item.type === "proactive") {
                                    host._dismissProactiveSuggestion(
                                      item._suggestionId,
                                    );
                                  } else {
                                    host._discardSuggestion(item._original);
                                  }
                                }
                              }
                              host._selectedSuggestionKeys = {};
                              host._suggestionBulkMode = false;
                            }}
                          >
                            ${host._t(
                              "suggestions_bulk_dismiss_selected",
                              "Dismiss selected",
                            )}
                          </button>
                          <button
                            class="btn btn-outline"
                            @click=${() => {
                              host._suggestionBulkMode = false;
                              host._selectedSuggestionKeys = {};
                            }}
                          >
                            ${host._t("suggestions_bulk_done", "Done")}
                          </button>
                        `
                      : html`
                          <button
                            class="btn btn-outline"
                            @click=${() => {
                              host._suggestionBulkMode = true;
                            }}
                          >
                            <ha-icon
                              icon="mdi:checkbox-multiple-outline"
                              style="--mdc-icon-size:14px;"
                            ></ha-icon>
                            ${host._t("suggestions_bulk_edit", "Bulk edit")}
                          </button>
                        `}
                  </div>
                </div>`
              : ""}

            <div class="automations-grid">
              ${visibleItems.map((item) =>
                renderSuggestionCard(host, item, bulkMode, selectedKeys),
              )}
            </div>

            ${remainingCount > 0
              ? html`
                  <button
                    class="show-more-link"
                    @click=${() => {
                      host._suggestionsVisibleCount = visibleCount + 10;
                    }}
                  >
                    ${host._t("suggestions_show_more", "Show more suggestions")}
                  </button>
                `
              : ""}
          `}
    </div>
  `;
}
