# Scenes

`scene_utils.validate_scene_payload` decides what a scene holds;
`scene_state_mapper.validate_entity_states` normalizes each entity's state.

- **Which domains: Home Assistant's answer, not a list of ours.** A scene is
  applied through each domain's `reproduce_state` platform, so
  `entity_capabilities.scene_exclusion` asks the loaded integration
  (`platforms_exists(("reproduce_state",))`), falling back to the core list
  (`_CORE_REPRODUCIBLE_DOMAINS`) without hass or for an unloaded one. A fixed
  six-domain list dropped dropdowns, number helpers and selects from scenes.
- **Locks and alarms stay out** (`_SCENE_SECURITY_DOMAINS`): HA could set them,
  but activating a scene would then unlock or disarm with none of the approval
  those need.
- **Device setting switches stay out** (`is_actionable_entity`'s patterns): a
  camera's privacy mode swept into a room's scene would quietly stop it
  recording. The patterns are names, so a real light called "floodlight" is
  caught too — which is why exclusions are reported, not silent.
- **Nothing is left out silently.** `scene_left_out` names each excluded
  entity with its reason; MCP validate/create return it as `left_out`.
- **Schemas for the six original domains are complete** — they list every
  attribute those `reproduce_state`s use, so other keys (brightness on a
  switch) are dropped as noise. A domain without a schema of ours passes its
  `state` and attributes through, bounded (`_passthrough`).

## Changing a scene

`scene_utils.async_edit_scene_yaml` changes one scenes.yaml entry IN PLACE —
name, states, icon — and is what the panel's rename and MCP
`selora_update_scene` both run.

- **Never rebuilt through `async_create_scene`.** Home Assistant's editor stores
  `icon` and `metadata` beside the entities, and entities carry extras of their
  own; a rebuilt entry keeps only id/name/entities. A rename also must not
  re-validate members nobody mentioned (an unpaired bulb would block it).
- **New states are checked as a new scene's are** — `validate_scene_payload`,
  the security check, entities that exist — before the file is touched.
  `metadata` for an entity no longer in the scene is dropped.
- **The entity_id survives** (the YAML `id` is the registry unique_id), so an
  edit does not break what activates the scene — the reason it is not a delete
  and a create. A scene from an integration (registry platform not
  `homeassistant`) is not in scenes.yaml and is refused.
- **Applied, or rolled back.** `scene.reload` swallows a config that does not
  parse, so what was written is compared with what the platform loaded: the
  name (`_rename_applied`, from the registry) and the icon, members and each
  state (`_loaded_matches`, from the scene entity's `scene_config`). Checking
  the name and member set alone passed an icon-only change HA never read.
- **The copies follow** (`async_propagate_scene_edit`): the SceneStore record,
  chat sessions naming the scene, and Assist's in-memory copy.
