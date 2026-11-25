"""Tests for asking a camera over RTSP whether it serves a stream."""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
import re
import ssl
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import pytest

from custom_components.selora_ai.frigate.rtsp import (
    RtspAuthError,
    RtspError,
    RtspUnreachable,
    async_describe,
    async_stream_size,
)

_REALM, _NONCE = "IPC", "abc123"


def _md5(text: str) -> str:
    return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()


# What a Uniview answers for its 2880×1620 H.265 main stream.
_SDP = (
    "v=0\r\ns=Media Server\r\nt=0 0\r\nm=video 0 RTP/AVP 96\r\n"
    "a=rtpmap:96 H265/90000\r\na=fmtp:96 "
    "sprop-sps=QgEBAWAAAAMAkAAAAwAAAwCWoAFoIAZZ95ZWaSTK8BaAgAAAAwCAAAADAIQ=\r\n"
)


def _self_signed(directory: Path) -> ssl.SSLContext:
    """A server context with a certificate nobody signed, as a Wyze camera has."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "camera")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = directory / "cert.pem", directory / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    return context


async def _camera(
    path: str, password: str, tls: ssl.SSLContext | None = None
) -> tuple[asyncio.Server, int, list[str]]:
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
                    body, status = _SDP, f"200 OK\r\nContent-Length: {len(_SDP)}"
                else:
                    body, status = "", "404 Not Found"
                writer.write(f"RTSP/1.0 {status}\r\nCSeq: 1\r\n\r\n{body}".encode())
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(_serve, "127.0.0.1", 0, ssl=tls)
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


async def test_a_stream_says_its_size_in_its_sdp() -> None:
    server, port, requests = await _camera("unicast/c1/s0/live", "p@ss/word")
    async with server:
        size = await async_stream_size(
            f"rtsp://admin:p%40ss%2Fword@127.0.0.1:{port}/unicast/c1/s0/live"
        )
        missing = await async_stream_size(f"rtsp://admin:p%40ss%2Fword@127.0.0.1:{port}/other")
        wrong = await async_stream_size(f"rtsp://admin:no@127.0.0.1:{port}/unicast/c1/s0/live")

    assert size == (2880, 1620)
    assert (missing, wrong) == (None, None)
    assert "p@ss" not in "".join(requests)


async def test_an_rtsps_stream_is_measured_over_tls_with_its_own_certificate(
    tmp_path: Path,
) -> None:
    server, port, requests = await _camera("stream1", "p@ss", _self_signed(tmp_path))
    async with server, asyncio.timeout(5):
        size = await async_stream_size(f"rtsps://admin:p%40ss@127.0.0.1:{port}/stream1")

    assert size == (2880, 1620)
    # The digest names the stream as the camera knows it, scheme included.
    assert requests[1].startswith(f"DESCRIBE rtsps://127.0.0.1:{port}/stream1 ")


async def test_a_stream_in_another_format_is_not_measured() -> None:
    assert await async_stream_size("rtmp://192.168.1.1/bcs/channel0_sub.bcs") is None


@pytest.mark.parametrize(
    "reply",
    [
        "RTSP/1.0 200 OK\r\nCSeq: 1\r\nContent-Length: ²\r\n\r\n",
        "RTSP/1.0 2²00 OK\r\nCSeq: 1\r\n\r\n",
        "RTSP/1.0 200 OK\r\nCSeq: 1\r\nContent-Length: " + "9" * 5000 + "\r\n\r\n",
    ],
)
async def test_a_reply_with_odd_numbers_is_no_size(reply: str) -> None:
    """A camera's reply is untrusted: digits ``int`` refuses are no answer,
    never an error that fails the whole confirm."""

    async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(reply.encode("latin-1"))
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(_serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server, asyncio.timeout(5):
        assert await async_stream_size(f"rtsp://127.0.0.1:{port}/live") is None


# The Living Room Wyze's own H.264 SPS: 2560×1440.
_WYZE_SPS = base64.b64decode("J2QAMq0AzoAoALWmoCAgPgAAAwACAAADADxmoAdTAAr8v/+BQA==")
# What a Wyze answers DESCRIBE with: no parameter sets, audio listed first.
_WYZE_SDP = (
    "v=0\r\ns=SmolRTSP example\r\nt=0 0\r\n"
    "m=audio 0 RTP/AVP 97\r\na=rtpmap:97 MPEG4-GENERIC/16000/1\r\na=control:audio\r\n"
    "m=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
    "a=fmtp:96 packetization-mode=1\r\na=control:video\r\n"
)


def _rtp(payload: bytes) -> bytes:
    return bytes([0x80, 96]) + b"\x00\x01" + b"\x00" * 8 + payload


def _interleaved(channel: int, packet: bytes) -> bytes:
    return b"$" + bytes([channel]) + len(packet).to_bytes(2) + packet


async def _wyze(*, sends: bool = True) -> tuple[asyncio.Server, int, list[str]]:
    """A camera whose SDP says no size and that streams only once every track
    is set up; its SPS comes fragmented (FU-A), after an audio packet."""
    requests: list[str] = []

    async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        set_up: set[str] = set()
        try:
            while True:
                head = (await reader.readuntil(b"\r\n\r\n")).decode()
                requests.append(head)
                method, uri = head.split(" ")[:2]
                cseq = re.search(r"CSeq: (\d+)", head).group(1)
                if method == "DESCRIBE":
                    reply = f"200 OK\r\nCSeq: {cseq}\r\nContent-Length: {len(_WYZE_SDP)}\r\n\r\n"
                    writer.write(f"RTSP/1.0 {reply}{_WYZE_SDP}".encode())
                elif method == "SETUP":
                    set_up.add(uri.rsplit("/", 1)[1])
                    writer.write(
                        f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nSession: 42;timeout=60\r\n\r\n".encode()
                    )
                elif method == "PLAY":
                    writer.write(f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\n\r\n".encode())
                    if sends and set_up == {"audio", "video"}:
                        writer.write(_interleaved(0, _rtp(b"\x21\x10")))  # audio
                        fu = bytes([(_WYZE_SPS[0] & 0xE0) | 28])
                        kind = _WYZE_SPS[0] & 0x1F
                        middle = len(_WYZE_SPS) // 2
                        writer.write(
                            _interleaved(2, _rtp(fu + bytes([0x80 | kind]) + _WYZE_SPS[1:middle]))
                        )
                        writer.write(
                            _interleaved(2, _rtp(fu + bytes([0x40 | kind]) + _WYZE_SPS[middle:]))
                        )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(_serve, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], requests


async def test_a_size_the_sdp_does_not_say_is_read_from_the_video() -> None:
    server, port, requests = await _wyze()
    async with server, asyncio.timeout(5):
        size = await async_stream_size(f"rtsp://127.0.0.1:{port}/stream0")

    assert size == (2560, 1440)
    # Every track is set up, each on its own interleaved channels, video second.
    setups = [r for r in requests if r.startswith("SETUP")]
    assert [r.split(" ")[1].rsplit("/", 1)[1] for r in setups] == ["audio", "video"]
    assert "interleaved=2-3" in setups[1] and "Session: 42" in setups[1]


async def test_a_camera_that_sends_no_video_has_no_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("custom_components.selora_ai.frigate.rtsp._INBAND_TIMEOUT", 0.3)
    server, port, _ = await _wyze(sends=False)
    async with server, asyncio.timeout(5):
        assert await async_stream_size(f"rtsp://127.0.0.1:{port}/stream0") is None
