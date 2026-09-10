import { html } from "lit";

// One line under a search hit naming the entities, devices or areas the query
// matched. A search covers everything a rule TARGETS, not just its title, so a
// row whose visible text contains none of the typed words is the normal case —
// and without this it reads as a bug in the search box.
//
// Shared by the automations and scenes lists so the two stay identical.
export function renderSearchMatchReason(host, reasons) {
  if (!reasons || !reasons.length) return "";
  return html`<span class="auto-row-match" title=${reasons.join(", ")}>
    <ha-icon icon="mdi:magnify"></ha-icon>
    <span class="auto-row-match-text"
      >${host
        ._t("search_match_reason", "matches {targets}")
        .replace("{targets}", reasons.join(", "))}</span
    >
  </span>`;
}
