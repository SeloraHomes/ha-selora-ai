"""Tests for how Frigate analyses each camera: the reports, the Health check
built on them, and their place in the audit record the export reads."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai import insights_checks
from custom_components.selora_ai.frigate import analysis
from custom_components.selora_ai.frigate.analysis import (
    CameraReports,
    async_camera_reports,
    async_stop_probes,
)
from custom_components.selora_ai.insights_checks import async_run_checks

FRIGATE = "http://frigate.local:5000"

# What Frigate 0.18's ffprobe answers for an input, trimmed to what is read.
_H265_4K = {
    "return_code": 0,
    "stderr": "[]",
    "stdout": {
        "streams": [
            {
                "avg_frame_rate": "20/1",
                "codec_long_name": "H.265 / HEVC (High Efficiency Video Coding)",
                "width": 3840,
                "height": 2160,
            },
            {
                "avg_frame_rate": "0/0",
                "codec_long_name": "AAC (Advanced Audio Coding)",
                "width": None,
                "height": None,
            },
        ]
    },
}
_H264_720P = {
    "return_code": 0,
    "stderr": "[]",
    "stdout": {
        "streams": [{"codec_long_name": "H.264 / AVC / MPEG-4 AVC", "width": 1280, "height": 720}]
    },
}
_NO_ANSWER = {
    "return_code": 1,
    "stderr": "rtsp://192.168.1.54/x: Connection timed out",
    "stdout": "",
}


def _camera(
    roles: list[list[str]], detect: tuple[int, int] = (1280, 720), **extra: Any
) -> dict[str, Any]:
    return {
        "friendly_name": extra.pop("friendly_name", None),
        "enabled": extra.pop("enabled", True),
        "ffmpeg": {
            "inputs": [
                {"path": f"rtsp://127.0.0.1:8554/cam_{i}", "roles": r} for i, r in enumerate(roles)
            ]
        },
        "detect": {"enabled": True, "width": detect[0], "height": detect[1], "fps": 5},
        **extra,
    }


def _stats(**cameras: dict[str, Any]) -> dict[str, Any]:
    usages: dict[str, Any] = {}
    out: dict[str, Any] = {}
    for name, rates in cameras.items():
        pid = 1000 + len(out)
        usages[str(pid)] = {"cpu": str(rates.pop("detect_cpu", 10.0)), "mem": "1.0"}
        out[name] = {
            "camera_fps": 5.0,
            "process_fps": 5.0,
            "skipped_fps": 0.0,
            "detection_fps": 1.0,
            "pid": pid,
            **rates,
        }
    return {"cameras": out, "cpu_usages": usages}


@pytest.fixture
def frigate(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain="frigate", data={"url": FRIGATE})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry


def _api(
    aioclient_mock: Any,
    cameras: dict[str, Any],
    stats: dict[str, Any] | None = None,
    probes: dict[str, list[Any]] | None = None,
) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", json={"cameras": cameras})
    aioclient_mock.get(f"{FRIGATE}/api/stats", json=stats or _stats(**{n: {} for n in cameras}))
    for name, answer in (probes or {}).items():
        aioclient_mock.get(
            f"{FRIGATE}/api/ffprobe", params={"paths": f"camera:{name}"}, json=answer
        )


async def _reports(hass: HomeAssistant, *, probed: bool = True) -> list[Any]:
    """Two audits: the first starts the probes, the second reads them."""
    first = await async_camera_reports(hass)
    if not probed:
        return first.reports or []
    state = analysis._state(hass)
    if state.task is not None:
        await state.task
    second = await async_camera_reports(hass)
    assert second.error is None
    return second.reports or []


def _frigate_camera_entity(hass: HomeAssistant, entry: MockConfigEntry, name: str) -> str:
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("frigate", f"{entry.entry_id}:{name}")}
    )
    return (
        er.async_get(hass)
        .async_get_or_create(
            "camera",
            "frigate",
            f"{entry.entry_id}:camera:{name}",
            config_entry=entry,
            device_id=device.id,
            suggested_object_id=name,
        )
        .entity_id
    )


async def test_without_frigate_there_is_nothing_to_report(hass: HomeAssistant) -> None:
    assert await async_camera_reports(hass) == CameraReports(None)


async def test_a_camera_analysed_in_4k_from_a_4k_substream(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    """Front Door before the fix: no detect size, so Frigate took the 4K."""
    entity_id = _frigate_camera_entity(hass, frigate, "front_door")
    _api(
        aioclient_mock,
        {"front_door": _camera([["record"], ["detect"]], (3840, 2160), friendly_name="Front Door")},
        probes={"front_door": [_H265_4K, _H265_4K]},
    )

    (report,) = await _reports(hass)

    assert report["name"] == "Front Door"
    assert report["entity_id"] == entity_id
    assert report["device_id"] is not None
    assert report["detect"] == {"width": 3840, "height": 2160, "fps": 5}
    assert report["streams"][1] == {
        "roles": ["detect"],
        "width": 3840,
        "height": 2160,
        "codec": "h265",
        "readable": True,
    }
    assert report["streams_measured_at"] is not None
    assert report["issues"] == ["detect_above_720p", "detect_stream_heavy"]


async def test_a_camera_analysed_from_its_720p_substream_is_fine(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(
        aioclient_mock,
        {"storage": _camera([["record"], ["detect"]])},
        probes={"storage": [_H265_4K, _H264_720P]},
    )

    (report,) = await _reports(hass)

    assert report["issues"] == []
    assert [(s["width"], s["height"]) for s in report["streams"]] == [(3840, 2160), (1280, 720)]


async def test_before_a_probe_the_streams_are_unmeasured(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(aioclient_mock, {"yard": _camera([["record", "detect"]], (2560, 1440))})

    with patch.object(analysis, "_start_probes"):
        (report,) = await _reports(hass, probed=False)

    assert report["streams"] == [
        {
            "roles": ["record", "detect"],
            "width": None,
            "height": None,
            "codec": None,
            "readable": None,
        }
    ]
    assert report["streams_measured_at"] is None
    # The detect size is Frigate's own config: it needs no probe.
    assert report["issues"] == ["detect_above_720p"]


async def test_a_detect_stream_that_does_not_answer_twice_is_unreadable(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(
        aioclient_mock,
        {"e1_zoom": _camera([["record"], ["detect"]])},
        probes={"e1_zoom": [_H265_4K, _NO_ANSWER]},
    )

    (once,) = await _reports(hass)
    assert once["streams"][1]["readable"] is False
    assert "detect_stream_unreadable" not in once["issues"]

    # The retry comes an hour after a failure.
    probe = analysis._state(hass).probes["e1_zoom"]
    assert 3500 < probe.next_at - analysis.time.time() <= 3600
    probe.next_at = 0
    (twice,) = await _reports(hass)

    assert twice["issues"] == ["detect_stream_unreadable"]


async def test_a_stream_that_answers_without_its_size_is_not_unreadable(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    """Front Door on a test Frigate: ffprobe answered, but gave up on the size."""
    unsized = {
        "return_code": 0,
        "stdout": {
            "streams": [
                {"codec_long_name": "H.264 / AVC / MPEG-4 AVC", "width": 0, "height": 0},
                {"codec_long_name": "AAC (Advanced Audio Coding)", "width": None, "height": None},
            ]
        },
    }
    _api(
        aioclient_mock,
        {"front_door": _camera([["record", "detect", "audio"]], (960, 720))},
        probes={"front_door": [unsized]},
    )

    for _ in range(3):
        (report,) = await _reports(hass)
        analysis._state(hass).probes["front_door"].next_at = 0

    assert report["streams"] == [
        {
            "roles": ["record", "detect", "audio"],
            "width": None,
            "height": None,
            "codec": "h264",
            "readable": True,
        }
    ]
    assert report["issues"] == []


async def test_frigate_failing_to_probe_keeps_the_last_measurement(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(
        aioclient_mock,
        {"storage": _camera([["record"], ["detect"]])},
        probes={"storage": [_H265_4K, _H264_720P]},
    )
    (measured,) = await _reports(hass)
    analysis._state(hass).probes["storage"].next_at = 0

    with patch.object(
        analysis.FrigateClient, "ffprobe", side_effect=analysis.FrigateError("Frigate failed.")
    ):
        (after,) = await _reports(hass)

    assert after["streams"] == measured["streams"]
    assert after["streams_measured_at"] == measured["streams_measured_at"]
    assert analysis._state(hass).probes["storage"].failures == 0


async def test_live_symptoms_count_only_once_they_hold(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(
        aioclient_mock,
        {"front_door": _camera([["record", "detect"]])},
        stats=_stats(front_door={"skipped_fps": 2.0, "detect_cpu": 146.8}),
    )
    clock = [1_000_000.0]

    with (
        patch.object(analysis.time, "time", side_effect=lambda: clock[0]),
        patch.object(analysis, "_start_probes"),
    ):
        (first,) = await _reports(hass, probed=False)
        clock[0] += 600
        (held,) = await _reports(hass, probed=False)

    assert first["issues"] == []
    assert held["issues"] == ["skipping_frames", "high_cpu"]
    assert held["stats"]["detect_cpu"] == 146.8


async def test_a_symptom_that_stops_starts_its_clock_again(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    clock = [1_000_000.0]
    with (
        patch.object(analysis.time, "time", side_effect=lambda: clock[0]),
        patch.object(analysis, "_start_probes"),
    ):
        for skipped in (2.0, 0.0, 2.0):
            aioclient_mock.clear_requests()
            _api(
                aioclient_mock,
                {"yard": _camera([["record", "detect"]])},
                stats=_stats(yard={"skipped_fps": skipped}),
            )
            (report,) = await _reports(hass, probed=False)
            clock[0] += 400

    assert report["issues"] == []


async def test_a_confirmed_symptom_stands_while_the_rates_are_missing(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    clock = [1_000_000.0]
    with (
        patch.object(analysis.time, "time", side_effect=lambda: clock[0]),
        patch.object(analysis, "_start_probes"),
    ):
        _api(
            aioclient_mock,
            {"yard": _camera([["record", "detect"]])},
            stats=_stats(yard={"skipped_fps": 2.0}),
        )
        await _reports(hass, probed=False)
        clock[0] += 600
        (held,) = await _reports(hass, probed=False)
        aioclient_mock.clear_requests()
        aioclient_mock.get(
            f"{FRIGATE}/api/config", json={"cameras": {"yard": _camera([["record", "detect"]])}}
        )
        aioclient_mock.get(f"{FRIGATE}/api/stats", status=500)
        clock[0] += 300
        (blind,) = await _reports(hass, probed=False)

    assert held["issues"] == blind["issues"] == ["skipping_frames"]


async def test_a_symptom_seen_once_is_not_confirmed_while_the_rates_are_missing(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    clock = [1_000_000.0]
    with (
        patch.object(analysis.time, "time", side_effect=lambda: clock[0]),
        patch.object(analysis, "_start_probes"),
    ):
        _api(
            aioclient_mock,
            {"yard": _camera([["record", "detect"]])},
            stats=_stats(yard={"skipped_fps": 2.0}),
        )
        await _reports(hass, probed=False)
        aioclient_mock.clear_requests()
        aioclient_mock.get(
            f"{FRIGATE}/api/config", json={"cameras": {"yard": _camera([["record", "detect"]])}}
        )
        aioclient_mock.get(f"{FRIGATE}/api/stats", status=500)
        clock[0] += 900
        (blind,) = await _reports(hass, probed=False)

    assert blind["issues"] == []


async def test_a_rate_frigate_leaves_out_does_not_clear_a_confirmed_symptom(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    clock = [1_000_000.0]
    with (
        patch.object(analysis.time, "time", side_effect=lambda: clock[0]),
        patch.object(analysis, "_start_probes"),
    ):
        for rates in ({"skipped_fps": 2.0}, {"skipped_fps": 2.0}, {"camera_fps": 5.0}):
            aioclient_mock.clear_requests()
            _api(
                aioclient_mock,
                {"yard": _camera([["record", "detect"]])},
                stats={"cameras": {"yard": rates}, "cpu_usages": {}},
            )
            (report,) = await _reports(hass, probed=False)
            clock[0] += 600

    assert report["issues"] == ["skipping_frames"]


async def test_a_pending_symptom_starts_over_after_missing_rates(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    clock = [1_000_000.0]
    with (
        patch.object(analysis.time, "time", side_effect=lambda: clock[0]),
        patch.object(analysis, "_start_probes"),
    ):
        for rates in ({"skipped_fps": 2.0}, {"camera_fps": 5.0}, {"skipped_fps": 2.0}):
            aioclient_mock.clear_requests()
            _api(
                aioclient_mock,
                {"yard": _camera([["record", "detect"]])},
                stats={"cameras": {"yard": rates}, "cpu_usages": {}},
            )
            (report,) = await _reports(hass, probed=False)
            clock[0] += 600

    assert report["issues"] == []


async def test_a_confirmed_symptom_without_its_rate_states_no_number(
    hass: HomeAssistant,
) -> None:
    report = {
        "name": "Yard",
        "detect": {"width": 1280, "height": 720, "fps": 5},
        "streams": [],
        "stats": {},
    }

    sentences = insights_checks._camera_sentences(report)  # type: ignore[arg-type]

    assert "dropping frames because" in sentences["skipping_frames"][1]
    assert "much of a processor core" in sentences["high_cpu"][1]


@pytest.mark.parametrize("outage", ["unreachable", "unloaded"])
async def test_a_pending_symptom_starts_over_after_frigate_was_gone(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any, outage: str
) -> None:
    clock = [1_000_000.0]
    with (
        patch.object(analysis.time, "time", side_effect=lambda: clock[0]),
        patch.object(analysis, "_start_probes"),
    ):
        for there in (True, False, True):
            aioclient_mock.clear_requests()
            frigate.mock_state(
                hass,
                ConfigEntryState.LOADED
                if there or outage == "unreachable"
                else ConfigEntryState.NOT_LOADED,
            )
            if there:
                _api(
                    aioclient_mock,
                    {"yard": _camera([["record", "detect"]])},
                    stats=_stats(yard={"skipped_fps": 2.0}),
                )
            else:
                aioclient_mock.get(f"{FRIGATE}/api/config", status=500)
            report = await async_camera_reports(hass)
            clock[0] += 600

    assert report.reports is not None
    assert report.reports[0]["issues"] == []


async def test_another_frigate_server_starts_from_nothing(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(
        aioclient_mock,
        {"front_door": _camera([["record", "detect"]], (3840, 2160))},
        probes={"front_door": [_H265_4K]},
    )
    await _reports(hass)
    assert analysis._state(hass).probes

    hass.config_entries.async_update_entry(frigate, data={"url": "http://other:5000"})
    aioclient_mock.get(
        "http://other:5000/api/config",
        json={"cameras": {"front_door": _camera([["record", "detect"]], (3840, 2160))}},
    )
    aioclient_mock.get("http://other:5000/api/stats", json=_stats(front_door={}))
    with patch.object(analysis, "_start_probes"):
        (report,) = (await async_camera_reports(hass)).reports or []

    assert report["streams_measured_at"] is None
    assert "detect_stream_heavy" not in report["issues"]


async def test_a_camera_turned_back_on_starts_its_clocks_again(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    clock = [1_000_000.0]
    with (
        patch.object(analysis.time, "time", side_effect=lambda: clock[0]),
        patch.object(analysis, "_start_probes"),
    ):
        for enabled in (True, False, True):
            aioclient_mock.clear_requests()
            _api(
                aioclient_mock,
                {"yard": _camera([["record", "detect"]], enabled=enabled)},
                stats=_stats(yard={"skipped_fps": 2.0}),
            )
            (report,) = await _reports(hass, probed=False)
            clock[0] += 400

    assert report["issues"] == []


async def test_a_camera_of_another_frigate_entry_is_not_this_one(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    other = MockConfigEntry(domain="frigate", data={"url": "http://other:5000"})
    other.add_to_hass(hass)
    _frigate_camera_entity(hass, other, "yard")
    _api(aioclient_mock, {"yard": _camera([["record", "detect"]])})

    with patch.object(analysis, "_start_probes"):
        (report,) = await _reports(hass, probed=False)

    assert (report["device_id"], report["entity_id"]) == (None, None)


async def test_a_camera_turned_off_in_frigate_has_no_issues(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _api(
        aioclient_mock,
        {"garage": _camera([["record", "detect"]], (3840, 2160), enabled=False)},
        stats={"cameras": {}, "cpu_usages": {}},
    )

    (report,) = await _reports(hass)

    assert (report["enabled"], report["issues"]) == (False, [])
    assert not any("ffprobe" in str(c[1]) for c in aioclient_mock.mock_calls)


async def test_frigate_unreachable_is_an_error_not_a_clear(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", status=500, text="<html>")

    reports = await async_camera_reports(hass)
    (camera_check,) = [
        r for r in await async_run_checks(hass, reports) if r["check_id"] == "camera_analysis"
    ]

    assert reports.reports is None and reports.error
    assert camera_check["status"] == "error"


@pytest.mark.parametrize(
    "config",
    [
        {"cameras": ["front"]},
        {"cameras": {"front": {"detect": ["wide"]}}},
        {"cameras": {"front": {"detect": {"width": "1e400", "height": 720}}}},
    ],
)
async def test_an_unexpected_frigate_answer_fails_only_the_camera_check(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any, config: dict
) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/config", json=config)
    aioclient_mock.get(f"{FRIGATE}/api/stats", json={"cameras": {}})
    aioclient_mock.get(f"{FRIGATE}/api/ffprobe", json=[])

    reports = await async_camera_reports(hass)
    state = analysis._state(hass)
    if state.task is not None:
        await state.task

    assert reports.error is None or reports.reports is None
    results = await async_run_checks(hass, reports)
    assert any(r["check_id"] == "camera_analysis" for r in results)


async def test_the_health_check_has_one_finding_per_camera(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    _frigate_camera_entity(hass, frigate, "front_door")
    _api(
        aioclient_mock,
        {
            "front_door": _camera(
                [["record"], ["detect"]], (3840, 2160), friendly_name="Front Door"
            ),
            "storage": _camera([["record"], ["detect"]], friendly_name="Storage"),
        },
        probes={"front_door": [_H265_4K, _H265_4K], "storage": [_H265_4K, _H264_720P]},
    )
    reports = CameraReports(await _reports(hass))

    (camera_check,) = [
        r for r in await async_run_checks(hass, reports) if r["check_id"] == "camera_analysis"
    ]

    (finding,) = camera_check["findings"]
    assert finding["title"] == "Front Door is analysed at 3840×2160"
    assert (
        "720p is enough" in finding["detail"]
        and "Lower the camera's substream" in finding["detail"]
    )
    assert finding["severity"] == "warning"
    assert finding["device_id"] is not None
    assert finding["entities"] == ["camera.front_door"]


async def test_a_main_stream_read_for_detection_says_to_use_the_substream(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    """Front Door on the test Frigate: one 2560×1920 input for everything, so
    there is no substream to lower."""
    main = {
        "return_code": 0,
        "stdout": {"streams": [{"codec_long_name": "H.264 / AVC", "width": 2560, "height": 1920}]},
    }
    _api(
        aioclient_mock,
        {
            "front_door": _camera(
                [["record", "detect", "audio"]], (960, 720), friendly_name="Front Door"
            )
        },
        probes={"front_door": [main]},
    )
    reports = CameraReports(await _reports(hass))

    (camera_check,) = [
        r for r in await async_run_checks(hass, reports) if r["check_id"] == "camera_analysis"
    ]

    (finding,) = camera_check["findings"]
    assert finding["title"] == "Front Door is analysed from a 2560×1920 stream"
    assert "analyse its substream instead" in finding["detail"]
    assert "Lower the camera's substream" not in finding["detail"]


async def test_without_frigate_the_check_is_clear(hass: HomeAssistant) -> None:
    results = await async_run_checks(hass)

    (camera_check,) = [r for r in results if r["check_id"] == "camera_analysis"]
    assert (camera_check["status"], camera_check["findings"]) == ("clear", [])


async def test_a_probe_still_running_is_cancelled_on_stop(
    hass: HomeAssistant, frigate: MockConfigEntry, aioclient_mock: Any
) -> None:
    import asyncio  # noqa: PLC0415

    hang = asyncio.Event()

    async def _never(*_args: Any) -> list[Any]:
        await hang.wait()
        return []

    _api(aioclient_mock, {"yard": _camera([["record", "detect"]])})
    with patch.object(analysis.FrigateClient, "ffprobe", _never):
        await async_camera_reports(hass)
        task = analysis._state(hass).task
        assert task is not None and not task.done()
        await async_stop_probes(hass)

    assert task.cancelled()
