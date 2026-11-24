// Temporary browser-code access: the header indicator and the approval /
// review dialog.
// Calls live in file-access-actions.js.
//
// The agent label, its reason, file names and finding excerpts are text the
// agent (or the code it wrote) chose. They are only ever interpolated as Lit
// text values — escaped — and the reason is marked as the agent's own words,
// so it cannot pass for Selora's explanation.
import { html } from "lit";

import { renderCardHeading } from "../shared/card-heading.js";
import { formatTime } from "../shared/date-utils.js";
import { interpolate } from "../shared/i18n.js";
import {
  FILE_ACCESS_MINUTES,
  defaultMinutes,
  formatRemaining,
  remainingFraction,
  resolveFileAccessTarget,
} from "./file-access-actions.js";

const SEVERITY_ORDER = { high: 0, medium: 1, info: 2 };

const KIND_LABELS = {
  network: ["file_access_finding_network", "Talks to the network"],
  external_url: [
    "file_access_finding_external_url",
    "Contacts an outside address",
  ],
  credentials: ["file_access_finding_credentials", "Reads your login"],
  dynamic_code: ["file_access_finding_dynamic_code", "Runs text as code"],
  admin_api: ["file_access_finding_admin_api", "Uses administrator commands"],
  service: ["file_access_finding_service", "Controls devices"],
  obfuscation: ["file_access_finding_obfuscation", "Hidden or encoded code"],
};

const ACTION_LABELS = {
  created: ["file_access_action_created", "Created"],
  replaced: ["file_access_action_replaced", "Replaced"],
  deleted: ["file_access_action_deleted", "Deleted"],
};

function t(host, [key, fallback], values) {
  const phrase = host._t(key, fallback);
  return values ? interpolate(phrase, values) : phrase;
}

function formatSize(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** Writes grouped by file, in the order each file was first written. */
export function groupWritesByFile(writes) {
  const groups = new Map();
  for (const w of writes || []) {
    const file = String(w?.file ?? "");
    if (!groups.has(file)) groups.set(file, []);
    groups.get(file).push(w);
  }
  return [...groups.entries()].map(([file, list]) => ({ file, writes: list }));
}

function sortedFindings(findings) {
  return [...(findings || [])].sort(
    (a, b) =>
      (SEVERITY_ORDER[a?.severity] ?? 3) - (SEVERITY_ORDER[b?.severity] ?? 3),
  );
}

const AGENT_KINDS = {
  selora_account: [
    "An AI assistant connected with your Selora account",
    "An AI assistant signed in as {name}",
  ],
  mcp_token: [
    "An AI assistant using a Selora key",
    "An AI assistant using the key “{name}”",
  ],
  ha_token: [
    "An AI assistant using a Home Assistant token",
    "An AI assistant using {name}'s Home Assistant access",
  ],
};

// Who asks, in the viewer's language: the credential's kind and a name a
// person recognises (email, key name, HA user), never an internal id. A record
// stored before kinds existed keeps the backend's English label.
export function agentLabel(host, item) {
  // The app ("ChatGPT") is what tells one assistant from another; with one
  // owner per home, the account says nothing. The credential it came in on is
  // shown beside it (connectedWith), since the app names itself.
  if (item?.agent_client) return item.agent_client;
  const phrases = AGENT_KINDS[item?.agent_kind];
  if (!phrases) return item?.agent || "";
  if (item.agent_name) {
    return t(host, [`file_access_agent_${item.agent_kind}_named`, phrases[1]], {
      name: item.agent_name,
    });
  }
  return host._t(`file_access_agent_${item.agent_kind}`, phrases[0]);
}

const VIA = {
  selora_account: ["file_access_via_selora_account", "Your Selora account"],
  mcp_token: ["file_access_via_mcp_token", "A Selora key"],
  ha_token: ["file_access_via_ha_token", "A Home Assistant token"],
};

// What the grant is actually bound to — the one part the client cannot claim.
export function connectedWith(host, item) {
  const via = VIA[item?.agent_kind];
  if (!via) return "";
  if (item.agent_kind === "mcp_token" && item.agent_name) {
    return t(host, ["file_access_via_mcp_token_named", "The key “{name}”"], {
      name: item.agent_name,
    });
  }
  if (item.agent_kind === "ha_token" && item.agent_name) {
    return t(
      host,
      ["file_access_via_ha_token_named", "{name}'s Home Assistant access"],
      { name: item.agent_name },
    );
  }
  return host._t(via[0], via[1]);
}

// ── Header indicator ────────────────────────────────────────────────────────

// What the header shows, most urgent first: a request waiting for an answer,
// then a live grant, then an ended one whose summary is still to be read.
export function fileAccessIndicatorState(host) {
  const state = host._fileAccess;
  if (!state) return null;
  const grants = (state.grants || []).filter(Boolean);
  const [request] = state.requests || [];
  if (request) {
    return {
      kind: "request",
      item: request,
      icon: "mdi:shield-alert-outline",
      label: "",
      title: t(
        host,
        [
          "file_access_banner_pending",
          "{agent} is asking to write browser code to {folder}",
        ],
        { agent: agentLabel(host, request), folder: request.folder },
      ),
    };
  }
  const active = grants.find((g) => !g.ended_at);
  if (active) {
    return {
      kind: "grant",
      item: active,
      icon: "mdi:shield-key-outline",
      label: formatRemaining(active.expires_at),
      title: t(
        host,
        [
          "file_access_banner_left",
          "{agent} can write browser code to {folder}: {time} left",
        ],
        {
          agent: agentLabel(host, active),
          folder: active.folder,
          time: formatRemaining(active.expires_at),
        },
      ),
    };
  }
  const ended = grants.find((g) => g.ended_at);
  if (ended) {
    return {
      kind: "grant",
      item: ended,
      icon: "mdi:shield-check-outline",
      label: "",
      title: t(
        host,
        [
          "file_access_banner_ended",
          "Access for {agent} ended — review what it wrote",
        ],
        { agent: agentLabel(host, ended) },
      ),
    };
  }
  return null;
}

// A small header button rather than a banner: access usually runs in the
// background, and a card above every tab pushed the page down for an hour.
// The full sentence is its tooltip and accessible name; the dialog it opens
// holds the details and Revoke.
export function renderFileAccessIndicator(host) {
  const shown = fileAccessIndicatorState(host);
  if (!shown) return "";
  return html`
    <button
      class="file-access-indicator file-access-indicator--${
        shown.item.ended_at ? "ended" : shown.kind
      }"
      title=${shown.title}
      aria-label=${shown.title}
      @click=${() => host._openFileAccess(shown.item.id, shown.kind)}
    >
      <ha-icon icon=${shown.icon}></ha-icon>
      ${
        shown.label
          ? html`<span class="file-access-indicator-label"
              >${shown.label}</span
            >`
          : ""
      }
      ${
        shown.kind === "request"
          ? html`<span class="file-access-indicator-dot"></span>`
          : ""
      }
    </button>
  `;
}

// ── Dialog ──────────────────────────────────────────────────────────────────

// What allowing risks, said once and plainly, then what limits it — never
// mixed: the risk listed among the safeguards read as one of them.
const SAFEGUARDS = [
  ["mdi:folder-lock-outline", "file_access_point_folder", "Only in {folder}"],
  [
    "mdi:timer-sand",
    "file_access_point_expires",
    "Ends on its own after the time you pick",
  ],
  [
    "mdi:shield-search",
    "file_access_point_checked",
    "Every file is checked and shown to you",
  ],
];

function renderRequest(host, req) {
  const minutes = host._fileAccessMinutes ?? defaultMinutes(req.minutes);
  const who =
    req.agent_client || host._t("file_access_an_assistant", "An AI assistant");
  // Signed in as the homeowner is the normal case and says nothing; a named
  // key or a Home Assistant token is worth a line.
  const via =
    req.agent_kind && req.agent_kind !== "selora_account"
      ? connectedWith(host, req)
      : "";
  return html`
    ${renderCardHeading({
      icon: "mdi:shield-key-outline",
      title: t(
        host,
        [
          "file_access_request_headline",
          "{agent} wants to add code that runs in your browser",
        ],
        { agent: who },
      ),
    })}
    ${renderReason(host, req.reason)}
    <div class="file-access-risk">
      <ha-icon icon="mdi:alert-outline"></ha-icon>
      <span>
        ${host._t(
          "file_access_risk",
          "The code runs in your browser, signed in as you: it can do whatever you can in Home Assistant.",
        )}
      </span>
    </div>
    <div class="file-access-section-label">
      ${host._t("file_access_safeguards", "Safeguards")}
    </div>
    <ul class="file-access-points">
      ${SAFEGUARDS.map(
        ([icon, key, fallback]) => html`
          <li>
            <ha-icon icon=${icon}></ha-icon>
            <span>${t(host, [key, fallback], { folder: req.folder })}</span>
          </li>
        `,
      )}
    </ul>
    ${
      via
        ? html`<p class="file-access-via">
            ${host._t("file_access_connected_with", "Connected with")}: ${via}
          </p>`
        : ""
    }
    <div class="file-access-footer">
      <div class="file-access-duration">
        <span id="file-access-duration-label"
          >${host._t("file_access_duration_label", "Allow for:")}</span
        >
        <div
          class="file-access-segments"
          role="radiogroup"
          aria-labelledby="file-access-duration-label"
        >
          ${FILE_ACCESS_MINUTES.map(
            (m) => html`
              <button
                class="file-access-segment"
                role="radio"
                aria-checked=${m === minutes ? "true" : "false"}
                @click=${() => (host._fileAccessMinutes = m)}
              >
                ${t(host, ["file_access_minutes", "{minutes} min"], {
                  minutes: m,
                })}
              </button>
            `,
          )}
        </div>
      </div>
      <div class="file-access-footer-buttons">
        <button
          class="btn btn-outline"
          ?disabled=${host._fileAccessBusy}
          @click=${() => host._decideFileAccess(req.id, false)}
        >
          ${host._t("file_access_deny", "Deny")}
        </button>
        <button
          class="btn btn-primary"
          ?disabled=${host._fileAccessBusy}
          @click=${() => host._decideFileAccess(req.id, true, minutes)}
        >
          ${host._t("file_access_approve", "Allow")}
        </button>
      </div>
    </div>
  `;
}

function findingSeverity(f) {
  return SEVERITY_ORDER[f?.severity] !== undefined ? f.severity : "info";
}

// A finding is a label on its own line and what it found under it: side by
// side, "Controls devices service chosen at runtime" read as one sentence.
function renderFinding(host, f) {
  const label = KIND_LABELS[f?.kind] || ["file_access_finding_other", "Other"];
  return html`
    <li class="file-access-finding file-access-finding--${findingSeverity(f)}">
      <div class="file-access-finding-kind">${t(host, label)}</div>
      <div class="file-access-finding-body">
        ${f?.detail ? html`<code>${f.detail}</code>` : ""}
        ${f?.earlier ? renderEarlier(host) : ""}
        ${
          f?.line
            ? html`<span class="file-access-finding-line">
                ${t(host, ["file_access_line", "line {line}"], { line: f.line })}
              </span>`
            : ""
        }
      </div>
    </li>
  `;
}

// Found in a version since replaced or deleted: it may have run before the
// agent cleaned it up, so it stays on the record, marked.
function renderEarlier(host) {
  return html`<span class="file-access-finding-earlier">
    ${host._t("file_access_earlier", "in an earlier version")}
  </span>`;
}

// The backend's sentences about actions picked while the code runs; the rest
// of a service finding's detail is a `domain.service` (or "domain (any
// action)") the file names.
const SERVICE_NOTES = {
  // Written by an earlier version of the check; same meaning.
  "service chosen at runtime": [
    "file_access_service_unlisted",
    "Which ones is decided while it runs, so they can't be listed.",
  ],
  "which actions is decided while it runs; none could be listed": [
    "file_access_service_unlisted",
    "Which ones is decided while it runs, so they can't be listed.",
  ],
  "may also call actions decided while it runs": [
    "file_access_service_more",
    "It may also call others, decided while it runs.",
  ],
};

const DOMAIN_ICONS = {
  alarm_control_panel: "mdi:shield-home-outline",
  automation: "mdi:robot-outline",
  climate: "mdi:thermostat",
  cover: "mdi:window-shutter",
  fan: "mdi:fan",
  light: "mdi:lightbulb-outline",
  lock: "mdi:lock-outline",
  media_player: "mdi:speaker",
  notify: "mdi:message-outline",
  scene: "mdi:palette-outline",
  script: "mdi:script-text-outline",
  switch: "mdi:toggle-switch-outline",
  valve: "mdi:pipe-valve",
};

function humanize(word) {
  const text = String(word).replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

// "lock.unlock" → {icon, label: "Lock: Unlock"}, in Home Assistant's own words
// when its translations are loaded; "cover (any action)" → "Cover: any action".
function describeAction(host, detail) {
  const raw = String(detail ?? "").replace(/ \(×\d+\)$/, "");
  const any = raw.match(/^([a-z0-9_]+) \(any action\)$/);
  const pair = raw.match(/^([a-z0-9_]+)\.([a-z0-9_]+)$/);
  const domain = any?.[1] || pair?.[1];
  if (!domain) return { icon: "mdi:gesture-tap", label: raw };
  const localize = host.hass?.localize?.bind(host.hass);
  const domainName =
    localize?.(`component.${domain}.title`) || humanize(domain);
  const action = any
    ? host._t("file_access_any_action", "any action")
    : localize?.(`component.${domain}.services.${pair[2]}.name`) ||
      humanize(pair[2]);
  return {
    icon: DOMAIN_ICONS[domain] || "mdi:gesture-tap",
    label: `${domainName}: ${action}`,
  };
}

function renderActionChips(host, actions) {
  return html`<div class="file-access-chips">
    ${actions.map((f) => {
      const { icon, label } = describeAction(host, f?.detail);
      return html`<span class="file-access-chip">
        <ha-icon icon=${icon}></ha-icon>${label}${
          f?.earlier ? html` · ${renderEarlier(host)}` : ""
        }
      </span>`;
    })}
  </div>`;
}

// Every action a file calls, in one finding: a dashboard calls a dozen, and a
// row each buried the findings that matter. In words, with the ones that open,
// unlock or disarm set apart — a list of `domain.service` codes with warning
// signs between them read as one sentence.
function renderServices(host, services) {
  const notes = [];
  const actions = [];
  for (const f of services) {
    const note =
      SERVICE_NOTES[String(f?.detail ?? "").replace(/ \(×\d+\)$/, "")];
    if (note) notes.push(note);
    else actions.push(f);
  }
  const sensitive = actions.filter((f) => findingSeverity(f) === "medium");
  const routine = actions.filter((f) => findingSeverity(f) !== "medium");
  // Nothing listed at all is a blind spot, worth the same look as a lock.
  const flagged = sensitive.length > 0 || !actions.length;
  return html`
    <li
      class="file-access-finding file-access-finding--${
        flagged ? "medium" : "info"
      }"
    >
      <div class="file-access-finding-kind">
        ${t(host, KIND_LABELS.service)}
      </div>
      ${
        sensitive.length
          ? html`<div class="file-access-chips-label">
                ${host._t(
                  "file_access_actions_sensitive",
                  "Can open, unlock or disarm",
                )}
              </div>
              ${renderActionChips(host, sensitive)}`
          : ""
      }
      ${
        routine.length
          ? html`<div class="file-access-chips-label">
                ${host._t("file_access_actions_routine", "Everyday controls")}
              </div>
              ${renderActionChips(host, routine)}`
          : ""
      }
      ${notes.map(
        (n) => html`<div class="file-access-finding-note">${t(host, n)}</div>`,
      )}
    </li>
  `;
}

// What a file is for, in words: the path alone told a homeowner nothing.
function fileUse(host, writes) {
  const latest = writes[writes.length - 1] || {};
  if (latest.action === "deleted") {
    return host._t("file_access_use_deleted", "Removed from your home.");
  }
  if (latest.loaded === true) {
    return host._t(
      "file_access_use_loaded",
      "Your browser runs it on every dashboard: it is a dashboard resource.",
    );
  }
  if (latest.loaded === false) {
    return host._t(
      "file_access_use_imported",
      "Not a dashboard resource itself; another file it wrote may load it.",
    );
  }
  return "";
}

const FILE_ICONS = {
  js: "mdi:language-javascript",
  mjs: "mdi:language-javascript",
  css: "mdi:language-css3",
  html: "mdi:language-html5",
  htm: "mdi:language-html5",
  json: "mdi:code-json",
  svg: "mdi:svg",
};

function fileIcon(group) {
  const latest = group.writes[group.writes.length - 1];
  if (latest?.action === "deleted") return "mdi:file-remove-outline";
  const ext = String(group.file).split(".").pop().toLowerCase();
  return FILE_ICONS[ext] || "mdi:file-code-outline";
}

function basename(path) {
  return String(path).split("/").pop();
}

function renderFileGroup(host, group) {
  const findings = sortedFindings(
    group.writes.flatMap((w) => w?.findings || []),
  );
  const risky = findings.some(
    (f) => f?.severity === "high" || f?.severity === "medium",
  );
  const others = findings.filter((f) => f?.kind !== "service");
  const services = findings.filter((f) => f?.kind === "service");
  const use = fileUse(host, group.writes);
  return html`
    <div class="file-access-file">
      <div class="file-access-file-head">
        <span class="file-access-file-icon">
          <ha-icon icon=${fileIcon(group)}></ha-icon>
        </span>
        <div class="file-access-file-title">
          <div class="file-access-file-name">${basename(group.file)}</div>
          ${group.writes.map(
            (w) => html`
              <div class="file-access-write">
                <code>${group.file}</code> ·
                ${t(host, ACTION_LABELS[w?.action] || ACTION_LABELS.replaced)}
                ${formatTime(w?.at)} · ${formatSize(w?.size)}
              </div>
            `,
          )}
        </div>
      </div>
      ${use ? html`<p class="file-access-file-use">${use}</p>` : ""}
      ${
        findings.length
          ? html`<div class="file-access-checks-label">
                ${host._t("file_access_checks", "What the code does")}
              </div>
              <ul class="file-access-findings">
                ${others.map((f) => renderFinding(host, f))}
                ${services.length ? renderServices(host, services) : ""}
              </ul>`
          : ""
      }
      ${
        risky
          ? ""
          : html`<div class="file-access-clean">
              ${host._t("file_access_no_risk", "No risky patterns found")}
            </div>`
      }
    </div>
  `;
}

// The agent's own reason, quoted under a label so it never reads as Selora's.
function renderReason(host, reason) {
  if (!reason) return "";
  return html`<div class="file-access-section-label">
      ${host._t("file_access_request_title", "Temporary access request")}
    </div>
    <blockquote class="file-access-reason">“${reason}”</blockquote>`;
}

function isRisky(group) {
  return group.writes.some((w) =>
    (w?.findings || []).some(
      (f) => f?.severity === "high" || f?.severity === "medium",
    ),
  );
}

// The verdict before the detail: how many files need a look, in the same box
// the request used for its risk — or a quiet line when none does.
function renderWritesSummary(host, groups) {
  if (!groups.length) return "";
  const flagged = groups.filter(isRisky).length;
  if (!flagged) {
    return html`<div class="file-access-summary">
      <ha-icon icon="mdi:check-circle-outline"></ha-icon>
      <span>
        ${host._t("file_access_summary_clean", "Nothing risky found in what it wrote")}
      </span>
    </div>`;
  }
  return html`<div class="file-access-risk">
    <ha-icon icon="mdi:alert-outline"></ha-icon>
    <span>
      ${
        flagged === 1
          ? host._t(
              "file_access_summary_flagged_one",
              "1 file does something worth checking",
            )
          : t(
              host,
              [
                "file_access_summary_flagged",
                "{count} files do something worth checking",
              ],
              { count: flagged },
            )
      }
    </span>
  </div>`;
}

function grantHeading(host, grant) {
  const agent =
    grant.agent_client ||
    host._t("file_access_an_assistant", "An AI assistant");
  if (!grant.ended_at) {
    return renderCardHeading({
      icon: "mdi:shield-key-outline",
      title: t(
        host,
        [
          "file_access_grant_headline",
          "{agent} can add code that runs in your browser",
        ],
        { agent },
      ),
    });
  }
  const revoked = grant.ended_reason === "revoked";
  return renderCardHeading({
    icon: "mdi:shield-check-outline",
    title: t(
      host,
      ["file_access_ended_headline", "{agent}'s access has ended"],
      { agent },
    ),
    subtitle: t(
      host,
      revoked
        ? ["file_access_revoked_at", "Revoked at {time}"]
        : ["file_access_expired_at", "Expired at {time}"],
      { time: formatTime(grant.ended_at) },
    ),
  });
}

function renderGrant(host, grant) {
  const ended = Boolean(grant.ended_at);
  const groups = groupWritesByFile(grant.writes);
  return html`
    ${grantHeading(host, grant)} ${ended ? "" : renderTimeLeft(host, grant)}
    ${renderReason(host, grant.reason)}
    <div class="file-access-section-label">
      ${host._t("file_access_files", "Files it wrote")}
    </div>
    ${renderWritesSummary(host, groups)}
    ${
      groups.length
        ? groups.map((g) => renderFileGroup(host, g))
        : html`<p class="file-access-empty">
            ${host._t("file_access_no_writes", "No files written yet.")}
          </p>`
    }
    <div class="file-access-actions">
      <button class="btn btn-outline" @click=${() => host._closeFileAccess()}>
        ${host._t("file_access_close", "Close")}
      </button>
      ${
        ended
          ? html`<button
              class="btn btn-primary"
              ?disabled=${host._fileAccessBusy}
              @click=${() => host._dismissFileAccess(grant.id)}
            >
              ${host._t("file_access_mark_reviewed", "Mark as reviewed")}
            </button>`
          : html`<button
              class="btn btn-danger"
              ?disabled=${host._fileAccessBusy}
              @click=${() => host._revokeFileAccess(grant.id)}
            >
              ${host._t("file_access_revoke", "Revoke")}
            </button>`
      }
    </div>
  `;
}

// Time left the way Connect shows a support session: the hours and minutes
// left (no seconds — it runs in the background) over a bar draining from the
// approval to the end. The clock time it ends is the small print.
function renderTimeLeft(host, grant) {
  const fraction = remainingFraction(grant);
  return html`
    <div class="file-access-timer">
      <div class="file-access-timer-row">
        <span class="file-access-timer-left"
          >${formatRemaining(grant.expires_at)}</span
        >
        <span class="file-access-timer-note">
          ${t(host, ["file_access_left_ends", "left · ends {time}"], {
            time: formatTime(grant.expires_at),
          })}
        </span>
      </div>
      <div class="file-access-timer-bar">
        <span style="width: ${(fraction * 100).toFixed(1)}%"></span>
      </div>
    </div>
  `;
}

function renderGone(host) {
  return html`
    ${renderCardHeading({
      icon: "mdi:shield-off-outline",
      title: host._t("file_access_request_title", "Temporary access request"),
    })}
    <p class="file-access-status">
      ${host._t(
        "file_access_gone",
        "This request was already answered or has expired.",
      )}
    </p>
    <div class="file-access-actions">
      <button class="btn btn-outline" @click=${() => host._closeFileAccess()}>
        ${host._t("file_access_close", "Close")}
      </button>
    </div>
  `;
}

export function renderFileAccessDialog(host) {
  const target = host._fileAccessDialog;
  if (!target) return "";
  const resolved =
    target.kind === "gone"
      ? { mode: "gone", item: null }
      : resolveFileAccessTarget(host._fileAccess, target);
  let body;
  if (resolved.mode === "request") body = renderRequest(host, resolved.item);
  else if (resolved.mode === "grant") body = renderGrant(host, resolved.item);
  else if (resolved.mode === "gone") body = renderGone(host);
  else body = html`<span class="spinner"></span>`;
  return html`
    <div
      class="modal-overlay"
      @click=${(e) => {
        if (e.target === e.currentTarget) host._closeFileAccess();
      }}
    >
      <div
        class="modal-content file-access-dialog"
        role="dialog"
        aria-modal="true"
      >
        ${body}
      </div>
    </div>
  `;
}
