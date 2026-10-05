"""Any Home Assistant service over MCP, gated by risk.

Chat keeps its allowlist and approval cards (``llm_client.command_policy``).
MCP has no card to show, so a call is sorted by risk instead:

* the denylist (restart, recorder purge, host reboot …) is refused outright;
* a LOW-risk call runs — the chat allowlist's curated services, the REVIEW
  table's low-risk entries (tts, notify, vacuum), and ``_LOW_RISK_SERVICES``;
* anything else — a REVIEW entry above low, a garage door, or a service no
  table names — needs ``confirmed: true``. The first call answers with the
  risk and the reason, and the agent asks the user before calling again.

An install that turned the approval requirement off is not asked again here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, TypedDict

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# Services that change only the entity they target and that the next call can
# set back, BY VERB: a domain is not a risk level — ``todo.remove_item``
# deletes, ``timer.finish`` fires the automations waiting on it,
# ``remote.send_command`` sends whatever it is given. Chat's allowlist counts
# with its own curated verbs (``_ALLOWED_COMMAND_SERVICES``), not its domains.
#
# No ``button`` / ``input_button``: a button can reboot or factory-reset a
# device, and pressing an input button fires whatever automations listen for it
# — the same unknown reach as a script.
_LOW_RISK_SERVICES: Final[dict[str, frozenset[str]]] = {
    "input_number": frozenset({"set_value", "increment", "decrement"}),
    "input_select": frozenset(
        {"select_option", "select_next", "select_previous", "select_first", "select_last"}
    ),
    "input_text": frozenset({"set_value"}),
    "input_datetime": frozenset({"set_datetime"}),
    "number": frozenset({"set_value"}),
    "select": frozenset(
        {"select_option", "select_next", "select_previous", "select_first", "select_last"}
    ),
    "text": frozenset({"set_value"}),
    "todo": frozenset({"add_item", "update_item", "get_items"}),
    "counter": frozenset({"increment", "decrement", "reset", "set_value"}),
    "timer": frozenset({"start", "pause", "cancel", "change"}),
    "humidifier": frozenset({"turn_on", "turn_off", "toggle", "set_humidity", "set_mode"}),
    "water_heater": frozenset({"turn_on", "turn_off", "set_temperature", "set_operation_mode"}),
    "lawn_mower": frozenset({"start_mowing", "pause", "dock"}),
    "remote": frozenset({"turn_on", "turn_off", "toggle"}),
    "calendar": frozenset({"get_events"}),
    "weather": frozenset({"get_forecasts"}),
}

# Far above chat's three: an MCP client is driving on purpose, "turn off every
# light" is an ordinary request, and anything risky is confirmed regardless.
_MAX_TARGETS: Final = 50

# Response data comes from the home — to-do item names, calendar summaries —
# so every string is bounded on its way out, like any other untrusted field.
_RESPONSE_STRING_LIMIT: Final = 500

# Ways to target entities other than ``entity_id``. Inside ``data`` they would
# reach Home Assistant unseen by the risk check — a garage door named there is
# not elevated — and device/area/floor/label targets expand to entities this
# module never checks. Refused; entities are named in ``entity_id``.
_TARGET_KEYS: Final = frozenset({"entity_id", "device_id", "area_id", "floor_id", "label_id"})

# Bound on the response once its strings are bounded, under the tool-result
# cap so the states and the rest of the result still fit beside it.
_RESPONSE_BUDGET: Final = 12000

_UNLISTED_REASON: Final = (
    "Not one of the services Selora knows to be low-risk, so its effect is unknown."
)


class ServiceVerdict(TypedDict, total=False):
    """What MCP may do with a service call, before running it."""

    valid: bool
    errors: list[str]
    service: str
    domain: str | None
    entity_ids: list[str]
    risk_level: str
    reason: str
    requires_confirmation: bool


def _schema_keys(schema: Any, depth: int = 0) -> set[str] | None:
    """Keys a service schema accepts; None if unreadable.

    The nesting is Home Assistant's and moves between releases: an entity
    service schema was ``All(Schema({...}), …)`` and became
    ``Schema(All(Schema({...}), …))``. So ``Schema`` and ``All`` are unwrapped
    until a dict of keys turns up, rather than to a fixed depth.
    """
    import voluptuous as vol  # noqa: PLC0415

    if depth > 5:
        return None
    if isinstance(schema, dict):
        return {str(key) for key in schema}
    if isinstance(schema, vol.Schema):
        return _schema_keys(schema.schema, depth + 1)
    if isinstance(schema, vol.All):
        for validator in schema.validators:
            if (keys := _schema_keys(validator, depth + 1)) is not None:
                return keys
    return None


def _needs_target(hass: HomeAssistant, domain: str, verb: str) -> bool:
    """Whether the service acts on entities and so must be told which.

    Called without one, an entity service is at the mercy of whatever the
    integration does with no target — for some, every entity in the domain.
    The REVIEW table says so explicitly for the services it lists; otherwise
    the service's own schema does, by taking ``entity_id``.
    """
    from .llm_client.command_policy import _classify_call  # noqa: PLC0415

    bucket, entry = _classify_call(f"{domain}.{verb}")
    # Either source can REQUIRE a target; neither can waive the other's. The
    # table's ``script.*`` entry is targetless for ``script.my_script``, but
    # ``script.turn_on`` matches it too and is an entity service.
    if bucket == "review" and entry is not None and entry.get("requires_target", True):
        return True
    service = hass.services.async_services_for_domain(domain).get(verb)
    keys = _schema_keys(getattr(service, "schema", None))
    if keys is not None:
        return "entity_id" in keys
    if bucket == "review" and entry is not None:
        return False
    # Unreadable schema: an entity domain still needs a target.
    return bool(hass.states.async_entity_ids(domain))


def _risk(hass: HomeAssistant, service: str, target_ids: list[str]) -> tuple[str, str]:
    """``(risk_level, reason)`` for a call that passed the shape checks."""
    from .const import APPROVAL_RISK_LOW, APPROVAL_RISK_MEDIUM  # noqa: PLC0415
    from .llm_client.command_policy import (  # noqa: PLC0415
        _ALLOWED_COMMAND_SERVICES,
        _classify_call,
        _entity_aware_review_entry,
    )

    elevated = _entity_aware_review_entry(hass, service, target_ids)
    if elevated is not None:
        return str(elevated["risk"]), str(elevated["reason"])
    bucket, entry = _classify_call(service)
    if bucket == "review" and entry is not None:
        return str(entry["risk"]), str(entry["reason"])
    domain, verb = service.split(".", 1)
    if verb in _ALLOWED_COMMAND_SERVICES.get(domain, ()) or verb in _LOW_RISK_SERVICES.get(
        domain, ()
    ):
        return APPROVAL_RISK_LOW, "Changes only the entities it targets."
    return APPROVAL_RISK_MEDIUM, _UNLISTED_REASON


def check_service_call(
    hass: HomeAssistant,
    service: str,
    raw_entity: Any,
    data: Any,
    *,
    confirmed: bool = False,
) -> ServiceVerdict:
    """Decide whether MCP may run this call now, without running it."""
    from .command_policy_options import resolve_command_policy_options  # noqa: PLC0415
    from .const import APPROVAL_RISK_LOW  # noqa: PLC0415
    from .llm_client.command_policy import (  # noqa: PLC0415
        _BLOCKED_SERVICES,
        _remote_media_content_error,
    )

    service = str(service or "").strip()
    verdict: ServiceVerdict = {"valid": False, "errors": [], "service": service, "domain": None}
    if "." not in service:
        verdict["errors"] = ["service must be in '<domain>.<verb>' form"]
        return verdict
    domain, verb = service.split(".", 1)
    verdict["domain"] = domain

    if isinstance(raw_entity, str):
        target_ids = [raw_entity.strip()] if raw_entity.strip() else []
    elif isinstance(raw_entity, list):
        target_ids = [str(e).strip() for e in raw_entity if str(e).strip()]
    elif raw_entity is None:
        target_ids = []
    else:
        verdict["errors"] = ["entity_id must be a string or a list of strings"]
        return verdict
    verdict["entity_ids"] = target_ids

    errors: list[str] = []
    if service in _BLOCKED_SERVICES:
        errors.append(f"'{service}' is never run from Selora; run it from Home Assistant directly.")
    elif not hass.services.has_service(domain, verb):
        errors.append(f"'{service}' is not a service Home Assistant has. Call list_services.")
    if data is not None and not isinstance(data, dict):
        errors.append("data must be an object")
    elif isinstance(data, dict) and (hidden := sorted(_TARGET_KEYS & set(data))):
        errors.append(
            f"{', '.join(hidden)} cannot go in data: name the entities in entity_id, "
            "so the call is checked against what it will actually act on."
        )
    if not errors and not target_ids and _needs_target(hass, domain, verb):
        errors.append(
            f"'{service}' acts on entities: name them in entity_id. Without one it "
            "could act on every entity of its kind."
        )
    if len(target_ids) > _MAX_TARGETS:
        errors.append(f"too many entity_ids at once (max {_MAX_TARGETS})")
    unknown = [eid for eid in target_ids if hass.states.get(eid) is None]
    if unknown:
        shown = ", ".join(sanitize_untrusted_text(eid, 80) for eid in unknown[:5])
        errors.append(f"not entities Home Assistant has: {shown}. Call search_entities.")
    if media_error := _remote_media_content_error(service, data):
        errors.append(media_error)
    if errors:
        verdict["errors"] = errors
        return verdict

    risk, reason = _risk(hass, service, target_ids)
    verdict["risk_level"] = risk
    verdict["reason"] = reason
    needs_confirmation = (
        risk != APPROVAL_RISK_LOW
        and resolve_command_policy_options(hass).approval_required
        and not confirmed
    )
    verdict["requires_confirmation"] = needs_confirmation
    verdict["valid"] = not needs_confirmation
    if needs_confirmation:
        verdict["errors"] = [
            f"'{service}' is {risk} risk: {reason} Ask the user to confirm, then call "
            "again with confirmed=true."
        ]
    return verdict


def _bounded(value: Any) -> Any:
    """Response data with every string sanitized and bounded."""
    if isinstance(value, str):
        return sanitize_untrusted_text(value, _RESPONSE_STRING_LIMIT)
    if isinstance(value, dict):
        return {str(k): _bounded(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bounded(v) for v in value]
    return value


def _longest_list(value: Any) -> list[Any] | None:
    """The longest list anywhere in *value*, at any depth."""
    best: list[Any] | None = None
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            if best is None or len(node) > len(best):
                best = node
            stack.extend(node)
        elif isinstance(node, dict):
            stack.extend(node.values())
    return best


def _within_budget(response: Any) -> tuple[Any, bool]:
    """``(response, trimmed)`` — halving the longest list until it fits.

    Responses nest (``todo.get_items`` → entity → ``items``), deeper than the
    generic result trimmer looks, so a long to-do list would otherwise leave
    the result over its cap.
    """
    import json  # noqa: PLC0415

    def size() -> int:
        return len(json.dumps(response, ensure_ascii=False, default=str))

    trimmed = False
    while size() > _RESPONSE_BUDGET:
        longest = _longest_list(response)
        if longest is None or len(longest) <= 1:
            return {"omitted": "the response was too large to return"}, True
        del longest[len(longest) // 2 :]
        trimmed = True
    return response, trimmed


async def async_execute_service_call(
    hass: HomeAssistant,
    service: str,
    raw_entity: Any,
    data: Any,
    *,
    confirmed: bool = False,
) -> dict[str, Any]:
    """Run any service MCP may run now; otherwise say why, or what to confirm.

    A service that returns data (to-do items, calendar events, forecasts) is
    asked for it, since Home Assistant refuses a response-only service called
    without asking.
    """
    from homeassistant.core import SupportsResponse  # noqa: PLC0415

    from .mcp_server.commands import _call_service_and_settle  # noqa: PLC0415
    from .tool_executor import _truncate_result  # noqa: PLC0415

    verdict = check_service_call(hass, service, raw_entity, data, confirmed=confirmed)
    if not verdict["valid"]:
        return {"executed": False, **verdict}

    domain, verb = verdict["service"].split(".", 1)
    supports = hass.services.supports_response(domain, verb)
    result = await _call_service_and_settle(
        hass,
        verdict["service"],
        verdict["entity_ids"],
        dict(data or {}),
        want_response=supports != SupportsResponse.NONE,
        # A read changes no state, so waiting for one would only sit out the
        # settle timeout on every to-do or calendar read.
        settle=supports != SupportsResponse.ONLY,
    )
    if "response" in result:
        result["response"], trimmed = _within_budget(_bounded(result["response"]))
        if trimmed:
            result["response_truncated"] = True
    result["risk_level"] = verdict["risk_level"]
    return _truncate_result(result)
