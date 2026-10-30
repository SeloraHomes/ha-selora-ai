import { html } from "lit";

// Native <input> wrapper used in place of <ha-textfield>. Recent HA builds
// wrap each panel in a scoped custom-element registry, which prevents
// globally-registered HA components like <ha-textfield> from upgrading
// inside our shadow root — they render as empty unknown elements. Plain
// <input> works regardless of the registry, and the .form-select class
// already styles it to match the rest of the settings form.
export function textInput({
  label,
  value,
  oninput,
  type = "text",
  placeholder = "",
  style = "",
}) {
  return html`
    ${
      label
        ? html`<label
            style="font-size:13px;color:var(--secondary-text-color);display:block;margin-bottom:6px;"
            >${label}</label
          >`
        : ""
    }
    <input
      class="form-select"
      type=${type}
      .value=${value || ""}
      @input=${oninput}
      placeholder=${placeholder}
      style="width:100%;box-sizing:border-box;${style}"
    />
  `;
}
