"""Just enough ONVIF to ask a camera for its stream URLs.

Four SOAP calls: the camera's clock (unauthenticated, so the WS-Security
timestamp matches it), its media service address, its profiles with their
resolutions, and the RTSP URI of each profile. HA's own ONVIF library is an
integration requirement, installed only once that integration is set up, so
it cannot be relied on here.

Responses come from devices on the LAN and are parsed with ElementTree after
a size cap. The password never appears in a log line: requests are not logged.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import os
from typing import TYPE_CHECKING, Final
from xml.etree import ElementTree as ET

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_TIMEOUT: Final = aiohttp.ClientTimeout(total=8, connect=3)
# Where cameras serve ONVIF when nothing says: the standard port, then
# Reolink's, a common alternative, and TP-Link Tapo's.
ONVIF_PORTS: Final = (80, 8000, 8080, 2020)
_MAX_RESPONSE: Final = 256 * 1024

_NS: Final = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
}
_WSSE: Final = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
_WSU: Final = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
_DIGEST: Final = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0"
    "#PasswordDigest"
)


class OnvifError(Exception):
    """The camera did not answer as an ONVIF camera."""


class OnvifAuthError(OnvifError):
    """The camera refused the username or password."""


@dataclass(frozen=True)
class Profile:
    token: str
    name: str
    width: int
    height: int
    uri: str  # without credentials; the caller adds them


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _security(username: str, password: str, skew: timedelta) -> str:
    """A WS-Security UsernameToken with the digest ONVIF mandates.

    SHA-1 is what the profile specifies (Base64(SHA1(nonce + created +
    password))); it authenticates to the camera and protects nothing here.
    """
    nonce = os.urandom(16)
    created = (datetime.now(UTC) + skew).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + password.encode(), usedforsecurity=False).digest()
    ).decode()
    return (
        f'<wsse:Security xmlns:wsse="{_WSSE}" xmlns:wsu="{_WSU}"><wsse:UsernameToken>'
        f"<wsse:Username>{_escape(username)}</wsse:Username>"
        f'<wsse:Password Type="{_DIGEST}">{digest}</wsse:Password>'
        f"<wsse:Nonce>{base64.b64encode(nonce).decode()}</wsse:Nonce>"
        f"<wsu:Created>{created}</wsu:Created>"
        "</wsse:UsernameToken></wsse:Security>"
    )


def _envelope(body: str, header: str = "") -> str:
    return (
        f'<s:Envelope xmlns:s="{_NS["s"]}" xmlns:tds="{_NS["tds"]}" '
        f'xmlns:trt="{_NS["trt"]}" xmlns:tt="{_NS["tt"]}">'
        f"<s:Header>{header}</s:Header><s:Body>{body}</s:Body></s:Envelope>"
    )


def _is_auth_fault(root: ET.Element) -> bool:
    text = " ".join(t.lower() for t in root.itertext())
    return "notauthorized" in text or "not authorized" in text or "authority failure" in text


class OnvifCamera:
    """One camera's ONVIF device service."""

    def __init__(self, hass: HomeAssistant, host: str, port: int = 80) -> None:
        self._session = async_get_clientsession(hass)
        self._device_url = f"http://{host}:{port}/onvif/device_service"
        self._skew = timedelta()

    async def _call(self, url: str, body: str, header: str = "") -> ET.Element:
        try:
            async with self._session.post(
                url,
                data=_envelope(body, header),
                headers={"Content-Type": "application/soap+xml; charset=utf-8"},
                timeout=_TIMEOUT,
            ) as resp:
                raw = await resp.content.read(_MAX_RESPONSE + 1)
                status = resp.status
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise OnvifError(f"the camera did not answer ONVIF: {exc}") from exc
        if len(raw) > _MAX_RESPONSE:
            raise OnvifError("the camera's answer was too large")
        try:
            root = ET.fromstring(raw)  # noqa: S314 — size-capped, from a LAN device
        except ET.ParseError as exc:
            raise OnvifError("the camera's answer was not ONVIF") from exc
        if status in (400, 401, 403, 500) and _is_auth_fault(root):
            raise OnvifAuthError("the camera refused the username or password")
        if status >= 400:
            raise OnvifError(f"the camera answered {status}")
        return root

    async def _sync_clock(self) -> None:
        """Measure the camera's clock offset; a digest outside its window fails."""
        root = await self._call(self._device_url, "<tds:GetSystemDateAndTime/>")
        utc = root.find(".//tt:UTCDateTime", _NS)
        if utc is None:
            return
        try:
            date, time = utc.find("tt:Date", _NS), utc.find("tt:Time", _NS)
            camera = datetime(
                int(date.findtext("tt:Year", "", _NS)),  # type: ignore[union-attr]
                int(date.findtext("tt:Month", "", _NS)),  # type: ignore[union-attr]
                int(date.findtext("tt:Day", "", _NS)),  # type: ignore[union-attr]
                int(time.findtext("tt:Hour", "", _NS)),  # type: ignore[union-attr]
                int(time.findtext("tt:Minute", "", _NS)),  # type: ignore[union-attr]
                int(time.findtext("tt:Second", "", _NS)),  # type: ignore[union-attr]
                tzinfo=UTC,
            )
        except (AttributeError, ValueError):
            return
        self._skew = camera - datetime.now(UTC)

    async def async_profiles(self, username: str, password: str) -> list[Profile]:
        """Every media profile with its RTSP URI, largest first."""
        await self._sync_clock()

        def auth() -> str:
            return _security(username, password, self._skew)

        caps = await self._call(
            self._device_url,
            "<tds:GetCapabilities><tds:Category>Media</tds:Category></tds:GetCapabilities>",
            auth(),
        )
        media_url = caps.findtext(".//tt:Media/tt:XAddr", "", _NS).strip() or self._device_url
        found = await self._call(media_url, "<trt:GetProfiles/>", auth())
        profiles: list[Profile] = []
        for element in found.findall(".//trt:Profiles", _NS):
            token = element.get("token") or ""
            if not token:
                continue
            resolution = element.find(".//tt:VideoEncoderConfiguration/tt:Resolution", _NS)
            try:
                width = (
                    int(resolution.findtext("tt:Width", "0", _NS)) if resolution is not None else 0
                )
                height = (
                    int(resolution.findtext("tt:Height", "0", _NS)) if resolution is not None else 0
                )
            except ValueError:
                width = height = 0
            uri_root = await self._call(
                media_url,
                "<trt:GetStreamUri><trt:StreamSetup>"
                "<tt:Stream>RTP-Unicast</tt:Stream>"
                "<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>"
                f"</trt:StreamSetup><trt:ProfileToken>{_escape(token)}</trt:ProfileToken>"
                "</trt:GetStreamUri>",
                auth(),
            )
            uri = uri_root.findtext(".//tt:Uri", "", _NS).strip()
            if uri.lower().startswith(("rtsp://", "rtsps://")):
                profiles.append(
                    Profile(token, element.findtext("tt:Name", token, _NS), width, height, uri)
                )
        if not profiles:
            raise OnvifError("the camera has no RTSP stream")
        return sorted(profiles, key=lambda p: p.width * p.height, reverse=True)
