import { html } from "lit";

import { interpolate } from "../shared/i18n.js";
import { textInput } from "./settings-inputs.js";

// Settings → Frigate. On a Selora hub the Frigate integration provides the
// connection and the card only says so; elsewhere the user enters Frigate's
// address and login here. The saved password never comes back from the
// server (`frigate_password_set`), so a blank field keeps it.

function setStatus(host, type, message) {
  host._frigateStatus = { type, message };
  host.requestUpdate();
}

export async function testFrigateConnection(host) {
  if (host._frigateBusy) return;
  host._frigateBusy = "testing";
  host._frigateStatus = null;
  host.requestUpdate();
  try {
    const result = await host.hass.callWS({
      type: "selora_ai/frigate/test_connection",
      url: host._config.frigate_url || "",
      username: host._config.frigate_username || "",
      password: host._frigatePassword || "",
    });
    if (result.ok) {
      setStatus(
        host,
        "success",
        interpolate(
          host._t(
            "settings_frigate_test_ok",
            "Connected to Frigate {version} · cameras: {count}",
          ),
          { version: result.version, count: result.cameras },
        ),
      );
    } else {
      setStatus(host, "error", result.error);
    }
  } catch (err) {
    setStatus(host, "error", err.message);
  } finally {
    host._frigateBusy = null;
    host.requestUpdate();
  }
}

export async function saveFrigateSettings(host, { disconnect = false } = {}) {
  if (host._frigateBusy) return;
  host._frigateBusy = "saving";
  host._frigateStatus = null;
  host.requestUpdate();
  const config = disconnect
    ? { frigate_url: "" }
    : {
        frigate_url: host._config.frigate_url || "",
        frigate_username: host._config.frigate_username || "",
        frigate_password: host._frigatePassword || "",
      };
  try {
    await host.hass.callWS({ type: "selora_ai/update_config", config });
    host._frigatePassword = "";
    await host._loadConfig();
    setStatus(
      host,
      "success",
      disconnect
        ? host._t("settings_frigate_disconnected", "Frigate disconnected.")
        : host._t("settings_frigate_saved", "Frigate settings saved."),
    );
  } catch (err) {
    setStatus(host, "error", err.message);
  } finally {
    host._frigateBusy = null;
    host.requestUpdate();
  }
}

function renderStatus(host) {
  const status = host._frigateStatus;
  if (!status) return "";
  return html`<div class="save-feedback save-feedback--${status.type}">
    <ha-icon
      icon=${status.type === "success" ? "mdi:check-circle" : "mdi:alert-circle"}
      style="--mdc-icon-size:14px;"
    ></ha-icon>
    ${status.message}
  </div>`;
}

function renderFields(host) {
  const config = host._config;
  const busy = Boolean(host._frigateBusy);
  return html`
    <div class="form-group">
      ${textInput({
        label: host._t("settings_frigate_url_label", "Frigate address"),
        value: config.frigate_url,
        placeholder: "http://192.168.1.10:5000",
        oninput: (e) => host._updateConfig("frigate_url", e.target.value),
      })}
    </div>
    <div class="form-group">
      ${textInput({
        label: host._t(
          "settings_frigate_username_label",
          "Username (if Frigate asks for a login)",
        ),
        value: config.frigate_username,
        oninput: (e) => host._updateConfig("frigate_username", e.target.value),
      })}
    </div>
    <div class="form-group">
      ${textInput({
        label: host._t("settings_frigate_password_label", "Password"),
        type: "password",
        value: host._frigatePassword,
        placeholder: config.frigate_password_set
          ? host._t(
              "settings_frigate_password_kept",
              "Saved — leave blank to keep it",
            )
          : "",
        oninput: (e) => (host._frigatePassword = e.target.value),
      })}
    </div>
    <p class="section-card-subtitle">
      ${host._t(
        "settings_frigate_no_integration_note",
        "Without the Frigate integration, Frigate's cameras have no Home Assistant entities: install it from HACS for their switches and live views.",
      )}
    </p>
    <div class="card-save-bar">
      ${
        config.frigate_url
          ? html`<button
              class="btn btn-outline"
              ?disabled=${busy}
              @click=${() => saveFrigateSettings(host, { disconnect: true })}
            >
              ${host._t("settings_frigate_disconnect_button", "Disconnect")}
            </button>`
          : ""
      }
      <button
        class="btn btn-outline"
        ?disabled=${busy || !config.frigate_url}
        @click=${() => testFrigateConnection(host)}
      >
        ${
          host._frigateBusy === "testing"
            ? host._t("settings_frigate_testing_label", "Testing…")
            : host._t("settings_frigate_test_button", "Test connection")
        }
      </button>
      <button
        class="btn btn-primary"
        ?disabled=${busy}
        @click=${() => saveFrigateSettings(host)}
      >
        ${
          host._frigateBusy === "saving"
            ? host._t("settings_saving_label", "Saving…")
            : host._t("settings_save_button", "Save")
        }
      </button>
    </div>
  `;
}

// Who connects Frigate, in the user's words: the hub, or the Frigate
// integration they set up. The address stays out: it cannot be changed here.
function renderConnected(host, source) {
  const hub = source === "hub";
  return html`
    <div class="frigate-connected">
      <ha-icon
        icon=${hub ? "mdi:check-decagram" : "mdi:check-circle"}
        class="frigate-connected-icon"
      ></ha-icon>
      <div class="frigate-connected-text">
        <div class="frigate-connected-title">
          ${
            hub
              ? host._t(
                  "settings_frigate_hub_title",
                  "Set up by your Selora hub",
                )
              : host._t(
                  "settings_frigate_integration_title",
                  "Connected through the Frigate integration",
                )
          }
        </div>
        <div class="frigate-connected-desc">
          ${
            hub
              ? host._t(
                  "settings_frigate_hub_desc",
                  "Frigate runs on your hub and Selora keeps it connected. There is nothing to configure.",
                )
              : host._t(
                  "settings_frigate_integration_desc",
                  "Selora AI uses the connection set up in Home Assistant. Change it in the Frigate integration's settings.",
                )
          }
        </div>
      </div>
    </div>
  `;
}

export function renderFrigateSettings(host) {
  const config = host._config || {};
  return html`
    <div class="section-card settings-section">
      <div class="section-card-header">
        <h3>${host._t("settings_frigate_heading", "Frigate")}</h3>
      </div>
      <p class="section-card-subtitle">
        ${host._t(
          "settings_frigate_desc",
          "Records your cameras and spots people, cars and animals. Selora AI can add cameras, choose what each one watches for, and tell you what they saw.",
        )}
      </p>
      ${
        config.frigate_source
          ? renderConnected(host, config.frigate_source)
          : renderFields(host)
      }
      ${renderStatus(host)}
    </div>
  `;
}
