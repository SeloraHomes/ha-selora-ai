"""What the cameras saw: Frigate events through find_camera_events."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.frigate.events import (
    async_find_events,
    async_known_names,
)
from custom_components.selora_ai.llm_client.client import LLMClient
from custom_components.selora_ai.llm_client.command_policy import (
    _build_delete_approval_response,
    _pending_destructive_from_log,
)
from custom_components.selora_ai.llm_client.prompts import _SHARED_STATE_QUERY_RULES
from custom_components.selora_ai.mcp_server.access import _ADMIN_TOOLS
from custom_components.selora_ai.mcp_server.names import TOOL_FIND_CAMERA_EVENTS
from custom_components.selora_ai.providers import AnthropicProvider
from custom_components.selora_ai.tool_executor import ToolExecutor

from .chat_harness import ChatHarness

FRIGATE = "http://frigate.local:5000"
# The provider Selora OS writes: Connect names it, its address makes it Selora's.
_SELORA_GENAI = {
    "Selora": {
        "provider": "openai",
        "base_url": "https://connect.example/api/v1/ai-gateway/v1",
        "roles": ["descriptions"],
    }
}
NOW = dt_util.utcnow().timestamp()

_CONFIG: dict[str, Any] = {
    "cameras": {
        "front_door": {"friendly_name": "Front Door"},
        "drive": {},
    },
    "semantic_search": {"enabled": False},
    "genai": {"enabled": False},
}


def _event(
    event_id: str, camera: str, label: str, start: float, end: float | None, **extra: Any
) -> dict[str, Any]:
    return {
        "id": event_id,
        "camera": camera,
        "label": label,
        "start_time": start,
        "end_time": end,
        "zones": [],
        "sub_label": None,
        "has_clip": True,
        "has_snapshot": True,
        "data": {"top_score": 0.8512, "description": "A man in a blue coat."},
        **extra,
    }


EVENTS = [
    _event(f"{NOW - 600:.6f}-old001", "drive", "car", NOW - 600, NOW - 540),
    _event(
        f"{NOW - 60:.6f}-new001",
        "front_door",
        "person",
        NOW - 60,
        None,
        sub_label=["Alice", 0.93],
        zones=["porch"],
    ),
    _event(f"{NOW - 300:.6f}-mid001", "front_door", "person", NOW - 300, NOW - 291.4),
    _event("../../api/config", "front_door", "person", NOW - 30, NOW - 20),
]


@pytest.fixture
def frigate(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain="frigate", data={"url": FRIGATE})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry


def _api(
    aioclient_mock: Any, events: list[dict[str, Any]], config: dict[str, Any] | None = None
) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", json=config or _CONFIG)
    aioclient_mock.get(f"{FRIGATE}/api/events", json=events)
    aioclient_mock.get(f"{FRIGATE}/api/events/search", json=events)


def _asked(aioclient_mock: Any) -> dict[str, str]:
    (url,) = [c[1] for c in aioclient_mock.mock_calls if "/api/events" in str(c[1])]
    return dict(url.query)


async def test_without_frigate_it_says_so(hass: HomeAssistant) -> None:
    result = await async_find_events(hass, {})

    assert "not set up" in result["error"]


async def test_events_come_newest_first_with_local_times(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, EVENTS)

    result = await async_find_events(hass, {})

    newest, middle, oldest = result["events"]
    assert newest["camera_name"] == "Front Door"
    assert newest["sub_label"] == "Alice"
    assert newest["zones"] == ["porch"]
    assert newest["in_progress"] is True and "seconds" not in newest
    assert newest["start"] == dt_util.as_local(dt_util.utc_from_timestamp(NOW - 60)).isoformat(
        timespec="seconds"
    )
    assert middle["seconds"] == 9
    assert middle["score"] == 0.85
    assert oldest["camera_name"] == "drive"
    # Frigate's AI descriptions only when its GenAI is on.
    assert "description" not in newest
    assert result["total"] == 3
    assert "by_object" not in result


async def test_an_event_id_that_is_not_one_is_dropped(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, EVENTS)

    result = await async_find_events(hass, {})

    assert all("/" not in e["id"] for e in result["events"])


async def test_a_camera_name_frigate_would_not_allow_gets_no_clip(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    odd = _event(f"{NOW - 5:.6f}-odd001", "../drive", "car", NOW - 5, NOW - 1)
    _api(aioclient_mock, [odd])

    (event,) = (await async_find_events(hass, {}))["events"]

    assert event["camera"] == ""
    assert event["has_clip"] is False and event["has_snapshot"] is True


async def test_filters_go_to_frigate_and_counts_cover_the_range(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    frigate_camera = er.async_get(hass).async_get_or_create(
        "camera", "frigate", f"{frigate.entry_id}:camera:front_door"
    )
    _api(aioclient_mock, EVENTS)

    result = await async_find_events(
        hass,
        {
            "cameras": [frigate_camera.entity_id, "Drive"],
            "objects": ["Person", "car"],
            "zones": ["porch"],
            "names": ["Alice"],
            "hours": 2,
            "limit": 1,
        },
    )

    asked = _asked(aioclient_mock)
    assert asked["cameras"] == "front_door,drive"
    assert asked["labels"] == "person,car"
    assert asked["zones"] == "porch"
    assert asked["sub_labels"] == "Alice"
    assert asked["include_thumbnails"] == "0"
    assert float(asked["before"]) - float(asked["after"]) == pytest.approx(7200, abs=1)
    assert len(result["events"]) == 1
    assert result["total"] == 3
    assert result["by_object"] == {"person": 2, "car": 1}
    assert result["by_camera"] == {"front_door": 2, "drive": 1}


async def test_an_unknown_camera_is_named_with_the_ones_there_are(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, EVENTS)

    result = await async_find_events(hass, {"cameras": ["garage"]})

    assert result["error"] == "Frigate has no camera 'garage'."
    assert {"camera": "front_door", "name": "Front Door"} in result["cameras"]


async def test_a_description_search_without_semantic_search_keeps_the_other_filters(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, [EVENTS[0]])

    result = await async_find_events(
        hass, {"query": "a white car", "objects": ["car"], "start": "2026-10-25"}
    )

    url = next(c[1] for c in aioclient_mock.mock_calls if "/api/events" in str(c[1]))
    assert url.path == "/api/events"
    assert url.query["labels"] == "car"
    assert "query" not in url.query
    assert [e["label"] for e in result["events"]] == ["car"]
    assert result["not_filtered_by"] == "a white car"
    assert "semantic search is off" in result["note"]
    assert set(result["semantic_search"]) == {"where", "cost", "history", "privacy"}


async def test_without_semantic_search_the_model_reads_the_descriptions_itself(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    """Told only that Frigate did not check them, the model said it could not
    verify a backpack with the description saying so in front of it."""
    _api(aioclient_mock, EVENTS, {**_CONFIG, "genai": {"enabled": True, "provider": "ollama"}})

    result = await async_find_events(hass, {"query": "someone with a backpack"})

    assert result["not_filtered_by"] == "someone with a backpack"
    assert "read each event's description yourself" in result["note"]


async def test_a_description_search_is_ranked_by_frigate(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    config = {**_CONFIG, "semantic_search": {"enabled": True}, "genai": {"enabled": True}}
    _api(aioclient_mock, [EVENTS[0], EVENTS[1]], config)

    result = await async_find_events(hass, {"query": "a blue coat"})

    url = next(c[1] for c in aioclient_mock.mock_calls if "/api/events" in str(c[1]))
    assert url.path == "/api/events/search"
    assert url.query["query"] == "a blue coat"
    # Frigate's order is the ranking: kept, not re-sorted by time.
    assert [e["label"] for e in result["events"]] == ["car", "person"]
    assert result["events"][0]["description"] == "A man in a blue coat."


async def test_a_search_hands_every_ranked_match_to_the_model(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    # One event scoring far ahead must not hide the rest: a weaker match's
    # description can still say what was asked, and the card folds what the
    # reply does not confirm.
    config = {**_CONFIG, "semantic_search": {"enabled": True}}
    ranked = [
        _event(f"{NOW - n:.6f}-van{n:03d}", "drive", "car", NOW - n, NOW, search_distance=d)
        for n, d in enumerate([-5.9, -3.05, -1.75, -1.65, -1.5], start=1)
    ]
    _api(aioclient_mock, ranked, config)

    result = await async_find_events(hass, {"query": "red car", "limit": 25})

    assert [e["id"] for e in result["events"]] == [e["id"] for e in ranked]


async def test_a_range_too_long_is_refused(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, EVENTS)

    result = await async_find_events(hass, {"start": "2020-01-01", "end": "2020-03-01"})

    assert "over 31 days" in result["error"]


async def test_frigate_down_says_so(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", status=500, text="<html>500</html>")

    result = await async_find_events(hass, {})

    assert result["error"].startswith("Frigate is not responding")


async def test_only_an_admin_may_ask(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, EVENTS)

    refused = await ToolExecutor(hass, MagicMock(), is_admin=False).execute(
        "find_camera_events", {}
    )
    allowed = await ToolExecutor(hass, MagicMock(), is_admin=True).execute("find_camera_events", {})

    assert "error" in refused
    assert len(allowed["events"]) == 3
    assert TOOL_FIND_CAMERA_EVENTS in _ADMIN_TOOLS


async def test_the_reply_keeps_the_events_for_its_card(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, EVENTS)
    harness = await ChatHarness.create(hass)
    # The tool really runs; the stream replays what it recorded, as the
    # provider round trip would.
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)
    await executor.execute("find_camera_events", {"cameras": ["front_door"]})

    turn = await harness.stream(
        "was anyone at the front door?",
        chunks="Alice is at the front door now.",
        tool_calls=executor.call_log,
    )

    assert not turn.errors
    stored = (await harness.messages())[-1]
    (call,) = [c for c in stored["tool_calls"] if c["tool"] == "find_camera_events"]
    assert [e["label"] for e in call["result"]["events"]] == ["person", "person", "car"]
    assert turn.done["tool_calls"] == stored["tool_calls"]


@pytest.mark.parametrize(("searched", "tile"), [(True, False), (False, True)])
async def test_a_camera_answer_shows_no_tile_for_a_device_named_like_the_place(
    hass: HomeAssistant,
    frigate: MockConfigEntry,
    aioclient_mock: Any,
    searched: bool,
    tile: bool,
) -> None:
    # The lock is named after the door, like the camera watching it.
    hass.states.async_set("lock.front_door", "locked", {"friendly_name": "Front Door"})
    _api(aioclient_mock, [])
    harness = await ChatHarness.create(hass)
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)
    if searched:
        await executor.execute("find_camera_events", {"cameras": ["front_door"]})

    turn = await harness.stream(
        "was anyone at the front door this morning?",
        chunks="Nobody was seen at the Front Door this morning.",
        tool_calls=executor.call_log,
    )

    assert not turn.errors
    assert ("[[entit" in turn.done["response"]) is tile


async def test_a_search_says_what_the_cameras_could_not_have_seen(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    config = {
        **_CONFIG,
        "cameras": {
            "front_door": {"detect": {"enabled": False}, "objects": {"track": ["person"]}},
            "drive": {"detect": {"enabled": True}, "objects": {"track": ["person"]}},
        },
    }
    _api(aioclient_mock, [], config)

    result = await async_find_events(hass, {"objects": ["car"]})

    assert result["watching"] == [
        {"camera": "front_door", "detection": False, "looks_for": ["person"]},
        {"camera": "drive", "detection": True, "looks_for": ["person"]},
    ]
    off, car = result["not_recorded"]
    assert "front_door" in off
    assert "'car'" in car
    assert result["offer"].startswith("Make drive look for car from now on")


async def test_a_camera_looking_for_it_leaves_nothing_unrecorded(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    config = {
        **_CONFIG,
        "cameras": {"drive": {"detect": {"enabled": True}, "objects": {"track": ["car"]}}},
    }
    _api(aioclient_mock, [], config)

    result = await async_find_events(hass, {"objects": ["car"]})

    assert "not_recorded" not in result
    assert "offer" not in result


_NO_DOGS = {
    **_CONFIG,
    "cameras": {
        "e1_zoom": {"detect": {"enabled": True}, "objects": {"track": ["person"]}},
        "wyze_front": {"detect": {"enabled": True}, "objects": {"track": ["car"]}},
    },
}


async def test_nothing_found_for_want_of_a_camera_ends_on_the_change_card(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    # "Would you like me to?" in prose, with buttons only when the model
    # remembered them: the same change card as every other change instead.
    _api(aioclient_mock, [], _NO_DOGS)
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)

    result = await executor.execute("find_camera_events", {"objects": ["dog"]})

    proposed = [e for e in executor.call_log if e["tool"] == "set_camera_objects"]
    assert [e["arguments"] for e in proposed] == [
        {"camera": "e1_zoom", "add": ["dog"]},
        {"camera": "wyze_front", "add": ["dog"]},
    ]
    assert all(e["result"]["requires_approval"] for e in proposed)
    assert "do not ask" in result["offer"]
    assert "mention the card" in result["offer"]
    card = _build_delete_approval_response(
        {"intent": "answer", "response": ""},
        [],
        executor.call_log,
        None,
        language="en",
        actions=_pending_destructive_from_log(executor.call_log),
    )
    assert len(card["command_approval"]["actions"]) == 2


async def test_a_search_that_found_events_answers_with_them(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    # Recorded while a camera looked for it: those are the answer, not a card.
    dog = _event(f"{NOW - 60:.6f}-dog001", "e1_zoom", "dog", NOW - 60, NOW - 50)
    _api(aioclient_mock, [dog], _NO_DOGS)
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)

    result = await executor.execute("find_camera_events", {"objects": ["dog"]})

    assert result["events"]
    assert [e["tool"] for e in executor.call_log] == ["find_camera_events"]


async def test_a_card_to_look_for_it_says_why(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    # The panel shows the card, not the reply: "Apply this change?" alone did
    # not say the search could not succeed.
    config = {
        **_CONFIG,
        "cameras": {"e1_zoom": {"detect": {"enabled": True}, "objects": {"track": ["person"]}}},
    }
    _api(aioclient_mock, [], config)
    search = await async_find_events(hass, {"objects": ["dog"], "query": "white dog"})
    change = {
        "kind": "frigate_objects",
        "verb": "set_objects",
        "target_id": "e1_zoom",
        "entity_id": "",
        "name": "E1 Zoom",
        "label": "E1 Zoom starts looking for dog (Selora Vision restarts)",
        "fingerprint": "abc",
    }
    log = [
        {"tool": "find_camera_events", "arguments": {}, "result": search},
        {
            "tool": "set_camera_objects",
            "arguments": {"camera": "e1_zoom", "add": ["dog"]},
            "result": {"requires_approval": True, "destructive": change},
        },
    ]

    card = _build_delete_approval_response(
        {"intent": "answer", "response": ""},
        [],
        log,
        None,
        language="en",
        actions=_pending_destructive_from_log(log),
    )

    why = card["command_approval"]["why"]
    assert why.startswith("No camera looks for dog yet")
    assert card["response"].startswith(why)
    # A change, not a deletion.
    assert "delete" not in card["response"].lower()


async def test_a_question_not_about_the_cameras_keeps_its_answer_above_the_card(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    # "Was the dog fed?": the model searches the cameras too. Its answer is the
    # reply, and the card under it only asks whether to look for dogs.
    _api(aioclient_mock, [], _NO_DOGS)
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)
    executor.camera_question = False
    await executor.execute("find_camera_events", {"objects": ["dog"]})
    answer = "I can't tell from the cameras: none of them looks for dogs."

    card = _build_delete_approval_response(
        {"intent": "answer", "response": answer},
        [],
        executor.call_log,
        None,
        language="en",
        actions=_pending_destructive_from_log(executor.call_log),
    )

    approval = card["command_approval"]
    assert approval["answer_first"] is True
    assert approval["why"] == "Have the cameras look for dog from now on?"
    assert card["response"] == answer
    assert len(approval["actions"]) == 2


async def test_the_answer_stays_beside_a_command_that_ran(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    # "Turn on the porch light — and was the dog fed?": the light's
    # confirmation and the answer, then the card.
    _api(aioclient_mock, [], _NO_DOGS)
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)
    executor.camera_question = False
    await executor.execute("find_camera_events", {"objects": ["dog"]})
    log = [
        {
            "tool": "execute_command",
            "arguments": {"service": "light.turn_on", "entity_id": "light.porch"},
            "result": {
                "executed": True,
                "service": "light.turn_on",
                "entity_ids": ["light.porch"],
            },
        },
        *executor.call_log,
    ]
    answer = "I can't tell from the cameras: none of them looks for dogs."

    card = _build_delete_approval_response(
        {"intent": "answer", "response": answer},
        [],
        log,
        None,
        language="en",
        actions=_pending_destructive_from_log(log),
    )

    assert card["response"].endswith(answer)
    assert card["response"] != answer


async def test_a_question_about_the_cameras_gets_the_card_alone(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, [], _NO_DOGS)
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)
    await executor.execute("find_camera_events", {"objects": ["dog"]})

    card = _build_delete_approval_response(
        {"intent": "answer", "response": "No dogs were seen."},
        [],
        executor.call_log,
        None,
        language="en",
        actions=_pending_destructive_from_log(executor.call_log),
    )

    assert "answer_first" not in card["command_approval"]
    assert card["command_approval"]["why"].startswith("No camera looks for dog yet")
    assert card["response"].startswith("No camera looks for dog yet")


def test_the_offer_in_a_note_is_one_the_model_is_told_to_make() -> None:
    # The prompt limits offers to safe devices; a tool's `offer` is the
    # exception, or the model only reports what could not be seen.
    assert "top-level `offer` field" in _SHARED_STATE_QUERY_RULES


@pytest.mark.parametrize(
    ("genai", "shown"),
    [
        # Frigate 0.18: named providers; no roles means every role.
        ({"selora": {"provider": "openai", "model": "m"}}, True),
        ({"selora": {"provider": "openai", "roles": ["descriptions"]}}, True),
        ({"selora": {"provider": "openai", "roles": ["chat", "embeddings"]}}, False),
        ({"selora": {"provider": "openai", "roles": []}}, False),
        ({"provider": {"provider": "openai", "roles": ["descriptions"]}}, True),
        # Before 0.18: one provider with a flag.
        ({"enabled": True, "provider": "openai"}, True),
        ({"enabled": False}, False),
        ({}, False),
    ],
)
async def test_descriptions_are_kept_when_frigate_writes_them(
    hass: HomeAssistant,
    frigate: MockConfigEntry,
    aioclient_mock: Any,
    genai: dict[str, Any],
    shown: bool,
) -> None:
    _api(aioclient_mock, EVENTS, {**_CONFIG, "genai": genai})

    result = await async_find_events(hass, {})

    described = [e for e in result["events"] if "description" in e]
    assert bool(described) is shown


async def test_a_search_reads_descriptions_when_frigate_writes_them(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    config = {
        **_CONFIG,
        "semantic_search": {"enabled": True},
        "genai": _SELORA_GENAI,
    }
    _api(aioclient_mock, EVENTS, config)

    await async_find_events(hass, {"query": "white car"})

    # The range is listed as well, for what the search could not match.
    (search,) = [c[1] for c in aioclient_mock.mock_calls if "/api/events/search" in str(c[1])]
    assert dict(search.query)["search_type"] == "thumbnail,description"


async def test_a_search_without_descriptions_leaves_frigates_default(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, EVENTS, {**_CONFIG, "semantic_search": {"enabled": True}})

    await async_find_events(hass, {"query": "white car"})

    assert "search_type" not in _asked(aioclient_mock)


async def test_a_label_the_model_cannot_report_is_not_offered(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    # No camera setting fixes it, so offering one would loop: the card would
    # be refused, or Frigate would drop the label at startup.
    config = {
        **_CONFIG,
        "cameras": {"drive": {"detect": {"enabled": True}, "objects": {"track": ["person"]}}},
        "detectors": {"rocm": {"model": {"labelmap": {0: "person", 2: "car", 5: "car"}}}},
    }
    _api(aioclient_mock, [], config)

    result = await async_find_events(hass, {"objects": ["bus"]})

    (note,) = result["not_recorded"]
    assert "does not detect 'bus'" in note
    assert "reported as car" in note
    assert "offer" not in result


async def test_the_result_names_the_frigate_instance_for_the_panels_media(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    # The panel draws an event without a snapshot from the integration's
    # thumbnail route, which is keyed by Frigate's MQTT client id.
    _api(aioclient_mock, EVENTS, {**_CONFIG, "mqtt": {"client_id": "frigate"}})

    result = await async_find_events(hass, {})

    assert result["instance"] == "frigate"


@pytest.mark.parametrize(
    ("client_id", "passed"),
    [
        ("frigate.garage-2", True),
        ("frigate:garage", True),
        ("a b", False),
        ("a?b", False),
        ("x" * 128, True),
        ("../api/config", False),
        ("..", False),
        (".", False),
        ("a/b", False),
        ("x" * 129, False),
    ],
)
async def test_only_a_path_safe_instance_id_is_passed_on(
    hass: HomeAssistant,
    frigate: MockConfigEntry,
    aioclient_mock: Any,
    client_id: str,
    passed: bool,
) -> None:
    _api(aioclient_mock, EVENTS, {**_CONFIG, "mqtt": {"client_id": client_id}})

    result = await async_find_events(hass, {})

    assert ("instance" in result) is passed


async def test_the_names_frigate_recognises_are_its_faces_and_plates(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    config = {
        **_CONFIG,
        "face_recognition": {"enabled": True},
        "lpr": {"known_plates": {"Alice's car": ["AB-123-CD"]}},
    }
    _api(aioclient_mock, [], config)
    aioclient_mock.get(f"{FRIGATE}/api/faces", json={"Alice": ["a.webp"], "train": ["x.webp"]})

    first = await async_known_names(hass)
    again = await async_known_names(hass)

    assert first == again == ["Alice", "Alice's car"]
    # Every chat turn asks: Frigate is read once per TTL.
    assert aioclient_mock.call_count == 2


async def test_faces_count_only_while_recognition_runs(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, [], {**_CONFIG, "face_recognition": {"enabled": False}})
    aioclient_mock.get(f"{FRIGATE}/api/faces", json={"Alice": ["a.webp"]})

    assert await async_known_names(hass) == []


async def test_a_frigate_that_does_not_answer_names_nobody(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", exc=TimeoutError)

    assert await async_known_names(hass) == []


def test_the_chat_context_says_whom_the_cameras_recognise(hass: HomeAssistant) -> None:
    client = LLMClient(hass, AnthropicProvider(hass, api_key="k"))

    told = client._build_chat_messages(
        "when was Alice last seen?", [], None, None, camera_names=["Alice"]
    )
    untold = client._build_chat_messages("when was Alice last seen?", [], None, None)

    assert "NAMES THE CAMERAS RECOGNISE" in told[-1]["content"]
    assert "Alice" in told[-1]["content"].split("NAMES THE CAMERAS RECOGNISE", 1)[1]
    assert "NAMES THE CAMERAS RECOGNISE" not in untold[-1]["content"]
