"""Just enough RTSP to ask a camera whether it serves a stream at a path, and
how large its picture is.

One DESCRIBE, answered again with the login when the camera asks for one
(Digest, or Basic), over TLS for ``rtsps``. A camera whose SDP does not say
the picture's size (Wyze) is asked to play the video, on the same connection,
until the first sequence parameter set arrives in it: the size is read from
that, and the connection closed. Nothing is decoded. The camera's certificate is its
own, self-signed, so it is not verified: FFmpeg reads these streams the same
way. Its status says whether the stream is there: 200 is a
stream, 401 a refused login, anything else no stream there. A 200's SDP says
the picture's size (``sdp.video_size``). The password never appears in a log
line: requests are not logged.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
from typing import Final, NamedTuple
from urllib.parse import unquote, urlsplit

from homeassistant.util.ssl import get_default_no_verify_context

from .sdp import _plausible, h264_size, h265_size, video_size

RTSP_PORT: Final = 554
RTSPS_PORT: Final = 322
_CONNECT_TIMEOUT: Final = 3.0
_READ_TIMEOUT: Final = 5.0
_MAX_HEAD: Final = 16 * 1024
_PARAM: Final = re.compile(r'(\w+)=(?:"([^"]*)"|([^,\s]*))')
# Reading the video for its first parameter set: cameras send one with every
# keyframe, a second or two apart.
_INBAND_TIMEOUT: Final = 6.0
_INBAND_MAX_BYTES: Final = 8 * 1024 * 1024
_MAX_NAL: Final = 64 * 1024
_RTPMAP: Final = re.compile(r"^a=rtpmap:\d+\s+([A-Za-z0-9-]+)/", re.IGNORECASE)
_H264_SPS: Final = 7
_H265_SPS: Final = 33


class _Reply(NamedTuple):
    status: int
    challenges: list[str]
    body: bytes
    headers: dict[str, str]  # lowercased names


class RtspError(Exception):
    """The camera did not answer RTSP on that port."""


class RtspUnreachable(RtspError):
    """Nothing answered at that address: the hub cannot reach it."""


class RtspAuthError(RtspError):
    """The camera refused the username or password."""


def _md5(text: str) -> str:
    # RFC 2617 digest, which RTSP cameras speak; it authenticates to the
    # camera and protects nothing here.
    return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()


def _authorization(
    challenges: list[str],
    uri: str,
    username: str,
    password: str,
    method: str = "DESCRIBE",
    count: int = 1,
) -> str | None:
    """The Authorization header answering the camera's challenge, Digest first.

    ``count`` is the request's number under that challenge (its ``nc``)."""
    for challenge in challenges:
        scheme, _, rest = challenge.partition(" ")
        if scheme.lower() != "digest":
            continue
        params = {k.lower(): a or b for k, a, b in _PARAM.findall(rest)}
        realm, nonce = params.get("realm", ""), params.get("nonce", "")
        ha1 = _md5(f"{username}:{realm}:{password}")
        ha2 = _md5(f"{method}:{uri}")
        fields = [
            f'username="{username}"',
            f'realm="{realm}"',
            f'nonce="{nonce}"',
            f'uri="{uri}"',
        ]
        if "auth" in [q.strip() for q in params.get("qop", "").split(",")]:
            cnonce = os.urandom(8).hex()
            nc = f"{count:08x}"
            response = _md5(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")
            fields += ["qop=auth", f"nc={nc}", f'cnonce="{cnonce}"']
        else:
            response = _md5(f"{ha1}:{nonce}:{ha2}")
        fields.append(f'response="{response}"')
        if opaque := params.get("opaque"):
            fields.append(f'opaque="{opaque}"')
        return "Digest " + ", ".join(fields)
    if any(c.lower().startswith("basic") for c in challenges):
        return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
    return None


async def _connect(
    host: str, port: int, *, tls: bool = False
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    try:
        async with asyncio.timeout(_CONNECT_TIMEOUT):
            if tls:
                return await asyncio.open_connection(
                    host, port, ssl=get_default_no_verify_context(), server_hostname=host
                )
            return await asyncio.open_connection(host, port)
    except ConnectionRefusedError as exc:
        raise RtspError(f"nothing serves RTSP on port {port}") from exc
    except (TimeoutError, OSError) as exc:
        raise RtspUnreachable(str(exc) or "timed out") from exc


async def _exchange(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    uri: str,
    cseq: int,
    authorization: str | None,
    method: str = "DESCRIBE",
    headers: tuple[str, ...] = ("Accept: application/sdp",),
) -> _Reply:
    """One request on the open connection: the status, any challenges, the
    body (a DESCRIBE's SDP) and the headers.

    The body is read whatever the status, so the connection is ready for the
    next.
    """
    request = f"{method} {uri} RTSP/1.0\r\nCSeq: {cseq}\r\n"
    request += "".join(f"{header}\r\n" for header in headers)
    if authorization:
        request += f"Authorization: {authorization}\r\n"
    try:
        writer.write((request + "\r\n").encode())
        async with asyncio.timeout(_READ_TIMEOUT):
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head[:_MAX_HEAD].decode("latin-1").split("\r\n")
            length = next(
                (
                    line.split(":", 1)[1].strip()
                    for line in lines[1:]
                    if line.lower().startswith("content-length:")
                ),
                "0",
            )
            body = b""
            if _is_number(length) and 0 < int(length) <= _MAX_HEAD:
                body = await reader.readexactly(int(length))
    except (TimeoutError, OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
        raise RtspError("the camera did not answer RTSP") from exc
    parts = lines[0].split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("RTSP/") or not _is_number(parts[1]):
        raise RtspError("the camera did not answer RTSP")
    challenges = [
        line.split(":", 1)[1].strip()
        for line in lines[1:]
        if line.lower().startswith("www-authenticate:")
    ]
    fields = {
        name.strip().lower(): value.strip()
        for name, sep, value in (line.partition(":") for line in lines[1:])
        if sep
    }
    return _Reply(int(parts[1]), challenges, body, fields)


def _is_number(text: str) -> bool:
    """ASCII digits only, few enough for ``int``: ``str.isdigit`` also accepts
    ``²``, which ``int`` refuses, and a camera's reply is untrusted."""
    return 0 < len(text) <= 9 and text.isascii() and text.isdigit()


async def async_describe(host: str, port: int, path: str, username: str, password: str) -> int:
    """The status the camera answers for this stream, logged in when it asks.

    Raises as ``_async_describe`` does.
    """
    status, _body, _size = await _async_describe(host, port, path, username, password)
    return status


async def async_stream_size(url: str) -> tuple[int, int] | None:
    """The picture size of the stream at ``url`` (its login in it), or None
    when the camera does not answer it or its SDP does not say.

    ``rtsp`` and ``rtsps``; any other scheme is not asked.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("rtsp", "rtsps") or not parts.hostname:
        return None
    tls = scheme == "rtsps"
    path = parts.path.lstrip("/") + (f"?{parts.query}" if parts.query else "")
    try:
        _status, _sdp, size = await _async_describe(
            parts.hostname,
            parts.port or (RTSPS_PORT if tls else RTSP_PORT),
            path,
            unquote(parts.username or ""),
            unquote(parts.password or ""),
            tls=tls,
            measure=True,
        )
    except RtspError:
        return None
    return size


async def _async_describe(
    host: str,
    port: int,
    path: str,
    username: str,
    password: str,
    *,
    tls: bool = False,
    measure: bool = False,
) -> tuple[int, bytes, tuple[int, int] | None]:
    """The status the camera answers for this stream, the body (a 200's SDP),
    and with ``measure`` the picture's size; logged in when it asks.

    The login answers the challenge on the connection that issued it, as
    FFmpeg does: a camera may refuse a nonce brought from another one.

    Raises ``RtspUnreachable`` when nothing answers at the address,
    ``RtspError`` when something does but not RTSP, ``RtspAuthError`` when
    it refuses the login.
    """
    uri = f"{'rtsps' if tls else 'rtsp'}://{host}:{port}/{path}"
    reader, writer = await _connect(host, port, tls=tls)
    try:
        reply = await _exchange(reader, writer, uri, 1, None)
        login: _Login | None = None
        if reply.status == 401:
            login = _Login(reply.challenges, username, password)
            if not username or (authorization := login("DESCRIBE", uri)) is None:
                raise RtspAuthError("the camera asks for a login")
            reply = await _exchange(reader, writer, uri, 2, authorization)
            if reply.status == 401:
                raise RtspAuthError("the camera refused the username or password")
        if reply.status != 200 or not measure:
            return reply.status, reply.body, None
        sdp = reply.body.decode("latin-1")
        size = video_size(sdp)
        if size is None:
            size = await _inband_size(reader, writer, uri, reply, sdp, login)
        return reply.status, reply.body, size
    finally:
        writer.close()


class _Login:
    """Answers each request under the challenge the connection was given."""

    def __init__(self, challenges: list[str], username: str, password: str) -> None:
        self._challenges, self._username, self._password = challenges, username, password
        self._count = 0

    def __call__(self, method: str, uri: str) -> str | None:
        self._count += 1
        return _authorization(
            self._challenges, uri, self._username, self._password, method, self._count
        )


class _Track(NamedTuple):
    control: str  # the URI its SETUP names
    codec: str | None  # H264 or H265 for the video track, else None


def _tracks(sdp: str, base: str) -> list[_Track]:
    """Every media track the SDP lists, in its order."""
    tracks: list[_Track] = []
    video, codec, control = False, None, None

    def close() -> None:
        if control is not None or video:
            target = control or "*"
            if target == "*":
                uri = base
            elif target.lower().startswith(("rtsp://", "rtsps://")):
                uri = target
            else:
                uri = f"{base.rstrip('/')}/{target}"
            tracks.append(_Track(uri, codec if video else None))

    in_media = False
    for raw in sdp.splitlines():
        line = raw.strip()
        if line.startswith("m="):
            if in_media:
                close()
            in_media, video, codec, control = True, line.startswith("m=video"), None, None
        elif in_media and (match := _RTPMAP.match(line)):
            codec = {"H264": "H264", "H265": "H265", "HEVC": "H265"}.get(match.group(1).upper())
        elif in_media and line.lower().startswith("a=control:"):
            control = line.split(":", 1)[1].strip()
    if in_media:
        close()
    return tracks


async def _inband_size(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    uri: str,
    described: _Reply,
    sdp: str,
    login: _Login | None,
) -> tuple[int, int] | None:
    """The size in the video's first sequence parameter set, played over this
    connection (RTP interleaved in it), or None.

    Every track is set up, not only the video: a Wyze sends nothing until its
    audio track is set up too.
    """
    base = described.headers.get("content-base") or uri
    tracks = _tracks(sdp, base)
    video = next((i for i, t in enumerate(tracks) if t.codec), None)
    if video is None:
        return None
    try:
        session = ""
        for index, track in enumerate(tracks):
            headers = [f"Transport: RTP/AVP/TCP;unicast;interleaved={2 * index}-{2 * index + 1}"]
            if session:
                headers.append(f"Session: {session}")
            setup = await _exchange(
                reader,
                writer,
                track.control,
                3 + index,
                login("SETUP", track.control) if login else None,
                "SETUP",
                tuple(headers),
            )
            if setup.status != 200:
                if index == video:
                    return None
                continue
            session = session or setup.headers.get("session", "").split(";", 1)[0].strip()
        if not session:
            return None
        play = await _exchange(
            reader,
            writer,
            base,
            3 + len(tracks),
            login("PLAY", base) if login else None,
            "PLAY",
            (f"Session: {session}", "Range: npt=0.000-"),
        )
        if play.status != 200:
            return None
        async with asyncio.timeout(_INBAND_TIMEOUT):
            return await _read_sps(reader, tracks[video].codec or "H264", 2 * video)
    except (
        RtspError,
        TimeoutError,
        OSError,
        asyncio.IncompleteReadError,
        asyncio.LimitOverrunError,
    ):
        return None


async def _read_sps(
    reader: asyncio.StreamReader, codec: str, channel: int
) -> tuple[int, int] | None:
    """Read interleaved RTP until the video's sequence parameter set, for its size."""
    unpack = _Unpacker(codec)
    received = 0
    while received < _INBAND_MAX_BYTES:
        if await reader.readexactly(1) != b"$":
            # An RTSP message between packets (a reply the camera sends unasked).
            await reader.readuntil(b"\r\n\r\n")
            continue
        head = await reader.readexactly(3)
        length = int.from_bytes(head[1:3])
        packet = await reader.readexactly(length)
        received += length + 4
        if head[0] != channel:
            continue  # RTCP, or another track
        for nal in unpack(_rtp_payload(packet)):
            if unpack.is_sps(nal):
                try:
                    size = h265_size(nal) if codec == "H265" else h264_size(nal)
                except (ValueError, IndexError):
                    continue
                return _plausible(size)
    return None


def _rtp_payload(packet: bytes) -> bytes:
    """An RTP packet's payload, past its CSRCs, extension and padding."""
    if len(packet) < 12:
        return b""
    offset = 12 + 4 * (packet[0] & 0x0F)
    if packet[0] & 0x10 and len(packet) >= offset + 4:
        offset += 4 + 4 * int.from_bytes(packet[offset + 2 : offset + 4])
    end = len(packet) - (packet[-1] if packet[0] & 0x20 else 0)
    return packet[offset:end] if offset < end else b""


class _Unpacker:
    """Whole NAL units out of RTP payloads (RFC 6184, RFC 7798): single,
    aggregated, or fragmented across packets."""

    def __init__(self, codec: str) -> None:
        self._h265 = codec == "H265"
        self._fragment: bytearray | None = None

    def is_sps(self, nal: bytes) -> bool:
        if not nal:
            return False
        if self._h265:
            return (nal[0] >> 1) & 0x3F == _H265_SPS
        return nal[0] & 0x1F == _H264_SPS

    def __call__(self, payload: bytes) -> list[bytes]:
        header = 2 if self._h265 else 1
        if len(payload) <= header:
            return []
        kind = (payload[0] >> 1) & 0x3F if self._h265 else payload[0] & 0x1F
        aggregate, fragment = (48, 49) if self._h265 else (24, 28)
        if kind == aggregate:
            units, at = [], header
            while at + 2 <= len(payload):
                size = int.from_bytes(payload[at : at + 2])
                units.append(payload[at + 2 : at + 2 + size])
                at += 2 + size
            return units
        if kind == fragment:
            return self._fragmented(payload)
        return [payload]

    def _fragmented(self, payload: bytes) -> list[bytes]:
        if self._h265:
            if len(payload) < 4:
                return []
            fu = payload[2]
            first = bytes([(payload[0] & 0x81) | ((fu & 0x3F) << 1), payload[1]])
            data = payload[3:]
        else:
            fu = payload[1]
            first = bytes([(payload[0] & 0xE0) | (fu & 0x1F)])
            data = payload[2:]
        if fu & 0x80:
            self._fragment = bytearray(first)
        if self._fragment is None:
            return []
        self._fragment += data
        if len(self._fragment) > _MAX_NAL:
            self._fragment = None
            return []
        if fu & 0x40:
            whole, self._fragment = bytes(self._fragment), None
            return [whole]
        return []
