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

from custom_components.selora_ai.frigate.events import async_find_events
from custom_components.selora_ai.llm_client.prompts import _SHARED_STATE_QUERY_RULES
from custom_components.selora_ai.mcp_server.access import _ADMIN_TOOLS
from custom_components.selora_ai.mcp_server.names import TOOL_FIND_CAMERA_EVENTS
from custom_components.selora_ai.tool_executor import ToolExecutor

from .chat_harness import ChatHarness

FRIGATE = "http://frigate.local:5000"
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
        "genai": {"selora": {"provider": "openai", "roles": ["descriptions"]}},
    }
    _api(aioclient_mock, EVENTS, config)

    await async_find_events(hass, {"query": "white car"})

    assert _asked(aioclient_mock)["search_type"] == "thumbnail,description"


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
