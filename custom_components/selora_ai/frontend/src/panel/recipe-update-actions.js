// Updating an installed recipe to the catalog's newer version
// (prototype-assigned to SeloraAIPanel).
//
// The backend re-runs the install with the devices and settings already on
// the install record, so an update is one click. When the new version needs
// a choice the old install never made, it halts before writing anything and
// the Overview says so; Reconfigure then opens the wizard prefilled with the
// install's choices, on the newer bundle the update already downloaded.

import { interpolate } from "../shared/i18n.js";

// Pipeline stages a halt at which means "the new version needs input", as
// opposed to a download or write that failed.
const NEEDS_CHOICE_STAGES = new Set(["resolve", "validate"]);

// Newer catalog version of an installed recipe, or null. The list and the
// catalog read report the same thing; whichever arrived first answers.
export function _recipeUpdateVersion(slug) {
  return (
    this._recipesList?.updates?.[slug] ||
    this._recipesCatalog?.updates?.[slug] ||
    null
  );
}

export async function _runRecipeUpdate(slug) {
  if (!slug || this._recipesBusy) return;
  const version = this._recipeUpdateVersion(slug);
  this._recipesBusy = true;
  this._recipeUpdateSlug = slug;
  this._recipeUpdateNotice = null;
  let result = null;
  let unsub = null;
  try {
    await new Promise((resolve, reject) => {
      this.hass.connection
        .subscribeMessage(
          (evt) => {
            const payload = evt?.event;
            if (payload?.type === "result") {
              result = payload.result;
              resolve();
            }
          },
          { type: "selora_ai/recipes/update_stream", slug },
        )
        .then((u) => {
          unsub = u;
        })
        .catch(reject);
    });
  } catch (err) {
    result = {
      ok: false,
      stage_reached: "definition",
      punch_list: [{ message: err?.message || String(err) }],
    };
  } finally {
    if (unsub) {
      try {
        unsub();
      } catch (e) {
        console.debug("update_stream unsub failed", e);
      }
    }
    this._recipesBusy = false;
    this._recipeUpdateSlug = null;
  }

  if (result?.ok) {
    this._recipeUpdateNotice = {
      slug,
      ok: true,
      message: interpolate(
        this._t("recipes_update_done", "Updated to v{version}."),
        { version: version || "" },
      ),
    };
  } else if (NEEDS_CHOICE_STAGES.has(result?.stage_reached)) {
    this._recipeUpdateNotice = {
      slug,
      ok: false,
      message: interpolate(
        this._t(
          "recipes_update_needs_choice",
          "v{version} needs a choice the current install didn't make. Reconfigure to finish the update.",
        ),
        { version: version || "" },
      ),
    };
  } else {
    const reasons = (result?.punch_list || [])
      .map((item) => item.message)
      .filter(Boolean)
      .join(" ");
    this._recipeUpdateNotice = {
      slug,
      ok: false,
      message:
        `${this._t("recipes_update_failed", "The update didn't complete.")} ${reasons}`.trim(),
    };
  }
  // Either way the bundle on disk may now be the new version, and on success
  // the record is: reload both so the Overview reads the current state. The
  // catalog is forced — its cached `updates` would keep offering this one.
  await Promise.all([this._loadRecipesList(), this._loadRecipesCatalog(true)]);
  if (this._recipeWizardSlug === slug) {
    const notice = this._recipeUpdateNotice;
    await this._openRecipeWizard(slug);
    this._recipeUpdateNotice = notice;
  }
}
