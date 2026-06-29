"""Async auto-resolvers for recipe inputs.

Inputs whose value can be computed deterministically from the home's
configuration (lat/lon, time zone, etc.) shouldn't appear in the
wizard's Settings form. The recipe author declares
``resolver: <name>`` on the input; this module owns the registry of
named resolvers and runs them before validation.

Resolvers are pure: HA in, single value out. They never mutate state.
They can raise :class:`ResolverError` to halt the pipeline with a
homeowner-readable message at the "validate" stage. Networked
resolvers should set tight timeouts — a wedged install pipeline is
worse than a wrong default.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import aiohttp_client

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .manifest import Manifest

_LOGGER = logging.getLogger(__name__)


class ResolverError(Exception):
    """Auto-resolver couldn't produce a value. Message goes verbatim
    to the homeowner via the punch list, so phrase it for them
    (no Python tracebacks, no internal jargon)."""


# ── NWS station resolver ───────────────────────────────────────────


# The NWS API rejects requests without a UA header. Email + URL is
# what their developer docs ask for.
_NWS_USER_AGENT = "Selora AI (https://selorahomes.com, support@selorahomes.com)"
_NWS_TIMEOUT_SECONDS = 8


async def _resolve_nws_station(hass: HomeAssistant) -> str:
    """Resolve the home's nearest NWS METAR station identifier from
    the lat/lon configured in Home Assistant. Calls api.weather.gov
    twice — first ``/points/{lat},{lon}`` then the returned
    observationStations URL — and returns the first station's
    ``stationIdentifier`` (e.g. ``KOKC``).

    Raises :class:`ResolverError` when the home is outside US NWS
    coverage (404 from /points/) or the network call fails.
    """
    lat = hass.config.latitude
    lon = hass.config.longitude
    if lat is None or lon is None:
        raise ResolverError(
            "Home location isn't set in Home Assistant. Open Settings "
            "→ System → General and set the location, then retry."
        )

    session = aiohttp_client.async_get_clientsession(hass)
    headers = {"User-Agent": _NWS_USER_AGENT, "Accept": "application/geo+json"}
    points_url = f"https://api.weather.gov/points/{lat},{lon}"

    try:
        async with asyncio.timeout(_NWS_TIMEOUT_SECONDS):
            async with session.get(points_url, headers=headers) as resp:
                if resp.status == 404:
                    raise ResolverError(
                        "The National Weather Service doesn't cover this "
                        "location. The Tornado Alert recipe is US-only."
                    )
                resp.raise_for_status()
                points = await resp.json()
            stations_url = (points.get("properties") or {}).get("observationStations")
            if not stations_url:
                raise ResolverError(
                    "NWS didn't return an observation stations URL for "
                    "this location. Try again later, or set a nearby "
                    "station code manually."
                )
            async with session.get(stations_url, headers=headers) as resp:
                resp.raise_for_status()
                stations = await resp.json()
    except ResolverError:
        raise
    except TimeoutError as exc:
        raise ResolverError(
            "Timed out reaching api.weather.gov. Check your internet connection and retry."
        ) from exc
    except Exception as exc:  # noqa: BLE001 — surface any network failure verbatim
        raise ResolverError(f"Could not reach the National Weather Service: {exc}.") from exc

    features = stations.get("features") or []
    if not features:
        raise ResolverError("NWS returned no observation stations for this location.")
    station_id = (features[0].get("properties") or {}).get("stationIdentifier")
    if not station_id:
        raise ResolverError("NWS returned a station entry without an identifier.")
    return str(station_id)


# ── HA location resolvers ──────────────────────────────────────────


async def _resolve_hass_latitude(hass: HomeAssistant) -> float:
    """Home Assistant's configured latitude. Used by recipe auto-setup
    to pre-fill the lat/lon fields on integration config flows so the
    homeowner doesn't retype what HA already knows.
    """
    if hass.config.latitude is None:
        raise ResolverError(
            "Home location isn't set in Home Assistant. Open Settings "
            "→ System → General and set the location, then retry."
        )
    return float(hass.config.latitude)


async def _resolve_hass_longitude(hass: HomeAssistant) -> float:
    if hass.config.longitude is None:
        raise ResolverError(
            "Home location isn't set in Home Assistant. Open Settings "
            "→ System → General and set the location, then retry."
        )
    return float(hass.config.longitude)


# ── TTS engine resolver ────────────────────────────────────────────


# Order of preference when several TTS engines are configured. Mirrors
# automation_utils._resolve_tts_engine so a recipe announcement and an
# LLM-generated announcement land on the same engine for a given home.
_TTS_ENGINE_PREFERENCE = ("cloud", "piper", "google")


async def _resolve_tts_engine(hass: HomeAssistant) -> str:
    """Pick a Text-to-Speech engine entity for ``tts.speak`` announcements.

    Recipes used to hard-code ``tts.cloud_say``, which only exists with a
    paid Home Assistant Cloud (Nabu Casa) subscription — on every other home
    the announcement passed its trigger but called nothing. Resolving the
    engine from the home's actual ``tts.*`` entities (prefer HA Cloud, then
    Piper, then Google, else the first available) lets the template emit a
    portable ``tts.speak`` call that works regardless of subscription.

    Returns the engine entity_id, or ``""`` when the home has no TTS engine
    configured. The empty string is intentional, not a :class:`ResolverError`:
    a home without TTS should still get the rest of the recipe (siren, push
    notification), so the template guards the announcement on a non-empty
    value and simply omits it rather than halting the whole install.
    """
    tts_entities = sorted(hass.states.async_entity_ids("tts"))
    if not tts_entities:
        return ""
    for preferred in _TTS_ENGINE_PREFERENCE:
        for eid in tts_entities:
            if preferred in eid:
                return eid
    return tts_entities[0]


# ── Registry ───────────────────────────────────────────────────────


Resolver = Callable[["HomeAssistant"], Awaitable[Any]]

RESOLVERS: dict[str, Resolver] = {
    "nws_station_from_location": _resolve_nws_station,
    "hass_config_latitude": _resolve_hass_latitude,
    "hass_config_longitude": _resolve_hass_longitude,
    "tts_engine": _resolve_tts_engine,
}


async def async_apply_auto_inputs(
    hass: HomeAssistant,
    manifest: Manifest,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """Mutate ``inputs`` in place with every resolver-driven value.

    Resolver outputs OVERWRITE user-supplied values for the same input
    id — the user can't override an auto-resolved input from the
    wizard (the field is hidden), so any value in ``inputs`` for an
    auto-resolved id is stale / shouldn't be trusted.

    Returns the same ``inputs`` dict for chaining. Raises
    :class:`ResolverError` from the first failing resolver — the
    pipeline turns that into a "validate" stage punch list entry.
    """
    for spec in manifest.inputs:
        if not spec.resolver:
            continue
        resolver = RESOLVERS.get(spec.resolver)
        if resolver is None:
            raise ResolverError(
                f"Recipe references unknown resolver "
                f"{spec.resolver!r}. This is a recipe authoring bug."
            )
        value = await resolver(hass)
        inputs[spec.id] = value
    return inputs
