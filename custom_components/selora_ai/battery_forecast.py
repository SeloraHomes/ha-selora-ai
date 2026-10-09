"""Battery depletion forecasts for the insights export.

For every replaceable battery in the home, estimate when it reaches 0% so an
installer can swap several in one visit before any dies. Rechargeable devices
(phones, vacuums, cars, home batteries) are never forecast — see
:func:`is_rechargeable`.

The series comes from Home Assistant's own recorder: daily long-term statistics
when the sensor has a ``state_class`` (they survive the purge), raw state
history otherwise. The fit is Theil–Sen (median of pairwise slopes), which a
coin cell's late cliff or a 10%-step reporter can't drag the way a least-squares
line would; its rank-based slope interval becomes ``depleted_range``.

Computed at most every ``REFRESH_INTERVAL`` and cached in memory between
publishes. Rules and the integration survey behind them: ``docs/dev/battery-forecast.md``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
import math
from statistics import median
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .health_monitor import _is_transient

if TYPE_CHECKING:
    from .types import BatteryForecast, BatteryForecastItem

_LOGGER = logging.getLogger(__name__)

REFRESH_INTERVAL = timedelta(hours=6)
# How far back the series reaches. Long-term statistics are kept forever, so
# this bounds the query, not the data; recorder states rarely reach it.
_HISTORY_WINDOW = timedelta(days=365)
# Past this, the device is left out rather than given a date.
_HORIZON = timedelta(days=3 * 365)

# A rise of this many points is a battery swap (once) or a recharge (repeatedly).
_RISE_POINTS = 5.0

# Minimum to forecast at all.
_MIN_LEVELS = 3
_MIN_SPAN_DAYS = 14.0
# "medium" when the minimum is clearly met: one more level, twice the span.
_MEDIUM_LEVELS = 4
_MEDIUM_SPAN_DAYS = 28.0
# "high" needs a long, well-sampled series that the line actually follows.
_HIGH_LEVELS = 6
_HIGH_SPAN_DAYS = 60.0
# "Close to the fit": the median absolute residual, in percentage points. 3
# points admits 5%-step reporters (whose readings sit up to half a step off a
# straight line) and rejects a coin cell's flat-then-cliff shape, whose
# residuals around any single line run well past it.
_HIGH_MAX_MEDIAN_RESIDUAL = 3.0

# z for the slope interval (two-sided ~95%).
_SLOPE_Z = 1.96
# Pairwise slopes are O(n²); a year of daily readings stays well under this.
_MAX_FIT_POINTS = 400

_NON_READINGS = frozenset({"unavailable", "unknown", "none", ""})

# A charging-state sensor's states (HA's battery-state convention, used by
# mobile_app and many device integrations).
_CHARGING_STATES = frozenset({"charging", "discharging", "full", "not_charging"})
# ...but "full" is also a tank's or a bin's state, so the sensor must name the
# battery or charging in its entity_id or registry name/key too.
_CHARGING_NAME_TOKENS = ("batter", "charg")

# Integrations whose battery is recharged, not replaced. Every name here is one
# this codebase already knows (``KNOWN_INTEGRATIONS`` in ``const.py``, or
# ``mobile_app``); ``tests/test_battery_forecast.py`` holds the list to that.
# No other phone/computer integration (icloud, system_bridge, …) appears in
# the codebase or its fixtures, so none is listed — a device they bring in
# still drops out through a charging companion entity or its recharge history.
RECHARGEABLE_INTEGRATIONS = frozenset(
    {
        # Phones, tablets, watches and computers running the HA companion app.
        "mobile_app",
        # Robot vacuums and mowers: they dock to charge.
        "roomba",
        "roborock",
        "sharkiq",
        "ecovacs",
        "husqvarna_automower",
        # Electric vehicles.
        "tesla_fleet",
        "bmw_connected_drive",
        "volvo",
        "subaru",
        "mbapi2020",
        "fordpass",
        "kia_uvo",
        "polestar_api",
        # Home batteries.
        "powerwall",
        "enphase_envoy",
    }
)

# (timestamp seconds, level %)
Reading = tuple[float, float]


@dataclass(frozen=True)
class Companion:
    """Another entity on the same device, as the exclusion rules see it."""

    entity_id: str
    device_class: str | None
    state: str | None
    # Registry name, translation key and unique id: what the entity is, when
    # its state alone ("full") could be anything's.
    name: str = ""


@dataclass(frozen=True)
class _Candidate:
    entity_id: str
    device_id: str
    level: int
    reading: float  # the live state, appended to the history
    integrations: frozenset[str]
    companions: tuple[Companion, ...]
    has_statistics: bool


# ── Exclusion ──────────────────────────────────────────────────────────────


def is_rechargeable(
    integrations: Iterable[str],
    companions: Iterable[Companion],
    series: Sequence[Reading],
) -> bool:
    """True when the device's battery is recharged rather than replaced.

    Any one of: an integration in ``RECHARGEABLE_INTEGRATIONS``; a companion
    ``battery_charging`` binary sensor or a battery/charging-named sensor
    reporting a charging state;
    or a history with more than one rise of ``_RISE_POINTS`` that falls again.
    A single rise is a battery swap — :func:`since_last_replacement` handles it.
    """
    if any(domain in RECHARGEABLE_INTEGRATIONS for domain in integrations):
        return True
    for companion in companions:
        if companion.entity_id.startswith("binary_sensor.") and (
            companion.device_class == "battery_charging"
        ):
            return True
        if (
            companion.entity_id.startswith("sensor.")
            and (companion.state or "").casefold() in _CHARGING_STATES
            and any(
                token in f"{companion.entity_id} {companion.name}".casefold()
                for token in _CHARGING_NAME_TOKENS
            )
        ):
            return True
    recharges = sum(1 for _trough, peak in _rises(series) if peak < len(series) - 1)
    return recharges > 1


# ── Series ─────────────────────────────────────────────────────────────────


def clean_series(raw: Iterable[tuple[float, Any]]) -> list[Reading]:
    """Numeric readings in time order, clamped to 0–100. Drops unavailable,
    unknown, non-numeric and non-finite states."""
    readings: list[Reading] = []
    for ts, value in raw:
        if value is None or (isinstance(value, str) and value.casefold() in _NON_READINGS):
            continue
        try:
            level = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(level):
            continue
        readings.append((ts, min(100.0, max(0.0, level))))
    readings.sort(key=lambda r: r[0])
    return readings


def _rises(series: Sequence[Reading]) -> list[tuple[int, int]]:
    """(trough, peak) indices of each non-decreasing run gaining ``_RISE_POINTS``
    or more. A run spanning several readings (a swap logged as 30 → 60 → 100 in
    daily means, or a slow charge) is one rise."""
    rises: list[tuple[int, int]] = []
    trough = 0
    for i in range(1, len(series) + 1):
        if i < len(series) and series[i][1] >= series[i - 1][1]:
            continue
        peak = i - 1
        if series[peak][1] - series[trough][1] >= _RISE_POINTS:
            rises.append((trough, peak))
        trough = i
    return rises


def since_last_replacement(series: Sequence[Reading]) -> list[Reading]:
    """The series from the last battery swap on (from the reading it rose to)."""
    rises = _rises(series)
    if not rises:
        return list(series)
    return list(series[rises[-1][1] :])


def _distinct_readings(series: Sequence[Reading]) -> list[Reading]:
    """One reading per level change. A device reporting in steps sits on each
    level for weeks; the first reading at a level is when it got there, and the
    repeats would only weigh the fit toward the flat stretches."""
    out: list[Reading] = []
    for reading in series:
        if not out or reading[1] != out[-1][1]:
            out.append(reading)
    return out


# ── Fit ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Fit:
    slope: float  # % per day (negative while draining)
    slope_low: float  # steepest plausible
    slope_high: float  # shallowest plausible
    median_residual: float  # percentage points


def _theil_sen(points: Sequence[tuple[float, float]]) -> _Fit | None:
    """Theil–Sen line through (day, level) points, with Sen's rank interval on
    the slope. ``None`` when every point shares one timestamp."""
    slopes = sorted(
        (y2 - y1) / (x2 - x1)
        for i, (x1, y1) in enumerate(points)
        for x2, y2 in points[i + 1 :]
        if x2 != x1
    )
    if not slopes:
        return None
    slope = median(slopes)
    intercept = median(y - slope * x for x, y in points)
    residual = median(abs(y - (intercept + slope * x)) for x, y in points)
    n, count = len(points), len(slopes)
    spread = _SLOPE_Z * math.sqrt(n * (n - 1) * (2 * n + 5) / 18)
    low_index = min(count - 1, max(0, math.floor((count - spread) / 2)))
    high_index = min(count - 1, max(0, math.ceil((count + spread) / 2)))
    return _Fit(slope, slopes[low_index], slopes[high_index], residual)


def _thin(points: list[Reading], limit: int) -> list[Reading]:
    if len(points) <= limit:
        return points
    step = (len(points) - 1) / (limit - 1)
    return [points[round(i * step)] for i in range(limit)]


def forecast_battery(
    entity_id: str,
    device_id: str,
    level: int,
    series: Sequence[Reading],
    now: datetime,
) -> BatteryForecastItem | None:
    """The forecast for one replaceable battery, or ``None`` when its series
    can't support one (too few levels, too short, flat, or past the horizon).

    ``series`` is the cleaned series since the last replacement.
    """
    readings = _thin(_distinct_readings(series), _MAX_FIT_POINTS)
    if not readings:
        return None
    levels = len({value for _ts, value in readings})
    start = readings[0][0]
    span_days = (readings[-1][0] - start) / 86400
    if levels < _MIN_LEVELS or span_days < _MIN_SPAN_DAYS:
        return None
    fit = _theil_sen([((ts - start) / 86400, value) for ts, value in readings])
    if fit is None or fit.slope >= 0:
        return None

    drain = -fit.slope
    # Projected from the last level change: a stepped reporter reached its
    # current level then, and has been draining unseen since.
    anchor_ts, anchor_level = readings[-1]
    anchor = datetime.fromtimestamp(anchor_ts, UTC)
    horizon = now + _HORIZON
    # Compared in days before building a datetime: a near-flat drain would
    # otherwise overflow one.
    days_to_horizon = (horizon - anchor).total_seconds() / 86400
    if anchor_level / drain > days_to_horizon:
        return None
    depleted = anchor + timedelta(days=anchor_level / drain)
    steepest, shallowest = -fit.slope_low, -fit.slope_high
    earliest = anchor + timedelta(days=anchor_level / max(steepest, drain))
    # The shallowest plausible drain can be flat or rising on a short series;
    # the range then runs to the horizon rather than claiming a later date.
    if shallowest > 0 and anchor_level / shallowest < days_to_horizon:
        latest = anchor + timedelta(days=anchor_level / shallowest)
    else:
        latest = horizon

    if (
        levels >= _HIGH_LEVELS
        and span_days >= _HIGH_SPAN_DAYS
        and fit.median_residual <= _HIGH_MAX_MEDIAN_RESIDUAL
    ):
        confidence = "high"
    elif levels >= _MEDIUM_LEVELS and span_days >= _MEDIUM_SPAN_DAYS:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "entity_id": entity_id,
        "device_id": device_id,
        "level": level,
        "drain_per_day": round(drain, 3),
        "depleted_at": depleted.isoformat(),
        "depleted_range": {
            "earliest": min(earliest, depleted).isoformat(),
            "latest": max(latest, depleted).isoformat(),
        },
        "confidence": confidence,
        "since": datetime.fromtimestamp(start, UTC).isoformat(),
        "points": len(readings),
    }


# ── Home Assistant side ────────────────────────────────────────────────────


class BatteryForecaster:
    """Computes the home's battery forecasts, at most every ``REFRESH_INTERVAL``."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._lock = asyncio.Lock()
        self._result: BatteryForecast | None = None
        self._attempted_at: datetime | None = None

    async def async_get(self) -> BatteryForecast | None:
        """The cached forecast, recomputed when older than ``REFRESH_INTERVAL``.

        ``None`` when it can't be computed (no recorder, or the query failed) —
        an unknown forecast, which the export omits rather than sending an
        empty one that would read as "nothing to replace".
        """
        async with self._lock:
            now = datetime.now(UTC)
            if self._attempted_at is not None and now - self._attempted_at < REFRESH_INTERVAL:
                return self._result
            # Stamped before computing, so a failing recorder is retried on the
            # refresh cadence and not on every publish.
            self._attempted_at = now
            try:
                self._result = await self._async_compute(now)
            except Exception:  # noqa: BLE001 — a forecast miss must never fail the export
                _LOGGER.exception("Battery forecast failed; keeping the previous one")
            return self._result

    async def _async_compute(self, now: datetime) -> BatteryForecast | None:
        if "recorder" not in self._hass.config.components:
            return None
        candidates = self._candidates()
        histories = await self._async_histories(candidates, now - _HISTORY_WINDOW, now)
        items: list[BatteryForecastItem] = []
        for candidate in candidates:
            # The live reading ends every series: daily statistics lag up to a
            # day, and a battery swapped since then must restart the fit, not
            # pair its new level with the old battery's date.
            series = clean_series(
                [*histories.get(candidate.entity_id, []), (now.timestamp(), candidate.reading)]
            )
            if is_rechargeable(candidate.integrations, candidate.companions, series):
                continue
            item = forecast_battery(
                candidate.entity_id,
                candidate.device_id,
                candidate.level,
                since_last_replacement(series),
                now,
            )
            if item is not None:
                items.append(item)
        items.sort(key=lambda item: item["depleted_at"])
        return {"generated_at": now.isoformat(), "items": items}

    def _candidates(self) -> list[_Candidate]:
        """Battery-level sensors on a device, readable now, that the health
        monitor would also judge — so an urgent forecast always has the
        ``battery_low`` signal behind it (``tests/test_battery_forecast.py``)."""
        from .entity_filter import resolve_ignored_entity_ids  # noqa: PLC0415

        excluded = resolve_ignored_entity_ids(self._hass)
        ent_reg = er.async_get(self._hass)
        dev_reg = dr.async_get(self._hass)
        candidates: list[_Candidate] = []
        for state in self._hass.states.async_all("sensor"):
            if state.attributes.get("device_class") != "battery":
                continue
            if state.entity_id in excluded or _is_transient(state.entity_id, ent_reg):
                continue
            entry = ent_reg.async_get(state.entity_id)
            if entry is None or entry.device_id is None:
                continue
            device = dev_reg.async_get(entry.device_id)
            if device is None:
                continue
            level = clean_series([(0.0, state.state)])
            if not level:
                continue
            integrations = {entry.platform}
            for entry_id in device.config_entries:
                if config_entry := self._hass.config_entries.async_get_entry(entry_id):
                    integrations.add(config_entry.domain)
            companions = []
            for sibling in er.async_entries_for_device(ent_reg, device.id):
                if sibling.entity_id == state.entity_id:
                    continue
                sibling_state = self._hass.states.get(sibling.entity_id)
                companions.append(
                    Companion(
                        sibling.entity_id,
                        (sibling_state.attributes.get("device_class") if sibling_state else None)
                        or sibling.device_class
                        or sibling.original_device_class,
                        sibling_state.state if sibling_state else None,
                        " ".join(
                            str(part)
                            for part in (
                                sibling.name,
                                sibling.original_name,
                                sibling.translation_key,
                                sibling.unique_id,
                            )
                            if part
                        ),
                    )
                )
            candidates.append(
                _Candidate(
                    state.entity_id,
                    device.id,
                    round(level[0][1]),
                    level[0][1],
                    frozenset(integrations),
                    tuple(companions),
                    state.attributes.get("state_class") is not None,
                )
            )
        return candidates

    async def _async_histories(
        self, candidates: list[_Candidate], start: datetime, end: datetime
    ) -> dict[str, list[tuple[float, Any]]]:
        """Each candidate's raw (timestamp, level) history over the window."""
        from homeassistant.components.recorder import get_instance  # noqa: PLC0415
        from homeassistant.components.recorder.history import (  # noqa: PLC0415
            get_significant_states,
        )
        from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
            get_metadata,
            statistics_during_period,
        )

        if not candidates:
            return {}
        instance = get_instance(self._hass)
        wanted = {c.entity_id for c in candidates if c.has_statistics}
        with_stats: set[str] = set()
        if wanted:
            metadata = await instance.async_add_executor_job(
                lambda: get_metadata(self._hass, statistic_ids=wanted)
            )
            with_stats = wanted & set(metadata)
        histories: dict[str, list[tuple[float, Any]]] = {}
        if with_stats:
            # Statistics are converted to the state's display unit; a battery
            # whose state lacks "%" would come back as a 0-1 fraction.
            rows = await instance.async_add_executor_job(
                statistics_during_period,
                self._hass,
                start,
                end,
                with_stats,
                "day",
                {"unitless": "%"},
                {"mean"},
            )
            for entity_id, entity_rows in rows.items():
                histories[entity_id] = [(row["start"], row.get("mean")) for row in entity_rows]
        from_states = [c.entity_id for c in candidates if c.entity_id not in with_stats]
        if from_states:
            states = await instance.async_add_executor_job(
                lambda: get_significant_states(
                    self._hass, start, end, from_states, no_attributes=True
                )
            )
            for entity_id, entity_states in states.items():
                histories[entity_id] = [
                    (s.last_changed.timestamp(), s.state)
                    for s in entity_states
                    if not isinstance(s, dict)
                ]
        return histories
