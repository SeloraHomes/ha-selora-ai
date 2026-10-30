// "I noticed a few things I can help with" — suggestions drawn from how the home is organised
// (home_suggestions.py), shown on the chat home.
//
// A row opens to say why Selora suggests it and what it changes; acting on it
// is an ordinary chat request: the prompt names the entities and the group or
// automation, and the usual tools and cards do the work, so the user sees
// every change before it is saved. The prompt is localized like any message
// the user types; entity ids ride along so the model needs no search to find
// them. The bubble shows the row's title and folds the prompt away.
//
// A suggestion that changes an automation opens it the way "Refine in chat"
// does: a fresh chat only edits what it was given, so without the automation
// loaded the model can make the group but never propose the edited rule.
import { html } from "lit";

import { renderActionButton } from "../shared/action-button.js";
import { renderCardIcon } from "../shared/card-heading.js";
import { interpolate, pickLocale } from "../shared/i18n.js";
import { buildDetailsMarker } from "./chat-autocomplete.js";
import { describeIdea } from "./camera-ideas.js";

const MAX_SHOWN = 3;
const MAX_NAMED = 3;

export async function loadHomeSuggestions(host) {
  try {
    host._homeSuggestions =
      (await host.hass.callWS({ type: "selora_ai/home_suggestions" })) || [];
  } catch (err) {
    console.error("Failed to load home suggestions", err);
  }
}

async function dismiss(host, suggestion) {
  host._homeSuggestions = (host._homeSuggestions || []).filter(
    (s) => s.suggestion_id !== suggestion.suggestion_id,
  );
  try {
    await host.hass.callWS({
      type: "selora_ai/home_suggestions/dismiss",
      suggestion_id: suggestion.suggestion_id,
    });
  } catch (err) {
    console.error("Failed to dismiss home suggestion", err);
  }
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
  if (suggestion.type === "describe_cameras") {
    // Every question in it is known, so the server writes the chat: no model.
    if (await openDescribeChat(host, said)) return;
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

// A Frigate setup guide opens under the Frigate banner. The describe chat the
// server writes carries it on its own first card; this is its model fallback.
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

async function openDescribeChat(host, title) {
  try {
    const { session_id } = await host.hass.callWS({
      type: "selora_ai/frigate/describe_start",
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

// { icon, title, prompt, why: [sentences] } for a suggestion, or null for a
// type this bundle does not know (a newer backend).
function describe(host, s) {
  const grouping = s.type === "group_entities";
  const listsEntities =
    grouping || s.type === "add_to_frigate" || s.type === "describe_cameras";
  const values = {
    entity: names(s.entities),
    entity_id: ids(s.entities),
    collection: s.collection.name,
    ref: s.collection.ref,
    count: s.entities.length,
    members: nameList(host, listsEntities ? s.entities : s.members || []),
    area: s.area || "",
  };
  const t = (key, fallback) => interpolate(host._t(key, fallback), values);
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
        t(
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
  if (s.type === "camera_idea") {
    const d = describeIdea(host, s.idea);
    return d
      ? { icon: d.icon, title: d.title, prompt: d.prompt, why: d.why }
      : null;
  }
  if (s.type === "add_to_frigate") {
    return {
      icon: "mdi:cctv",
      title:
        s.entities.length === 1
          ? t("home_suggestion_frigate_one", "Add {entity} to Frigate")
          : t("home_suggestion_frigate", "Add {count} cameras to Frigate"),
      prompt: t(
        "home_suggestion_frigate_prompt",
        "Add these cameras to Frigate: {entity} ({entity_id}).",
      ),
      why: [
        t(
          "home_suggestion_frigate_why",
          "Frigate is not watching {members} yet. Once added, it spots people as they appear and keeps recordings of what it saw.",
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
          "Frigate only knows it saw “a car” or “a person”. Selora AI can describe each one, like “a white FedEx van in the driveway” or “someone leaving a parcel at the door”, so you can simply ask “Did a delivery come today?”",
        ),
        t(
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
            "Add the people Frigate should recognize",
          ),
          prompt: t(
            "home_suggestion_faces_empty_prompt",
            "Help me add the people Frigate should recognize: where to add them, and which photos work best.",
          ),
          why: [
            t(
              "home_suggestion_faces_empty_why",
              "Face recognition is on, but Frigate does not know anyone yet. Add a few photos of each person in its Face Library.",
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
            "Help me set up face recognition in Frigate: tell me what it does and what it needs, then ask me before turning it on.",
          ),
          why: [
            t(
              "home_suggestion_faces_why",
              "Frigate can tell who is at the door once it knows their faces. It runs on your hub, and needs a few photos of each person.",
            ),
          ],
        };
  }
  if (s.type !== "add_to_collection") return null;
  const why = [
    s.area
      ? t(
          "home_suggestion_why_area",
          "{collection} already covers {members}: every one of this kind in {area}.",
        )
      : t(
          "home_suggestion_why_home",
          "{collection} already covers {members}: every one of this kind in your home.",
        ),
    t(
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

export function renderHomeSuggestions(host) {
  const rows = (host._homeSuggestions || [])
    .map((s) => [s, describe(host, s)])
    .filter(([, d]) => d)
    .slice(0, MAX_SHOWN);
  if (!rows.length) return "";
  return html`
    <div class="home-suggestions">
      <div class="home-suggestions-heading">
        ${host._t(
          "home_suggestions_heading",
          "I noticed a few things I can help with",
        )}
      </div>
      ${rows.map(([s, d]) => {
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
                title=${host._t("home_suggestion_dismiss", "Dismiss")}
                aria-label=${host._t("home_suggestion_dismiss", "Dismiss")}
                @click=${() => dismiss(host, s)}
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
                          host._t(
                            "home_suggestion_do_it",
                            "Ask Selora to do it",
                          ),
                        tone: "primary",
                        onClick: () => act(host, s, d),
                      })}
                    </div>
                  </div>`
                : ""
            }
          </div>
        `;
      })}
    </div>
  `;
}
