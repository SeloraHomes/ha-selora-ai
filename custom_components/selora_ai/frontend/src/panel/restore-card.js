// The card a `restore_deleted` chat turn ends on (backend: trash.py). Names
// repeat — two "Garage Night Spots" are how the trash fills in the first place
// — so the user is shown what each match does, its YAML and when it was
// deleted, and restores the one they mean; chat never restores on its own.
//
// Rendered from the reply's stored tool calls, so a reopened conversation
// shows it too. Those carry only names and dates (a long entry would be trimmed
// out of a tool result); each card fetches its matches in full when it first
// renders, which also says which are still kept.
import { html } from "lit";
import { interpolate } from "../shared/i18n.js";
import {
  renderAutomationFlowchart,
  renderAutomationIdentity,
} from "./render-automations.js";
import { renderSceneEntityList } from "./render-scenes.js";
import { restore } from "./trash.js";

function candidatesOf(msg) {
  const seen = new Map();
  for (const call of msg.tool_calls || []) {
    if (call?.tool !== "restore_deleted") continue;
    for (const c of call.result?.candidates || []) seen.set(c.trash_id, c);
  }
  return [...seen.values()];
}

function formatDate(host, iso) {
  try {
    return new Date(iso).toLocaleString(
      host.hass?.locale?.language || host.hass?.language || undefined,
      { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" },
    );
  } catch {
    return iso;
  }
}

// Fetched once per card, so an item deleted after the panel opened is not
// judged against a list read before it existed.
async function fetchDetails(host, ids) {
  let items = [];
  try {
    ({ items } = await host.hass.callWS({
      type: "selora_ai/trash/get",
      trash_ids: ids,
    }));
  } catch (err) {
    console.error("Failed to load deleted items", err);
    return;
  }
  const found = Object.fromEntries((items || []).map((i) => [i.trash_id, i]));
  host._restoreDetails = {
    ...(host._restoreDetails || {}),
    ...Object.fromEntries(ids.map((id) => [id, found[id] || null])),
  };
  host.requestUpdate();
}

function renderAction(host, c, detail) {
  if (host._restoredTrash?.has(c.trash_id)) {
    return html`<span class="restore-card-state">
      <ha-icon icon="mdi:check-circle-outline"></ha-icon>
      ${host._t("restore_card_restored", "Restored")}
    </span>`;
  }
  if (detail === null) {
    return html`<span class="restore-card-state">
      ${host._t("restore_card_gone", "No longer in Recently deleted")}
    </span>`;
  }
  if (!detail) return "";
  return html`<button
    class="btn btn-outline btn-sm"
    ?disabled=${host._trashBusy?.[c.trash_id]}
    @click=${() => restore(host, c)}
  >
    <ha-icon icon="mdi:restore"></ha-icon>
    ${host._t("trash_restore", "Restore")}
  </button>`;
}

function renderCandidate(host, c) {
  const detail = host._restoreDetails?.[c.trash_id];
  const yamlKey = `restore_${c.trash_id}`;
  const yamlOpen = host._yamlOpen?.[yamlKey];
  return html`
    <div class="automation-subcard restore-candidate">
      <div class="automation-subcard-header">
        ${renderAutomationIdentity(c.name, c.description, {
          badge: interpolate(
            host._t("restore_card_deleted_on", "Deleted {date}"),
            { date: formatDate(host, c.deleted_at) },
          ),
        })}
      </div>
      <div class="automation-subcard-body">
        ${
          !detail
            ? ""
            : c.kind === "scene"
              ? renderSceneEntityList(host, detail.config?.entities)
              : renderAutomationFlowchart(host, detail.config)
        }
        ${detail ? renderYaml(host, detail, yamlKey, yamlOpen) : ""}
        <div class="restore-card-actions">${renderAction(host, c, detail)}</div>
      </div>
    </div>
  `;
}

function renderYaml(host, detail, yamlKey, yamlOpen) {
  return html`
    <div
      class="yaml-toggle"
      style="margin-top:12px;"
      @click=${() => {
        host._yamlOpen = {
          ...(host._yamlOpen || {}),
          [yamlKey]: !yamlOpen,
        };
        host.requestUpdate();
      }}
    >
      <ha-icon icon="mdi:code-braces" style="--mdc-icon-size:14px;"></ha-icon>
      ${
        yamlOpen
          ? host._t("automations_yaml_toggle_hide", "Hide YAML")
          : host._t("automations_yaml_toggle_view", "View YAML")
      }
    </div>
    ${
      yamlOpen
        ? html`<div style="margin-top:6px;">
            ${host._renderYamlEditor(yamlKey, detail.yaml, null, {
              readOnly: true,
            })}
          </div>`
        : ""
    }
  `;
}

export function renderRestoreCard(host, msg) {
  const candidates = candidatesOf(msg);
  if (!candidates.length) return "";
  const missing = candidates
    .map((c) => c.trash_id)
    .filter((id) => !(id in (host._restoreDetails || {})));
  host._restoreFetching = host._restoreFetching || new Set();
  const toFetch = missing.filter((id) => !host._restoreFetching.has(id));
  if (toFetch.length) {
    toFetch.forEach((id) => host._restoreFetching.add(id));
    fetchDetails(host, toFetch);
  }
  return html`<div class="restore-card">
    ${candidates.map((c) => renderCandidate(host, c))}
  </div>`;
}
