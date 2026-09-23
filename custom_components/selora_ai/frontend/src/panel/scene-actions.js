import { interpolate } from "../shared/i18n.js";

// Scene proposal actions (prototype-assigned to SeloraAIArchitectPanel)

// The backend stores its own index into the (possibly pruned) session
// messages array. Streaming events attach it as msg.scene_message_index so
// scene actions stay correct even when the local array has more entries
// than the stored session. After a reload the local array IS the stored
// array, so msgIndex is also correct — fall back to it then.
function _storedSceneIndex(msg, msgIndex) {
  return msg && msg.scene_message_index != null
    ? msg.scene_message_index
    : msgIndex;
}

export async function _acceptScene(msgIndex) {
  const msg = this._messages[msgIndex] || {};
  const scene = msg.scene;
  if (!scene) return;

  try {
    // refine_scene_id is read server-side from the stored proposal so a
    // stale or crafted client cannot retarget the accept onto an unrelated
    // Selora scene.
    const result = await this.hass.callWS({
      type: "selora_ai/accept_scene",
      session_id: this._activeSessionId,
      message_index: _storedSceneIndex(msg, msgIndex),
    });

    msg.scene_status = "saved";
    msg.scene_id = result.scene_id;
    // HA may resolve the entity_id to a slug or collision-suffixed form
    // rather than scene.<scene_id>, so keep the resolved value the
    // backend returned for use when the user clicks Activate.
    msg.entity_id = result.entity_id;
    this._markJustCreated(result.scene_id);
    this._messages = [...this._messages];
    await this._loadScenes();
    this._markSceneCreated(result.scene_id);

    // A refinement rewrites the scene this session already saved — telling the
    // user it was created is wrong for the commonest one of those, a rename.
    this._showToast(
      interpolate(
        result.replaced
          ? this._t("scene_actions_updated", 'Scene "{name}" updated.')
          : this._t(
              "scene_actions_created",
              'Scene "{name}" created and saved.',
            ),
        { name: scene.name },
      ),
      "success",
    );

    // The scene was half the request — "create a scene AND add it to the
    // dashboard". The turn that proposed it ended at the card, and the scene
    // entity did not exist until just now, so this is the first moment the
    // rest can be done. The server refuses if the scene was not saved or
    // nothing was declared, so the panel does not decide any of that.
    // Not gated on a declared remainder any more: the model announces the
    // follow-up in prose and leaves the field unset, so the server replays the
    // original request instead. It decides whether anything is outstanding —
    // the panel only says the scene now exists.
    if (result.scene_id) {
      await this._sendMessage?.({ resumeProposalId: result.scene_id });
    }
  } catch (err) {
    this._showToast("Failed to create scene: " + err.message, "error");
  }
}

export async function _declineScene(msgIndex) {
  const msg = this._messages[msgIndex] || {};
  try {
    await this.hass.callWS({
      type: "selora_ai/set_scene_status",
      session_id: this._activeSessionId,
      message_index: _storedSceneIndex(msg, msgIndex),
      status: "declined",
    });
    const session = await this.hass.callWS({
      type: "selora_ai/get_session",
      session_id: this._activeSessionId,
    });
    this._messages = session.messages || [];
  } catch (err) {
    console.error("Failed to decline scene", err);
  }
}

export async function _refineScene(msgIndex) {
  const msg = this._messages[msgIndex] || {};
  const scene = msg.scene;

  try {
    await this.hass.callWS({
      type: "selora_ai/set_scene_status",
      session_id: this._activeSessionId,
      message_index: _storedSceneIndex(msg, msgIndex),
      status: "refining",
    });
    const session = await this.hass.callWS({
      type: "selora_ai/get_session",
      session_id: this._activeSessionId,
    });
    this._messages = session.messages || [];
  } catch (err) {
    console.error("Failed to mark scene as refining", err);
  }

  const name = scene
    ? scene.name
    : this._t("scene_actions_refine_default_name", "the scene");
  this._input = `Refine "${name}": `;
  this.shadowRoot.querySelector(".composer-textarea")?.focus();
}

export async function _loadSceneToChat(sceneId) {
  if (!sceneId) return;
  this._loadingToChat = { ...this._loadingToChat, [sceneId]: true };
  try {
    const result = await this.hass.callWS({
      type: "selora_ai/load_scene_to_session",
      scene_id: sceneId,
    });
    const sessionId = result?.session_id;
    if (sessionId) {
      this._activeSessionId = sessionId;
      this._input = "";
      this._setActiveTab("chat");
      this._showSidebar = false;
      await this._openSession(sessionId);
    }
  } catch (err) {
    console.error("Failed to load scene to chat", err);
    this._showToast("Failed to load scene into chat: " + err.message, "error");
  } finally {
    this._loadingToChat = { ...this._loadingToChat, [sceneId]: false };
  }
  this.requestUpdate();
}

// -------------------------------------------------------------------------
// Rename a saved scene
// -------------------------------------------------------------------------

export function _startRenameScene(sceneId, currentName) {
  this._editingSceneName = sceneId;
  this._editingSceneNameValue = currentName || "";
  this._openSceneBurger = null;
  this.requestUpdate();
  // Focus the input once it has rendered.
  this.updateComplete.then(() => {
    const input = this.shadowRoot.querySelector(
      `.rename-input[data-scene-id="${sceneId}"]`,
    );
    if (input) {
      input.focus();
      input.select();
    }
  });
}

export async function _saveRenameScene(sceneId) {
  const name = (this._editingSceneNameValue || "").trim();
  // An empty field is a cancel, not a rename: the backend refuses the name
  // anyway, and a toast about it reads as a failure the user did not cause.
  if (!name) {
    this._cancelRenameScene();
    return;
  }
  try {
    await this.hass.callWS({
      type: "selora_ai/rename_scene",
      scene_id: sceneId,
      name,
    });
    this._cancelRenameScene();
    this._showToast(
      this._t("scene_actions_renamed", "Scene renamed"),
      "success",
    );
    // The row's name comes from the scene list, so it is the reload that
    // shows the new one.
    await this._loadScenes();
  } catch (err) {
    console.error("Failed to rename scene", err);
    this._showToast("Failed to rename: " + err.message, "error");
  }
}

export function _cancelRenameScene() {
  this._editingSceneName = null;
  this._editingSceneNameValue = "";
}
