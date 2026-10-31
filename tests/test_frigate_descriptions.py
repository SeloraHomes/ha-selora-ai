"""Choosing which Frigate cameras Selora AI describes, and the budget behind it."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse
import yaml

from custom_components.selora_ai import _apply_destructive_actions
from custom_components.selora_ai.const import DOMAIN
from custom_components.selora_ai.frigate.client import SELORA_HUB_HOST
from custom_components.selora_ai.frigate.descriptions import (
    _APPLY_AFTER_S,
    _APPLY_TIMER,
    async_answer,
    async_describe_suggestions,
    async_start,
    cancel_apply,
    selora_describes,
)
from custom_components.selora_ai.frigate.events import async_find_events
from custom_components.selora_ai.llm_client.command_policy import (
    _DESTRUCTIVE_TOOLS,
    _pending_destructive_from_log,
    synthesize_approval_from_tool_log,
)
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import (
    TOOL_GET_CAMERA_DESCRIPTIONS,
    TOOL_SET_CAMERA_DESCRIPTIONS,
)
from custom_components.selora_ai.providers.selora_cloud import SeloraCloudProvider
from custom_components.selora_ai.selora_auth import SeloraAuthContext
from custom_components.selora_ai.tool_executor import ToolExecutor
from custom_components.selora_ai.tool_registry import COMMAND_TOOL_NAMES, CONFIG_TOOL_NAMES

FRIGATE = "http://frigate.local:5000"
HUB_FRIGATE = f"http://{SELORA_HUB_HOST}:5000"
NOW = dt_util.utcnow().timestamp()

_GLOBAL_GENAI = {"enabled": False, "objects": ["car", "person"], "use_snapshot": False}
# The provider Selora OS writes, as Frigate runs it: Connect names it, and its
# address on Connect's gateway is what makes it Selora's.
_GATEWAY = "https://connect.example/api/v1/ai-gateway/v1"
_SELORA = {"provider": "openai", "base_url": _GATEWAY, "model": "selora", "roles": ["descriptions"]}


def _running(*, provider: bool = True, describes: tuple[str, ...] = ()) -> dict[str, Any]:
    """Frigate's running config: the global ``objects.genai`` merged into
    every camera, so a camera nobody was asked about reads ``false`` here."""

    def camera(name: str, friendly: str, zones: dict[str, Any]) -> dict[str, Any]:
        return {
            "friendly_name": friendly,
            "zones": zones,
            "objects": {"genai": {**_GLOBAL_GENAI, "enabled": name in describes}},
        }

    config: dict[str, Any] = {
        "cameras": {
            "driveway": camera(
                "driveway", "Front Driveway", {"street": {"friendly_name": "Street"}}
            ),
            "living_room": camera("living_room", "Living Room", {}),
            "garage": camera("garage", "Garage", {}),
        },
        "objects": {"genai": _GLOBAL_GENAI},
        "semantic_search": {"enabled": True},
    }
    if provider:
        config["genai"] = {"Selora": _SELORA}
    return config


def _saved(answers: dict[str, dict[str, Any]]) -> str:
    """Frigate's saved file, with only the answers recorded on each camera."""
    cameras: dict[str, Any] = {"driveway": {}, "living_room": {}, "garage": {}}
    for name, genai in answers.items():
        cameras[name] = {"objects": {"genai": genai}}
    saved = {
        "cameras": cameras,
        "genai": {"Selora": {**_SELORA, "api_key": "sk-secret"}},
    }
    return json.dumps(yaml.safe_dump(saved))


@pytest.fixture
def frigate(hass: HomeAssistant) -> Iterator[MockConfigEntry]:
    entry = MockConfigEntry(domain="frigate", data={"url": FRIGATE})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    yield entry
    # An answer left mid-flow arms a restart; the entry's unload would drop it.
    cancel_apply(hass)


def _api(
    aioclient_mock: Any,
    running: dict[str, Any],
    answers: dict[str, dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    *,
    before: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Frigate's API; its saved file holds *before* until a write, then *answers*."""
    aioclient_mock.get(f"{FRIGATE}/api/config", json=running)
    if before is None:
        aioclient_mock.get(f"{FRIGATE}/api/config/raw", text=_saved(answers or {}))
    else:

        async def _raw(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
            written = bool(_written(aioclient_mock))
            return AiohttpClientMockResponse(
                method, url, text=_saved((answers or {}) if written else before)
            )

        aioclient_mock.get(f"{FRIGATE}/api/config/raw", side_effect=_raw)
    aioclient_mock.get(f"{FRIGATE}/api/events", json=events or [])
    aioclient_mock.get(f"{FRIGATE}/api/events/search", json=events or [])
    aioclient_mock.put(f"{FRIGATE}/api/config/set", json={"success": True})
    aioclient_mock.post(f"{FRIGATE}/api/restart", json={"success": True})


def _written(aioclient_mock: Any) -> list[dict[str, Any]]:
    return [
        c[2] if isinstance(c[2], dict) else json.loads(c[2])
        for c in aioclient_mock.mock_calls
        if str(c[1]).endswith("/api/config/set")
    ]


def _restarted(aioclient_mock: Any) -> bool:
    return any(str(c[1]).endswith("/api/restart") for c in aioclient_mock.mock_calls)


def _budget(hass: HomeAssistant, budget: dict[str, Any] | None) -> AsyncMock:
    """A Selora Cloud provider whose gateway reported *budget*."""
    refresh = AsyncMock()
    provider = SimpleNamespace(
        provider_type="selora_cloud", camera_descriptions=budget, async_refresh_capabilities=refresh
    )
    hass.data.setdefault(DOMAIN, {})["entry"] = {"llm": SimpleNamespace(provider=provider)}
    return refresh


async def _run(hass: HomeAssistant, tool: str, arguments: dict[str, Any]) -> tuple[dict, Any]:
    executor = ToolExecutor(hass, MagicMock(), is_admin=True)
    return await executor.execute(tool, arguments), executor


async def _card(hass: HomeAssistant, arguments: dict[str, Any]) -> tuple[dict, dict]:
    result, executor = await _run(hass, "set_camera_descriptions", arguments)
    assert result.get("requires_approval"), result
    (action,) = _pending_destructive_from_log(executor.call_log)
    return result, {**action, "fingerprint": result["destructive"]["fingerprint"]}


def test_the_provider_on_connects_gateway_with_descriptions_is_the_signal() -> None:
    assert selora_describes(_running())
    assert not selora_describes(_running(provider=False))
    # Found by its address, whatever Connect named it.
    assert selora_describes({"genai": {"anything": {**_SELORA, "base_url": _GATEWAY + "/"}}})
    # roles left out means every role, as Frigate reads it.
    assert selora_describes({"genai": {"Selora": {"base_url": _GATEWAY}}})
    assert not selora_describes({"genai": {"Selora": {**_SELORA, "roles": ["review"]}}})
    # Another provider describing is not Selora AI's feature, even named selora.
    assert not selora_describes(
        {"genai": {"selora": {"base_url": "http://ollama:11434", "roles": ["descriptions"]}}}
    )
    assert not selora_describes({"genai": {"local": {"roles": ["descriptions"]}}})


@pytest.fixture
def hub_frigate(hass: HomeAssistant) -> Iterator[MockConfigEntry]:
    """The Frigate Selora OS runs on the hub, at the host's address."""
    entry = MockConfigEntry(domain="frigate", data={"url": HUB_FRIGATE})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    yield entry
    cancel_apply(hass)


def _no_switch(text: str) -> None:
    """There is no setting for descriptions: off never says where to turn them on."""
    assert "turn them on in" not in text.lower(), text
    assert "in Selora Connect" not in text, text


async def test_off_on_a_frigate_that_is_not_the_hubs_says_so(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running(provider=False))

    result, _ = await _run(hass, "get_camera_descriptions", {})

    assert result["descriptions"] == "off"
    assert "not the hub's" in result["note"]
    _no_switch(result["note"])
    assert "never send" in result["how_to_answer"]
    assert "which cameras" in result["how_to_answer"]
    assert await async_describe_suggestions(hass) == []


@pytest.mark.parametrize(
    ("budget", "says"),
    [
        # Connect issued the key: the hub writes the provider on its next sync.
        ({"enabled": True}, "being set up"),
        # Connect reports the plan, so it includes Frigate, but no key yet.
        ({"enabled": False}, "has not set them up"),
        # Nothing from Connect (another plan, an older Connect, a failed lookup).
        (None, "every paid plan"),
    ],
)
async def test_off_on_the_hub_says_why_from_connect(
    hass: HomeAssistant,
    hub_frigate: MockConfigEntry,
    aioclient_mock: Any,
    budget: dict[str, Any] | None,
    says: str,
) -> None:
    aioclient_mock.get(f"{HUB_FRIGATE}/api/config", json=_running(provider=False))
    _budget(hass, budget)

    result, _ = await _run(hass, "get_camera_descriptions", {})

    assert result["descriptions"] == "off"
    assert says in result["note"]
    _no_switch(result["note"])


async def test_off_on_the_hub_without_selora_cloud_says_how_it_gets_set_up(
    hass: HomeAssistant, hub_frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    aioclient_mock.get(f"{HUB_FRIGATE}/api/config", json=_running(provider=False))

    result, _ = await _run(hass, "get_camera_descriptions", {})

    assert "Selora support" in result["note"]
    _no_switch(result["note"])


async def test_answers_come_from_the_saved_file_not_the_merged_config(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(
        aioclient_mock,
        _running(describes=("driveway",)),
        {"driveway": {"enabled": True, "required_zones": ["street"]}, "garage": {"enabled": False}},
    )

    result, _ = await _run(hass, "get_camera_descriptions", {})

    cameras = {c["camera"]: c for c in result["cameras"]}
    assert cameras["driveway"]["describes"] == "yes"
    assert cameras["driveway"]["only_zones"] == ["street"]
    assert cameras["driveway"]["zones"] == [{"zone": "street", "name": "Street"}]
    assert cameras["garage"]["describes"] == "no"
    # The running config says false for it too; only the file tells it apart.
    assert cameras["living_room"]["describes"] == "not asked"
    assert "indoor" in result["how_to_ask"]
    # The tool picks the camera, so the model asks about it rather than which.
    assert result["ask_next"] == "living_room"
    # The provider key in the saved file never reaches the model.
    assert "sk-secret" not in json.dumps(result)


async def test_without_connect_numbers_the_rules_come_with_no_number(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running())

    result, _ = await _run(hass, "get_camera_descriptions", {})

    budget = result["budget"]
    assert any("60%" in rule for rule in budget["rules"])
    assert any("Top-ups" in rule for rule in budget["rules"])
    assert "no numbers" in budget["numbers"]
    assert "objects_left_today" not in budget


async def test_connect_numbers_are_told_in_objects_with_why_it_paused(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(
        aioclient_mock,
        _running(describes=("driveway",)),
        {"driveway": {"enabled": True}},
        events=[{"id": f"{NOW}-a", "camera": "driveway"}] * 3
        + [{"id": f"{NOW}-b", "camera": "garage"}],
    )
    refresh = _budget(
        hass,
        {
            "enabled": True,
            "daily_budget_tokens": 400000,
            "used_today_tokens": 120000,
            "est_tokens_per_object": 3200,
            "monthly_share": 0.6,
            "plan_used_fraction": 0.34,
            "paused": "daily_budget",
            "period_end": "2026-11-14T00:00:00Z",
        },
    )

    result, _ = await _run(hass, "get_camera_descriptions", {})

    budget = result["budget"]
    assert budget["objects_per_day"] == 125
    assert budget["objects_left_today"] == 87
    assert budget["month_used_percent"] == 34
    assert "midnight UTC" in budget["status"]
    # A budget question asks for numbers fresher than the capabilities TTL.
    assert refresh.await_args.kwargs["max_age"] <= 60
    assert result["busiest"] == "driveway"
    assert "75%" in result["offer"]


async def test_a_budget_kept_for_the_assistant_resumes_at_the_period_end(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running())
    _budget(
        hass,
        {
            "daily_budget_tokens": 400000,
            "used_today_tokens": 0,
            "est_tokens_per_object": 3200,
            "monthly_share": 0.6,
            "paused": "chat_priority",
            "period_end": "2026-11-14T00:00:00Z",
        },
    )

    result, _ = await _run(hass, "get_camera_descriptions", {})

    assert "kept for the assistant" in result["budget"]["status"]
    assert result["budget"]["resumes"].startswith("2026-11-1")


async def test_a_yes_goes_on_a_card_that_writes_only_the_camera(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running())

    result, action = await _card(
        hass, {"camera": "Front Driveway", "describe": True, "zones": ["Street"]}
    )

    label = result["destructive"]["label"]
    assert label.startswith(
        "Restart Frigate so Selora AI describes the people and vehicles Front Driveway "
        "sees, in street only"
    )
    # The first camera turned on says what it costs.
    assert "allowance" in label
    assert _written(aioclient_mock) == []

    applied, errors = await _apply_destructive_actions(hass, [action])

    assert errors == []
    assert applied == [
        "Selora AI describes what Front Driveway sees in street. Frigate is restarting, about a minute."
    ]
    (body,) = _written(aioclient_mock)
    assert body["config_data"] == {
        "cameras": {
            "driveway": {"objects": {"genai": {"enabled": True, "required_zones": ["street"]}}}
        }
    }
    assert body["requires_restart"] == 1
    assert _restarted(aioclient_mock)


async def test_the_second_camera_card_does_not_repeat_the_budget(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running(describes=("driveway",)), {"driveway": {"enabled": True}})

    result, _ = await _card(hass, {"camera": "garage", "describe": True})

    assert "allowance" not in result["destructive"]["label"]


async def test_a_zone_the_camera_does_not_have_is_refused(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running())

    result, _ = await _run(
        hass, "set_camera_descriptions", {"camera": "driveway", "describe": True, "zones": ["lawn"]}
    )

    assert "no zone 'lawn'" in result["error"]
    assert "Street" in result["error"]


async def test_a_no_is_recorded_at_once_without_a_restart(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running())

    result, _ = await _run(
        hass, "set_camera_descriptions", {"camera": "living_room", "describe": False}
    )

    assert result["recorded"] is True
    (body,) = _written(aioclient_mock)
    assert body == {
        "config_data": {"cameras": {"living_room": {"objects": {"genai": {"enabled": False}}}}},
        "requires_restart": 0,
    }
    assert not _restarted(aioclient_mock)


async def test_turning_a_described_camera_off_needs_a_card(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running(describes=("living_room",)), {"living_room": {"enabled": True}})

    result, action = await _card(hass, {"camera": "living_room", "describe": False})

    assert "stops describing what Living Room sees" in result["destructive"]["label"]
    applied, errors = await _apply_destructive_actions(hass, [action])
    assert errors == []
    assert _written(aioclient_mock)[0]["config_data"] == {
        "cameras": {"living_room": {"objects": {"genai": {"enabled": False}}}}
    }


async def test_an_answer_already_given_needs_no_card(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running(), {"garage": {"enabled": False}})

    result, _ = await _run(hass, "set_camera_descriptions", {"camera": "garage", "describe": False})

    assert result["unchanged"] is True
    assert _written(aioclient_mock) == []


async def test_an_answer_changed_since_the_card_is_not_written(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running())
    _result, action = await _card(hass, {"camera": "driveway", "describe": True})
    aioclient_mock.clear_requests()
    _api(aioclient_mock, _running(), {"driveway": {"enabled": False}})

    _applied, errors = await _apply_destructive_actions(hass, [action])

    assert "changed since it was shown" in errors[0]
    assert _written(aioclient_mock) == []


async def test_descriptions_off_refuse_a_yes_and_say_why(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running(provider=False))

    result, _ = await _run(
        hass, "set_camera_descriptions", {"camera": "driveway", "describe": True}
    )

    assert "not the hub's" in result["error"]
    assert _written(aioclient_mock) == []


async def test_the_suggestion_names_the_cameras_never_asked_about(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running(), {"garage": {"enabled": False}})

    (suggestion,) = await async_describe_suggestions(hass)

    assert suggestion["type"] == "describe_cameras"
    # Named by its cameras, so a dismissal holds until a new one arrives.
    assert suggestion["suggestion_id"] == "describe:driveway,living_room"
    assert [e["name"] for e in suggestion["entities"]] == ["Front Driveway", "Living Room"]


async def test_the_suggestion_goes_once_every_camera_has_an_answer(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(
        aioclient_mock,
        _running(),
        {name: {"enabled": False} for name in ("driveway", "living_room", "garage")},
    )

    assert await async_describe_suggestions(hass) == []


def _event(camera: str, label: str, *, description: str | None, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"{NOW - 100:.6f}-{camera[:3]}{label[:3]}",
        "camera": camera,
        "label": label,
        "start_time": NOW - 100,
        "end_time": NOW - 90,
        "zones": [],
        "data": {"description": description} if description else {},
        **extra,
    }


async def test_a_search_says_what_was_not_described_and_why(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    events = [
        _event("driveway", "car", description="A white sedan."),
        _event("driveway", "car", description=None),
        # Dogs are not described, so a dog without one is not missing it.
        _event("driveway", "dog", description=None),
    ]
    _api(aioclient_mock, _running(describes=("driveway",)), events=events)
    _budget(
        hass,
        {
            "daily_budget_tokens": 400000,
            "used_today_tokens": 400000,
            "est_tokens_per_object": 3200,
            "paused": "daily_budget",
        },
    )

    result = await async_find_events(hass, {"query": "white car", "objects": ["car"]})

    assert result["undescribed"]["count"] == 1
    assert result["undescribed"]["by_camera"] == {"driveway": 1}
    assert "budget is used up" in result["undescribed"]["why"]
    assert "instead of presenting the results as complete" in result["undescribed"]["note"]
    assert set(result["cameras_not_described"]) == {"garage", "living_room"}


async def test_an_object_outside_the_required_zone_is_not_missing_a_description(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    running = _running(describes=("driveway",))
    running["cameras"]["driveway"]["objects"]["genai"]["required_zones"] = ["street"]
    events = [
        _event("driveway", "car", description=None),
        _event("driveway", "car", description=None, zones=["street"]),
    ]
    _api(aioclient_mock, running, events=events)

    result = await async_find_events(hass, {"objects": ["car"]})

    assert result["undescribed"]["count"] == 1
    # Without Connect's numbers it says only that one may still be coming.
    assert "may still be coming" in result["undescribed"]["why"]
    # A plain listing is not a description search: no list of cameras.
    assert "cameras_not_described" not in result


async def test_an_old_event_without_a_description_has_no_reason_made_up(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    old = _event("driveway", "car", description=None, start_time=NOW - 7200, end_time=NOW - 7100)
    _api(aioclient_mock, _running(describes=("driveway",)), events=[old])

    result = await async_find_events(hass, {"objects": ["car"]})

    assert "may still be coming" not in result["undescribed"]["why"]
    assert "reason is unknown" in result["undescribed"]["why"]


async def test_both_tools_are_in_both_lanes_and_the_change_holds_for_a_card() -> None:
    for name in ("get_camera_descriptions", "set_camera_descriptions"):
        assert name in COMMAND_TOOL_NAMES
        assert name in CONFIG_TOOL_NAMES
    assert "set_camera_descriptions" in _DESTRUCTIVE_TOOLS


class _Response:
    def __init__(self, status: int, data: dict[str, Any] | None = None) -> None:
        self.status = status
        self._data = data or {}

    async def json(self) -> dict[str, Any]:
        return self._data

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


def _provider(hass: HomeAssistant, response: _Response) -> SeloraCloudProvider:
    provider = SeloraCloudProvider(hass, access_token="t")
    provider._get_session = lambda: SimpleNamespace(get=lambda *_a, **_k: response)  # type: ignore[method-assign]
    provider._ensure_token = AsyncMock()  # type: ignore[method-assign]
    return provider


async def test_the_gateway_budget_is_kept_from_the_capabilities_fetch(hass: HomeAssistant) -> None:
    budget = {"enabled": True, "daily_budget_tokens": 400000}
    provider = _provider(hass, _Response(200, {"camera_descriptions": budget}))

    await provider.async_refresh_capabilities()

    assert provider.camera_descriptions == budget


async def test_an_older_gateway_reports_no_budget(hass: HomeAssistant) -> None:
    provider = _provider(hass, _Response(200, {"supports_vision": True}))
    await provider.async_refresh_capabilities()
    assert provider.camera_descriptions is None


def _asked(**extra: Any) -> dict[str, Any]:
    return {
        "tool": "get_camera_descriptions",
        "arguments": {},
        "result": {
            "ask_next": "e1_zoom",
            "cameras": [
                {
                    "camera": "e1_zoom",
                    "name": "E1 Zoom",
                    "describes": "not asked",
                    "zones": [{"zone": "drive", "name": "Driveway"}],
                }
            ],
            **extra,
        },
    }


def test_the_question_about_a_camera_comes_with_its_answers() -> None:
    reply = {"intent": "answer", "response": "Should I describe E1 Zoom?"}

    out = synthesize_approval_from_tool_log(reply, [_asked()], language="en")

    assert [(a["label"], a["value"]) for a in out["quick_actions"]] == [
        ("Turn descriptions on", "Turn descriptions on for E1 Zoom"),
        ("Leave it off", "Leave E1 Zoom off"),
        ("Only in Driveway", "Describe E1 Zoom, only what enters Driveway"),
    ]
    # Drawn under the question, not above the composer.
    assert all(a["inline"] for a in out["quick_actions"])
    assert out["quick_actions"][0]["tone"] == "approve"
    french = synthesize_approval_from_tool_log(reply, [_asked()], language="fr-CA")
    assert french["quick_actions"][1]["value"] == "Laisse E1 Zoom désactivée"


def test_no_answers_once_answered_or_when_the_model_gave_its_own() -> None:
    reply = {"intent": "answer", "response": "Recorded."}
    answered = {"tool": "set_camera_descriptions", "arguments": {}, "result": {"recorded": True}}

    assert "quick_actions" not in synthesize_approval_from_tool_log(reply, [_asked(), answered])
    own = [{"label": "Later", "value": "Later", "mode": "confirmation"}]
    kept = synthesize_approval_from_tool_log({**reply, "quick_actions": own}, [_asked()])
    assert kept["quick_actions"] == own


async def test_a_clicked_answer_is_saved_and_the_next_camera_asked_without_the_model(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    after = {"driveway": {"enabled": True, "required_zones": ["street"]}}
    _api(aioclient_mock, _running(), after, before={})

    result = await async_answer(hass, "driveway", True, ["street"], "en")

    assert result["said"] == "Describe Front Driveway, only what enters Street"
    (body,) = _written(aioclient_mock)
    assert body["config_data"] == {
        "cameras": {
            "driveway": {"objects": {"genai": {"enabled": True, "required_zones": ["street"]}}}
        }
    }
    # No card and no restart per camera: Frigate restarts after the last one.
    assert body["requires_restart"] == 0
    assert not _restarted(aioclient_mock)
    assert result["reply"] == "Should Selora AI describe what Garage sees?"
    assert result["quick_actions"][0]["camera_answer"] == {
        "camera": "garage",
        "describe": True,
        "zones": [],
    }


async def test_the_last_answer_restarts_frigate_once_if_it_changes_what_runs(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    every = {
        "driveway": {"enabled": True},
        "garage": {"enabled": False},
        "living_room": {"enabled": False},
    }
    earlier = {k: v for k, v in every.items() if k != "living_room"}
    _api(aioclient_mock, _running(), every, before=earlier)

    result = await async_answer(hass, "living_room", False, [], "fr")

    assert result["said"] == "Laisse Living Room désactivée"
    assert "Frigate redémarre" in result["reply"]
    # The panel shows how it ended with a tick, not as plain text.
    assert result["card"] == {"done": "restarting"}
    assert "quick_actions" not in result
    assert _restarted(aioclient_mock)

    aioclient_mock.clear_requests()
    _api(aioclient_mock, _running(describes=("driveway",)), every, before=earlier)
    result = await async_answer(hass, "living_room", False, [], "en")
    assert "already runs your answers" in result["reply"]
    assert not _restarted(aioclient_mock)


async def test_a_zone_the_camera_lacks_is_refused(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running())

    result = await async_answer(hass, "garage", True, ["street"], "en")

    assert "no zone" in result["error"]
    assert _written(aioclient_mock) == []


async def test_a_button_from_an_older_question_does_not_overwrite_its_answer(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running(), {"driveway": {"enabled": False}})

    result = await async_answer(hass, "driveway", True, [], "en")

    assert "already has an answer" in result["error"]
    assert _written(aioclient_mock) == []


async def test_a_flow_left_before_its_last_camera_still_gets_applied(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running(), {"driveway": {"enabled": True}}, before={})

    result = await async_answer(hass, "driveway", True, [], "en")
    assert "quick_actions" in result
    assert not _restarted(aioclient_mock)

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=_APPLY_AFTER_S + 1))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _restarted(aioclient_mock)


async def test_the_last_answer_drops_the_pending_restart(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running(), {"driveway": {"enabled": True}}, before={})
    await async_answer(hass, "driveway", True, [], "en")
    assert _APPLY_TIMER in hass.data[DOMAIN]

    every = {"driveway": {"enabled": True}, "garage": {"enabled": False}}
    aioclient_mock.clear_requests()
    _api(aioclient_mock, _running(), {**every, "living_room": {"enabled": False}}, before=every)
    await async_answer(hass, "living_room", False, [], "en")

    assert _APPLY_TIMER not in hass.data[DOMAIN]
    assert sum(str(c[1]).endswith("/api/restart") for c in aioclient_mock.mock_calls) == 1


async def test_the_websocket_answer_keeps_both_sides_in_the_conversation(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    import inspect

    from custom_components.selora_ai.conversation_store import ConversationStore
    from custom_components.selora_ai.websocket import frigate as frigate_ws

    _api(aioclient_mock, _running(), {"driveway": {"enabled": False}}, before={})
    store = ConversationStore(hass)
    hass.data.setdefault(DOMAIN, {})["_conv_store"] = store
    session = await store.create_session()
    handler = inspect.unwrap(frigate_ws._handle_websocket_frigate_describe_answer)
    connection = MagicMock()
    connection.user.is_admin = True

    msg = {"id": 1, "session_id": session["id"], "camera": "driveway", "describe": False}
    await handler(hass, connection, {**msg, "zones": []})

    said, reply = connection.send_result.call_args[0][1]["messages"]
    assert (said["role"], said["content"]) == ("user", "Leave Front Driveway off")
    assert reply["quick_actions"][0]["camera_answer"]["camera"] == "garage"
    # The next question comes with its camera's snapshot, without the opening.
    assert reply["describe_card"] == {
        "camera": "garage",
        "camera_name": "Garage",
        "step": "Camera 2 of 3",
        "question": "Should Selora AI describe what Garage sees?",
    }
    stored = (await store.get_session(session["id"]))["messages"]
    assert [m["content"] for m in stored] == [said["content"], reply["content"]]


async def test_the_suggestion_opens_a_chat_written_without_the_model(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    import inspect

    from custom_components.selora_ai.conversation_store import ConversationStore
    from custom_components.selora_ai.websocket import frigate as frigate_ws

    _api(aioclient_mock, _running(), {"driveway": {"enabled": True}})
    store = ConversationStore(hass)
    hass.data.setdefault(DOMAIN, {})["_conv_store"] = store
    handler = inspect.unwrap(frigate_ws._handle_websocket_frigate_describe_start)
    connection = MagicMock()
    connection.user.is_admin = True

    title = "Help me choose which cameras Selora AI describes"
    await handler(hass, connection, {"id": 1, "title": title, "language": "en-US"})

    session_id = connection.send_result.call_args[0][1]["session_id"]
    asked, opening = (await store.get_session(session_id))["messages"]
    assert (asked["role"], asked["content"]) == ("user", title)
    # What it costs and what leaves the house, then the first unanswered camera.
    assert "up to 60% of your monthly" in opening["content"]
    assert "indoor camera is best left off" in opening["content"]
    assert opening["content"].endswith("Should Selora AI describe what Garage sees?")
    # The panel shows the same as a card: the example, then the camera's question.
    card = opening["describe_card"]
    assert (card["camera"], card["camera_name"]) == ("garage", "Garage")
    assert card["question"] == "Should Selora AI describe what Garage sees?"
    assert card["intro"]["image"] == "/api/selora_ai/images/describe-example.jpg"
    # The heading gets a short line; the lead stays in the text, for the model.
    assert card["intro"]["tagline"] == "Search what your cameras saw, in plain words."
    assert opening["content"].startswith(card["intro"]["lead"])
    assert [p["text"] for p in card["intro"]["points"]] == opening["content"].split("\n\n")[1:-1]
    assert opening["quick_actions"][1]["camera_answer"] == {
        "camera": "garage",
        "describe": False,
        "zones": [],
    }


async def test_the_opening_gives_the_daily_budget_in_the_reply_language(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running())
    raw = {"daily_budget_tokens": 40000, "used_today_tokens": 0, "est_tokens_per_object": 1000}
    _budget(hass, {**raw, "monthly_share": 0.6})

    opening = await async_start(hass, "fr")

    points = [p["text"] for p in opening["card"]["intro"]["points"]]
    assert "60 % de votre forfait Selora AI du mois, jusqu'à 40 descriptions par jour" in points[2]
    assert opening["card"]["question"] == "Selora AI doit-il décrire ce que voit Front Driveway ?"


async def test_nothing_to_open_once_every_camera_has_an_answer(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    every = {name: {"enabled": False} for name in ("driveway", "garage", "living_room")}
    _api(aioclient_mock, _running(), every)

    assert "already has an answer" in (await async_start(hass, "en"))["error"]


# ── Over MCP ─────────────────────────────────────────────────────────────────


async def _mcp(hass: HomeAssistant, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await mcp_dispatch._jsonrpc_dispatch(
        hass,
        "tools/call",
        {"name": tool, "arguments": arguments},
        SeloraAuthContext("admin", None, True, "ha_token"),
    )
    return json.loads(result["content"][0]["text"])


async def test_over_mcp_the_cameras_are_asked_about_without_buttons(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running())

    result = await _mcp(hass, TOOL_GET_CAMERA_DESCRIPTIONS)

    assert result["ask_next"] == "driveway"
    assert "selora_set_camera_descriptions" in result["how_to_ask"]
    assert "buttons" not in result["how_to_ask"]
    assert "sk-secret" not in json.dumps(result)


async def test_over_mcp_a_yes_is_confirmed_in_two_calls(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running())

    asked = await _mcp(hass, TOOL_SET_CAMERA_DESCRIPTIONS, camera="Front Driveway", describe=True)

    assert asked["requires_confirmation"] is True
    assert asked["camera"] == "driveway"
    assert "allowance" in asked["change"]
    assert _written(aioclient_mock) == []
    assert "error" in await _mcp(
        hass, TOOL_SET_CAMERA_DESCRIPTIONS, camera="driveway", describe=True, confirmed=True
    )

    done = await _mcp(
        hass,
        TOOL_SET_CAMERA_DESCRIPTIONS,
        camera="driveway",
        describe=True,
        confirmed=True,
        confirm_token=asked["confirm_token"],
    )

    assert done["done"].startswith("Selora AI describes what Front Driveway sees")
    (body,) = _written(aioclient_mock)
    assert body["config_data"] == {
        "cameras": {"driveway": {"objects": {"genai": {"enabled": True}}}}
    }
    assert _restarted(aioclient_mock)


async def test_over_mcp_a_no_to_a_camera_never_on_needs_no_confirmation(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, _running())

    result = await _mcp(hass, TOOL_SET_CAMERA_DESCRIPTIONS, camera="living_room", describe=False)

    assert result["recorded"] is True
    assert not _restarted(aioclient_mock)
