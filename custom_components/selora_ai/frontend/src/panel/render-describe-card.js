// The question about a camera's descriptions (`msg.describe_card`, written by
// the backend in frigate/descriptions.py), in place of the message text: the
// camera's snapshot, so the homeowner sees which camera, and whether it looks
// inside the home, before answering. The first question opens with what a
// description adds, on an example, and what it costs. Every word comes from
// the backend, in the reply's language.
//
// Each section is a heading, then the picture beside its words: a chat column
// is wide, and stacked it made a card taller than the screen.
import { html } from "lit";

import { CARD_BANNERS } from "../shared/card-banners.js";
import { renderCardHeading } from "../shared/card-heading.js";
import { renderCreatedCheck } from "../shared/created-check.js";
import {
  frigateEntity,
  openMoreInfo,
  snapshotUrl,
} from "./render-frigate-result.js";

function renderIntro(intro) {
  return html`
    <section class="describe-section">
      ${renderCardHeading({
        icon: "mdi:text-box-search-outline",
        title: intro.title,
        subtitle: intro.tagline,
        banner: CARD_BANNERS.frigate,
      })}
      <div class="describe-columns">
        <div class="frigate-result-preview describe-example-picture">
          <img src=${intro.image} alt=${intro.example} loading="lazy" />
        </div>
        <ul class="describe-points">
          <li>
            <ha-icon icon="mdi:cctv"></ha-icon>
            <span class="describe-point-pair">
              <span class="describe-example-by">${intro.seen}</span>
              <span class="describe-example-described">${intro.label}</span>
            </span>
          </li>
          <li>
            <ha-icon icon="mdi:creation"></ha-icon>
            <span class="describe-point-pair">
              <span class="describe-example-by">${intro.described}</span>
              <span class="describe-example-described">${intro.example}</span>
            </span>
          </li>
          ${(intro.points || []).map(
            (point) =>
              html`<li>
                <ha-icon icon=${point.icon}></ha-icon><span>${point.text}</span>
              </li>`,
          )}
        </ul>
      </div>
    </section>
  `;
}

function renderCamera(host, card) {
  const cameraId = frigateEntity(host, `camera.${card.camera}`);
  const snapshot = cameraId ? snapshotUrl(host, cameraId) : "";
  return html`
    <section class="describe-section">
      ${renderCardHeading({
        icon: "mdi:cctv",
        title: card.camera_name,
        subtitle: card.step || "",
      })}
      <div class="describe-columns">
        ${
          snapshot
            ? html`<button
                class="frigate-result-preview"
                title=${host._t("frigate_result_open", "Open the camera")}
                @click=${() => openMoreInfo(host, cameraId)}
              >
                <img src=${snapshot} alt=${card.camera_name} loading="lazy" />
              </button>`
            : ""
        }
        <div class="describe-side">
          <div class="describe-question">${card.question}</div>
        </div>
      </div>
    </section>
  `;
}

// A reply this fresh draws its tick; reopening the session later does not.
const FRESH_MS = 60 * 1000;

// How the flow ended: a tick once the answers are saved, a warning when
// Frigate could not restart to apply them.
function renderDone(msg, card) {
  const at = Date.parse(msg.timestamp || "");
  const fresh = !Number.isNaN(at) && Date.now() - at < FRESH_MS;
  const applied = card.done !== "not_restarted";
  return html`
    <div class="describe-done${fresh ? " pop-in" : ""}">
      <span class="scene-saved-icon${applied ? "" : " describe-done-warn"}"
        >${
          applied
            ? renderCreatedCheck({ animate: fresh, size: 20 })
            : html`<ha-icon icon="mdi:alert-outline"></ha-icon>`
        }</span
      >
      <span>${msg.content}</span>
    </div>
  `;
}

/** The card for a camera-descriptions message, or "" for any other message. */
export function renderDescribeCard(host, msg) {
  const card = msg.describe_card;
  if (card?.done) return renderDone(msg, card);
  if (!card?.question) return "";
  return html`
    <div class="describe-card">
      ${card.intro ? renderIntro(card.intro) : ""} ${renderCamera(host, card)}
    </div>
  `;
}
