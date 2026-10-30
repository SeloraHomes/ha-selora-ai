// The heading a chat card opens with: an icon in a tinted square, a title,
// and an optional line under it. One look for every card that names what it
// shows, so the home suggestions and the cards in a reply read alike.
import { html } from "lit";

/** The tinted icon square on its own, for a row that lays out its own title. */
export function renderCardIcon(icon) {
  return html`<span class="card-heading-icon"
    ><ha-icon icon=${icon}></ha-icon
  ></span>`;
}

// `banner` puts a picture behind the heading, on the right, fading into the
// card under the words on the left.
export function renderCardHeading({ icon, title, subtitle = "", banner = "" }) {
  return html`
    <div
      class="card-heading${banner ? " card-heading--banner" : ""}"
      style=${banner ? `--card-heading-banner:url("${banner}")` : ""}
    >
      ${renderCardIcon(icon)}
      <div class="card-heading-text">
        ${title ? html`<div class="card-heading-title">${title}</div>` : ""}
        ${subtitle ? html`<div class="card-heading-subtitle">${subtitle}</div>` : ""}
      </div>
    </div>
  `;
}
