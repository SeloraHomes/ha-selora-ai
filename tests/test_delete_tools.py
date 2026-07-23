"""Tests for the delete_automation / delete_scene chat tools + confirmation card."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai.llm_client.command_policy import (
    _delete_approval_quick_actions,
    _pending_deletes_from_log,
    synthesize_approval_from_tool_log,
)
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import (
    CHAT_TOOLS,
    COMMAND_TOOL_NAMES,
    TOOL_DELETE_AUTOMATION,
    TOOL_DELETE_SCENE,
    TOOL_MAP,
)


def _make_executor(hass: HomeAssistant, *, is_admin: bool = False) -> ToolExecutor:
    return ToolExecutor(hass, MagicMock(), is_admin=is_admin)


# ── ToolRegistry ────────────────────────────────────────────────────


class TestDeleteToolRegistry:
    def test_in_chat_tools(self) -> None:
        assert TOOL_DELETE_AUTOMATION in CHAT_TOOLS
        assert TOOL_DELETE_SCENE in CHAT_TOOLS

    def test_in_tool_map(self) -> None:
        assert "delete_automation" in TOOL_MAP
        assert "delete_scene" in TOOL_MAP

    def test_require_admin(self) -> None:
        assert TOOL_DELETE_AUTOMATION.requires_admin
        assert TOOL_DELETE_SCENE.requires_admin

    def test_targets_optional(self) -> None:
        # Either id or entity_id may be given; neither is individually required.
        for tool in (TOOL_DELETE_AUTOMATION, TOOL_DELETE_SCENE):
            assert all(not p.required for p in tool.params)

    def test_in_command_tool_names(self) -> None:
        # "get rid of the Movie Night scene" classifies as a command intent,
        # so the trimmed command schema must still expose the delete tools.
        assert "delete_automation" in COMMAND_TOOL_NAMES
        assert "delete_scene" in COMMAND_TOOL_NAMES


# ── ToolExecutor gating + delegation ────────────────────────────────


class TestDeleteHandlers:
    @pytest.mark.asyncio
    async def test_non_admin_blocked(self, hass: HomeAssistant) -> None:
        executor = _make_executor(hass, is_admin=False)
        result = await executor.execute("delete_automation", {"entity_id": "automation.x"})
        assert "requires admin" in result["error"]

    @pytest.mark.asyncio
    async def test_delete_automation_delegates_to_preview(self, hass: HomeAssistant) -> None:
        executor = _make_executor(hass, is_admin=True)
        preview = {
            "requires_approval": True,
            "delete": {
                "kind": "automation",
                "target_id": "abc",
                "entity_id": "automation.evening_lights",
                "label": "[Selora AI] Evening Lights",
            },
        }
        with patch(
            "custom_components.selora_ai.mcp_server._preview_delete_automation",
            new_callable=AsyncMock,
            return_value=preview,
        ) as mock_preview:
            result = await executor.execute(
                "delete_automation", {"entity_id": "automation.evening_lights"}
            )
        mock_preview.assert_awaited_once()
        assert result["requires_approval"] is True
        assert result["delete"]["kind"] == "automation"

    @pytest.mark.asyncio
    async def test_delete_scene_delegates_to_preview(self, hass: HomeAssistant) -> None:
        executor = _make_executor(hass, is_admin=True)
        preview = {
            "requires_approval": True,
            "delete": {
                "kind": "scene",
                "target_id": "sc1",
                "entity_id": "scene.movie_night",
                "label": "Movie Night",
            },
        }
        with patch(
            "custom_components.selora_ai.mcp_server._preview_delete_scene",
            new_callable=AsyncMock,
            return_value=preview,
        ):
            result = await executor.execute("delete_scene", {"entity_id": "scene.movie_night"})
        assert result["delete"]["kind"] == "scene"


# ── Synthesizer: tool_log → delete-confirmation card ────────────────


def _delete_log(
    kind: str,
    entity_id: str,
    label: str,
    *,
    alias: str = "",
    name: str = "",
) -> list[dict]:
    return [
        {
            "tool": f"delete_{kind}",
            "arguments": {"entity_id": entity_id},
            "result": {
                "requires_approval": True,
                "delete": {
                    "kind": kind,
                    "target_id": "tid",
                    "entity_id": entity_id,
                    "alias": alias,
                    "name": name,
                    "label": label,
                },
            },
        }
    ]


class TestPendingDeletesFromLog:
    def test_extracts_descriptor(self) -> None:
        deletes = _pending_deletes_from_log(
            _delete_log("automation", "automation.x", "X", alias="X Alias")
        )
        assert deletes == [
            {
                "kind": "automation",
                "target_id": "tid",
                "entity_id": "automation.x",
                # Identity fingerprints carried through verbatim.
                "alias": "X Alias",
                "name": "",
                "label": "X",
            }
        ]

    def test_carries_scene_name_fingerprint(self) -> None:
        deletes = _pending_deletes_from_log(
            _delete_log("scene", "scene.y", "Y", name="Movie Night")
        )
        assert deletes[0]["name"] == "Movie Night"

    def test_dedups_repeated_target(self) -> None:
        log = _delete_log("scene", "scene.y", "Y") + _delete_log("scene", "scene.y", "Y")
        assert len(_pending_deletes_from_log(log)) == 1

    def test_ignores_non_delete_and_unconfirmed(self) -> None:
        log = [
            {"tool": "get_home_snapshot", "arguments": {}, "result": {}},
            {
                "tool": "delete_scene",
                "arguments": {},
                "result": {"error": "not found"},  # no requires_approval
            },
        ]
        assert _pending_deletes_from_log(log) == []


class TestSynthesizeDeleteCard:
    def test_builds_command_approval_delete(self) -> None:
        base = {"intent": "answer", "response": "hint"}
        out = synthesize_approval_from_tool_log(
            base,
            _delete_log("automation", "automation.evening", "Evening"),
            hass=None,
            language="en",
        )
        assert out["intent"] == "command_approval"
        approval = out["command_approval"]
        assert approval["approval_kind"] == "delete"
        assert approval["calls"] == []
        assert approval["deletes"][0]["entity_id"] == "automation.evening"
        assert approval["proposal_id"]
        # Two buttons routed via approve: sentinels (delete + cancel).
        values = [a["value"] for a in out["quick_actions"]]
        pid = approval["proposal_id"]
        assert values == [f"approve:delete:{pid}", f"approve:cancel:{pid}"]

    def test_alias_fingerprint_survives_synthesis(self) -> None:
        """The id-less identity fingerprint must reach the persisted proposal —
        without it the confirm-time revalidation rejects every id-less delete."""
        out = synthesize_approval_from_tool_log(
            {"intent": "answer", "response": "hint"},
            _delete_log("automation", "automation.x", "X", alias="Nightly"),
            hass=None,
            language="en",
        )
        assert out["command_approval"]["deletes"][0]["alias"] == "Nightly"

    def test_explicit_command_intent_not_hijacked(self) -> None:
        # A real service-call command must pass through untouched.
        base = {"intent": "command", "calls": [{"service": "light.turn_on"}]}
        out = synthesize_approval_from_tool_log(
            base, _delete_log("scene", "scene.z", "Z"), hass=None, language="en"
        )
        assert out["intent"] == "command"

    def test_service_call_approval_wins_over_delete(self) -> None:
        # When a delete tool AND a review-level execute_command both return
        # requires_approval in the same round, the service-call approval must
        # not be silently dropped in favour of the delete card.
        log = _delete_log("scene", "scene.z", "Z") + [
            {
                "tool": "execute_command",
                "arguments": {"service": "lock.unlock", "entity_id": "lock.front"},
                "result": {
                    "requires_approval": True,
                    "service": "lock.unlock",
                    "risk_level": "high",
                    "approval_reason": "Unlocks a physical lock",
                },
            }
        ]
        out = synthesize_approval_from_tool_log(
            {"intent": "answer", "response": "hint"}, log, hass=None, language="en"
        )
        assert out["intent"] == "command_approval"
        approval = out["command_approval"]
        # Service-call proposal, not the delete card.
        assert approval.get("approval_kind") != "delete"
        assert approval.get("calls")
        assert approval["calls"][0]["service"] == "lock.unlock"


# ── Preview resolver: unloaded YAML entry by id ─────────────────────


class TestPreviewDeleteAutomation:
    @pytest.mark.asyncio
    async def test_unloaded_yaml_entry_by_id_preserves_target(self, hass: HomeAssistant) -> None:
        """A YAML automation that isn't loaded as an entity can still be
        previewed (and thus deleted) by its id — the caller-provided id must
        survive _resolve_automation returning empty for the unloaded entry."""
        from pathlib import Path

        from custom_components.selora_ai.automation_utils import _write_automations_yaml
        from custom_components.selora_ai.mcp_server import _preview_delete_automation

        path = Path(hass.config.config_dir) / "automations.yaml"
        await hass.async_add_executor_job(
            _write_automations_yaml,
            path,
            [{"id": "auto99", "alias": "Nightly Cleanup", "trigger": [], "action": []}],
        )
        # No automation.* state registered → the entry is "unloaded".
        result = await _preview_delete_automation(hass, {"automation_id": "auto99"})

        assert result["requires_approval"] is True
        assert result["delete"]["kind"] == "automation"
        assert result["delete"]["target_id"] == "auto99"
        assert result["delete"]["label"] == "Nightly Cleanup"

    @pytest.mark.asyncio
    async def test_idless_entry_clears_stale_automation_id(self, hass: HomeAssistant) -> None:
        """When both ids are supplied but entity_id resolves to an id-less yaml
        entry, the stale caller automation_id must be cleared so confirm uses
        the entity + alias fingerprint path (not a not-found id lookup)."""
        from pathlib import Path

        from custom_components.selora_ai.automation_utils import _write_automations_yaml
        from custom_components.selora_ai.mcp_server import _preview_delete_automation

        # An id-less yaml automation, loaded as an entity with no `id` attr.
        await hass.async_add_executor_job(
            _write_automations_yaml,
            Path(hass.config.config_dir) / "automations.yaml",
            [{"alias": "Bedtime", "trigger": [], "action": []}],
        )
        hass.states.async_set("automation.bedtime", "on", {"friendly_name": "Bedtime"})

        result = await _preview_delete_automation(
            hass, {"automation_id": "stale-id-123", "entity_id": "automation.bedtime"}
        )

        assert result["requires_approval"] is True
        assert result["delete"]["target_id"] == ""  # stale id cleared
        assert result["delete"]["entity_id"] == "automation.bedtime"
        assert result["delete"]["alias"] == "Bedtime"


class TestPreviewDeleteScene:
    @pytest.mark.asyncio
    async def test_storage_managed_scene_refused_before_card(self, hass: HomeAssistant) -> None:
        """A storage/UI-managed scene (live state, no SceneStore record, no
        scenes.yaml entry) must be refused during preview — offering a Delete
        button would surface a card that _tool_delete_scene rejects at confirm
        time."""
        from pathlib import Path

        from custom_components.selora_ai.mcp_server import _preview_delete_scene
        from custom_components.selora_ai.scene_utils import _write_scenes_yaml

        # Empty scenes.yaml → no yaml entry maps to the entity.
        await hass.async_add_executor_job(
            _write_scenes_yaml, Path(hass.config.config_dir) / "scenes.yaml", []
        )
        hass.states.async_set("scene.ui_made", "scening", {"friendly_name": "UI Made"})

        result = await _preview_delete_scene(hass, {"entity_id": "scene.ui_made"})

        assert "requires_approval" not in result
        assert "error" in result
        assert "not yaml-managed" in result["error"]


# ── Confirm resolution: failure handling ────────────────────────────


class TestResolveDeleteApproval:
    @pytest.mark.asyncio
    async def test_total_failure_sends_error_not_approved(self, hass: HomeAssistant) -> None:
        """If every delete fails, the card must NOT be marked approved
        (which the frontend renders as a terminal "Deleted"); send_error keeps
        it pending + retryable."""
        from custom_components.selora_ai import _resolve_delete_approval

        store = MagicMock()
        store.set_approval_status = AsyncMock()
        store.append_message = AsyncMock(return_value={"role": "assistant"})
        connection = MagicMock()
        approval = {
            "approval_kind": "delete",
            "deletes": [
                {
                    "kind": "automation",
                    "target_id": "x",
                    "entity_id": "automation.x",
                    "label": "X",
                }
            ],
        }
        with patch(
            "custom_components.selora_ai.mcp_server._tool_delete_automation",
            new_callable=AsyncMock,
            return_value={"error": "reload failed"},
        ):
            await _resolve_delete_approval(
                hass,
                connection,
                {"id": 1},
                store,
                "sess",
                0,
                approval,
                "delete",
                language="en",
            )

        connection.send_error.assert_called_once()
        connection.send_result.assert_not_called()
        store.set_approval_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_automation_deleted_by_stable_id_not_entity(self, hass: HomeAssistant) -> None:
        """Confirm must delete by the stable automation_id shown on the card,
        never by the mutable entity_id (which could have been remapped to a
        different automation between render and click)."""
        from custom_components.selora_ai import _resolve_delete_approval

        store = MagicMock()
        store.set_approval_status = AsyncMock()
        store.append_message = AsyncMock(return_value={"role": "assistant"})
        connection = MagicMock()
        approval = {
            "approval_kind": "delete",
            "deletes": [
                {
                    "kind": "automation",
                    "target_id": "auto42",
                    "entity_id": "automation.evening_lights",
                    "label": "Evening Lights",
                }
            ],
        }
        with patch(
            "custom_components.selora_ai.mcp_server._tool_delete_automation",
            new_callable=AsyncMock,
            return_value={"automation_id": "auto42", "status": "deleted"},
        ) as mock_del:
            await _resolve_delete_approval(
                hass,
                connection,
                {"id": 1},
                store,
                "sess",
                0,
                approval,
                "delete",
                language="en",
            )
        # Called with the stable id ONLY — entity_id must not be forwarded.
        mock_del.assert_awaited_once_with(hass, {"automation_id": "auto42"})

    @pytest.mark.asyncio
    async def test_success_marks_approved(self, hass: HomeAssistant) -> None:
        from custom_components.selora_ai import _resolve_delete_approval

        store = MagicMock()
        store.set_approval_status = AsyncMock()
        store.append_message = AsyncMock(return_value={"role": "assistant"})
        connection = MagicMock()
        approval = {
            "approval_kind": "delete",
            "deletes": [
                {
                    "kind": "scene",
                    "target_id": "sc1",
                    "entity_id": "scene.movie",
                    "label": "Movie",
                }
            ],
        }
        with patch(
            "custom_components.selora_ai.mcp_server._tool_delete_scene",
            new_callable=AsyncMock,
            return_value={"scene_id": "sc1", "status": "deleted"},
        ):
            await _resolve_delete_approval(
                hass,
                connection,
                {"id": 1},
                store,
                "sess",
                0,
                approval,
                "delete",
                language="en",
            )

        store.set_approval_status.assert_awaited_once_with("sess", 0, "approved")
        connection.send_result.assert_called_once()
        connection.send_error.assert_not_called()

    @pytest.mark.asyncio
    async def test_idless_automation_confirm_enforces_alias_fingerprint(
        self, hass: HomeAssistant
    ) -> None:
        """The id-less automation confirm hands the alias fingerprint to the
        backend (which enforces it atomically) — never the mutable entity_id."""
        from custom_components.selora_ai import _resolve_delete_approval

        store = MagicMock()
        store.set_approval_status = AsyncMock()
        store.append_message = AsyncMock(return_value={"role": "assistant"})
        connection = MagicMock()
        approval = {
            "approval_kind": "delete",
            "deletes": [
                {
                    "kind": "automation",
                    "target_id": "",  # id-less
                    "entity_id": "automation.evening",
                    "alias": "Evening Routine",
                    "label": "Evening Routine",
                }
            ],
        }
        with patch(
            "custom_components.selora_ai.mcp_server._tool_delete_automation",
            new_callable=AsyncMock,
            return_value={"status": "deleted"},
        ) as mock_del:
            await _resolve_delete_approval(
                hass,
                connection,
                {"id": 1},
                store,
                "sess",
                0,
                approval,
                "delete",
                language="en",
            )
        mock_del.assert_awaited_once_with(hass, {"expected_alias": "Evening Routine"})

    @pytest.mark.asyncio
    async def test_idless_automation_missing_fingerprint_skips(self, hass: HomeAssistant) -> None:
        """No alias fingerprint → refuse without touching the backend."""
        from custom_components.selora_ai import _resolve_delete_approval

        store = MagicMock()
        store.set_approval_status = AsyncMock()
        store.append_message = AsyncMock(return_value={"role": "assistant"})
        connection = MagicMock()
        approval = {
            "approval_kind": "delete",
            "deletes": [
                {"kind": "automation", "target_id": "", "entity_id": "automation.x", "label": "X"}
            ],
        }
        with patch(
            "custom_components.selora_ai.mcp_server._tool_delete_automation",
            new_callable=AsyncMock,
        ) as mock_del:
            await _resolve_delete_approval(
                hass,
                connection,
                {"id": 1},
                store,
                "sess",
                0,
                approval,
                "delete",
                language="en",
            )
        mock_del.assert_not_awaited()
        connection.send_error.assert_called_once()
        store.set_approval_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_idless_scene_confirm_enforces_name_fingerprint(
        self, hass: HomeAssistant
    ) -> None:
        """The id-less scene confirm hands the name fingerprint to the backend."""
        from custom_components.selora_ai import _resolve_delete_approval

        store = MagicMock()
        store.set_approval_status = AsyncMock()
        store.append_message = AsyncMock(return_value={"role": "assistant"})
        connection = MagicMock()
        approval = {
            "approval_kind": "delete",
            "deletes": [
                {
                    "kind": "scene",
                    "target_id": "",
                    "entity_id": "scene.hand_authored",
                    "name": "Hand Authored",
                    "label": "Hand Authored",
                }
            ],
        }
        with patch(
            "custom_components.selora_ai.mcp_server._tool_delete_scene",
            new_callable=AsyncMock,
            return_value={"entity_id": "scene.hand_authored", "status": "deleted"},
        ) as mock_del:
            await _resolve_delete_approval(
                hass,
                connection,
                {"id": 1},
                store,
                "sess",
                0,
                approval,
                "delete",
                language="en",
            )
        mock_del.assert_awaited_once_with(
            hass, {"entity_id": "scene.hand_authored", "expected_name": "Hand Authored"}
        )


# ── Scope / approval_kind validation ────────────────────────────────


class TestScopeValidation:
    def _session(self, approval: dict) -> dict:
        return {
            "messages": [
                {
                    "intent": "command_approval",
                    "command_approval": approval,
                    "approval_status": "pending",
                }
            ]
        }

    @pytest.mark.asyncio
    async def test_cancel_scope_on_service_call_is_rejected(self, hass: HomeAssistant) -> None:
        """A `cancel` scope must not fall through to the allow path and execute
        a service-call approval."""
        from custom_components.selora_ai import _resolve_approval

        approval = {
            "proposal_id": "p1",
            "calls": [{"service": "lock.unlock", "target": {"entity_id": ["lock.front"]}}],
            "risk_level": "high",
        }
        store = MagicMock()
        store.get_session = AsyncMock(return_value=self._session(approval))
        store.set_approval_status = AsyncMock()
        connection = MagicMock()
        called: list = []

        async def _unlock(call: Any) -> None:
            called.append(call)

        hass.services.async_register("lock", "unlock", _unlock)

        await _resolve_approval(
            hass,
            connection,
            {"id": 1},
            store,
            MagicMock(),
            "sess",
            "p1",
            "cancel",
        )

        connection.send_error.assert_called_once()
        assert connection.send_error.call_args.args[1] == "invalid_scope"
        assert called == []  # the lock was never unlocked
        store.set_approval_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_allow_scope_on_delete_card_is_rejected(self, hass: HomeAssistant) -> None:
        """An `once` scope is not valid for a delete confirmation card."""
        from custom_components.selora_ai import _resolve_approval

        approval = {
            "proposal_id": "p2",
            "approval_kind": "delete",
            "deletes": [{"kind": "scene", "target_id": "s", "entity_id": "scene.x"}],
        }
        store = MagicMock()
        store.get_session = AsyncMock(return_value=self._session(approval))
        connection = MagicMock()

        await _resolve_approval(
            hass,
            connection,
            {"id": 1},
            store,
            MagicMock(),
            "sess",
            "p2",
            "once",
        )

        connection.send_error.assert_called_once()
        assert connection.send_error.call_args.args[1] == "invalid_scope"


# ── Executed-write acknowledgement in mixed rounds ──────────────────


class TestDeleteCardMixedRound:
    @pytest.mark.asyncio
    async def test_executed_write_acknowledged_alongside_delete(self, hass: HomeAssistant) -> None:
        """A safe write that already ran in the same round must be acknowledged
        alongside the delete-confirmation hint, not dropped."""
        from custom_components.selora_ai.llm_client.command_policy import (
            delete_pending_hint,
        )

        tool_log = [
            {
                "tool": "execute_command",
                "arguments": {"service": "light.turn_off", "entity_id": "light.kitchen"},
                "result": {
                    "executed": True,
                    "service": "light.turn_off",
                    "entity_ids": ["light.kitchen"],
                },
            },
            *_delete_log("scene", "scene.movie", "Movie Night"),
        ]
        out = synthesize_approval_from_tool_log(
            {"intent": "answer", "response": "x"}, tool_log, hass=hass, language="en"
        )
        assert out["intent"] == "command_approval"
        hint = delete_pending_hint("en")
        # The hint is present AND something precedes it (the executed-write ack).
        assert hint in out["response"]
        assert out["response"] != hint
        assert "\n\n" in out["response"]


class TestPreviewDeleteSceneStaleId:
    @pytest.mark.asyncio
    async def test_native_yaml_scene_clears_stale_scene_id(self, hass: HomeAssistant) -> None:
        """A native yaml scene given a stale/non-Selora scene_id must clear it
        so confirm uses the entity fallback instead of a not-found scene_id."""
        from pathlib import Path

        from custom_components.selora_ai.mcp_server import _preview_delete_scene
        from custom_components.selora_ai.scene_utils import _write_scenes_yaml

        await hass.async_add_executor_job(
            _write_scenes_yaml,
            Path(hass.config.config_dir) / "scenes.yaml",
            [{"name": "Hand Authored", "entities": {"light.x": {"state": "on"}}}],
        )
        hass.states.async_set("scene.hand_authored", "scening")

        result = await _preview_delete_scene(
            hass, {"entity_id": "scene.hand_authored", "scene_id": "stale-nonselora"}
        )

        assert result["requires_approval"] is True
        # Stale scene_id cleared → confirm falls back to entity_id, with the
        # yaml name captured as the revalidation fingerprint.
        assert result["delete"]["target_id"] == ""
        assert result["delete"]["entity_id"] == "scene.hand_authored"
        assert result["delete"]["name"] == "Hand Authored"

    @pytest.mark.asyncio
    async def test_native_yaml_scene_with_id_uses_stable_id(self, hass: HomeAssistant) -> None:
        """A native yaml scene that has its own id is confirmed by that stable
        id (no fingerprint needed), not by the mutable entity_id."""
        from pathlib import Path

        from custom_components.selora_ai.mcp_server import _preview_delete_scene
        from custom_components.selora_ai.scene_utils import _write_scenes_yaml

        await hass.async_add_executor_job(
            _write_scenes_yaml,
            Path(hass.config.config_dir) / "scenes.yaml",
            [{"id": "native42", "name": "External Lights", "entities": {}}],
        )
        hass.states.async_set("scene.external_lights", "scening", {"id": "native42"})

        result = await _preview_delete_scene(hass, {"entity_id": "scene.external_lights"})

        assert result["requires_approval"] is True
        assert result["delete"]["target_id"] == "native42"
        assert result["delete"]["name"] == ""  # stable id → no fingerprint


class TestAtomicIdlessBackend:
    """Direct tests of the atomic, fingerprint-enforcing backend helpers."""

    @pytest.mark.asyncio
    async def test_automation_by_alias_removes_match(self, hass: HomeAssistant) -> None:
        from pathlib import Path

        from custom_components.selora_ai.automation_utils import (
            _read_automations_yaml,
            _write_automations_yaml,
        )
        from custom_components.selora_ai.mcp_server import _delete_idless_automation_by_alias

        path = Path(hass.config.config_dir) / "automations.yaml"
        await hass.async_add_executor_job(
            _write_automations_yaml, path, [{"alias": "Evening", "trigger": [], "action": []}]
        )
        hass.services.async_register("automation", "reload", AsyncMock())

        result = await _delete_idless_automation_by_alias(hass, "Evening")
        assert result == {"status": "deleted"}
        assert await hass.async_add_executor_job(_read_automations_yaml, path) == []

    @pytest.mark.asyncio
    async def test_automation_by_alias_absent_is_changed(self, hass: HomeAssistant) -> None:
        from pathlib import Path

        from custom_components.selora_ai.automation_utils import _write_automations_yaml
        from custom_components.selora_ai.mcp_server import _delete_idless_automation_by_alias

        path = Path(hass.config.config_dir) / "automations.yaml"
        await hass.async_add_executor_job(
            _write_automations_yaml,
            path,
            [{"alias": "Something Else", "trigger": [], "action": []}],
        )
        hass.services.async_register("automation", "reload", AsyncMock())

        result = await _delete_idless_automation_by_alias(hass, "Evening")
        assert "error" in result
        assert "changed since it was shown" in result["error"]

    @pytest.mark.asyncio
    async def test_automation_by_alias_ambiguous_refused(self, hass: HomeAssistant) -> None:
        from pathlib import Path

        from custom_components.selora_ai.automation_utils import (
            _read_automations_yaml,
            _write_automations_yaml,
        )
        from custom_components.selora_ai.mcp_server import _delete_idless_automation_by_alias

        path = Path(hass.config.config_dir) / "automations.yaml"
        await hass.async_add_executor_job(
            _write_automations_yaml,
            path,
            [
                {"alias": "Dup", "trigger": [], "action": []},
                {"alias": "Dup", "trigger": [], "action": []},
            ],
        )
        hass.services.async_register("automation", "reload", AsyncMock())

        result = await _delete_idless_automation_by_alias(hass, "Dup")
        assert "error" in result and "Multiple" in result["error"]
        # Nothing removed on an ambiguous match.
        assert len(await hass.async_add_executor_job(_read_automations_yaml, path)) == 2

    @pytest.mark.asyncio
    async def test_scene_by_name_enforced_atomically(self, hass: HomeAssistant) -> None:
        from pathlib import Path

        from custom_components.selora_ai.scene_utils import (
            _read_scenes_yaml,
            _write_scenes_yaml,
            async_remove_yaml_scene_by_entity,
        )

        path = Path(hass.config.config_dir) / "scenes.yaml"
        await hass.async_add_executor_job(
            _write_scenes_yaml, path, [{"name": "Movie", "entities": {"light.x": {"state": "on"}}}]
        )
        hass.states.async_set("scene.movie", "scening")
        hass.services.async_register("scene", "reload", AsyncMock())

        removed, code, _ = await async_remove_yaml_scene_by_entity(
            hass, "scene.movie", expected_name="Movie"
        )
        assert removed is True
        assert code is None
        assert await hass.async_add_executor_job(_read_scenes_yaml, path) == []

    @pytest.mark.asyncio
    async def test_scene_by_name_works_when_entity_unloaded(self, hass: HomeAssistant) -> None:
        """The fingerprint path must run before the live-state check: a scene
        whose entity became unloaded/remapped after the card is still deletable
        by its confirmed yaml name."""
        from pathlib import Path

        from custom_components.selora_ai.scene_utils import (
            _read_scenes_yaml,
            _write_scenes_yaml,
            async_remove_yaml_scene_by_entity,
        )

        path = Path(hass.config.config_dir) / "scenes.yaml"
        await hass.async_add_executor_job(
            _write_scenes_yaml, path, [{"name": "Movie", "entities": {"light.x": {"state": "on"}}}]
        )
        # Deliberately do NOT register scene.movie state → entity is "unloaded".
        hass.services.async_register("scene", "reload", AsyncMock())

        removed, code, _ = await async_remove_yaml_scene_by_entity(
            hass, "scene.movie", expected_name="Movie"
        )
        assert removed is True
        assert code is None
        assert await hass.async_add_executor_job(_read_scenes_yaml, path) == []

    @pytest.mark.asyncio
    async def test_tool_delete_scene_fingerprint_skips_store_lookup(
        self, hass: HomeAssistant
    ) -> None:
        """With a confirmed name, _tool_delete_scene routes straight to the
        name-based yaml helper and never consults the SceneStore — so a new
        Selora scene that reused the entity_id can't be deleted instead."""
        from custom_components.selora_ai.mcp_server import _tool_delete_scene

        with (
            patch("custom_components.selora_ai.helpers.get_scene_store") as mock_getter,
            patch(
                "custom_components.selora_ai.scene_utils.async_remove_yaml_scene_by_entity",
                new_callable=AsyncMock,
                return_value=(True, None, None),
            ) as mock_remove,
        ):
            result = await _tool_delete_scene(
                hass, {"entity_id": "scene.night", "expected_name": "Old Native"}
            )
        assert result == {"entity_id": "scene.night", "status": "deleted"}
        mock_remove.assert_awaited_once_with(hass, "scene.night", expected_name="Old Native")
        # The SceneStore was never consulted — no chance to match/delete a new
        # record that reused this entity_id.
        mock_getter.assert_not_called()

    @pytest.mark.asyncio
    async def test_scene_by_name_mismatch_reports_not_found(self, hass: HomeAssistant) -> None:
        from pathlib import Path

        from custom_components.selora_ai.scene_utils import (
            _read_scenes_yaml,
            _write_scenes_yaml,
            async_remove_yaml_scene_by_entity,
        )

        path = Path(hass.config.config_dir) / "scenes.yaml"
        await hass.async_add_executor_job(
            _write_scenes_yaml, path, [{"name": "Movie", "entities": {"light.x": {"state": "on"}}}]
        )
        hass.states.async_set("scene.movie", "scening")
        hass.services.async_register("scene", "reload", AsyncMock())

        removed, code, _ = await async_remove_yaml_scene_by_entity(
            hass, "scene.movie", expected_name="Ghost"
        )
        assert removed is False
        assert code == "not_found_in_yaml"
        # The mismatched entry is left intact.
        assert len(await hass.async_add_executor_job(_read_scenes_yaml, path)) == 1


class TestDeleteQuickActions:
    def test_shape(self) -> None:
        actions = _delete_approval_quick_actions("pid-1")
        assert [a["value"] for a in actions] == [
            "approve:delete:pid-1",
            "approve:cancel:pid-1",
        ]
        # The destructive confirm carries the deny tone (red); cancel is neutral.
        assert actions[0]["tone"] == "deny"
        assert "tone" not in actions[1]
