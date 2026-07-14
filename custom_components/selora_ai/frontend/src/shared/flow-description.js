// ---------------------------------------------------------------------------
// Unified flow item description (triggers, conditions, actions)
// ---------------------------------------------------------------------------
// Replaces panel's _describeFlowItem and card's _formatTrigger/_formatAction
// with a single shared implementation.
// ---------------------------------------------------------------------------

import {
  humanizeToken,
  fmtEntity,
  fmtEntities,
  fmtState,
  fmtDuration,
  fmtWeekdays,
  fmtNumericValue,
  fmtTime,
} from "./formatting.js";

// Normalize a value that HA allows as either a single mapping or a list
// (conditions, sequence, triggers, actions, ...) into an array. `x || []`
// only guards null/undefined — a lone object slips through and later `.map`
// / `.length` throws. Falsy -> [], array -> as-is, scalar/object -> [x].
export function asArray(v) {
  return Array.isArray(v) ? v : v == null || v === false ? [] : [v];
}

// Phrase catalog. Each template is a function so we can interpolate vars
// without runtime string-templating. Adding a locale: copy the EN block,
// translate, keep the same keys + arg order. Missing keys fall back to EN
// (acceptable — uncommon shapes stay English instead of breaking).
const PHRASES = {
  en: {
    when_time_is: (t) => `When the time is ${t}`,
    when_time_is_any: (list) => `When the time is ${list}`,
    or: " or ",
    when_it_is: (ev) => `When it is ${ev}`,
    sun_offset: (label, neg, ev) =>
      `${label} ${neg ? "before" : "after"} ${ev}`,
    sunset: "sunset",
    sunrise: "sunrise",
    when_turns_on: (eid, dur) => `When ${eid} turns on${dur}`,
    when_turns_off: (eid, dur) => `When ${eid} turns off${dur}`,
    when_changes_from_to: (eid, from, to, dur) =>
      `When ${eid} changes from ${from} to ${to}${dur}`,
    when_becomes: (eid, st, dur) => `When ${eid} becomes ${st}${dur}`,
    when_changes_state: (eid, dur) => `When ${eid} changes state${dur}`,
    for_duration: (d) => ` for ${d}`,
    when_between: (eid, a, b) => `When ${eid} is between ${a} and ${b}`,
    when_rises_above: (eid, v) => `When ${eid} rises above ${v}`,
    when_drops_below: (eid, v) => `When ${eid} drops below ${v}`,
    when_value_changes: (eid) => `When ${eid} value changes`,
    when_ha: (ev) => `When Home Assistant ${ev}`,
    ha_starts: "starts",
    ha_shuts_down: "shuts down",
    ha_changes_state: "changes state",
    every_seconds: (n) => `Every ${n} second${Number(n) === 1 ? "" : "s"}`,
    every_minutes: (n) => `Every ${n} minute${Number(n) === 1 ? "" : "s"}`,
    every_hours: (n) => `Every ${n} hour${Number(n) === 1 ? "" : "s"}`,
    on_time_pattern: "On a time pattern",
    when_template_entity: (e) => `When ${e} condition is met`,
    when_template_met: "When a template condition is met",
    when_event: (n) => `When ${n} happens`,
    when_event_generic: "When an event happens",
    when_device_triggered: (t) => `When a device ${t}`,
    when_device_is: (t) => `When a device is ${t}`,
    triggered: "triggered",
    zone_enters: "enters",
    zone_leaves: "leaves",
    when_mqtt_topic: (t) => `When a device message arrives (${t})`,
    when_mqtt: "When a device message arrives",
    when_webhook: "When an outside service sends an update",
    when_tag: (id) => `When a tag is scanned${id ? ` (${id})` : ""}`,
    when_geo: "When a location update is received",
    when_calendar: (ev, entity) => `When a calendar ${ev} begins${entity}`,
    calendar_event: "event",
    on_entity: (e) => ` on ${e}`,
    when_trigger_happens: "When this trigger happens",
    cond_is: (eid, st) => `${eid} is ${st}`,
    cond_between: (eid, a, b) => `${eid} between ${a} and ${b}`,
    cond_above: (eid, v) => `${eid} above ${v}`,
    cond_below: (eid, v) => `${eid} below ${v}`,
    cond_numeric: (eid) => `${eid} numeric check`,
    cond_after_time: (t) => `after ${t}`,
    cond_before_time: (t) => `before ${t}`,
    cond_on_weekday: (d) => `on ${d}`,
    cond_time_window: "Time window",
    cond_template_true: "Template evaluates to true",
    cond_after_sun: (s) => `after ${s}`,
    cond_before_sun: (s) => `before ${s}`,
    cond_sun_position: "Sun position",
    cond_all: (n) => `All ${n} conditions must be true`,
    cond_any: (n) => `Any of ${n} conditions is true`,
    cond_none: "None of the conditions are true",
    cond_in_zone: (eid, z) => `${eid} is in ${z || "zone"}`,
    cond_device: "Device condition",
    notify_quoted: (q) => `Notify: "${q}"`,
    notify_target: (tgt, q) => `Notify ${tgt}: "${q}"`,
    notify_via: (tgt) => `Notify via ${tgt}`,
    send_notification: "Send a notification",
    say_quoted: (q) => `Say: "${q}"`,
    tts: "Text-to-speech",
    action_turn_on: "Turn on",
    action_turn_off: "Turn off",
    action_toggle: "Toggle",
    action_lock: "Lock",
    action_unlock: "Unlock",
    action_open_cover: "Open",
    action_close_cover: "Close",
    action_set_temperature: "Set temperature for",
    action_set_value: "Set value for",
    action_send_command: "Send command to",
    action_reload: "Reload",
    extra_brightness: (v) => `at ${v}%`,
    extra_temp: (v) => `to ${v}°`,
    extra_color_temp: (v) => `color temp ${v}`,
    wait_str: (d) => `Wait ${d}`,
    wait_parts: (p) => `Wait ${p}`,
    wait_plain: "Wait",
    wait_until: "Wait until condition is met",
    wait_for_trigger: "Wait for a trigger",
    activate_scene: (e) => `Activate scene: ${e}`,
    choose_between: (n) => `Choose between ${n} option${n !== 1 ? "s" : ""}`,
    repeat_count: (n) => `Repeat ${n} time${n !== 1 ? "s" : ""}`,
    repeat_while: "Repeat while condition holds",
    repeat_until: "Repeat until condition is met",
    repeat: "Repeat",
    parallel: (n) => `Run ${n} actions in parallel`,
    sequence: (n) => `Run a sequence of ${n} steps`,
    set_variables: "Set variables",
    stop_label: (s) => `Stop: ${s}`,
    fire_event: (e) => `Fire event: ${e}`,
    automation_step: "Automation step",
    joiner_dot: " · ",
  },
  fr: {
    when_time_is: (t) => `Quand l'heure est ${t}`,
    when_time_is_any: (list) => `Quand l'heure est ${list}`,
    or: " ou ",
    when_it_is: (ev) => `Quand c'est ${ev}`,
    sun_offset: (label, neg, ev) => `${label} ${neg ? "avant" : "après"} ${ev}`,
    sunset: "le coucher du soleil",
    sunrise: "le lever du soleil",
    when_turns_on: (eid, dur) => `Quand ${eid} s'allume${dur}`,
    when_turns_off: (eid, dur) => `Quand ${eid} s'éteint${dur}`,
    when_changes_from_to: (eid, from, to, dur) =>
      `Quand ${eid} passe de ${from} à ${to}${dur}`,
    when_becomes: (eid, st, dur) => `Quand ${eid} devient ${st}${dur}`,
    when_changes_state: (eid, dur) => `Quand ${eid} change d'état${dur}`,
    for_duration: (d) => ` pendant ${d}`,
    when_between: (eid, a, b) => `Quand ${eid} est entre ${a} et ${b}`,
    when_rises_above: (eid, v) => `Quand ${eid} dépasse ${v}`,
    when_drops_below: (eid, v) => `Quand ${eid} descend sous ${v}`,
    when_value_changes: (eid) => `Quand la valeur de ${eid} change`,
    when_ha: (ev) => `Quand Home Assistant ${ev}`,
    ha_starts: "démarre",
    ha_shuts_down: "s'arrête",
    ha_changes_state: "change d'état",
    every_seconds: (n) =>
      `Toutes les ${n} seconde${Number(n) === 1 ? "" : "s"}`,
    every_minutes: (n) => `Toutes les ${n} minute${Number(n) === 1 ? "" : "s"}`,
    every_hours: (n) => `Toutes les ${n} heure${Number(n) === 1 ? "" : "s"}`,
    on_time_pattern: "Selon un schéma temporel",
    when_template_entity: (e) => `Quand la condition sur ${e} est vraie`,
    when_template_met: "Quand une condition modèle est vraie",
    when_event: (n) => `Quand ${n} se produit`,
    when_event_generic: "Quand un événement se produit",
    when_device_triggered: (t) => `Quand un appareil ${t}`,
    when_device_is: (t) => `Quand un appareil est ${t}`,
    triggered: "déclenché",
    zone_enters: "entre dans",
    zone_leaves: "quitte",
    when_mqtt_topic: (t) => `Quand un message d'appareil arrive (${t})`,
    when_mqtt: "Quand un message d'appareil arrive",
    when_webhook: "Quand un service externe envoie une mise à jour",
    when_tag: (id) => `Quand un tag est scanné${id ? ` (${id})` : ""}`,
    when_geo: "Quand une mise à jour de position est reçue",
    when_calendar: (ev, entity) =>
      `Quand un ${ev} de calendrier commence${entity}`,
    calendar_event: "événement",
    on_entity: (e) => ` sur ${e}`,
    when_trigger_happens: "Quand ce déclencheur se produit",
    cond_is: (eid, st) => `${eid} est ${st}`,
    cond_between: (eid, a, b) => `${eid} entre ${a} et ${b}`,
    cond_above: (eid, v) => `${eid} au-dessus de ${v}`,
    cond_below: (eid, v) => `${eid} en dessous de ${v}`,
    cond_numeric: (eid) => `vérification numérique de ${eid}`,
    cond_after_time: (t) => `après ${t}`,
    cond_before_time: (t) => `avant ${t}`,
    cond_on_weekday: (d) => `le ${d}`,
    cond_time_window: "Fenêtre temporelle",
    cond_template_true: "Le modèle est évalué à vrai",
    cond_after_sun: (s) => `après ${s}`,
    cond_before_sun: (s) => `avant ${s}`,
    cond_sun_position: "Position du soleil",
    cond_all: (n) => `Les ${n} conditions doivent être vraies`,
    cond_any: (n) => `L'une des ${n} conditions est vraie`,
    cond_none: "Aucune des conditions n'est vraie",
    cond_in_zone: (eid, z) => `${eid} est dans ${z || "la zone"}`,
    cond_device: "Condition d'appareil",
    notify_quoted: (q) => `Notification : « ${q} »`,
    notify_target: (tgt, q) => `Notifier ${tgt} : « ${q} »`,
    notify_via: (tgt) => `Notifier via ${tgt}`,
    send_notification: "Envoyer une notification",
    say_quoted: (q) => `Dire : « ${q} »`,
    tts: "Synthèse vocale",
    action_turn_on: "Allumer",
    action_turn_off: "Éteindre",
    action_toggle: "Basculer",
    action_lock: "Verrouiller",
    action_unlock: "Déverrouiller",
    action_open_cover: "Ouvrir",
    action_close_cover: "Fermer",
    action_set_temperature: "Régler la température de",
    action_set_value: "Définir la valeur de",
    action_send_command: "Envoyer la commande à",
    action_reload: "Recharger",
    extra_brightness: (v) => `à ${v}%`,
    extra_temp: (v) => `à ${v}°`,
    extra_color_temp: (v) => `temp. de couleur ${v}`,
    wait_str: (d) => `Attendre ${d}`,
    wait_parts: (p) => `Attendre ${p}`,
    wait_plain: "Attendre",
    wait_until: "Attendre que la condition soit vraie",
    wait_for_trigger: "Attendre un déclencheur",
    activate_scene: (e) => `Activer la scène : ${e}`,
    choose_between: (n) => `Choisir parmi ${n} option${n !== 1 ? "s" : ""}`,
    repeat_count: (n) => `Répéter ${n} fois`,
    repeat_while: "Répéter tant que la condition est vraie",
    repeat_until: "Répéter jusqu'à ce que la condition soit vraie",
    repeat: "Répéter",
    parallel: (n) => `Exécuter ${n} actions en parallèle`,
    sequence: (n) => `Exécuter une séquence de ${n} étapes`,
    set_variables: "Définir des variables",
    stop_label: (s) => `Arrêter : ${s}`,
    fire_event: (e) => `Déclencher l'événement : ${e}`,
    automation_step: "Étape d'automatisation",
    joiner_dot: " · ",
  },
  de: {
    when_time_is: (t) => `Wenn die Uhrzeit ${t} ist`,
    when_time_is_any: (list) => `Wenn die Uhrzeit ${list} ist`,
    or: " oder ",
    when_it_is: (ev) => `Wenn ${ev} ist`,
    sun_offset: (label, neg, ev) => `${label} ${neg ? "vor" : "nach"} ${ev}`,
    sunset: "Sonnenuntergang",
    sunrise: "Sonnenaufgang",
    when_turns_on: (eid, dur) => `Wenn ${eid} eingeschaltet wird${dur}`,
    when_turns_off: (eid, dur) => `Wenn ${eid} ausgeschaltet wird${dur}`,
    when_changes_from_to: (eid, from, to, dur) =>
      `Wenn ${eid} von ${from} zu ${to} wechselt${dur}`,
    when_becomes: (eid, st, dur) => `Wenn ${eid} zu ${st} wird${dur}`,
    when_changes_state: (eid, dur) => `Wenn ${eid} den Zustand ändert${dur}`,
    for_duration: (d) => ` für ${d}`,
    when_between: (eid, a, b) => `Wenn ${eid} zwischen ${a} und ${b} liegt`,
    when_rises_above: (eid, v) => `Wenn ${eid} über ${v} steigt`,
    when_drops_below: (eid, v) => `Wenn ${eid} unter ${v} fällt`,
    when_value_changes: (eid) => `Wenn sich der Wert von ${eid} ändert`,
    when_ha: (ev) => `Wenn Home Assistant ${ev}`,
    ha_starts: "startet",
    ha_shuts_down: "herunterfährt",
    ha_changes_state: "den Zustand ändert",
    every_seconds: (n) => `Alle ${n} Sekunde${Number(n) === 1 ? "" : "n"}`,
    every_minutes: (n) => `Alle ${n} Minute${Number(n) === 1 ? "" : "n"}`,
    every_hours: (n) => `Alle ${n} Stunde${Number(n) === 1 ? "" : "n"}`,
    on_time_pattern: "Nach einem Zeitmuster",
    when_template_entity: (e) => `Wenn Bedingung für ${e} erfüllt ist`,
    when_template_met: "Wenn eine Template-Bedingung erfüllt ist",
    when_event: (n) => `Wenn ${n} eintritt`,
    when_event_generic: "Wenn ein Ereignis eintritt",
    when_device_triggered: (t) => `Wenn ein Gerät ${t}`,
    when_device_is: (t) => `Wenn ein Gerät ${t} ist`,
    triggered: "ausgelöst",
    zone_enters: "betritt",
    zone_leaves: "verlässt",
    when_mqtt_topic: (t) => `Wenn eine Gerätenachricht eintrifft (${t})`,
    when_mqtt: "Wenn eine Gerätenachricht eintrifft",
    when_webhook: "Wenn ein externer Dienst ein Update sendet",
    when_tag: (id) => `Wenn ein Tag gescannt wird${id ? ` (${id})` : ""}`,
    when_geo: "Wenn ein Standort-Update empfangen wird",
    when_calendar: (ev, entity) => `Wenn ein Kalender-${ev} beginnt${entity}`,
    calendar_event: "Ereignis",
    on_entity: (e) => ` auf ${e}`,
    when_trigger_happens: "Wenn dieser Auslöser eintritt",
    cond_is: (eid, st) => `${eid} ist ${st}`,
    cond_between: (eid, a, b) => `${eid} zwischen ${a} und ${b}`,
    cond_above: (eid, v) => `${eid} über ${v}`,
    cond_below: (eid, v) => `${eid} unter ${v}`,
    cond_numeric: (eid) => `${eid} numerische Prüfung`,
    cond_after_time: (t) => `nach ${t}`,
    cond_before_time: (t) => `vor ${t}`,
    cond_on_weekday: (d) => `am ${d}`,
    cond_time_window: "Zeitfenster",
    cond_template_true: "Template wird zu wahr ausgewertet",
    cond_after_sun: (s) => `nach ${s}`,
    cond_before_sun: (s) => `vor ${s}`,
    cond_sun_position: "Sonnenposition",
    cond_all: (n) => `Alle ${n} Bedingungen müssen erfüllt sein`,
    cond_any: (n) => `Eine der ${n} Bedingungen ist erfüllt`,
    cond_none: "Keine der Bedingungen ist erfüllt",
    cond_in_zone: (eid, z) => `${eid} ist in ${z || "Zone"}`,
    cond_device: "Gerätebedingung",
    notify_quoted: (q) => `Benachrichtigen: „${q}“`,
    notify_target: (tgt, q) => `${tgt} benachrichtigen: „${q}“`,
    notify_via: (tgt) => `Benachrichtigen über ${tgt}`,
    send_notification: "Eine Benachrichtigung senden",
    say_quoted: (q) => `Sagen: „${q}“`,
    tts: "Sprachausgabe",
    action_turn_on: "Einschalten",
    action_turn_off: "Ausschalten",
    action_toggle: "Umschalten",
    action_lock: "Verriegeln",
    action_unlock: "Entriegeln",
    action_open_cover: "Öffnen",
    action_close_cover: "Schließen",
    action_set_temperature: "Temperatur einstellen für",
    action_set_value: "Wert setzen für",
    action_send_command: "Befehl senden an",
    action_reload: "Neu laden",
    extra_brightness: (v) => `auf ${v}%`,
    extra_temp: (v) => `auf ${v}°`,
    extra_color_temp: (v) => `Farbtemp. ${v}`,
    wait_str: (d) => `${d} warten`,
    wait_parts: (p) => `${p} warten`,
    wait_plain: "Warten",
    wait_until: "Warten bis Bedingung erfüllt ist",
    wait_for_trigger: "Auf Auslöser warten",
    activate_scene: (e) => `Szene aktivieren: ${e}`,
    choose_between: (n) => `Aus ${n} Optionen wählen`,
    repeat_count: (n) => `${n}-mal wiederholen`,
    repeat_while: "Wiederholen solange Bedingung erfüllt ist",
    repeat_until: "Wiederholen bis Bedingung erfüllt ist",
    repeat: "Wiederholen",
    parallel: (n) => `${n} Aktionen parallel ausführen`,
    sequence: (n) => `Eine Sequenz von ${n} Schritten ausführen`,
    set_variables: "Variablen setzen",
    stop_label: (s) => `Stoppen: ${s}`,
    fire_event: (e) => `Ereignis auslösen: ${e}`,
    automation_step: "Automatisierungsschritt",
    joiner_dot: " · ",
  },
  es: {
    when_time_is: (t) => `Cuando la hora sea ${t}`,
    when_time_is_any: (list) => `Cuando la hora sea ${list}`,
    or: " o ",
    when_it_is: (ev) => `Cuando sea ${ev}`,
    sun_offset: (label, neg, ev) =>
      `${label} ${neg ? "antes" : "después"} ${ev}`,
    sunset: "atardecer",
    sunrise: "amanecer",
    when_turns_on: (eid, dur) => `Cuando ${eid} se encienda${dur}`,
    when_turns_off: (eid, dur) => `Cuando ${eid} se apague${dur}`,
    when_changes_from_to: (eid, from, to, dur) =>
      `Cuando ${eid} cambie de ${from} a ${to}${dur}`,
    when_becomes: (eid, st, dur) => `Cuando ${eid} pase a ${st}${dur}`,
    when_changes_state: (eid, dur) => `Cuando ${eid} cambie de estado${dur}`,
    for_duration: (d) => ` durante ${d}`,
    when_between: (eid, a, b) => `Cuando ${eid} esté entre ${a} y ${b}`,
    when_rises_above: (eid, v) => `Cuando ${eid} supere ${v}`,
    when_drops_below: (eid, v) => `Cuando ${eid} baje de ${v}`,
    when_value_changes: (eid) => `Cuando cambie el valor de ${eid}`,
    when_ha: (ev) => `Cuando Home Assistant ${ev}`,
    ha_starts: "se inicie",
    ha_shuts_down: "se apague",
    ha_changes_state: "cambie de estado",
    every_seconds: (n) => `Cada ${n} segundo${Number(n) === 1 ? "" : "s"}`,
    every_minutes: (n) => `Cada ${n} minuto${Number(n) === 1 ? "" : "s"}`,
    every_hours: (n) => `Cada ${n} hora${Number(n) === 1 ? "" : "s"}`,
    on_time_pattern: "En un patrón temporal",
    when_template_entity: (e) => `Cuando se cumpla la condición de ${e}`,
    when_template_met: "Cuando se cumpla una condición de plantilla",
    when_event: (n) => `Cuando ocurra ${n}`,
    when_event_generic: "Cuando ocurra un evento",
    when_device_triggered: (t) => `Cuando un dispositivo ${t}`,
    when_device_is: (t) => `Cuando un dispositivo esté ${t}`,
    triggered: "activado",
    zone_enters: "entra en",
    zone_leaves: "sale de",
    when_mqtt_topic: (t) => `Cuando llegue un mensaje de dispositivo (${t})`,
    when_mqtt: "Cuando llegue un mensaje de dispositivo",
    when_webhook: "Cuando un servicio externo envíe una actualización",
    when_tag: (id) => `Cuando se escanee una etiqueta${id ? ` (${id})` : ""}`,
    when_geo: "Cuando se reciba una actualización de ubicación",
    when_calendar: (ev, entity) =>
      `Cuando comience un ${ev} de calendario${entity}`,
    calendar_event: "evento",
    on_entity: (e) => ` en ${e}`,
    when_trigger_happens: "Cuando ocurra este disparador",
    cond_is: (eid, st) => `${eid} es ${st}`,
    cond_between: (eid, a, b) => `${eid} entre ${a} y ${b}`,
    cond_above: (eid, v) => `${eid} por encima de ${v}`,
    cond_below: (eid, v) => `${eid} por debajo de ${v}`,
    cond_numeric: (eid) => `verificación numérica de ${eid}`,
    cond_after_time: (t) => `después de ${t}`,
    cond_before_time: (t) => `antes de ${t}`,
    cond_on_weekday: (d) => `el ${d}`,
    cond_time_window: "Ventana temporal",
    cond_template_true: "La plantilla se evalúa como verdadera",
    cond_after_sun: (s) => `después de ${s}`,
    cond_before_sun: (s) => `antes de ${s}`,
    cond_sun_position: "Posición del sol",
    cond_all: (n) => `Las ${n} condiciones deben ser verdaderas`,
    cond_any: (n) => `Cualquiera de las ${n} condiciones es verdadera`,
    cond_none: "Ninguna de las condiciones es verdadera",
    cond_in_zone: (eid, z) => `${eid} está en ${z || "la zona"}`,
    cond_device: "Condición de dispositivo",
    notify_quoted: (q) => `Notificar: «${q}»`,
    notify_target: (tgt, q) => `Notificar a ${tgt}: «${q}»`,
    notify_via: (tgt) => `Notificar vía ${tgt}`,
    send_notification: "Enviar una notificación",
    say_quoted: (q) => `Decir: «${q}»`,
    tts: "Síntesis de voz",
    action_turn_on: "Encender",
    action_turn_off: "Apagar",
    action_toggle: "Alternar",
    action_lock: "Bloquear",
    action_unlock: "Desbloquear",
    action_open_cover: "Abrir",
    action_close_cover: "Cerrar",
    action_set_temperature: "Establecer temperatura para",
    action_set_value: "Establecer valor para",
    action_send_command: "Enviar comando a",
    action_reload: "Recargar",
    extra_brightness: (v) => `al ${v}%`,
    extra_temp: (v) => `a ${v}°`,
    extra_color_temp: (v) => `temp. de color ${v}`,
    wait_str: (d) => `Esperar ${d}`,
    wait_parts: (p) => `Esperar ${p}`,
    wait_plain: "Esperar",
    wait_until: "Esperar hasta que se cumpla la condición",
    wait_for_trigger: "Esperar un disparador",
    activate_scene: (e) => `Activar escena: ${e}`,
    choose_between: (n) => `Elegir entre ${n} opci${n !== 1 ? "ones" : "ón"}`,
    repeat_count: (n) => `Repetir ${n} ve${n !== 1 ? "ces" : "z"}`,
    repeat_while: "Repetir mientras la condición sea verdadera",
    repeat_until: "Repetir hasta que la condición sea verdadera",
    repeat: "Repetir",
    parallel: (n) => `Ejecutar ${n} acciones en paralelo`,
    sequence: (n) => `Ejecutar una secuencia de ${n} pasos`,
    set_variables: "Establecer variables",
    stop_label: (s) => `Detener: ${s}`,
    fire_event: (e) => `Disparar evento: ${e}`,
    automation_step: "Paso de automatización",
    joiner_dot: " · ",
  },
  it: {
    when_time_is: (t) => `Quando l'ora è ${t}`,
    when_time_is_any: (list) => `Quando l'ora è ${list}`,
    or: " o ",
    when_it_is: (ev) => `Quando è ${ev}`,
    sun_offset: (label, neg, ev) => `${label} ${neg ? "prima" : "dopo"} ${ev}`,
    sunset: "tramonto",
    sunrise: "alba",
    when_turns_on: (eid, dur) => `Quando ${eid} si accende${dur}`,
    when_turns_off: (eid, dur) => `Quando ${eid} si spegne${dur}`,
    when_changes_from_to: (eid, from, to, dur) =>
      `Quando ${eid} passa da ${from} a ${to}${dur}`,
    when_becomes: (eid, st, dur) => `Quando ${eid} diventa ${st}${dur}`,
    when_changes_state: (eid, dur) => `Quando ${eid} cambia stato${dur}`,
    for_duration: (d) => ` per ${d}`,
    when_between: (eid, a, b) => `Quando ${eid} è tra ${a} e ${b}`,
    when_rises_above: (eid, v) => `Quando ${eid} supera ${v}`,
    when_drops_below: (eid, v) => `Quando ${eid} scende sotto ${v}`,
    when_value_changes: (eid) => `Quando il valore di ${eid} cambia`,
    when_ha: (ev) => `Quando Home Assistant ${ev}`,
    ha_starts: "si avvia",
    ha_shuts_down: "si arresta",
    ha_changes_state: "cambia stato",
    every_seconds: (n) => `Ogni ${n} second${Number(n) === 1 ? "o" : "i"}`,
    every_minutes: (n) => `Ogni ${n} minut${Number(n) === 1 ? "o" : "i"}`,
    every_hours: (n) => `Ogni ${n} or${Number(n) === 1 ? "a" : "e"}`,
    on_time_pattern: "Su uno schema temporale",
    when_template_entity: (e) => `Quando la condizione su ${e} è soddisfatta`,
    when_template_met: "Quando una condizione del modello è soddisfatta",
    when_event: (n) => `Quando ${n} si verifica`,
    when_event_generic: "Quando si verifica un evento",
    when_device_triggered: (t) => `Quando un dispositivo ${t}`,
    when_device_is: (t) => `Quando un dispositivo è ${t}`,
    triggered: "attivato",
    zone_enters: "entra in",
    zone_leaves: "esce da",
    when_mqtt_topic: (t) => `Quando arriva un messaggio del dispositivo (${t})`,
    when_mqtt: "Quando arriva un messaggio del dispositivo",
    when_webhook: "Quando un servizio esterno invia un aggiornamento",
    when_tag: (id) => `Quando un tag viene scansionato${id ? ` (${id})` : ""}`,
    when_geo: "Quando si riceve un aggiornamento di posizione",
    when_calendar: (ev, entity) =>
      `Quando inizia un ${ev} del calendario${entity}`,
    calendar_event: "evento",
    on_entity: (e) => ` su ${e}`,
    when_trigger_happens: "Quando si verifica questo trigger",
    cond_is: (eid, st) => `${eid} è ${st}`,
    cond_between: (eid, a, b) => `${eid} tra ${a} e ${b}`,
    cond_above: (eid, v) => `${eid} sopra ${v}`,
    cond_below: (eid, v) => `${eid} sotto ${v}`,
    cond_numeric: (eid) => `verifica numerica di ${eid}`,
    cond_after_time: (t) => `dopo ${t}`,
    cond_before_time: (t) => `prima di ${t}`,
    cond_on_weekday: (d) => `il ${d}`,
    cond_time_window: "Finestra temporale",
    cond_template_true: "Il modello è valutato vero",
    cond_after_sun: (s) => `dopo ${s}`,
    cond_before_sun: (s) => `prima di ${s}`,
    cond_sun_position: "Posizione del sole",
    cond_all: (n) => `Tutte le ${n} condizioni devono essere vere`,
    cond_any: (n) => `Una delle ${n} condizioni è vera`,
    cond_none: "Nessuna delle condizioni è vera",
    cond_in_zone: (eid, z) => `${eid} è in ${z || "zona"}`,
    cond_device: "Condizione del dispositivo",
    notify_quoted: (q) => `Notifica: «${q}»`,
    notify_target: (tgt, q) => `Notifica a ${tgt}: «${q}»`,
    notify_via: (tgt) => `Notifica tramite ${tgt}`,
    send_notification: "Invia una notifica",
    say_quoted: (q) => `Dire: «${q}»`,
    tts: "Sintesi vocale",
    action_turn_on: "Accendi",
    action_turn_off: "Spegni",
    action_toggle: "Commuta",
    action_lock: "Blocca",
    action_unlock: "Sblocca",
    action_open_cover: "Apri",
    action_close_cover: "Chiudi",
    action_set_temperature: "Imposta temperatura per",
    action_set_value: "Imposta valore per",
    action_send_command: "Invia comando a",
    action_reload: "Ricarica",
    extra_brightness: (v) => `al ${v}%`,
    extra_temp: (v) => `a ${v}°`,
    extra_color_temp: (v) => `temp. colore ${v}`,
    wait_str: (d) => `Attendi ${d}`,
    wait_parts: (p) => `Attendi ${p}`,
    wait_plain: "Attendi",
    wait_until: "Attendi finché la condizione non è soddisfatta",
    wait_for_trigger: "Attendi un trigger",
    activate_scene: (e) => `Attiva scena: ${e}`,
    choose_between: (n) => `Scegli tra ${n} opzion${n !== 1 ? "i" : "e"}`,
    repeat_count: (n) => `Ripeti ${n} volt${n !== 1 ? "e" : "a"}`,
    repeat_while: "Ripeti finché la condizione è vera",
    repeat_until: "Ripeti finché la condizione non è vera",
    repeat: "Ripeti",
    parallel: (n) => `Esegui ${n} azioni in parallelo`,
    sequence: (n) => `Esegui una sequenza di ${n} passaggi`,
    set_variables: "Imposta variabili",
    stop_label: (s) => `Ferma: ${s}`,
    fire_event: (e) => `Lancia evento: ${e}`,
    automation_step: "Passo di automazione",
    joiner_dot: " · ",
  },
  nl: {
    when_time_is: (t) => `Wanneer het tijdstip ${t} is`,
    when_time_is_any: (list) => `Wanneer het tijdstip ${list} is`,
    or: " of ",
    when_it_is: (ev) => `Wanneer het ${ev} is`,
    sun_offset: (label, neg, ev) => `${label} ${neg ? "vóór" : "na"} ${ev}`,
    sunset: "zonsondergang",
    sunrise: "zonsopgang",
    when_turns_on: (eid, dur) => `Wanneer ${eid} aangaat${dur}`,
    when_turns_off: (eid, dur) => `Wanneer ${eid} uitgaat${dur}`,
    when_changes_from_to: (eid, from, to, dur) =>
      `Wanneer ${eid} verandert van ${from} naar ${to}${dur}`,
    when_becomes: (eid, st, dur) => `Wanneer ${eid} ${st} wordt${dur}`,
    when_changes_state: (eid, dur) =>
      `Wanneer ${eid} van status verandert${dur}`,
    for_duration: (d) => ` gedurende ${d}`,
    when_between: (eid, a, b) => `Wanneer ${eid} tussen ${a} en ${b} is`,
    when_rises_above: (eid, v) => `Wanneer ${eid} boven ${v} stijgt`,
    when_drops_below: (eid, v) => `Wanneer ${eid} onder ${v} zakt`,
    when_value_changes: (eid) => `Wanneer de waarde van ${eid} verandert`,
    when_ha: (ev) => `Wanneer Home Assistant ${ev}`,
    ha_starts: "start",
    ha_shuts_down: "afsluit",
    ha_changes_state: "van status verandert",
    every_seconds: (n) =>
      `Elke ${n} ${Number(n) === 1 ? "seconde" : "seconden"}`,
    every_minutes: (n) => `Elke ${n} ${Number(n) === 1 ? "minuut" : "minuten"}`,
    every_hours: (n) => `Elke ${n} uur`,
    on_time_pattern: "Op een tijdpatroon",
    when_template_entity: (e) => `Wanneer de voorwaarde op ${e} klopt`,
    when_template_met: "Wanneer aan een sjabloonvoorwaarde wordt voldaan",
    when_event: (n) => `Wanneer ${n} gebeurt`,
    when_event_generic: "Wanneer een gebeurtenis plaatsvindt",
    when_device_triggered: (t) => `Wanneer een apparaat ${t}`,
    when_device_is: (t) => `Wanneer een apparaat ${t} is`,
    triggered: "geactiveerd",
    zone_enters: "betreedt",
    zone_leaves: "verlaat",
    when_mqtt_topic: (t) => `Wanneer er een apparaatbericht binnenkomt (${t})`,
    when_mqtt: "Wanneer er een apparaatbericht binnenkomt",
    when_webhook: "Wanneer een externe service een update stuurt",
    when_tag: (id) => `Wanneer een tag wordt gescand${id ? ` (${id})` : ""}`,
    when_geo: "Wanneer er een locatie-update binnenkomt",
    when_calendar: (ev, entity) => `Wanneer een agenda-${ev} begint${entity}`,
    calendar_event: "gebeurtenis",
    on_entity: (e) => ` op ${e}`,
    when_trigger_happens: "Wanneer deze trigger optreedt",
    cond_is: (eid, st) => `${eid} is ${st}`,
    cond_between: (eid, a, b) => `${eid} tussen ${a} en ${b}`,
    cond_above: (eid, v) => `${eid} boven ${v}`,
    cond_below: (eid, v) => `${eid} onder ${v}`,
    cond_numeric: (eid) => `${eid} numerieke controle`,
    cond_after_time: (t) => `na ${t}`,
    cond_before_time: (t) => `vóór ${t}`,
    cond_on_weekday: (d) => `op ${d}`,
    cond_time_window: "Tijdvenster",
    cond_template_true: "Sjabloon evalueert naar waar",
    cond_after_sun: (s) => `na ${s}`,
    cond_before_sun: (s) => `vóór ${s}`,
    cond_sun_position: "Zonpositie",
    cond_all: (n) => `Alle ${n} voorwaarden moeten waar zijn`,
    cond_any: (n) => `Eén van de ${n} voorwaarden is waar`,
    cond_none: "Geen van de voorwaarden is waar",
    cond_in_zone: (eid, z) => `${eid} bevindt zich in ${z || "zone"}`,
    cond_device: "Apparaatvoorwaarde",
    notify_quoted: (q) => `Melding: „${q}”`,
    notify_target: (tgt, q) => `${tgt} melden: „${q}”`,
    notify_via: (tgt) => `Melden via ${tgt}`,
    send_notification: "Een melding sturen",
    say_quoted: (q) => `Zeggen: „${q}”`,
    tts: "Tekst-naar-spraak",
    action_turn_on: "Aanzetten",
    action_turn_off: "Uitzetten",
    action_toggle: "Omschakelen",
    action_lock: "Vergrendelen",
    action_unlock: "Ontgrendelen",
    action_open_cover: "Openen",
    action_close_cover: "Sluiten",
    action_set_temperature: "Temperatuur instellen voor",
    action_set_value: "Waarde instellen voor",
    action_send_command: "Commando sturen naar",
    action_reload: "Opnieuw laden",
    extra_brightness: (v) => `op ${v}%`,
    extra_temp: (v) => `naar ${v}°`,
    extra_color_temp: (v) => `kleurtemp. ${v}`,
    wait_str: (d) => `Wacht ${d}`,
    wait_parts: (p) => `Wacht ${p}`,
    wait_plain: "Wacht",
    wait_until: "Wacht tot aan de voorwaarde is voldaan",
    wait_for_trigger: "Wacht op een trigger",
    activate_scene: (e) => `Scène activeren: ${e}`,
    choose_between: (n) => `Kies tussen ${n} ${n !== 1 ? "opties" : "optie"}`,
    repeat_count: (n) => `Herhaal ${n} ${n !== 1 ? "keer" : "keer"}`,
    repeat_while: "Herhaal zolang de voorwaarde geldt",
    repeat_until: "Herhaal totdat de voorwaarde wordt voldaan",
    repeat: "Herhalen",
    parallel: (n) => `Voer ${n} acties parallel uit`,
    sequence: (n) => `Voer een reeks van ${n} stappen uit`,
    set_variables: "Variabelen instellen",
    stop_label: (s) => `Stop: ${s}`,
    fire_event: (e) => `Gebeurtenis afvuren: ${e}`,
    automation_step: "Automatiseringsstap",
    joiner_dot: " · ",
  },
  hu: {
    when_time_is: (t) => `Amikor az idő ${t}`,
    when_time_is_any: (list) => `Amikor az idő ${list}`,
    or: " vagy ",
    when_it_is: (ev) => `Amikor ${ev} van`,
    sun_offset: (label, neg, ev) => `${label} ${neg ? "előtt" : "után"} ${ev}`,
    sunset: "napnyugta",
    sunrise: "napkelte",
    when_turns_on: (eid, dur) => `Amikor ${eid} bekapcsol${dur}`,
    when_turns_off: (eid, dur) => `Amikor ${eid} kikapcsol${dur}`,
    when_changes_from_to: (eid, from, to, dur) =>
      `Amikor ${eid} ${from}-ról ${to}-ra vált${dur}`,
    when_becomes: (eid, st, dur) => `Amikor ${eid} ${st} lesz${dur}`,
    when_changes_state: (eid, dur) => `Amikor ${eid} állapotot vált${dur}`,
    for_duration: (d) => ` ${d}-ig`,
    when_between: (eid, a, b) => `Amikor ${eid} ${a} és ${b} között van`,
    when_rises_above: (eid, v) => `Amikor ${eid} ${v} fölé emelkedik`,
    when_drops_below: (eid, v) => `Amikor ${eid} ${v} alá esik`,
    when_value_changes: (eid) => `Amikor ${eid} értéke változik`,
    when_ha: (ev) => `Amikor a Home Assistant ${ev}`,
    ha_starts: "elindul",
    ha_shuts_down: "leáll",
    ha_changes_state: "állapotot vált",
    every_seconds: (n) => `Minden ${n} másodperc`,
    every_minutes: (n) => `Minden ${n} perc`,
    every_hours: (n) => `Minden ${n} óra`,
    on_time_pattern: "Időminta szerint",
    when_template_entity: (e) => `Amikor a ${e} feltétel teljesül`,
    when_template_met: "Amikor egy sablonfeltétel teljesül",
    when_event: (n) => `Amikor ${n} történik`,
    when_event_generic: "Amikor egy esemény történik",
    when_device_triggered: (t) => `Amikor egy eszköz ${t}`,
    when_device_is: (t) => `Amikor egy eszköz ${t}`,
    triggered: "kiváltódik",
    zone_enters: "belép ide:",
    zone_leaves: "elhagyja ezt:",
    when_mqtt_topic: (t) => `Amikor eszközüzenet érkezik (${t})`,
    when_mqtt: "Amikor eszközüzenet érkezik",
    when_webhook: "Amikor egy külső szolgáltatás frissítést küld",
    when_tag: (id) => `Amikor egy címkét beolvasnak${id ? ` (${id})` : ""}`,
    when_geo: "Amikor helyfrissítés érkezik",
    when_calendar: (ev, entity) =>
      `Amikor egy naptári ${ev} elkezdődik${entity}`,
    calendar_event: "esemény",
    on_entity: (e) => ` itt: ${e}`,
    when_trigger_happens: "Amikor ez a trigger bekövetkezik",
    cond_is: (eid, st) => `${eid} értéke ${st}`,
    cond_between: (eid, a, b) => `${eid} ${a} és ${b} között`,
    cond_above: (eid, v) => `${eid} ${v} fölött`,
    cond_below: (eid, v) => `${eid} ${v} alatt`,
    cond_numeric: (eid) => `${eid} numerikus ellenőrzés`,
    cond_after_time: (t) => `${t} után`,
    cond_before_time: (t) => `${t} előtt`,
    cond_on_weekday: (d) => `${d} napokon`,
    cond_time_window: "Időablak",
    cond_template_true: "A sablon igaznak értékelődik",
    cond_after_sun: (s) => `${s} után`,
    cond_before_sun: (s) => `${s} előtt`,
    cond_sun_position: "Nappozíció",
    cond_all: (n) => `Mind a ${n} feltételnek igaznak kell lennie`,
    cond_any: (n) => `A ${n} feltétel egyike igaz`,
    cond_none: "Egyik feltétel sem igaz",
    cond_in_zone: (eid, z) => `${eid} itt: ${z || "zóna"}`,
    cond_device: "Eszközfeltétel",
    notify_quoted: (q) => `Értesítés: „${q}”`,
    notify_target: (tgt, q) => `${tgt} értesítése: „${q}”`,
    notify_via: (tgt) => `Értesítés ${tgt} útján`,
    send_notification: "Értesítés küldése",
    say_quoted: (q) => `Mondás: „${q}”`,
    tts: "Szövegfelolvasás",
    action_turn_on: "Bekapcsolás",
    action_turn_off: "Kikapcsolás",
    action_toggle: "Átkapcsolás",
    action_lock: "Zárolás",
    action_unlock: "Feloldás",
    action_open_cover: "Nyitás",
    action_close_cover: "Zárás",
    action_set_temperature: "Hőmérséklet beállítása:",
    action_set_value: "Érték beállítása:",
    action_send_command: "Parancs küldése:",
    action_reload: "Újratöltés",
    extra_brightness: (v) => `${v}%-on`,
    extra_temp: (v) => `${v}°-ra`,
    extra_color_temp: (v) => `színhőmérséklet ${v}`,
    wait_str: (d) => `Várakozás: ${d}`,
    wait_parts: (p) => `Várakozás: ${p}`,
    wait_plain: "Várakozás",
    wait_until: "Várakozás, amíg a feltétel teljesül",
    wait_for_trigger: "Várakozás triggerre",
    activate_scene: (e) => `Jelenet aktiválása: ${e}`,
    choose_between: (n) => `Választás ${n} opció közül`,
    repeat_count: (n) => `Ismétlés ${n}-szor`,
    repeat_while: "Ismétlés, amíg a feltétel fennáll",
    repeat_until: "Ismétlés, amíg a feltétel teljesül",
    repeat: "Ismétlés",
    parallel: (n) => `${n} művelet párhuzamos futtatása`,
    sequence: (n) => `${n} lépéses szekvencia futtatása`,
    set_variables: "Változók beállítása",
    stop_label: (s) => `Megállítás: ${s}`,
    fire_event: (e) => `Esemény kiváltása: ${e}`,
    automation_step: "Automatizmus lépése",
    joiner_dot: " · ",
  },
};

function _phrases(hass) {
  const lang = String(hass?.language || "en")
    .toLowerCase()
    .split("-")[0];
  return PHRASES[lang] || PHRASES.en;
}

function _val(phrases, key, ...args) {
  const v = phrases[key];
  if (v === undefined) return PHRASES.en[key];
  return typeof v === "function" ? v(...args) : v;
}

/**
 * Describe a single HA flow item (trigger, condition, or action) as a human-readable string.
 *
 * Dispatches on three sections:
 *   1. Triggers — keyed on `item.platform` or `item.trigger` (e.g. "time", "state", "sun")
 *   2. Conditions — keyed on `item.condition` (e.g. "state", "time", "sun")
 *   3. Actions — keyed on `item.service` or `item.action`, then structural keys (delay, choose, etc.)
 *
 * Falls back to a readable summary of the item's keys if none of the above match.
 *
 * @param {{ states?: Object, language?: string }} hass - Home Assistant instance for entity name lookups and locale
 * @param {Object} item - trigger, condition, or action object from an automation
 * @returns {string} human-readable description
 */
export function describeFlowItem(hass, item) {
  if (!item || typeof item !== "object") return String(item ?? "");
  const T = _phrases(hass);
  const t = (k, ...a) => _val(T, k, ...a);
  const lang = hass?.language;

  const p = item.platform || item.trigger;

  // ── Triggers ──────────────────────────────────────────────────────────────
  if (p === "time") {
    const raw = item.at;
    if (Array.isArray(raw)) {
      return t(
        "when_time_is_any",
        raw.map((x) => fmtTime(hass, x)).join(t("or")),
      );
    }
    return t("when_time_is", fmtTime(hass, raw));
  }
  if (p === "sun") {
    const ev =
      item.event === "sunset"
        ? t("sunset")
        : item.event === "sunrise"
          ? t("sunrise")
          : humanizeToken(item.event || "sun event").toLowerCase();
    if (item.offset) {
      const neg = item.offset.startsWith("-");
      const raw = neg ? item.offset.slice(1) : item.offset;
      const [h, m, s] = raw.split(":").map(Number);
      const parts = [];
      if (h) parts.push(`${h}h`);
      if (m) parts.push(`${m}min`);
      if (s) parts.push(`${s}s`);
      const label = parts.join(" ") || item.offset;
      return t("sun_offset", label, neg, ev);
    }
    return t("when_it_is", ev);
  }
  if (p === "state") {
    const eid = fmtEntities(hass, item.entity_id, lang);
    const rawTo = item.to == null ? null : String(item.to);
    const fromState = fmtState(item.from, lang);
    const toState = fmtState(item.to, lang);
    const duration = fmtDuration(item.for);
    const dur = duration ? t("for_duration", duration) : "";
    // Branch on the raw state token (locale-independent), then render
    // the localized label only when emitting a generic phrase.
    if (rawTo === "on") return t("when_turns_on", eid, dur);
    if (rawTo === "off") return t("when_turns_off", eid, dur);
    if (toState && fromState)
      return t("when_changes_from_to", eid, fromState, toState, dur);
    if (toState) return t("when_becomes", eid, toState, dur);
    return t("when_changes_state", eid, dur);
  }
  if (p === "numeric_state") {
    const eid = fmtEntities(hass, item.entity_id, lang);
    const above = fmtNumericValue(item.entity_id, item.above);
    const below = fmtNumericValue(item.entity_id, item.below);
    if (item.above != null && item.below != null)
      return t("when_between", eid, above, below);
    if (item.above != null) return t("when_rises_above", eid, above);
    if (item.below != null) return t("when_drops_below", eid, below);
    return t("when_value_changes", eid);
  }
  if (p === "homeassistant") {
    const ev =
      item.event === "start"
        ? t("ha_starts")
        : item.event === "shutdown"
          ? t("ha_shuts_down")
          : t("ha_changes_state");
    return t("when_ha", ev);
  }
  if (p === "time_pattern") {
    if (item.seconds != null) return t("every_seconds", item.seconds);
    if (item.minutes != null) return t("every_minutes", item.minutes);
    if (item.hours != null) return t("every_hours", item.hours);
    return t("on_time_pattern");
  }
  if (p === "template") {
    const tmpl = item.value_template || "";
    const entityMatch = tmpl.match(/states\(['"]([^'"]+)['"]\)/);
    if (entityMatch)
      return t("when_template_entity", fmtEntity(hass, entityMatch[1]));
    return t("when_template_met");
  }
  if (p === "event") {
    if (item.event_type)
      return t("when_event", humanizeToken(item.event_type).toLowerCase());
    return t("when_event_generic");
  }
  if (p === "device") {
    const triggerType = item.type
      ? humanizeToken(item.type).toLowerCase()
      : t("triggered");
    return item.device_id
      ? t("when_device_triggered", triggerType)
      : t("when_device_is", triggerType);
  }
  if (p === "zone") {
    const eid = fmtEntities(hass, item.entity_id, lang);
    const zone = fmtEntity(hass, item.zone);
    const rawEvent = String(item.event || "enter");
    const ev =
      rawEvent === "enter"
        ? t("zone_enters")
        : rawEvent === "leave"
          ? t("zone_leaves")
          : humanizeToken(rawEvent).toLowerCase();
    return `${eid} ${ev} ${zone}`.trim();
  }
  if (p === "mqtt")
    return item.topic ? t("when_mqtt_topic", item.topic) : t("when_mqtt");
  if (p === "webhook") return t("when_webhook");
  if (p === "tag") return t("when_tag", item.tag_id || "");
  if (p === "geo_location") return t("when_geo");
  if (p === "calendar") {
    const eventName = item.event
      ? humanizeToken(item.event).toLowerCase()
      : t("calendar_event");
    const entity = item.entity_id
      ? t("on_entity", fmtEntity(hass, item.entity_id))
      : "";
    return t("when_calendar", eventName, entity);
  }
  if (p) return t("when_trigger_happens");

  // ── Conditions ─────────────────────────────────────────────────────────────
  const cond = item.condition;
  if (cond === "state") {
    const eid = fmtEntities(hass, item.entity_id, lang);
    const st = fmtState(item.state ?? item.to, lang);
    return t("cond_is", eid, st);
  }
  if (cond === "numeric_state") {
    const eid = fmtEntities(hass, item.entity_id, lang);
    if (item.above != null && item.below != null)
      return t("cond_between", eid, item.above, item.below);
    if (item.above != null) return t("cond_above", eid, item.above);
    if (item.below != null) return t("cond_below", eid, item.below);
    return t("cond_numeric", eid);
  }
  if (cond === "time") {
    const parts = [];
    if (item.after) parts.push(t("cond_after_time", fmtTime(hass, item.after)));
    if (item.before)
      parts.push(t("cond_before_time", fmtTime(hass, item.before)));
    if (item.weekday)
      parts.push(t("cond_on_weekday", fmtWeekdays(item.weekday, lang)));
    return parts.length ? parts.join(t("joiner_dot")) : t("cond_time_window");
  }
  if (cond === "template") return t("cond_template_true");
  if (cond === "sun") {
    const parts = [];
    if (item.after)
      parts.push(t("cond_after_sun", String(item.after).replace(/_/g, " ")));
    if (item.before)
      parts.push(t("cond_before_sun", String(item.before).replace(/_/g, " ")));
    return parts.join(", ") || t("cond_sun_position");
  }
  if (cond === "and") return t("cond_all", asArray(item.conditions).length);
  if (cond === "or") return t("cond_any", asArray(item.conditions).length);
  if (cond === "not") return t("cond_none");
  if (cond === "zone") {
    const eid = fmtEntities(hass, item.entity_id, lang);
    return t("cond_in_zone", eid, fmtEntity(hass, item.zone));
  }
  if (cond === "device")
    return item.type ? String(item.type).replace(/_/g, " ") : t("cond_device");
  if (cond) return String(cond).replace(/_/g, " ");

  // ── Actions ────────────────────────────────────────────────────────────────
  const svc = item.service || item.action;
  if (svc) {
    const svcStr = String(svc);
    const [domain = "", svcName = svc] = svcStr.split(".");

    if (
      svcStr === "notify.persistent_notification" ||
      domain === "persistent_notification"
    ) {
      const title = item.data?.title;
      const msg = item.data?.message;
      if (title) return t("notify_quoted", title);
      if (msg) {
        const short = msg.length > 60 ? msg.slice(0, 57) + "…" : msg;
        return t("notify_quoted", short);
      }
      return t("send_notification");
    }
    if (domain === "notify") {
      const target = svcName
        .replace(/_/g, " ")
        .replace(/\b\w/g, (c) => c.toUpperCase());
      const msg = item.data?.message;
      const title = item.data?.title;
      if (title) return t("notify_target", target, title);
      if (msg) {
        const short = msg.length > 50 ? msg.slice(0, 47) + "…" : msg;
        return t("notify_target", target, short);
      }
      return t("notify_via", target);
    }
    if (domain === "tts") {
      const msg = item.data?.message;
      if (msg) {
        const short = msg.length > 50 ? msg.slice(0, 47) + "…" : msg;
        return t("say_quoted", short);
      }
      return t("tts");
    }

    const ACTION_KEYS = {
      turn_on: "action_turn_on",
      turn_off: "action_turn_off",
      toggle: "action_toggle",
      lock: "action_lock",
      unlock: "action_unlock",
      open_cover: "action_open_cover",
      close_cover: "action_close_cover",
      set_temperature: "action_set_temperature",
      set_value: "action_set_value",
      send_command: "action_send_command",
      reload: "action_reload",
    };
    const actionKey = ACTION_KEYS[svcName];
    const name = actionKey
      ? t(actionKey)
      : svcName.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
    const targets = item.target?.entity_id ?? item.data?.entity_id;
    const tgt = fmtEntities(hass, targets, lang);
    const extras = [];
    if (item.data?.brightness_pct != null)
      extras.push(t("extra_brightness", item.data.brightness_pct));
    if (item.data?.temperature != null)
      extras.push(t("extra_temp", item.data.temperature));
    if (item.data?.color_temp != null)
      extras.push(t("extra_color_temp", item.data.color_temp));
    if (item.data?.message && !String(item.data.message).includes("{{")) {
      const short =
        item.data.message.length > 50
          ? item.data.message.slice(0, 47) + "…"
          : item.data.message;
      extras.push(`"${short}"`);
    }
    if (item.data?.title && !String(item.data.title).includes("{{"))
      extras.push(item.data.title);
    const detail = extras.length ? ` (${extras.join(", ")})` : "";
    return tgt ? `${name} ${tgt}${detail}` : `${name}${detail}`;
  }
  if (item.delay) {
    const d = item.delay;
    if (typeof d === "string") return t("wait_str", d);
    const parts = [];
    if (d.hours) parts.push(`${d.hours}h`);
    if (d.minutes) parts.push(`${d.minutes}m`);
    if (d.seconds) parts.push(`${d.seconds}s`);
    return parts.length ? t("wait_parts", parts.join(" ")) : t("wait_plain");
  }
  if (item.wait_template) return t("wait_until");
  if (item.wait_for_trigger) return t("wait_for_trigger");
  if (item.scene) return t("activate_scene", fmtEntity(hass, item.scene));
  if (item.choose) return t("choose_between", asArray(item.choose).length);
  if (item.repeat) {
    const r = item.repeat;
    if (r.count != null) return t("repeat_count", r.count);
    if (r.while) return t("repeat_while");
    if (r.until) return t("repeat_until");
    return t("repeat");
  }
  if (item.parallel) return t("parallel", asArray(item.parallel).length);
  if (item.sequence) return t("sequence", asArray(item.sequence).length);
  if (item.variables) return t("set_variables");
  if (item.stop) return t("stop_label", item.stop);
  if (item.event) return t("fire_event", String(item.event).replace(/_/g, " "));

  // ── Human-readable fallback ─────────────────────────────────────────────
  const SKIP = new Set(["id", "enabled", "mode", "alias", "description"]);
  const readable = Object.entries(item)
    .filter(([k, v]) => !SKIP.has(k) && v != null && v !== "")
    .map(([k, v]) => {
      const label = k.replace(/_/g, " ");
      const strVal =
        typeof v === "string"
          ? v
          : Array.isArray(v)
            ? v.map((x) => (typeof x === "object" ? "…" : x)).join(", ")
            : String(v);
      if (strVal.includes("{{") || strVal.includes("{%")) return null;
      return `${label}: ${strVal}`;
    })
    .filter(Boolean)
    .slice(0, 3);
  return readable.length
    ? readable.join(t("joiner_dot"))
    : t("automation_step");
}

/**
 * Pull every entity_id referenced by a single trigger / condition / action.
 * Covers the spots HA understands today:
 *   - Triggers / conditions: top-level `entity_id` (state, numeric_state, zone, …).
 *   - Actions: `target.entity_id` plus the deprecated `data.entity_id`.
 *   - "Device" triggers/actions don't carry an entity_id — they reference a
 *     device by id, which can't be opened with the more-info dialog anyway,
 *     so we skip those.
 * Returns a de-duplicated array in source order, preserving the order the
 * automation listed them.
 *
 * @param {Object} item - trigger, condition, or action object
 * @returns {string[]}
 */
export function collectFlowEntityIds(item) {
  if (!item || typeof item !== "object") return [];
  const out = [];
  const seen = new Set();
  const push = (val) => {
    if (val == null) return;
    const arr = Array.isArray(val) ? val : [val];
    for (const v of arr) {
      if (typeof v !== "string" || !v) continue;
      // Heuristic: an entity_id always has exactly one dot between domain
      // and object_id (snake_case both sides). Filters out device_ids,
      // area_ids, templated values (`{{ states(...) }}`), and special
      // sentinels like `all` — clicking those would dispatch
      // `hass-more-info` with an invalid ID and render broken chips.
      if (!/^[a-z0-9_]+\.[a-z0-9_]+$/.test(v)) continue;
      if (seen.has(v)) continue;
      seen.add(v);
      out.push(v);
    }
  };
  push(item.entity_id);
  push(item.target?.entity_id);
  push(item.data?.entity_id);
  return out;
}
