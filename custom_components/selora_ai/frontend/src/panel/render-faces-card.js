// Face recognition's guide (`msg.faces_card`, written by the backend in
// frigate/faces.py), in place of the message text: what it gives and needs,
// then the question, answered by the buttons under it; or, once it is on, the
// card that adds people to Frigate's Face Library. The guide's words come from
// the backend, in the reply's language; the people card's controls are the
// panel's own.
//
// The people card reads the library live (faces-actions.js), so a reopened
// conversation shows who Frigate knows now, and waits for Frigate to run face
// recognition before it takes a photo.
import { html } from "lit";

import { CARD_BANNERS } from "../shared/card-banners.js";
import { renderCardHeading } from "../shared/card-heading.js";
import { renderActionButton } from "../shared/action-button.js";
import { interpolate } from "../shared/i18n.js";
import {
  faceLibraryShown,
  renderFaceLibraryButton,
} from "./face-library-link.js";
import {
  addPhotos,
  loadPeople,
  peopleState,
  stoppedWaiting,
  waitAgain,
} from "./faces-actions.js";

const ACCEPT = "image/jpeg,image/png,image/webp";

function renderIntro(card) {
  const intro = card.intro;
  return html`
    <section class="describe-section">
      ${renderCardHeading({
        icon: "mdi:face-recognition",
        title: intro.title,
        subtitle: intro.tagline,
        banner: CARD_BANNERS.frigate,
      })}
      <ul class="describe-points">
        ${(intro.points || []).map(
          (point) =>
            html`<li>
              <ha-icon icon=${point.icon}></ha-icon><span>${point.text}</span>
            </li>`,
        )}
      </ul>
      <div class="describe-question">${card.question}</div>
    </section>
  `;
}

// A tick once it is on, a warning when Frigate could not restart, and no
// colour for a no: nothing was done.
const STATUS_ICONS = {
  restarting: ["faces-status-icon", "mdi:check-circle-outline"],
  not_restarted: ["describe-done-warn", "mdi:alert-outline"],
  declined: ["faces-status-quiet", "mdi:close-circle-outline"],
};

function renderStatus(card) {
  const [cls, icon] = STATUS_ICONS[card.done] || STATUS_ICONS.declined;
  return html`
    <div class="describe-done">
      <ha-icon class=${cls} icon=${icon}></ha-icon>
      <span>${card.status}</span>
    </div>
  `;
}

function renderWaiting(host, state) {
  if (stoppedWaiting(state)) {
    return html`
      <div class="faces-wait">
        <ha-icon icon="mdi:timer-sand-complete"></ha-icon>
        <span
          >${host._t(
            "faces_not_back",
            "Frigate isn't running face recognition yet. It starts after Frigate's next restart.",
          )}</span
        >
        ${renderActionButton({
          label: host._t("faces_check_again", "Check again"),
          mark: "mdi:refresh",
          onClick: () => waitAgain(host),
        })}
      </div>
    `;
  }
  return html`
    <div class="faces-wait">
      <span class="spinner" style="width:14px;height:14px;"></span>
      <span
        >${
          state.loaded
            ? host._t(
                "faces_waiting",
                "Waiting for Frigate to restart with face recognition on…",
              )
            : host._t("faces_checking", "Checking Frigate…")
        }</span
      >
    </div>
  `;
}

function renderPeople(host, state) {
  if (!state.people.length) {
    return html`<div class="faces-nobody">
      ${host._t("faces_nobody", "Frigate doesn't know anyone yet.")}
    </div>`;
  }
  return html`
    <div class="faces-people">
      ${state.people.map(
        (person) => html`
          <button
            class="faces-person${state.name === person.name ? " is-picked" : ""}"
            title=${host._t("faces_add_more", "Add photos of this person")}
            @click=${() => {
              state.name = person.name;
              state.result = null;
              host.requestUpdate?.();
            }}
          >
            <ha-icon icon="mdi:account-circle-outline"></ha-icon>
            <span class="faces-person-name">${person.name}</span>
            <span class="faces-person-count"
              >${host._tn("faces_photo_count", person.photos, "{count} photos")}</span
            >
          </button>
        `,
      )}
    </div>
  `;
}

function renderResult(host, state) {
  const result = state.result;
  if (!result) return "";
  return html`
    <div class="faces-result">
      ${
        result.added
          ? html`<div class="describe-done">
              <ha-icon
                class="faces-status-icon"
                icon="mdi:check-circle-outline"
              ></ha-icon>
              <span
                >${interpolate(
                  host._tn(
                    "faces_added",
                    result.added,
                    "{count} photos added for {name}.",
                  ),
                  { name: result.name },
                )}</span
              >
            </div>`
          : ""
      }
      ${result.failed.map(
        (f) =>
          html`<div class="frigate-camera-error faces-failed">
            ${f.file}: ${f.error}
          </div>`,
      )}
    </div>
  `;
}

function renderForm(host, state) {
  const pick = (e) => {
    const input = e.currentTarget
      .closest(".faces-form")
      ?.querySelector("input[type=file]");
    input?.click();
  };
  return html`
    <div class="faces-form">
      <label class="faces-name">
        ${host._t("faces_name_label", "Name")}
        <input
          class="form-select"
          type="text"
          maxlength="40"
          autocomplete="off"
          placeholder=${host._t("faces_name_placeholder", "Who is it?")}
          .value=${state.name}
          ?disabled=${state.busy}
          @input=${(e) => {
            state.name = e.target.value;
            state.result = null;
            host.requestUpdate?.();
          }}
        />
      </label>
      <input
        type="file"
        accept=${ACCEPT}
        multiple
        hidden
        @change=${(e) => {
          const files = [...e.target.files];
          e.target.value = "";
          addPhotos(host, files);
        }}
      />
      ${
        state.progress
          ? html`<div class="faces-wait">
              <span class="spinner" style="width:14px;height:14px;"></span>
              <span
                >${interpolate(
                  host._t("faces_adding", "Adding photo {done} of {total}…"),
                  {
                    done: Math.min(
                      state.progress.done + 1,
                      state.progress.total,
                    ),
                    total: state.progress.total,
                  },
                )}</span
              >
            </div>`
          : renderActionButton({
              label: host._t("faces_add_photos", "Add photos"),
              mark: "mdi:image-plus",
              tone: "primary",
              disabled: !state.name.trim() || state.busy,
              onClick: pick,
            })
      }
    </div>
  `;
}

function renderPeopleSection(host, card, opening) {
  const state = peopleState(host);
  if (!state.loaded && !state.loading) loadPeople(host);
  return html`
    <section class="describe-section">
      ${renderCardHeading({
        icon: "mdi:account-multiple-plus-outline",
        title: card.title,
        banner: opening ? CARD_BANNERS.frigate : "",
      })}
      ${card.hint ? html`<div class="faces-hint">${card.hint}</div>` : ""}
      ${
        state.running
          ? html`${renderPeople(host, state)} ${renderForm(host, state)}
            ${renderResult(host, state)}`
          : renderWaiting(host, state)
      }
    </section>
  `;
}

/** The card for a face-recognition guide message, or "" for any other. */
export function renderFacesCard(host, msg) {
  const card = msg.faces_card;
  if (!card) return "";
  if (card.question && card.intro) {
    return html`<div class="describe-card">${renderIntro(card)}</div>`;
  }
  if (!card.people) return card.status ? renderStatus(card) : "";
  return html`
    <div class="describe-card faces-card">
      ${card.status ? renderStatus(card) : ""}
      ${renderPeopleSection(host, card, !card.done)}
      ${renderFaceLibraryButton(host)}
    </div>
  `;
}

/** Under a model reply that read face recognition as on: the people card,
 * then the way into Frigate's Face Library. */
export function renderFaceRecognitionReply(host, msg) {
  if (!faceLibraryShown(msg)) return "";
  const title = host._t("faces_people_title", "People Frigate recognizes");
  return html`
    <div class="describe-card faces-card faces-card--reply">
      ${renderPeopleSection(host, { title, people: true }, false)}
    </div>
    ${renderFaceLibraryButton(host)}
  `;
}
