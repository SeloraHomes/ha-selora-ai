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
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_MAC
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers import entity_registry as er

from ..const import DOMAIN, HOME_HEALTH_UNIQUE_ID

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .manifest import Manifest

_LOGGER = logging.getLogger(__name__)


class ResolverError(Exception):
    """Auto-resolver couldn't produce a value. Message goes verbatim
    to the homeowner via the punch list, so phrase it for them
    (no Python tracebacks, no internal jargon)."""


@dataclass(frozen=True, slots=True)
class ResolverContext:
    """What a resolver may read besides ``hass``.

    Some values can't be derived from HA state alone. The Samsung TV
    MAC is the MAC of the home's *chosen* television, not of "the only
    Samsung on the network" — in a two-TV home there is no answer
    without the choice. Every resolver therefore takes this alongside
    ``hass``, whether it reads it or not: one uniform signature beats
    a registry holding two call shapes.

    ``bindings`` maps role id to the entity ids resolved for it, the
    same shape ``ResolutionReport.bindings`` carries. It is EMPTY when
    the caller has nothing yet — the wizard offers auto-setup from the
    Match step, where the homeowner may not have picked a device. A
    resolver must read that as "not chosen yet" and say so, never as
    "nothing matches".
    """

    bindings: Mapping[str, Sequence[str]] = field(default_factory=dict)

    @property
    def entity_ids(self) -> tuple[str, ...]:
        """Every bound entity id across all roles, de-duplicated, in
        role order. Resolvers that care about one integration filter
        this rather than naming a role id: role ids belong to the
        recipe author and a resolver is shared across recipes.
        """
        seen: dict[str, None] = {}
        for entities in self.bindings.values():
            for entity_id in entities or ():
                seen.setdefault(entity_id, None)
        return tuple(seen)


# ── NWS station resolver ───────────────────────────────────────────


# The NWS API rejects requests without a UA header. Email + URL is
# what their developer docs ask for.
_NWS_USER_AGENT = "Selora AI (https://selorahomes.com, support@selorahomes.com)"
_NWS_TIMEOUT_SECONDS = 8


async def _resolve_nws_station(hass: HomeAssistant, ctx: ResolverContext) -> str:
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


async def _resolve_hass_latitude(hass: HomeAssistant, ctx: ResolverContext) -> float:
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


async def _resolve_hass_longitude(hass: HomeAssistant, ctx: ResolverContext) -> float:
    if hass.config.longitude is None:
        raise ResolverError(
            "Home location isn't set in Home Assistant. Open Settings "
            "→ System → General and set the location, then retry."
        )
    return float(hass.config.longitude)


# ── TTS engine resolver ────────────────────────────────────────────


# Order of preference when several TTS engines are configured. Local /
# open engines first — Home Assistant Cloud (Nabu Casa) is a competitor,
# so it's never *preferred*, only used as a last-resort fallback (and
# only when actually usable). Mirrors automation_utils._resolve_tts_engine
# so a recipe announcement and an LLM-generated announcement land on the
# same engine for a given home.
_TTS_ENGINE_PREFERENCE = ("piper", "google")


async def _resolve_tts_engine(hass: HomeAssistant, ctx: ResolverContext) -> str:
    """Pick a Text-to-Speech engine entity for ``tts.speak`` announcements.

    Recipes used to hard-code ``tts.cloud_say``, which only exists with a
    paid Home Assistant Cloud (Nabu Casa) subscription — on every other home
    the announcement passed its trigger but called nothing. Resolving the
    engine from the home's *usable* ``tts.*`` entities (prefer Piper, then
    Google, else the first usable) lets the template emit a portable
    ``tts.speak`` call that works regardless of subscription.

    Only usable engines are considered: HA Cloud reports ``available`` even
    without an active subscription but fails at call time, so it's filtered
    out unless the home actually has a subscription — and even then it's
    never preferred over a local engine (it's a competitor). Homeowners can
    still change the engine after install; this is just the default pick.

    Returns the engine entity_id, or ``""`` when the home has no usable TTS
    engine. The empty string is intentional, not a :class:`ResolverError`:
    a home without TTS should still get the rest of the recipe (siren, push
    notification), so the template guards the announcement on a non-empty
    value and simply omits it rather than halting the whole install.
    """
    # Lazy import: automation_utils is heavy and pulls HA helpers; keep it
    # off the module-load path for the recipes package.
    from ..automation_utils import (  # noqa: PLC0415
        _is_ha_cloud_engine,
        _tts_engine_usable,
    )

    usable = [
        eid for eid in sorted(hass.states.async_entity_ids("tts")) if _tts_engine_usable(hass, eid)
    ]
    if not usable:
        return ""
    for preferred in _TTS_ENGINE_PREFERENCE:
        for eid in usable:
            if preferred in eid:
                return eid
    # Cloud is a competitor → never chosen while any other usable engine
    # exists (e.g. tts.microsoft); sorted order could otherwise put it
    # first. Fall back to cloud only when it's the sole usable engine.
    for eid in usable:
        if not _is_ha_cloud_engine(eid):
            return eid
    return usable[0]


# ── Selora AI entity resolvers ─────────────────────────────────────


async def _resolve_selora_home_health(hass: HomeAssistant, ctx: ResolverContext) -> str:
    """The entity id of Selora AI's own Home Health sensor.

    Its entity id differs between homes (``sensor.selora_ai_hub_home_health``
    where the hub device registered under its old name, ``sensor.selora_ai_home_health``
    since, or whatever the homeowner renamed it to), so a recipe that hard-codes
    one installs triggers that never set up. The unique id is the one fixed thing.
    """
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, HOME_HEALTH_UNIQUE_ID)
    entry = registry.async_get(entity_id) if entity_id else None
    if entry is None:
        raise ResolverError(
            "Selora AI's Home Health sensor isn't set up in this home yet. "
            "Reload the Selora AI integration, then retry."
        )
    # A disabled entity has no state, so its triggers could never fire.
    if entry.disabled_by is not None:
        raise ResolverError(
            "Selora AI's Home Health sensor is disabled. Enable it under "
            "Settings → Devices & services → Entities, then retry."
        )
    return entry.entity_id


# ── Samsung TV MAC resolver ────────────────────────────────────────


def _names_dont_report(names: Sequence[str]) -> str:
    """``"The Frame doesn't report"`` / ``"The Frame, Den TV don't report"``
    — resolver messages reach the homeowner verbatim."""
    return f"{', '.join(names)} {'doesn' if len(names) == 1 else 'don'}'t report"


_SAMSUNG_TV_ADD_BY_HAND = (
    "You can add it by hand from Settings → Devices & Services → Add Integration → Wake on LAN."
)


def _samsung_tv_entries(hass: HomeAssistant) -> dict[str, Any]:
    """The home's samsungtv config entries, by entry id — only the ones
    it is actually using.

    A disabled entry keeps its MAC but exposes no entities, so it can
    never be a television the homeowner picked; counting it resolves to
    a set that was replaced. An ignored entry stores no data at all,
    excluded for the same reason rather than left to the MAC check.
    """
    return {
        entry.entry_id: entry
        for entry in hass.config_entries.async_entries(
            "samsungtv", include_ignore=False, include_disabled=False
        )
    }


def _samsung_tv_picks(hass: HomeAssistant, ctx: ResolverContext) -> dict[str, Any]:
    """Map each bound entity that is a Samsung TV to its config entry,
    in the order the roles bound them.

    Entities from the recipe's other roles (speakers, lights) pass
    through here too and drop out, because their config entry isn't a
    samsungtv one.
    """
    entries = _samsung_tv_entries(hass)
    picks: dict[str, Any] = {}
    if bound := ctx.entity_ids:
        registry = er.async_get(hass)
        for entity_id in bound:
            registry_entry = registry.async_get(entity_id)
            if registry_entry and (entry := entries.get(registry_entry.config_entry_id)):
                picks[entity_id] = entry
    return picks


async def _resolve_samsung_tv_mac(hass: HomeAssistant, ctx: ResolverContext) -> str:
    """One MAC address, for seeding HA's ``wake_on_lan`` config flow.

    ``wake_on_lan`` registers ``send_magic_packet`` in the component's
    ``async_setup``, not per config entry, so a single entry is what
    makes the service exist for EVERY television. This resolver exists
    to give that flow its one required field without showing the
    homeowner a form asking for an address Home Assistant already
    knows; the automations then carry their own MACs (see
    ``samsung_tv_macs``). With several TVs picked, the first one is as
    good as any: the entry is a vehicle for the service, not the
    recipe's wiring. Returned exactly as stored — HA's own flow runs it
    through ``dr.format_mac()``.
    """
    picks = _samsung_tv_picks(hass, ctx)
    for entry in picks.values():
        if mac := entry.data.get(CONF_MAC):
            return str(mac)

    if picks:
        # Every television they picked is a model that publishes no MAC.
        names = sorted({entry.title for entry in picks.values()})
        raise ResolverError(
            f"{_names_dont_report(names)} a MAC address, so Wake on LAN can't be "
            "set up for you — older Samsung models don't publish one. "
            f"{_SAMSUNG_TV_ADD_BY_HAND}"
        )

    # Nothing bound yet — the homeowner opened the Wake on LAN row
    # before picking a television.
    with_mac = [e for e in _samsung_tv_entries(hass).values() if e.data.get(CONF_MAC)]
    if len(with_mac) > 1:
        raise ResolverError(
            f"This home has {len(with_mac)} Samsung TVs. Pick the ones this "
            "recipe should wake first, then set up Wake on LAN."
        )
    if not with_mac:
        raise ResolverError(
            "Your Samsung TV doesn't report a MAC address, so Wake on "
            "LAN can't be set up for you — older Samsung models don't "
            f"publish one. {_SAMSUNG_TV_ADD_BY_HAND}"
        )
    return str(with_mac[0].data[CONF_MAC])


async def _resolve_samsung_tv_macs(hass: HomeAssistant, ctx: ResolverContext) -> dict[str, str]:
    """Every picked Samsung TV's MAC, keyed by its entity id.

    This is what lets one install cover a whole house: the recipe's
    template walks its TV role and reads the MAC belonging to each
    entity, so three televisions become three automations against three
    addresses rather than three installs of the same recipe (the
    package is written per slug, so the second install would overwrite
    the first).

    Raises rather than skipping when a picked television publishes no
    MAC: silently dropping it would leave a set the homeowner chose
    with no wake automation and no explanation.
    """
    picks = _samsung_tv_picks(hass, ctx)
    macs: dict[str, str] = {}
    missing: list[str] = []
    for entity_id, entry in picks.items():
        if mac := entry.data.get(CONF_MAC):
            macs[entity_id] = str(mac)
        else:
            missing.append(entry.title or entity_id)
    if missing:
        it = "it" if len(missing) == 1 else "them"
        raise ResolverError(
            f"{_names_dont_report(missing)} a MAC address, so Wake on LAN "
            f"can't wake {it} — older Samsung models don't publish one. Leave "
            f"{it} out of the selection, or add Wake on LAN by hand."
        )
    return macs


# ── Registry ───────────────────────────────────────────────────────


Resolver = Callable[["HomeAssistant", ResolverContext], Awaitable[Any]]

RESOLVERS: dict[str, Resolver] = {
    "nws_station_from_location": _resolve_nws_station,
    "hass_config_latitude": _resolve_hass_latitude,
    "hass_config_longitude": _resolve_hass_longitude,
    "tts_engine": _resolve_tts_engine,
    "selora_home_health_sensor": _resolve_selora_home_health,
    "samsung_tv_mac": _resolve_samsung_tv_mac,
    "samsung_tv_macs": _resolve_samsung_tv_macs,
}


async def async_apply_auto_inputs(
    hass: HomeAssistant,
    manifest: Manifest,
    inputs: dict[str, Any],
    *,
    bindings: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Mutate ``inputs`` in place with every resolver-driven value.

    Resolver outputs OVERWRITE user-supplied values for the same input
    id — the user can't override an auto-resolved input from the
    wizard (the field is hidden), so any value in ``inputs`` for an
    auto-resolved id is stale / shouldn't be trusted.

    ``bindings`` is the role resolution this install is running with.
    The pipeline always has it by this stage (roles resolve before
    inputs), and resolvers that answer "which device did they pick?"
    need it. Omitted, resolvers see an empty context and fall back to
    whatever they can read from HA alone.

    Returns the same ``inputs`` dict for chaining. Raises
    :class:`ResolverError` from the first failing resolver — the
    pipeline turns that into a "validate" stage punch list entry.
    """
    ctx = ResolverContext(bindings=dict(bindings or {}))
    for spec in manifest.inputs:
        if not spec.resolver:
            continue
        resolver = RESOLVERS.get(spec.resolver)
        if resolver is None:
            raise ResolverError(
                f"Recipe references unknown resolver "
                f"{spec.resolver!r}. This is a recipe authoring bug."
            )
        value = await resolver(hass, ctx)
        inputs[spec.id] = value
    return inputs
