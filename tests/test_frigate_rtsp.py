"""Tests for asking a camera over RTSP whether it serves a stream."""

from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any

import pytest

from custom_components.selora_ai.frigate.rtsp import (
    RtspAuthError,
    RtspError,
    RtspUnreachable,
    async_describe,
)

_REALM, _NONCE = "IPC", "abc123"


def _md5(text: str) -> str:
    return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()


async def _camera(path: str, password: str) -> tuple[asyncio.Server, int, list[str]]:
    """A camera serving ``path`` behind Digest, which accepts the login only
    on the connection whose challenge it answers, as a Uniview does."""
    requests: list[str] = []

    async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonce = f"{_NONCE}-{len(requests)}"
        try:
            while True:
                head = (await reader.readuntil(b"\r\n\r\n")).decode()
                requests.append(head)
                uri = head.split(" ")[1]
                answer = re.search(r'response="([^"]+)"', head)
                expected = _md5(
                    f"{_md5(f'admin:{_REALM}:{password}')}:{nonce}:{_md5(f'DESCRIBE:{uri}')}"
                )
                if answer is None or answer.group(1) != expected:
                    body = "Unauthorized"
                    status = (
                        "401 Unauthorized\r\n"
                        f'WWW-Authenticate: Digest realm="{_REALM}", nonce="{nonce}"\r\n'
                        f"Content-Length: {len(body)}"
                    )
                elif uri.endswith("/" + path):
                    body, status = "", "200 OK"
                else:
                    body, status = "", "404 Not Found"
                writer.write(f"RTSP/1.0 {status}\r\nCSeq: 1\r\n\r\n{body}".encode())
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(_serve, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], requests


@pytest.fixture(autouse=True)
def _sockets(socket_enabled: Any) -> None:
    """These tests talk to a server on the loopback address."""


async def test_a_served_path_answers_200_after_the_digest_login() -> None:
    server, port, requests = await _camera("unicast/c1/s0/live", "secret")
    async with server:
        status = await async_describe("127.0.0.1", port, "unicast/c1/s0/live", "admin", "secret")

    assert status == 200
    assert "Authorization" not in requests[0] and "Digest" in requests[1]
    assert "secret" not in "".join(requests)


async def test_another_path_is_not_a_stream() -> None:
    server, port, _ = await _camera("unicast/c1/s0/live", "secret")
    async with server:
        status = await async_describe("127.0.0.1", port, "h264Preview_01_main", "admin", "secret")

    assert status == 404


async def test_a_wrong_password_is_refused() -> None:
    server, port, _ = await _camera("unicast/c1/s0/live", "secret")
    async with server, asyncio.timeout(5):
        with pytest.raises(RtspAuthError):
            await async_describe("127.0.0.1", port, "unicast/c1/s0/live", "admin", "wrong")


async def test_a_closed_port_is_not_rtsp_but_reachable() -> None:
    server = await asyncio.start_server(lambda *_: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()

    with pytest.raises(RtspError) as raised:
        await async_describe("127.0.0.1", port, "x", "admin", "p")

    assert not isinstance(raised.value, RtspUnreachable)
