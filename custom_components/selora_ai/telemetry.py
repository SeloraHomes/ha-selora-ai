"""Anonymous, opt-in product telemetry.

Three event types, all opt-in and counter/enum-only:

1. ``home_snapshot`` — a periodic inventory of the install: how many
   devices, integrations, automations, scenes, scripts, blueprints,
   areas, and entities exist, plus a device-count-per-integration
   breakdown, the LLM provider in use, and Selora/HA versions. Sent
   once shortly after startup, then daily.
2. ``llm_output_repaired`` — counts how often each "safety-net" repair
   on raw model output fires (see ``REPAIR_TYPES``), broken down by
   provider/model, so we can measure repair effectiveness across model
   versions and retire corrections that are no longer needed.
3. ``usage_activity`` — a period rollup of how the install is *used*:
   automations created/refined/deleted/toggled, scenes created,
   patterns detected, suggestions generated/accepted/dismissed/snoozed,
   chat messages and sessions, Assist queries, commands executed,
   devices discovered/paired, and LLM call + token totals. Counters are
   accumulated in memory via ``record_activity`` and flushed (then
   reset) once per snapshot interval. Restart loses the partial window —
   acceptable for anonymous trend data; counters are never persisted to
   avoid write amplification on the hot chat path.

Privacy contract (epic selorahomes/products#56, sub-issue #106):
- OFF by default. Nothing leaves the network unless the user flips the
  ``telemetry_enabled`` toggle in settings.
- Payloads carry counters/enums/versions only. The per-event allowlists
  (``_REPAIR_PROPERTY_KEYS`` / ``_SNAPSHOT_PROPERTY_KEYS``) are enforced
  in ``_capture`` before every POST so a caller cannot accidentally
  widen the payload.
- NEVER sends entity ids, friendly names, prompt text, or response text.
- ``distinct_id`` is a random per-install UUID generated locally with no
  link to any household, network, or account identifier.
- Every POST overrides ``$ip`` and sets ``$geoip_disable`` so the host's
  public IP is never stored or geolocated by PostHog — the anon id can't
  be re-linked to a household network regardless of project settings.

Repairs are recorded by pure, ``hass``-free helpers via ``record_repair``
into a ContextVar buffer that ``UsageTracker.scope`` opens around each
LLM call. The buffer is drained at the call boundary — where the
provider and model are known — and POSTed fire-and-forget.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar, Token
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
import uuid

import aiohttp
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import (
    CONF_ENTRY_TYPE,
    CONF_TELEMETRY_ENABLED,
    CONF_TELEMETRY_ENDPOINT,
    DEFAULT_TELEMETRY_ENABLED,
    DEFAULT_TELEMETRY_ENDPOINT,
    DOMAIN,
    ENTRY_TYPE_LLM,
    KNOWN_INTEGRATIONS,
    LLM_PRICING_USD_PER_MTOK,
    LLM_PROVIDER_OLLAMA,
    LLM_PROVIDER_SELORA_LOCAL,
    TELEMETRY_EVENT_ACTIVITY,
    TELEMETRY_EVENT_REPAIR,
    TELEMETRY_EVENT_SNAPSHOT,
    TELEMETRY_PROJECT_KEY,
    TELEMETRY_SNAPSHOT_INTERVAL_HOURS,
    TELEMETRY_STORE_VERSION,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_STORAGE_KEY = f"{DOMAIN}.telemetry"
_POST_TIMEOUT = aiohttp.ClientTimeout(total=10)

# The complete set of repair identifiers we are willing to emit. A value
# outside this set is dropped by ``record_repair`` — instrumentation can
# never introduce a new (potentially identifying) label by accident.
REPAIR_TYPES: frozenset[str] = frozenset(
    {
        "service_name_inference",
        "state_info_strip",
        "trailing_marker_reposition",
        "friendly_name_strip",
        "qwen_normalize",
        "cloud_json_salvage",
    }
)

# The ONLY property keys allowed per event type. Enforced in ``_capture``
# before the POST — this is the hard privacy boundary. Every value is a
# counter, an enum, or a version string; nothing household-identifying.
_REPAIR_PROPERTY_KEYS: frozenset[str] = frozenset(
    {
        "repair_type",
        "provider",
        "model",
        "app_version",
    }
)

_SNAPSHOT_PROPERTY_KEYS: frozenset[str] = frozenset(
    {
        "devices",
        "integrations",
        "automations",
        "scenes",
        "scripts",
        "blueprints",
        "areas",
        "entities",
        "selora_automations",
        "suggestions_accepted",
        "suggestions_dismissed",
        "llm_provider",
        "devices_by_integration",
        "country",
        "ha_version",
        "app_version",
    }
)

# The activity counters we accumulate per period. A name outside this set
# is dropped by ``record_activity`` — instrumentation can never introduce
# a new (potentially identifying) label by accident. Every counter is a
# plain integer count of an in-product action; none carries any entity
# id, name, or content.
_ACTIVITY_COUNTER_KEYS: frozenset[str] = frozenset(
    {
        "automations_created",
        "automations_refined",
        "automations_deleted",
        "automations_enabled",
        "automations_disabled",
        "scenes_created",
        "patterns_detected",
        "suggestions_generated",
        "suggestions_accepted",
        "suggestions_dismissed",
        "suggestions_snoozed",
        "chat_messages",
        "chat_sessions",
        "chat_feedback_positive",
        "chat_feedback_negative",
        # Subject breakdown of the same thumbs (the aggregate above is the
        # sum across subjects): which kind of reply was rated.
        "chat_feedback_automation_positive",
        "chat_feedback_automation_negative",
        "chat_feedback_scene_positive",
        "chat_feedback_scene_negative",
        "chat_feedback_prose_positive",
        "chat_feedback_prose_negative",
        "assist_queries",
        "commands_executed",
        "devices_paired",
        "discoveries_run",
        "llm_calls",
        "llm_input_tokens",
        "llm_output_tokens",
        "llm_quota_exceeded",
    }
)

# The ONLY property keys allowed on a ``usage_activity`` event: the
# counters above plus the enum/meta fields. Enforced in ``_capture``.
_ACTIVITY_PROPERTY_KEYS: frozenset[str] = _ACTIVITY_COUNTER_KEYS | frozenset(
    {
        "llm_provider",
        "period_hours",
        "app_version",
    }
)

# Per-call buffer of repair_type strings recorded by pure helpers.
# ``None`` outside an LLM call, so ``record_repair`` is a safe no-op
# anywhere else.
_pending_repairs: ContextVar[list[str] | None] = ContextVar("selora_pending_repairs", default=None)


def record_repair(repair_type: str) -> None:
    """Record that an LLM-output repair fired during the current call.

    Safe to call from pure, ``hass``-free helpers. Appends ``repair_type``
    to the active per-call buffer (opened by ``UsageTracker.scope``); a
    no-op if no buffer is active or the type is unknown. The actual POST —
    gated on the opt-in toggle — happens later at the call boundary.
    """
    if repair_type not in REPAIR_TYPES:
        return
    buf = _pending_repairs.get()
    if buf is not None:
        buf.append(repair_type)


@contextmanager
def repair_capture() -> Any:
    """Open a per-call repair buffer; yields the list it accumulates."""
    buf: list[str] = []
    token: Token[list[str] | None] = _pending_repairs.set(buf)
    try:
        yield buf
    finally:
        _pending_repairs.reset(token)


class TelemetryClient:
    """POSTs anonymous inventory snapshots + repair counters when opted in."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store[dict[str, Any]] = Store(
            hass, version=TELEMETRY_STORE_VERSION, key=_STORAGE_KEY
        )
        self._install_id: str | None = None
        self._app_version: str | None = None
        self._lock = asyncio.Lock()
        # In-memory activity counters, accumulated since the last flush.
        # Never persisted; flushed and reset by ``async_send_activity``.
        self._activity: dict[str, int] = {}

    def _llm_entry(self) -> ConfigEntry | None:
        """Return the LLM config entry, whose options hold the toggle."""
        for entry in self._hass.config_entries.async_entries(DOMAIN):
            if entry.data.get(CONF_ENTRY_TYPE, ENTRY_TYPE_LLM) == ENTRY_TYPE_LLM:
                return entry
        return None

    def is_enabled(self) -> bool:
        """Return whether the user has opted in (read live each call)."""
        entry = self._llm_entry()
        if entry is None:
            return DEFAULT_TELEMETRY_ENABLED
        merged = {**entry.data, **entry.options}
        return bool(merged.get(CONF_TELEMETRY_ENABLED, DEFAULT_TELEMETRY_ENABLED))

    def _endpoint(self) -> str:
        entry = self._llm_entry()
        if entry is not None:
            override = {**entry.data, **entry.options}.get(CONF_TELEMETRY_ENDPOINT)
            if override:
                return str(override)
        return DEFAULT_TELEMETRY_ENDPOINT

    async def _async_install_id(self) -> str:
        """Return the stable per-install anonymous id, creating it once."""
        if self._install_id is not None:
            return self._install_id
        async with self._lock:
            if self._install_id is not None:
                return self._install_id
            raw = await self._store.async_load()
            if isinstance(raw, dict) and isinstance(raw.get("install_id"), str):
                self._install_id = raw["install_id"]
            else:
                self._install_id = uuid.uuid4().hex
                await self._store.async_save({"install_id": self._install_id})
            return self._install_id

    async def _async_app_version(self) -> str:
        if self._app_version is not None:
            return self._app_version
        self._app_version = await self._hass.async_add_executor_job(_read_manifest_version)
        return self._app_version

    def record_repairs(self, repair_types: list[str], *, provider: str, model: str) -> None:
        """Schedule fire-and-forget emission of the call's repairs.

        Deduplicates within the call: one event per distinct repair type,
        so a chatty call that triggers the same repair twice still counts
        as one "this call needed repair X" data point. No-op when empty or
        opted out (checked again in the task, since the toggle is live).
        """
        if not repair_types or not self.is_enabled():
            return
        distinct = sorted(set(repair_types))
        self._hass.async_create_task(self._async_emit(distinct, provider, model))

    def record_activity(self, name: str, n: int = 1) -> None:
        """Increment an in-memory activity counter.

        Cheap and synchronous — safe to call from any in-product action
        site. Accumulates regardless of the opt-in toggle (the counters
        never leave the process until ``async_send_activity`` runs, which
        *is* gated). A name outside ``_ACTIVITY_COUNTER_KEYS`` is ignored,
        so a typo'd or new label can never widen the payload.
        """
        if name not in _ACTIVITY_COUNTER_KEYS or n <= 0:
            return
        self._activity[name] = self._activity.get(name, 0) + n

    async def _async_emit(self, repair_types: list[str], provider: str, model: str) -> None:
        if not self.is_enabled():
            return
        app_version = await self._async_app_version()
        safe_model = _safe_model(provider, model)
        for repair_type in repair_types:
            await self._capture(
                TELEMETRY_EVENT_REPAIR,
                {
                    "repair_type": repair_type,
                    "provider": provider,
                    "model": safe_model,
                    "app_version": app_version,
                },
                allowed=_REPAIR_PROPERTY_KEYS,
            )

    async def async_send_snapshot(self, *, provider: str) -> None:
        """Gather and emit the anonymous home-inventory snapshot.

        Schedules nothing — call from a background task / timer. Counts
        only; never any entity id, name, or content. No-op when opted out.
        """
        if not self.is_enabled():
            return
        try:
            properties = await self._gather_snapshot(provider)
        except Exception:  # noqa: BLE001 — gathering must never break setup
            _LOGGER.debug("Telemetry snapshot gather failed", exc_info=True)
            return
        await self._capture(TELEMETRY_EVENT_SNAPSHOT, properties, allowed=_SNAPSHOT_PROPERTY_KEYS)

    async def async_send_activity(self, *, provider: str) -> None:
        """Flush the accumulated activity counters as one period event.

        Snapshots the counters by *copy* — they are cleared only after
        ``_capture`` confirms the POST landed. If consent is withdrawn
        during the awaited version/id lookup, or the POST fails, nothing
        is cleared and the full window is retried on the next tick (no
        silent 24h loss). Increments that arrive during the await are
        preserved by subtracting only the snapshotted counts on success,
        rather than clearing the whole dict. No-op when opted out
        (counters preserved until opt-in) or when nothing accumulated.
        """
        if not self.is_enabled() or not self._activity:
            return
        counters = dict(self._activity)
        properties: dict[str, Any] = {
            **{key: int(value) for key, value in counters.items()},
            "llm_provider": provider,
            "period_hours": TELEMETRY_SNAPSHOT_INTERVAL_HOURS,
            "app_version": await self._async_app_version(),
        }
        sent = await self._capture(
            TELEMETRY_EVENT_ACTIVITY, properties, allowed=_ACTIVITY_PROPERTY_KEYS
        )
        if not sent:
            return
        # Confirmed delivered — subtract exactly what was sent, leaving any
        # increments that landed during the await to seed the next window.
        for key, value in counters.items():
            remaining = self._activity.get(key, 0) - value
            if remaining > 0:
                self._activity[key] = remaining
            else:
                self._activity.pop(key, None)

    async def _gather_snapshot(self, provider: str) -> dict[str, Any]:
        """Build the snapshot payload from HA registries and Selora stores."""
        from homeassistant.helpers import area_registry as ar  # noqa: PLC0415
        from homeassistant.helpers import device_registry as dr  # noqa: PLC0415
        from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

        hass = self._hass
        dev_reg = dr.async_get(hass)
        ent_reg = er.async_get(hass)
        area_reg = ar.async_get(hass)

        # Device count grouped by the owning integration domain. Each device
        # counts once even if linked to multiple entries (dedup by domain set).
        # Bucket the per-integration breakdown against a curated public
        # allowlist. A custom (e.g. HACS) integration's domain is a
        # developer-chosen string that could embed a household/company/
        # project name, so anything not in the known-integrations catalog
        # is collapsed to "other" rather than transmitted verbatim.
        devices_by_integration: dict[str, int] = {}
        for device in dev_reg.devices.values():
            domains: set[str] = set()
            for entry_id in device.config_entries:
                entry = hass.config_entries.async_get_entry(entry_id)
                if entry is not None:
                    domains.add(entry.domain if entry.domain in KNOWN_INTEGRATIONS else "other")
            for domain in domains:
                devices_by_integration[domain] = devices_by_integration.get(domain, 0) + 1

        integrations = len({e.domain for e in hass.config_entries.async_entries()})

        selora_automations = 0
        try:
            from .automation_utils import count_selora_automations  # noqa: PLC0415

            selora_automations = count_selora_automations(hass)
        except Exception:  # noqa: BLE001 — best-effort count
            _LOGGER.debug("Selora automation count failed", exc_info=True)

        suggestions_accepted = await self._count_suggestions("accepted")
        suggestions_dismissed = await self._count_suggestions("dismissed")

        blueprints = await hass.async_add_executor_job(
            _count_blueprints, hass.config.path("blueprints")
        )

        snapshot: dict[str, Any] = {
            "devices": len(dev_reg.devices),
            "integrations": integrations,
            "automations": len(hass.states.async_all("automation")),
            "scenes": len(hass.states.async_all("scene")),
            "scripts": len(hass.states.async_all("script")),
            "blueprints": blueprints,
            "areas": len(area_reg.async_list_areas()),
            "entities": len(ent_reg.entities),
            "selora_automations": selora_automations,
            "suggestions_accepted": suggestions_accepted,
            "suggestions_dismissed": suggestions_dismissed,
            "llm_provider": provider,
            "devices_by_integration": devices_by_integration,
            "ha_version": HA_VERSION,
            "app_version": await self._async_app_version(),
        }

        # Coarse, self-declared install country from HA's own general
        # settings (ISO-3166 alpha-2, e.g. "CA"). Read locally — no IP is
        # ever sent and GeoIP stays disabled (see ``_capture``), so this is
        # the only geographic signal we transmit. Omitted when unset.
        country = hass.config.country
        if country:
            snapshot["country"] = country

        return snapshot

    async def _count_suggestions(self, status: str) -> int:
        """Count stored suggestions in ``status`` (0 on any failure)."""
        try:
            from .pattern_store import get_pattern_store  # noqa: PLC0415

            store = get_pattern_store(self._hass)
            if store is None:
                return 0
            return len(await store.get_suggestions(status=status))
        except Exception:  # noqa: BLE001 — best-effort count
            _LOGGER.debug("Suggestion count (%s) failed", status, exc_info=True)
            return 0

    async def _capture(
        self, event: str, properties: dict[str, Any], *, allowed: frozenset[str]
    ) -> bool:
        """POST one event to PostHog. Never raises — telemetry must not
        break the user-facing call path.

        Returns ``True`` only when the request completed with a non-error
        status, so callers that need at-least-once delivery (the activity
        rollup) can preserve their state until a confirmed send. Every
        early-out — opted out, disallowed payload, consent withdrawn
        mid-prep, network failure, or HTTP >= 400 — returns ``False``.
        """
        # Final consent gate. Gathering, manifest load, and install-id
        # storage all await before reaching here; the user may have
        # disabled telemetry meanwhile. Recheck so a withdrawn consent
        # stops any not-yet-started request.
        if not self.is_enabled():
            return False
        disallowed = set(properties) - allowed
        if disallowed:
            # A coding error tried to send something outside the allowlist.
            # Drop the whole event rather than risk leaking it.
            _LOGGER.error(
                "Telemetry payload rejected: disallowed properties %s",
                sorted(disallowed),
            )
            return False
        try:
            distinct_id = await self._async_install_id()
            # On the first event, the install-id load/save above awaits
            # storage after the consent gate. Recheck immediately before
            # opening the request so a consent withdrawal during that await
            # still cancels the POST.
            if not self.is_enabled():
                return False
            session = async_get_clientsession(self._hass)
            # Control properties (PostHog reserved `$` keys, not telemetry
            # data) that make IP anonymity hold regardless of the project's
            # "Discard client IP" setting: override the request IP with a
            # placeholder so the household's public IP is never stored, and
            # disable GeoIP enrichment so no location is derived. Added here,
            # after the data allowlist check, since they aren't caller data.
            # ``distinct_id`` lives inside ``properties`` per PostHog's
            # capture schema (a sibling top-level field is dropped).
            send_properties = {
                **properties,
                "distinct_id": distinct_id,
                "$ip": "0.0.0.0",
                "$geoip_disable": True,
            }
            payload = {
                "api_key": TELEMETRY_PROJECT_KEY,
                "event": event,
                "distinct_id": distinct_id,
                "properties": send_properties,
            }
            async with session.post(self._endpoint(), json=payload, timeout=_POST_TIMEOUT) as resp:
                if resp.status >= 400:
                    _LOGGER.debug("Telemetry POST returned HTTP %s", resp.status)
                    return False
            return True
        except Exception:  # noqa: BLE001 — telemetry must never raise
            _LOGGER.debug("Telemetry POST failed", exc_info=True)
            return False


def _read_manifest_version() -> str:
    """Read the integration version from manifest.json (blocking)."""
    try:
        manifest = Path(__file__).parent / "manifest.json"
        with manifest.open(encoding="utf-8") as fh:
            return str(json.load(fh).get("version", "unknown"))
    except OSError:
        return "unknown"
    except ValueError:
        # Malformed manifest JSON (json.JSONDecodeError subclasses ValueError).
        return "unknown"


# Providers whose model name is configured locally by the user (an
# arbitrary, potentially identifying string like "my-house-llama"). We
# never transmit those raw — they're replaced with a constant.
_LOCAL_MODEL_PROVIDERS: frozenset[str] = frozenset({LLM_PROVIDER_OLLAMA, LLM_PROVIDER_SELORA_LOCAL})

# Per-provider allowlist of public catalog model ids, sourced from the
# pricing table (the curated set of models Selora knows about). A
# shape-matching regex is not enough: a free-form cloud field can hold a
# catalog-shaped but user-chosen id like ``my-private-house-model-v3``.
# Only ids present in the known catalog are transmitted; everything else —
# fine-tunes, private deployments, typos — is redacted to ``custom`` so no
# configuration text can leak.
_CATALOG_MODELS_BY_PROVIDER: dict[str, frozenset[str]] = {
    provider: frozenset(models) for provider, models in LLM_PRICING_USD_PER_MTOK.items()
}


def _safe_model(provider: str, model: str) -> str:
    """Return a telemetry-safe model id.

    Local providers (user-named models) collapse to ``local``. Cloud model
    ids pass through only when they appear in the provider's known public
    catalog; otherwise they're redacted to ``custom`` so a fine-tuned,
    private, or otherwise user-chosen id can't carry an org or project name.
    """
    if provider in _LOCAL_MODEL_PROVIDERS:
        return "local"
    candidate = (model or "").strip()
    if not candidate:
        return ""
    catalog = _CATALOG_MODELS_BY_PROVIDER.get(provider, frozenset())
    return candidate if candidate in catalog else "custom"


def _count_blueprints(base: str) -> int:
    """Count ``*.yaml`` blueprint files under ``base`` (blocking).

    HA exposes no registry for blueprints — they're file-only under
    ``config/blueprints/{automation,script}`` — so we walk the tree.
    """
    root = Path(base)
    if not root.is_dir():
        return 0
    try:
        return sum(1 for _ in root.rglob("*.yaml"))
    except OSError:
        return 0


def get_telemetry(hass: HomeAssistant) -> TelemetryClient:
    """Return the shared TelemetryClient, creating it lazily."""
    bucket = hass.data.setdefault(DOMAIN, {})
    client = bucket.get("_telemetry")
    if client is None:
        client = TelemetryClient(hass)
        bucket["_telemetry"] = client
    return client


def record_activity(hass: HomeAssistant, name: str, n: int = 1) -> None:
    """Increment a shared activity counter — convenience for call sites.

    Forwards to ``TelemetryClient.record_activity``. Never raises, so an
    instrumentation point can call it inline without guarding the
    user-facing path.
    """
    try:
        get_telemetry(hass).record_activity(name, n)
    except Exception:  # noqa: BLE001 — telemetry must never break the call path
        _LOGGER.debug("record_activity(%s) failed", name, exc_info=True)
