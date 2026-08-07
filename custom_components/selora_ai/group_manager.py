"""Create, inspect, and modify Home Assistant group helpers.

A group gives an automation ONE stable entity_id that fans out to many
devices, so membership can change later without touching the automation.
That makes "turn off every downstairs light" a single-target action whose
member list the homeowner can edit in Settings → Devices & Services →
Helpers.

We drive HA's own ``group`` config-entry flow rather than writing a legacy
``group:`` YAML block. The flow route persists to ``.storage``, needs no
``configuration.yaml`` include, stays editable in the Helpers UI, and yields
a domain-typed entity (``light.evening_lights``) that accepts the full
service surface of its domain — ``light.turn_on`` with brightness/colour,
not just an opaque on/off toggle.

The trade-off: HA group helpers are per-domain. Mixed light+switch
membership is impossible by construction, so :func:`infer_group_type`
refuses it with an explanation instead of silently dropping members.

All state lives in ``entry.options`` (``group_type``/``name``/``entities``
plus per-type extras); ``entry.data`` is empty. See
``homeassistant/components/group/config_flow.py``.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import valid_entity_id
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import voluptuous as vol

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .types import GroupInfo

_LOGGER = logging.getLogger(__name__)

GROUP_DOMAIN: Final = "group"

# The group_type values HA's helper flow offers. Mirrors GROUP_TYPES in
# homeassistant/components/group/config_flow.py — kept as our own tuple so
# the tool schema has a static enum and no component-internal import runs at
# module import time. :func:`async_create_group` cross-checks the requested
# type against the live flow's menu_options, so a version skew surfaces as a
# clear error rather than a wrong-looking group.
SUPPORTED_GROUP_TYPES: Final[tuple[str, ...]] = (
    "binary_sensor",
    "button",
    "cover",
    "event",
    "fan",
    "light",
    "lock",
    "media_player",
    "notify",
    "sensor",
    "switch",
    "valve",
)

# Only these three group types accept the ``all`` toggle. The other config
# schemas are vol.PREVENT_EXTRA, so passing ``all`` to e.g. a cover group is
# a validation error, not a harmless no-op.
_SUPPORTS_ALL: Final = frozenset({"binary_sensor", "light", "switch"})

# HA's sensor group aggregates numeric members from three domains into one
# statistic, so these all map to group_type "sensor".
_SENSOR_MEMBER_DOMAINS: Final = frozenset({"sensor", "number", "input_number"})

# Statistic a sensor group publishes; required by SENSOR_CONFIG_SCHEMA.
SENSOR_STATISTICS: Final[tuple[str, ...]] = (
    "last",
    "first_available",
    "max",
    "mean",
    "median",
    "min",
    "product",
    "range",
    "stdev",
    "sum",
)
_DEFAULT_SENSOR_STATISTIC: Final = "mean"

# Members reported per group by :func:`describe_group`. A listing orients the
# caller — it does not need to enumerate a 600-member group, and the entity_id
# is what any follow-up acts on. The cap is not cosmetic: ToolExecutor's
# semantic trimmer cannot descend into a list of dicts, so one oversized group
# makes it drop the whole record and the caller gets a bare ``count`` with no
# name, entity_id, or members at all.
_MAX_LISTED_MEMBERS: Final = 50

# Raised by the flow on bad input (InvalidData subclasses vol.Invalid;
# AbortFlow/UnknownStep/UnknownHandler subclass HomeAssistantError).
_FLOW_ERRORS: Final = (HomeAssistantError, vol.Invalid)


def _member_domain(entity_id: str) -> str:
    """Return the domain part of an entity_id."""
    return entity_id.split(".", 1)[0]


def infer_group_type(entity_ids: Sequence[str]) -> tuple[str | None, str | None]:
    """Resolve the group_type implied by *entity_ids*.

    Returns ``(group_type, None)`` or ``(None, error)``. HA group helpers are
    per-domain, so a mixed-domain member list is rejected with guidance rather
    than partially honoured.
    """
    domains = {_member_domain(e) for e in entity_ids}
    if not domains:
        return None, "entities must list at least one entity_id"
    # Checked before the single-domain branch so a numeric group may mix
    # sensor + number + input_number members, which HA explicitly allows.
    if domains <= _SENSOR_MEMBER_DOMAINS:
        return "sensor", None
    if len(domains) > 1:
        return None, (
            "A Home Assistant group helper holds one domain only, but these "
            f"members span {', '.join(sorted(domains))}. Create one group per "
            "domain (e.g. a light group and a switch group) instead of mixing them."
        )
    domain = next(iter(domains))
    if domain not in SUPPORTED_GROUP_TYPES:
        return None, (
            f"Home Assistant has no group helper for the '{domain}' domain. "
            f"Groupable domains: {', '.join(SUPPORTED_GROUP_TYPES)}."
        )
    return domain, None


def validate_members(hass: HomeAssistant, raw: Any) -> tuple[list[str], str | None]:
    """Normalise a member list: dedupe, keep order, reject unknown entities.

    Unknown ids are refused rather than passed through: HA's EntitySelector
    does not verify existence server-side, so a hallucinated ``light.bedroom``
    would otherwise create a permanently-broken group that reports
    ``unavailable`` with no obvious cause.
    """
    if isinstance(raw, str):
        # Tolerate a comma-joined string — some models emit one despite the
        # array schema.
        raw = [part.strip() for part in raw.split(",")]
    if not isinstance(raw, list) or not raw:
        return [], "entities must be a non-empty array of entity_ids"

    registry = er.async_get(hass)
    members: list[str] = []
    seen: set[str] = set()
    malformed: list[str] = []
    unknown: list[str] = []

    for item in raw:
        entity_id = str(item or "").strip()
        if not entity_id:
            continue
        if not valid_entity_id(entity_id):
            malformed.append(entity_id)
            continue
        if entity_id in seen:
            continue
        if hass.states.get(entity_id) is None and registry.async_get(entity_id) is None:
            unknown.append(entity_id)
            continue
        seen.add(entity_id)
        members.append(entity_id)

    if malformed:
        return [], (
            "Not valid entity_ids (expected '<domain>.<object_id>'): "
            f"{', '.join(sanitize_untrusted_text(m, limit=60) for m in malformed[:5])}"
        )
    if unknown:
        return [], (
            "These entities do not exist in Home Assistant: "
            f"{', '.join(sanitize_untrusted_text(u, limit=60) for u in unknown[:5])}. "
            "Resolve names to real entity_ids with search_entities first."
        )
    if not members:
        return [], "entities must list at least one entity_id"
    return members, None


def group_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Return every group-helper config entry."""
    return list(hass.config_entries.async_entries(GROUP_DOMAIN))


def own_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> set[str]:
    """Return every entity_id *entry* itself owns.

    Mirrors what HA's options flow excludes via
    ``entity_selector_without_own_entities``: a group must never list its own
    entity as a member, or it tracks its own state and can loop.
    """
    registry = er.async_get(hass)
    return {e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}


def group_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Return the entity_id a group helper entry produced, if registered."""
    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    return entries[0].entity_id if entries else None


def _resolve_members(hass: HomeAssistant, members: Sequence[str]) -> list[str]:
    """Map stored member ids to entity_ids, positionally.

    HA's entity selector validates with ``cv.entity_id_or_uuid`` and stores
    whichever form the caller sent, so a UI-created group's member list may
    hold entity-registry ids. Anything reasoning about *what* a member is —
    domain inference, self-reference, membership comparison — has to run on
    the resolved form, and anything *reported* has to be resolved too: a
    registry id is not something the caller can quote or feed to another tool.
    Unresolvable ids (a deleted entity) are kept as-is so they stay visible to
    the caller rather than silently vanishing.
    """
    registry = er.async_get(hass)
    return [er.async_resolve_entity_id(registry, member) or member for member in members]


def describe_group(hass: HomeAssistant, entry: ConfigEntry) -> GroupInfo:
    """Summarise one group-helper entry for a tool result.

    Members are capped at :data:`_MAX_LISTED_MEMBERS`; ``member_count`` stays
    exact and ``members_omitted`` says how many were left out.
    """
    options = entry.options
    # Resolved for the caller: list_groups feeds entity_ids to the entity-based
    # tools, and a stored registry id is useless to both the LLM and the user.
    all_members = _resolve_members(hass, [str(e) for e in options.get("entities", []) or []])
    members = all_members[:_MAX_LISTED_MEMBERS]
    entity_id = group_entity_id(hass, entry)
    state = hass.states.get(entity_id) if entity_id else None
    info: GroupInfo = {
        "entry_id": entry.entry_id,
        "name": sanitize_untrusted_text(options.get("name") or entry.title),
        "group_type": str(options.get("group_type", "")),
        "entity_id": entity_id,
        "state": state.state if state is not None else "unknown",
        "members": members,
        "member_count": len(all_members),
    }
    if (omitted := len(all_members) - len(members)) > 0:
        info["members_omitted"] = omitted
    if entry.options.get("hide_members"):
        info["hide_members"] = True
    if "all" in options:
        info["requires_all_members"] = bool(options["all"])
    if options.get("group_type") == "sensor" and options.get("type"):
        info["statistic"] = str(options["type"])
    return info


def unmanaged_yaml_groups(hass: HomeAssistant) -> list[str]:
    """Return ``group.*`` entities that are NOT helper config entries.

    These come from a legacy ``group:`` YAML block (including the ones Selora
    recipes write). They cannot be edited through the helper flow, so they are
    surfaced read-only — otherwise a request to change one looks to the model
    like a nonexistent group and invites a confusing duplicate.
    """
    helper_ids = {
        entity_id
        for entry in group_entries(hass)
        if (entity_id := group_entity_id(hass, entry)) is not None
    }
    return sorted(
        state.entity_id
        for state in hass.states.async_all(GROUP_DOMAIN)
        if state.entity_id not in helper_ids
    )


_NO_HELPERS_ERROR = "This home has no group helpers yet. Create one with create_group."


def _all_members_unsupported_error(group_type: str) -> str:
    """Explain that all-members mode does not exist for *group_type*."""
    return (
        f"A '{group_type}' group has no all-members mode — Home Assistant offers it only "
        f"for {', '.join(sorted(_SUPPORTS_ALL))} groups. Drop requires_all_members."
    )


def _yaml_group_error(hass: HomeAssistant, target: str) -> str:
    """Explain that *target* is a YAML group and therefore not editable here."""
    return (
        f"{sanitize_untrusted_text(target)} is a YAML-defined group, not a helper "
        "group, so it cannot be edited here. Change it where it is defined "
        "(or create a helper group instead) — do NOT create a duplicate."
    )


def _find_yaml_group_by_name(hass: HomeAssistant, needle: str) -> str | None:
    """Return a YAML group entity_id whose name or object_id matches *needle*."""
    for entity_id in unmanaged_yaml_groups(hass):
        object_id = entity_id.split(".", 1)[-1]
        state = hass.states.get(entity_id)
        friendly = str((state.attributes.get("friendly_name") if state else "") or "")
        labels = {friendly.casefold(), object_id.casefold(), object_id.replace("_", " ").casefold()}
        if needle in labels:
            return entity_id
    return None


def resolve_group(
    hass: HomeAssistant, *, entity_id: str = "", entry_id: str = "", name: str = ""
) -> tuple[ConfigEntry | None, str | None]:
    """Find one group-helper entry by entity_id, entry_id, or name.

    Returns ``(entry, None)`` or ``(None, error)``. Name matching is
    casefolded-exact first, then unique substring — an ambiguous substring is
    an error listing the candidates rather than an arbitrary pick.

    A YAML-defined group is reported as such (not as missing) even when the
    home has NO helper groups at all. Getting that order wrong is what makes
    the model report a group the user can plainly see as nonexistent, and then
    offer to create a duplicate of it.
    """
    entries = group_entries(hass)

    if entry_id:
        match = next((e for e in entries if e.entry_id == entry_id), None)
        if match is None:
            return None, f"No group helper with entry_id {sanitize_untrusted_text(entry_id)}"
        return match, None

    if entity_id:
        match = next((e for e in entries if group_entity_id(hass, e) == entity_id), None)
        if match is not None:
            return match, None
        # Checked before the no-helpers shortcut: a home whose only groups are
        # YAML-defined still owes the user the read-only explanation.
        if entity_id in unmanaged_yaml_groups(hass):
            return None, _yaml_group_error(hass, entity_id)
        if not entries:
            return None, _NO_HELPERS_ERROR
        return None, f"No group helper found for {sanitize_untrusted_text(entity_id)}"

    needle = " ".join(name.split()).casefold()
    if not needle:
        return None, "Provide entity_id, entry_id, or name to identify the group"

    def _label(entry: ConfigEntry) -> str:
        return str(entry.options.get("name") or entry.title or "").casefold()

    exact = [e for e in entries if _label(e) == needle]
    candidates = exact or [e for e in entries if needle in _label(e)]
    if not candidates:
        # The user may well be naming a YAML group ("add the lamp to the
        # Downstairs group"), so try that before declaring it missing.
        if (yaml_match := _find_yaml_group_by_name(hass, needle)) is not None:
            return None, _yaml_group_error(hass, yaml_match)
        if not entries:
            return None, _NO_HELPERS_ERROR
        return None, (
            f"No group helper named '{sanitize_untrusted_text(name)}'. "
            "Call list_groups to see what exists."
        )
    if len(candidates) > 1:
        names = ", ".join(
            sanitize_untrusted_text(e.options.get("name") or e.title, limit=60)
            for e in candidates[:5]
        )
        return None, (
            f"'{sanitize_untrusted_text(name)}' matches several groups ({names}). "
            "Identify the one you mean by entity_id."
        )
    return candidates[0], None


def _apply_member_visibility(hass: HomeAssistant, members: Sequence[str], hide: bool) -> None:
    """Hide or unhide group members, mirroring HA's own helper flow.

    HA applies this in ``async_config_flow_finished`` /
    ``async_options_flow_finished``. Creation goes through the real flow so it
    happens for free; an update bypasses the options flow, so newly-added
    members of an already-hidden group need it applied here or they would stay
    visible while their siblings are hidden.
    """
    registry = er.async_get(hass)
    hidden_by = er.RegistryEntryHider.INTEGRATION if hide else None
    for member in members:
        entity_id = er.async_resolve_entity_id(registry, member)
        if entity_id is None:
            continue
        entry = registry.async_get(entity_id)
        if entry is None:
            continue
        # A hand-applied hide is left alone in BOTH directions. Releasing it
        # would undo the user's choice outright; overwriting it with an
        # integration hide looks harmless but silently transfers ownership, so
        # removing the member later (or deleting the group) would release a
        # hide the user set for their own reasons.
        if entry.hidden_by == er.RegistryEntryHider.USER:
            continue
        # Only ever release a hide WE (or HA's group flow) applied.
        if not hide and entry.hidden_by != er.RegistryEntryHider.INTEGRATION:
            continue
        if entry.hidden_by == hidden_by:
            continue
        registry.async_update_entity(entity_id, hidden_by=hidden_by)


def _non_numeric_members(hass: HomeAssistant, entity_ids: Sequence[str]) -> list[str]:
    """Members whose current state is known but is not a number.

    HA builds our sensor groups with ``ignore_non_numeric`` at its default of
    False, which does NOT mean "refuse" — ``SensorGroup`` drops the member it
    cannot parse from the calculation and logs a warning. So a text member is
    accepted, silently excluded, and the group still reports success with it in
    the member list; a group of nothing but text members publishes ``unknown``.
    Either way the caller named a member that contributes nothing.

    ``unknown``/``unavailable`` pass: a perfectly numeric sensor reads both
    while its device is offline, and refusing then would make whether a group
    can be created depend on whether a battery happened to be flat.
    """
    offenders: list[str] = []
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, ""):
            continue
        try:
            float(state.state)
        except ValueError:
            offenders.append(entity_id)
    return offenders


def _non_numeric_error(hass: HomeAssistant, offenders: Sequence[str]) -> str:
    """Explain which members cannot take part in a numeric aggregate."""
    listed = ", ".join(
        f"{sanitize_untrusted_text(entity_id, limit=60)} "
        f"('{sanitize_untrusted_text(state.state, limit=30)}')"
        if (state := hass.states.get(entity_id)) is not None
        else sanitize_untrusted_text(entity_id, limit=60)
        for entity_id in offenders[:5]
    )
    return (
        f"A numeric group combines its members into one number, but these report "
        f"text: {listed}. Leave them out, or group them another way — Home Assistant "
        "has no helper that aggregates non-numeric sensors."
    )


def _user_hidden_members(hass: HomeAssistant, members: Sequence[str]) -> list[str]:
    """Resolved members the user hid by hand.

    Creation goes through HA's real config flow, whose
    ``async_config_flow_finished`` hook hides members by writing
    ``hidden_by = INTEGRATION`` with no regard for what was there before. We
    cannot opt out of that hook, so the provenance is captured beforehand and
    put back after — otherwise removing the member later, or deleting the
    group, releases a hide the user set for their own reasons.
    """
    registry = er.async_get(hass)
    hidden: list[str] = []
    for member in members:
        entity_id = er.async_resolve_entity_id(registry, member)
        entry = registry.async_get(entity_id) if entity_id else None
        if entry is not None and entry.hidden_by == er.RegistryEntryHider.USER:
            hidden.append(entity_id)  # type: ignore[arg-type]
    return hidden


def _restore_user_hides(hass: HomeAssistant, entity_ids: Sequence[str]) -> None:
    """Give back the USER provenance HA's hide hook overwrote."""
    registry = er.async_get(hass)
    for entity_id in entity_ids:
        entry = registry.async_get(entity_id)
        if entry is not None and entry.hidden_by == er.RegistryEntryHider.INTEGRATION:
            registry.async_update_entity(entity_id, hidden_by=er.RegistryEntryHider.USER)


def _members_free_to_unhide(
    hass: HomeAssistant, members: Sequence[str], exclude_entry_id: str
) -> list[str]:
    """Narrow *members* to those no OTHER hide_members group still claims.

    An entity may belong to several group helpers, and hiding is a property of
    the entity, not of the membership. Dropping it from one hidden group would
    otherwise reveal it in the UI while a second hidden group still lists it —
    that group's members would no longer be uniformly hidden, and nothing in
    its own config changed to explain why.

    Membership is compared on resolved entity_ids because HA's entity selector
    may persist a registry entry id instead, so the two groups can refer to the
    same entity in different forms.
    """
    registry = er.async_get(hass)
    claimed: set[str] = set()
    for other in group_entries(hass):
        if other.entry_id == exclude_entry_id or not other.options.get("hide_members"):
            continue
        for member in other.options.get("entities", []) or []:
            if (resolved := er.async_resolve_entity_id(registry, str(member))) is not None:
                claimed.add(resolved)

    free: list[str] = []
    for member in members:
        resolved = er.async_resolve_entity_id(registry, member)
        # Unresolvable members are passed through for _apply_member_visibility
        # to skip, keeping the "is it known?" decision in one place.
        if resolved is None or resolved not in claimed:
            free.append(member)
    return free


def _build_create_payload(
    *,
    name: str,
    members: list[str],
    group_type: str,
    hide_members: bool,
    requires_all_members: bool | None,
    statistic: str | None,
) -> dict[str, Any]:
    """Assemble the form payload for a group_type's config step."""
    payload: dict[str, Any] = {
        "name": name,
        "entities": members,
        "hide_members": hide_members,
    }
    if group_type in _SUPPORTS_ALL:
        payload["all"] = bool(requires_all_members)
    if group_type == "sensor":
        payload["type"] = statistic or _DEFAULT_SENSOR_STATISTIC
    return payload


async def async_create_group(
    hass: HomeAssistant,
    *,
    name: str,
    entities: Any,
    group_type: str | None = None,
    hide_members: bool = False,
    requires_all_members: bool | None = None,
    statistic: str | None = None,
) -> dict[str, Any]:
    """Create a group helper. Returns a result dict or ``{"error": ...}``."""
    clean_name = " ".join(str(name or "").split())
    if not clean_name:
        return {"error": "name is required"}

    members, error = validate_members(hass, entities)
    if error:
        return {"error": error}

    inferred, error = infer_group_type(members)
    if error:
        return {"error": error}
    if group_type:
        requested = str(group_type).strip()
        if requested != inferred:
            return {
                "error": (
                    f"group_type '{sanitize_untrusted_text(requested)}' does not match the "
                    f"members, which form a '{inferred}' group. Omit group_type to infer it."
                )
            }
    resolved_type = inferred or ""

    # An inapplicable per-type option is refused rather than dropped, because
    # _build_create_payload can only forward an option to the schemas that
    # accept it — so it would otherwise be ignored while the call still
    # reported success. requires_all_members is user-expressible on any of the
    # state-combining types ("closed only when every cover is"), so a caller
    # asking for it on a cover group means something we cannot deliver.
    if requires_all_members is not None and resolved_type not in _SUPPORTS_ALL:
        return {"error": _all_members_unsupported_error(resolved_type)}

    # statistic is the opposite case and is dropped, not refused. Only a sensor
    # group holds a number, so "mean of two lights" is not a request a user can
    # make — there is no intent here to discard. The MCP schema cannot express
    # "statistic only when the members are numeric", and models volunteer it
    # from the enum, so refusing turned "group my two lights" into a dead end.
    # Checked before the flow so nothing is written for a group that would
    # quietly ignore half its members.
    if resolved_type == "sensor" and (offenders := _non_numeric_members(hass, members)):
        return {"error": _non_numeric_error(hass, offenders)}

    if statistic and resolved_type != "sensor":
        _LOGGER.debug(
            "Ignoring statistic %r on a '%s' group: only sensor groups publish one.",
            statistic,
            resolved_type,
        )
        statistic = None

    # Validated after the drop above, so a bogus value on a non-numeric group
    # goes the same way as a valid one instead of erroring on an ignored field.
    if statistic and statistic not in SENSOR_STATISTICS:
        return {
            "error": (
                f"statistic must be one of: {', '.join(SENSOR_STATISTICS)}; "
                f"got '{sanitize_untrusted_text(statistic)}'"
            )
        }

    # Refuse a duplicate name up front: group helpers carry no unique_id, so HA
    # would happily create a second "Evening Lights" and every later
    # resolve-by-name would become ambiguous.
    for entry in group_entries(hass):
        existing = str(entry.options.get("name") or entry.title or "")
        if existing.casefold() == clean_name.casefold():
            return {
                "error": (
                    f"A group named '{sanitize_untrusted_text(clean_name)}' already exists "
                    f"({group_entity_id(hass, entry) or entry.entry_id}). Use update_group "
                    "to change its members, or pick a different name."
                )
            }

    payload = _build_create_payload(
        name=clean_name,
        members=members,
        group_type=resolved_type,
        hide_members=hide_members,
        requires_all_members=requires_all_members,
        statistic=statistic,
    )

    # Every non-success exit must drop the flow, or a rejected request leaves an
    # orphaned "Group" flow sitting in the user's Settings → Devices & Services
    # page that they have to dismiss by hand. The finally block covers the
    # early returns and the raising paths alike; aborting an already-finished
    # flow is a no-op (see :func:`_abort_flow`).
    # Captured before the flow runs its hide hook, restored once it has.
    user_hidden = _user_hidden_members(hass, members) if hide_members else []

    flow_id: str | None = None
    succeeded = False
    try:
        started = await hass.config_entries.flow.async_init(
            GROUP_DOMAIN, context={"source": "user"}
        )
        flow_id = started.get("flow_id")
        if started.get("type") != "menu":
            return {"error": "Unexpected group config flow shape; cannot create the group."}
        # The live menu is the authority on what this HA version can group.
        menu_options = started.get("menu_options") or []
        if resolved_type not in menu_options:
            return {
                "error": (
                    f"This Home Assistant version cannot group '{resolved_type}' entities. "
                    f"Groupable: {', '.join(str(o) for o in menu_options)}."
                )
            }
        form = await hass.config_entries.flow.async_configure(
            flow_id, {"next_step_id": resolved_type}
        )
        if form.get("type") != "form":
            return {"error": f"Group flow did not present the {resolved_type} form."}
        result = await hass.config_entries.flow.async_configure(flow_id, payload)
        if result.get("type") != "create_entry":
            reason = str(result.get("reason") or result.get("type") or "unknown")
            return {"error": f"Group was not created ({sanitize_untrusted_text(reason)})."}
        succeeded = True
    except _FLOW_ERRORS as exc:
        _LOGGER.warning("Group creation flow failed for %s: %s", clean_name, exc)
        return {"error": f"Home Assistant rejected the group: {exc}"}
    finally:
        if flow_id and not succeeded:
            _abort_flow(hass, flow_id)

    if user_hidden:
        _restore_user_hides(hass, user_hidden)

    entry: ConfigEntry = result["result"]
    entity_id = group_entity_id(hass, entry)
    _LOGGER.info(
        "Created %s group '%s' (%s) with %d members",
        resolved_type,
        clean_name,
        entity_id or entry.entry_id,
        len(members),
    )
    return {
        "status": "created",
        "entry_id": entry.entry_id,
        "entity_id": entity_id,
        "name": clean_name,
        "group_type": resolved_type,
        "members": members,
        "member_count": len(members),
    }


def _abort_flow(hass: HomeAssistant, flow_id: str) -> None:
    """Drop a half-driven config flow so it doesn't linger in the UI."""
    # Already finished or gone — nothing to clean up.
    with contextlib.suppress(KeyError, HomeAssistantError):
        hass.config_entries.flow.async_abort(flow_id)


async def async_update_group(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    name: str | None = None,
    entities: Any = None,
    add_entities: Any = None,
    remove_entities: Any = None,
    requires_all_members: bool | None = None,
) -> dict[str, Any]:
    """Rename a group and/or change its membership.

    ``entities`` replaces the member list; ``add_entities`` /
    ``remove_entities`` apply a delta to it. Returns a result dict or
    ``{"error": ...}``.
    """
    options = dict(entry.options)
    group_type = str(options.get("group_type", ""))
    current: list[str] = [str(e) for e in options.get("entities", []) or []]
    new_members = current
    # The stored form is preserved on write: a registry id keeps tracking an
    # entity across an entity_id rename, so normalizing it away would make a
    # UI-created group's membership fragile. Only the comparisons below use the
    # resolved view.
    current_entity_ids = _resolve_members(hass, current)
    stored_by_entity_id = dict(zip(current_entity_ids, current, strict=True))

    # Replacement and delta are different intents, and an LLM tool call can
    # carry both despite the schema wording. Guessing would silently drop the
    # loser (the deltas, since replacement is applied first) while still
    # reporting success, so refuse and make the caller pick.
    if entities is not None and (add_entities is not None or remove_entities is not None):
        return {
            "error": (
                "Pass either entities (replace the whole member list) or "
                "add_entities/remove_entities (adjust it) — not both, since they are "
                "different operations and combining them is ambiguous."
            )
        }

    if entities is not None:
        new_members, error = validate_members(hass, entities)
        if error:
            return {"error": error}
        # A retained member keeps the representation it was stored under. The
        # caller names entity_ids, but rewriting a registry id into one would
        # silently trade a rename-proof reference for a fragile one on a group
        # the user built in the UI. Genuinely new members are stored as given.
        new_members = [stored_by_entity_id.get(member, member) for member in new_members]
    elif add_entities is not None or remove_entities is not None:
        additions: list[str] = []
        removals: list[str] = []
        drop: set[str] = set()
        if add_entities is not None:
            additions, error = validate_members(hass, add_entities)
            if error:
                return {"error": error}
        if remove_entities is not None:
            # Removals are matched against the CURRENT membership, so an
            # already-deleted entity can still be removed from the group.
            raw = (
                [p.strip() for p in remove_entities.split(",")]
                if isinstance(remove_entities, str)
                else remove_entities
            )
            if not isinstance(raw, list):
                return {"error": "remove_entities must be an array of entity_ids"}
            removals = [str(r or "").strip() for r in raw if str(r or "").strip()]
            # Matched in either form: the caller names an entity_id, while the
            # stored member may be a registry id.
            known = set(current)
            missing = []
            for candidate in removals:
                if candidate in known:
                    drop.add(candidate)
                elif (stored := stored_by_entity_id.get(candidate)) is not None:
                    drop.add(stored)
                else:
                    missing.append(candidate)
            if missing:
                return {
                    "error": (
                        "Not members of this group: "
                        f"{', '.join(sanitize_untrusted_text(m, limit=60) for m in missing[:5])}"
                    )
                }
        merged = [e for e in current if e not in drop]
        # Deduped on the resolved form: additions are entity_ids, so a raw
        # compare against a uuid-stored member appends the same entity twice.
        held = {
            entity_id
            for stored, entity_id in zip(current, current_entity_ids, strict=True)
            if stored not in drop
        }
        for addition in additions:
            if addition in held:
                continue
            merged.append(addition)
            held.add(addition)
        new_members = merged

    clean_name = " ".join(str(name or "").split()) if name is not None else None
    if name is not None and not clean_name:
        return {"error": "name cannot be empty"}

    if clean_name is None and new_members == current and requires_all_members is None:
        return {"error": "Nothing to change: provide a new name, members, or all-members flag."}

    # Checked BEFORE anything is written. The no-op guard above counts
    # requires_all_members as a requested change, but only the three supporting
    # types can store it — so without this an all-members-only update on a
    # cover/fan group would reload the entry, change nothing, and still report
    # ``status: updated``.
    if requires_all_members is not None and group_type not in _SUPPORTS_ALL:
        return {"error": _all_members_unsupported_error(group_type)}

    if not new_members:
        return {
            "error": (
                "A group must keep at least one member. Delete the group instead of emptying it."
            )
        }

    # A group listing its own entity tracks its own state, which can loop or
    # otherwise malfunction. HA's options flow makes this unrepresentable via
    # entity_selector_without_own_entities; we bypass that flow, so the guard
    # has to live here. Members that are OTHER groups stay legal — nesting is
    # a supported pattern.
    # Resolved, so a stored registry id is still recognised as this group's own
    # entity and still yields a domain rather than being read as one.
    new_entity_ids = _resolve_members(hass, new_members)
    if self_refs := sorted(own_entity_ids(hass, entry) & set(new_entity_ids)):
        return {
            "error": (
                f"A group cannot contain itself ({', '.join(self_refs)}). Remove it from "
                "the member list."
            )
        }

    # A stored registry id whose entity has since been deleted cannot be
    # written back: the group platform runs the saved list through
    # er.async_validate_entity_ids on setup, which raises on an id that
    # resolves to nothing. The reload after the save then leaves the group
    # entity ``unavailable`` while the config entry still reports LOADED — so
    # neither async_reload's result nor entry.state reveals it, and a plain
    # rename would brick the group while returning ``status: updated``.
    # Refused with the exact string to drop, since it is the caller's only
    # handle on a member that no longer has an entity_id.
    if stale := [member for member in new_entity_ids if not valid_entity_id(member)]:
        listed = ", ".join(f"'{sanitize_untrusted_text(s, limit=60)}'" for s in stale[:5])
        return {
            "error": (
                f"This group still lists {len(stale)} member(s) whose entity no longer "
                f"exists in Home Assistant: {listed}. Home Assistant cannot load the "
                "group while they are there, so no edit can be saved. Remove them first "
                "with remove_entities, passing those exact strings."
            )
        }

    inferred, error = infer_group_type(new_entity_ids)
    if error:
        return {"error": error}
    # Same reason as creation: a text member is dropped from the aggregate
    # with only a log line, so adding one would report success on a member
    # that contributes nothing.
    if inferred == "sensor" and (offenders := _non_numeric_members(hass, new_entity_ids)):
        return {"error": _non_numeric_error(hass, offenders)}
    if inferred != group_type:
        return {
            "error": (
                f"This is a '{group_type}' group, but the resulting members form a "
                f"'{inferred}' group. A group helper cannot change domain — create a "
                "separate group for the other domain."
            )
        }

    if (
        clean_name is not None
        and clean_name.casefold() != str(options.get("name") or "").casefold()
    ):
        for other in group_entries(hass):
            if other.entry_id == entry.entry_id:
                continue
            existing = str(other.options.get("name") or other.title or "")
            if existing.casefold() == clean_name.casefold():
                return {
                    "error": (
                        f"Another group is already named "
                        f"'{sanitize_untrusted_text(clean_name)}'. Pick a different name."
                    )
                }

    # Compared resolved, or a replacement naming entity_ids against uuid-stored
    # members reads as "everything removed, everything added" — which would
    # unhide members the group still holds.
    kept = set(new_entity_ids)
    removed_members = [
        stored
        for stored, entity_id in zip(current, current_entity_ids, strict=True)
        if entity_id not in kept
    ]
    options["entities"] = new_members
    if clean_name is not None:
        options["name"] = clean_name
    if requires_all_members is not None:
        # Type support was already validated above, so this can't be dropped.
        options["all"] = bool(requires_all_members)

    title = str(options.get("name") or entry.title)
    hass.config_entries.async_update_entry(entry, options=options, title=title)

    # Group's async_setup_entry registers no update listener, and we bypass the
    # options flow (which would have reloaded), so the reload is ours to do —
    # without it the live entity keeps tracking the OLD member list.
    await hass.config_entries.async_reload(entry.entry_id)

    if options.get("hide_members"):
        _apply_member_visibility(hass, new_members, True)
    # Members dropped from a hidden group must become visible again, or they
    # vanish from the UI with no group left to explain why. Ones another hidden
    # group still claims stay hidden — see _members_free_to_unhide.
    if (
        removed_members
        and options.get("hide_members")
        and (releasable := _members_free_to_unhide(hass, removed_members, entry.entry_id))
    ):
        _apply_member_visibility(hass, releasable, False)

    entity_id = group_entity_id(hass, entry)
    _LOGGER.info(
        "Updated group '%s' (%s): %d members",
        title,
        entity_id or entry.entry_id,
        len(new_members),
    )
    # Reported resolved: the caller is an LLM that will quote these back to the
    # user, and a stored registry id means nothing to either of them.
    return {
        "status": "updated",
        "entry_id": entry.entry_id,
        "entity_id": entity_id,
        "name": title,
        "group_type": group_type,
        "members": new_entity_ids,
        "member_count": len(new_members),
        "added": [e for e in new_entity_ids if e not in set(current_entity_ids)],
        "removed": _resolve_members(hass, removed_members),
    }


def parent_groups(hass: HomeAssistant, entity_id: str | None) -> list[str]:
    """Group helpers that list *entity_id* as a member.

    Nesting one group inside another is supported, so a group can be a member
    like any other entity. HA's own ``groups_with_entity`` does not find these
    — it only walks the legacy ``group`` component's entities, not helper
    config entries — and a deleted member leaves the parent silently smaller
    rather than broken, so nothing else would surface it.
    """
    if not entity_id:
        return []
    parents: list[str] = []
    for entry in group_entries(hass):
        members = _resolve_members(hass, [str(e) for e in entry.options.get("entities", []) or []])
        if entity_id in members and (parent := group_entity_id(hass, entry)):
            parents.append(parent)
    return sorted(parents)


def scenes_with_entity(hass: HomeAssistant, entity_id: str | None) -> list[str]:
    """Scenes that set *entity_id*.

    Read off the live state because ``scene`` ships no ``scenes_with_entity``
    helper to match automation's and script's — a scene publishes the entities
    it applies as its ``entity_id`` attribute.
    """
    if not entity_id:
        return []
    scenes: list[str] = []
    for state in hass.states.async_all("scene"):
        members = state.attributes.get("entity_id") or []
        if isinstance(members, str):
            members = [members]
        if entity_id in members:
            scenes.append(state.entity_id)
    return sorted(scenes)


def group_dependents(hass: HomeAssistant, entity_id: str | None) -> dict[str, list[str]]:
    """Return automations/scripts/scenes/groups that reference *entity_id*.

    Deleting a group silently breaks anything targeting it, so the delete
    confirmation surfaces the blast radius up front.
    """
    if not entity_id:
        return {"automations": [], "scripts": [], "scenes": [], "groups": []}
    automations: list[str] = []
    scripts: list[str] = []
    try:
        from homeassistant.components.automation import (  # noqa: PLC0415
            automations_with_entity,
        )

        automations = list(automations_with_entity(hass, entity_id))
    except (ImportError, KeyError, AttributeError):
        # automation/script may not be set up; a missing blast-radius hint
        # must never block the delete path.
        automations = []
    try:
        from homeassistant.components.script import scripts_with_entity  # noqa: PLC0415

        scripts = list(scripts_with_entity(hass, entity_id))
    except (ImportError, KeyError, AttributeError):
        scripts = []
    return {
        "automations": automations,
        "scripts": scripts,
        "scenes": scenes_with_entity(hass, entity_id),
        "groups": parent_groups(hass, entity_id),
    }


def _hides_to_restore_after_delete(hass: HomeAssistant, entry: ConfigEntry) -> list[str]:
    """Members whose hide must survive deleting *entry*.

    ``group.async_remove_entry`` unhides every member the entry had hidden,
    without regard for other groups — so deleting one of two overlapping
    hidden groups would leave the survivor with a visible member. Computed
    before removal, because the entry's options are gone afterwards.

    Limited to members HA will actually clear: it skips anything not hidden by
    the integration, and re-applying an integration hide over a *user's* manual
    one would quietly downgrade it, making a later removal unhide what the user
    hid by hand.
    """
    if not entry.options.get("hide_members"):
        return []

    registry = er.async_get(hass)
    members = [str(e) for e in entry.options.get("entities", []) or []]
    free = set(_members_free_to_unhide(hass, members, entry.entry_id))
    restore: list[str] = []
    for member in members:
        if member in free:
            continue
        resolved = er.async_resolve_entity_id(registry, member)
        registry_entry = registry.async_get(resolved) if resolved else None
        if registry_entry is not None and (
            registry_entry.hidden_by == er.RegistryEntryHider.INTEGRATION
        ):
            restore.append(member)
    return restore


async def async_delete_group(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    """Remove a group-helper config entry.

    HA's ``async_remove_entry`` unhides any members the group had hidden; see
    :func:`_hides_to_restore_after_delete` for the overlapping-group exception.
    """
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        return {"error": f"No group helper with entry_id {sanitize_untrusted_text(entry_id)}"}
    if entry.domain != GROUP_DOMAIN:
        # Guard the confirm path: a stale or spoofed entry_id must never let a
        # delete_group confirmation remove some unrelated integration.
        return {"error": f"{sanitize_untrusted_text(entry_id)} is not a group helper"}

    name = str(entry.options.get("name") or entry.title or "")
    entity_id = group_entity_id(hass, entry)
    restore = _hides_to_restore_after_delete(hass, entry)
    unloaded = await hass.config_entries.async_remove(entry_id)
    if restore:
        # Re-applied after removal, since that is what cleared them.
        _apply_member_visibility(hass, restore, True)
    _LOGGER.info("Deleted group '%s' (%s)", name, entity_id or entry_id)
    return {
        "status": "deleted",
        "entry_id": entry_id,
        "entity_id": entity_id,
        "name": sanitize_untrusted_text(name),
        "require_restart": bool(unloaded.get("require_restart")),
    }
