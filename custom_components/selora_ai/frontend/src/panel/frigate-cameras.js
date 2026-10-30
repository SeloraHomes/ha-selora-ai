// The "add cameras to Frigate" card: one row per camera, and a login form for
// each camera found on the network.
//
// The logins live in this module only — never on the message, the session or
// anything the model sees — and go straight to `selora_ai/frigate/add_cameras`
// with the card's plan id. The backend holds the plan; nothing else from the
// descriptor is sent. A login a camera refuses comes back per camera, and the
// card stays open to fix it.
import { html } from "lit";
import { interpolate } from "../shared/i18n.js";

const DEFAULT_USERNAME = "admin";

// plan_id → camera key → {username, password}
const LOGINS = new Map();
// plan_id → camera key → error text
const ERRORS = new Map();

function loginFor(planId, key) {
  if (!LOGINS.has(planId)) LOGINS.set(planId, {});
  const logins = LOGINS.get(planId);
  if (!logins[key]) logins[key] = { username: DEFAULT_USERNAME, password: "" };
  return logins[key];
}

/** A login a camera refused; the error carries them per camera. */
export class CameraLoginError extends Error {
  constructor(message, cameraErrors) {
    super(message);
    this.cameraErrors = cameraErrors;
  }
}

/** Client-action handler: add the card's cameras with the logins typed in. */
export async function addFrigateCameras(hass, action) {
  const planId = String(action.plan_id || "");
  const credentials = {};
  for (const camera of action.cameras || []) {
    if (camera.needs_login) {
      const login = loginFor(planId, String(camera.key));
      credentials[String(camera.key)] = {
        username: String(login.username || ""),
        password: String(login.password || ""),
      };
    }
  }
  const result = await hass.callWS({
    type: "selora_ai/frigate/add_cameras",
    plan_id: planId,
    credentials,
  });
  if (result?.camera_errors) {
    ERRORS.set(planId, result.camera_errors);
    throw new CameraLoginError(result.error || "", result.camera_errors);
  }
  if (result?.error) throw new Error(result.error);
  LOGINS.delete(planId);
  ERRORS.delete(planId);
  return { cameras: result?.cameras || [] };
}

// The line under the camera's name: what tells it apart, without repeating
// the name — a camera named in chat by its address is named by it.
function cameraLine(host, camera) {
  if (camera.source !== "network") return camera.area || "";
  const parts = [];
  if (camera.stream) parts.push(camera.stream);
  else if (camera.address !== camera.name) parts.push(camera.address);
  if (camera.brand) parts.push(camera.brand);
  return parts.join(" · ");
}

// A camera with only its substream enabled records in low resolution; the
// user decides before approving, so the card says so and what to turn on.
function lowResLine(host, camera) {
  if (!camera.low_resolution) return "";
  const text = camera.turn_on
    ? interpolate(
        host._t(
          "frigate_cameras_low_res_turn_on",
          "Only its low-resolution stream is on, so it records in low resolution. Turn on its “{stream}” camera in Home Assistant first to record in full quality.",
        ),
        { stream: camera.turn_on },
      )
    : host._t(
        "frigate_cameras_low_res",
        "Only its low-resolution stream is available, so it records in low resolution.",
      );
  return html`<div
    style="font-size:12px;color:var(--warning-color);line-height:1.4;padding-top:4px;"
  >
    ${text}
  </div>`;
}

function loginFields(host, planId, camera) {
  const login = loginFor(planId, String(camera.key));
  const field = (key, type, labelKey, fallback) => html`
    <label>
      ${host._t(labelKey, fallback)}
      <input
        class="form-select"
        type=${type}
        autocomplete=${type === "password" ? "new-password" : "off"}
        .value=${login[key] || ""}
        @input=${(e) => {
          login[key] = e.target.value;
        }}
      />
    </label>
  `;
  return html`
    <div class="frigate-login">
      ${field("username", "text", "frigate_camera_username", "Username")}
      ${field("password", "password", "frigate_camera_password", "Password")}
    </div>
  `;
}

function settingsLine(host, action) {
  const objects = (action.objects || []).join(", ");
  const recording = !action.record
    ? host._t("frigate_cameras_no_recording", "no recording")
    : action.record_days
      ? interpolate(
          host._t(
            "frigate_cameras_recording_days",
            "recordings kept {days} days",
          ),
          { days: action.record_days },
        )
      : host._t("frigate_cameras_recording", "recordings kept");
  return interpolate(
    host._t("frigate_cameras_settings", "Detects {objects}; {recording}."),
    { objects, recording },
  );
}

/** The card's rows: each camera, with a login form where one is needed. */
export function renderFrigateCameraRows(host, action) {
  const planId = String(action.plan_id || "");
  const errors = ERRORS.get(planId) || {};
  return html`
    <div style="display:flex;flex-direction:column;gap:6px;padding:4px 0;">
      ${(action.cameras || []).map(
        (camera) => html`
          <div style="padding:4px 0;">
            <div style="display:flex;align-items:center;gap:10px;">
              <ha-icon
                icon="mdi:cctv"
                style="--mdc-icon-size:22px;color:var(--secondary-text-color);flex-shrink:0;"
              ></ha-icon>
              <div style="min-width:0;">
                <div
                  style="font-size:13px;font-weight:600;color:var(--primary-text-color);"
                >
                  ${camera.name}
                </div>
                <div style="font-size:12px;color:var(--secondary-text-color);">
                  ${cameraLine(host, camera)}
                </div>
              </div>
            </div>
            ${lowResLine(host, camera)}
            ${camera.needs_login ? loginFields(host, planId, camera) : ""}
            ${
              errors[camera.key]
                ? html`<div class="frigate-camera-error">
                    ${errors[camera.key]}
                  </div>`
                : ""
            }
          </div>
        `,
      )}
      <div
        style="font-size:12px;color:var(--secondary-text-color);line-height:1.4;"
      >
        ${settingsLine(host, action)}
      </div>
      ${
        (action.skipped || []).length
          ? html`<div
              style="font-size:12px;color:var(--secondary-text-color);line-height:1.4;"
            >
              ${interpolate(
                host._t("frigate_cameras_not_added", "Not added: {list}."),
                {
                  list: action.skipped
                    .map((s) => `${s.name} (${s.reason})`)
                    .join("; "),
                },
              )}
            </div>`
          : ""
      }
      <div
        style="font-size:12px;color:var(--secondary-text-color);line-height:1.4;"
      >
        ${host._t(
          "frigate_cameras_restart",
          "Frigate restarts to add them, pausing its cameras for about a minute.",
        )}
      </div>
    </div>
  `;
}
