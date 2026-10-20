# Recently deleted

Deleted automations and scenes are kept for `TRASH_DAYS` (at most
`TRASH_MAX_PER_KIND` per kind) and listed under a "Recently deleted" filter tab
on the Automations and Scenes tabs, where Restore puts one back exactly as it
was (`trash.py`, `panel/trash.js`). The filter tab shows only while something
is in it, and is the whole list once every live automation or scene is gone.

- **Only a delete the user asked for is kept.** The delete helpers take
  `trash=True` (`async_delete_automation`, `async_delete_automations_batch`,
  `async_remove_scene_yaml`, `async_remove_yaml_scene_by_entity`); the panel,
  chat/MCP and the stale-automation auto-purge pass it. A clean-up that removes
  its own temporary automation (scheduled actions) does not, or the trash fills
  with things nobody deleted. **A new user-facing delete path must pass it.**
- **An auto-purge never pushes out the user's own deletes.** The auto-purge also
  passes `automatic=True`, which marks the trash item (`automatic`) and the
  retired record (`retired_automatically`); both prunes count marked entries
  apart, each up to the per-kind cap, so a burst of purges can't evict an
  automation the user deleted by mistake or drop its history.
- **The entry is kept as written,** not as Selora would regenerate it, so
  someone else's automation, an id-less one, or a scene with editor metadata
  comes back unchanged. The list command never sends it to the panel.
- **A restore never takes an id in use** (`in_use`): two automations under one
  id load as one. The item stays in the trash.
- **A Selora automation gets its history back.** A trashed delete retires the
  record (`retire_record`) instead of purging it, and the retired window is the
  same as the trash's, so a restore always finds it; Delete forever purges it
  (`purge_retired`). A restore also forgets the deletion hash, or the collector
  would never suggest it again.
- **A Selora scene revives through the scene store's reconcile,** which clears
  `deleted_at` when its id is back in `scenes.yaml`.
- **The confirmation copy says so.** Automation and scene deletes tell the user
  they can restore (`delete_pending_hint(restorable=True)`, the card's
  `delete_approval_warning_restorable`, the tab confirms); every other delete
  kind is still permanent and still says it.
- **Asking to restore never restores.** `restore_deleted` (chat and MCP, one
  `ToolDef`) finds matches (`trash.async_find`: any name or description word,
  short ones included, best match then newest). Chat turns them into a card
  with each match's flow or scene devices, its YAML, when it was deleted, and a
  Restore button (`panel/restore-card.js`), so the user picks: same-named
  automations are what fill the trash. MCP lists the same matches in full and
  restores only the `trash_id` the caller comes back with.
- **The chat result carries names and dates only.** A tool result over the
  size limit is trimmed by dropping list entries, which would take a long
  automation's Restore button with it; the card fetches each match itself
  (`selora_ai/trash/get`), which also tells it what is still kept.
- **The chat tool is `panel_only`**: the card is the restore, and Assist has
  no card to show.
