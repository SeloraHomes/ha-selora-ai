/**
 * Actions Selora proposes but the PANEL performs.
 *
 * Creating a dashboard needs Home Assistant's `lovelace/dashboards/create`
 * websocket command. The integration runs in-process and cannot call it; this
 * panel is already an authenticated websocket client, so it can — under the
 * signed-in user's own account and permissions.
 *
 * The security boundary lives here: the backend sends a CLOSED intent
 * (kind + validated fields) and this file builds the websocket payload from a
 * fixed shape. Nothing the model wrote is ever forwarded as a command. If that
 * inverted — if the panel passed through a payload the backend assembled from
 * model output — the model could issue any admin websocket command through the
 * user's session.
 */

/**
 * Whether the dashboard sitting there is the one this action was built for.
 *
 * Compared on the four fields a dashboard's metadata carries, normalized the
 * same way the create payload normalizes them — an absent `show_in_sidebar`
 * and an explicit `false` are the same stored dashboard, and an absent icon is
 * not a mismatch against an action that carries none. HA stores these
 * verbatim, so a retry of our own create agrees on all four.
 *
 * Both handlers ask it, for the same reason from opposite ends: create wants
 * to know whether the dashboard now at that url_path is its own earlier
 * attempt, delete wants to know whether the dashboard now holding that id is
 * still the one the card named.
 */
function matchesProposal(existing, expected) {
  return (
    String(existing.title || "") === String(expected.title || "") &&
    String(existing.icon || "") === String(expected.icon || "") &&
    Boolean(existing.require_admin) === Boolean(expected.require_admin) &&
    Boolean(existing.show_in_sidebar) === Boolean(expected.show_in_sidebar)
  );
}

/** Kinds this panel will execute. Checked again here, not just server-side. */
const HANDLERS = {
  delete_dashboard: async (hass, action) => {
    const urlPath = String(action.url_path || "");

    // Matched by the COLLECTION id the proposal resolved, not by url_path. A
    // path is reusable: delete a dashboard and make another at the same path
    // between the proposal and the tap, and a card approved for the first
    // would remove the second. The id is what HA deletes by anyway.
    const dashboardId = String(action.dashboard_id || "");
    const existing = await hass.callWS({ type: "lovelace/dashboards/list" });
    const match = (existing || []).find((d) => d?.id && d.id === dashboardId);
    if (!match) {
      // Idempotent, because the card can outlive its own execution: if the
      // delete succeeded and the REPORT failed, a retry finds it already gone.
      // That is the outcome the user asked for, not a failure.
      return { url_path: urlPath, title: action.title, already_gone: true };
    }
    if (urlPath && match.url_path !== urlPath) {
      // Same dashboard, moved. The card named a page the user recognised, and
      // deleting something that now answers to a different address is not what
      // they approved.
      throw new Error(
        `That dashboard is no longer at /${urlPath} — it is at /${match.url_path}. ` +
          `Ask again to confirm which one to delete.`,
      );
    }

    // An id is not immutable either. HA derives a dashboard's collection id
    // from its url_path, so deleting one frees the id — delete /office and
    // make a new /office before the tap, and the replacement answers to both
    // handles the check above uses. Comparing the metadata is what separates
    // them. `expected` is absent on a card built before it was sent, which is
    // a proposal that outlived a deploy: verify what is there to verify rather
    // than failing a delete the user asked for on a field nobody sent.
    if (action.expected && !matchesProposal(match, action.expected)) {
      throw new Error(
        `The dashboard at /${match.url_path} is not the one this card named — ` +
          `"${String(match.title || "")}" is there now. ` +
          `Ask again to confirm which one to delete.`,
      );
    }

    await hass.callWS({
      type: "lovelace/dashboards/delete",
      dashboard_id: match.id,
    });
    return { url_path: urlPath, title: match.title || action.title };
  },

  create_dashboard: async (hass, action) => {
    const urlPath = String(action.url_path || "");

    // Idempotent, because the card can outlive its own execution. If the
    // create succeeds and the RESULT REPORT then fails, the backend still
    // serves the proposal as pending — so after a refresh the button is back,
    // and a blind retry would fail on a url_path that already exists and
    // record the card as denied for a dashboard that is sitting right there.
    // Checking first turns that retry into the reconciliation it should be.
    //
    // The backend refuses a colliding url_path when it builds the proposal, so
    // a match here means the dashboard appeared after that. Only one of the
    // ways that can happen is a retry of THIS action, so the url_path alone
    // does not settle it: the user may have made an unrelated dashboard at
    // that path in between, and reconciling to it reports success for a
    // dashboard nobody created — the transcript then names a title that
    // exists nowhere. So every field this action would have set has to match.
    // A retry of our own create matches all of them by construction, since
    // they are the values it sent; anything else is a genuine collision and is
    // reported as the failure it is.
    const existing = await hass.callWS({ type: "lovelace/dashboards/list" });
    const already = (existing || []).find((d) => d?.url_path === urlPath);
    if (already) {
      if (!matchesProposal(already, action)) {
        throw new Error(
          `The dashboard at /${urlPath} is not the one proposed — ` +
            `"${String(already.title || "")}" is already there. ` +
            `Pick another url_path, or edit that dashboard instead.`,
        );
      }
      return {
        url_path: already.url_path,
        title: already.title || action.title,
      };
    }

    // Built field by field. Never spread `action` into the call: that is what
    // would let an unexpected key ride along into a privileged command.
    const payload = {
      type: "lovelace/dashboards/create",
      title: String(action.title || ""),
      url_path: String(action.url_path || ""),
      require_admin: Boolean(action.require_admin),
      show_in_sidebar: Boolean(action.show_in_sidebar),
    };
    if (action.icon) payload.icon = String(action.icon);
    if (action.allow_single_word) payload.allow_single_word = true;
    const created = await hass.callWS(payload);
    return {
      url_path: created?.url_path || payload.url_path,
      title: created?.title || payload.title,
    };
  },
};

/**
 * Run one proposed client action and report what actually happened.
 *
 * Returns `{ok, kind, detail}`. The caller sends this back to the backend so
 * the conversation reflects the real outcome — Selora must not claim the
 * dashboard exists before Home Assistant has made it.
 */
export async function runClientAction(hass, action) {
  const kind = String(action?.kind || "");
  const handler = HANDLERS[kind];
  if (!handler) {
    return { ok: false, kind, detail: `Unsupported action: ${kind}` };
  }
  try {
    const detail = await handler(hass, action);
    return { ok: true, kind, detail };
  } catch (err) {
    // A websocket error here is the user's own permissions or a colliding
    // url_path — surfaced verbatim, because a generic failure would send them
    // looking in the wrong place.
    return { ok: false, kind, detail: err?.message || String(err) };
  }
}

/**
 * Execute every action on a card, then tell the backend the results.
 *
 * Reporting is not optional: without it the card resolves in the UI while the
 * conversation still says nothing happened, or worse, implies it did.
 */
/** Proposals currently executing, for cards with no message to mark. */
const IN_FLIGHT = new Set();

export async function resolveClientActions(host, msg, approval) {
  // Synchronously, before any await. A double click lands both handlers before
  // Lit rerenders the disabled button, and the idempotency check inside the
  // action cannot save us: both would finish the dashboard-list request before
  // either creates, both would see nothing, and both would create. One then
  // fails and overwrites the card as denied for a dashboard that exists.
  const proposalId = approval?.proposal_id;
  if ((msg && msg._resolving) || IN_FLIGHT.has(proposalId)) return;
  IN_FLIGHT.add(proposalId);

  const actions = approval?.client_actions || [];
  // Captured BEFORE the first await. The user can switch conversations while
  // the websocket call is in flight, and reporting the old proposal against the
  // new session fails — leaving the card pending on a dashboard that exists.
  const sessionId = host._activeSessionId;

  // The card renders msg.approval_status, and hiding the buttons is what stops
  // a second click running the command twice — same handling as the
  // server-resolved cards in chat-actions.js.
  if (msg) {
    msg._resolving = true;
    msg.quick_actions = null;
    msg.approval_status = "resolving";
    host._messages = [...host._messages];
  }

  const results = [];
  for (const action of actions) {
    results.push(await runClientAction(host.hass, action));
  }
  const ok = results.length > 0 && results.every((r) => r.ok);

  let reported = true;
  try {
    await host.hass.callWS({
      type: "selora_ai/client_action_result",
      session_id: sessionId,
      proposal_id: approval.proposal_id,
      results,
      // The language RESOLVED for the turn, carried on the proposal. NOT
      // hass.language, which is only the UI locale: a French message on an
      // English-UI install must get a French outcome, and the panel cannot
      // work out which — only the turn that detected it knows.
      ...(approval.language || host.hass?.language
        ? { language: approval.language || host.hass.language }
        : {}),
    });
  } catch (err) {
    // The command itself may well have run; only the report failed. Do NOT
    // reload: the server's copy is still pending, and swapping it in would put
    // the button back and invite a second creation of a dashboard that already
    // exists. The locally resolved state stays instead.
    reported = false;
    console.error("Selora AI: could not report client action result", err);
  }

  if (msg) {
    msg._resolving = false;
    msg.approval_status = ok ? "approved" : "denied";
    host._messages = [...host._messages];
  }
  // Only once the backend agrees, and only for the session this card belongs
  // to — reloading a conversation the user has since left would yank them back.
  IN_FLIGHT.delete(proposalId);
  if (reported && sessionId && host._activeSessionId === sessionId) {
    await host._openSession?.(sessionId);
    // The card was only ever the first step — a client action exists because
    // the thing it makes does not exist yet. Not gated on a declared
    // remainder: the model announces the follow-up in prose and leaves the
    // field unset, so the server replays the original request instead and
    // decides what is outstanding. It also refuses a denial, a repeat, and
    // anything already continued once.
    if (ok) {
      await host._sendMessage?.({ resumeProposalId: proposalId });
    }
  }
  host.requestUpdate();
}
