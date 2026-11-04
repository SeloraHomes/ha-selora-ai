// Automation and scene ideas for a camera (frigate/ideas.py), worded for the
// panel. Shown as chips on the Frigate result card and as rows among the
// chat home's suggestions.
//
// Acting on one is an ordinary chat request: the visible title, with the
// exact entities folded into a details marker, because cameras are not in the
// model's entity list. The model proposes the automation or scene on the usual
// card; nothing is written until the user accepts it there.
import { html } from "lit";

import { interpolate, pickLocale } from "../shared/i18n.js";
import { buildDetailsMarker } from "./chat-autocomplete.js";

const MAX_CHIPS = 3;
const SNAPSHOT = [
  "camera_idea_snapshot",
  "Attach the camera snapshot to the notification as data image {image}.",
];
const SNAPSHOT_MANY = [
  "camera_idea_snapshot_many",
  "Attach the snapshot of the camera that saw it to the notification as data image /api/camera_proxy/ followed by that camera's entity id.",
];
const MAX_NAMED = 3;

const TEXT = {
  person_away: {
    icon: "mdi:account-alert-outline",
    title: [
      "camera_idea_person_away_title",
      "Alert me when someone is at {camera} while nobody is home",
    ],
    chip: ["camera_idea_person_away_chip", "Alert me when away"],
    why: [
      "camera_idea_person_away_why",
      "{camera} detects people. With nobody home, a person there is worth a notification with a snapshot.",
    ],
    prompt: [
      "camera_idea_person_away_prompt",
      "Propose an automation I can review: when {trigger} turns on while {presence} is {presence_empty} (nobody is home), call {notify} to say a person was seen on {camera}.",
    ],
    many: {
      why: [
        "camera_idea_person_away_why_many",
        "{cameras} detect people. With nobody home, a person at any of them is worth a notification with a snapshot.",
      ],
      prompt: [
        "camera_idea_person_away_prompt_many",
        "Propose one automation I can review, triggered by any of these sensors, each followed by its camera: {pairs}. While {presence} is {presence_empty} (nobody is home), call {notify} to say a person was seen, naming the camera that saw them.",
      ],
    },
  },
  person_night_light: {
    icon: "mdi:lightbulb-on-outline",
    title: [
      "camera_idea_person_night_light_title",
      "Turn on a light when someone is at {camera} after dark",
    ],
    chip: ["camera_idea_person_night_light_chip", "Light up at night"],
    why: [
      "camera_idea_person_night_light_why",
      "{camera} detects people, and {light} is in the same area.",
    ],
    prompt: [
      "camera_idea_person_night_light_prompt",
      "Propose an automation I can review: when {trigger} turns on while sun.sun is below_horizon, turn on {light}, and turn it off again 5 minutes after {trigger} turns off.",
    ],
  },
  animal: {
    icon: "mdi:paw",
    title: [
      "camera_idea_animal_title",
      "Tell me when an animal is at {camera}",
    ],
    chip: ["camera_idea_animal_chip", "Animal alert"],
    why: [
      "camera_idea_animal_why",
      "{camera} detects animals: a pet in the garden, or a visitor that should not be there.",
    ],
    prompt: [
      "camera_idea_animal_prompt",
      "Propose an automation I can review: when {trigger} turns on, call {notify} to say an animal was seen on {camera}.",
    ],
    many: {
      why: [
        "camera_idea_animal_why_many",
        "{cameras} detect animals: a pet in the garden, or a visitor that should not be there.",
      ],
      prompt: [
        "camera_idea_animal_prompt_many",
        "Propose one automation I can review, triggered by any of these sensors, each followed by its camera: {pairs}. Call {notify} to say an animal was seen, naming the camera that saw it.",
      ],
    },
  },
  vehicle: {
    icon: "mdi:car-outline",
    title: [
      "camera_idea_vehicle_title",
      "Tell me when a car arrives at {camera}",
    ],
    chip: ["camera_idea_vehicle_chip", "Car alert"],
    why: [
      "camera_idea_vehicle_why",
      "{camera} detects cars, so a delivery or a visitor arriving can be announced.",
    ],
    prompt: [
      "camera_idea_vehicle_prompt",
      "Propose an automation I can review: when {trigger} turns on, call {notify} to say a car arrived at {camera}.",
    ],
    many: {
      why: [
        "camera_idea_vehicle_why_many",
        "{cameras} detect cars, so a delivery or a visitor arriving can be announced.",
      ],
      prompt: [
        "camera_idea_vehicle_prompt_many",
        "Propose one automation I can review, triggered by any of these sensors, each followed by its camera: {pairs}. Call {notify} to say a car arrived, naming the camera that saw it.",
      ],
    },
  },
  watch_scene: {
    icon: "mdi:cctv",
    title: [
      "camera_idea_watch_scene_title",
      "Create a scene that turns on every camera's recording and detection",
    ],
    why: [
      "camera_idea_watch_scene_why",
      "One tap before you leave, instead of switching each camera on.",
    ],
    prompt: [
      "camera_idea_watch_scene_prompt",
      "Propose a scene I can review, called Watch, that turns on {switches}.",
    ],
  },
};

/** {icon, title, chip, why: [..], prompt} for an idea, or null for an unknown kind. */
export function describeIdea(host, idea) {
  const text = TEXT[idea?.kind];
  if (!text) return null;
  const values = { camera: idea.camera?.name || "", ...(idea.facts || {}) };
  const t = ([key, fallback]) => interpolate(host._t(key, fallback), values);
  return {
    icon: text.icon,
    title: t(text.title),
    chip: text.chip ? t(text.chip) : t(text.title),
    why: [t(text.why)],
    // The snapshot goes in only when the notifier can carry one: HA's own
    // notifications take a title and a message, nothing else.
    prompt: idea.facts?.image
      ? `${t(text.prompt)} ${t(SNAPSHOT)}`
      : t(text.prompt),
  };
}

// "Front Door, Garage or 2 more", in the panel's language.
function cameraList(host, names, type) {
  const shown = names.slice(0, MAX_NAMED);
  if (names.length > shown.length) {
    shown.push(
      interpolate(host._t("home_suggestion_more", "{count} more"), {
        count: names.length - shown.length,
      }),
    );
  }
  try {
    return new Intl.ListFormat(pickLocale(host.hass), { type }).format(shown);
  } catch {
    return shown.join(", ");
  }
}

/**
 * {icon, title, why: [..], prompt} for the same idea on several cameras (a
 * home suggestion row naming them all), asking for one automation that covers
 * every camera. Null for a kind that has no shared wording.
 */
export function describeIdeas(host, ideas) {
  if (ideas?.length === 1) return describeIdea(host, ideas[0]);
  const text = TEXT[ideas?.[0]?.kind];
  if (!text?.many) return null;
  const names = ideas.map((i) => i.camera?.name || "");
  const values = {
    ...(ideas[0].facts || {}),
    camera: cameraList(host, names, "disjunction"),
    cameras: cameraList(host, names, "conjunction"),
    pairs: ideas
      .map((i) => `${i.facts?.trigger} (${i.camera?.entity_id})`)
      .join(", "),
  };
  const t = ([key, fallback]) => interpolate(host._t(key, fallback), values);
  return {
    icon: text.icon,
    title: t(text.title),
    why: [t(text.many.why)],
    prompt: ideas[0].facts?.image
      ? `${t(text.many.prompt)} ${t(SNAPSHOT_MANY)}`
      : t(text.many.prompt),
  };
}

/** The chat message an idea sends: its title, the entities folded away. */
export function ideaMessage(host, idea) {
  const d = describeIdea(host, idea);
  return d ? d.title + buildDetailsMarker(d.prompt) : "";
}

// camera entity → {at, ideas}. Asked again after a minute, so an idea whose
// automation was just accepted goes away (the backend drops an idea once an
// automation uses its trigger).
const IDEAS = new Map();
const FRESH_MS = 60 * 1000;

async function loadIdeas(host, cameraEntity) {
  const previous = IDEAS.get(cameraEntity);
  IDEAS.set(cameraEntity, { at: Date.now(), ideas: previous?.ideas || [] });
  try {
    const result = await host.hass.callWS({
      type: "selora_ai/frigate/ideas",
      camera_entity: cameraEntity,
    });
    IDEAS.set(cameraEntity, { at: Date.now(), ideas: result?.ideas || [] });
  } catch (err) {
    console.error("Failed to load camera ideas", err);
  }
  host.requestUpdate?.();
}

/** Up to three idea chips for a live camera; asks the backend at most once a minute. */
export function renderIdeaChips(host, cameraEntity) {
  const loaded = IDEAS.get(cameraEntity);
  if (!loaded || Date.now() - loaded.at > FRESH_MS) {
    loadIdeas(host, cameraEntity);
    if (!loaded) return "";
  }
  const ideas = loaded.ideas.slice(0, MAX_CHIPS);
  if (!ideas.length) return "";
  return html`
    <div class="camera-ideas">
      <span class="camera-ideas-label"
        >${host._t("camera_ideas_label", "Ideas")}</span
      >
      ${ideas.map((idea) => {
        const d = describeIdea(host, idea);
        if (!d) return "";
        return html`<button
          class="camera-idea-chip"
          title=${d.title}
          @click=${() => host._quickStart(ideaMessage(host, idea))}
        >
          <ha-icon icon=${d.icon}></ha-icon>
          <span>${d.chip}</span>
        </button>`;
      })}
    </div>
  `;
}
