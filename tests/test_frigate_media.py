"""Signed links to Frigate events' snapshots and clips, for MCP clients to hand
the user, and the view that streams them from Frigate."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.setup import async_setup_component
import pytest

from custom_components.selora_ai.frigate import media
from custom_components.selora_ai.mcp_server import dispatch as mcp_dispatch
from custom_components.selora_ai.mcp_server.names import TOOL_FIND_CAMERA_EVENTS
from custom_components.selora_ai.selora_auth import SeloraAuthContext

ADMIN = SeloraAuthContext("admin", None, True, "ha_token")
EVENT = "1730000000.123-abc123"
EXTERNAL = "https://home.example.com"


class _Upstream:
    """Frigate's response to one path."""

    def __init__(
        self, status: int, body: bytes = b"", headers: dict[str, str] | None = None
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._body = body
        self.content = self

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        for start in range(0, len(self._body), size):
            yield self._body[start : start + size]


class _Frigate:
    """A FrigateClient whose media() answers from a table of paths."""

    def __init__(self, responses: dict[str, _Upstream]) -> None:
        self.responses = responses
        self.asked: list[tuple[str, dict[str, str]]] = []

    @asynccontextmanager
    async def media(self, path: str, headers: dict[str, str] | None = None) -> AsyncIterator[Any]:
        self.asked.append((path, dict(headers or {})))
        yield self.responses.get(path, _Upstream(404))


@pytest.fixture
async def view(hass: HomeAssistant, tmp_path: Path) -> AsyncIterator[None]:
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "http", {})
    media.async_register_view(hass)
    # Fetched clips land in this test's own directory.
    with patch.object(media.tempfile, "gettempdir", return_value=str(tmp_path)):
        yield


def _frigate(responses: dict[str, _Upstream]) -> Any:
    frigate = _Frigate(responses)
    return frigate, patch.object(media.FrigateClient, "from_hass", return_value=frigate)


_JPEG = _Upstream(
    200,
    b"\xff\xd8jpeg",
    {"Content-Type": "image/jpeg", "Content-Length": "6", "Set-Cookie": "frigate_token=x"},
)


@pytest.mark.usefixtures("view")
async def test_a_signed_link_streams_the_snapshot(
    hass: HomeAssistant, hass_client_no_auth: Any
) -> None:
    link = media.event_links(hass, "", EVENT, has_clip=False)["snapshot_url"]
    frigate, patched = _frigate({f"/api/events/{EVENT}/snapshot.jpg?bbox=1": _JPEG})
    client = await hass_client_no_auth()
    with patched:
        resp = await client.get(link)

    assert resp.status == 200
    assert await resp.read() == b"\xff\xd8jpeg"
    assert resp.headers["Content-Type"] == "image/jpeg"
    # Frigate's login cookie stays on the hub.
    assert "Set-Cookie" not in resp.headers
    assert frigate.asked == [(f"/api/events/{EVENT}/snapshot.jpg?bbox=1", {})]


@pytest.mark.usefixtures("view")
async def test_without_a_snapshot_the_thumbnail_is_sent(
    hass: HomeAssistant, hass_client_no_auth: Any
) -> None:
    link = media.event_links(hass, "", EVENT, has_clip=False)["snapshot_url"]
    _, patched = _frigate({f"/api/events/{EVENT}/thumbnail.jpg": _JPEG})
    client = await hass_client_no_auth()
    with patched:
        resp = await client.get(link)

    assert resp.status == 200
    assert await resp.read() == b"\xff\xd8jpeg"


# Frigate builds a clip as it sends it: chunked, no length, Range ignored.
_CLIP_BODY = bytes(range(256)) * 4
_CLIP = _Upstream(200, _CLIP_BODY, {"Content-Type": "video/mp4"})


@pytest.mark.usefixtures("view")
async def test_a_clip_answers_ranges(hass: HomeAssistant, hass_client_no_auth: Any) -> None:
    """Safari and the apps' WebKit play nothing without a 206 to their first
    ``bytes=0-1``, which Frigate never gives."""
    link = media.event_links(hass, "", EVENT, has_clip=True)["clip_url"]
    frigate, patched = _frigate({f"/api/events/{EVENT}/clip.mp4": _CLIP})
    client = await hass_client_no_auth()
    with patched:
        probe = await client.get(link, headers={"Range": "bytes=0-1"})
        seek = await client.get(link, headers={"Range": "bytes=512-"})
        whole = await client.get(link)

    assert probe.status == 206
    assert probe.headers["Content-Range"] == f"bytes 0-1/{len(_CLIP_BODY)}"
    assert probe.headers["Content-Type"] == "video/mp4"
    assert await probe.read() == _CLIP_BODY[:2]
    assert seek.status == 206
    assert await seek.read() == _CLIP_BODY[512:]
    assert whole.status == 200
    assert await whole.read() == _CLIP_BODY
    # Fetched once, whole: a range sent on would be ignored anyway.
    assert frigate.asked == [(f"/api/events/{EVENT}/clip.mp4", {})]


@pytest.mark.usefixtures("view")
async def test_ranges_asked_at_once_share_one_fetch(
    hass: HomeAssistant, hass_client_no_auth: Any
) -> None:
    link = media.event_links(hass, "", EVENT, has_clip=True)["clip_url"]
    frigate, patched = _frigate({f"/api/events/{EVENT}/clip.mp4": _CLIP})
    client = await hass_client_no_auth()
    with patched:
        first, second = await asyncio.gather(
            client.get(link, headers={"Range": "bytes=0-1"}),
            client.get(link, headers={"Range": "bytes=2-3"}),
        )

    assert (first.status, second.status) == (206, 206)
    assert await second.read() == _CLIP_BODY[2:4]
    assert len(frigate.asked) == 1


@pytest.mark.usefixtures("view")
async def test_an_event_without_a_clip_is_not_found(
    hass: HomeAssistant, hass_client_no_auth: Any
) -> None:
    link = media.event_links(hass, "", EVENT, has_clip=True)["clip_url"]
    _, patched = _frigate({})
    client = await hass_client_no_auth()
    with patched:
        resp = await client.get(link)

    assert resp.status == 404


@pytest.mark.usefixtures("view")
async def test_an_oversized_clip_is_streamed_through(
    hass: HomeAssistant, hass_client_no_auth: Any, tmp_path: Path
) -> None:
    """Too large to keep on the hub: Chrome still plays it as it arrives."""
    link = media.event_links(hass, "", EVENT, has_clip=True)["clip_url"]
    frigate, patched = _frigate({f"/api/events/{EVENT}/clip.mp4": _CLIP})
    client = await hass_client_no_auth()
    with patched, patch.object(media, "_CLIP_MAX_BYTES", 100), patch.object(media, "_CHUNK", 64):
        resp = await client.get(link)
        again = await client.get(link, headers={"Range": "bytes=0-1"})

    assert resp.status == 200
    assert await resp.read() == _CLIP_BODY
    assert not list((tmp_path / media._CLIP_DIR).iterdir())
    # Learned once: the next request streams at once, without buffering it again.
    assert again.status == 200
    assert len(frigate.asked) == 3
    assert frigate.asked[-1] == (f"/api/events/{EVENT}/clip.mp4", {"Range": "bytes=0-1"})


def test_only_the_latest_clips_are_kept(tmp_path: Path) -> None:
    directory = tmp_path / media._CLIP_DIR
    directory.mkdir()
    now = time.time()
    for index in range(media._CLIPS_KEPT + 2):
        old = directory / f"{index}.mp4"
        old.write_bytes(b"x")
        os.utime(old, (now - index, now - index))
    stale = directory / "stale.mp4"
    stale.write_bytes(b"x")
    os.utime(stale, (now - media._CLIP_REUSE_SECONDS - 1,) * 2)
    arriving = directory / "arriving.part"
    arriving.write_bytes(b"x")
    partial = directory / "new.part"
    partial.write_bytes(b"clip")

    media._keep(partial.open("ab"), partial, directory / "new.mp4")

    names = {entry.name for entry in directory.iterdir()}
    kept = {f"{index}.mp4" for index in range(media._CLIPS_KEPT - 1)}
    assert names == kept | {"new.mp4", "arriving.part"}


def test_kept_clips_stay_under_a_total(tmp_path: Path) -> None:
    directory = tmp_path / media._CLIP_DIR
    directory.mkdir()
    now = time.time()
    for index in range(3):
        old = directory / f"{index}.mp4"
        old.write_bytes(b"x" * 40)
        os.utime(old, (now - index - 1, now - index - 1))
    partial = directory / "new.part"
    partial.write_bytes(b"x" * 40)

    with patch.object(media, "_CLIPS_MAX_TOTAL", 100):
        media._keep(partial.open("ab"), partial, directory / "new.mp4")

    assert {entry.name for entry in directory.iterdir()} == {"new.mp4", "0.mp4"}


@pytest.mark.usefixtures("view")
async def test_a_link_opens_only_what_it_was_signed_for(
    hass: HomeAssistant, hass_client_no_auth: Any
) -> None:
    link = media.event_links(hass, "", EVENT, has_clip=False)["snapshot_url"]
    _, patched = _frigate({f"/api/events/{EVENT}/clip.mp4": _Upstream(200, b"video")})
    client = await hass_client_no_auth()
    with patched:
        tampered = await client.get(link.replace("snapshot.jpg", "clip.mp4"))
        unsigned = await client.get(f"/api/selora_ai/frigate/events/{EVENT}/clip.mp4")

    assert tampered.status == 401
    assert unsigned.status == 401


@pytest.mark.usefixtures("view")
async def test_a_non_admin_s_own_login_is_refused(
    hass_client: Any, hass_read_only_access_token: str
) -> None:
    _, patched = _frigate({f"/api/events/{EVENT}/thumbnail.jpg": _JPEG})
    client = await hass_client(access_token=hass_read_only_access_token)
    with patched:
        resp = await client.get(f"/api/selora_ai/frigate/events/{EVENT}/snapshot.jpg")

    assert resp.status == 403


@pytest.mark.usefixtures("view")
async def test_an_admin_needs_no_link(hass_client: Any) -> None:
    _, patched = _frigate({f"/api/events/{EVENT}/thumbnail.jpg": _JPEG})
    client = await hass_client()
    with patched:
        resp = await client.get(f"/api/selora_ai/frigate/events/{EVENT}/snapshot.jpg")
        other = await client.get(f"/api/selora_ai/frigate/events/{EVENT}/config.yml")
        bad_id = await client.get("/api/selora_ai/frigate/events/not-an-id/snapshot.jpg")

    assert resp.status == 200
    assert other.status == 404
    assert bad_id.status == 404


# ── Over MCP ──────────────────────────────────────────────────────────────────


_FOUND = {
    "events": [
        {"id": EVENT, "camera": "porch", "has_snapshot": True, "has_clip": True},
        {"id": "1730000001.5-def456", "camera": "porch", "has_snapshot": False, "has_clip": False},
    ],
    "total": 2,
}


async def _find(hass: HomeAssistant) -> dict[str, Any]:
    result = await mcp_dispatch._jsonrpc_dispatch(
        hass,
        "tools/call",
        {"name": TOOL_FIND_CAMERA_EVENTS, "arguments": {}},
        ADMIN,
    )
    return json.loads(result["content"][0]["text"])


@pytest.mark.usefixtures("view")
async def test_events_over_mcp_carry_links(hass: HomeAssistant) -> None:
    found = json.loads(json.dumps(_FOUND))
    with (
        patch(
            "custom_components.selora_ai.frigate.events.async_find_events",
            AsyncMock(return_value=found),
        ),
        patch.object(media, "get_url", return_value=EXTERNAL + "/"),
    ):
        result = await _find(hass)

    first, second = result["events"]
    assert first["snapshot_url"].startswith(
        f"{EXTERNAL}/api/selora_ai/frigate/events/{EVENT}/snapshot.jpg?authSig="
    )
    assert first["clip_url"].startswith(
        f"{EXTERNAL}/api/selora_ai/frigate/events/{EVENT}/clip.mp4?authSig="
    )
    assert "snapshot_url" in second
    assert "clip_url" not in second
    assert "15 minutes" in result["links"]


LOCAL = "http://192.168.2.84:8123"


def _deliver(hass: HomeAssistant, content: str) -> None:
    """Write the file as Selora OS does, in this test's own config dir."""
    directory = Path(hass.config.path(media.REMOTE_URL_DIR))
    directory.mkdir(exist_ok=True)
    (directory / media.REMOTE_URL_FILE).write_text(content, encoding="utf-8")


async def test_links_use_the_hub_s_remote_address(hass: HomeAssistant) -> None:
    """Connect's remote access, behind its login: not the MCP address."""
    _deliver(hass, '{"remote_url": "https://abc-def.selorabox.com/"}\n')
    with patch.object(media, "get_url", return_value=LOCAL):
        assert await media.async_link_base(hass) == "https://abc-def.selorabox.com"


async def test_without_the_file_links_are_local(hass: HomeAssistant) -> None:
    """The OS removes the file when remote access is off."""
    get_url = MagicMock(return_value=LOCAL + "/")
    with patch.object(media, "get_url", get_url):
        assert await media.async_link_base(hass) == LOCAL
    # Internal addresses allowed: a link that opens at home beats none.
    assert get_url.call_args.kwargs == {"prefer_external": True}


@pytest.mark.parametrize(
    "content",
    [
        '{"remote_url": "https://abc-def',
        "[]",
        "{}",
        '{"remote_url": 42}',
        '{"remote_url": "ftp://hub.example.com"}',
        '{"remote_url": "https://user:pw@hub.example.com"}',
        '{"remote_url": "x"}',
    ],
)
async def test_an_unusable_file_leaves_links_local(
    hass: HomeAssistant, content: str, caplog: pytest.LogCaptureFixture
) -> None:
    _deliver(hass, content)
    with patch.object(media, "get_url", return_value=LOCAL):
        assert await media.async_link_base(hass) == LOCAL
    # Why, at most, never what the file holds.
    assert "hub.example.com" not in caplog.text
    assert all(r.levelname == "DEBUG" for r in caplog.records if r.name == media.__name__)


@pytest.mark.usefixtures("view")
async def test_without_an_external_address_there_are_no_links(hass: HomeAssistant) -> None:
    """A LAN address would not open away from home, and the client cannot tell."""
    found = json.loads(json.dumps(_FOUND))
    with (
        patch(
            "custom_components.selora_ai.frigate.events.async_find_events",
            AsyncMock(return_value=found),
        ),
        patch.object(media, "get_url", side_effect=NoURLAvailableError),
    ):
        result = await _find(hass)

    assert all("snapshot_url" not in e for e in result["events"])
    assert "No links" in result["links"]


# ── The image the snapshot tool returns ───────────────────────────────────────


class _Session:
    """An aiohttp session whose GET answers with a body that arrives in parts."""

    def __init__(self, parts: list[bytes]) -> None:
        self.parts = parts

    @asynccontextmanager
    async def get(self, url: str, **kwargs: Any) -> AsyncIterator[Any]:
        response = _Upstream(200, b"".join(self.parts), {})
        response.content_length = None
        response.content_type = "image/jpeg"
        parts = self.parts

        async def iter_chunked(size: int) -> AsyncIterator[bytes]:
            for part in parts:
                yield part

        async def read(size: int) -> bytes:
            # aiohttp's StreamReader.read(n): what has arrived, up to n bytes.
            return parts[0][:size]

        response.iter_chunked = iter_chunked
        response.read = read
        yield response


async def test_a_snapshot_is_read_to_its_end(hass: HomeAssistant) -> None:
    """A snapshot arrives in several reads; the first is only the top of the frame."""
    client = media.FrigateClient(hass, "http://frigate.local:5000")
    client._session = _Session([b"\xff\xd8top", b"middle", b"bottom\xff\xd9"])

    data, content_type = await client.get_image("/api/porch/latest.jpg")

    assert data == b"\xff\xd8topmiddlebottom\xff\xd9"
    assert content_type == "image/jpeg"


async def test_an_oversized_image_is_refused(hass: HomeAssistant) -> None:
    client = media.FrigateClient(hass, "http://frigate.local:5000")
    client._session = _Session([b"x" * (5 * 1024 * 1024)] * 2)

    with pytest.raises(media.FrigateError, match="too large"):
        await client.get_image("/api/porch/latest.jpg")
