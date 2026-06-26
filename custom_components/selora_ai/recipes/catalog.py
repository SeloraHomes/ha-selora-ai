"""Fetch + cache the public recipes catalog.

Recipes live at https://selorahomes.com/recipes (Hugo content) and
the site emits ``/api/recipes.json`` listing every available bundle.
The HA integration consumes that catalog at runtime so new recipes
ship without re-releasing the integration. Dev overrides via the
``SELORA_AI_RECIPE_CATALOG_URL`` env var.

We cache the response in memory for ``RECIPE_CATALOG_TTL_SECONDS`` to
avoid hammering the CDN — the wizard refreshes on every panel mount,
which is fine but we don't need to round-trip every keystroke.
"""

from __future__ import annotations

import asyncio
import logging
import os
from time import monotonic
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import aiohttp_client

from .const import (
    RECIPE_CATALOG_TTL_SECONDS,
    RECIPE_CATALOG_URL_DEFAULT,
    RECIPE_FETCH_TIMEOUT_SECONDS,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class CatalogError(Exception):
    """Fetch failed for a homeowner-readable reason."""


# Single-process cache: timestamp + payload. Lives in module scope so
# every HA installation shares one cache per process; the TTL bounds
# staleness. We don't bother with a per-instance store because the
# catalog is the same for every homeowner.
_cache_payload: dict[str, Any] | None = None
_cache_at: float = 0.0
_cache_url: str = ""  # URL the cached payload was fetched from
_cache_lock = asyncio.Lock()


def _catalog_url(override: str | None = None) -> str:
    """Resolve the catalog URL. Precedence (highest to lowest):
    explicit ``override`` (from WS arg — typically dev "use localhost"),
    env var ``SELORA_AI_RECIPE_CATALOG_URL``, then the production
    default.
    """
    if override:
        return override
    return os.environ.get("SELORA_AI_RECIPE_CATALOG_URL", RECIPE_CATALOG_URL_DEFAULT)


def _is_fresh(url: str) -> bool:
    if _cache_payload is None or _cache_url != url:
        return False
    return (monotonic() - _cache_at) < RECIPE_CATALOG_TTL_SECONDS


async def async_get_catalog(
    hass: HomeAssistant,
    *,
    force_refresh: bool = False,
    url_override: str | None = None,
) -> dict[str, Any]:
    """Return the recipes catalog dict. Cached for
    ``RECIPE_CATALOG_TTL_SECONDS``; ``force_refresh`` bypasses the
    cache. ``url_override`` lets the caller point at a different
    catalog endpoint per-request (dev mode → localhost Hugo server).

    Raises :class:`CatalogError` with a homeowner-readable message
    when the fetch fails — network down, CDN 5xx, malformed JSON.
    """
    global _cache_payload, _cache_at, _cache_url
    url = _catalog_url(url_override)
    if not force_refresh and _is_fresh(url):
        return _cache_payload  # type: ignore[return-value]

    async with _cache_lock:
        # Re-check inside the lock so concurrent callers share one fetch.
        if not force_refresh and _is_fresh(url):
            return _cache_payload  # type: ignore[return-value]
        session = aiohttp_client.async_get_clientsession(hass)
        try:
            async with asyncio.timeout(RECIPE_FETCH_TIMEOUT_SECONDS):
                async with session.get(url) as resp:
                    if resp.status != 200:
                        raise CatalogError(f"Catalog at {url} returned HTTP {resp.status}.")
                    payload = await resp.json(content_type=None)
        except CatalogError:
            raise
        except TimeoutError as exc:
            raise CatalogError(f"Timed out fetching recipes catalog from {url}.") from exc
        except Exception as exc:  # noqa: BLE001 — surface verbatim
            raise CatalogError(f"Could not reach the recipes catalog: {exc}.") from exc

        if not isinstance(payload, dict) or "recipes" not in payload:
            raise CatalogError(f"Catalog at {url} has unexpected shape (no 'recipes' key).")

        _cache_payload = payload
        _cache_at = monotonic()
        _cache_url = url
        _LOGGER.info(
            "Fetched %d recipe(s) from %s",
            len(payload.get("recipes", [])),
            url,
        )
        return payload


def invalidate_cache() -> None:
    """Reset the in-memory cache. Called from tests + after an
    install completes so the next list view reflects the latest
    install state.
    """
    global _cache_payload, _cache_at, _cache_url
    _cache_payload = None
    _cache_at = 0.0
    _cache_url = ""
