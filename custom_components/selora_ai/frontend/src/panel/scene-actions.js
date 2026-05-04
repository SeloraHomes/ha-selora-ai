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
    this._messages = [...this._messages];
    await this._loadScenes();

    this._showToast(`Scene "${scene.name}" created and saved.`, "success");
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

  const name = scene ? scene.name : "the scene";
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
      this._activeTab = "chat";
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
