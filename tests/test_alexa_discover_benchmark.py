"""Times HA's Alexa mapping over a real entity registry.

ADR-0022's performance gate has exactly one unmeasured term: how long
``async_handle_message`` takes to build a ``Discover.Response`` over a full
registry. That cost is CPU inside the HA process — ``async_get_entities``
walking every state and ``serialize_discovery`` building a capability list per
endpoint — so an end-to-end number cannot isolate it and a payload-size curve
measures the wrong variable.

This replays a real installation's registry into a real ``hass`` and times the
handler with nothing else in the way. Point ``SELORA_ALEXA_SNAPSHOT`` at a
directory holding ``core.entity_registry`` (and optionally
``core.restore_state``) copied from the installation's ``.storage``; without
it the module skips, since a snapshot names a household's devices and does not
belong in the repository.

    SELORA_ALEXA_SNAPSHOT=/path/to/snapshot \
        pytest tests/test_alexa_discover_benchmark.py -s -q

States are reconstructed from the registry — ``capabilities``,
``supported_features``, ``device_class`` and ``unit_of_measurement`` are stored
there precisely so they survive a restart, and they are what the capability
mapping reads — overlaid with the real attributes of whatever
``core.restore_state`` retained. What that does not reproduce is the live value
of each state, which changes the serialized *properties* of a report but not
the set of interfaces an endpoint is discovered with.
"""

from __future__ import annotations

from collections import Counter
import gzip
import json
import os
from pathlib import Path
import statistics
import time
from typing import Any

from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
import pytest

pytest.importorskip(
    "turbojpeg",
    reason="alexa.entities imports the camera integration, which needs PyTurboJPEG",
)

from custom_components.selora_ai.alexa_config import SeloraAlexaConfig  # noqa: E402

_SNAPSHOT_ENV = "SELORA_ALEXA_SNAPSHOT"
_ITERATIONS = int(os.environ.get("SELORA_ALEXA_ITERATIONS", "30"))
# Clones the snapshot N times under distinct entity_ids, to read the slope
# rather than one point — the gate has to hold for homes larger than the one
# that happened to be snapshotted.
_MULTIPLIER = int(os.environ.get("SELORA_ALEXA_MULTIPLIER", "1"))

pytestmark = pytest.mark.skipif(
    not os.environ.get(_SNAPSHOT_ENV),
    reason=f"set {_SNAPSHOT_ENV} to a directory holding a .storage registry snapshot",
)

# A plausible state per domain, used where restore_state retained none. The
# value decides which properties serialize, not which interfaces exist, so an
# "on" that should be "off" costs nothing in the figure being measured.
_DOMAIN_STATES = {
    "alarm_control_panel": "disarmed",
    "binary_sensor": "off",
    "climate": "heat",
    "cover": "open",
    "fan": "on",
    "humidifier": "on",
    "light": "on",
    "lock": "locked",
    "media_player": "playing",
    "number": "42",
    "sensor": "21.5",
    "switch": "on",
    "vacuum": "docked",
    "valve": "open",
    "water_heater": "eco",
}


def _snapshot_dir() -> Path:
    return Path(os.environ[_SNAPSHOT_ENV])


def _percentile(samples: list[float], pct: float) -> float:
    """Nearest-rank percentile — no interpolation across a short sample."""
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, round(pct / 100 * len(ordered) + 0.5) - 1))
    return ordered[index]


def _report(label: str, samples_ms: list[float], extra: str = "") -> None:
    print(
        f"  {label:<34} n={len(samples_ms):<4} "
        f"p50={statistics.median(samples_ms):8.2f} ms  "
        f"p99={_percentile(samples_ms, 99):8.2f} ms  "
        f"min={min(samples_ms):8.2f}  max={max(samples_ms):8.2f}  {extra}"
    )


async def _seed_registry(hass: Any) -> list[dict[str, Any]]:
    """Load the snapshot's entities into the real registry and state machine."""
    entities = json.loads((_snapshot_dir() / "core.entity_registry").read_text())["data"][
        "entities"
    ]

    restored: dict[str, dict[str, Any]] = {}
    restore_path = _snapshot_dir() / "core.restore_state"
    if restore_path.exists():
        for record in json.loads(restore_path.read_text())["data"]:
            state = record.get("state") or {}
            if entity_id := state.get("entity_id"):
                restored[entity_id] = state

    registry = er.async_get(hass)
    for copy_index in range(_MULTIPLIER):
        for entry in entities:
            _seed_entry(hass, registry, entry, restored, copy_index)

    await hass.async_block_till_done()
    return entities * _MULTIPLIER


def _seed_entry(
    hass: Any,
    registry: er.EntityRegistry,
    entry: dict[str, Any],
    restored: dict[str, dict[str, Any]],
    copy_index: int,
) -> None:
    """Create one registry entry and, unless it is disabled, its state."""
    entity_id: str = entry["entity_id"]
    domain, _, object_id = entity_id.partition(".")
    if copy_index:
        object_id = f"{object_id}_copy{copy_index}"
    category = entry.get("entity_category")
    created = registry.async_get_or_create(
        domain,
        entry.get("platform") or "snapshot",
        f"{entry.get('unique_id') or entity_id}#{copy_index}",
        suggested_object_id=object_id,
        capabilities=entry.get("capabilities"),
        supported_features=entry.get("supported_features") or 0,
        original_device_class=entry.get("original_device_class"),
        original_name=entry.get("original_name"),
        unit_of_measurement=entry.get("unit_of_measurement"),
        entity_category=EntityCategory(category) if category else None,
        hidden_by=er.RegistryEntryHider(entry["hidden_by"]) if entry.get("hidden_by") else None,
        disabled_by=er.RegistryEntryDisabler(entry["disabled_by"])
        if entry.get("disabled_by")
        else None,
    )
    if created.disabled:
        # A disabled entity holds no state, which is what keeps it out of
        # discovery — the registry entry alone would not.
        return

    attributes: dict[str, Any] = {
        "friendly_name": entry.get("name")
        or entry.get("original_name")
        or object_id.replace("_", " ").title(),
    }
    if features := entry.get("supported_features"):
        attributes["supported_features"] = features
    if device_class := (entry.get("device_class") or entry.get("original_device_class")):
        attributes["device_class"] = device_class
    if unit := entry.get("unit_of_measurement"):
        attributes["unit_of_measurement"] = unit
    if isinstance(capabilities := entry.get("capabilities"), dict):
        attributes.update(capabilities)

    state_value = _DOMAIN_STATES.get(domain, "on")
    # Keyed on the ORIGINAL entity_id: a clone is the same device under
    # another name, and matching on the clone's id would silently give every
    # copy past the first the reconstructed attributes instead of the real
    # ones — making a larger registry look cheaper per entity than it is.
    if real := restored.get(entity_id):
        state_value = real.get("state") or state_value
        if isinstance(real_attrs := real.get("attributes"), dict):
            attributes.update(real_attrs)
            attributes["friendly_name"] = (
                f"{real_attrs.get('friendly_name', object_id)} {copy_index}"
                if copy_index
                else real_attrs.get("friendly_name", object_id)
            )

    hass.states.async_set(created.entity_id, state_value, attributes)


def _directive(namespace: str, name: str, **extra: Any) -> dict[str, Any]:
    header = {
        "namespace": namespace,
        "name": name,
        "payloadVersion": "3",
        "messageId": "bench",
    }
    return {"directive": {"header": header, "payload": {}, **extra}}


async def test_alexa_handler_cost_over_a_real_registry(hass: Any) -> None:
    """Report the handler's own cost for Discover, TurnOn and ReportState."""
    from homeassistant.components.alexa.smart_home import async_handle_message

    assert await async_setup_component(hass, "homeassistant", {})
    raw_entities = await _seed_registry(hass)

    config = SeloraAlexaConfig(hass, "bench-installation")
    await config.async_initialize()

    discover = _directive(
        "Alexa.Discovery", "Discover", payload={"scope": {"type": "BearerToken", "token": "t"}}
    )

    # The FIRST Discover is its own measurement. `async_should_expose` writes
    # its answer into each entity's registry options the first time it is
    # asked, so this one call also materialises an exposure decision for every
    # entity in the home — and that is paid inside a directive, against the
    # same 3s budget as any other.
    first_started = time.perf_counter()
    warm = await async_handle_message(hass, config, discover)
    first_discover_ms = (time.perf_counter() - first_started) * 1000
    endpoints = warm["event"]["payload"]["endpoints"]
    body = json.dumps(warm).encode()
    body_bytes = len(body)
    gzipped_bytes = len(gzip.compress(body, 6))

    discover_ms: list[float] = []
    for _ in range(_ITERATIONS):
        started = time.perf_counter()
        await async_handle_message(hass, config, discover)
        discover_ms.append((time.perf_counter() - started) * 1000)

    # A control directive and a state report, against a real exposed endpoint.
    target = next(
        (
            state.entity_id
            for state in hass.states.async_all()
            if state.domain in ("light", "switch") and config.should_expose(state.entity_id)
        ),
        None,
    )
    assert target is not None, "snapshot exposes no light or switch to control"
    hass.services.async_register(target.split(".")[0], "turn_on", lambda call: None)

    endpoint_block = {
        "scope": {"type": "BearerToken", "token": "t"},
        "endpointId": target.replace(".", "#"),
        "cookie": {},
    }
    turn_on = _directive("Alexa.PowerController", "TurnOn", endpoint=endpoint_block)
    report_state = _directive("Alexa", "ReportState", endpoint=endpoint_block)

    control_ms: list[float] = []
    state_ms: list[float] = []
    for message, samples in ((turn_on, control_ms), (report_state, state_ms)):
        await async_handle_message(hass, config, message)
        for _ in range(_ITERATIONS):
            started = time.perf_counter()
            await async_handle_message(hass, config, message)
            samples.append((time.perf_counter() - started) * 1000)

    # Serialization is the hub's cost too, and it sits outside the handler —
    # so a figure for the handler alone would understate what the hub spends
    # on a Discover before a single byte leaves it.
    serialize_ms: list[float] = []
    for _ in range(_ITERATIONS):
        started = time.perf_counter()
        json.dumps(warm).encode()
        serialize_ms.append((time.perf_counter() - started) * 1000)

    # ReportState is per endpoint, so a full refresh is one directive each.
    # This is both the limiter's sizing case and the concurrency workload the
    # gate asks for, and it is the only figure that scales with the registry
    # on the control side.
    # Swept over the DISCOVERED endpoints, not over everything should_expose
    # admits: Alexa only ever asks about what discovery handed it, and an
    # entity in no Alexa domain is an error round rather than a report.
    sweep_started = time.perf_counter()
    for endpoint in endpoints:
        await async_handle_message(
            hass,
            config,
            _directive(
                "Alexa",
                "ReportState",
                endpoint={
                    "scope": {"type": "BearerToken", "token": "t"},
                    "endpointId": endpoint["endpointId"],
                    "cookie": {},
                },
            ),
        )
    sweep_ms = (time.perf_counter() - sweep_started) * 1000

    exposed = sum(1 for s in hass.states.async_all() if config.should_expose(s.entity_id))
    print("\n\nAlexa handler cost — in-process, transport excluded")
    print(f"  registry entities        {len(raw_entities)}")
    print(f"  states in machine        {len(hass.states.async_all())}")
    print(f"  should_expose            {exposed}")
    by_domain = Counter(e["endpointId"].split("#")[0] for e in endpoints)
    print(f"  discovered endpoints     {len(endpoints)}  ({dict(by_domain.most_common())})")
    print(f"  Amazon's 300 ceiling     {len(endpoints) * 100 // 300}% consumed")
    print(f"  Discover.Response body   {body_bytes:,} bytes ({gzipped_bytes:,} gzipped)")
    print(f"  first Discover (cold)    {first_discover_ms:8.2f} ms  (materialises exposure)")
    _report("Alexa.Discovery/Discover", discover_ms)
    _report("Alexa.PowerController/TurnOn", control_ms)
    _report("Alexa/ReportState", state_ms)
    _report("json.dumps(Discover.Response)", serialize_ms)
    print(f"  full ReportState sweep   {len(endpoints)} directives in {sweep_ms:.0f} ms\n")
