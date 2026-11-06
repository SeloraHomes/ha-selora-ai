// "I noticed a few things I can help with" — suggestions drawn from how the
// home is organised (home_suggestions.py) and how it is used
// (usage_suggestions.py), shown on the chat home. They are the only place
// Selora proposes something unasked: the Automations tab lists what exists.
//
// A row opens to say why Selora suggests it and what it changes; acting on it
// is an ordinary chat request: the prompt names the entities and the group or
// automation, and the usual tools and cards do the work, so the user sees
// every change before it is saved. The prompt is localized like any message
// the user types; entity ids ride along so the model needs no search to find
// them. The bubble shows the row's title and folds the prompt away.
//
// The chat home has room for three rows, so the rest come a page at a time:
// "Other ideas" swaps in the next three, on demand, never on a timer, and
// wraps back to the first. The page is not remembered; the chat home opens on
// the top three. A row is put away for good (dismiss) or for a week (snooze,
// the server's ``_SNOOZE_FOR``), and the next one moves up into its place.
//
// A suggestion that changes an automation opens it the way "Refine in chat"
// does: a fresh chat only edits what it was given, so without the automation
// loaded the model can make the group but never propose the edited rule.
import { html } from "lit";
import { keyed } from "lit/directives/keyed.js";

import { renderActionButton } from "../shared/action-button.js";
import { renderCardIcon } from "../shared/card-heading.js";
import { interpolate, pickLocale } from "../shared/i18n.js";
import { buildDetailsMarker } from "./chat-autocomplete.js";
import { renderFlowEntityLink } from "./entity-link.js";
import { describeIdea, describeIdeas } from "./camera-ideas.js";

const MAX_SHOWN = 3;
const MAX_NAMED = 3;

export async function loadHomeSuggestions(host) {
  try {
    host._homeSuggestions =
      (await host.hass.callWS({ type: "selora_ai/home_suggestions" })) || [];
  } catch (err) {
    console.error("Failed to load home suggestions", err);
    // Nothing to show is still loaded: the chat home offers its starters.
    host._homeSuggestions ??= [];
  }
}

/** Take *suggestion* off the list; *how* is "dismiss" (for good) or "snooze". */
async function putAway(host, suggestion, how) {
  host._homeSuggestions = (host._homeSuggestions || []).filter(
    (s) => s.suggestion_id !== suggestion.suggestion_id,
  );
  try {
    await host.hass.callWS({
      type: `selora_ai/home_suggestions/${how}`,
      suggestion_id: suggestion.suggestion_id,
    });
  } catch (err) {
    console.error(`Failed to ${how} home suggestion`, err);
    return;
  }
  // Discovered devices and Frigate steps come one at a time: the server has
  // the next one in line, which the list held here never had.
  await loadHomeSuggestions(host);
}

/** Show the page after *shown*, the one on screen, not the one stored: putting rows away may have clamped it. */
function turnPage(host, shown, pages) {
  host._homeSuggestionPage = (shown + 1) % pages;
  host._homeSuggestionsTurned = true;
  host._expandedHomeSuggestion = null;
}

function toggle(host, suggestion) {
  host._expandedHomeSuggestion =
    host._expandedHomeSuggestion === suggestion.suggestion_id
      ? null
      : suggestion.suggestion_id;
}

async function act(host, suggestion, d) {
  // What the user says in the chat it opens, when the title is not a request.
  const said = d.ask || d.title;
  const message = said + buildDetailsMarker(d.prompt);
  const guide = SERVER_GUIDES[suggestion.type];
  if (guide) {
    // Every question in it is known, so the server writes the chat: no model.
    if (await openServerGuide(host, guide, said)) return;
  }
  const automationId = suggestion.collection.automation_id;
  if (!automationId) {
    await host._askInNewChat(message, { guide: guideFor(suggestion, d) });
    return;
  }
  try {
    const { session_id } = await host.hass.callWS({
      type: "selora_ai/load_automation_to_session",
      automation_id: automationId,
    });
    host._setActiveTab("chat");
    await host._openSession(session_id);
    // _openSession reports a failure only as a toast; sending anyway would
    // put this request in whichever conversation was open before.
    if (host._activeSessionId !== session_id) return;
    host._input = message;
    host._sendMessage();
  } catch (err) {
    console.error("Failed to open the automation in chat", err);
    await host._askInNewChat(message);
  }
}

// A Frigate setup guide opens under the Frigate banner. The describe and faces
// chats the server writes carry it on their own first card; this is their
// model fallback.
const FRIGATE_GUIDES = new Set([
  "add_to_frigate",
  "recognize_faces",
  "describe_cameras",
]);

function guideFor(suggestion, d) {
  if (!FRIGATE_GUIDES.has(suggestion.type)) return undefined;
  return {
    icon: d.icon,
    // A heading, not a paragraph: the row's reasons stay in the row. The
    // server's limit cuts a long list of camera names rather than refusing it.
    title: d.title.slice(0, 200),
    subtitle: "",
    banner: "frigate",
  };
}

// The guides the server writes, by suggestion type: the command that opens one.
const SERVER_GUIDES = {
  describe_cameras: "selora_ai/frigate/describe_start",
  recognize_faces: "selora_ai/frigate/faces_start",
};

async function openServerGuide(host, type, title) {
  try {
    const { session_id } = await host.hass.callWS({
      type,
      title,
      ...(host.hass?.language ? { language: host.hass.language } : {}),
    });
    host._setActiveTab("chat");
    await host._openSession(session_id);
    return true;
  } catch (err) {
    // Only a server without the command hands the request to the model. A
    // refusal is the server's answer (every camera already has one, Frigate
    // is down): a model chat would ask the same questions again. The row was
    // loaded earlier, so the list is read again.
    if (err?.code === "unknown_command") return false;
    host._showToast?.(err?.message || String(err), "error");
    await loadHomeSuggestions(host);
    return true;
  }
}

function names(entities) {
  return entities.map((e) => e.name).join(", ");
}

// A camera found on the network has no entity; its address names it.
function ids(entities) {
  return entities.map((e) => e.entity_id || e.address).join(", ");
}

// "Front Door, Back Door and 2 more", in the panel's language.
function nameList(host, entities) {
  // A camera found on the network is told apart by its address.
  const shown = entities
    .slice(0, MAX_NAMED)
    .map((e) => (e.address ? `${e.name} (${e.address})` : e.name));
  const rest = entities.length - shown.length;
  if (rest > 0) {
    shown.push(
      interpolate(host._t("home_suggestion_more", "{count} more"), {
        count: rest,
      }),
    );
  }
  try {
    return new Intl.ListFormat(pickLocale(host.hass), {
      type: "conjunction",
    }).format(shown);
  } catch {
    return shown.join(", ");
  }
}

// The same list for the open row's reasons, each entity a link to its
// more-info dialog, so the user can check what Selora saw (when a light was
// last switched, what it reads now) before acting on it. "N more" shows the
// rest. A camera found on the network has no entity, so it stays text.
function linkedNameList(host, s, entities) {
  const all = host._homeSuggestionAllNames === s.suggestion_id;
  const named = all ? entities : entities.slice(0, MAX_NAMED);
  const parts = named.map((e) => {
    if (e.entity_id && host.hass?.states?.[e.entity_id]) {
      return renderFlowEntityLink(host, e.entity_id);
    }
    return e.address ? `${e.name} (${e.address})` : e.name;
  });
  const rest = entities.length - named.length;
  if (rest > 0) {
    parts.push(
      html`<button
        type="button"
        class="flow-entity-link"
        @click=${() => {
          host._homeSuggestionAllNames = s.suggestion_id;
          host.requestUpdate?.();
        }}
      >
        <span
          >${interpolate(host._t("home_suggestion_more", "{count} more"), {
            count: rest,
          })}</span
        >
      </button>`,
    );
  }
  let pieces;
  try {
    pieces = new Intl.ListFormat(pickLocale(host.hass), {
      type: "conjunction",
    }).formatToParts(parts.map((_, i) => String(i)));
  } catch {
    return parts.flatMap((p, i) => (i ? [", ", p] : [p]));
  }
  let i = 0;
  return pieces.map((p) => (p.type === "element" ? parts[i++] : p.value));
}

// The pattern engine's suggestion: its own words for the title and the why,
// and its YAML for the model to start from, so the chat proposes the
// automation that was observed rather than one it imagines.
function describeAutomationIdea(host, s) {
  const auto = s.automation || {};
  const values = {
    name: auto.alias || auto.description || "",
    description: auto.description || auto.alias || "",
    evidence: s.evidence || "",
    yaml: auto.yaml || "",
  };
  const tv = (key, fallback) => interpolate(host._t(key, fallback), values);
  const why = [
    values.evidence
      ? tv("home_suggestion_automation_why", "Selora noticed: {evidence}.")
      : tv(
          "home_suggestion_automation_why_plain",
          "You do this often, so Selora can do it for you.",
        ),
  ];
  if (values.description !== values.name) why.unshift(values.description);
  return {
    icon: "mdi:robot-outline",
    title: values.name,
    prompt: tv(
      values.yaml
        ? "home_suggestion_automation_prompt"
        : "home_suggestion_automation_prompt_plain",
      values.yaml
        ? "Create the automation {name}: {description} Start from this YAML, and change it only where an entity no longer fits:\n{yaml}"
        : "Create the automation {name}: {description}",
    ),
    why,
  };
}

// { icon, title, prompt, why: [sentences] } for a suggestion, or null for a
// type this bundle does not know (a newer backend).
function describe(host, s) {
  const grouping = s.type === "group_entities";
  const listsEntities =
    grouping ||
    s.type === "add_to_frigate" ||
    s.type === "describe_cameras" ||
    s.type === "create_scene";
  const values = {
    entity: names(s.entities),
    entity_id: ids(s.entities),
    collection: s.collection.name,
    ref: s.collection.ref,
    count: s.entities.length,
    members: nameList(host, listsEntities ? s.entities : s.members || []),
    area: s.area || "",
    waiting: s.waiting || 0,
    days: s.days || 0,
  };
  const t = (key, fallback) => interpolate(host._t(key, fallback), values);
  // A reason, with the entities it names as links.
  const linked = {
    "{members}": listsEntities ? s.entities : s.members || [],
    "{entity}": s.entities,
  };
  const tl = (key, fallback) =>
    host
      ._t(key, fallback)
      .split(/(\{members\}|\{entity\})/)
      .map((part) =>
        linked[part]
          ? linkedNameList(host, s, linked[part])
          : interpolate(part, values),
      );
  if (s.type === "automation_idea") return describeAutomationIdea(host, s);
  if (s.type === "create_scene") {
    // Each device with the state the scene sets it to, for the model.
    values.settings = s.entities
      .map((e) => `${e.name} (${e.entity_id}): ${e.state}`)
      .join(", ");
    return {
      icon: "mdi:palette-outline",
      title: t("home_suggestion_scene", "Make a scene for {members}"),
      prompt: t(
        "home_suggestion_scene_prompt",
        "Create a scene that sets {settings}. Name it after what it is for, and the room if they share one.",
      ),
      why: [
        tl(
          "home_suggestion_scene_why",
          "On {days} different days you set {members} the same way, within a couple of minutes. A scene does it in one tap.",
        ),
      ],
    };
  }
  if (grouping) {
    return {
      icon: "mdi:group",
      title: t(
        "home_suggestion_group",
        "Group the {count} devices that trigger {collection}",
      ),
      prompt: t(
        "home_suggestion_group_prompt",
        "Group {entity} ({entity_id}) into one group helper, and update the automation {collection} ({ref}) to use the group instead of listing them. Reuse an existing group if one already holds exactly these.",
      ),
      why: [
        tl(
          "home_suggestion_group_why",
          "{collection} lists {members} one by one. With a group, a new device only needs adding to the group and the automation follows.",
        ),
        t(
          "home_suggestion_group_caveat",
          "It then runs when the group as a whole changes, not again for each device while another one is still active.",
        ),
      ],
    };
  }
  if (s.type === "set_up_discovered") {
    // One device per chat, named as its Discovered card names it: the model
    // finds the discovery itself, so the bubble carries no ids.
    const why = [
      tl(
        "home_suggestion_discovered_why",
        "Home Assistant found {entity} through {collection}, but nobody has set it up yet. Selora walks you through it; a password or a sign-in, you type in yourself, right in the chat.",
      ),
    ];
    if (values.waiting) {
      why.push(
        t(
          "home_suggestion_discovered_waiting",
          "Other devices Home Assistant found, waiting after this one: {waiting}.",
        ),
      );
    }
    return {
      icon: "mdi:plus-network-outline",
      title: t("home_suggestion_discovered", "Set up {entity}"),
      prompt: t(
        "home_suggestion_discovered_prompt",
        "Set up {entity}, which Home Assistant discovered through {collection}.",
      ),
      why,
    };
  }
  if (s.type === "camera_idea") {
    // A row naming several cameras asks for one automation covering them all.
    const d = s.ideas
      ? describeIdeas(host, s.ideas)
      : describeIdea(host, s.idea);
    return d
      ? { icon: d.icon, title: d.title, prompt: d.prompt, why: d.why }
      : null;
  }
  if (s.type === "add_to_frigate") {
    return {
      icon: "mdi:cctv",
      title:
        s.entities.length === 1
          ? t("home_suggestion_frigate_one", "Add {entity} to Selora Vision")
          : t(
              "home_suggestion_frigate",
              "Add {count} cameras to Selora Vision",
            ),
      prompt: t(
        "home_suggestion_frigate_prompt",
        "Add these cameras to Selora Vision: {entity} ({entity_id}).",
      ),
      why: [
        tl(
          "home_suggestion_frigate_why",
          "Selora Vision is not watching {members} yet. Once added, it spots people as they appear and keeps recordings of what it saw.",
        ),
      ],
    };
  }
  if (s.type === "describe_cameras") {
    return {
      icon: "mdi:text-box-search-outline",
      title:
        s.entities.length === 1
          ? t(
              "home_suggestion_describe_one",
              "Search what {entity} saw, in plain words",
            )
          : t(
              "home_suggestion_describe",
              "Search what your cameras saw, in plain words",
            ),
      prompt: t(
        "home_suggestion_describe_prompt",
        "Ask me, one camera at a time, whether Selora AI should describe what these cameras see: {entity}.",
      ),
      why: [
        t(
          "home_suggestion_describe_why",
          "Selora Vision only knows it saw “a car” or “a person”. Selora AI can describe each one, like “a white FedEx van in the driveway” or “someone leaving a parcel at the door”, so you can simply ask “Did a delivery come today?”",
        ),
        tl(
          "home_suggestion_describe_how",
          "Pick which cameras to describe: {members}. It takes a minute, and no picture leaves your home until you say yes.",
        ),
      ],
      act: t("home_suggestion_describe_act", "Choose cameras"),
      ask: t(
        "home_suggestion_describe_ask",
        "Help me choose which cameras Selora AI describes",
      ),
    };
  }
  if (s.type === "recognize_faces") {
    return s.state === "empty"
      ? {
          icon: "mdi:face-recognition",
          title: t(
            "home_suggestion_faces_empty",
            "Add the people Selora Vision should recognize",
          ),
          prompt: t(
            "home_suggestion_faces_empty_prompt",
            "Help me add the people Selora Vision should recognize: where to add them, and which photos work best.",
          ),
          why: [
            t(
              "home_suggestion_faces_empty_why",
              "Face recognition is on, but Selora Vision does not know anyone yet. Add a few photos of each person in its Face Library.",
            ),
          ],
        }
      : {
          icon: "mdi:face-recognition",
          title: t(
            "home_suggestion_faces",
            "Recognize the people you know on your cameras",
          ),
          prompt: t(
            "home_suggestion_faces_prompt",
            "Help me set up face recognition in Selora Vision: tell me what it does and what it needs, then ask me before turning it on.",
          ),
          why: [
            t(
              "home_suggestion_faces_why",
              "Selora Vision can tell who is at the door once it knows their faces. It runs on your hub, and needs a few photos of each person.",
            ),
          ],
        };
  }
  if (s.type !== "add_to_collection") return null;
  const why = [
    s.area
      ? tl(
          "home_suggestion_why_area",
          "{collection} already covers {members}: every one of this kind in {area}.",
        )
      : tl(
          "home_suggestion_why_home",
          "{collection} already covers {members}: every one of this kind in your home.",
        ),
    tl(
      "home_suggestion_why_newer",
      "{entity} was added after them, so it was probably not left out on purpose.",
    ),
  ];
  if (s.collection.source === "automation") {
    return {
      icon: "mdi:robot-outline",
      title: t(
        "home_suggestion_add_automation",
        "Include {entity} in {collection}",
      ),
      prompt: t(
        "home_suggestion_add_automation_prompt",
        "Update the automation {collection} ({ref}) so it also covers {entity} ({entity_id}), the same way it covers the others like it. If Home Assistant already has one entity that follows all of them together (a group, or a zone counting the people in it), offer that as an alternative: new ones are then covered without editing the automation again.",
      ),
      why,
    };
  }
  return {
    icon: "mdi:plus-circle-outline",
    title: t("home_suggestion_add_group", "Add {entity} to {collection}"),
    prompt: t(
      "home_suggestion_add_group_prompt",
      "Add {entity} ({entity_id}) to the group {collection} ({ref}).",
    ),
    why,
  };
}

// The list loads after the chat home is on screen, and the home is centred:
// shown at once, it would push the composer up in one frame. Arriving late,
// it grows open instead (the composer slides with it) and its rows come in
// one by one. Shown from the first frame, nothing moves, so nothing animates.
// The class stays a moment past the animation, not for good: lit re-renders
// often, and the element is recreated each time the chat home opens.
const ENTER_MS = 1500;

function reveal(host, content) {
  if (host._homeSuggestionsPending) {
    host._homeSuggestionsPending = false;
    host._homeSuggestionsEnterAt = Date.now();
  }
  const entering = Date.now() - (host._homeSuggestionsEnterAt || 0) < ENTER_MS;
  return html`<div
    class="home-suggestions-reveal ${entering ? "entering" : ""}"
  >
    ${content}
  </div>`;
}

// A home Selora has nothing to say about yet gets a few things to ask instead,
// in the same rows: the chat home shows one list, never two. Not before the
// suggestions have loaded, so the starters don't flash and get swapped out.
function renderStarters(host, starters) {
  if (!starters.length || host._homeSuggestions === undefined) return "";
  return reveal(
    host,
    html`
      <div class="home-suggestions">
        <div class="home-suggestions-heading">
          ${host._t("home_suggestions_starters_heading", "Try asking")}
        </div>
        ${starters.map(
          (s) => html`
            <div class="home-suggestion">
              <div class="home-suggestion-row">
                <button
                  class="home-suggestion-main"
                  @click=${() => host._quickStart(s.value)}
                >
                  ${renderCardIcon(s.icon)}
                  <span class="home-suggestion-title">${s.label}</span>
                  <ha-icon
                    class="home-suggestion-chevron"
                    icon="mdi:arrow-right"
                  ></ha-icon>
                </button>
              </div>
            </div>
          `,
        )}
      </div>
    `,
  );
}

/** The chat home's suggestions, or *starters* ({label, value, icon}) when there are none. */
export function renderHomeSuggestions(host, starters = []) {
  const all = (host._homeSuggestions || [])
    .map((s) => [s, describe(host, s)])
    .filter(([, d]) => d);
  const pages = Math.ceil(all.length / MAX_SHOWN);
  // Putting rows away can leave the last page empty: show the one before.
  const page = Math.min(host._homeSuggestionPage || 0, Math.max(pages - 1, 0));
  const rows = all.slice(page * MAX_SHOWN, (page + 1) * MAX_SHOWN);
  if (!rows.length) {
    const out = renderStarters(host, starters);
    if (!out) host._homeSuggestionsPending = true;
    return out;
  }
  return reveal(
    host,
    html`
      <div class="home-suggestions">
        <div class="home-suggestions-heading">
          ${host._t(
            "home_suggestions_heading",
            "I noticed a few things I can help with",
          )}
        </div>
        ${keyed(
          page,
          html`<div
            class="home-suggestions-page ${
              host._homeSuggestionsTurned ? "turned" : ""
            }"
          >
            ${rows.map(([s, d]) => renderRow(host, s, d))}
          </div>`,
        )}
        ${
          pages > 1
            ? html`<button
                class="home-suggestions-other"
                @click=${() => turnPage(host, page, pages)}
              >
                <ha-icon icon="mdi:refresh"></ha-icon>
                ${host._t("home_suggestions_other", "Other ideas")}
              </button>`
            : ""
        }
      </div>
    `,
  );
}

function renderRow(host, s, d) {
  const open = host._expandedHomeSuggestion === s.suggestion_id;
  return html`
    <div class="home-suggestion ${open ? "open" : ""}">
      <div class="home-suggestion-row">
        <button
          class="home-suggestion-main"
          aria-expanded=${open ? "true" : "false"}
          @click=${() => toggle(host, s)}
        >
          ${renderCardIcon(d.icon)}
          <span class="home-suggestion-title">${d.title}</span>
          <ha-icon
            class="home-suggestion-chevron"
            icon="mdi:chevron-down"
          ></ha-icon>
        </button>
        <button
          class="home-suggestion-dismiss"
          title=${host._t("home_suggestion_snooze", "Remind me in a week")}
          aria-label=${host._t("home_suggestion_snooze", "Remind me in a week")}
          @click=${() => putAway(host, s, "snooze")}
        >
          <ha-icon icon="mdi:clock-outline"></ha-icon>
        </button>
        <button
          class="home-suggestion-dismiss"
          title=${host._t("home_suggestion_dismiss", "Dismiss")}
          aria-label=${host._t("home_suggestion_dismiss", "Dismiss")}
          @click=${() => putAway(host, s, "dismiss")}
        >
          <ha-icon icon="mdi:close"></ha-icon>
        </button>
      </div>
      ${
        open
          ? html`<div class="home-suggestion-detail">
              ${d.why.map((line) => html`<p>${line}</p>`)}
              <div class="home-suggestion-footer">
                ${renderActionButton({
                  label:
                    d.act ||
                    host._t("home_suggestion_do_it", "Ask Selora to do it"),
                  tone: "primary",
                  onClick: () => act(host, s, d),
                })}
              </div>
            </div>`
          : ""
      }
    </div>
  `;
}
