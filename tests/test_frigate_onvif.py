"""Tests for asking an ONVIF camera for its stream URLs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
import pytest

from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from custom_components.selora_ai.frigate.onvif import OnvifAuthError, OnvifCamera, OnvifError

DEVICE = "http://192.168.1.60:80/onvif/device_service"
MEDIA = "http://192.168.1.60:80/onvif/media_service"

_ENV = (
    '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
    'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
    'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
    'xmlns:tt="http://www.onvif.org/ver10/schema"><s:Body>{}</s:Body></s:Envelope>'
)


def _clock(at: datetime) -> str:
    return _ENV.format(
        "<tds:GetSystemDateAndTimeResponse><tds:SystemDateAndTime><tt:UTCDateTime>"
        f"<tt:Date><tt:Year>{at.year}</tt:Year><tt:Month>{at.month}</tt:Month>"
        f"<tt:Day>{at.day}</tt:Day></tt:Date><tt:Time><tt:Hour>{at.hour}</tt:Hour>"
        f"<tt:Minute>{at.minute}</tt:Minute><tt:Second>{at.second}</tt:Second></tt:Time>"
        "</tt:UTCDateTime></tds:SystemDateAndTime></tds:GetSystemDateAndTimeResponse>"
    )


_CAPABILITIES = _ENV.format(
    "<tds:GetCapabilitiesResponse><tds:Capabilities><tt:Media>"
    f"<tt:XAddr>{MEDIA}</tt:XAddr></tt:Media></tds:Capabilities></tds:GetCapabilitiesResponse>"
)


def _profile(token: str, width: int, height: int) -> str:
    return (
        f'<trt:Profiles token="{token}"><tt:Name>{token}</tt:Name>'
        "<tt:VideoEncoderConfiguration><tt:Resolution>"
        f"<tt:Width>{width}</tt:Width><tt:Height>{height}</tt:Height>"
        "</tt:Resolution></tt:VideoEncoderConfiguration></trt:Profiles>"
    )


_PROFILES = _ENV.format(
    "<trt:GetProfilesResponse>"
    + _profile("sub", 640, 360)
    + _profile("main", 2560, 1440)
    + "</trt:GetProfilesResponse>"
)

_URI = _ENV.format(
    "<trt:GetStreamUriResponse><trt:MediaUri>"
    "<tt:Uri>rtsp://192.168.1.60:554/stream</tt:Uri>"
    "</trt:MediaUri></trt:GetStreamUriResponse>"
)

_NOT_AUTHORIZED = (
    '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
    'xmlns:ter="http://www.onvif.org/ver10/error"><s:Body><s:Fault><s:Code>'
    "<s:Value>s:Sender</s:Value><s:Subcode><s:Value>ter:NotAuthorized</s:Value>"
    "</s:Subcode></s:Code></s:Fault></s:Body></s:Envelope>"
)


def _camera(aioclient_mock: Any, answers: dict[str, tuple[int, str]]) -> None:
    """Answer each SOAP call by the operation its body names."""

    async def _answer(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        body = str(data)
        for operation, (status, text) in answers.items():
            if f":{operation}" in body:
                return AiohttpClientMockResponse(method, url, status=status, text=text)
        return AiohttpClientMockResponse(method, url, status=500, text="<x/>")

    aioclient_mock.post(DEVICE, side_effect=_answer)
    aioclient_mock.post(MEDIA, side_effect=_answer)


def _working(clock: datetime | None = None) -> dict[str, tuple[int, str]]:
    return {
        "GetSystemDateAndTime": (200, _clock(clock or datetime.now(UTC))),
        "GetCapabilities": (200, _CAPABILITIES),
        "GetProfiles": (200, _PROFILES),
        "GetStreamUri": (200, _URI),
    }


async def test_profiles_come_back_largest_first(hass: HomeAssistant, aioclient_mock: Any) -> None:
    _camera(aioclient_mock, _working())

    profiles = await OnvifCamera(hass, "192.168.1.60").async_profiles("admin", "pw")

    assert [(p.token, p.width) for p in profiles] == [("main", 2560), ("sub", 640)]
    assert profiles[0].uri == "rtsp://192.168.1.60:554/stream"


async def test_the_password_is_sent_as_a_digest_only(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    _camera(aioclient_mock, _working())

    await OnvifCamera(hass, "192.168.1.60").async_profiles("admin", "s3cret-pass")

    bodies = [str(call[2]) for call in aioclient_mock.mock_calls]
    assert all("s3cret-pass" not in body for body in bodies)
    assert "PasswordDigest" in bodies[1]


async def test_a_refused_login_is_an_auth_error(hass: HomeAssistant, aioclient_mock: Any) -> None:
    _camera(aioclient_mock, {**_working(), "GetCapabilities": (400, _NOT_AUTHORIZED)})

    with pytest.raises(OnvifAuthError):
        await OnvifCamera(hass, "192.168.1.60").async_profiles("admin", "wrong")


async def test_the_camera_clock_sets_the_timestamp(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """A camera whose clock is off rejects a digest stamped with ours."""
    camera_time = datetime.now(UTC) - timedelta(hours=3)
    _camera(aioclient_mock, _working(camera_time))

    await OnvifCamera(hass, "192.168.1.60").async_profiles("admin", "pw")

    created = str(aioclient_mock.mock_calls[1][2]).split("<wsu:Created>")[1][:13]
    assert created == camera_time.strftime("%Y-%m-%dT%H")


async def test_not_an_onvif_camera(hass: HomeAssistant, aioclient_mock: Any) -> None:
    aioclient_mock.post(DEVICE, text="<html>router login</html")

    with pytest.raises(OnvifError):
        await OnvifCamera(hass, "192.168.1.60").async_profiles("admin", "pw")


async def test_an_oversized_answer_is_refused(hass: HomeAssistant, aioclient_mock: Any) -> None:
    aioclient_mock.post(DEVICE, text="<a>" + "x" * (300 * 1024) + "</a>")

    with pytest.raises(OnvifError, match="too large"):
        await OnvifCamera(hass, "192.168.1.60").async_profiles("admin", "pw")
