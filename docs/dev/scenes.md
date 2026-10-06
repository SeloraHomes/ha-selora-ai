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
