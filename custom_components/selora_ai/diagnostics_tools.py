"""Read-only diagnostics: recent errors and automation run traces.

These answer the question Selora could not answer at all before — "why didn't
my automation run?" The model could see the automation's YAML and its current
state, but nothing about what happened when it last fired, so the honest answer
was always a guess. A trace carries the actual trigger, the condition results,
and where the run stopped.

Both are strictly read-only, and both read HA's in-memory stores rather than
files: ``system_log`` keeps a deduplicated ring of the most recent warnings and
errors, and ``trace`` keeps a bounded number of runs per automation/script.
The system log does not survive a restart; traces partly do — HA saves them and
``async_list_traces`` restores them — so an empty trace result must not be
reported as "it has not run".
"""

from __future__ import annotations

from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any, Final

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_MAX_LOG_ENTRIES: Final = 25
_MAX_TRACES: Final = 5

_LEVELS: Final = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")


def get_logs(
    hass: HomeAssistant,
    *,
    level: str | None = None,
    contains: str | None = None,
) -> dict[str, Any]:
    """Return recent errors and warnings from HA's system log.

    ``system_log`` deduplicates: an error firing every 30 seconds appears once
    with a ``count``. That is the number worth reporting — a raw tail would show
    the same line 200 times and bury everything else.
    """
    try:
        from homeassistant.components.system_log import DATA_SYSTEM_LOG  # noqa: PLC0415
    except ImportError:
        return {"error": "The system_log component is not available."}

    # Argument validation runs before the store lookup: a bad ``level`` is a bad
    # argument whether or not system_log happens to be set up, and answering
    # "no errors captured" would leave the model thinking the call succeeded.
    level = str(level or "").strip().upper()
    if level and level not in _LEVELS:
        return {"error": f"level must be one of: {', '.join(_LEVELS)}."}
    needle = str(contains or "").strip().casefold()

    # ``hass.data[DATA_SYSTEM_LOG]`` is the logging *handler*; the deduplicated
    # ring it fills lives on ``.records``.
    store = getattr(hass.data.get(DATA_SYSTEM_LOG), "records", None)
    if store is None:
        return {
            "entries": [],
            "count": 0,
            "message": "system_log is not set up, so no errors have been captured.",
        }

    entries: list[dict[str, Any]] = []
    for raw in store.to_list():
        if level and str(raw.get("level", "")).upper() != level:
            continue
        message = " ".join(str(m) for m in (raw.get("message") or []))
        if needle and needle not in f"{message} {raw.get('name', '')}".casefold():
            continue
        entries.append(
            {
                "level": raw.get("level"),
                "logger": sanitize_untrusted_text(raw.get("name"), 120),
                "message": sanitize_untrusted_text(message, 400),
                "source": sanitize_untrusted_text(
                    (raw.get("source") or ["", 0])[0] if raw.get("source") else "", 160
                ),
                "count": raw.get("count", 1),
                "timestamp": raw.get("timestamp"),
            }
        )
        if len(entries) >= _MAX_LOG_ENTRIES:
            break

    return {
        "entries": entries,
        "count": len(entries),
        "note": "Deduplicated; 'count' is how many times each entry repeated since restart.",
    }


def _json_safe(value: Any) -> Any:
    """Recursively render *value* JSON-serialisable.

    A trace's ``timestamp`` is a mapping of ``datetime`` objects, not a string
    (``BaseTrace.as_short_dict``). The MCP dispatcher serialises tool results
    with a plain ``json.dumps`` and no ``default=``, so returning the mapping
    unchanged raises ``TypeError`` — and only ever on the path where a trace
    actually exists, which is exactly the case worth having.

    The chat path survives it by accident: ``_truncate_result`` passes
    ``default=str``. Relying on that would leave the two surfaces disagreeing
    about which tool calls work.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _resolve_trace_key(hass: HomeAssistant, ref: str) -> tuple[str | None, str | None]:
    """Map an automation entity_id or alias to its trace key.

    The key is ``automation.<config id>`` — the automation's *config* id, not
    its object_id, so ``automation.porch_light`` (the only handle the rest of
    the tool surface gives out) does not work as a key on its own and has to be
    translated through the state's ``id`` attribute.
    """
    ref = str(ref or "").strip()
    if not ref:
        return None, "An automation entity_id or name is required."

    state = hass.states.get(ref) if ref.startswith("automation.") else None
    if state is None:
        wanted = ref.casefold()
        state = next(
            (
                candidate
                for candidate in hass.states.async_all("automation")
                if candidate.name.casefold() == wanted or candidate.entity_id.casefold() == wanted
            ),
            None,
        )
    if state is None:
        return None, f"No automation matching '{sanitize_untrusted_text(ref, 60)}'."

    unique_id = state.attributes.get("id")
    if not unique_id:
        return None, (
            f"'{state.entity_id}' has no config id, so Home Assistant stores no traces "
            "for it. YAML automations without an 'id' are not traced."
        )
    return f"automation.{unique_id}", None


async def get_automation_traces(hass: HomeAssistant, ref: str) -> dict[str, Any]:
    """Return the most recent runs of one automation, newest first."""
    try:
        from homeassistant.components.trace.util import (  # noqa: PLC0415
            async_list_traces,
        )
    except ImportError:
        return {"error": "The trace component is not available."}

    key, error = _resolve_trace_key(hass, ref)
    if error or key is None:
        return {"error": error or "Automation not found."}

    try:
        raw_traces = await async_list_traces(hass, "automation", key)
    except Exception as exc:  # noqa: BLE001 — HomeAssistantError and friends
        return {"error": f"Could not read traces: {exc}"}

    traces = []
    for raw in list(raw_traces)[-_MAX_TRACES:][::-1]:
        trace = dict(raw)
        # ``last_step`` is where the run ended: a condition path means the
        # automation triggered and was stopped by a condition, which is the
        # single most common answer to "why didn't it run?".
        traces.append(
            {
                "run_id": trace.get("run_id"),
                "timestamp": _json_safe(trace.get("timestamp")),
                "state": trace.get("state"),
                "script_execution": trace.get("script_execution"),
                "last_step": trace.get("last_step"),
                "error": sanitize_untrusted_text(trace.get("error"), 300)
                if trace.get("error")
                else None,
            }
        )

    if not traces:
        return {
            "entity_id": ref,
            "traces": [],
            "message": (
                "No retained trace is available for this automation. Home Assistant "
                "keeps a bounded number of traces per automation and restores saved "
                "ones after a restart, so this does not prove the automation has never "
                "run — only that no trace it kept is available now."
            ),
        }
    return {"trace_key": key, "traces": traces, "count": len(traces)}
