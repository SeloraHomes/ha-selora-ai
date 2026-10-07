# Recipe updates

A newer catalog version of an installed recipe reaches a home as an `update`
entity (Settings → Updates) and as an Update button on the recipe's Overview.
Both run `async_update_recipe` (`recipes/updates.py`): download the new bundle,
then the ordinary install with the record's bindings and inputs.

- **The record's choices are the update's input.** Resolver inputs (a TV's MAC)
  are recomputed, as on a first install; everything else comes from the record.
  A new version that needs a role, input or integration the old install never
  had halts at resolve/validate before anything is written. The entity raises
  and points at the panel; the Overview says so and Reconfigure opens the wizard
  prefilled from the record.
- **The download lands before the install runs.** A halted update leaves the new
  bundle staged and the old package live, which is what the wizard needs to
  finish it. The record keeps the old version until an install succeeds.
- **The dashboard step is skipped.** The placed card keeps pointing at the same
  entities; re-placing it cannot tell "the default dashboard" from "the
  manifest's target".
- **Ownership claims carry forward on every reinstall.** `integrations_installed`
  is merged with the previous record's (minus entries since deleted). A run that
  sets nothing up would otherwise blank it, and uninstall would stop offering to
  remove what the recipe created.
- **One source of truth for "newer".** `RecipeUpdateChecker` holds the latest
  compatible catalog entries; the periodic check and the panel's own catalog
  read both feed it, so the panel and Settings → Updates agree. A dev catalog
  override is not fed in: it would advertise versions production lacks.
- **No catalog reads without an installed recipe**, and the first read is a
  timer, never a sleeping task (see the startup rule in `CLAUDE.md`).
- Release notes come from the catalog entry's optional `changelog` (the recipe's
  CHANGELOG.md); the summary is that version's section, cut to HA's 255 chars.
