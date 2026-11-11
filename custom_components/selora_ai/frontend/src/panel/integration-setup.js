// The setup card: a step of an integration's setup the user fills in
// themselves, in the chat. `add_integration` hands a step over when it asks
// for a login or a sign-in in the browser, which never goes through the model:
// this card reads the step from Home Assistant's own flow endpoint and posts
// what the user types straight back to it, as Home Assistant's dialog does.
// Nothing typed here reaches the conversation or the server's report.
//
// The step's words are the integration's own (its `config` translations), so
// the card says what Home Assistant's dialog would.
import { html } from "lit";

import { renderActionButton } from "../shared/action-button.js";
import { interpolate } from "../shared/i18n.js";

// The card's flow_id → { flowId, status, step, values, texts, error }, where
// flowId is the flow it is on now: Check again starts another. In memory only: a login
// typed on the card is never saved, and a reload reads the step again. A card
// that is done keeps only that it is gone, so what was typed on it (a
// password) does not stay for the life of the tab; the oldest cards go first
// past MAX_FLOWS, and one drawn again reads its step again.
const FLOWS = new Map();
const MAX_FLOWS = 20;

/** Another step to fill in: the card stays open, showing it. */
export class SetupStepError extends Error {}

const SECRET = /pass|token|secret|api_?key|pin|code/i;

async function readTexts(hass, domain) {
  try {
    const { resources } = await hass.callWS({
      type: "frontend/get_translations",
      language: hass.language || "en",
      category: "config",
      integration: [domain],
    });
    return resources || {};
  } catch {
    return {};
  }
}

function text(state, action, key, placeholders) {
  const said = state.texts?.[`component.${action.domain}.config.${key}`];
  return said ? interpolate(said, placeholders || {}) : "";
}

function defaults(schema) {
  const values = {};
  for (const field of schema || []) {
    if (field.type === "expandable")
      values[field.name] = defaults(field.schema);
    else if (field.default !== undefined) values[field.name] = field.default;
    // What the control shows is what Continue sends: a required select shows
    // its first option and a checkbox shows unticked, with no change event
    // until the user picks something else.
    else if (field.required && isSelect(field)) {
      const [first] = options(field);
      if (first) values[field.name] = first.value;
    } else if (field.required && isBoolean(field)) values[field.name] = false;
  }
  return values;
}

function show(state, step) {
  state.step = step;
  state.status = step.type;
  state.error = "";
  if (step.type === "form") state.values = defaults(step.data_schema);
  if (step.type === "menu") state.values = {};
}

/** The card is done: keep that it is gone, and its words while it still
 * shows a reason with Check again, never what was typed on it. */
function retire(state, { keepTexts = false } = {}) {
  state.status = "gone";
  state.values = {};
  state.step = undefined;
  if (!keepTexts) state.texts = {};
}

/** The card was declined: its setup is over, whatever it was showing. */
export function forgetIntegrationSetup(cardId) {
  const state = FLOWS.get(String(cardId || ""));
  if (state) {
    retire(state);
    state.stuck = false;
  }
}

/** Read the step the flow is on, once per flow; the card redraws when it lands. */
function load(host, action) {
  const flowId = String(action.flow_id || "");
  if (FLOWS.has(flowId)) return FLOWS.get(flowId);
  const state = { flowId, status: "loading", values: {}, texts: {} };
  FLOWS.set(flowId, state);
  while (FLOWS.size > MAX_FLOWS) FLOWS.delete(FLOWS.keys().next().value);
  host.hass
    .callApi("GET", `config/config_entries/flow/${flowId}`)
    // The words only for a step there is to show.
    .then(async (step) => {
      state.texts = await readTexts(host.hass, action.domain);
      show(state, step);
    })
    .catch(() => retire(state))
    .finally(() => host.requestUpdate?.());
  return state;
}

function body(state) {
  if (state.status === "menu") return { next_step_id: state.values.choice };
  // Only what the user filled in: an empty optional field is left out, as
  // Home Assistant's own form leaves it.
  const clean = (values) =>
    Object.fromEntries(
      Object.entries(values)
        .filter(([, v]) => v !== "" && v !== undefined && v !== null)
        .map(([k, v]) =>
          v && typeof v === "object" && !Array.isArray(v)
            ? [k, clean(v)]
            : [k, v],
        ),
    );
  return clean(state.values || {});
}

/**
 * The card's button: answer the step. Resolves with the new entry once the
 * setup is done; throws SetupStepError while there is another step to fill in.
 */
export async function submitIntegrationSetup(hass, action) {
  const cardId = String(action.flow_id || "");
  const state = FLOWS.get(cardId);
  // Still being read: nothing to answer yet, and the card stays.
  if (state?.status === "loading") throw new SetupStepError("loading");
  if (!state || state.status === "gone") {
    throw new Error("That setup is no longer open.");
  }
  const flowId = state.flowId;
  // Continue on a step that only asks the user to act retries it; whether
  // that moved the setup on decides whether to offer starting over.
  const acted = asksToAct(state);
  let result;
  try {
    // A sign-in in the browser, or work in progress, moves on by itself:
    // reading the flow again picks up where it is.
    result =
      state.status === "external" || state.status === "progress"
        ? await hass.callApi("GET", `config/config_entries/flow/${flowId}`)
        : await hass.callApi(
            "POST",
            `config/config_entries/flow/${flowId}`,
            body(state),
          );
  } catch (err) {
    state.error = err?.body?.message || err?.message || String(err);
    throw new SetupStepError(state.error);
  }
  if (result.type === "create_entry") {
    FLOWS.delete(cardId);
    return { entry_id: result.result?.entry_id, title: result.title };
  }
  if (result.type === "abort") {
    const reason =
      text(
        state,
        action,
        `abort.${result.reason}`,
        result.description_placeholders,
      ) || result.reason;
    if (acted) {
      // What the user did on the device was not enough for this setup: the
      // card stays, with the reason and a fresh start.
      retire(state, { keepTexts: true });
      state.error = reason;
      state.stuck = true;
      throw new SetupStepError(reason);
    }
    FLOWS.delete(cardId);
    throw new Error(reason);
  }
  const errors = result.errors;
  const before = state.step?.step_id;
  show(state, result);
  state.stuck = acted && asksToAct(state) && state.step.step_id === before;
  if (result.type === "external" && result.url) {
    window.open(result.url, "_blank", "noopener");
  }
  throw new SetupStepError(errors ? "" : "next step");
}

/**
 * Check again: end this setup and start the integration's afresh. Home
 * Assistant checks some things (a device's access or pairing settings) only
 * when a setup starts, so a change made on the device for a step is never
 * seen by the setup that asked for it. Offered only once Continue did not
 * move the setup on: most such steps (a TV's Allow prompt) are retried by
 * Continue, and starting over loses a discovered device's address. Pressed
 * by the user, as closing Home Assistant's own dialog ends a flow.
 *
 * The server starts it, not this card: the new flow is handed off as the
 * first was, so declining the card (which names the card's flow_id) or
 * leaving it ends the new one too.
 */
async function restart(host, action, state) {
  state.status = "loading";
  state.error = "";
  state.stuck = false;
  host.requestUpdate?.();
  try {
    const started = await host.hass.callWS({
      type: "selora_ai/integration_setup_restart",
      flow_id: String(action.flow_id || ""),
    });
    if (started.reason) {
      state.error =
        text(
          state,
          action,
          `abort.${started.reason}`,
          started.description_placeholders,
        ) || started.reason;
      retire(state);
    } else {
      state.flowId = started.flow_id;
      show(
        state,
        await host.hass.callApi(
          "GET",
          `config/config_entries/flow/${started.flow_id}`,
        ),
      );
    }
  } catch (err) {
    retire(state);
    state.error = err?.body?.message || err?.message || String(err);
  }
  host.requestUpdate?.();
}

function checkAgain(host, action, state) {
  return html`<div class="setup-again">
    ${renderActionButton({
      label: host._t("setup_check_again", "Check again"),
      mark: "mdi:refresh",
      onClick: () => restart(host, action, state),
    })}
  </div>`;
}

/** Whether the step only asks the user to act: nothing to fill in. */
function asksToAct(state) {
  return (
    state.status === "form" &&
    !(state.step.data_schema || []).some((f) => f.type !== "constant")
  );
}

function setValue(host, state, path, value) {
  let target = state.values;
  for (const key of path.slice(0, -1)) target = target[key] ??= {};
  target[path.at(-1)] = value;
  host.requestUpdate?.();
}

function options(field) {
  const raw = field.selector?.select?.options ?? field.options ?? [];
  const list = Array.isArray(raw) ? raw : Object.entries(raw);
  return list.map((o) =>
    Array.isArray(o)
      ? { value: o[0], label: o[1] ?? o[0] }
      : typeof o === "object"
        ? { value: o.value, label: o.label ?? o.value }
        : { value: o, label: o },
  );
}

function isBoolean(field) {
  return field.type === "boolean" || Boolean(field.selector?.boolean);
}

function isSelect(field) {
  return (
    field.type === "select" ||
    Boolean(field.selector?.select) ||
    Boolean(field.options)
  );
}

function control(host, state, field, path, value) {
  const set = (v) => setValue(host, state, path, v);
  const selector = field.selector || {};
  if (isBoolean(field)) {
    return html`<input
      type="checkbox"
      .checked=${Boolean(value)}
      @change=${(e) => set(e.target.checked)}
    />`;
  }
  if (isSelect(field)) {
    return html`<select
      class="form-select"
      @change=${(e) => set(e.target.value)}
    >
      ${field.required ? "" : html`<option value=""></option>`}
      ${options(field).map(
        (o) =>
          html`<option value=${o.value} ?selected=${o.value === value}>
            ${o.label}
          </option>`,
      )}
    </select>`;
  }
  const numeric =
    field.type === "integer" || field.type === "float" || selector.number;
  const secret =
    selector.text?.type === "password" ||
    (!selector.text?.type && SECRET.test(field.name));
  return html`<input
    class="form-select"
    type=${numeric ? "number" : secret ? "password" : selector.text?.type || "text"}
    autocomplete=${secret ? "new-password" : "off"}
    .value=${value === undefined || value === null ? "" : String(value)}
    @input=${(e) =>
      set(
        numeric && e.target.value !== ""
          ? Number(e.target.value)
          : e.target.value,
      )}
  />`;
}

function fields(host, state, action, schema, path, base) {
  const stepId = state.step.step_id;
  return (schema || []).map((field) => {
    if (field.type === "constant") return "";
    const at = [...path, field.name];
    if (field.type === "expandable") {
      const label =
        text(state, action, `step.${stepId}.sections.${field.name}.name`) ||
        field.name;
      return html`<fieldset class="setup-section">
        <legend>${label}</legend>
        ${fields(
          host,
          state,
          action,
          field.schema,
          at,
          `step.${stepId}.sections.${field.name}.`,
        )}
      </fieldset>`;
    }
    const key = base || `step.${stepId}.`;
    const label = text(state, action, `${key}data.${field.name}`) || field.name;
    const hint = text(state, action, `${key}data_description.${field.name}`);
    const error = state.step.errors?.[field.name];
    const value = at.reduce((v, k) => v?.[k], state.values);
    return html`<label class="setup-field">
      <span>${label}${field.required ? " *" : ""}</span>
      ${control(host, state, field, at, value)}
      ${hint ? html`<small>${hint}</small>` : ""}
      ${
        error
          ? html`<small class="setup-error">
              ${text(state, action, `error.${error}`) || error}
            </small>`
          : ""
      }
    </label>`;
  });
}

function menu(host, state, action) {
  const stepId = state.step.step_id;
  const choices = state.step.menu_options || [];
  const list = Array.isArray(choices)
    ? choices.map((c) => [c, c])
    : Object.entries(choices);
  return list.map(
    ([value, label]) =>
      html`<label class="setup-choice">
        <input
          type="radio"
          name=${`setup-${action.flow_id}`}
          .checked=${state.values.choice === value}
          @change=${() => setValue(host, state, ["choice"], value)}
        />
        ${text(state, action, `step.${stepId}.menu_options.${value}`) || label}
      </label>`,
  );
}

// `quoted` words (a protocol, a setting) as code, as Home Assistant's
// markdown shows them.
function inline(words) {
  return String(words)
    .split("`")
    .map((part, i) => (i % 2 ? html`<code>${part}</code>` : part));
}

function paragraphs(words) {
  return String(words || "")
    .split(/\n{2,}/)
    .filter(Boolean)
    .map((p) => html`<p>${inline(p)}</p>`);
}

/** The card's row: the step Home Assistant is waiting on, in its own words. */
export function renderIntegrationSetupRow(host, action) {
  const state = load(host, action);
  const t = (key, fallback) => host._t(key, fallback);
  if (state.status === "loading") {
    return html`<div class="setup-step">
      ${t("setup_loading", "Loading…")}
    </div>`;
  }
  if (state.status === "gone") {
    return html`<div class="setup-step">
      ${state.error ? html`<p class="setup-error">${state.error}</p>` : ""}
      ${
        state.stuck
          ? checkAgain(host, action, state)
          : t(
              "setup_gone",
              "This setup is no longer open. Ask me again to start over.",
            )
      }
    </div>`;
  }
  const step = state.step;
  const placeholders = step.description_placeholders;
  const title =
    text(state, action, `step.${step.step_id}.title`, placeholders) ||
    action.label;
  const description = text(
    state,
    action,
    `step.${step.step_id}.description`,
    placeholders,
  );
  const base = step.errors?.base;
  const steps = Array.isArray(action.steps) ? action.steps : [];
  return html`<div class="setup-step">
    <div class="setup-title">${title}</div>
    ${paragraphs(description)}
    ${
      steps.length
        ? html`<ol class="setup-steps">
            ${steps.map((s) => html`<li>${s}</li>`)}
          </ol>`
        : ""
    }
    ${
      state.status === "form"
        ? fields(host, state, action, step.data_schema, [], "")
        : ""
    }
    ${state.status === "menu" ? menu(host, state, action) : ""}
    ${
      state.status === "external"
        ? html`<p>
            <a href=${step.url} target="_blank" rel="noopener"
              >${t("setup_open_sign_in", "Open the sign-in page")}</a
            >
            — ${t("setup_after_sign_in", "then come back and press Continue.")}
          </p>`
        : ""
    }
    ${
      state.status === "progress"
        ? html`<p>
            ${t("setup_in_progress", "Home Assistant is still working on it; press Continue in a moment.")}
          </p>`
        : ""
    }
    ${
      base || state.error
        ? html`<p class="setup-error">
            ${base ? text(state, action, `error.${base}`) || base : state.error}
          </p>`
        : ""
    }
    ${state.stuck && asksToAct(state) ? checkAgain(host, action, state) : ""}
  </div>`;
}
