import { html } from "lit";

// "Why this score" — a compact, readable decomposition of the deterministic
// health score, shown under the gauge. It answers the two questions the bare
// number can't: how far the home is from a clean 100, and which categories are
// pulling it down (biggest first). Each row links down to its section in the
// checklist below, so the user can jump straight to the offending devices.

// Bar colour by how much the category drags the score down — the redder, the
// worse. Same warning→critical ramp as the finding severities, so the biggest
// offenders pop at a glance without needing a legend.
function _barColor(points) {
  if (points >= 20) return "#ef4444"; // heavy drag
  if (points >= 5) return "#f97316"; // notable
  return "#f59e0b"; // minor
}

// Whole numbers read cleaner than "−28.6" for a proportional impact chart; the
// exact decimal is accounting precision the user doesn't need to see here.
function _round(n) {
  return Math.round(n);
}

// Scroll the checklist row for a check into view and flash it, so clicking a
// breakdown bar takes the user straight to the devices behind that penalty.
function _jumpToCheck(host, checkId) {
  const root = host.renderRoot || host.shadowRoot;
  const el = root && root.getElementById(`hc-${checkId}`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  el.classList.add("check-item-flash");
  // Clear the class after the flash so a repeat click re-triggers it.
  setTimeout(() => el.classList.remove("check-item-flash"), 1200);
}

function _sectionRow(host, section, maxPoints, linkable, index) {
  const color = _barColor(section.points);
  // Bar width is proportional to the biggest contributor (sections arrive
  // sorted desc), with a floor so a tiny penalty still shows a sliver.
  const width = Math.max(5, Math.round((section.points / maxPoints) * 100));
  const count = section.count || 0;
  const countLabel = `${count} ${
    count === 1
      ? host._t("insights_issue_one", "issue")
      : host._t("insights_issue_many", "issues")
  }`;
  // Stagger the row/bar entrance top-to-bottom so the chart builds itself in.
  // --sb-w is the target width the grow keyframe animates to; the inline width
  // is the no-animation fallback (reduced motion snaps straight to it).
  const delay = `${index * 90}ms`;
  const body = html`
    <span class="sb-row-name" title=${section.title}>${section.title}</span>
    <div class="sb-track">
      <div
        class="sb-fill"
        style="width:${width}%;--sb-w:${width}%;background:${color};animation-delay:${delay};"
      ></div>
    </div>
    <span class="sb-row-count">${countLabel}</span>
    <span class="sb-row-pts">−${_round(section.points)}</span>
    ${
      linkable
        ? html`<ha-icon
            class="sb-row-arrow"
            icon="mdi:chevron-right"
          ></ha-icon>`
        : html`<span class="sb-row-arrow-spacer"></span>`
    }
  `;
  return linkable
    ? html`<button
        class="sb-row sb-row-link"
        style="animation-delay:${delay};"
        @click=${() => _jumpToCheck(host, section.check_id)}
      >
        ${body}
      </button>`
    : html`<div class="sb-row" style="animation-delay:${delay};">${body}</div>`;
}

export function renderScoreBreakdown(host) {
  const bd = host._auditBreakdown;
  const score = host._auditScore;
  if (!bd || typeof score !== "number") return "";
  const sections = Array.isArray(bd.sections)
    ? bd.sections.filter((s) => s && s.points > 0)
    : [];
  // A clean 100 (nothing flagged) has nothing to explain — skip the card
  // entirely so a healthy home isn't handed an empty "breakdown".
  if (!sections.length) return "";

  // Only rows whose check actually renders below get an anchor link.
  const checkIds = new Set(
    (host._auditChecks || []).map((c) => c.check_id).filter(Boolean),
  );
  const maxPoints = sections[0].points || 1;

  return html`
    <div class="score-breakdown">
      <div class="sb-head">
        <span class="sb-title">
          ${host._t("insights_score_breakdown", "What's affecting your score")}
        </span>
      </div>
      <div class="sb-rows">
        ${sections.map((s, i) =>
          _sectionRow(host, s, maxPoints, checkIds.has(s.check_id), i),
        )}
      </div>
    </div>
  `;
}
