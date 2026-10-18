// "Recently deleted", a filter tab in the Automations and Scenes tabs
// (backend: trash.py). Every delete the user asked for lands there for a while, and a
// restore puts the entry back exactly as it was. Loaded alongside each tab's
// list, which reloads after every delete and restore.
import { html } from "lit";
import { interpolate } from "../shared/i18n.js";

export async function loadTrash(host) {
  try {
    host._trash =
      (await host.hass.callWS({ type: "selora_ai/trash/list" })) || [];
  } catch (err) {
    // An older backend without the command just has nothing to show.
    host._trash = [];
  }
}

function reloadTab(host, kind) {
  return kind === "scene" ? host._loadScenes() : host._loadAutomations();
}

async function withBusy(host, item, work) {
  host._trashBusy = { ...(host._trashBusy || {}), [item.trash_id]: true };
  try {
    await work();
  } finally {
    host._trashBusy = { ...host._trashBusy, [item.trash_id]: false };
  }
}

async function restore(host, item) {
  const values = { name: item.name };
  await withBusy(host, item, async () => {
    try {
      await host.hass.callWS({
        type: "selora_ai/trash/restore",
        trash_id: item.trash_id,
      });
      host._showToast(
        interpolate(host._t("trash_restored", "Restored “{name}”."), values),
        "success",
      );
    } catch (err) {
      host._showToast(
        err?.code === "in_use"
          ? interpolate(
              host._t(
                "trash_restore_in_use",
                "Couldn't restore “{name}”: another one now uses its id.",
              ),
              values,
            )
          : interpolate(
              host._t(
                "trash_restore_failed",
                "Couldn't restore “{name}”: {error}",
              ),
              { ...values, error: err?.message || "" },
            ),
        "error",
      );
    }
    await reloadTab(host, item.kind);
  });
}

async function deleteForever(host, item) {
  const values = { name: item.name };
  if (
    !confirm(
      interpolate(
        host._t(
          "trash_delete_forever_confirm",
          "Delete “{name}” for good? This can't be undone.",
        ),
        values,
      ),
    )
  )
    return;
  await withBusy(host, item, async () => {
    try {
      await host.hass.callWS({
        type: "selora_ai/trash/delete",
        trash_id: item.trash_id,
      });
    } catch (err) {
      host._showToast(
        interpolate(
          host._t("trash_delete_failed", "Couldn't delete “{name}”: {error}"),
          { ...values, error: err?.message || "" },
        ),
        "error",
      );
    }
    await loadTrash(host);
  });
}

function formatDate(host, iso) {
  try {
    return new Date(iso).toLocaleDateString(
      host.hass?.locale?.language || host.hass?.language || undefined,
      { day: "numeric", month: "short" },
    );
  } catch {
    return iso.slice(0, 10);
  }
}

export function trashItems(host, kind) {
  return (host._trash || []).filter((i) => i.kind === kind);
}

// The "Recently deleted (n)" filter tab, beside the tab's other filters. Shown
// only while something is in it.
export function renderTrashTab(host, kind, active, onSelect) {
  const count = trashItems(host, kind).length;
  if (!count) return "";
  return html`<button
    role="tab"
    aria-selected=${active}
    class="filter-tab ${active ? "active" : ""}"
    @click=${onSelect}
  >
    <ha-icon
      icon="mdi:delete-clock-outline"
      style="--mdc-icon-size:14px;display:block;"
    ></ha-icon>
    <span>${host._t("trash_heading", "Recently deleted")} (${count})</span>
  </button>`;
}

// What the tab lists in place of the live ones while it is selected.
export function renderTrashList(host, kind) {
  const busy = host._trashBusy || {};
  return html`
    <div class="automations-list">
      ${trashItems(host, kind).map(
        (item) => html`
          <div class="trash-row">
            <div class="trash-row-text">
              <div class="trash-name">${item.name}</div>
              <div class="trash-meta">
                ${interpolate(
                  host._t("trash_kept_until", "Kept until {date}"),
                  { date: formatDate(host, item.expires_at) },
                )}
              </div>
            </div>
            <button
              class="btn btn-outline btn-sm"
              ?disabled=${busy[item.trash_id]}
              @click=${() => restore(host, item)}
            >
              <ha-icon icon="mdi:restore"></ha-icon>
              ${host._t("trash_restore", "Restore")}
            </button>
            <button
              class="trash-forever"
              title=${host._t("trash_delete_forever", "Delete forever")}
              aria-label=${host._t("trash_delete_forever", "Delete forever")}
              ?disabled=${busy[item.trash_id]}
              @click=${() => deleteForever(host, item)}
            >
              <ha-icon icon="mdi:delete-forever-outline"></ha-icon>
            </button>
          </div>
        `,
      )}
    </div>
  `;
}
