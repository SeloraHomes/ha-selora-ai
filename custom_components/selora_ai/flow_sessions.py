"""Home Assistant flows held open between tool calls.

A helper's setup, an integration's options and a repair's fix are flows that
can run several forms and menus, and a tool call answers one of them. So a
flow stays open between calls and is continued by its ``flow_id``:

* **Only flows started here can be continued**, and only for what they were
  started for — an owner tuple such as ``("helper", "template")``. A caller
  holding some other flow_id (the user's own flow in the UI) is refused.
* **A discovery can be adopted** (``adopted=True``): a flow Home Assistant
  started from mDNS, SSDP, DHCP, Bluetooth… and lists under Discovered. It is
  continued here as a started one is, but never aborted, only let go — so a
  setup refused or left alone stays on that list for the user to finish.
* **One left alone is aborted.** Each step re-arms a timer
  (``async_call_later``, never a task sleeping out the delay); when it fires,
  the flow is aborted, or it would linger in progress. Unloading the
  integration aborts every open flow and cancels the timers.
* **One handed to the user is still ours to end** (``hand_off``). The card
  posts to Home Assistant's own flow endpoint, so no step reaches us again:
  one timer, longer than a step's, aborts it unless it finished, and declining
  the card aborts it at once (``drop_handed``). Left in progress, a second
  setup of the same device is refused as ``already_in_progress``. Check again
  on the card restarts it here too (``async_restart_handed``), so the new flow
  is handed off in its place and the card's flow_id still reaches it.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.core import HassJob, callback
from homeassistant.data_entry_flow import InvalidData, UnknownFlow
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN
from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# A flow left between steps this long is aborted.
FLOW_TTL: Final = 15 * 60
# A flow handed to the user on a card is aborted this long after, unless it
# finished: time to fetch a password or press a button on the device.
HANDED_TTL: Final = 60 * 60
_KEY: Final = "_open_flows"
_HANDED_KEY: Final = "_handed_flows"
_ALIAS_KEY: Final = "_handed_flow_aliases"


@dataclass
class _OpenFlow:
    manager: Any
    owner: tuple[str, ...]
    cancel_timer: Callable[[], None]
    # The step the flow is waiting on, as the manager last returned it.
    step: dict[str, Any]
    # A discovery Home Assistant started: let go, never aborted.
    adopted: bool = False


def _flows(hass: HomeAssistant) -> dict[str, _OpenFlow]:
    return hass.data.setdefault(DOMAIN, {}).setdefault(_KEY, {})


def owner_of(hass: HomeAssistant, flow_id: str) -> tuple[str, ...] | None:
    """What an open flow was started for, or None if it is not one of ours."""
    held = _flows(hass).get(flow_id)
    return held.owner if held else None


def held_step(hass: HomeAssistant, flow_id: str) -> dict[str, Any]:
    """The step an open flow of ours waits on, as Home Assistant returned it."""
    held = _flows(hass).get(flow_id)
    return dict(held.step) if held else {}


@callback
def keep_open(
    hass: HomeAssistant,
    manager: Any,
    flow_id: str,
    owner: tuple[str, ...],
    step: dict[str, Any] | None = None,
    *,
    adopted: bool = False,
) -> None:
    """Hold *flow_id* open for its next step, (re)starting its timer."""
    flows = _flows(hass)
    if previous := flows.get(flow_id):
        previous.cancel_timer()

    @callback
    def _expire(_now: Any) -> None:
        if (held := flows.get(flow_id)) is not None and held.owner == owner:
            flows.pop(flow_id, None)
            if not held.adopted:
                with contextlib.suppress(UnknownFlow):
                    manager.async_abort(flow_id)

    # Cancelled on shutdown: a stopping Home Assistant drops every flow anyway.
    expiry = HassJob(_expire, "selora_ai flow expiry", cancel_on_shutdown=True)
    flows[flow_id] = _OpenFlow(
        manager, owner, async_call_later(hass, FLOW_TTL, expiry), dict(step or {}), adopted
    )


@callback
def release(hass: HomeAssistant, flow_id: str | None, *, abort: bool) -> None:
    """Stop holding *flow_id*; abort it unless it already finished or was adopted."""
    if not flow_id:
        return
    held = _flows(hass).pop(flow_id, None)
    if held is not None:
        held.cancel_timer()
        if abort and not held.adopted:
            with contextlib.suppress(UnknownFlow):
                held.manager.async_abort(flow_id)


@dataclass
class _HandedFlow:
    manager: Any
    cancel_timer: Callable[[], None]
    # The integration, so Check again can start it afresh.
    handler: str
    adopted: bool


def _handed(hass: HomeAssistant) -> dict[str, _HandedFlow]:
    return hass.data.setdefault(DOMAIN, {}).setdefault(_HANDED_KEY, {})


def _aliases(hass: HomeAssistant) -> dict[str, str]:
    """A card's flow_id → the flow Check again started in its place."""
    return hass.data.setdefault(DOMAIN, {}).setdefault(_ALIAS_KEY, {})


@callback
def hand_off(hass: HomeAssistant, manager: Any, flow_id: str, *, adopted: bool) -> None:
    """Let the user finish *flow_id* on a card, aborting it if left alone.

    A discovery is never aborted: it goes back to the Discovered list.
    """
    if (held := _flows(hass).get(flow_id)) is not None:
        adopted = adopted or held.adopted
    release(hass, flow_id, abort=False)
    handed = _handed(hass)
    if previous := handed.pop(flow_id, None):
        previous.cancel_timer()
    try:
        handler = str(manager.async_get(flow_id).get("handler") or "")
    except UnknownFlow:
        return

    @callback
    def _expire(_now: Any) -> None:
        if (held := handed.pop(flow_id, None)) is None:
            return
        aliases = _aliases(hass)
        for card in [card for card, current in aliases.items() if current == flow_id]:
            del aliases[card]
        if not held.adopted:
            with contextlib.suppress(UnknownFlow):
                manager.async_abort(flow_id)

    expiry = HassJob(_expire, "selora_ai handed flow expiry", cancel_on_shutdown=True)
    handed[flow_id] = _HandedFlow(
        manager, async_call_later(hass, HANDED_TTL, expiry), handler, adopted
    )


@callback
def drop_handed(hass: HomeAssistant, flow_id: str) -> None:
    """End a flow handed to the user, as closing Home Assistant's dialog does.

    *flow_id* is the card's: the flow Check again started in its place is the
    one ended. Only a flow handed off here, and never a discovery: any other
    flow_id is left alone.
    """
    flow_id = _aliases(hass).pop(flow_id, flow_id)
    if (held := _handed(hass).pop(flow_id, None)) is None:
        return
    held.cancel_timer()
    if not held.adopted:
        with contextlib.suppress(UnknownFlow):
            held.manager.async_abort(flow_id)


async def async_restart_handed(hass: HomeAssistant, card_flow_id: str) -> dict[str, Any]:
    """Check again: end the card's setup and start its integration afresh.

    The new flow is handed off as the first was, so declining the card or
    leaving it ends it too; started from the browser, nothing here would know
    of it and it would stay in progress. A discovery the user restarts is
    ended as well: they asked to start over, as Home Assistant's dialog does.
    """
    from homeassistant.config_entries import SOURCE_USER  # noqa: PLC0415
    from homeassistant.exceptions import HomeAssistantError  # noqa: PLC0415

    aliases = _aliases(hass)
    current = aliases.pop(card_flow_id, card_flow_id)
    held = _handed(hass).pop(current, None)
    if held is None or not held.handler:
        return {"error": "That setup is no longer open."}
    held.cancel_timer()
    with contextlib.suppress(UnknownFlow):
        held.manager.async_abort(current)
    try:
        step = await held.manager.async_init(
            held.handler, context={"source": SOURCE_USER, "show_advanced_options": False}
        )
    except HomeAssistantError as exc:
        return {"error": sanitize_untrusted_text(str(exc), 200)}
    if step.get("type") == "abort":
        return {
            "reason": str(step.get("reason") or step.get("type") or ""),
            "description_placeholders": dict(step.get("description_placeholders") or {}),
        }
    new_id = str(step["flow_id"])
    hand_off(hass, held.manager, new_id, adopted=False)
    aliases[card_flow_id] = new_id
    return {"flow_id": new_id}


@callback
def async_close_all(hass: HomeAssistant) -> None:
    """Abort every open or handed flow and cancel its timer — on unload."""
    for flow_id in list(_flows(hass)):
        release(hass, flow_id, abort=True)
    _aliases(hass).clear()
    for flow_id in list(_handed(hass)):
        drop_handed(hass, flow_id)


# ── Walking a flow across calls ────────────────────────────────────────────


# A text from the flow's integration catalog by key under its category
# ("step.<id>.description", "error.<key>", "abort.<reason>"), placeholders
# filled; empty when the integration has none.
type StepTexts = Callable[[str, Mapping[str, Any] | None], str]


async def async_step_texts(hass: HomeAssistant, domain: str, category: str) -> StepTexts:
    """What Home Assistant's dialog would say at each step of *domain*'s
    ``config`` or ``options`` flow: the integration's own translations, so a
    step that only explains itself (no fields) is not a bare step id."""
    from homeassistant.helpers.translation import async_get_translations  # noqa: PLC0415

    from .repairs_manager import _fill  # noqa: PLC0415

    catalog = await async_get_translations(hass, hass.config.language, category, [domain])
    prefix = f"component.{domain}.{category}."

    def text(key: str, placeholders: Mapping[str, Any] | None) -> str:
        # Placeholders are what the device or service reported about itself.
        values = {k: sanitize_untrusted_text(v, 80) for k, v in (placeholders or {}).items()}
        return sanitize_untrusted_text(_fill(catalog.get(prefix + key, ""), values), 800)

    return text


async def async_drive(
    hass: HomeAssistant,
    manager: Any,
    *,
    owner: tuple[str, ...],
    start: Callable[[], Awaitable[dict[str, Any]]],
    flow_id: str | None,
    choice: str | None,
    values: dict[str, Any] | None,
    values_param: str,
    errors: tuple[type[Exception], ...],
    adopted: bool = False,
    texts: StepTexts | None = None,
    hand_over: bool = False,
) -> dict[str, Any]:
    """Start a flow or continue an open one by one step, and say where it is.

    Without ``flow_id`` the flow is started, a menu answered with ``choice``
    and, when ``values`` are given, the form submitted — one call for a flow
    of one form. With it, the open flow's current step is answered: a menu by
    ``choice``, a form by ``values``; neither describes the step again.

    With ``adopted``, ``start`` takes over a discovery rather than starting a
    flow (Home Assistant's own flow view reads its step the same way, with
    ``async_configure(flow_id)``), and nothing here ever aborts it.

    With ``texts``, a step carries the words Home Assistant's dialog shows for
    it (title, description, errors), and an abort its reason's sentence.

    With ``hand_over``, a step that needs the browser (an account sign-in) is
    let go rather than aborted, as ``{"status": "needs_browser", "flow_id"}``:
    the caller gives it to the user to finish.

    Returns ``{"done": result}`` once the flow created its entry, or the
    response to give: the next menu or form (with ``flow_id``), or an error.
    """
    from .helper_flow import _describe_fields  # noqa: PLC0415

    def _waiting(result: dict[str, Any]) -> dict[str, Any]:
        current = str(result.get("flow_id") or flow_id)
        keep_open(hass, manager, current, owner, result, adopted=adopted)
        step: dict[str, Any] = {"flow_id": current, "step": result.get("step_id")}
        placeholders = result.get("description_placeholders")
        if texts:
            for part in ("title", "description"):
                if said := texts(f"step.{result.get('step_id')}.{part}", placeholders):
                    step[part] = said
        if result.get("type") == "menu":
            return {
                "status": "needs_type",
                **step,
                "types": [str(o) for o in (result.get("menu_options") or [])],
                "hint": "Call again with this flow_id and `type` set to one of these.",
            }
        fields = _describe_fields(result.get("data_schema"))
        response = {
            "status": "needs_options" if fields else "needs_confirmation",
            **step,
            "fields": fields,
            "hint": (
                f"Call again with this flow_id and `{values_param}` holding these fields."
                if fields
                else f"Call again with this flow_id and `{values_param}` {{}} to confirm."
            ),
        }
        if result.get("errors"):
            response["errors"] = {
                field: (texts and texts(f"error.{key}", placeholders)) or key
                for field, key in result["errors"].items()
            }
        return response

    try:
        if flow_id:
            if owner_of(hass, flow_id) != owner:
                return {
                    "error": (
                        "That flow_id is not a flow in progress here for this — it may "
                        "have timed out. Start again without flow_id."
                    )
                }
            held = _flows(hass)[flow_id]
            adopted = adopted or held.adopted
            if held.step.get("type") == "menu":
                if not choice or choice not in (held.step.get("menu_options") or []):
                    return _waiting(held.step)
                result = await manager.async_configure(flow_id, {"next_step_id": choice})
            elif values is None:
                return _waiting(held.step)
            else:
                result = await manager.async_configure(flow_id, values)
        else:
            result = await start()
            flow_id = result.get("flow_id")
            if result.get("type") == "menu" and choice in (result.get("menu_options") or []):
                result = await manager.async_configure(flow_id, {"next_step_id": choice})
            if result.get("type") == "form" and values is not None:
                keep_open(hass, manager, str(flow_id), owner, result, adopted=adopted)
                result = await manager.async_configure(flow_id, values)
    except InvalidData as exc:
        # The form is still waiting: the same flow takes a corrected answer.
        held_step = _flows(hass)[flow_id].step if owner_of(hass, flow_id or "") else {}
        return {
            "error": f"Home Assistant rejected those values: {exc.schema_errors or exc}",
            **({**_waiting(held_step), "status": "rejected"} if held_step else {}),
        }
    except errors as exc:
        _LOGGER.warning("%s flow failed: %s", owner[0], exc)
        release(hass, flow_id, abort=True)
        # A flow that failed before it was held — on the first call's menu
        # step — is not released above, and Home Assistant leaves a flow whose
        # step raised in progress: aborted directly, or it lingers.
        if flow_id and not adopted:
            with contextlib.suppress(UnknownFlow):
                manager.async_abort(flow_id)
        return {"error": f"Home Assistant refused it: {sanitize_untrusted_text(str(exc), 200)}"}

    kind = result.get("type")
    if kind in ("form", "menu"):
        return _waiting(result)
    if kind == "create_entry":
        release(hass, flow_id, abort=False)
        return {"done": result}
    browser = kind in ("external", "progress", "external_step", "show_progress")
    if browser and hand_over and flow_id:
        hand_off(hass, manager, str(flow_id), adopted=adopted)
        return {"status": "needs_browser", "flow_id": str(flow_id), "step": result.get("step_id")}
    release(hass, flow_id, abort=True)
    # One refused at its first step was never held, so release left it open;
    # one that aborted itself is already gone.
    if flow_id and not adopted:
        with contextlib.suppress(UnknownFlow):
            manager.async_abort(flow_id)
    if browser:
        return {"error": "This step needs the browser; finish it in Settings."}
    reason = str(result.get("reason") or kind)
    said = texts and texts(f"abort.{reason}", result.get("description_placeholders"))
    return {
        "error": f"It was not completed ({sanitize_untrusted_text(reason)})."
        + (f" Home Assistant says: {said}" if said else ""),
        "reason": sanitize_untrusted_text(reason, 60),
    }
