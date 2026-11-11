"""Just enough RTSP to ask a camera whether it serves a stream at a path.

One DESCRIBE, answered again with the login when the camera asks for one
(Digest, or Basic). The answer's status is all that is read: 200 is a
stream, 401 a refused login, anything else no stream there. The password
never appears in a log line: requests are not logged.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
from typing import Final

RTSP_PORT: Final = 554
_CONNECT_TIMEOUT: Final = 3.0
_READ_TIMEOUT: Final = 5.0
_MAX_HEAD: Final = 16 * 1024
_PARAM: Final = re.compile(r'(\w+)=(?:"([^"]*)"|([^,\s]*))')


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


def _authorization(challenges: list[str], uri: str, username: str, password: str) -> str | None:
    """The Authorization header answering the camera's challenge, Digest first."""
    for challenge in challenges:
        scheme, _, rest = challenge.partition(" ")
        if scheme.lower() != "digest":
            continue
        params = {k.lower(): a or b for k, a, b in _PARAM.findall(rest)}
        realm, nonce = params.get("realm", ""), params.get("nonce", "")
        ha1 = _md5(f"{username}:{realm}:{password}")
        ha2 = _md5(f"DESCRIBE:{uri}")
        fields = [
            f'username="{username}"',
            f'realm="{realm}"',
            f'nonce="{nonce}"',
            f'uri="{uri}"',
        ]
        if "auth" in [q.strip() for q in params.get("qop", "").split(",")]:
            cnonce = os.urandom(8).hex()
            response = _md5(f"{ha1}:{nonce}:00000001:{cnonce}:auth:{ha2}")
            fields += ["qop=auth", "nc=00000001", f'cnonce="{cnonce}"']
        else:
            response = _md5(f"{ha1}:{nonce}:{ha2}")
        fields.append(f'response="{response}"')
        if opaque := params.get("opaque"):
            fields.append(f'opaque="{opaque}"')
        return "Digest " + ", ".join(fields)
    if any(c.lower().startswith("basic") for c in challenges):
        return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
    return None


async def _connect(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    try:
        async with asyncio.timeout(_CONNECT_TIMEOUT):
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
) -> tuple[int, list[str]]:
    """One DESCRIBE on the open connection: the status and any challenges.

    The answer's body is read too, so the connection is ready for the next.
    """
    request = f"DESCRIBE {uri} RTSP/1.0\r\nCSeq: {cseq}\r\nAccept: application/sdp\r\n"
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
            if length.isdigit() and 0 < int(length) <= _MAX_HEAD:
                await reader.readexactly(int(length))
    except (TimeoutError, OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
        raise RtspError("the camera did not answer RTSP") from exc
    parts = lines[0].split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("RTSP/") or not parts[1].isdigit():
        raise RtspError("the camera did not answer RTSP")
    challenges = [
        line.split(":", 1)[1].strip()
        for line in lines[1:]
        if line.lower().startswith("www-authenticate:")
    ]
    return int(parts[1]), challenges


async def async_describe(host: str, port: int, path: str, username: str, password: str) -> int:
    """The status the camera answers for this stream, logged in when it asks.

    The login answers the challenge on the connection that issued it, as
    FFmpeg does: a camera may refuse a nonce brought from another one.

    Raises ``RtspUnreachable`` when nothing answers at the address,
    ``RtspError`` when something does but not RTSP, ``RtspAuthError`` when
    it refuses the login.
    """
    uri = f"rtsp://{host}:{port}/{path}"
    reader, writer = await _connect(host, port)
    try:
        status, challenges = await _exchange(reader, writer, uri, 1, None)
        if status != 401:
            return status
        authorization = _authorization(challenges, uri, username, password) if username else None
        if authorization is None:
            raise RtspAuthError("the camera asks for a login")
        status, _ = await _exchange(reader, writer, uri, 2, authorization)
    finally:
        writer.close()
    if status == 401:
        raise RtspAuthError("the camera refused the username or password")
    return status
