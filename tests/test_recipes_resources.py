"""Tests for installing a recipe's Lovelace card resource.

The unit under test downloads a pinned bundle, verifies it, writes it into
the config dir and registers it with Home Assistant's Lovelace resource
collection. Exercised against light fakes of that collection and of the
aiohttp session, so no network and no full ``hass``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homeassistant.components.lovelace.const import LOVELACE_DATA  # noqa: E402
import pytest

from custom_components.selora_ai.recipes.manifest import CardResourceSpec, ManifestError
from custom_components.selora_ai.recipes.resources import (
    RESOURCE_DIR,
    RESOURCE_URL_BASE,
    async_ensure_resource,
    async_prune_superseded,
    async_remove_resource,
    resource_url,
)

PAYLOAD = b"customElements.define('toothbrush-card', class extends HTMLElement {});"
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


# ── Fakes ───────────────────────────────────────────────────────────


class FakeResources:
    """Stand-in for HA's Lovelace ResourceStorageCollection."""

    def __init__(self, items: list[dict[str, Any]] | None = None) -> None:
        self._items = items or []
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    async def async_get_info(self) -> dict[str, int]:
        return {"resources": len(self._items)}

    def async_items(self) -> list[dict[str, Any]]:
        return list(self._items)

    async def async_create_item(self, data: dict[str, Any]) -> dict[str, Any]:
        item = {"id": f"id{len(self._items)}", **data}
        self._items.append(item)
        self.created.append(data)
        return item

    async def async_delete_item(self, item_id: str) -> None:
        self._items = [i for i in self._items if i.get("id") != item_id]
        self.deleted.append(item_id)


class FakeResponse:
    """Stand-in for an aiohttp response. ``chunk_size`` splits the body so
    the tests exercise reassembly rather than a single convenient read."""

    def __init__(
        self,
        payload: bytes = b"",
        status: int = 200,
        chunk_size: int = 0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._payload = payload
        self._chunk_size = chunk_size or max(len(payload), 1)
        self.content = SimpleNamespace(iter_chunked=self._iter_chunked)

    async def _iter_chunked(self, n: int) -> Any:
        for i in range(0, len(self._payload), self._chunk_size):
            yield self._payload[i : i + self._chunk_size]


class FakeRequest:
    """What aiohttp's ``session.get`` returns: an async context manager, so
    every response is released whichever way the caller leaves it."""

    def __init__(self, response: Any, error: Exception | None) -> None:
        self._response = response
        self._error = error
        self.released = False

    async def __aenter__(self) -> Any:
        if self._error:
            raise self._error
        return self._response

    async def __aexit__(self, *exc: Any) -> bool:
        self.released = True
        return False


class FakeSession:
    """One canned response, or a per-URL map for redirect chains."""

    def __init__(
        self,
        response: Any = None,
        error: Exception | None = None,
        by_url: dict[str, Any] | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self._by_url = by_url or {}
        self.requested: list[str] = []
        self.requests: list[FakeRequest] = []

    def get(self, url: str, timeout: int = 0, allow_redirects: bool = True) -> FakeRequest:
        self.requested.append(url)
        request = FakeRequest(self._by_url.get(url, self._response), self._error)
        self.requests.append(request)
        return request


def _hass(tmp_path: Path, resources: Any) -> Any:
    """Minimal hass: a config dir, the lovelace data slot, and an executor
    that just runs the job inline."""

    async def async_add_executor_job(func: Any, *args: Any) -> Any:
        return func(*args)

    return SimpleNamespace(
        config=SimpleNamespace(path=lambda *p: str(tmp_path.joinpath(*p))),
        data={LOVELACE_DATA: SimpleNamespace(resources=resources), "selora_ai": {}},
        async_add_executor_job=async_add_executor_job,
        http=SimpleNamespace(async_register_static_paths=_noop_register),
    )


async def _noop_register(paths: Any) -> None:
    return None


def _spec(**kw: Any) -> CardResourceSpec:
    base: dict[str, Any] = {
        "name": "toothbrush-card",
        "url": "https://github.com/mtheli/toothbrush-card/releases/download/v0.34.0/toothbrush-card.js",
        "version": "v0.34.0",
        "sha256": DIGEST,
    }
    base.update(kw)
    return CardResourceSpec(**base)


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    """Patch the aiohttp session the downloader pulls from HA."""
    fake = FakeSession(FakeResponse(PAYLOAD))

    from homeassistant.helpers import aiohttp_client

    monkeypatch.setattr(aiohttp_client, "async_get_clientsession", lambda hass: fake)
    return fake


class FakeRecord:
    def __init__(self, slug: str, url: str) -> None:
        self.slug = slug
        self.dashboard_card = {"resource_urls": [url]}


def _patch_records(monkeypatch: pytest.MonkeyPatch, records: list[FakeRecord]) -> None:
    """Stub the install store the pruner consults."""
    from custom_components.selora_ai.recipes import store as store_module

    class FakeStore:
        async def async_list(self) -> list[FakeRecord]:
            return records

    monkeypatch.setattr(store_module, "get_install_store", lambda hass: FakeStore())


# ── Manifest ────────────────────────────────────────────────────────


def test_resource_spec_requires_https() -> None:
    with pytest.raises(ManifestError):
        CardResourceSpec(name="c", url="http://github.com/x/y.js", version="v1").validate()


def test_resource_spec_rejects_a_malformed_digest() -> None:
    with pytest.raises(ManifestError):
        CardResourceSpec(name="c", url="https://github.com/x/y.js", sha256="nope").validate()


def test_resource_spec_requires_a_version_or_digest() -> None:
    """Neither means a filename that never changes, which would pin a home
    to whatever bytes it downloaded first."""
    with pytest.raises(ManifestError):
        CardResourceSpec(name="c", url="https://github.com/x/y.js").validate()


def test_resource_url_uses_the_digest_alone_when_unversioned() -> None:
    spec = CardResourceSpec(name="toothbrush-card", url="https://github.com/x/y.js", sha256=DIGEST)
    assert resource_url(spec) == f"{RESOURCE_URL_BASE}/toothbrush-card-{DIGEST[:12]}.js"


def test_resource_url_carries_the_version_and_digest() -> None:
    """A new pin has to be a new URL: browsers cache module URLs hard, and
    an already-registered URL is never re-verified."""
    assert resource_url(_spec()) == f"{RESOURCE_URL_BASE}/toothbrush-card-v0.34.0-{DIGEST[:12]}.js"


# ── Install ─────────────────────────────────────────────────────────


async def test_downloads_writes_and_registers(tmp_path: Path, session: FakeSession) -> None:
    resources = FakeResources()
    hass = _hass(tmp_path, resources)

    result = await async_ensure_resource(hass, _spec())

    assert result.ok and result.reason == "installed"
    written = tmp_path / RESOURCE_DIR / f"toothbrush-card-v0.34.0-{DIGEST[:12]}.js"
    assert written.read_bytes() == PAYLOAD
    assert resources.created == [
        {
            "res_type": "module",
            "url": f"{RESOURCE_URL_BASE}/toothbrush-card-v0.34.0-{DIGEST[:12]}.js",
        }
    ]


async def test_already_registered_is_left_alone(tmp_path: Path, session: FakeSession) -> None:
    """Re-installing must not hand the frontend the same module twice."""
    name = f"toothbrush-card-v0.34.0-{DIGEST[:12]}.js"
    url = f"{RESOURCE_URL_BASE}/{name}"
    (tmp_path / RESOURCE_DIR).mkdir(parents=True)
    (tmp_path / RESOURCE_DIR / name).write_bytes(PAYLOAD)
    resources = FakeResources([{"id": "a", "url": f"{url}?v=1", "type": "module"}])
    hass = _hass(tmp_path, resources)

    result = await async_ensure_resource(hass, _spec())

    assert result.ok and result.reason == "present"
    assert resources.created == []
    assert session.requested == []  # nothing downloaded either


async def test_checksum_mismatch_is_refused(tmp_path: Path, session: FakeSession) -> None:
    resources = FakeResources()
    hass = _hass(tmp_path, resources)

    result = await async_ensure_resource(hass, _spec(sha256="0" * 64))

    assert not result.ok and result.reason == "checksum_mismatch"
    assert not (tmp_path / RESOURCE_DIR).exists() or not list((tmp_path / RESOURCE_DIR).iterdir())
    assert resources.created == []


async def test_non_github_url_is_refused(tmp_path: Path, session: FakeSession) -> None:
    resources = FakeResources()
    hass = _hass(tmp_path, resources)

    result = await async_ensure_resource(hass, _spec(url="https://example.com/card.js"))

    assert not result.ok and result.reason == "unsupported_url"
    assert session.requested == []


async def test_http_url_is_refused(tmp_path: Path, session: FakeSession) -> None:
    resources = FakeResources()
    hass = _hass(tmp_path, resources)

    result = await async_ensure_resource(hass, _spec(url="http://github.com/a/b.js"))

    assert not result.ok and result.reason == "unsupported_url"


async def test_error_status_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from homeassistant.helpers import aiohttp_client

    monkeypatch.setattr(
        aiohttp_client,
        "async_get_clientsession",
        lambda hass: FakeSession(FakeResponse(b"", status=404)),
    )
    resources = FakeResources()

    result = await async_ensure_resource(_hass(tmp_path, resources), _spec())

    assert not result.ok and result.reason == "download_failed"
    assert "404" in result.message


async def test_network_failure_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from homeassistant.helpers import aiohttp_client

    monkeypatch.setattr(
        aiohttp_client,
        "async_get_clientsession",
        lambda hass: FakeSession(error=TimeoutError("too slow")),
    )

    result = await async_ensure_resource(_hass(tmp_path, FakeResources()), _spec())

    assert not result.ok and result.reason == "download_failed"


async def test_oversized_download_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from homeassistant.helpers import aiohttp_client

    from custom_components.selora_ai.recipes import resources as module

    monkeypatch.setattr(module, "MAX_BYTES", 8)
    monkeypatch.setattr(
        aiohttp_client,
        "async_get_clientsession",
        lambda hass: FakeSession(FakeResponse(b"x" * 64)),
    )

    result = await async_ensure_resource(_hass(tmp_path, FakeResources()), _spec(sha256=""))

    assert not result.ok and result.reason == "too_large"


async def test_unverified_download_is_allowed_without_a_digest(
    tmp_path: Path, session: FakeSession
) -> None:
    """sha256 is optional in the schema so a recipe can be authored before
    the digest is known; review is where it becomes mandatory."""
    result = await async_ensure_resource(_hass(tmp_path, FakeResources()), _spec(sha256=""))

    assert result.ok and result.reason == "installed"


async def test_missing_lovelace_is_not_fatal(tmp_path: Path, session: FakeSession) -> None:
    hass = _hass(tmp_path, None)

    result = await async_ensure_resource(hass, _spec())

    assert not result.ok and result.reason == "lovelace_unavailable"


# ── Remove ──────────────────────────────────────────────────────────


async def test_remove_deregisters_and_deletes(tmp_path: Path, session: FakeSession) -> None:
    resources = FakeResources()
    hass = _hass(tmp_path, resources)
    installed = await async_ensure_resource(hass, _spec())

    removed = await async_remove_resource(hass, installed.url)

    assert removed
    assert resources.async_items() == []
    assert not (tmp_path / RESOURCE_DIR / f"toothbrush-card-v0.34.0-{DIGEST[:12]}.js").exists()


async def test_remove_ignores_resources_we_did_not_install(tmp_path: Path) -> None:
    """A card the homeowner installed themselves survives the uninstall."""
    resources = FakeResources([{"id": "a", "url": "/hacsfiles/toothbrush-card/x.js"}])
    hass = _hass(tmp_path, resources)

    removed = await async_remove_resource(hass, "/hacsfiles/toothbrush-card/x.js")

    assert not removed
    assert resources.deleted == []


async def test_installing_a_new_version_drops_the_old_one(
    tmp_path: Path, session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two modules defining the same element is a race, not a fallback."""
    _patch_records(monkeypatch, [])
    resources = FakeResources()
    hass = _hass(tmp_path, resources)
    await async_ensure_resource(hass, _spec())
    old_file = tmp_path / RESOURCE_DIR / f"toothbrush-card-v0.34.0-{DIGEST[:12]}.js"
    assert old_file.exists()

    await async_ensure_resource(hass, _spec(version="v0.35.0"))
    await async_prune_superseded(hass, _spec(version="v0.35.0"))

    urls = [i["url"] for i in resources.async_items()]
    assert urls == [f"{RESOURCE_URL_BASE}/toothbrush-card-v0.35.0-{DIGEST[:12]}.js"]
    assert not old_file.exists()


async def test_pruning_leaves_other_bundles_alone(tmp_path: Path, session: FakeSession) -> None:
    other = {"id": "x", "url": f"{RESOURCE_URL_BASE}/mini-graph-card-v1.js"}
    hacs = {"id": "y", "url": "/hacsfiles/toothbrush-card/toothbrush-card.js"}
    resources = FakeResources([other, hacs])
    hass = _hass(tmp_path, resources)

    await async_ensure_resource(hass, _spec())

    urls = {i["url"] for i in resources.async_items()}
    assert other["url"] in urls
    assert hacs["url"] in urls


async def test_failed_registration_leaves_no_file_behind(
    tmp_path: Path, session: FakeSession
) -> None:
    """The result carries no URL, so uninstall could never clean this up."""

    class Refusing(FakeResources):
        async def async_create_item(self, data: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("resources are managed via YAML")

    hass = _hass(tmp_path, Refusing())

    result = await async_ensure_resource(hass, _spec())

    assert not result.ok and result.reason == "register_failed"
    assert not list((tmp_path / RESOURCE_DIR).iterdir())


async def test_reassembles_a_chunked_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stream hands over what it has buffered; hashing the first chunk
    would fail the checksum on exactly the slow connections that need it."""
    from homeassistant.helpers import aiohttp_client

    monkeypatch.setattr(
        aiohttp_client,
        "async_get_clientsession",
        lambda hass: FakeSession(FakeResponse(PAYLOAD, chunk_size=7)),
    )

    result = await async_ensure_resource(_hass(tmp_path, FakeResources()), _spec())

    assert result.ok and result.reason == "installed"
    assert (
        tmp_path / RESOURCE_DIR / f"toothbrush-card-v0.34.0-{DIGEST[:12]}.js"
    ).read_bytes() == PAYLOAD


async def test_pruning_spares_a_version_another_recipe_owns(
    tmp_path: Path, session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pulling it would leave that recipe's card with no module behind it."""
    theirs = f"{RESOURCE_URL_BASE}/toothbrush-card-v0.33.0.js"
    _patch_records(monkeypatch, [FakeRecord("other-recipe", theirs)])
    resources = FakeResources([{"id": "a", "url": theirs}])

    hass = _hass(tmp_path, resources)
    await async_ensure_resource(hass, _spec())
    await async_prune_superseded(hass, _spec())

    assert theirs in {i["url"] for i in resources.async_items()}


async def test_pruning_replaces_the_installing_recipes_own_version(
    tmp_path: Path, session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a reinstall the store still holds this recipe's previous record;
    that is the one being replaced, not another recipe's claim."""
    old = f"{RESOURCE_URL_BASE}/toothbrush-card-v0.33.0.js"
    _patch_records(monkeypatch, [FakeRecord("brush", old)])
    resources = FakeResources([{"id": "a", "url": old}])

    hass = _hass(tmp_path, resources)
    await async_ensure_resource(hass, _spec(), owner_slug="brush")
    await async_prune_superseded(hass, _spec(), owner_slug="brush")

    assert old not in {i["url"] for i in resources.async_items()}


async def test_pruning_catches_an_earlier_unversioned_file(
    tmp_path: Path, session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recipe that shipped the bundle before pinning a version left it at
    <stem>.js, which no <stem>- prefix matches."""
    _patch_records(monkeypatch, [])
    old = f"{RESOURCE_URL_BASE}/toothbrush-card.js"
    resources = FakeResources([{"id": "a", "url": old}])

    hass = _hass(tmp_path, resources)
    await async_ensure_resource(hass, _spec())
    await async_prune_superseded(hass, _spec())

    assert {i["url"] for i in resources.async_items()} == {
        f"{RESOURCE_URL_BASE}/toothbrush-card-v0.34.0-{DIGEST[:12]}.js"
    }


# ── Ownership sticks to the record ──────────────────────────────────


def test_claims_survive_a_record_that_decides_nothing() -> None:
    """A reinstall that skipped the dashboard step writes no key at all.
    Blanking the list there would strand files with nothing left to
    attribute them to."""
    from custom_components.selora_ai.recipes.store import _keep_resource_claim

    merged = _keep_resource_claim(
        {"resource_urls": ["/selora_ai_resources/x.js"]},
        {"ok": False, "reason": "skipped"},
    )
    assert merged["resource_urls"] == ["/selora_ai_resources/x.js"]
    assert merged["reason"] == "skipped"  # everything else is the new truth


def test_an_incoming_claim_list_is_authoritative() -> None:
    """Including an empty one: a placement that ran and found nothing left
    to own has said so."""
    from custom_components.selora_ai.recipes.store import _keep_resource_claim

    merged = _keep_resource_claim(
        {"resource_urls": ["/selora_ai_resources/old.js"]},
        {"resource_urls": []},
    )
    assert merged["resource_urls"] == []


def test_a_legacy_singular_claim_is_still_carried() -> None:
    """Records written before the list existed still uninstall cleanly."""
    from custom_components.selora_ai.recipes.store import _keep_resource_claim

    merged = _keep_resource_claim({"resource_url": "/selora_ai_resources/x.js"}, {"ok": True})
    assert merged["resource_urls"] == ["/selora_ai_resources/x.js"]


def test_a_new_resource_claim_wins() -> None:
    from custom_components.selora_ai.recipes.store import _keep_resource_claim

    merged = _keep_resource_claim(
        {"resource_url": "/selora_ai_resources/old.js"},
        {"resource_url": "/selora_ai_resources/new.js"},
    )
    assert merged["resource_url"] == "/selora_ai_resources/new.js"


async def test_pruning_catches_a_bundle_this_recipe_renamed(
    tmp_path: Path, session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rename shares no prefix with the new name, so only the record ties
    the old file to this recipe."""
    old = f"{RESOURCE_URL_BASE}/brush-card-v1-abcdef123456.js"

    class Record:
        slug = "brush"
        dashboard_card = {"resource_urls": [old]}

    from custom_components.selora_ai.recipes import store as store_module

    class FakeStore:
        async def async_list(self) -> list[Any]:
            return [Record()]

        async def async_get(self, slug: str) -> Any:
            return Record() if slug == "brush" else None

    monkeypatch.setattr(store_module, "get_install_store", lambda hass: FakeStore())
    resources = FakeResources([{"id": "a", "url": old}])

    hass = _hass(tmp_path, resources)
    await async_ensure_resource(hass, _spec(), owner_slug="brush")
    await async_prune_superseded(hass, _spec(), owner_slug="brush")

    assert old not in {i["url"] for i in resources.async_items()}


# ── Redirects ───────────────────────────────────────────────────────


async def test_follows_a_redirect_within_the_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The normal case: a GitHub release link redirects to its CDN."""
    from homeassistant.helpers import aiohttp_client

    release = _spec().url
    # Where GitHub actually sends release downloads now; it was
    # objects.githubusercontent.com for years before that.
    cdn = "https://release-assets.githubusercontent.com/blob/toothbrush-card.js"
    fake = FakeSession(
        by_url={
            release: FakeResponse(status=302, headers={"Location": cdn}),
            cdn: FakeResponse(PAYLOAD),
        }
    )
    monkeypatch.setattr(aiohttp_client, "async_get_clientsession", lambda hass: fake)

    result = await async_ensure_resource(_hass(tmp_path, FakeResources()), _spec())

    assert result.ok and result.reason == "installed"
    assert fake.requested == [release, cdn]


async def test_refuses_a_redirect_off_the_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Letting aiohttp follow redirects would vet only the first URL, and
    a recipe without a digest would take whatever came back."""
    from homeassistant.helpers import aiohttp_client

    release = _spec().url
    elsewhere = "https://evil.example.com/payload.js"
    fake = FakeSession(
        by_url={
            release: FakeResponse(status=302, headers={"Location": elsewhere}),
            elsewhere: FakeResponse(b"alert(1)"),
        }
    )
    monkeypatch.setattr(aiohttp_client, "async_get_clientsession", lambda hass: fake)

    result = await async_ensure_resource(_hass(tmp_path, FakeResources()), _spec(sha256=""))

    assert not result.ok and result.reason == "unsupported_url"
    assert fake.requested == [release]  # never fetched


async def test_refuses_an_endless_redirect_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from homeassistant.helpers import aiohttp_client

    release = _spec().url
    fake = FakeSession(FakeResponse(status=302, headers={"Location": release}))
    monkeypatch.setattr(aiohttp_client, "async_get_clientsession", lambda hass: fake)

    result = await async_ensure_resource(_hass(tmp_path, FakeResources()), _spec())

    assert not result.ok and result.reason == "download_failed"
    assert "redirect" in result.message


# ── Repair ──────────────────────────────────────────────────────────


async def test_registered_but_missing_file_is_re_downloaded(
    tmp_path: Path, session: FakeSession
) -> None:
    """A restore that skipped the directory leaves a registration whose URL
    404s, and nothing would ever repair it."""
    url = f"{RESOURCE_URL_BASE}/toothbrush-card-v0.34.0-{DIGEST[:12]}.js"
    resources = FakeResources([{"id": "a", "url": url, "type": "module"}])
    hass = _hass(tmp_path, resources)

    result = await async_ensure_resource(hass, _spec())

    # "restored", not "installed": the registration predates this call, so a
    # caller rolling back a failure must not take it away.
    assert result.ok and result.reason == "restored"
    assert (tmp_path / RESOURCE_DIR / f"toothbrush-card-v0.34.0-{DIGEST[:12]}.js").exists()
    # Re-registering would load the module twice.
    assert resources.created == []


async def test_remove_keeps_the_file_when_deregistration_fails(
    tmp_path: Path, session: FakeSession
) -> None:
    """A registration pointing at a deleted file is a permanent 404; a
    stray file nobody loads is not."""

    class Stubborn(FakeResources):
        async def async_delete_item(self, item_id: str) -> None:
            raise RuntimeError("resources are managed via YAML")

    hass = _hass(tmp_path, Stubborn())
    installed = await async_ensure_resource(hass, _spec())
    path = tmp_path / RESOURCE_DIR / Path(installed.url).name
    assert path.exists()

    removed = await async_remove_resource(hass, installed.url)

    assert not removed
    assert path.exists()


async def test_remove_deletes_the_file_when_nothing_is_registered(
    tmp_path: Path, session: FakeSession
) -> None:
    """Deregistration is trivially complete when there is nothing to
    deregister, so the leftover file should still go."""
    hass = _hass(tmp_path, FakeResources())
    installed = await async_ensure_resource(hass, _spec())
    path = tmp_path / RESOURCE_DIR / Path(installed.url).name
    # Drop the registration behind its back, as a hand-edit would.
    hass.data[LOVELACE_DATA].resources._items.clear()

    await async_remove_resource(hass, installed.url)

    assert not path.exists()


async def test_prune_can_drop_every_managed_copy(
    tmp_path: Path, session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the card ends up running on a HACS copy, ours is surplus."""
    _patch_records(monkeypatch, [])
    ours = f"{RESOURCE_URL_BASE}/toothbrush-card-v0.34.0-{DIGEST[:12]}.js"
    hacs = "/hacsfiles/toothbrush-card/toothbrush-card.js"
    resources = FakeResources([{"id": "a", "url": ours}, {"id": "b", "url": hacs}])
    hass = _hass(tmp_path, resources)

    await async_prune_superseded(hass, _spec(), owner_slug="brush", keep_url="")

    urls = {i["url"] for i in resources.async_items()}
    assert urls == {hacs}


async def test_unservable_download_leaves_no_file(tmp_path: Path, session: FakeSession) -> None:
    """No result carries its URL, so nothing could ever clean it up."""
    hass = _hass(tmp_path, FakeResources())
    # An http component with neither the modern nor the legacy registrar:
    # whatever we downloaded could never be served.
    hass.http = SimpleNamespace()

    result = await async_ensure_resource(hass, _spec())

    assert not result.ok and result.reason == "serve_failed"
    assert not list((tmp_path / RESOURCE_DIR).iterdir())


async def test_lookalike_hosts_are_not_github(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The suffix check hangs off the leading dot, so a host that merely
    ends in those characters is not GitHub."""
    from homeassistant.helpers import aiohttp_client

    fake = FakeSession(FakeResponse(PAYLOAD))
    monkeypatch.setattr(aiohttp_client, "async_get_clientsession", lambda hass: fake)

    for host in ("evilgithubusercontent.com", "githubusercontent.com.evil.test"):
        result = await async_ensure_resource(
            _hass(tmp_path, FakeResources()), _spec(url=f"https://{host}/card.js")
        )
        assert not result.ok and result.reason == "unsupported_url"
    assert fake.requested == []


async def test_concurrent_installs_register_once(tmp_path: Path, session: FakeSession) -> None:
    """HA's resource collection does not deduplicate by URL, so two
    installs racing would hand the frontend the same module twice."""
    import asyncio

    resources = FakeResources()
    hass = _hass(tmp_path, resources)

    results = await asyncio.gather(
        async_ensure_resource(hass, _spec()),
        async_ensure_resource(hass, _spec()),
    )

    assert all(r.ok for r in results)
    assert len(resources.created) == 1
    assert {r.reason for r in results} == {"installed", "present"}


async def test_a_corrupted_file_is_replaced(tmp_path: Path, session: FakeSession) -> None:
    """The name carries the digest, but the bytes are what get served."""
    name = f"toothbrush-card-v0.34.0-{DIGEST[:12]}.js"
    url = f"{RESOURCE_URL_BASE}/{name}"
    (tmp_path / RESOURCE_DIR).mkdir(parents=True)
    (tmp_path / RESOURCE_DIR / name).write_bytes(b"truncated")
    resources = FakeResources([{"id": "a", "url": url, "type": "module"}])
    hass = _hass(tmp_path, resources)

    result = await async_ensure_resource(hass, _spec())

    assert result.ok and result.reason == "restored"
    assert (tmp_path / RESOURCE_DIR / name).read_bytes() == PAYLOAD
    assert resources.created == []  # the registration was already there


async def test_drop_leaves_a_shared_url_for_its_other_owner(
    tmp_path: Path, session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file stays because someone still needs it. Both records go on
    claiming it, and it goes when the last of them does."""
    from custom_components.selora_ai.recipes.resources import async_drop_if_unshared

    url = f"{RESOURCE_URL_BASE}/toothbrush-card-v0.34.0-{DIGEST[:12]}.js"
    _patch_records(monkeypatch, [FakeRecord("other-recipe", url)])
    resources = FakeResources([{"id": "a", "url": url}])

    await async_drop_if_unshared(_hass(tmp_path, resources), url, owner_slug="brush")

    assert resources.async_items()


async def test_drop_keeps_a_url_when_ownership_is_unreadable(
    tmp_path: Path, session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown is not "nobody else": leave it, and let the claim on the
    record bring uninstall back to it."""
    from custom_components.selora_ai.recipes import store as store_module
    from custom_components.selora_ai.recipes.resources import async_drop_if_unshared

    class BrokenStore:
        async def async_list(self) -> list[Any]:
            raise RuntimeError("storage unavailable")

    monkeypatch.setattr(store_module, "get_install_store", lambda hass: BrokenStore())
    url = f"{RESOURCE_URL_BASE}/toothbrush-card-v0.34.0-{DIGEST[:12]}.js"
    resources = FakeResources([{"id": "a", "url": url}])

    await async_drop_if_unshared(_hass(tmp_path, resources), url, owner_slug="brush")

    assert resources.async_items()


async def test_drop_removes_a_url_nobody_else_claims(
    tmp_path: Path, session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_records(monkeypatch, [])
    hass = _hass(tmp_path, FakeResources())
    installed = await async_ensure_resource(hass, _spec())

    from custom_components.selora_ai.recipes.resources import async_drop_if_unshared

    await async_drop_if_unshared(hass, installed.url, owner_slug="brush")

    assert hass.data[LOVELACE_DATA].resources.async_items() == []


async def test_drop_ignores_a_url_that_is_not_ours(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.selora_ai.recipes.resources import async_drop_if_unshared

    _patch_records(monkeypatch, [])
    hacs = "/hacsfiles/toothbrush-card/toothbrush-card.js"
    resources = FakeResources([{"id": "a", "url": hacs}])

    await async_drop_if_unshared(_hass(tmp_path, resources), hacs, owner_slug="brush")

    assert resources.async_items()


async def test_remove_reports_success_for_an_already_absent_registration(
    tmp_path: Path, session: FakeSession
) -> None:
    """A prune earlier in the same install may have taken the registration
    out. Calling that a failure would leave an ownership claim on
    something that no longer exists, which then blocks whoever else shares
    the URL from ever cleaning it up."""
    hass = _hass(tmp_path, FakeResources())
    installed = await async_ensure_resource(hass, _spec())
    hass.data[LOVELACE_DATA].resources._items.clear()  # as a prune would leave it

    assert await async_remove_resource(hass, installed.url)
    assert not (tmp_path / RESOURCE_DIR / Path(installed.url).name).exists()


async def test_every_response_is_released(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A redirect response left unread holds its connection, and an install
    that redirects once per attempt would work through the connector pool
    until later downloads simply hang."""
    from homeassistant.helpers import aiohttp_client

    release = _spec().url
    cdn = "https://release-assets.githubusercontent.com/blob/toothbrush-card.js"
    fake = FakeSession(
        by_url={
            release: FakeResponse(status=302, headers={"Location": cdn}),
            cdn: FakeResponse(PAYLOAD),
        }
    )
    monkeypatch.setattr(aiohttp_client, "async_get_clientsession", lambda hass: fake)

    await async_ensure_resource(_hass(tmp_path, FakeResources()), _spec())

    assert len(fake.requests) == 2
    assert all(r.released for r in fake.requests)


async def test_uninstall_claims_survive_an_unreadable_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An orphan uninstall comes back to beats a file nobody is
    responsible for, so a registry we can't read keeps the claim."""
    from custom_components.selora_ai.recipes.resources import async_registered_urls

    class Unreadable(FakeResources):
        async def async_get_info(self) -> dict[str, int]:
            raise RuntimeError("storage unavailable")

    assert await async_registered_urls(_hass(tmp_path, Unreadable())) is None
    assert await async_registered_urls(_hass(tmp_path, None)) is None


async def test_registered_urls_strips_cache_busters(tmp_path: Path) -> None:
    resources = FakeResources([{"id": "a", "url": "/selora_ai_resources/x.js?v=2"}])
    from custom_components.selora_ai.recipes.resources import async_registered_urls

    assert await async_registered_urls(_hass(tmp_path, resources)) == {"/selora_ai_resources/x.js"}


async def test_a_failed_repair_reports_itself_as_such(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registration there, file not, and the download to replace it fails:
    the browser is told to load a module that 404s, which is a different
    failure from a fresh install falling over."""
    from homeassistant.helpers import aiohttp_client

    monkeypatch.setattr(
        aiohttp_client,
        "async_get_clientsession",
        lambda hass: FakeSession(FakeResponse(b"", status=503)),
    )
    url = f"{RESOURCE_URL_BASE}/toothbrush-card-v0.34.0-{DIGEST[:12]}.js"
    resources = FakeResources([{"id": "a", "url": url, "type": "module"}])

    result = await async_ensure_resource(_hass(tmp_path, resources), _spec())

    assert not result.ok and result.reason == "repair_failed"


async def test_a_failed_fresh_install_is_not_a_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from homeassistant.helpers import aiohttp_client

    monkeypatch.setattr(
        aiohttp_client,
        "async_get_clientsession",
        lambda hass: FakeSession(FakeResponse(b"", status=503)),
    )

    result = await async_ensure_resource(_hass(tmp_path, FakeResources()), _spec())

    assert not result.ok and result.reason == "download_failed"
