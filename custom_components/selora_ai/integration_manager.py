"""List integrations, add one, and reload, enable, disable, remove or reconfigure one.

An integration here is a config entry: what Settings → Devices & services shows
as one card. The devices Home Assistant discovered but nobody set up yet are
listed with them, as that page's Discovered section shows them; one is added
by taking over its discovery flow, or ignored as that section's Ignore does. Everything goes through ``hass.config_entries``, as that page's
websocket commands do. Options are changed by driving the entry's own options
flow the way ``helper_flow`` drives a config flow: a call without ``options``
describes the form, a call with them submits it, so Home Assistant's own
validation decides.

Selora AI's own entry is refused for every change: disabling, removing or
reloading it would cut the connection the request came in on, and its options
hold the AI provider's credentials, which are never configured automatically.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlparse

from homeassistant.config_entries import (
    DISCOVERY_SOURCES,
    SOURCE_IGNORE,
    ConfigEntryDisabler,
    ConfigFlowContext,
    OperationNotAllowed,
    UnknownEntry,
)
from homeassistant.data_entry_flow import UnknownFlow
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from homeassistant.loader import (
    Integration,
    IntegrationNotFound,
    async_get_integration,
    async_get_integrations,
)
import voluptuous as vol

from .const import DOMAIN
from .helper_flow import _FLOW_ERRORS, is_credential_name
from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# States that mean the integration is not working.
_PROBLEM_STATES: Final = frozenset(
    {"setup_error", "setup_retry", "migration_error", "failed_unload"}
)


def _counts(hass: HomeAssistant, entry_id: str) -> tuple[int, int]:
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry_id)
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry_id)
    return len(devices), len(entities)


# The added card's tiles: enough for a hub or a bridge to show what it brought.
_ADDED_TILES: Final = 12


def _shown_entities(hass: HomeAssistant, entry_id: str) -> list[str]:
    """The entry's entities a dashboard would show: not disabled, hidden, or
    a diagnostic or configuration entity."""
    return [
        e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry_id)
        if not e.disabled_by and not e.hidden_by and e.entity_category is None
    ][:_ADDED_TILES]


def _only_device(hass: HomeAssistant, entry_id: str) -> dict[str, str]:
    """The entry's device when it has just one, for the added card to open:
    one device is what the user asked to set up. Otherwise the card opens
    the entry's own page, which lists them."""
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry_id)
    return {"device_id": devices[0].id} if len(devices) == 1 else {}


def _row(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    devices, entities = _counts(hass, entry.entry_id)
    row: dict[str, Any] = {
        "entry_id": entry.entry_id,
        "domain": entry.domain,
        # Titles are often set from the account or device they connect to.
        "title": sanitize_untrusted_text(entry.title or "", 80),
        "state": entry.state.value,
        "devices": devices,
        "entities": entities,
        "supports_options": entry.supports_options,
    }
    if entry.reason:
        row["reason"] = sanitize_untrusted_text(str(entry.reason), 200)
    if entry.disabled_by:
        row["disabled_by"] = str(entry.disabled_by.value)
    return row


def discovery_flows(hass: HomeAssistant, domain: str = "") -> list[dict[str, Any]]:
    """Flows Home Assistant started on its own and waits on the user to finish.

    ``DISCOVERY_SOURCES`` is core's own set: the one it fires
    ``config_entry_discovered`` and notifies flow subscribers for.
    """
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"].get("source") in DISCOVERY_SOURCES
        and flow["handler"] != DOMAIN
        and (not domain or flow["handler"] == domain)
    ]


# Integrations that speak a protocol many devices share, rather than a
# device's own: a TV found by DLNA and by its maker's integration is set up
# with the latter. Home Assistant's DLNA flow holds back the TVs it knows a
# better integration for (`_is_ignored_device`), by model name, which misses
# a Samsung whose model is a part number.
_GENERIC_DISCOVERY: Final = frozenset({"dlna_dmr", "dlna_dms", "upnp"})


def _device_key(unique_id: Any) -> str:
    """A discovery's unique id with UPnP's ``uuid:`` prefix dropped: Samsung's
    integration strips it from the same UDN DLNA keeps it on."""
    key = str(unique_id or "").strip().lower()
    return key.removeprefix("uuid:")


def _device_name(title: str) -> str:
    """A title without a leading ``[TV]``-style tag, for matching."""
    return re.sub(r"^(\[[^\]]*\]\s*)+", "", title).strip().casefold()


def _same_name(name: str, title: str) -> bool:
    """Whether a title names that device: the same name, or the same name
    with a model after it in brackets (``Samsung Q6 Series (82) (QN82Q6FNA)``).
    A name merely inside another (``Living Room`` in ``Living Room Speaker``)
    is another device."""
    other = _device_name(title)
    return bool(name) and (other == name or (other.startswith(f"{name} (") and other.endswith(")")))


def _found_better(row: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    """Whether a generic discovery's device was also found by its own
    integration: the same unique id, or that row's title naming it."""
    if row["domain"] not in _GENERIC_DISCOVERY:
        return False
    key, name = _device_key(row["unique_id"]), _device_name(row["title"])
    return any(
        other["domain"] not in _GENERIC_DISCOVERY
        and ((key and _device_key(other["unique_id"]) == key) or _same_name(name, other["title"]))
        for other in rows
    )


async def async_list_discovered(hass: HomeAssistant, domain: str = "") -> list[dict[str, Any]]:
    """Discovered devices, named as the Discovered cards name them: the
    integration's ``config.flow_title`` filled from the flow's
    ``title_placeholders``, else the integration's name.

    A device a generic protocol integration (DLNA) found is left out when its
    own integration found it too: asked to set up the TV, the user means the
    TV's integration. Its flow stays under Discovered, and its flow_id still
    works."""
    from .repairs_manager import _fill  # noqa: PLC0415

    flows = discovery_flows(hass)
    if not flows:
        return []
    domains = {flow["handler"] for flow in flows}
    integrations = await async_get_integrations(hass, domains)
    titles = await async_get_translations(hass, hass.config.language, "config", domains)
    rows = []
    for flow in flows:
        handler = flow["handler"]
        integration = integrations.get(handler)
        name = integration.name if isinstance(integration, Integration) else handler
        # Placeholders are what the device advertises about itself.
        title = _fill(
            titles.get(f"component.{handler}.config.flow_title", ""),
            flow["context"].get("title_placeholders"),
        )
        rows.append(
            {
                "flow_id": flow["flow_id"],
                "domain": handler,
                "integration": name,
                "title": sanitize_untrusted_text(title or name, 80),
                "source": flow["context"]["source"],
                "unique_id": flow["context"].get("unique_id"),
            }
        )
    # Judged against every discovery, so a domain asked for alone is too.
    shown = [
        {k: v for k, v in r.items() if k != "unique_id"}
        for r in rows
        if not _found_better(r, rows) and (not domain or r["domain"] == domain)
    ]
    shown.sort(key=lambda r: (r["domain"], r["title"]))
    return shown


async def async_ignore_discovered(hass: HomeAssistant, flow_id: str) -> dict[str, Any]:
    """Ignore a discovered device as its Discovered card's Ignore does.

    Home Assistant's own ``config_entries/ignore_flow``: an ``ignore`` flow for
    the device's unique id makes an ignored entry, which ends the discovery and
    stops it coming back. The user brings it back from the Ignored list.
    """
    flow_id = str(flow_id or "").strip()
    flow = next((f for f in discovery_flows(hass) if f["flow_id"] == flow_id), None)
    if flow is None:
        return {
            "error": (
                "No discovered device has that flow_id; list the discovered devices "
                "again, as it may have been set up or gone."
            )
        }
    if "unique_id" not in flow["context"]:
        return {
            "error": (
                "Home Assistant cannot ignore this one: its integration gives the "
                "device no unique id, so nothing would stop it being found again."
            )
        }
    row = next(
        r for r in await async_list_discovered(hass, flow["handler"]) if r["flow_id"] == flow_id
    )
    context = ConfigFlowContext(source=SOURCE_IGNORE)
    if "discovery_key" in flow["context"]:
        context["discovery_key"] = flow["context"]["discovery_key"]
    await hass.config_entries.flow.async_init(
        flow["handler"],
        context=context,
        data={"unique_id": flow["context"]["unique_id"], "title": row["title"]},
    )
    return {
        "status": "ignored",
        "domain": row["domain"],
        "title": row["title"],
        "hint": "Settings → Devices & services lists it under Ignored, where it can be brought back.",
    }


async def async_list_integrations(
    hass: HomeAssistant, domain: str | None = None, *, problems_only: bool = False
) -> dict[str, Any]:
    """Every config entry, or one domain's, or only those not working — and,
    unless only problems are asked for, what was discovered but not set up."""
    wanted = str(domain or "").strip().lower()
    rows = [
        _row(hass, entry)
        for entry in hass.config_entries.async_entries()
        if (not wanted or entry.domain == wanted)
        and (not problems_only or entry.state.value in _PROBLEM_STATES)
    ]
    rows.sort(key=lambda r: (r["domain"], r["title"]))
    result: dict[str, Any] = {
        "integrations": rows,
        "not_working": sum(1 for r in rows if r["state"] in _PROBLEM_STATES),
    }
    if not problems_only:
        result["discovered"] = await async_list_discovered(hass, wanted)
    return result


def _entry(hass: HomeAssistant, entry_id: str, verb: str) -> ConfigEntry | str:
    entry = hass.config_entries.async_get_entry(str(entry_id or "").strip())
    if entry is None:
        return (
            f"No integration with entry_id {sanitize_untrusted_text(str(entry_id), 40)}. "
            "Call list_integrations for it."
        )
    if entry.domain == DOMAIN:
        return f"Selora AI does not {verb} itself. Do it under Settings → Devices & services."
    return entry


async def async_reload_integration(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    """Reload an integration, and say what state it came back in."""
    entry = _entry(hass, entry_id, "reload")
    if isinstance(entry, str):
        return {"error": entry}
    if entry.disabled_by:
        return {"error": "That integration is disabled. Enable it instead."}
    try:
        reloaded = await hass.config_entries.async_reload(entry.entry_id)
    except (OperationNotAllowed, UnknownEntry, HomeAssistantError) as exc:
        return {"error": f"It could not be reloaded: {sanitize_untrusted_text(str(exc), 200)}"}
    return {"status": "reloaded" if reloaded else "not_reloaded", **_row(hass, entry)}


async def async_set_integration_enabled(
    hass: HomeAssistant, entry_id: str, enabled: bool
) -> dict[str, Any]:
    """Enable or disable an integration; its devices and entities follow."""
    entry = _entry(hass, entry_id, "enable or disable")
    if isinstance(entry, str):
        return {"error": entry}
    try:
        reloaded = await hass.config_entries.async_set_disabled_by(
            entry.entry_id, None if enabled else ConfigEntryDisabler.USER
        )
    except (OperationNotAllowed, UnknownEntry, HomeAssistantError) as exc:
        return {"error": f"It could not be changed: {sanitize_untrusted_text(str(exc), 200)}"}
    # Home Assistant's own command reads a failed unload or load the same way:
    # the setting is saved, and takes effect on the next restart.
    return {
        "status": "enabled" if enabled else "disabled",
        **({"require_restart": True} if not reloaded else {}),
        **_row(hass, entry),
    }


async def async_remove_integration(
    hass: HomeAssistant, entry_id: str, *, confirmed: bool = False
) -> dict[str, Any]:
    """Remove an integration with its devices and entities, once confirmed."""
    entry = _entry(hass, entry_id, "remove")
    if isinstance(entry, str):
        return {"error": entry}
    row = _row(hass, entry)
    if not confirmed:
        return {
            "requires_confirmation": True,
            "integration": row,
            "hint": (
                f"This removes {row['title'] or entry.domain} with its {row['devices']} "
                f"device(s) and {row['entities']} entit(ies); automations using them "
                "stop working. Tell the user, and only once they agree call again with "
                "confirmed=true."
            ),
        }
    try:
        # Shielded as Home Assistant's own endpoint does: a dropped request must
        # not leave the entry removed from memory but not from storage.
        result = await asyncio.shield(
            hass.async_create_task(
                hass.config_entries.async_remove(entry.entry_id),
                f"selora_ai remove {entry.domain} entry",
            )
        )
    except (OperationNotAllowed, UnknownEntry, HomeAssistantError) as exc:
        return {"error": f"It could not be removed: {sanitize_untrusted_text(str(exc), 200)}"}
    return {
        "status": "removed",
        "domain": entry.domain,
        "title": row["title"],
        "require_restart": bool(result.get("require_restart")),
    }


async def async_integration_options(
    hass: HomeAssistant,
    entry_id: str,
    kind: str | None,
    options: dict[str, Any] | None,
    flow_id: str | None = None,
) -> dict[str, Any]:
    """Describe the step an integration's options flow is on, or answer it.

    Held open between calls like a helper's setup (``flow_sessions``), so a
    multi-step options flow is walked one step per call by ``flow_id``.
    ``None`` describes; an empty mapping is a submission — some options forms
    take ``{}`` on purpose (clearing optional credentials).
    """
    from .flow_sessions import async_drive, async_step_texts  # noqa: PLC0415

    entry = _entry(hass, entry_id, "change the options of")
    if isinstance(entry, str):
        return {"error": entry}
    if not entry.supports_options:
        return {"error": f"{entry.domain} has no options to change."}

    manager = hass.config_entries.options
    outcome = await async_drive(
        hass,
        manager,
        owner=("options", entry.entry_id),
        start=lambda: manager.async_init(entry.entry_id),
        flow_id=flow_id,
        choice=kind,
        values=options,
        values_param="options",
        errors=_FLOW_ERRORS,
        texts=await async_step_texts(hass, entry.domain, "options"),
    )
    if "done" not in outcome:
        return {"entry_id": entry.entry_id, **outcome}
    await hass.async_block_till_done()
    return {"status": "saved", **_row(hass, entry)}


async def async_setup_card(hass: HomeAssistant, flow_id: str) -> dict[str, Any]:
    """The ``integration_setup`` client action for a setup in progress, or an
    error: what a link handed out over MCP opens in the chat."""
    try:
        flow = hass.config_entries.flow.async_get(flow_id)
    except UnknownFlow:
        return {"error": "That setup is no longer open."}
    domain = str(flow["handler"])
    try:
        name = (await async_get_integration(hass, domain)).name
    except IntegrationNotFound:
        name = domain
    steps = hass.data.get(DOMAIN, {}).get(_STEPS_KEY, {}).get(flow_id)
    return {
        "kind": "integration_setup",
        "flow_id": flow_id,
        "domain": domain,
        "label": sanitize_untrusted_text(name, 80),
        **({"steps": steps} if steps else {}),
    }


_LOGIN_HINT: Final = (
    "This step takes a login or a sign-in, which never goes through an "
    "assistant: the user fills it in on the setup card shown with this "
    "answer. Do not ask them for it."
)
_STEPS_HINT: Final = (
    "This step is the user's: it asks them to do something themselves (on the "
    "device or in its app), or whether to go on without it. Never answer it "
    "for them. Call again with this flow_id and `steps`: how to do what it "
    "asks on this very device, in order, one short plain-text action each (the "
    "app or menu, then the setting's name and the value to pick), as far as you "
    "know them. They are shown on a setup card with two buttons: Continue "
    "answers this step once it is done; Check again starts the setup over, for "
    "a change Home Assistant only checks when the setup starts (an access or "
    "pairing setting). End with which one to press."
)
_DEVICE_HINT: Final = (
    "This step is the user's, on the setup card shown with this answer, with "
    "the steps you gave. Do not repeat them; say in one sentence what the card "
    "is for."
)
# The steps written for a step handed over, by flow_id, for the card a link
# opens. Bounded: a setup abandoned mid-way leaves its steps behind.
_STEPS_KEY: Final = "_setup_steps"
_STEPS_KEPT: Final = 50
_STEPS_MAX: Final = 8


def _clean_steps(steps: list[Any]) -> list[str]:
    cleaned = (sanitize_untrusted_text(str(step), 200).strip() for step in steps)
    return [step for step in cleaned if step][:_STEPS_MAX]


def _keep_steps(hass: HomeAssistant, flow_id: str, steps: list[str]) -> None:
    kept: dict[str, list[str]] = hass.data.setdefault(DOMAIN, {}).setdefault(_STEPS_KEY, {})
    kept.pop(flow_id, None)
    kept[flow_id] = steps
    while len(kept) > _STEPS_KEPT:
        kept.pop(next(iter(kept)))


def _hand_over(
    domain: str, name: str, outcome: dict[str, Any], hint: str, steps: list[str]
) -> dict[str, Any]:
    """A step for the user to fill in themselves, on a card in the chat."""
    flow_id = str(outcome["flow_id"])
    return {
        "requires_approval": True,
        "status": "needs_user",
        "domain": domain,
        **{k: outcome[k] for k in ("flow_id", "step", "title", "description") if outcome.get(k)},
        "client_action": {
            "kind": "integration_setup",
            "flow_id": flow_id,
            "domain": domain,
            "label": sanitize_untrusted_text(name, 80),
            **({"steps": steps} if steps else {}),
        },
        "hint": hint,
    }


_CHOICE_HINT: Final = (
    "This step asks which one to set up, among what Home Assistant found (a "
    "device, an account, a network): the user picks it on the setup card shown "
    "with this answer. Never pick it for them. When the one they named is not "
    "offered, tell them: it may be in `configured`, already set up."
)


def _picks_found(schema: Any) -> bool:
    """Whether a step's form chooses among things found at runtime: its
    choices are names, not translated settings. Read from the form itself,
    as the model's description of it drops a selector's translation key."""
    from .helper_flow import _to_field_list  # noqa: PLC0415

    if schema is None:
        return False
    try:
        fields = _to_field_list(schema)
    except (TypeError, ValueError, vol.Invalid):
        return False
    return isinstance(fields, list) and _found_fields(fields)


def _found_fields(fields: list[Any]) -> bool:
    for field in fields:
        if not isinstance(field, dict):
            continue
        # A section's fields.
        if isinstance(field.get("schema"), list) and _found_fields(field["schema"]):
            return True
        # ``vol.In`` of names: each option its own label.
        if field.get("type") == "select" and _named_options(field.get("options")):
            return True
        # A select selector whose options are not translated by a key.
        selector = field.get("selector")
        config = selector.get("select") if isinstance(selector, dict) else None
        if isinstance(config, dict) and config.get("options") and not config.get("translation_key"):
            return True
    return False


def _named_options(options: Any) -> bool:
    return (
        isinstance(options, list)
        and bool(options)
        and all(isinstance(o, list | tuple) and len(o) == 2 and o[0] == o[1] for o in options)
    )


_KNOWN_HOSTS_MAX: Final = 30


def _asks_for_host(fields: list[dict[str, Any]]) -> bool:
    return any(f.get("name") == "host" or _asks_for_host(f.get("fields") or []) for f in fields)


def _known_hosts(hass: HomeAssistant) -> list[dict[str, str]]:
    """The addresses of devices already set up, by entry title: a device set up
    with one integration (DLNA) and asked for by another (its maker's) is at
    the same address. Only an entry's ``host``, or its ``url``'s host — never
    anything else it stores."""
    known: dict[str, dict[str, str]] = {}
    for entry in hass.config_entries.async_entries():
        if entry.source == SOURCE_IGNORE or entry.disabled_by:
            continue
        host = entry.data.get("host")
        if not isinstance(host, str) and isinstance(entry.data.get("url"), str):
            host = urlparse(entry.data["url"]).hostname
        if isinstance(host, str) and host and host not in known:
            known[host] = {
                "title": sanitize_untrusted_text(entry.title or entry.domain, 80),
                "host": sanitize_untrusted_text(host, 80),
            }
    return list(known.values())[:_KNOWN_HOSTS_MAX]


def _asks_for_credential(fields: list[dict[str, Any]]) -> bool:
    """Whether a described form has a credential field, in a section or not —
    ``_describe_serialized`` marks each with ``is_set``."""
    return any("is_set" in f or _asks_for_credential(f.get("fields") or []) for f in fields)


def _carries_credential(values: dict[str, Any]) -> bool:
    """Whether submitted values name a credential, in a section or not — for a
    form that could not be described, so was never checked."""
    return any(
        is_credential_name(str(key)) or (isinstance(value, dict) and _carries_credential(value))
        for key, value in values.items()
    )


async def async_add_integration(
    hass: HomeAssistant,
    domain: str,
    kind: str | None,
    fields: dict[str, Any] | None,
    flow_id: str | None = None,
    steps: list[Any] | None = None,
) -> dict[str, Any]:
    """Describe the step an integration's setup is on, or answer it.

    The setup is the integration's own config flow, started as Settings → Add
    integration starts it and walked one step per call by ``flow_id``
    (``flow_sessions``). A step that asks for a login or needs the browser (an
    account sign-in) is handed to the user, never answered here: the result
    carries an ``integration_setup`` client action, a card in the chat on
    which the user fills that step in. Logins never reach the model.

    A discovered device's ``flow_id`` (from the listing's ``discovered``) takes
    over the flow Home Assistant started for it, as pressing Add on its
    Discovered card does, so what the discovery found is kept. That flow is
    never aborted: a refused or abandoned one stays under Discovered.
    """
    from .flow_sessions import (  # noqa: PLC0415
        async_drive,
        async_step_texts,
        held_step,
        owner_of,
        release,
    )
    from .helper_flow import _DEDICATED  # noqa: PLC0415

    domain = str(domain or "").strip().lower()
    shown = sanitize_untrusted_text(domain, 40)
    if domain == DOMAIN:
        return {"error": "Selora AI is already set up; it is not added again."}
    try:
        integration = await async_get_integration(hass, domain)
    except IntegrationNotFound:
        return {
            "error": (
                f"No integration '{shown}' is installed. A custom one is installed with "
                "hacs_install first, then Home Assistant restarted."
            )
        }
    if integration.integration_type == "helper":
        tool = _DEDICATED.get(domain, "create_helper")
        return {"error": f"{shown} is a helper; use {tool}."}
    if not integration.config_flow:
        return {"error": f"{shown} has no setup form; it is set up in configuration.yaml."}

    manager = hass.config_entries.flow
    discovery = bool(flow_id) and any(
        flow["flow_id"] == flow_id for flow in discovery_flows(hass, domain)
    )
    adopt = discovery and owner_of(hass, str(flow_id)) is None
    if flow_id and not discovery and owner_of(hass, str(flow_id)) != ("integration", domain):
        # A flow_id made up, from another integration, or from a setup that
        # ended: answered with what is found now, each with its own domain
        # and flow_id, so the call is made again rather than given up on.
        return {
            "domain": domain,
            "error": (
                "No setup with that flow_id is in progress for this integration. "
                "When the device is in `discovered`, call again with its domain "
                "and flow_id from there; otherwise call again without flow_id."
            ),
            "discovered": [
                row
                for row in await async_list_discovered(hass)
                if owner_of(hass, row["flow_id"]) is None
            ],
        }
    withheld = bool(fields) and _carries_credential(fields or {})
    if withheld:
        # Never submitted: the step is described again, and goes to the card.
        fields = None

    # Which device to set up is the user's choice: an answer to a step choosing
    # among found devices is not sent, the step goes to the card instead: asked
    # for a device no longer offered (already set up), a model picks another.
    ours = (
        bool(flow_id) and not discovery and owner_of(hass, str(flow_id)) == ("integration", domain)
    )
    if ours and fields and _picks_found(held_step(hass, str(flow_id)).get("data_schema")):
        fields = None
    outcome = await async_drive(
        hass,
        manager,
        owner=("integration", domain),
        start=(
            # Home Assistant's own flow view reads a flow's step this way.
            (lambda: manager.async_configure(str(flow_id)))
            if adopt
            else (lambda: manager.async_init(domain, context={"source": "user"}))
        ),
        flow_id=None if adopt else flow_id,
        choice=kind,
        # A step is described before it is answered, so a login is refused
        # before one can be submitted: the call that starts the flow submits
        # nothing.
        values=fields if flow_id and not adopt else None,
        values_param="fields",
        errors=_FLOW_ERRORS,
        adopted=adopt,
        texts=await async_step_texts(hass, domain, "config"),
        hand_over=True,
    )
    if outcome.get("reason") == "already_in_progress" and not adopt:
        # Home Assistant is already setting this device up from its discovery,
        # and a second setup of it is refused: that one is continued instead,
        # as pressing Add on its Discovered card would.
        waiting = [
            row
            for row in await async_list_discovered(hass, domain)
            if owner_of(hass, row["flow_id"]) is None
        ]
        if len(waiting) == 1:
            continued = await async_add_integration(hass, domain, None, None, waiting[0]["flow_id"])
            return {
                **continued,
                "note": (
                    "Home Assistant was already setting this device up from its "
                    "discovery, so that setup was continued."
                ),
            }
        if waiting:
            return {
                "domain": domain,
                "status": "pick_discovered",
                "discovered": [{"flow_id": r["flow_id"], "title": r["title"]} for r in waiting],
                "hint": (
                    "Home Assistant is already setting these up from their discovery. "
                    "Call again with the flow_id of the one the user means."
                ),
            }
    login = outcome.get("status") == "needs_browser" or _asks_for_credential(
        outcome.get("fields") or []
    )
    # A step with nothing to fill in, reached by answering one, asks the user
    # to act: press the device's button, allow access in its app, or say
    # whether to go on without it. Confirming it for them skips a pairing they
    # could still allow, and a setup left with nothing paired fails. Home
    # Assistant only says what to allow, not where on the device, so the model
    # is asked for those steps first (the flow stays held), and the card shows
    # them.
    held = bool(flow_id) and not adopt
    confirming = outcome.get("status") == "needs_confirmation"
    written = _clean_steps(steps) if held and fields is None and steps is not None else None
    if held and confirming and fields is not None and not login:
        return {"domain": domain, **outcome, "status": "needs_steps", "hint": _STEPS_HINT}
    if login or (confirming and written is not None):
        # Let go, not aborted: the card continues this very flow.
        handed = str(outcome.get("flow_id"))
        release(hass, handed, abort=False)
        if written:
            _keep_steps(hass, handed, written)
        return _hand_over(
            domain,
            integration.name,
            outcome,
            _LOGIN_HINT if login else _DEVICE_HINT,
            written or [],
        )
    if not flow_id and "done" not in outcome and "error" not in outcome:
        # What the user may mean is already found, or already set up: a step
        # choosing among devices no longer offers one that is, and a model
        # left to guess picked another device instead.
        waiting = [
            {"flow_id": r["flow_id"], "title": r["title"]}
            for r in await async_list_discovered(hass, domain)
            if owner_of(hass, r["flow_id"]) is None
        ]
        configured = [
            {"entry_id": e.entry_id, "title": sanitize_untrusted_text(e.title or "", 80)}
            for e in hass.config_entries.async_entries(domain)
            if e.source != SOURCE_IGNORE
        ]
        if waiting:
            outcome = {
                **outcome,
                "discovered": waiting,
                "discovered_hint": (
                    "Home Assistant already found these: when the user means one, "
                    "call again with its flow_id alone, which continues its setup."
                ),
            }
        asks_host = outcome.get("status") == "needs_options" and _asks_for_host(
            outcome.get("fields") or []
        )
        if asks_host and (known := _known_hosts(hass)):
            outcome = {
                **outcome,
                "known_hosts": known,
                "known_hosts_hint": (
                    "Addresses of devices Home Assistant already set up. When the "
                    "device the user means is among them (by name), fill `host` "
                    "with its address instead of asking them for it."
                ),
            }
        if configured:
            outcome = {
                **outcome,
                "configured": configured,
                "configured_hint": (
                    "Already set up. When the user means one of these, say so and add nothing."
                ),
            }
    if (
        not discovery
        and not outcome.get("discovered")
        and outcome.get("status") == "needs_options"
        and _picks_found(held_step(hass, str(outcome.get("flow_id") or "")).get("data_schema"))
    ):
        handed = str(outcome.get("flow_id"))
        release(hass, handed, abort=False)
        return {
            **_hand_over(domain, integration.name, outcome, _CHOICE_HINT, []),
            **{k: outcome[k] for k in ("configured",) if k in outcome},
        }
    if "done" not in outcome:
        note = (
            {
                "note": "Those values held a login, so none were sent: a login never goes "
                "through an assistant."
            }
            if withheld
            else {}
        )
        return {"domain": domain, **outcome, **note}
    # The flow sets the entry up before it returns, so its state is final here.
    entry = outcome["done"]["result"]
    _LOGGER.info("Added %s integration '%s'", domain, entry.title)
    if discovery:
        from .telemetry import record_activity  # noqa: PLC0415

        record_activity(hass, "devices_paired")
    return {
        "status": "added",
        "name": sanitize_untrusted_text(integration.name, 80),
        **_row(hass, entry),
        "entity_ids": _shown_entities(hass, entry.entry_id),
        **_only_device(hass, entry.entry_id),
        "hint": (
            "Done: the chat shows it added, with a tick. Say so in one sentence; "
            "the steps it went through are over, so do not repeat what they said."
        ),
    }
