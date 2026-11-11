"""A camera question opens with its search (``llm_client/camera_question.py``).

Asking the model for the obvious ``find_camera_events`` call cost a whole
round of the full prompt, three to six seconds, before Frigate was even asked.
The search is written from the question and run before the model's first
round, which reads its result as if it had asked; anything the classifier is
unsure of is left to the model, as before.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai.llm_client import LLMClient
from custom_components.selora_ai.llm_client.camera_question import (
    opening_search,
    points_at_cameras,
)
from custom_components.selora_ai.providers import create_provider
from custom_components.selora_ai.tool_executor import ToolExecutor

NOW = datetime(2026, 11, 6, 15, 30)
CAMERAS = {"front_door": "Front Door", "wyze_front": "Wyze Front"}


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "Show when a white truck was in the street",
            # A truck is a car to coco; the query tells them apart. No time
            # named: a week, not the tool's day.
            {"objects": ["car"], "query": "white truck", "hours": 168.0},
        ),
        (
            "Any FedEx truck today?",
            {"objects": ["car"], "query": "fedex truck", "start": "2026-11-06T00:00:00"},
        ),
        (
            "Was anyone at the front door in the last 3 hours?",
            # A label answers on its own: no query, which would fold the card.
            {"objects": ["person"], "cameras": ["front_door"], "hours": 3.0},
        ),
        (
            "When was Sam seen for the last time",
            {"names": ["Sam"], "hours": 168.0},
        ),
        (
            "How many people came by yesterday?",
            {
                "objects": ["person"],
                "start": "2026-11-05T00:00:00",
                "end": "2026-11-06T00:00:00",
            },
        ),
        (
            "Was there a man in a red coat at the front door this morning?",
            {
                "objects": ["person"],
                "query": "man in a red coat",
                "cameras": ["front_door"],
                "start": "2026-11-06T00:00:00",
                "end": "2026-11-06T12:00:00",
            },
        ),
        (
            "Did someone with a backpack come by last night?",
            {
                "objects": ["person"],
                "query": "someone with a backpack",
                "start": "2026-11-05T18:00:00",
                "end": "2026-11-06T06:00:00",
            },
        ),
    ],
)
def test_a_question_about_the_past_opens_with_its_search(
    message: str, expected: dict[str, Any]
) -> None:
    assert opening_search(message, NOW, cameras=CAMERAS, names=["Sam"]) == expected


@pytest.mark.parametrize(
    "message",
    [
        # Now, not the past: the entities answer.
        "Is the front door locked?",
        "Is there a car in the driveway?",
        # Automations and commands.
        "Notify me when a truck is in the street",
        "Turn on the porch light when someone arrives",
        "Track cars on the Wyze Front camera",
        # A time it cannot read: a wrong range is worse than a slower answer.
        "When did the dog go out on Monday?",
        "Was a car here 2 hours ago?",
        # A calendar week starts on a day that depends on who asks.
        "Which camera saw a bus this week?",
        "Who was seen at the front door last week?",
        # A condition on the answer is the model's to apply.
        "Was anyone at the door? If you can't verify it, don't show me the clips.",
        # Nothing a camera tracks.
        "When was the last time the heating ran?",
        # A thing a camera tracks, asked about as a thing: nothing says camera.
        "Was the dog fed?",
        "Has the car been charged today?",
        "When was the car last charged?",
        "Did the cat eat today?",
        "How many cars do I have?",
        "How many people are home?",
        "Did someone leave the garage door unlocked?",
    ],
)
def test_anything_else_is_left_to_the_model(message: str) -> None:
    assert opening_search(message, NOW, cameras=CAMERAS, names=["Sam"]) is None


@pytest.mark.parametrize(
    "message",
    [
        "When was the last time someone came to the door?",
        "Show me cars that drove past the street yesterday",
    ],
)
def test_filler_words_are_not_a_description(message: str) -> None:
    """A query ranks the events by relevance; "the last time" asks for the newest."""
    found = opening_search(message, NOW, cameras=CAMERAS)

    assert found is not None
    assert "query" not in found


def test_a_place_phrase_does_not_name_a_camera() -> None:
    """ "in front of the garage" is the garage camera, not one called "front"."""
    found = opening_search(
        "Was someone in front of the garage today?",
        NOW,
        cameras={"front": "Front", "garage": "Garage"},
    )

    assert found is not None
    assert found["cameras"] == ["garage"]


def test_this_month_starts_on_the_first() -> None:
    found = opening_search("Which camera saw a bus this month?", NOW)
    assert found == {"query": "bus", "start": "2026-11-01T00:00:00"}


def test_an_afternoon_not_yet_begun_is_left_to_the_model() -> None:
    morning = datetime(2026, 11, 6, 9, 0)
    assert opening_search("Show me the people seen this afternoon", morning) is None


_EVENTS = {"events": [{"id": "1793470197.253093-1wv7iz", "label": "car"}]}
ANSWER = "A white truck passed at 8:02."


def _client(hass: HomeAssistant) -> LLMClient:
    return LLMClient(hass, create_provider("anthropic", hass, api_key="k"))


async def test_the_search_runs_before_the_models_first_round(hass: HomeAssistant) -> None:
    client = _client(hass)
    seen: list[list[dict[str, Any]]] = []

    async def _raw_stream(_system: str, messages: list, tools: Any = None, **_kw: Any):  # noqa: ANN202
        seen.append([dict(m) for m in messages])
        yield MagicMock()

    async def _stream(_resp: Any, _tool_calls: list, content_blocks: list):  # noqa: ANN202
        content_blocks.append({"type": "text", "text": ANSWER})
        yield ANSWER

    client._provider.raw_request_stream = _raw_stream
    client._provider.stream_with_tools = _stream
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)
    arguments = {"objects": ["car"], "query": "white truck", "hours": 168.0}
    with patch(
        "custom_components.selora_ai.frigate.events.async_find_events",
        AsyncMock(return_value=_EVENTS),
    ) as find:
        out = [
            chunk
            async for chunk in client._stream_request_with_tools(
                system="s",
                messages=[{"role": "user", "content": "q"}],
                tool_executor=executor,
                tools=[{"name": "t"}],
                opening_search=arguments,
            )
        ]

    find.assert_awaited_once()
    assert find.await_args.args[1] == arguments
    # One model round: the one that reads the events.
    assert len(seen) == 1
    _user, assistant, result = seen[0]
    (use,) = assistant["content"]
    assert use["type"] == "tool_use"
    assert use["name"] == "find_camera_events"
    assert use["input"] == arguments
    assert result["content"][0]["tool_use_id"] == use["id"]
    assert "1793470197.253093-1wv7iz" in result["content"][0]["content"]
    # The card and the history read the call log, as for the model's own call.
    assert executor.call_log[0]["tool"] == "find_camera_events"
    assert ANSWER in "".join(c for c in out if isinstance(c, str))


async def test_the_search_written_from_the_message_never_ends_on_a_card(
    hass: HomeAssistant,
) -> None:
    """A misread question must not end on "make every camera look for dogs"."""
    client = _client(hass)

    async def _raw_stream(_system: str, _messages: list, tools: Any = None, **_kw: Any):  # noqa: ANN202
        yield MagicMock()

    async def _stream(_resp: Any, _tool_calls: list, content_blocks: list):  # noqa: ANN202
        content_blocks.append({"type": "text", "text": ANSWER})
        yield ANSWER

    client._provider.raw_request_stream = _raw_stream
    client._provider.stream_with_tools = _stream
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)
    executor.camera_question = False
    unwatched = {"events": [], "offer": "No camera looks for dog: front_door could."}
    with (
        patch(
            "custom_components.selora_ai.frigate.events.async_find_events",
            AsyncMock(return_value=unwatched),
        ),
        patch("custom_components.selora_ai.frigate.events.async_offer_card", AsyncMock()) as offer,
    ):
        _ = [
            chunk
            async for chunk in client._stream_request_with_tools(
                system="s",
                messages=[{"role": "user", "content": "q"}],
                tool_executor=executor,
                tools=[{"name": "t"}],
                opening_search={"objects": ["dog"], "hours": 168.0},
            )
        ]

    offer.assert_not_awaited()
    assert [c["tool"] for c in executor.call_log] == ["find_camera_events"]
    # The model's own search, later in the turn, still offers it.
    assert executor.offer_camera_card is True


async def test_the_search_for_a_camera_question_still_offers_its_card(
    hass: HomeAssistant,
) -> None:
    """ "Have you seen a dog?" opened by the shortcut keeps the change card."""
    client = _client(hass)

    async def _raw_stream(_system: str, _messages: list, tools: Any = None, **_kw: Any):  # noqa: ANN202
        yield MagicMock()

    async def _stream(_resp: Any, _tool_calls: list, content_blocks: list):  # noqa: ANN202
        content_blocks.append({"type": "text", "text": ANSWER})
        yield ANSWER

    client._provider.raw_request_stream = _raw_stream
    client._provider.stream_with_tools = _stream
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)
    executor.camera_question = True
    unwatched = {"events": [], "offer": "No camera looks for dog: front_door could."}
    with (
        patch(
            "custom_components.selora_ai.frigate.events.async_find_events",
            AsyncMock(return_value=unwatched),
        ),
        patch(
            "custom_components.selora_ai.frigate.events.async_offer_card",
            AsyncMock(return_value=[]),
        ) as offer,
    ):
        _ = [
            chunk
            async for chunk in client._stream_request_with_tools(
                system="s",
                messages=[{"role": "user", "content": "q"}],
                tool_executor=executor,
                tools=[{"name": "t"}],
                opening_search={"objects": ["dog"], "hours": 168.0},
            )
        ]

    offer.assert_awaited_once()


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Have you seen a white dog?", True),
        ("Was there a dog in the yard today?", True),
        ("Which camera saw a bus?", True),
        ("Did anyone come by today?", True),
        ("Was the dog fed?", False),
        ("Has the car been charged today?", False),
        ("How many people are home?", False),
    ],
)
def test_only_a_question_about_the_cameras_may_end_on_their_card(
    message: str, expected: bool
) -> None:
    assert points_at_cameras(message) is expected


@pytest.mark.parametrize("offer", [True, False])
async def test_the_models_search_offers_the_card_only_when_allowed(
    hass: HomeAssistant, offer: bool
) -> None:
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)
    executor.offer_camera_card = offer
    unwatched = {"events": [], "offer": "No camera looks for dog: front_door could."}
    proposed = [{"tool": "set_camera_objects", "arguments": {}, "result": {}}]
    with (
        patch(
            "custom_components.selora_ai.frigate.events.async_find_events",
            AsyncMock(return_value=unwatched),
        ),
        patch(
            "custom_components.selora_ai.frigate.events.async_offer_card",
            AsyncMock(return_value=proposed),
        ),
    ):
        await executor.execute("find_camera_events", {"objects": ["dog"]})

    tools = [c["tool"] for c in executor.call_log]
    assert ("set_camera_objects" in tools) is offer


async def test_no_frigate_no_opening_search(hass: HomeAssistant) -> None:
    client = _client(hass)
    with patch("custom_components.selora_ai.frigate.client.frigate_available", return_value=False):
        assert await client._opening_camera_search("Any FedEx truck today?", None) is None


async def test_a_lane_without_the_search_gets_none(hass: HomeAssistant) -> None:
    client = _client(hass)
    with patch("custom_components.selora_ai.frigate.client.frigate_available", return_value=True):
        assert await client._opening_camera_search("Any FedEx truck today?", "command") is None


async def test_the_search_reads_the_homes_cameras_and_names(hass: HomeAssistant) -> None:
    client = _client(hass)
    with (
        patch("custom_components.selora_ai.frigate.client.frigate_available", return_value=True),
        patch(
            "custom_components.selora_ai.frigate.events.async_known_names",
            AsyncMock(return_value=["Sam"]),
        ),
        patch(
            "custom_components.selora_ai.frigate.events.async_camera_names",
            AsyncMock(return_value=CAMERAS),
        ),
    ):
        arguments = await client._opening_camera_search(
            "Was Sam at the front door in the last 2 hours?", None
        )

    assert arguments == {
        "names": ["Sam"],
        "cameras": ["front_door"],
        "hours": 2.0,
    }
