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

from homeassistant.core import valid_entity_id

from .helpers import resolve_domain_ref, sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_MAX_LOG_ENTRIES: Final = 25
_MAX_TRACES: Final = 5

# Home Assistant's system log keeps WARNING and above only, so a lower level
# could never match anything.
_LEVELS: Final = ("CRITICAL", "ERROR", "WARNING")


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
    """Map an automation's or a script's entity_id or name to its trace key.

    An automation's key is ``automation.<config id>`` — the config id, not its
    object_id, so ``automation.porch_light`` (the only handle the rest of the
    tool surface gives out) is translated through the state's ``id``
    attribute. A script's is ``script.<unique id>``, from the registry, which
    survives a rename of its entity_id.

    A ``script.`` entity_id is a script; anything else is looked up as an
    automation first, then as a script. A name both use is refused: they
    share names freely, and the wrong one's runs would answer the question.
    """
    ref = str(ref or "").strip()
    if not ref:
        return None, "An automation or script entity_id or name is required."
    # An entity_id, or a bare object id, of either kind — exactly as written:
    # the state machine lowercases what it is asked, so a NAME such as
    # "Bedtime" would otherwise be taken for script.bedtime here.
    for candidate in (ref, f"automation.{ref}", f"script.{ref}"):
        if (
            candidate.split(".", 1)[0] in ("automation", "script")
            and valid_entity_id(candidate)
            and (found := hass.states.get(candidate))
        ):
            matches = [found]
            break
    else:
        # Every exact name across both kinds at once: an ambiguity in either is
        # an ambiguity, and must not fall through to the other kind's runs.
        wanted = ref.casefold()
        matches = [
            state
            for domain in ("automation", "script")
            for state in hass.states.async_all(domain)
            if state.name.casefold() == wanted
        ]
    if len(matches) > 1:
        ids = ", ".join(sorted(state.entity_id for state in matches))
        return None, (
            f"{len(matches)} automations or scripts are named "
            f"'{sanitize_untrusted_text(ref, 60)}' ({ids}). Pass the entity_id."
        )
    if not matches:
        return None, (
            f"No automation or script matching '{sanitize_untrusted_text(ref, 60)}'. "
            "Resolve it with search_entities(domain='automation') or domain='script'."
        )
    state = matches[0]
    if state.domain == "script":
        return _script_trace_key(hass, state.entity_id)

    unique_id = state.attributes.get("id")
    if not unique_id:
        return None, (
            f"'{state.entity_id}' has no config id, so Home Assistant stores no traces "
            "for it. YAML automations without an 'id' are not traced."
        )
    return f"automation.{unique_id}", None


def _script_trace_key(hass: HomeAssistant, ref: str) -> tuple[str | None, str | None]:
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    state, error = resolve_domain_ref(hass, "script", ref)
    if state is None:
        return None, error
    entry = er.async_get(hass).async_get(state.entity_id)
    unique_id = entry.unique_id if entry is not None else state.entity_id.split(".", 1)[1]
    return f"script.{unique_id}", None


def _step_config(config: Any, path: str) -> Any:
    """Return the piece of *config* a trace path such as ``condition/0`` names.

    Trace paths use HA's singular keys (``condition``, ``action``), while the
    stored config may use either form (``conditions:`` is the current one), and
    a single condition may be written as a mapping rather than a one-item list.
    """
    node = config
    for segment in path.split("/"):
        if isinstance(node, list) and segment.isdigit():
            index = int(segment)
            if index >= len(node):
                return None
            node = node[index]
        elif isinstance(node, dict) and segment.isdigit():
            if segment != "0":
                return None
        elif isinstance(node, dict):
            for key in (segment, f"{segment}s", segment.removesuffix("s")):
                if key in node:
                    node = node[key]
                    break
            else:
                return None
        else:
            return None
    return node


async def _stopped_at(hass: HomeAssistant, key: str, run_id: str, path: str) -> dict[str, Any]:
    """Describe the step a run ended on: its configuration and its result.

    A bare path (``condition/0``) is an index into a config the model has not
    seen, so on its own it only lets the model say *that* a condition failed.
    The extended trace carries the config the run used and the step's result
    (for a condition, ``result: false`` plus the entities it checked), which is
    what answers "which prerequisite failed".
    """
    from homeassistant.components.trace.util import async_get_trace  # noqa: PLC0415

    try:
        extended = await async_get_trace(hass, key, run_id)
    except KeyError:
        return {"path": path}
    step: dict[str, Any] = {"path": path}
    # A run stopped inside a condition ends on a leaf such as
    # `sequence/1/entity_id/0` — a string within the step, not the step. The
    # nearest enclosing mapping is the step the user wrote.
    segments = path.split("/")
    for end in range(len(segments), 0, -1):
        config = _step_config(extended.get("config"), "/".join(segments[:end]))
        if isinstance(config, dict):
            step["config"] = _json_safe(config)
            break
    elements = (extended.get("trace") or {}).get(path) or []
    if elements:
        last = elements[-1]
        if "result" in last:
            step["result"] = _json_safe(last["result"])
        if last.get("error"):
            step["error"] = sanitize_untrusted_text(last["error"], 300)
    return step


async def get_automation_traces(hass: HomeAssistant, ref: str) -> dict[str, Any]:
    """Return the most recent runs of one automation or script, newest first."""
    try:
        from homeassistant.components.trace.util import (  # noqa: PLC0415
            async_list_traces,
        )
    except ImportError:
        return {"error": "The trace component is not available."}

    key, error = _resolve_trace_key(hass, ref)
    if error or key is None:
        return {"error": error or "Automation not found."}
    domain = key.split(".", 1)[0]

    try:
        raw_traces = await async_list_traces(hass, domain, key)
    except Exception as exc:  # noqa: BLE001 — HomeAssistantError and friends
        return {"error": f"Could not read traces: {exc}"}

    traces = []
    for raw in list(raw_traces)[-_MAX_TRACES:][::-1]:
        trace = dict(raw)
        # ``last_step`` is where the run ended: a condition path means the
        # automation triggered and was stopped by a condition, which is the
        # single most common answer to "why didn't it run?".
        last_step = trace.get("last_step")
        run_id = trace.get("run_id")
        traces.append(
            {
                "run_id": run_id,
                "timestamp": _json_safe(trace.get("timestamp")),
                "trigger": sanitize_untrusted_text(trace.get("trigger"), 200)
                if trace.get("trigger")
                else None,
                "state": trace.get("state"),
                "script_execution": trace.get("script_execution"),
                "last_step": last_step,
                "stopped_at": await _stopped_at(hass, key, str(run_id), last_step)
                if last_step and run_id
                else None,
                "error": sanitize_untrusted_text(trace.get("error"), 300)
                if trace.get("error")
                else None,
            }
        )

    if not traces:
        return {
            "entity_id": ref,
            "trace_key": key,
            "traces": [],
            "message": (
                f"No retained trace is available for this {domain}. Home Assistant "
                f"keeps a bounded number of traces per {domain} and restores saved "
                f"ones after a restart, so this does not prove the {domain} has never "
                "run — only that no trace it kept is available now."
            ),
        }
    return {"trace_key": key, "traces": traces, "count": len(traces)}
