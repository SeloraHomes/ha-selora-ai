// ---------------------------------------------------------------------------
// "What changed?" panel for a pending automation proposal
// ---------------------------------------------------------------------------
// When the model refines an automation it describes the change in prose
// ("temperature now exceeds 19°C, previously 18"). That prose is a claim; this
// panel is the evidence.
//
// The question it answers is narrower than "what did the model change": it is
// "what will accepting this write, over what". Nothing here reconstructs that
// answer. Which automation gets written comes from the resolver the accept path
// branches on, and both documents come from selora_ai/preview_automation_write,
// which runs the writer's own helpers over the live automations.yaml without
// writing. This module only diffs and renders what it is handed.
//
// That division is the whole design. Every part of this answer reconstructed on
// the client — normalizing YAML the way the save would, guessing which keys the
// save overrides, reading a version store the save does not consult — described
// what ought to happen rather than what will, which is worse than showing
// nothing.
// ---------------------------------------------------------------------------

import { html } from "lit";
import {
  diffLines,
  collapseDiff,
  DEFAULT_CONTEXT_RADIUS,
} from "../shared/yaml-diff.js";
import {
  _getRefiningAutomationId,
  _initialStateEdited,
} from "./automation-crud.js";

// A preview is re-requested as the user types in the YAML editor, which reports
// every keystroke. The first request for a card goes out immediately so the
// chip appears at once; later ones wait for the text to settle.
const PREVIEW_DEBOUNCE_MS = 350;

// Ask the backend what accepting would overwrite and write.
//
// Both sides have to come from the save path, and only it can produce them:
//
//   - the current side is read from automations.yaml, so an automation edited
//     in HA's own editor — which never touches Selora's version store — is
//     compared against what is really there;
//   - the proposed side is the submitted text parsed, validated and re-dumped
//     by the writer's own helpers, so formatting, key order and comments (none
//     of which survive a save) do not show as changes, while the fields the
//     save owns do: `id` rewritten, `initial_state` preserved from disk, or
//     forced off when the refinement escalates the automation's risk.
//
// The request carries the editor's text, so it repeats as the user types.
// Entries are keyed by that text and the request is scheduled, not fired, so a
// burst of keystrokes leaves one call; the first request for a card skips the
// wait so the chip appears immediately.
// `pending` covers the whole request — scheduled AND awaiting — because the
// timer handle is cleared the moment the call goes out, leaving a window in
// which a second refresh would race the first. The generation token is the
// backstop: an answer that is no longer the newest request for this entry is
// dropped rather than written, so a slow early reply cannot overwrite a fresh
// one and put a stale document back on screen.
function requestPreview(host, entry, delay) {
  const generation = ++entry.generation;
  entry.pending = true;
  entry.timer = setTimeout(async () => {
    entry.timer = null;
    let current = "";
    let proposed = "";
    try {
      const result = await host.hass?.callWS({
        type: "selora_ai/preview_automation_write",
        automation_id: entry.targetId,
        yaml_text: entry.yamlText,
        preserve_enabled_state: entry.preserveEnabledState,
      });
      current = result?.current_yaml || "";
      proposed = result?.proposed_yaml || "";
    } catch {
      // Unparseable YAML, an automation not on disk, an older integration that
      // has no such command: nothing to show rather than something
      // reconstructed here, which is what this whole endpoint exists to stop.
    }
    if (generation !== entry.generation) return;

    entry.current = current;
    entry.proposed = proposed;
    entry.loading = false;
    entry.pending = false;
    // Marked stale while this was in flight: the answer just applied may
    // already describe the previous document, so go again now that the slot
    // is free.
    if (entry.stale) {
      entry.stale = false;
      requestPreview(host, entry, 0);
    }
    host.requestUpdate?.();
  }, delay);
}

function previewWrite(host, msgIndex, request) {
  if (!host._previewCache) host._previewCache = new Map();
  const cached = host._previewCache.get(msgIndex);
  const sameRequest =
    cached &&
    cached.targetId === request.targetId &&
    cached.yamlText === request.yamlText &&
    cached.preserveEnabledState === request.preserveEnabledState;

  if (sameRequest) {
    // The automation can be edited in HA while this card sits on screen, so a
    // preview is only good until something says otherwise. Re-request in the
    // background and keep serving the last answer: dropping it would blank the
    // chip and the open panel for the length of a round trip.
    if (cached.stale && !cached.pending) {
      cached.stale = false;
      requestPreview(host, cached, 0);
    }
    return cached;
  }

  if (cached?.timer) clearTimeout(cached.timer);
  const entry = {
    ...request,
    stale: false,
    loading: true,
    pending: false,
    generation: 0,
    current: "",
    proposed: "",
    timer: null,
  };
  host._previewCache.set(msgIndex, entry);
  requestPreview(host, entry, cached ? PREVIEW_DEBOUNCE_MS : 0);
  return entry;
}

// Mark every previewed card for re-request. Called when the panel reloads
// automations and when a diff is reopened — the two moments where a document
// edited elsewhere would otherwise keep showing its old contents.
export function invalidateProposalPreviews(host, msgIndex = null) {
  if (!host._previewCache) return;
  const entries =
    msgIndex === null
      ? host._previewCache.values()
      : [host._previewCache.get(msgIndex)].filter(Boolean);
  for (const entry of entries) entry.stale = true;
}

/**
 * Resolve the diff for a pending proposal, or null when there is nothing
 * comparable — accepting will create a new automation, the preview has not
 * arrived, or it could not be produced.
 */
// The diff for an automation ALREADY SAVED: the stored version before this one
// against the one now on disk.
//
// A different question from `proposalDiff`, which compares the file against
// what accepting WOULD write — after the write both its sides are the same
// document, so it correctly has nothing to show and the card lost its diff at
// exactly the moment "what did that change" became worth asking.
//
// Rendered through the same toggle and panel as the pending card, so there is
// one diff vocabulary in the UI rather than two, and no dialog: the compare
// dialog's two selects are a version PICKER, and the answer wanted here is
// always the same pair.
export function savedDiff(host, msgIndex) {
  const msg = (host._messages || [])[msgIndex];
  const automationId = msg?.automation_id;
  if (!automationId) return null;

  const versions = host._versions?.[automationId];
  if (versions === undefined) {
    // Not fetched yet. Kick it off once and render nothing this pass —
    // `_loadVersionHistory` calls `requestUpdate` when it lands, exactly as
    // `previewWrite` does for the pending card. The guard is what keeps a
    // render that returns null from scheduling another fetch forever.
    if (!host._savedDiffRequested) host._savedDiffRequested = new Set();
    if (!host._savedDiffRequested.has(automationId)) {
      host._savedDiffRequested.add(automationId);
      host._loadVersionHistory(automationId);
    }
    return null;
  }
  // Newest first, as `_loadVersionHistory` reverses them. One version means a
  // create with nothing before it — no diff rather than a diff against
  // nothing.
  if (!Array.isArray(versions) || versions.length < 2) return null;
  const after = versions[0]?.yaml || "";
  const before = versions[1]?.yaml || "";
  if (!after || !before) return null;

  if (!host._savedDiffCache) host._savedDiffCache = new Map();
  const cached = host._savedDiffCache.get(msgIndex);
  if (cached && cached.before === before && cached.after === after) {
    return cached.diff;
  }
  // OLDER first: `diffLines(before, after)`, so what the new version added
  // reads as an addition.
  const diff = diffLines(before, after);
  host._savedDiffCache.set(msgIndex, { before, after, diff });
  return diff;
}

export function proposalDiff(host, msgIndex) {
  const msg = (host._messages || [])[msgIndex];
  if (!msg?.automation) return null;
  const yamlKey = `proposal_${msgIndex}`;
  const edited = host._editedYaml?.[yamlKey];
  // What the user would actually submit: unsaved editor edits included.
  const after = edited === undefined ? msg.automation_yaml || "" : edited;
  if (!after) return null;

  // Which automation the save will write is the accept path's decision, not
  // this module's: _acceptAutomationWithEdits branches on this same resolver.
  // Nothing to compare when it creates one instead.
  const targetId = _getRefiningAutomationId.call(host, msgIndex);
  if (!targetId) return null;

  // ...and so is how it treats the enabled state. The accept path sends
  // preserve_enabled_state: false exactly when the user's edit touched
  // initial_state, which makes their value authoritative instead of the boot
  // override on disk. Previewing the other call would describe a different
  // write and could omit an enable/disable that acceptance performs.
  const generated = msg.automation_yaml || "";
  const proposalBaseline = host._originalYaml?.[yamlKey] ?? generated;
  const stateEdited =
    !!edited &&
    edited !== proposalBaseline &&
    _initialStateEdited(proposalBaseline, edited);

  const preview = previewWrite(host, msgIndex, {
    targetId,
    yamlText: after,
    preserveEnabledState: !stateEdited,
  });
  if (preview.loading || !preview.current || !preview.proposed) return null;

  if (!host._proposalDiffCache) host._proposalDiffCache = new Map();
  const cached = host._proposalDiffCache.get(msgIndex);
  if (
    cached &&
    cached.before === preview.current &&
    cached.after === preview.proposed
  ) {
    return cached.diff;
  }

  const diff = diffLines(preview.current, preview.proposed);
  host._proposalDiffCache.set(msgIndex, {
    before: preview.current,
    after: preview.proposed,
    diff,
  });
  return diff;
}

// Every piece of diff state is keyed by message index, which means nothing
// once _messages belongs to a different conversation: a proposal landing at
// the same index would open with the previous session's panel state. Call this
// from every path that swaps _messages, not just the one that loads a session.
export function resetProposalDiffState(host) {
  host._proposalDiffOpen = {};
  host._proposalDiffExpanded = {};
  host._proposalDiffFull = {};
  host._proposalDiffCache = null;
  // Same reasoning as the caches above: keyed by message index, and a saved
  // card at the same index in another conversation is a different automation.
  host._savedDiffCache = null;
  host._savedDiffRequested = null;
  // Drop scheduled previews with the cache that owns them, or a keystroke from
  // the previous conversation lands a request after the switch.
  for (const entry of host._previewCache?.values() || []) {
    if (entry.timer) clearTimeout(entry.timer);
  }
  host._previewCache = null;
}

// How long to keep following a panel as it grows. Comfortably covers
// ha-code-editor mounting CodeMirror; the observer usually stops firing well
// before this.
const REVEAL_SETTLE_MS = 800;

// Bring what a footer link just opened into view. The panels unfold below the
// links at the bottom of the card, so on a tall card the click otherwise looks
// like it did nothing until you scroll after it.
//
// One scroll is not enough. ha-code-editor mounts CodeMirror asynchronously,
// so at updateComplete the panel is an empty box a few pixels tall: scrolling
// then concludes it is already in view, and the editor afterwards unfolds
// several hundred pixels below the fold. So follow it while it grows, and stop
// once it settles.
//
// `nearest` scrolls the least amount that works — an already-visible panel
// does not move, and one taller than the viewport lands top-first rather than
// jumping to its end — which is also what makes it safe to call repeatedly.
export async function revealPanel(host, selector) {
  await host.updateComplete;
  const panel = host.shadowRoot?.querySelector(selector);
  if (!panel) return;

  const bring = () =>
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  bring();

  if (typeof ResizeObserver === "undefined") return;
  const observer = new ResizeObserver(bring);
  observer.observe(panel);
  setTimeout(() => observer.disconnect(), REVEAL_SETTLE_MS);
}

export function toggleProposalDiff(host, msgIndex) {
  const opening = !(host._proposalDiffOpen || {})[msgIndex];
  host._proposalDiffOpen = {
    ...(host._proposalDiffOpen || {}),
    [msgIndex]: opening,
  };
  // Someone opening the panel is about to read it closely, and the automation
  // may have been edited elsewhere since the last look.
  // requestUpdate before revealPanel: it waits on updateComplete, which
  // resolves straight away when no render is pending yet and would then look
  // for a panel that does not exist.
  if (opening) invalidateProposalPreviews(host, msgIndex);
  host.requestUpdate();
  if (opening) revealPanel(host, `[data-diff-panel="${msgIndex}"]`);
}

// Reveal one collapsed run. Keyed by the run's first line index, which stays
// valid for as long as the diff itself does — a changed proposal recomputes the
// diff, and stale indices simply match nothing.
function expandGap(host, msgIndex, start) {
  const current = (host._proposalDiffExpanded || {})[msgIndex] || [];
  host._proposalDiffExpanded = {
    ...(host._proposalDiffExpanded || {}),
    [msgIndex]: [...current, start],
  };
  host.requestUpdate();
}

// Whole-document view. Turning it back off also drops the per-gap expansions,
// so "changes only" means exactly that rather than whatever was opened before.
function toggleFullDiff(host, msgIndex) {
  const full = !(host._proposalDiffFull || {})[msgIndex];
  host._proposalDiffFull = {
    ...(host._proposalDiffFull || {}),
    [msgIndex]: full,
  };
  if (!full) {
    host._proposalDiffExpanded = {
      ...(host._proposalDiffExpanded || {}),
      [msgIndex]: [],
    };
  }
  host.requestUpdate();
}

// The chip that sits next to "Edit YAML". Renders nothing when there is no
// previous version to compare against — a first-time proposal has none.
export function renderProposalDiffToggle(host, msgIndex, diff) {
  if (!diff) return "";
  const open = !!(host._proposalDiffOpen || {})[msgIndex];
  const changed = diff.added > 0 || diff.removed > 0;
  return html`
    <button
      type="button"
      class="subcard-action-link ${open ? "active" : ""}"
      @click=${() => toggleProposalDiff(host, msgIndex)}
    >
      <ha-icon icon="mdi:file-compare"></ha-icon>
      ${
        open
          ? host._t("automations_diff_toggle_hide", "Hide changes")
          : host._t("automations_diff_toggle_view", "View changes")
      }
      ${
        changed
          ? html`<span class="diff-stat-inline">
              <span class="diff-stat add">+${diff.added}</span>
              <span class="diff-stat del">−${diff.removed}</span>
            </span>`
          : ""
      }
    </button>
  `;
}

export function renderProposalDiffPanel(host, msgIndex, diff) {
  if (!diff) return "";
  if (!(host._proposalDiffOpen || {})[msgIndex]) return "";

  if (diff.added === 0 && diff.removed === 0) {
    return html`<div class="proposal-diff" data-diff-panel=${msgIndex}>
      <div class="proposal-diff-empty">
        ${host._t(
          "automations_diff_identical",
          "Identical to the previous version — nothing changed.",
        )}
      </div>
    </div>`;
  }

  const full = !!(host._proposalDiffFull || {})[msgIndex];
  const expanded = (host._proposalDiffExpanded || {})[msgIndex] || [];
  const collapsed = collapseDiff(diff.lines, DEFAULT_CONTEXT_RADIUS, expanded);
  const entries = full ? diff.lines : collapsed;
  // Nothing left to unfold means the whole-file switch would do nothing —
  // except when it is the thing currently holding the document open.
  const foldable = full || collapsed.some((e) => e.type === "gap");

  return html`
    <div class="proposal-diff" data-diff-panel=${msgIndex}>
      <div class="proposal-diff-head">
        <span class="proposal-diff-legend">
          ${host._t("automations_diff_legend_previous", "Previous")}
          <ha-icon
            icon="mdi:arrow-right"
            style="--mdc-icon-size:13px;"
          ></ha-icon>
          ${host._t("automations_diff_legend_proposed", "Proposed")}
        </span>
        <span class="proposal-diff-head-right">
          ${
            foldable
              ? html`<button
                  type="button"
                  class="proposal-diff-expand-all"
                  @click=${() => toggleFullDiff(host, msgIndex)}
                >
                  <ha-icon
                    icon=${
                      full
                        ? "mdi:unfold-less-horizontal"
                        : "mdi:unfold-more-horizontal"
                    }
                    style="--mdc-icon-size:13px;"
                  ></ha-icon>
                  ${
                    full
                      ? host._t("automations_diff_changes_only", "Changes only")
                      : host._t("automations_diff_show_all", "Whole file")
                  }
                </button>`
              : ""
          }
          <span class="diff-stat-inline">
            <span class="diff-stat add">+${diff.added}</span>
            <span class="diff-stat del">−${diff.removed}</span>
          </span>
        </span>
      </div>
      <div class="proposal-diff-body">
        ${entries.map((entry) => renderDiffEntry(host, msgIndex, entry))}
      </div>
    </div>
  `;
}

function renderDiffEntry(host, msgIndex, entry) {
  if (entry.type === "gap") {
    // The key path names where the change below it sits — a bare
    // `above: 18 → 19` says nothing until you know which condition owns it.
    const path = entry.path || [];
    return html`<button
      type="button"
      class="proposal-diff-gap"
      title=${host._t("automations_diff_expand_tooltip", "Show these lines")}
      @click=${() => expandGap(host, msgIndex, entry.start)}
    >
      <ha-icon
        icon="mdi:unfold-more-horizontal"
        style="--mdc-icon-size:13px;"
      ></ha-icon>
      <span
        >${host
          ._t("automations_diff_unchanged_lines", "{count} unchanged lines")
          .replace("{count}", String(entry.count))}</span
      >
      ${
        path.length
          ? html`<span class="proposal-diff-gap-path"
              >${path.join(" › ")}</span
            >`
          : ""
      }
    </button>`;
  }
  // The +/− gutter is drawn by CSS (::before), not markup: generated content
  // is outside the DOM text, so selecting the diff copies the YAML alone. As a
  // real element it came along on every copy — and, being its own flex item,
  // landed on a line of its own. The line stays a flex container so the
  // whitespace this template puts around the span is dropped rather than
  // copied with it.
  return html`<div class="proposal-diff-line ${entry.type}">
    <span class="proposal-diff-text">${entry.text || " "}</span>
  </div>`;
}
