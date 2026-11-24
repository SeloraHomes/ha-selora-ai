"""Picking which cameras a "look for it" card changes."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest

import custom_components.selora_ai as selora
from custom_components.selora_ai.llm_client.command_policy import (
    _build_delete_approval_response,
)


def _camera(name: str) -> dict[str, Any]:
    return {
        "kind": "frigate_objects",
        "verb": "set_objects",
        "target_id": name,
        "entity_id": "",
        "name": name,
        "label": f"{name} starts looking for dog (Selora Vision restarts)",
        "fingerprint": f"fp-{name}",
        "payload": {"camera": name, "add": ["dog"]},
    }


def _rename() -> dict[str, Any]:
    return {**_camera("x"), "kind": "entity", "target_id": "light.porch"}


def _card(actions: list[dict[str, Any]]) -> dict[str, Any]:
    return _build_delete_approval_response(
        {"intent": "answer", "response": ""}, [], [], None, language="en", actions=actions
    )["command_approval"]


def test_a_card_for_several_cameras_lets_the_user_pick() -> None:
    assert _card([_camera("e1_zoom"), _camera("wyze_front")])["pickable"] is True


@pytest.mark.parametrize(
    "actions",
    [
        [_camera("e1_zoom")],  # one camera: Apply or Cancel says it all
        [_camera("e1_zoom"), _rename()],  # not only cameras
    ],
)
def test_any_other_card_is_all_or_nothing(actions: list[dict[str, Any]]) -> None:
    assert "pickable" not in _card(actions)


async def _resolve(
    hass: HomeAssistant, approval: dict[str, Any], targets: list[str]
) -> tuple[MagicMock, AsyncMock]:
    store = MagicMock()
    store.get_session = AsyncMock(
        return_value={
            "messages": [
                {
                    "intent": "command_approval",
                    "command_approval": approval,
                    "approval_status": "pending",
                }
            ]
        }
    )
    store.set_approval_status = AsyncMock()
    store.append_message = AsyncMock(return_value={"role": "assistant"})
    connection = MagicMock()
    apply = AsyncMock(return_value=(["applied"], []))
    with patch.object(selora, "_apply_destructive_actions", apply):
        await selora._resolve_approval(
            hass,
            connection,
            {"id": 1, "targets": targets},
            store,
            MagicMock(),
            "sess",
            "p1",
            "apply",
        )
    return connection, apply


def _approval(*, pickable: bool = True) -> dict[str, Any]:
    return {
        "proposal_id": "p1",
        "approval_kind": "destructive",
        "actions": [_camera("e1_zoom"), _camera("wyze_front")],
        **({"pickable": True} if pickable else {}),
    }


async def test_only_the_ticked_cameras_change(hass: HomeAssistant) -> None:
    _connection, apply = await _resolve(hass, _approval(), ["wyze_front"])

    (actions,) = [call.args[1] for call in apply.await_args_list]
    assert [a["target_id"] for a in actions] == ["wyze_front"]


@pytest.mark.parametrize(
    ("targets", "pickable"),
    [
        ([], True),  # nothing ticked
        (["garage"], True),  # not a row of the card
        (["wyze_front", "garage"], True),
        (["wyze_front"], False),  # a card that does not let the user pick
    ],
)
async def test_a_pick_the_card_did_not_offer_changes_nothing(
    hass: HomeAssistant, targets: list[str], pickable: bool
) -> None:
    connection, apply = await _resolve(hass, _approval(pickable=pickable), targets)

    apply.assert_not_awaited()
    assert connection.send_error.call_args.args[1] == "invalid_targets"
