"""LLM provider abstraction — factory and registry.

Adding a new provider:
  1. Create a new module under providers/ with a class extending LLMProvider
     (or OpenAICompatibleProvider for OpenAI-format APIs).
  2. Register it in PROVIDER_REGISTRY below.
  3. Add config constants in const.py and a config flow step.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from ..const import DEFAULT_SELORA_LOCAL_HOST
from .anthropic import AnthropicProvider
from .base import LLMProvider
from .gemini import GeminiProvider
from .ollama import OllamaProvider
from .openai import OpenAIProvider
from .openrouter import OpenRouterProvider
from .selora_cloud import SeloraCloudProvider
from .selora_local import SeloraLocalProvider

__all__ = [
    "AnthropicProvider",
    "GeminiProvider",
    "LLMProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "OpenRouterProvider",
    "SeloraCloudProvider",
    "SeloraLocalProvider",
    "create_provider",
    "discover_selora_local_host",
    "is_selora_local_available",
]

_LOGGER = logging.getLogger(__name__)

_SELORA_LOCAL_PROBE_TIMEOUT = 2.0

# Hosts the local add-on can reach us on, in order of likelihood. We try
# all of them on probe because the HA Core container only sees a
# Supervisor-managed add-on via its bridge gateway, not `localhost`; the
# default host string is correct for some deployments and wrong for
# others, so we don't rely on it.
_SELORA_LOCAL_PROBE_HOSTS: tuple[str, ...] = (
    "http://localhost:8080",
    # Supervisor bridge gateways — HA Core inside HA OS reaches add-ons
    # via one of these IPs, not via `localhost`. Using literal IPs avoids
    # the DNS lookups (which leak cancellation traces in tests).
    "http://172.30.32.1:8080",
    "http://172.30.33.1:8080",
)

PROVIDER_REGISTRY: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
    "openrouter": OpenRouterProvider,
    "ollama": OllamaProvider,
    "selora_cloud": SeloraCloudProvider,
    "selora_local": SeloraLocalProvider,
}


def create_provider(
    provider_name: str,
    hass: HomeAssistant,
    **kwargs: Any,
) -> LLMProvider:
    """Create a provider instance by name.

    Raises ValueError for unknown provider names.
    """
    cls = PROVIDER_REGISTRY.get(provider_name)
    if cls is None:
        raise ValueError(f"Unknown LLM provider: {provider_name!r}")
    return cls(hass, **kwargs)


async def _probe_one(hass: HomeAssistant, host: str) -> str | None:
    """Return ``host`` if the local backend answers on it, else ``None``.

    Uses a raw TCP connect rather than HTTP — different Selora Local
    builds expose different health paths (``/health``, ``/v1/models``,
    or just the OpenAI surface), so a successful TCP handshake is a
    more reliable "something is listening" signal than any single HTTP
    request.
    """
    parsed_host, parsed_port = _split_host_port(host)
    if parsed_host is None or parsed_port is None:
        return None
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(parsed_host, parsed_port),
            timeout=_SELORA_LOCAL_PROBE_TIMEOUT,
        )
    except (
        TimeoutError,
        OSError,
    ):
        return None
    except Exception:  # noqa: BLE001 - probe must not break callers
        _LOGGER.debug("Selora AI Local probe failed for %s", host, exc_info=True)
        return None
    writer.close()
    with contextlib.suppress(OSError, ConnectionError):
        await writer.wait_closed()
    return host


def _split_host_port(url: str) -> tuple[str | None, int | None]:
    """Best-effort ``http(s)://host:port`` parser. Returns ``(None, None)``
    when the URL is malformed or omits the port."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return None, None
    if not parts.hostname or parts.port is None:
        return None, None
    return parts.hostname, parts.port


def _supervisor_selora_hosts(hass: HomeAssistant) -> list[str]:
    """Look up installed Selora add-ons via Supervisor and return their
    hostname-based URLs. Empty list when not on HA OS / Supervised or
    when no matching add-on is installed.
    """
    try:
        from homeassistant.components.hassio import (
            get_addons_info,
            hostname_from_addon_slug,
        )
    except ImportError:
        return []

    try:
        addons = get_addons_info(hass) or {}
    except Exception:  # noqa: BLE001 - Supervisor lookup must not break callers
        _LOGGER.debug("Supervisor add-on lookup failed", exc_info=True)
        return []

    hosts: list[str] = []
    for slug in addons:
        if "selora" not in slug.lower():
            continue
        hostname = hostname_from_addon_slug(slug)
        hosts.append(f"http://{hostname}:8080")
    return hosts


async def discover_selora_local_host(
    hass: HomeAssistant, configured_host: str | None = None
) -> str | None:
    """Return the first reachable Selora AI Local host, or ``None``.

    Tries known host candidates in parallel — HA Core inside HA OS only
    sees a Supervisor-managed add-on via its bridge gateway, not
    ``localhost``, so the default host alone is not a reliable signal.
    Also asks Supervisor for any installed Selora add-on and probes its
    Docker-DNS hostname (e.g. ``http://<repo>_libselora:8080``).
    The returned host should be used as the form default so a user who
    accepts the prefilled value does not immediately fail validation.
    """
    candidates: tuple[str, ...] = (
        *((configured_host,) if configured_host else ()),
        DEFAULT_SELORA_LOCAL_HOST,
        *_supervisor_selora_hosts(hass),
        *_SELORA_LOCAL_PROBE_HOSTS,
    )
    hosts = list(dict.fromkeys(candidates))
    results = await asyncio.gather(*(_probe_one(hass, h) for h in hosts))
    for host, result in zip(hosts, results, strict=True):
        if result is not None:
            return host
    return None


async def is_selora_local_available(
    hass: HomeAssistant, configured_host: str | None = None
) -> bool:
    """Back-compat wrapper around :func:`discover_selora_local_host`."""
    return await discover_selora_local_host(hass, configured_host) is not None
