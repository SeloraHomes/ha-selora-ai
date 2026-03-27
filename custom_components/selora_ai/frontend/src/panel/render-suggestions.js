import { html } from "lit";
import { masonryColumns } from "./render-automations.js";

export function renderUnifiedSuggestions(host) {
  const MIN_CONF = 0.8;
  const SPAGE_SIZE = host._suggestionsPerPage || 10;
  // Deduplicate proactive suggestions by description
  const seenDescs = new Set();
  const qualified = (host._proactiveSuggestions || []).filter((s) => {
    if ((s.confidence || 0) < MIN_CONF) return false;
    const key = (s.description || "").toLowerCase().trim();
    if (seenDescs.has(key)) return false;
    seenDescs.add(key);
    return true;
  });
  const totalItems = qualified.length + (host._suggestions || []).length;

  return html`
    <div style="margin-bottom:16px;">
      <div class="filter-row" style="margin-bottom:12px;">
        <div
          style="display:flex;align-items:center;gap:8px;justify-content:center;"
        >
          <button
            class="btn"
            style="font-size:11px;"
            ?disabled=${host._loadingProactive}
            @click=${() => host._triggerPatternScan()}
          >
            <ha-icon icon="mdi:refresh" style="--mdc-icon-size:13px;"></ha-icon>
            ${host._loadingProactive ? "Scanning…" : "Scan Now"}
          </button>
          <button
            class="btn btn-primary"
            style="font-size:11px;"
            ?disabled=${host._generatingSuggestions}
            @click=${() => host._triggerGenerateSuggestions()}
          >
            ${host._generatingSuggestions
              ? html`<span
                  class="spinner"
                  style="width:14px;height:14px;border-width:2px;vertical-align:middle;"
                ></span>`
              : html`<ha-icon
                  icon="mdi:auto-fix"
                  style="--mdc-icon-size:13px;"
                ></ha-icon>`}
            ${host._generatingSuggestions ? "Analyzing…" : "Generate"}
          </button>
          <label class="per-page-label"
            >Show Per Page:
            <select
              class="per-page-select"
              .value=${String(host._suggestionsPerPage)}
              @change=${(e) => {
                host._suggestionsPerPage = Number(e.target.value);
                host._suggestionsPage = 1;
              }}
            >
              <option value="10">10</option>
              <option value="20">20</option>
              <option value="50">50</option>
            </select>
          </label>
        </div>
      </div>

      ${totalItems === 0
        ? html`
            <div
              style="display:flex;flex-direction:column;align-items:center;padding:32px 0;gap:12px;"
            >
              <ha-icon
                icon="mdi:lightbulb-auto-outline"
                style="--mdc-icon-size:48px;opacity:0.3;"
              ></ha-icon>
              <p style="opacity:0.45;margin:0;font-size:13px;">
                No suggestions yet. Tap "Generate" to analyze your home.
              </p>
            </div>
          `
        : ""}
      ${qualified.length > 0
        ? html`
            ${(() => {
              const sTotalPages = Math.max(
                1,
                Math.ceil(qualified.length / SPAGE_SIZE),
              );
              const sSafePage = Math.min(host._suggestionsPage, sTotalPages);
              const sPaged = qualified.slice(
                (sSafePage - 1) * SPAGE_SIZE,
                sSafePage * SPAGE_SIZE,
              );

              return html`
                <div class="automations-grid">
                  ${masonryColumns(
                    sPaged.map((s) => {
                      const accepting =
                        !!host._acceptingProactive[s.suggestion_id];
                      const dismissing =
                        !!host._dismissingProactive[s.suggestion_id];
                      const cardKey = `proactive_${s.suggestion_id}`;
                      const editedYaml = host._editedYaml[cardKey];
                      const displayYaml =
                        editedYaml !== undefined
                          ? editedYaml
                          : s.automation_yaml;
                      const parsedAuto = s.automation_data || null;
                      const hasFlow =
                        parsedAuto &&
                        (parsedAuto.trigger?.length ||
                          parsedAuto.triggers?.length ||
                          parsedAuto.action?.length ||
                          parsedAuto.actions?.length);
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
                              ${s.description}
                            </h3>
                          </div>

                          ${s.evidence_summary
                            ? html`
                                <div
                                  style="font-size:11px;opacity:0.55;margin-bottom:6px;"
                                >
                                  ${s.evidence_summary}
                                </div>
                              `
                            : ""}

                          <div
                            style="display:flex;align-items:center;gap:6px;margin-bottom:2px;"
                          >
                            <button
                              class="btn btn-primary"
                              style="flex:1;font-size:11px;padding:5px 8px;justify-content:center;"
                              ?disabled=${accepting}
                              @click=${() =>
                                host._acceptProactiveSuggestion(
                                  s.suggestion_id,
                                  editedYaml,
                                )}
                            >
                              <ha-icon
                                icon="mdi:check"
                                style="--mdc-icon-size:13px;"
                              ></ha-icon>
                              ${accepting ? "Creating…" : "Accept"}
                            </button>
                            <button
                              class="btn btn-outline"
                              style="flex:1;font-size:11px;padding:5px 8px;justify-content:center;"
                              ?disabled=${dismissing}
                              @click=${() =>
                                host._dismissProactiveSuggestion(
                                  s.suggestion_id,
                                )}
                            >
                              <ha-icon
                                icon="mdi:close"
                                style="--mdc-icon-size:13px;"
                              ></ha-icon>
                              ${dismissing ? "Dismissing…" : "Dismiss"}
                            </button>
                            <button
                              class="btn btn-outline"
                              style="flex:1;font-size:11px;padding:5px 8px;justify-content:center;"
                              ?disabled=${dismissing}
                              @click=${() =>
                                host._snoozeProactiveSuggestion(
                                  s.suggestion_id,
                                )}
                            >
                              <ha-icon
                                icon="mdi:clock-outline"
                                style="--mdc-icon-size:13px;"
                              ></ha-icon>
                              Snooze
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
                                          activeTab === "flow" ? null : "flow",
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
                                  [cardKey]: activeTab ? null : "yaml",
                                };
                              }}
                            ></ha-icon>
                          </div>

                          ${activeTab === "flow" && hasFlow
                            ? host._renderAutomationFlowchart(parsedAuto)
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
                ${sTotalPages > 1
                  ? html`
                      <div class="pagination">
                        <button
                          class="btn btn-outline"
                          ?disabled=${sSafePage <= 1}
                          @click=${() => {
                            host._suggestionsPage = sSafePage - 1;
                          }}
                        >
                          ‹ Prev
                        </button>
                        <span class="page-info"
                          >Page ${sSafePage} of ${sTotalPages} ·
                          ${qualified.length} suggestions</span
                        >
                        <button
                          class="btn btn-outline"
                          ?disabled=${sSafePage >= sTotalPages}
                          @click=${() => {
                            host._suggestionsPage = sSafePage + 1;
                          }}
                        >
                          Next ›
                        </button>
                      </div>
                    `
                  : ""}
              `;
            })()}
          `
        : ""}
    </div>
  `;
}
