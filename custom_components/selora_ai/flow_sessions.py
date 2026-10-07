"""Home Assistant flows held open between tool calls.

A helper's setup, an integration's options and a repair's fix are flows that
can run several forms and menus, and a tool call answers one of them. So a
flow stays open between calls and is continued by its ``flow_id``:

* **Only flows started here can be continued**, and only for what they were
  started for — an owner tuple such as ``("helper", "template")``. A caller
  holding some other flow_id (the user's own flow in the UI, a discovery)
  is refused.
* **One left alone is aborted.** Each step re-arms a timer
  (``async_call_later``, never a task sleeping out the delay); when it fires,
  the flow is aborted, or it would linger in progress. Unloading the
  integration aborts every open flow and cancels the timers.
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
    from collections.abc import Awaitable, Callable

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# A flow left between steps this long is aborted.
FLOW_TTL: Final = 15 * 60
_KEY: Final = "_open_flows"


@dataclass
class _OpenFlow:
    manager: Any
    owner: tuple[str, ...]
    cancel_timer: Callable[[], None]
    # The step the flow is waiting on, as the manager last returned it.
    step: dict[str, Any]


def _flows(hass: HomeAssistant) -> dict[str, _OpenFlow]:
    return hass.data.setdefault(DOMAIN, {}).setdefault(_KEY, {})


def owner_of(hass: HomeAssistant, flow_id: str) -> tuple[str, ...] | None:
    """What an open flow was started for, or None if it is not one of ours."""
    held = _flows(hass).get(flow_id)
    return held.owner if held else None


@callback
def keep_open(
    hass: HomeAssistant,
    manager: Any,
    flow_id: str,
    owner: tuple[str, ...],
    step: dict[str, Any] | None = None,
) -> None:
    """Hold *flow_id* open for its next step, (re)starting its timer."""
    flows = _flows(hass)
    if previous := flows.get(flow_id):
        previous.cancel_timer()

    @callback
    def _expire(_now: Any) -> None:
        if (held := flows.get(flow_id)) is not None and held.owner == owner:
            flows.pop(flow_id, None)
            with contextlib.suppress(UnknownFlow):
                manager.async_abort(flow_id)

    # Cancelled on shutdown: a stopping Home Assistant drops every flow anyway.
    expiry = HassJob(_expire, "selora_ai flow expiry", cancel_on_shutdown=True)
    flows[flow_id] = _OpenFlow(
        manager, owner, async_call_later(hass, FLOW_TTL, expiry), dict(step or {})
    )


@callback
def release(hass: HomeAssistant, flow_id: str | None, *, abort: bool) -> None:
    """Stop holding *flow_id*; abort it unless it already finished."""
    if not flow_id:
        return
    held = _flows(hass).pop(flow_id, None)
    if held is not None:
        held.cancel_timer()
        if abort:
            with contextlib.suppress(UnknownFlow):
                held.manager.async_abort(flow_id)


@callback
def async_close_all(hass: HomeAssistant) -> None:
    """Abort every open flow and cancel its timer — on unload."""
    for flow_id in list(_flows(hass)):
        release(hass, flow_id, abort=True)


# ── Walking a flow across calls ────────────────────────────────────────────


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
) -> dict[str, Any]:
    """Start a flow or continue an open one by one step, and say where it is.

    Without ``flow_id`` the flow is started, a menu answered with ``choice``
    and, when ``values`` are given, the form submitted — one call for a flow
    of one form. With it, the open flow's current step is answered: a menu by
    ``choice``, a form by ``values``; neither describes the step again.

    Returns ``{"done": result}`` once the flow created its entry, or the
    response to give: the next menu or form (with ``flow_id``), or an error.
    """
    from .helper_flow import _describe_fields  # noqa: PLC0415

    def _waiting(result: dict[str, Any]) -> dict[str, Any]:
        current = str(result.get("flow_id") or flow_id)
        keep_open(hass, manager, current, owner, result)
        step = {"flow_id": current, "step": result.get("step_id")}
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
            response["errors"] = result["errors"]
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
                keep_open(hass, manager, str(flow_id), owner, result)
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
        if flow_id:
            with contextlib.suppress(UnknownFlow):
                manager.async_abort(flow_id)
        return {"error": f"Home Assistant refused it: {sanitize_untrusted_text(str(exc), 200)}"}

    kind = result.get("type")
    if kind in ("form", "menu"):
        return _waiting(result)
    if kind == "create_entry":
        release(hass, flow_id, abort=False)
        return {"done": result}
    release(hass, flow_id, abort=True)
    if kind in ("external", "progress", "external_step", "show_progress"):
        return {"error": "This step needs the browser; finish it in Settings."}
    reason = result.get("reason") or kind
    return {"error": f"It was not completed ({sanitize_untrusted_text(str(reason))})."}
