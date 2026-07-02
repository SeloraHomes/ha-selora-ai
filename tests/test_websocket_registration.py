"""Smoke test: every Selora AI websocket command registers.

Guards the websocket extraction — asserts the full set of ``selora_ai/*``
command types is registered by ``async_register_websocket_commands`` so a
handler that fails to import, gets dropped, or is renamed during the
extraction is caught immediately.
"""

from __future__ import annotations

from homeassistant.components import websocket_api
from homeassistant.setup import async_setup_component

from custom_components.selora_ai.websocket import async_register_websocket_commands

# Snapshot of the command types owned by the integration. Update
# deliberately when adding/removing a websocket command.
EXPECTED_COMMANDS = frozenset(
    {
        "selora_ai/accept_scene",
        "selora_ai/accept_suggestion_with_edits",
        "selora_ai/activate_scene",
        "selora_ai/apply_automation_yaml",
        "selora_ai/apply_exclude_label",
        "selora_ai/apply_scene_states",
        "selora_ai/chat",
        "selora_ai/chat_stream",
        "selora_ai/create_automation",
        "selora_ai/create_draft",
        "selora_ai/create_mcp_token",
        "selora_ai/delete_automation",
        "selora_ai/delete_scene",
        "selora_ai/delete_session",
        "selora_ai/exchange_aigateway_code",
        "selora_ai/exchange_connect_code",
        "selora_ai/generate_suggestions",
        "selora_ai/get_analytics",
        "selora_ai/get_automation_diff",
        "selora_ai/get_automation_lineage",
        "selora_ai/get_automation_versions",
        "selora_ai/get_automations",
        "selora_ai/get_config",
        "selora_ai/get_device_detail",
        "selora_ai/get_drafts",
        "selora_ai/get_pattern_detail",
        "selora_ai/get_patterns",
        "selora_ai/get_proactive_suggestions",
        "selora_ai/get_scenes",
        "selora_ai/get_session",
        "selora_ai/get_session_automations",
        "selora_ai/get_sessions",
        "selora_ai/get_state_history_summary",
        "selora_ai/get_suggestion_detail",
        "selora_ai/get_suggestions",
        "selora_ai/list_approvals",
        "selora_ai/list_mcp_tokens",
        "selora_ai/load_automation_to_session",
        "selora_ai/load_scene_to_session",
        "selora_ai/new_session",
        "selora_ai/quick_create_automation",
        "selora_ai/record_chat_feedback",
        "selora_ai/remove_draft",
        "selora_ai/remove_exclude_label",
        "selora_ai/rename_automation",
        "selora_ai/rename_session",
        "selora_ai/resolve_approval",
        "selora_ai/revoke_approval",
        "selora_ai/revoke_mcp_token",
        "selora_ai/save_scene_edits",
        "selora_ai/set_automation_status",
        "selora_ai/set_scene_status",
        "selora_ai/toggle_automation",
        "selora_ai/trigger_pattern_scan",
        "selora_ai/unlink_aigateway",
        "selora_ai/unlink_connect",
        "selora_ai/update_automation_yaml",
        "selora_ai/update_config",
        "selora_ai/update_pattern_status",
        "selora_ai/update_proactive_suggestion",
        "selora_ai/usage/breakdown",
        "selora_ai/usage/pricing_defaults",
        "selora_ai/usage/recent",
        "selora_ai/usage/totals",
        "selora_ai/validate_llm_key",
    }
)


async def test_all_websocket_commands_register(hass) -> None:
    """Registering wires up exactly the expected selora_ai/* commands."""
    # oauth_link registration installs an HTTP view, so http must exist.
    await async_setup_component(hass, "http", {})
    async_register_websocket_commands(hass)

    handlers = hass.data[websocket_api.const.DOMAIN]
    registered = {cmd for cmd in handlers if cmd.startswith("selora_ai/")}

    missing = EXPECTED_COMMANDS - registered
    assert not missing, f"commands failed to register: {sorted(missing)}"
