"""List Home Assistant's repairs, ignore one, or run its fix.

Repairs are the issue registry's entries — Settings → Repairs. Each names a
translation key, so its title and description are rendered here from the
integration's own translations in the home's language, the way the Repairs
page shows them. A fixable issue has a fix flow; it is driven like
``integration_manager`` drives an options flow: a call without ``fields``
describes the step (most are a single confirmation), a call with them submits
it, so the integration's own flow decides.

Issue text is written by integrations and may carry placeholders filled from
outside data (a device name, a URL), so it is bounded and normalized.
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
from typing import TYPE_CHECKING, Any, Final

from homeassistant.data_entry_flow import InvalidData, UnknownFlow
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations

from .const import DOMAIN
from .helper_flow import _FLOW_ERRORS, _describe_fields
from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_PLACEHOLDER: Final = re.compile(r"\{(\w+)\}")
_SEVERITY_ORDER: Final = {"critical": 0, "error": 1, "warning": 2}
# A fix left between steps this long is aborted.
_FLOW_TTL: Final = 15 * 60


def _fill(text: str, placeholders: dict[str, Any] | None) -> str:
    """Substitute ``{name}`` placeholders, leaving unknown ones as written."""
    values = placeholders or {}
    return _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), m.group(0))), text)


async def _texts(hass: HomeAssistant, issues: list[ir.IssueEntry]) -> dict[str, str]:
    # The creator's catalog holds the text; ``issue_domain`` only names the
    # integration the issue is about.
    domains = {issue.domain for issue in issues if issue.translation_key}
    if not domains:
        return {}
    return await async_get_translations(hass, hass.config.language, "issues", domains)


def _row(issue: ir.IssueEntry, texts: dict[str, str]) -> dict[str, Any]:
    base = f"component.{issue.domain}.issues.{issue.translation_key}"
    title = texts.get(f"{base}.title") or issue.translation_key or issue.issue_id
    description = texts.get(f"{base}.description") or texts.get(
        f"{base}.fix_flow.step.init.description", ""
    )
    placeholders = issue.translation_placeholders
    row: dict[str, Any] = {
        "domain": issue.domain,
        "issue_id": issue.issue_id,
        "severity": str(issue.severity) if issue.severity else None,
        "title": sanitize_untrusted_text(_fill(title, placeholders), 200),
        "fixable": bool(issue.is_fixable),
    }
    if description:
        row["description"] = sanitize_untrusted_text(_fill(description, placeholders), 800)
    if issue.learn_more_url:
        row["learn_more_url"] = sanitize_untrusted_text(issue.learn_more_url, 300)
    if issue.breaks_in_ha_version:
        row["breaks_in_ha_version"] = issue.breaks_in_ha_version
    if issue.dismissed_version:
        row["ignored"] = True
    return row


async def async_list_repairs(
    hass: HomeAssistant, *, include_ignored: bool = False
) -> dict[str, Any]:
    """Open repairs, most severe first, with their text."""
    issues = [
        issue
        for issue in ir.async_get(hass).issues.values()
        if issue.active and (include_ignored or not issue.dismissed_version)
    ]
    texts = await _texts(hass, issues)
    rows = [_row(issue, texts) for issue in issues]
    rows.sort(key=lambda r: (_SEVERITY_ORDER.get(r["severity"] or "", 3), r["domain"]))
    return {"repairs": rows}


def _issue(hass: HomeAssistant, domain: str, issue_id: str) -> ir.IssueEntry | str:
    issue = ir.async_get(hass).async_get_issue(str(domain or ""), str(issue_id or ""))
    if issue is None or not issue.active:
        return (
            f"No open repair {sanitize_untrusted_text(f'{domain}/{issue_id}', 80)}. Call "
            "list_repairs for its domain and issue_id."
        )
    return issue


def async_ignore_repair(
    hass: HomeAssistant, domain: str, issue_id: str, *, ignore: bool = True
) -> dict[str, Any]:
    """Ignore a repair (until its integration raises it again), or show it again."""
    issue = _issue(hass, domain, issue_id)
    if isinstance(issue, str):
        return {"error": issue}
    ir.async_ignore_issue(hass, issue.domain, issue.issue_id, ignore)
    return {
        "status": "ignored" if ignore else "shown",
        "domain": issue.domain,
        "issue_id": issue.issue_id,
    }


def _open_flows(hass: HomeAssistant) -> dict[str, tuple[str, str, float]]:
    """Fix flows this tool started and has not finished:
    flow_id → (domain, issue_id, last step at).

    A flow continues across calls, so it stays open between them; only these
    may be continued, only for the repair they were started for, and one left
    alone past ``_FLOW_TTL`` since its last step is aborted.
    """
    return hass.data.setdefault(DOMAIN, {}).setdefault("_repair_fix_flows", {})


def _prune(hass: HomeAssistant, manager: Any) -> None:
    flows = _open_flows(hass)
    now = time.monotonic()
    for flow_id, (_domain, _issue_id, last_step) in list(flows.items()):
        if now - last_step > _FLOW_TTL:
            flows.pop(flow_id, None)
            with contextlib.suppress(UnknownFlow):
                manager.async_abort(flow_id)


def _step(issue: ir.IssueEntry, texts: dict[str, str], result: dict[str, Any]) -> dict[str, Any]:
    """The current step of a fix flow, with its own title and description."""
    step_id = result.get("step_id") or "init"
    base = f"component.{issue.domain}.issues.{issue.translation_key}.fix_flow.step.{step_id}"
    placeholders = {
        **(issue.translation_placeholders or {}),
        **(result.get("description_placeholders") or {}),
    }
    step: dict[str, Any] = {"flow_id": result.get("flow_id"), "step": step_id}
    if title := texts.get(f"{base}.title"):
        step["title"] = sanitize_untrusted_text(_fill(title, placeholders), 200)
    if description := texts.get(f"{base}.description"):
        step["description"] = sanitize_untrusted_text(_fill(description, placeholders), 800)
    kind = result.get("type")
    if kind == "menu":
        step["status"] = "needs_choice"
        step["choices"] = [str(o) for o in (result.get("menu_options") or [])]
        step["hint"] = "Call again with this flow_id and `choice` set to one of these."
    elif kind == "external":
        step["status"] = "needs_browser"
        step["url"] = sanitize_untrusted_text(str(result.get("url") or ""), 300)
        step["hint"] = (
            "This step happens in a browser: give the user the url. Finish it in Settings → Repairs."
        )
    else:
        fields = _describe_fields(result.get("data_schema"))
        step["status"] = "needs_input" if fields else "needs_confirmation"
        step["fields"] = fields
        if result.get("errors"):
            step["errors"] = result["errors"]
        step["hint"] = (
            "Tell the user what this step does, and only once they agree call again with "
            "this flow_id and `fields` ({} when there are none)."
        )
    return step


async def async_fix_repair(
    hass: HomeAssistant,
    domain: str,
    issue_id: str,
    *,
    flow_id: str | None = None,
    fields: dict[str, Any] | None = None,
    choice: str | None = None,
) -> dict[str, Any]:
    """Start a repair's fix and describe its first step, or answer the step of
    one already started (``flow_id``) with ``fields`` or a menu ``choice``."""
    from homeassistant.components.repairs import repairs_flow_manager  # noqa: PLC0415

    issue = _issue(hass, domain, issue_id)
    if isinstance(issue, str):
        return {"error": issue}
    if not issue.is_fixable:
        return {"error": "This repair has no automatic fix; its description says what to do."}
    manager = repairs_flow_manager(hass)
    if manager is None:
        return {"error": "Repairs are not set up in this Home Assistant."}
    _prune(hass, manager)
    flows = _open_flows(hass)
    texts = await _texts(hass, [issue])

    try:
        if flow_id is None:
            result = await manager.async_init(issue.domain, data={"issue_id": issue.issue_id})
        else:
            started_for = flows.get(flow_id)
            if started_for is None:
                return {
                    "error": (
                        "That fix is not one in progress here (or it timed out). Start "
                        "again without flow_id."
                    )
                }
            if started_for[:2] != (issue.domain, issue.issue_id):
                return {
                    "error": (
                        f"That flow_id fixes {started_for[0]}/{started_for[1]}, not this repair."
                    )
                }
            if choice:
                result = await manager.async_configure(flow_id, {"next_step_id": choice})
            elif fields is not None:
                result = await manager.async_configure(flow_id, fields)
            else:
                return {"error": "Pass `fields` for this step, or `choice` for a menu."}
    except InvalidData as exc:
        return {"error": f"The fix rejected that input: {exc.schema_errors or exc}"}
    except _FLOW_ERRORS as exc:
        _LOGGER.warning("%s repair flow failed: %s", issue.domain, exc)
        if flow_id:
            flows.pop(flow_id, None)
        return {"error": f"The fix failed: {sanitize_untrusted_text(str(exc), 200)}"}

    kind = result.get("type")
    if kind in ("form", "menu", "external"):
        # Pending again: (re)start its inactivity clock.
        if result.get("flow_id"):
            flows[result["flow_id"]] = (issue.domain, issue.issue_id, time.monotonic())
        return {"domain": issue.domain, "issue_id": issue.issue_id, **_step(issue, texts, result)}
    flows.pop(result.get("flow_id") or flow_id or "", None)
    if kind == "create_entry":
        return {"status": "fixed", "domain": issue.domain, "issue_id": issue.issue_id}
    reason = result.get("reason") or kind
    return {"error": f"The fix did not complete ({sanitize_untrusted_text(str(reason))})."}
