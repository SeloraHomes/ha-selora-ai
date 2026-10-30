import { html } from "lit";

import { renderActionButton } from "../shared/action-button.js";

// Map approval-scope → presentation. Persisted messages from before the
// choice-card switch carry mode="confirmation" with no icon/tone, so we
// normalise every approval action here regardless of what the backend
// stored. Detection is by sentinel value prefix ``approve:<scope>:…``
// — that's the contract between backend and frontend, so it's stable
// across versions.
function _approvalPresentation(host) {
  return {
    once: {
      label: host._t("quick_actions_approve_once_label", "Allow once"),
      icon: "mdi:check",
      tone: "approve",
      description: host._t(
        "quick_actions_approve_once_desc",
        "Just this one request",
      ),
    },
    session: {
      label: host._t(
        "quick_actions_approve_session_label",
        "For this conversation",
      ),
      icon: "mdi:check-all",
      tone: "approve",
      description: host._t(
        "quick_actions_approve_session_desc",
        "Allow for the rest of this conversation",
      ),
    },
    always: {
      label: host._t("quick_actions_approve_always_label", "Always"),
      icon: "mdi:shield-check",
      tone: "approve",
      description: host._t(
        "quick_actions_approve_always_desc",
        "Remember this approval",
      ),
    },
    deny: {
      label: host._t("quick_actions_deny_label", "Deny"),
      icon: "mdi:close",
      tone: "deny",
      description: host._t(
        "quick_actions_deny_desc",
        "Do not run this request",
      ),
    },
    // Delete-confirmation card (approval_kind === "delete"): a destructive
    // confirm + a neutral cancel. The confirm is styled red (deny tone) so
    // it reads as destructive rather than as a friendly "approve".
    delete: {
      label: host._t("quick_actions_delete_label", "Delete"),
      icon: "mdi:delete-outline",
      tone: "deny",
      description: host._t(
        "quick_actions_delete_desc",
        "Permanently delete this",
      ),
    },
    // A change (disable, rename, what a camera looks for) confirms with its
    // own scope. A card that also deletes keeps the delete scope and asks for
    // this wording with `presentation`; the server sends the deny tone then.
    apply: {
      label: host._t("quick_actions_apply_label", "Apply"),
      icon: "mdi:check",
      tone: "approve",
      description: host._t("quick_actions_apply_desc", "Make this change"),
    },
    cancel: {
      label: host._t("quick_actions_cancel_label", "Cancel"),
      icon: "mdi:close",
      tone: null,
      description: host._t("quick_actions_cancel_desc", "Keep it"),
    },
  };
}

function _approvalScope(value) {
  if (typeof value !== "string" || !value.startsWith("approve:")) return null;
  return value.split(":", 2)[1] || null;
}

// Return a copy of *actions* where any approval-scope action is upgraded
// to the choice-card presentation (icon + tone + description). Untouched
// when no approval sentinels are present so existing AI-suggested
// choices keep their original payload.
function _normalizeApprovalActions(host, actions) {
  let touched = false;
  const presentation = _approvalPresentation(host);
  const out = actions.map((a) => {
    const scope = _approvalScope(a?.value);
    if (!scope) return a;
    const preset = presentation[a.presentation || scope];
    if (!preset) return a;
    touched = true;
    return {
      ...a,
      // Override label too — older persisted messages may have shipped
      // "Session" / "Allow once" wording, but we want the new copy
      // ("For this conversation") to appear consistently.
      label: preset.label,
      mode: "choice",
      icon: a.icon || preset.icon,
      tone: a.tone || preset.tone,
      description: a.description || preset.description,
    };
  });
  return touched ? out : actions;
}

/**
 * Render quick-action buttons below a chat message or in the welcome screen.
 * Every one is the panel's action button (shared/action-button.js).
 *
 * Each action object:
 *   { label, value?, description?, icon?, mode?, primary?, tone?, inline? }
 *
 * The badge says what the press does: the action's own mark on an approval
 * or an answer to the message's question, the send arrow on anything that
 * sends its words to Selora. The first answer a row offers is gold (one per
 * row); a press that deletes is red.
 *
 * @param {object}   host     Panel element (provides _selectQuickAction)
 * @param {object[]} actions  List of quick-action objects
 * @param {object}   [opts]   { used: boolean, picked?: string } — dims the
 *   group after selection; the action whose value is `picked` stays lit
 */
export function renderQuickActions(host, actions, opts = {}) {
  if (!actions || !actions.length) return "";
  actions = _normalizeApprovalActions(host, actions);

  const picked = opts.used && actions.some((a) => a.value === opts.picked);
  const usedClass = opts.used
    ? picked
      ? " qa-group--used qa-group--picked"
      : " qa-group--used"
    : "";
  // Chips that start a conversation sit where the welcome screen puts them;
  // answers to a message sit under it, at its right edge.
  const answers = actions.some(
    (a) => a.inline || a.tone || a.primary !== undefined || a.mode === "choice",
  );
  const lead = _leadIndex(actions);
  return html`
    <div class="qa-group${answers ? " qa-group--answers" : ""}${usedClass}">
      ${actions.map((a, i) =>
        renderActionButton({
          label: a.label,
          description: _ownsMark(a) ? "" : a.description || "",
          title: a.description ? `${a.label} — ${a.description}` : a.label,
          mark: _mark(a, i === lead),
          tone: _isDestructive(a)
            ? "danger"
            : i === lead
              ? "primary"
              : _approvalScope(a.value) === "deny"
                ? "decline"
                : "quiet",
          picked: picked && a.value === opts.picked,
          onClick: () => _onSelect(host, a),
        }),
      )}
    </div>
  `;
}

// The answer the row offers: the first approval or primary, or the first of
// the answers to the message's own question. A row of plain suggestions has
// none, so no chip outshines the others.
function _leadIndex(actions) {
  const offered = actions.findIndex(
    (a) => !_isDestructive(a) && (a.tone === "approve" || a.primary === true),
  );
  if (offered !== -1) return offered;
  return actions[0]?.inline ? 0 : -1;
}

// Red for a confirm that removes something. After normalising, every delete
// confirm carries the deny tone, and so does an Apply on a card that deletes
// alongside another change; the refusals share that tone but only decline.
function _isDestructive(action) {
  const scope = _approvalScope(action.value);
  return (
    action.tone === "deny" && !!scope && scope !== "deny" && scope !== "cancel"
  );
}

// Approvals and answers to the message's own question carry their own mark
// (their description is the tooltip); anything else sends its words.
function _ownsMark(action) {
  return !!(action.inline || action.tone || _approvalScope(action.value));
}

function _mark(action, isLead) {
  if (action.inline) {
    return isLead ? "mdi:check" : action.icon || "mdi:close";
  }
  if (_ownsMark(action)) return action.icon || "mdi:check";
  return "mdi:arrow-up";
}

function _onSelect(host, action) {
  host._selectQuickAction(action);
}

/**
 * One confirmation button — the shape every "tap to authorize this" press in
 * chat uses, whether it resolves server-side (Allow / Deny on a risk card) or
 * in the panel (Create on a client-action card).
 *
 * Exported so a card can render one with its OWN handler: a client action has
 * no quick-action value to send back, it calls the panel directly.
 *
 * @param {object} host      panel element
 * @param {object} action    {label, icon?, description?, tone?, primary?}
 * @param {Function} onClick what the press does
 */
export function renderConfirmChip(host, action, onClick) {
  const tone =
    action.tone === "deny"
      ? "danger"
      : action.tone === "approve" || action.tone === "primary" || action.primary
        ? "primary"
        : "quiet";
  return renderActionButton({
    label: action.label,
    description: action.description || "",
    mark: action.icon || "mdi:check",
    tone,
    onClick,
  });
}
