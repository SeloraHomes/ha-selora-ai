import { html } from "lit";
import { renderMarkdown } from "../shared/markdown.js";
import { renderQuickActions } from "./quick-actions.js";
import { renderHealthGauge } from "./health-gauge.js";
import { renderScoreBreakdown } from "./health-score-breakdown.js";

// Health tab.
//   A deterministic health score + a checklist: every check that ran (device
//   health, automation hygiene, updates), each shown clear or expanded to its
//   finding cards. Data loaded via insights-actions.js (_loadAudit).

const _SEVERITY_META = {
  critical: { color: "#ef4444", icon: "mdi:alert-octagon", order: 0 },
  warning: { color: "#f59e0b", icon: "mdi:alert", order: 1 },
  info: { color: "#3b82f6", icon: "mdi:information", order: 2 },
};

function _severityMeta(sev) {
  return _SEVERITY_META[sev] || _SEVERITY_META.info;
}

function _relativeTime(host, iso) {
  if (!iso) return host._t("insights_never", "never");
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 1) return host._t("insights_just_now", "just now");
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  return hrs < 24 ? `${hrs} h ago` : `${Math.round(hrs / 24)} d ago`;
}

// ── Audit (primary) ──────────────────────────────────────────────────

const _CATEGORY_LABEL = {
  issue: "Issue",
  fix: "Fix",
  improvement: "Improvement",
};

// Render a set of entity references as live HA tile cards, hydrated by the
// panel's _hydrateEntityChips (same path as chat). Only entity_ids that
// actually exist get a tile — an unknown id (e.g. an LLM slip in an audit
// card) is dropped rather than rendered as a broken "unavailable" tile.
function _entityCards(host, entityIds) {
  const ids = (entityIds || []).filter((e) => host.hass?.states?.[e]);
  if (!ids.length) return "";
  return html`<div
    class="selora-entity-grid audit-card-tiles"
    data-entity-ids=${ids.join(",")}
  ></div>`;
}

// Build the chat message for a recommendation's "Ask Selora" button. The
// finding's `action` is a terse card label ("Replace sensor battery.") —
// useless as a prompt on its own. Send the full context (title + detail +
// the affected entity_ids so the assistant can resolve them) framed as a
// help request, so the reply is grounded and actionable.
function _askPrompt(rec) {
  const parts = [
    `My home audit flagged this ${rec.category || "issue"}: "${rec.title}".`,
  ];
  if (rec.detail) parts.push(rec.detail);
  if (rec.entities && rec.entities.length) {
    parts.push(`Affected entities: ${rec.entities.join(", ")}.`);
  }
  // Advisory framing: explain + options. WITHOUT the "don't change anything"
  // guard the architect reads "how do I fix it" on an automation finding as
  // "build the automation" and switches into automation-creation mode.
  parts.push(
    "Please explain what's going on and what my options are. Just advise — " +
      "don't create or change any automations, scenes, or devices.",
  );
  return parts.join(" ");
}

function _recCard(host, rec) {
  const meta = _severityMeta(rec.severity);
  const category = host._t(
    `insights_kind_${rec.category}`,
    _CATEGORY_LABEL[rec.category] || rec.category,
  );
  return html`
    <div class="audit-card">
      <div class="audit-card-bar" style="background:${meta.color};"></div>
      <div class="audit-card-body">
        <div class="audit-card-head">
          <ha-icon
            icon=${meta.icon}
            style="color:${meta.color};--mdc-icon-size:18px;"
          ></ha-icon>
          <span class="audit-card-title">${rec.title}</span>
          <span class="badge audit-card-badge">${category}</span>
        </div>
        ${
          rec.detail
            ? html`<div class="audit-card-detail">${rec.detail}</div>`
            : ""
        }
        ${_entityCards(host, rec.entities)}
        <div class="audit-card-actions">
          <button
            class="btn btn-outline btn-sm"
            @click=${() => host._askInNewChat(_askPrompt(rec))}
          >
            <ha-icon icon="mdi:auto-fix"></ha-icon>
            ${host._t("insights_ask_selora", "Ask Selora AI")}
          </button>
          ${
            rec.link
              ? html`<a class="btn btn-outline btn-sm" href=${rec.link}>
                  <ha-icon icon="mdi:open-in-new"></ha-icon>
                  ${host._t(
                    "insights_open_settings",
                    rec.link_label || "Open in Settings",
                  )}
                </a>`
              : ""
          }
          ${
            rec.device_id || (rec.entities && rec.entities.length)
              ? html`<button
                  class="btn btn-outline btn-sm"
                  @click=${() => host._ignoreFix(rec)}
                >
                  <ha-icon icon="mdi:bell-off-outline"></ha-icon>
                  ${host._t("insights_ignore_short", "Ignore")}
                </button>`
              : ""
          }
        </div>
      </div>
    </div>
  `;
}

// One checklist row: what the check assessed + its outcome. A clear check
// shows a green tick (so the user sees it WAS tested); a check with issues
// shows an amber marker and expands to the finding cards below it.
function _checkRow(host, check) {
  const issues = check.status === "issues";
  const errored = check.status === "error";
  const findings = check.findings || [];
  const statusText = errored
    ? host._t("insights_check_error", "Couldn't check")
    : issues
      ? `${findings.length} ${findings.length === 1 ? "issue" : "issues"}`
      : host._t("insights_check_clear", "Clear");
  const iconName = errored
    ? "mdi:help-circle-outline"
    : issues
      ? "mdi:alert-circle"
      : "mdi:check-circle";
  const iconClass = errored
    ? "check-error"
    : issues
      ? "check-issues"
      : "check-clear";
  const badgeClass = errored ? "error" : issues ? "issues" : "clear";
  return html`
    <div
      id=${`hc-${check.check_id}`}
      class="check-item ${issues ? "check-item-issues" : ""}"
    >
      <div class="check-head">
        <ha-icon class="check-icon ${iconClass}" icon=${iconName}></ha-icon>
        <span class="check-title">${check.title}</span>
        ${
          check.kind === "model"
            ? html`<span class="badge check-ai">AI</span>`
            : ""
        }
        <span class="check-badge ${badgeClass}">${statusText}</span>
      </div>
      ${
        issues
          ? html`<div class="audit-cards check-findings">
              ${findings.map((f) => _recCard(host, f))}
            </div>`
          : ""
      }
    </div>
  `;
}

function _auditBody(host) {
  // Show the loading state while a run is in flight OR before the first fetch
  // resolves — otherwise the page briefly flashes the "No audit yet" empty
  // state on open, before _loadAudit populates the checklist.
  if (host._auditRunning || !host._auditLoaded) {
    return html`<div class="insight-audit-card">
      <div class="insight-audit-status insight-audit-running">
        <span class="spinner"></span>
        <div>
          <div>${host._t("insights_audit_running", "Running checks…")}</div>
        </div>
      </div>
    </div>`;
  }
  const status = host._auditStatus;
  // Deterministic check catalog: show the full checklist — every check that
  // ran, whether it's clear, and (expanded) what it found.
  const checks = host._auditChecks || [];
  if (checks.length) {
    return html`<div class="check-list">
      ${checks.map((c) => _checkRow(host, c))}
    </div>`;
  }
  if (status === "no_llm") {
    return html`<div class="insight-audit-card">
      <div class="insight-audit-status">
        ${host._t(
          "insights_audit_no_llm",
          "Configure an LLM provider in Settings to get daily home audits.",
        )}
      </div>
    </div>`;
  }
  if (status === "unsupported") {
    return html`<div class="insight-audit-card">
      <div class="insight-audit-status">
        ${host._t(
          "insights_audit_unsupported",
          "Home audits need a cloud LLM provider (Anthropic, Gemini, or OpenAI). A local model can't run them.",
        )}
      </div>
    </div>`;
  }
  if (status === "error") {
    return html`<div class="insight-audit-card">
      <div class="insight-audit-status">
        ${host._t("insights_audit_error", "The last audit failed.")}
        ${
          host._auditError
            ? html`<div class="insight-audit-err">${host._auditError}</div>`
            : ""
        }
      </div>
    </div>`;
  }

  const recs = host._auditRecommendations || [];
  if (status === "ok" && recs.length) {
    return html`<div class="audit-cards">
      ${recs.map((r) => _recCard(host, r))}
    </div>`;
  }
  // Fallback: a model that didn't return structured JSON — render its prose.
  if (status === "ok" && host._auditResponse) {
    return html`
      <div class="insight-audit-card">
        <div
          class="insight-audit-md"
          .innerHTML=${renderMarkdown(host._auditResponse)}
        ></div>
        ${
          host._auditQuickActions && host._auditQuickActions.length
            ? html`<div class="insight-audit-actions">
                ${renderQuickActions(host, host._auditQuickActions, {})}
              </div>`
            : ""
        }
      </div>
    `;
  }
  // A completed audit that found nothing to flag ([] recommendations, no
  // prose): the home is healthy. Distinct from "never ran" so it doesn't read
  // as "No audit yet".
  if (status === "ok") {
    return html`<div class="insight-audit-card">
      <div class="insight-audit-status">
        ${host._t(
          "insights_audit_empty_ok",
          "All clear — the last audit found nothing to fix or improve.",
        )}
      </div>
    </div>`;
  }
  return html`<div class="insight-audit-card">
    <div class="insight-audit-status">
      ${host._t(
        "insights_audit_pending",
        "No audit yet — the first one runs shortly after startup, or run it now.",
      )}
    </div>
  </div>`;
}

export function renderInsights(host) {
  if (host._insightsEnabled === false) {
    return html`
      <div class="scroll-view">
        <div class="page-root">
          <div class="empty-state" style="text-align:center;padding:48px 16px;">
            <ha-icon
              icon="mdi:heart-off-outline"
              style="--mdc-icon-size:40px;opacity:0.4;"
            ></ha-icon>
            <p>
              ${host._t(
                "insights_disabled",
                "Health monitoring is turned off. Enable it in Settings to monitor your home's health.",
              )}
            </p>
          </div>
        </div>
      </div>
    `;
  }

  // Home still booting: devices are reconnecting, so any score would over-count
  // offline devices. Show a spinner instead of a misleading gauge — the panel
  // re-fetches on its own (insights-actions) once the settle grace elapses.
  if (host._auditSettling) {
    return html`
      <div class="scroll-view">
        <div class="page-root">
          <div class="page-header">
            <h1 class="page-h1">${host._t("insights_title", "Health")}</h1>
          </div>
          <div class="insights-settling">
            <span class="spinner"></span>
            <div class="insights-settling-title">
              ${host._t(
                "insights_settling_title",
                "Your home is still starting up",
              )}
            </div>
            <div class="insights-settling-sub">
              ${host._t(
                "insights_settling_sub",
                "We'll run a health check once your devices are back online.",
              )}
            </div>
          </div>
        </div>
      </div>
    `;
  }

  return html`
    <div class="scroll-view">
      <div class="page-root">
        <div class="page-header">
          <h1 class="page-h1">${host._t("insights_title", "Health")}</h1>
          <span class="page-header-spacer"></span>
          <button
            class="filter-row-action"
            ?disabled=${host._auditRunning}
            @click=${() => host._rerunAudit()}
          >
            <ha-icon icon="mdi:refresh" style="--mdc-icon-size:13px;"></ha-icon>
            ${host._t("insights_rerun_audit", "Re-run")}
          </button>
        </div>
        <div class="page-sub">
          ${host._t("insights_last_audit", "Last checked")}:
          ${_relativeTime(host, host._auditGeneratedAt)}
        </div>

        ${renderHealthGauge(host)} ${renderScoreBreakdown(host)}
        ${_auditBody(host)}
      </div>
    </div>
  `;
}
