"""Install the Lovelace card resources a recipe's dashboard card needs.

A recipe that places a ``custom:`` card is only half-installed if the
JavaScript defining that element isn't there: Home Assistant renders a red
"Custom element doesn't exist" box, and the homeowner is left to go and
fetch it by hand. Recipes exist so nobody has to do that, so a recipe can
declare where its card comes from and the pipeline fetches it.

What this does, in order, when a declared resource isn't registered yet:

1. Download the manifest's pinned URL (HTTPS, from an allow-listed host).
2. Verify the SHA-256 when the manifest declares one, and refuse the file
   otherwise unverified content would land in the frontend.
3. Write it under ``<config>/selora_ai_resources/``.
4. Serve that directory at ``/selora_ai_resources/`` — our own static
   path, the way HACS serves ``/hacsfiles/``. Writing into ``config/www``
   instead would depend on that directory having existed at boot for
   ``/local`` to be registered, which for many homes it hasn't.
5. Register the Lovelace resource so the frontend loads the module.

Uninstall reverses 5 and 3, so a recipe leaves nothing of ours behind.

Deliberately NOT a package manager: no update checks, no dependency
resolution, no "latest". A recipe pins one version and one digest; moving
to a newer card is a recipe change, reviewed like any other. That keeps
the trust story simple — the bytes that reach a home's frontend are the
bytes the recipe author signed off on.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import hashlib
import logging
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .manifest import CardResourceSpec

_LOGGER = logging.getLogger(__name__)

# Directory under the HA config dir where downloaded card JS lands, and the
# URL path it is served at. Kept out of ``config/www`` on purpose — see the
# module docstring.
RESOURCE_DIR = "selora_ai_resources"
RESOURCE_URL_BASE = "/selora_ai_resources"

# Hosts a recipe may download a card from: github.com and its content
# domain. A release link redirects to whichever asset host GitHub is using
# at the time — objects.githubusercontent.com for years, then
# release-assets.githubusercontent.com — so the suffix is what's checked
# rather than a list of names that goes stale the next time they move.
# Both are GitHub-controlled and no less trustworthy than github.com
# itself, which is the actual line being drawn here.
ALLOWED_HOSTS = frozenset({"github.com"})
ALLOWED_HOST_SUFFIX = ".githubusercontent.com"

# A card bundle is a JS file. 10 MB is roughly 30x the largest card we ship
# and small enough that a redirect to something unexpected can't fill a
# disk before we notice.
MAX_BYTES = 10 * 1024 * 1024
DOWNLOAD_TIMEOUT = 60
# GitHub release links redirect once, to objects.githubusercontent.com.
# A handful of hops is generous; a chain longer than that is a sign the
# link is not what the recipe thinks it is.
MAX_REDIRECTS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

# Held across check → download → register. Two recipes installing the same
# card at once would otherwise both find it absent and both register it,
# and HA's resource collection does not deduplicate by URL: the frontend
# would load the module twice and the second customElements.define throws.
#
# It does not cover placement and pruning, which happen after the caller
# gets its result. Two recipes installing *different* pins of one bundle
# at the same instant could therefore each prune the other's version,
# since neither install record exists yet. Closing that needs the record
# write inside the lock, which means holding it across the whole install
# pipeline — a lot of contention to buy safety in a race that needs two
# recipes shipping the same card at different pins, installed in the same
# breath.
_INSTALL_LOCK = asyncio.Lock()


@dataclass(frozen=True, slots=True)
class ResourceResult:
    """Outcome of ensuring one card resource is present.

    ``ok`` False is never fatal to an install: the package is already
    live, and a card we couldn't fetch degrades to the same "here's what
    to install" advisory as a card we couldn't place.
    """

    ok: bool
    # Stable reason code: "present" (already registered), "installed"
    # (registered by this call), "restored" (file re-fetched behind a
    # registration that was already there),
    # "repair_failed" (registered, but its file could not be replaced),
    # "unsupported_url", "download_failed", "checksum_mismatch",
    # "too_large", "write_failed", "register_failed", "serve_failed",
    # "lovelace_unavailable".
    reason: str
    url: str = ""
    message: str = ""


def _safe_filename(name: str, version: str, sha256: str = "") -> str:
    """``toothbrush-card`` + ``v0.34.0`` -> ``toothbrush-card-v0.34.0.js``.

    Something that changes with the content has to ride in the filename.
    Browsers cache module URLs hard, and an already-registered URL is
    taken as "the home has this", so a stable name would pin a home to
    the first bytes it ever downloaded: a recipe revising its pinned card
    would never reach it. The version is the readable choice; a digest
    prefix stands in when a recipe pins bytes without naming a version.
    """
    stem = re.sub(r"[^a-zA-Z0-9._-]", "-", name).strip("-.") or "card"
    parts = [p for p in (re.sub(r"[^a-zA-Z0-9._-]", "-", version).strip("-."),) if p]
    # Both, when both are given. A version alone is a promise about the
    # bytes, not proof: upstream can rebuild a tag, and two recipes can
    # pin the same name and version to different files. Either would map
    # to one URL, and a URL already registered is never re-verified.
    if sha256:
        parts.append(sha256.strip().lower()[:12])
    return f"{stem}-{'-'.join(parts)}.js" if parts else f"{stem}.js"


def resource_url(spec: CardResourceSpec) -> str:
    """Public URL the Lovelace resource will point at."""
    return f"{RESOURCE_URL_BASE}/{_safe_filename(spec.name, spec.version, spec.sha256)}"


def record_claims(dashboard_card: dict[str, Any] | None) -> list[str]:
    """The managed URLs an install record says a recipe must clean up.

    A recipe can be on the hook for more than one at a time: an upgrade
    whose pruning of the old version failed still owns it, alongside the
    new one. Reads the singular key too, so records written before the
    list existed still uninstall cleanly.
    """
    card = dashboard_card or {}
    urls = card.get("resource_urls")
    if isinstance(urls, (list, tuple)):
        return [str(u) for u in urls if u]
    legacy = str(card.get("resource_url", ""))
    return [legacy] if legacy else []


def _url_supported(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        return False
    # Suffix match on the leading dot, so "evilgithubusercontent.com" and
    # "githubusercontent.com.evil.test" are not GitHub.
    return host in ALLOWED_HOSTS or host.endswith(ALLOWED_HOST_SUFFIX)


def _lovelace_resources(hass: HomeAssistant) -> Any | None:
    """HA's Lovelace resource collection, or None when unavailable."""
    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA  # noqa: PLC0415
    except ImportError:  # pragma: no cover — lovelace ships with core
        return None
    return getattr(hass.data.get(LOVELACE_DATA), "resources", None)


async def _registered_items(resources: Any) -> list[dict[str, Any]]:
    """Every registered resource. ``async_get_info`` first: it loads the
    storage collection, which ``async_items`` alone doesn't, and an
    unloaded collection reads as "nothing installed"."""
    await resources.async_get_info()
    return [i for i in (resources.async_items() or []) if isinstance(i, dict)]


async def async_registered_urls(hass: HomeAssistant) -> set[str] | None:
    """Every registered Lovelace resource URL, query strings stripped.

    ``None`` when the collection can't be read. Callers deciding what a
    recipe still owns treat that as "keep the claim": an orphan they come
    back to beats a file nobody is responsible for.
    """
    resources = _lovelace_resources(hass)
    if resources is None:
        return None
    try:
        items = await _registered_items(resources)
    except Exception:  # noqa: BLE001 — unreadable means "we don't know"
        _LOGGER.debug("Could not read Lovelace resources", exc_info=True)
        return None
    return {str(i.get("url", "")).split("?")[0] for i in items}


async def async_is_registered(hass: HomeAssistant, url: str) -> bool:
    """Whether ``url`` is already a registered Lovelace resource.

    Lets the caller tell "this home has our copy from an earlier install"
    from "this home has the card from somewhere else", which decide
    different things: the first keeps ownership on the new record, the
    second means there is nothing for us to install. An unreadable
    collection answers False — for this question, "we can't see it" and
    "it isn't there" both mean there is nothing to build on.
    """
    registered = await async_registered_urls(hass)
    return bool(registered and url.split("?")[0] in registered)


async def async_register_static_path(hass: HomeAssistant) -> bool:
    """Serve the resource directory, reporting whether it is being served.

    Idempotent, and safe to call before anything has been downloaded —
    the directory is created here. False means the URL would 404, which
    makes registering a Lovelace resource against it worse than useless:
    the card would render the missing-element error this whole path
    exists to avoid.

    Registered at integration setup so resources installed by an earlier
    run are served after a restart, and again right after a download so a
    first install doesn't need one.
    """
    if hass.data.setdefault("selora_ai", {}).get("_resource_path_registered"):
        return True
    directory = Path(hass.config.path(RESOURCE_DIR))
    await hass.async_add_executor_job(lambda: directory.mkdir(parents=True, exist_ok=True))
    try:
        from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

        await hass.http.async_register_static_paths(
            [StaticPathConfig(RESOURCE_URL_BASE, str(directory), cache_headers=True)]
        )
    except (ImportError, AttributeError):
        # Older cores have no StaticPathConfig. Falling through silently
        # would leave every downloaded resource registered with Lovelace
        # but served as a 404, so use the legacy call the panel assets
        # already fall back to.
        try:
            hass.http.register_static_path(RESOURCE_URL_BASE, str(directory), True)
        except Exception as exc:  # noqa: BLE001 — nothing left to try
            _LOGGER.warning("Could not serve %s: %s", RESOURCE_URL_BASE, exc)
            return False
    except RuntimeError as exc:
        # What HA raises for a path registered twice: the normal case on a
        # reload, not a failure.
        _LOGGER.debug("Static path %s already registered: %s", RESOURCE_URL_BASE, exc)
    hass.data["selora_ai"]["_resource_path_registered"] = True
    return True


async def async_ensure_resource(
    hass: HomeAssistant, spec: CardResourceSpec, owner_slug: str = ""
) -> ResourceResult:
    """Make sure ``spec`` is downloaded, served and registered.

    ``owner_slug`` is the recipe doing the installing. Pruning superseded
    versions consults the install records to avoid pulling a bundle
    another recipe pinned, and on a reinstall the record still on file
    for this recipe is the one being replaced — without the slug, a
    recipe upgrading its own card would decline to prune itself.

    Returns a non-ok result rather than raising: every failure here is
    "the card can't be placed", which the caller reports as an advisory.
    """
    async with _INSTALL_LOCK:
        return await _async_ensure_resource(hass, spec, owner_slug)


async def _async_ensure_resource(
    hass: HomeAssistant, spec: CardResourceSpec, owner_slug: str = ""
) -> ResourceResult:
    """The body of :func:`async_ensure_resource`, run under the lock."""
    resources = _lovelace_resources(hass)
    if resources is None:
        return ResourceResult(
            ok=False,
            reason="lovelace_unavailable",
            message="Home Assistant's dashboard resources are not available.",
        )

    target_url = resource_url(spec)
    try:
        items = await _registered_items(resources)
    except Exception:  # noqa: BLE001 — an unreadable collection is not fatal
        _LOGGER.debug("Could not read Lovelace resources", exc_info=True)
        items = []

    path = Path(hass.config.path(RESOURCE_DIR)) / _safe_filename(
        spec.name, spec.version, spec.sha256
    )
    registered = any(str(i.get("url", "")).split("?")[0] == target_url for i in items)
    on_disk = await hass.async_add_executor_job(path.is_file)
    if on_disk and spec.sha256 and not await _async_file_matches(hass, path, spec.sha256):
        # Digest in the filename or not, the bytes on disk are what get
        # served: a truncated write or a half-finished restore leaves a
        # file that looks right by name. Treat it as absent and fetch it
        # again rather than serving something the recipe never vouched for.
        _LOGGER.warning("Replacing %s: contents do not match the recipe's digest", path)
        on_disk = False
    if registered and on_disk:
        # Already ours and already registered. Re-registering would give
        # the frontend the same module twice. Still prune: this recipe may
        # be moving onto a pin another recipe had already installed, and
        # its own older file would otherwise be left with nothing pointing
        # at it once the record's claim moves to this URL.
        if not await async_register_static_path(hass):
            return ResourceResult(
                ok=False,
                reason="serve_failed",
                message=f"{spec.name} is installed but cannot be served by Home Assistant.",
            )
        return ResourceResult(ok=True, reason="present", url=target_url)
    if registered and not on_disk:
        # A restore that skipped the directory, or someone tidying up. The
        # registration says the home has the card while the URL 404s, and
        # nothing would ever repair it: fetch the file again and reuse the
        # registration that is already there.
        _LOGGER.info("Re-downloading %s: registered but missing on disk", target_url)

    if not _url_supported(spec.url):
        return ResourceResult(
            ok=False,
            reason="unsupported_url",
            message=(
                f"{spec.name} can't be downloaded: recipes may only fetch card "
                f"resources over HTTPS from GitHub."
            ),
        )

    payload = await _async_download(hass, spec)
    if isinstance(payload, ResourceResult):
        if registered:
            # The registration is there and the file behind it is not, so
            # the browser gets a 404 for a module it is told to load. That
            # is not the same failure as a fresh install falling over, and
            # the caller has to know: any card relying on this element is
            # broken until the file comes back.
            return replace(payload, reason="repair_failed")
        return payload

    directory = Path(hass.config.path(RESOURCE_DIR))

    def _write() -> None:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    try:
        await hass.async_add_executor_job(_write)
    except OSError as exc:
        _LOGGER.warning("Could not write %s: %s", path, exc)
        return ResourceResult(
            ok=False,
            reason="write_failed",
            message=f"{spec.name} could not be saved: {exc}",
        )

    if not await async_register_static_path(hass):
        # Same reasoning as a failed registration: nothing points at the
        # file and no result carries its URL, so it would sit there
        # forever. (A repair keeps its file — the registration that
        # survives still needs one.)
        if not registered:
            await hass.async_add_executor_job(lambda: path.unlink(missing_ok=True))
        return ResourceResult(
            ok=False,
            reason="serve_failed",
            message=f"{spec.name} was downloaded but cannot be served by Home Assistant.",
        )

    if registered:
        # Repairing a missing file behind an existing registration; adding
        # it again would load the module twice. Reported as its own
        # outcome, not as an install: the registration predates this call,
        # another recipe may claim it and a card may already be running on
        # it, so a later failure here must not roll it back.
        _LOGGER.info("Restored card resource %s", target_url)
        return ResourceResult(ok=True, reason="restored", url=target_url)

    try:
        await resources.async_create_item({"res_type": "module", "url": target_url})
    except Exception as exc:  # noqa: BLE001 — HA raises several types here
        _LOGGER.warning("Could not register resource %s: %s", target_url, exc)
        # Nothing references the file we just wrote, and the failed result
        # carries no URL for uninstall to clean up later, so it would sit
        # there forever. Take it back out now.
        await hass.async_add_executor_job(lambda: path.unlink(missing_ok=True))
        return ResourceResult(
            ok=False,
            reason="register_failed",
            message=f"{spec.name} was downloaded but could not be registered: {exc}",
        )

    # Pruning waits for the card: nothing says a recipe's next revision
    # ships the same custom element, so a placement that fails after we
    # dropped the previous bundle would leave the old card with no module
    # behind it. Two modules registered for a moment is the cheaper
    # failure, and the next successful placement clears it.
    _LOGGER.info("Installed card resource %s (%s)", spec.name, spec.version or "unversioned")
    return ResourceResult(ok=True, reason="installed", url=target_url)


async def async_prune_superseded(
    hass: HomeAssistant,
    spec: CardResourceSpec,
    owner_slug: str = "",
    keep_url: str | None = None,
) -> None:
    """Drop what this recipe's card no longer needs.

    ``keep_url`` defaults to the pin the spec names. Pass ``""`` when the
    card is being served by someone else's copy — a HACS install that
    turned up after ours — and every managed copy of this bundle should
    go, other recipes' claims excepted.

    Called once the new card is on the dashboard, never before: pruning
    at install time means a placement that then fails has taken the
    module out from under the card still sitting there. An old bundle
    left registered for a few seconds longer costs nothing; one removed
    while something still points at it is a red error box.
    """
    resources = _lovelace_resources(hass)
    if resources is None:
        return
    keep = resource_url(spec) if keep_url is None else keep_url
    await _async_prune_old_versions(hass, resources, spec, keep, owner_slug)


async def async_drop_if_unshared(hass: HomeAssistant, url: str, owner_slug: str = "") -> None:
    """Take out a managed resource this recipe has finished with, unless
    another recipe's record still claims it.

    Best-effort and silent about the outcome: what the caller records is
    not "did this work" but "is the URL still registered", which it asks
    the resource collection directly. A file another recipe needs stays,
    and both records go on claiming it until the last of them is gone.
    """
    if not url.startswith(f"{RESOURCE_URL_BASE}/"):
        return
    spoken_for = await _urls_other_recipes_own(hass, owner_slug)
    if spoken_for is None:
        # Unknown ownership: leave it. The claim survives on the record,
        # so a later uninstall comes back to this.
        return
    if url.split("?")[0] in spoken_for:
        _LOGGER.debug("Keeping %s: another recipe still owns it", url)
        return
    await async_remove_resource(hass, url)


async def _urls_other_recipes_own(hass: HomeAssistant, exclude_slug: str = "") -> set[str] | None:
    """Resource URLs recorded by installed recipes, bar one.

    ``None`` when the records can't be read: unknown ownership is not the
    same as no ownership, and the caller declines to prune rather than
    risk deleting a bundle another recipe's card is running on.

    Imported lazily: the install store knows about recipes, and a
    file-downloading module has no business depending on that at import
    time. An unreadable store yields an empty set, which only makes
    pruning more conservative than it needs to be.
    """
    try:
        from .store import get_install_store  # noqa: PLC0415

        records = await get_install_store(hass).async_list()
    except Exception:  # noqa: BLE001 — housekeeping, never fatal
        _LOGGER.debug("Could not read install records", exc_info=True)
        return None
    owned: set[str] = set()
    for record in records:
        if record.slug == exclude_slug:
            continue
        for url in record_claims(record.dashboard_card):
            owned.add(url.split("?")[0])
    return owned


async def _urls_this_recipe_owns(hass: HomeAssistant, slug: str) -> set[str]:
    """The resource URLs this recipe's own record still claims."""
    try:
        from .store import get_install_store  # noqa: PLC0415

        record = await get_install_store(hass).async_get(slug)
    except Exception:  # noqa: BLE001 — housekeeping, never fatal
        return set()
    if record is None:
        return set()
    return {url.split("?")[0] for url in record_claims(record.dashboard_card)}


async def _async_prune_old_versions(
    hass: HomeAssistant,
    resources: Any,
    spec: CardResourceSpec,
    keep_url: str,
    owner_slug: str = "",
) -> None:
    """Drop our own earlier builds of the same bundle.

    A recipe that moves to a newer card writes a new versioned file, and
    leaving the previous one registered would have the frontend load two
    modules defining the same element — whichever loses the race wins the
    dashboard. Only files under our base with this bundle's name are
    touched, so another recipe's card and anything from HACS is safe.
    """
    stem = _safe_filename(spec.name, "").removesuffix(".js")
    prefix = f"{RESOURCE_URL_BASE}/{stem}-"
    # An earlier revision of the recipe may have shipped the bundle with
    # no version at all, which lands at <stem>.js and matches no prefix.
    unversioned = f"{RESOURCE_URL_BASE}/{stem}.js"
    try:
        items = await _registered_items(resources)
    except Exception:  # noqa: BLE001 — pruning is housekeeping, never fatal
        return

    # Another recipe may have pinned an older version of the same bundle
    # and recorded it as its own. Pulling it here would leave that recipe
    # with a card and no module behind it.
    #
    # Two recipes on two versions therefore leaves both registered, and
    # the second customElements.define() call throws: whichever module
    # loads first defines the element for both cards. That is the lesser
    # evil — one card silently running the other's version beats one card
    # certainly broken — and it needs two recipes shipping the same card
    # at different pins to happen at all. Recipes pinning the same
    # version share one file and never reach this.
    spoken_for = await _urls_other_recipes_own(hass, owner_slug)
    if spoken_for is None:
        _LOGGER.debug("Skipping prune: install records unreadable")
        return

    # A recipe can also rename its bundle between revisions, and the old
    # name shares no prefix with the new one. The record still lists what
    # this recipe installed last time, so those URLs get the same
    # treatment as a superseded version.
    previous = await _urls_this_recipe_owns(hass, owner_slug) if owner_slug else set()

    for item in items:
        url = str(item.get("url", "")).split("?")[0]
        ours = url.startswith(prefix) or url == unversioned or url in previous
        if url == keep_url or not ours or url in spoken_for:
            continue
        try:
            await resources.async_delete_item(item["id"])
            stale = Path(hass.config.path(RESOURCE_DIR)) / Path(url).name
            await hass.async_add_executor_job(lambda p=stale: p.unlink(missing_ok=True))
            _LOGGER.info("Removed superseded card resource %s", url)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Could not prune %s", url, exc_info=True)


async def _async_file_matches(hass: HomeAssistant, path: Path, sha256: str) -> bool:
    """Whether the file on disk still hashes to what the recipe declared."""

    def _digest() -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    try:
        return await hass.async_add_executor_job(_digest) == sha256.strip().lower()
    except OSError:
        _LOGGER.debug("Could not read %s", path, exc_info=True)
        return False


async def _async_download(hass: HomeAssistant, spec: CardResourceSpec) -> bytes | ResourceResult:
    """Fetch and verify the bundle. Returns the bytes, or the result to
    hand back when it couldn't be trusted."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession  # noqa: PLC0415

    session = async_get_clientsession(hass)
    try:
        # Redirects are followed by hand so every hop is checked against
        # the allowlist. Letting aiohttp follow them means only the first
        # URL is ever vetted, and a release link that redirects off GitHub
        # would hand arbitrary JavaScript to the frontend — with nothing
        # to catch it when the recipe pins no digest.
        url = spec.url
        payload = b""
        for _hop in range(MAX_REDIRECTS + 1):
            if not _url_supported(url):
                return ResourceResult(
                    ok=False,
                    reason="unsupported_url",
                    message=(
                        f"{spec.name} can't be downloaded: it redirects to "
                        f"{urlparse(url).hostname}, which recipes may not fetch from."
                    ),
                )
            # ``async with`` on every hop: a redirect response left unread
            # holds its connection, and an install that redirects once per
            # attempt would work through the connector pool until later
            # downloads simply hang.
            async with session.get(
                url, timeout=DOWNLOAD_TIMEOUT, allow_redirects=False
            ) as response:
                if response.status in _REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location:
                        return ResourceResult(
                            ok=False,
                            reason="download_failed",
                            message=(
                                f"{spec.name} could not be downloaded: redirect with no target."
                            ),
                        )
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    return ResourceResult(
                        ok=False,
                        reason="download_failed",
                        message=(f"{spec.name} could not be downloaded (HTTP {response.status})."),
                    )
                # Chunked rather than one read(): a stream hands over what it
                # has buffered, which on a slow or chunked response is not the
                # whole body. Taking that as the file would hash a fragment — a
                # spurious checksum failure at best, truncated JavaScript in the
                # frontend at worst. Accumulating with a cap keeps the "don't
                # buffer something enormous" property.
                buffer = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    buffer.extend(chunk)
                    if len(buffer) > MAX_BYTES:
                        return ResourceResult(
                            ok=False,
                            reason="too_large",
                            message=(
                                f"{spec.name} is larger than the "
                                f"{MAX_BYTES // (1024 * 1024)} MB limit."
                            ),
                        )
                payload = bytes(buffer)
            break
        else:
            return ResourceResult(
                ok=False,
                reason="download_failed",
                message=f"{spec.name} could not be downloaded: too many redirects.",
            )
    except Exception as exc:  # noqa: BLE001 — aiohttp raises a wide family
        _LOGGER.warning("Could not download %s: %s", spec.url, exc)
        return ResourceResult(
            ok=False,
            reason="download_failed",
            message=f"{spec.name} could not be downloaded: {exc}",
        )

    if spec.sha256:
        digest = hashlib.sha256(payload).hexdigest()
        if digest != spec.sha256.lower():
            _LOGGER.warning(
                "Checksum mismatch for %s: expected %s, got %s",
                spec.url,
                spec.sha256.lower(),
                digest,
            )
            return ResourceResult(
                ok=False,
                reason="checksum_mismatch",
                message=(
                    f"{spec.name} was not installed: the downloaded file does not "
                    f"match the checksum the recipe declares."
                ),
            )
    return payload


async def async_remove_resource(hass: HomeAssistant, target_url: str) -> bool:
    """Deregister and delete a resource we installed. Best-effort.

    True means the registration is gone — removed here, or already absent
    when we looked. False means it is still there, or the URL was never
    ours to touch.

    Takes the URL rather than the spec because uninstall runs after the
    recipe bundle is gone: the install record remembers what was placed,
    which is the only account of it left by then.

    Our own URL base is what marks a resource as ours — HA's resource
    collection has nowhere to hang metadata, so the path is the claim. A
    card the homeowner installed themselves lives elsewhere and survives
    uninstalling the recipe.
    """
    target_url = (target_url or "").split("?")[0]
    if not target_url.startswith(f"{RESOURCE_URL_BASE}/"):
        return False

    # Whether the registration is gone, either because we removed it or
    # because it was already absent. Only then may the file go: a
    # registration pointing at a deleted file is a permanent 404 in the
    # frontend, which is worse than a stray file nobody loads.
    #
    # "Already absent" counts as success, and the distinction matters: a
    # prune earlier in the same install may have taken the registration
    # out, and reporting failure there would have the caller keep an
    # ownership claim on something that no longer exists — which then
    # blocks whoever else shares the URL from ever cleaning it up.
    deregistered = False
    resources = _lovelace_resources(hass)
    if resources is not None:
        try:
            matches = [
                item
                for item in await _registered_items(resources)
                if str(item.get("url", "")).split("?")[0] == target_url
            ]
            for item in matches:
                await resources.async_delete_item(item["id"])
            deregistered = True
        except Exception:  # noqa: BLE001 — uninstall must not fail on this
            _LOGGER.debug("Could not deregister %s", target_url, exc_info=True)

    if not deregistered:
        _LOGGER.info("Keeping %s: its registration could not be removed", target_url)
        return False

    # Basename only: the URL is ours by the check above, and a resource
    # collection edited by hand shouldn't be able to steer a delete.
    path = Path(hass.config.path(RESOURCE_DIR)) / Path(target_url).name

    def _unlink() -> None:
        path.unlink(missing_ok=True)

    try:
        await hass.async_add_executor_job(_unlink)
    except OSError:
        _LOGGER.debug("Could not delete %s", path, exc_info=True)
    return True
