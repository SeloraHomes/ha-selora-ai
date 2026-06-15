// In-place scene editing (prototype-assigned to SeloraAIPanel).
//
// The "Scene sets" tile on each scene row is a live HA tile whose
// hass.callService is intercepted in _hydrateEntityChips: instead of
// driving the real device, the service call is translated into a change
// to the scene's *desired* state. Edits accumulate in host._sceneEdits
// (keyed by scene_id) until the user saves or discards.

// Translate a tile's service call into the scene state patch it implies.
// Returns the new target state object for the entity, or null when the
// call carries no persistable state change (e.g. cover.stop).
function _sceneStateFromService(domain, service, data, prev) {
  const base = { ...(prev || {}) };

  switch (domain) {
    case "light": {
      if (service === "turn_off") return { state: "off" };
      if (service === "toggle") {
        return base.state === "on"
          ? { state: "off" }
          : { ...base, state: "on" };
      }
      // turn_on (with or without attributes)
      const next = { ...base, state: "on" };
      if (data.brightness_pct != null) {
        next.brightness = Math.round((Number(data.brightness_pct) / 100) * 255);
        delete next.brightness_pct;
      }
      if (data.brightness != null) next.brightness = Number(data.brightness);
      if (data.color_temp != null) next.color_temp = Number(data.color_temp);
      if (data.color_temp_kelvin != null)
        next.color_temp_kelvin = Number(data.color_temp_kelvin);
      if (data.rgb_color != null) next.rgb_color = data.rgb_color;
      if (data.hs_color != null) next.hs_color = data.hs_color;
      if (data.xy_color != null) next.xy_color = data.xy_color;
      if (data.effect != null) next.effect = data.effect;
      return next;
    }

    case "cover": {
      if (service === "open_cover")
        return { ...base, state: "open", current_position: 100 };
      if (service === "close_cover")
        return { ...base, state: "closed", current_position: 0 };
      if (service === "set_cover_position" && data.position != null) {
        const p = Number(data.position);
        return {
          ...base,
          state: p > 0 ? "open" : "closed",
          current_position: p,
        };
      }
      return null; // stop_cover and others — no target change
    }

    case "fan": {
      if (service === "turn_off") return { state: "off" };
      if (service === "turn_on") return { ...base, state: "on" };
      if (service === "set_percentage" && data.percentage != null) {
        const p = Number(data.percentage);
        return { ...base, state: p > 0 ? "on" : "off", percentage: p };
      }
      if (service === "set_preset_mode" && data.preset_mode != null) {
        return { ...base, state: "on", preset_mode: data.preset_mode };
      }
      return null;
    }

    case "media_player": {
      if (service === "turn_off") return { state: "off" };
      if (service === "turn_on") return { ...base, state: "on" };
      if (service === "media_play") return { ...base, state: "playing" };
      if (service === "media_pause") return { ...base, state: "paused" };
      if (service === "media_stop") return { ...base, state: "idle" };
      if (service === "volume_set" && data.volume_level != null)
        return { ...base, volume_level: Number(data.volume_level) };
      if (service === "volume_mute" && data.is_volume_muted != null)
        return { ...base, is_volume_muted: data.is_volume_muted };
      if (service === "select_source" && data.source != null)
        return { ...base, source: data.source };
      return null;
    }

    case "switch":
    case "input_boolean":
    case "humidifier": {
      if (service === "turn_off") return { state: "off" };
      if (service === "turn_on") return { ...base, state: "on" };
      if (service === "toggle")
        return { ...base, state: base.state === "on" ? "off" : "on" };
      return null;
    }

    case "lock": {
      if (service === "lock") return { ...base, state: "locked" };
      if (service === "unlock") return { ...base, state: "unlocked" };
      return null;
    }

    case "climate": {
      const next = { ...base };
      if (data.temperature != null) next.temperature = Number(data.temperature);
      if (data.hvac_mode != null) next.state = data.hvac_mode;
      if (service === "turn_off") next.state = "off";
      return next;
    }

    default:
      return null;
  }
}

// Drop null/undefined attribute values from each entity's state. HA's
// scene.apply (and scenes.yaml validation) rejects null-valued attrs
// like a fan's `direction: null`, which entities legitimately report
// when the feature is inactive. State strings are always kept.
function _cleanSceneEntities(entities) {
  const out = {};
  for (const [id, st] of Object.entries(entities || {})) {
    const clean = {};
    for (const [k, v] of Object.entries(st || {})) {
      if (k === "state" || v != null) clean[k] = v;
    }
    out[id] = clean;
  }
  return out;
}

// Current working entities for a scene: the edited copy if one exists,
// otherwise the scene's saved entities. Always returns a fresh object so
// callers can mutate without touching the stored scene.
export function _sceneEditedEntities(sceneId) {
  const edited = this._sceneEdits?.[sceneId];
  if (edited) return edited;
  const scene = (this._scenes || []).find((s) => s.scene_id === sceneId);
  return scene?.entities || {};
}

// Called from the intercepted tile callService. The entity is passed
// explicitly by the hydrator (the grid is single-entity) since HA tile
// features put the entity in the call's target arg, not in data.
export function _applySceneTileEdit(sceneId, entityId, domain, service, data) {
  if (!sceneId || !entityId) return;

  const current = this._sceneEditedEntities(sceneId);
  const prev = current[entityId];
  const next = _sceneStateFromService(domain, service, data || {}, prev);
  if (!next) return; // no persistable change

  // Dedupe: if the call doesn't actually change the target, do nothing.
  // HA tile features can re-issue a callService while reconciling their
  // display against the (forced) hass we feed them; without this guard
  // each call would requestUpdate → re-hydrate → reset the tile's hass
  // → another call, an unbounded loop that freezes the UI.
  if (prev && JSON.stringify(prev) === JSON.stringify(next)) return;

  this._sceneEdits = {
    ...this._sceneEdits,
    [sceneId]: { ...current, [entityId]: next },
  };
  this.requestUpdate();
}

export function _sceneIsDirty(sceneId) {
  return !!this._sceneEdits?.[sceneId];
}

function _yamlScalar(v) {
  if (typeof v === "number") return String(v);
  if (typeof v === "boolean") return v ? "true" : "false";
  if (Array.isArray(v)) return `[${v.map(_yamlScalar).join(", ")}]`;
  return `'${String(v).replace(/'/g, "''")}'`;
}

// Serialize the working (edited) scene to a YAML preview matching the
// backend's scenes.yaml format. Used for the live "unsaved preview" so
// the YAML reflects edits before they're persisted.
export function _sceneEditYaml(sceneId, displayName) {
  const entities = this._sceneEditedEntities(sceneId);
  const name = String(displayName || "").replace(/^\[Selora AI\]\s*/, "");
  const lines = [
    "# Unsaved preview — Save changes to apply.",
    `name: '[Selora AI] ${name.replace(/'/g, "''")}'`,
    "entities:",
  ];
  for (const [id, st] of Object.entries(entities)) {
    lines.push(`  ${id}:`);
    if ("state" in st) lines.push(`    state: ${_yamlScalar(st.state)}`);
    for (const [k, val] of Object.entries(st)) {
      if (k === "state") continue;
      lines.push(`    ${k}: ${_yamlScalar(val)}`);
    }
  }
  return lines.join("\n");
}

export async function _saveSceneEdits(sceneId) {
  const submitted = this._sceneEdits?.[sceneId];
  if (!submitted) return;
  this._savingScene = { ...this._savingScene, [sceneId]: true };
  this.requestUpdate();
  try {
    await this.hass.callWS({
      type: "selora_ai/save_scene_edits",
      scene_id: sceneId,
      entities: _cleanSceneEntities(submitted),
    });
    // Only clear if the user didn't edit again mid-save. Each edit
    // replaces the entry with a fresh object (see _applySceneTileEdit),
    // so an unchanged reference means nothing new was added.
    if (this._sceneEdits?.[sceneId] === submitted) {
      const next = { ...this._sceneEdits };
      delete next[sceneId];
      this._sceneEdits = next;
    }
    await this._loadScenes();
    this._showToast("Scene updated.", "success");
  } catch (err) {
    this._showToast("Failed to save scene: " + err.message, "error");
  } finally {
    this._savingScene = { ...this._savingScene, [sceneId]: false };
    this.requestUpdate();
  }
}

// Apply the working (edited) scene states to the real devices without
// saving, using HA's scene.apply service. Lets the user preview the
// scene live before committing. Changes the actual devices — there's no
// automatic revert (HA has no built-in undo for scene.apply).
export async function _testSceneEdits(sceneId) {
  const entities = this._sceneEditedEntities(sceneId);
  if (!entities || !Object.keys(entities).length) return;
  this._testingScene = { ...this._testingScene, [sceneId]: true };
  this.requestUpdate();
  try {
    // Apply via the backend WS (not scene.apply directly): it strips
    // null attributes server-side so a fan's direction: null can't make
    // the apply fail, and centralises admin validation.
    await this.hass.callWS({
      type: "selora_ai/apply_scene_states",
      entities: _cleanSceneEntities(entities),
    });
    this._showToast("Applied to your devices (not saved).", "success");
  } catch (err) {
    this._showToast("Failed to test scene: " + err.message, "error");
  } finally {
    this._testingScene = { ...this._testingScene, [sceneId]: false };
    this.requestUpdate();
  }
}

export function _discardSceneEdits(sceneId) {
  if (!this._sceneEdits?.[sceneId]) return;
  const next = { ...this._sceneEdits };
  delete next[sceneId];
  this._sceneEdits = next;
  this.requestUpdate();
}
