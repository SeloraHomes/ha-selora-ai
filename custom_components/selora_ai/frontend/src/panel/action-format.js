// Translate a ServiceCallDict into a user-facing imperative verb + past
// tense sentence + a target label. Shared between the approval card
// (proposal phase, present tense) and the Done bubble (executed phase,
// past tense + entity tile) so both surfaces speak the same English.

const DOMAIN_ICONS = {
  light: "mdi:lightbulb",
  switch: "mdi:toggle-switch",
  scene: "mdi:palette",
  cover: "mdi:window-shutter",
  fan: "mdi:fan",
  climate: "mdi:thermostat",
  input_boolean: "mdi:toggle-switch-outline",
  media_player: "mdi:speaker",
  lock: "mdi:lock",
  alarm_control_panel: "mdi:shield-home",
  vacuum: "mdi:robot-vacuum",
  water_heater: "mdi:water-boiler",
  tts: "mdi:account-voice",
  notify: "mdi:bell",
  script: "mdi:script-text-play",
  shell_command: "mdi:console",
};

// Imperative + past forms for each REVIEW (and selected SAFE) service.
// Keys are full ``<domain>.<verb>`` strings, matched first so a
// service-specific override beats the domain fallback below.
const SERVICE_FORMS = {
  "lock.lock": { imperative: "Lock", past: "Locked" },
  "lock.unlock": { imperative: "Unlock", past: "Unlocked" },
  "lock.open": { imperative: "Open", past: "Opened" },
  "tts.cloud_say": { imperative: "Announce on", past: "Announced on" },
  "tts.google_translate_say": {
    imperative: "Announce on",
    past: "Announced on",
  },
  "tts.speak": { imperative: "Announce on", past: "Announced on" },
  "alarm_control_panel.alarm_arm_home": {
    imperative: "Arm (home mode)",
    past: "Armed (home mode)",
  },
  "alarm_control_panel.alarm_arm_away": {
    imperative: "Arm (away mode)",
    past: "Armed (away mode)",
  },
  "alarm_control_panel.alarm_arm_night": {
    imperative: "Arm (night mode)",
    past: "Armed (night mode)",
  },
  "alarm_control_panel.alarm_disarm": {
    imperative: "Disarm",
    past: "Disarmed",
  },
  "vacuum.start": { imperative: "Start", past: "Started" },
  "vacuum.pause": { imperative: "Pause", past: "Paused" },
  "vacuum.stop": { imperative: "Stop", past: "Stopped" },
  "vacuum.return_to_base": {
    imperative: "Send to dock",
    past: "Sent to dock",
  },
  "vacuum.clean_spot": {
    imperative: "Spot-clean with",
    past: "Spot-cleaned with",
  },
  "water_heater.set_temperature": {
    imperative: "Set temperature on",
    past: "Updated temperature on",
  },
  "water_heater.set_operation_mode": {
    imperative: "Change mode on",
    past: "Changed mode on",
  },
  // SAFE-bucket services that can appear in a bundled approval (the
  // policy holds an entire turn back until the user clicks through
  // the REVIEW call). Past tense matters here because these may also
  // run via the Done message synthesizer below.
  "light.turn_on": { imperative: "Turn on", past: "Turned on" },
  "light.turn_off": { imperative: "Turn off", past: "Turned off" },
  "light.toggle": { imperative: "Toggle", past: "Toggled" },
  "switch.turn_on": { imperative: "Turn on", past: "Turned on" },
  "switch.turn_off": { imperative: "Turn off", past: "Turned off" },
  "scene.turn_on": { imperative: "Activate", past: "Activated" },
};

const DOMAIN_FORMS = {
  tts: { imperative: "Announce on", past: "Announced on" },
  notify: {
    imperative: "Send a notification via",
    past: "Sent a notification via",
  },
  script: { imperative: "Run script", past: "Ran script" },
  shell_command: {
    imperative: "Run shell command",
    past: "Ran shell command",
  },
};

function _domainOf(s) {
  return (s || "").split(".", 1)[0];
}

function _serviceSuffix(s) {
  const parts = (s || "").split(".");
  return parts.length > 1 ? parts.slice(1).join(".") : "";
}

function _friendlyName(host, entityId) {
  return host?.hass?.states?.[entityId]?.attributes?.friendly_name || entityId;
}

export function actionIcon(service) {
  return DOMAIN_ICONS[_domainOf(service)] || "mdi:cog-play-outline";
}

// Return ``{ verb, pastVerb, targetText, entityIds }`` for one call.
// ``entityIds`` is empty when the service has no entity target (notify
// channels, scripts, shell_commands). Callers that want to render an
// entity tile should consult ``entityIds``; ``targetText`` covers the
// non-entity fallback case.
export function describeCall(host, call) {
  const service = call?.service || "";
  const target = call?.target?.entity_id;
  const ids = Array.isArray(target) ? target : target ? [target] : [];

  const forms =
    SERVICE_FORMS[service] || DOMAIN_FORMS[_domainOf(service)] || null;
  // describeCall is reachable from approval cards that pass a stub `host`
  // (no `_t`) when the proposal arrives before the LitElement is fully
  // wired. Guard the helper call so an unknown service like an elevated
  // `cover.open_cover` doesn't throw and blank the card.
  const t =
    typeof host?._t === "function" ? (k, fb) => host._t(k, fb) : (_k, fb) => fb;
  const imperative = forms?.imperative || t("action_format_run_verb", "Run");
  const pastVerb = forms?.past || t("action_format_ran_verb", "Ran");

  if (ids.length) {
    const names = ids.map((eid) => _friendlyName(host, eid));
    return {
      verb: imperative,
      pastVerb,
      targetText: names.join(", "),
      entityIds: ids,
    };
  }

  // No entity target — the service tail (notify.mobile_app_pixel →
  // "mobile_app_pixel", script.bedtime → "bedtime") IS the target.
  const tail = _serviceSuffix(service);
  return {
    verb: imperative,
    pastVerb,
    targetText: tail || service,
    entityIds: [],
  };
}
