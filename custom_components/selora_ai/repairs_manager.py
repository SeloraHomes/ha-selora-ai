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
from typing import TYPE_CHECKING, Any, Final

from homeassistant.data_entry_flow import InvalidData, UnknownFlow
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations

from .flow_sessions import keep_open, owner_of, release
from .helper_flow import _FLOW_ERRORS, _describe_fields
from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_PLACEHOLDER: Final = re.compile(r"\{(\w+)\}")
_SEVERITY_ORDER: Final = {"critical": 0, "error": 1, "warning": 2}


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
    texts = await _texts(hass, [issue])

    try:
        if flow_id is None:
            result = await manager.async_init(issue.domain, data={"issue_id": issue.issue_id})
        else:
            started_for = owner_of(hass, flow_id)
            if started_for is None or started_for[0] != "repair":
                return {
                    "error": (
                        "That fix is not one in progress here (or it timed out). Start "
                        "again without flow_id."
                    )
                }
            if started_for[1:] != (issue.domain, issue.issue_id):
                return {
                    "error": (
                        f"That flow_id fixes {started_for[1]}/{started_for[2]}, not this repair."
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
        release(hass, flow_id, abort=True)
        return {"error": f"The fix failed: {sanitize_untrusted_text(str(exc), 200)}"}

    kind = result.get("type")
    if kind in ("form", "menu", "external"):
        # Pending again: (re)start its inactivity clock.
        if result.get("flow_id"):
            keep_open(
                hass, manager, result["flow_id"], ("repair", issue.domain, issue.issue_id), result
            )
        return {"domain": issue.domain, "issue_id": issue.issue_id, **_step(issue, texts, result)}
    release(hass, result.get("flow_id") or flow_id, abort=kind != "create_entry")
    if kind == "create_entry":
        return {"status": "fixed", "domain": issue.domain, "issue_id": issue.issue_id}
    reason = result.get("reason") or kind
    return {"error": f"The fix did not complete ({sanitize_untrusted_text(str(reason))})."}


# ── Chat: a fix behind a confirmation card ────────────────────────────────


def _fingerprint(issue: ir.IssueEntry) -> str:
    """The issue as the card showed it.

    Not just its creation time: an integration can update an issue in place
    (same id, same ``created``) with new data or placeholders — a different
    description, a different thing for the fix to act on.
    """
    import hashlib  # noqa: PLC0415
    import json  # noqa: PLC0415

    payload = json.dumps(
        [
            issue.domain,
            issue.issue_id,
            issue.created.isoformat(),
            issue.translation_key,
            issue.translation_placeholders,
            issue.data,
            str(issue.severity),
        ],
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


async def _is_one_confirmation(manager: Any, issue: ir.IssueEntry) -> bool:
    """Whether the fix is Home Assistant's own one-step confirmation.

    The first step cannot say: an empty confirmation form may lead on to more
    steps (HA's legacy subscription fix goes on to an external step). Only the
    stock ``ConfirmRepairFlow`` is known to finish on that one confirmation. The
    manager's ``async_create_flow`` builds the flow the integration answers
    with WITHOUT running a step — running one could already do the fix's work —
    so the class is read off that, and nothing is left registered.
    """
    from homeassistant.components.repairs import ConfirmRepairFlow  # noqa: PLC0415

    flow = await manager.async_create_flow(issue.domain, data={"issue_id": issue.issue_id})
    return type(flow) is ConfirmRepairFlow


async def async_preview_fix(hass: HomeAssistant, domain: str, issue_id: str) -> dict[str, Any]:
    """A confirmation card for a repair's fix, when it is a single confirmation.

    A fix that asks for choices or input is not carded: walking it belongs to
    the Repairs page (or the MCP tool, which goes step by step).
    """
    from homeassistant.components.repairs import repairs_flow_manager  # noqa: PLC0415

    issue = _issue(hass, domain, issue_id)
    if isinstance(issue, str):
        return {"error": issue}
    if not issue.is_fixable:
        return {"error": "This repair has no automatic fix; its description says what to do."}
    manager = repairs_flow_manager(hass)
    if manager is None:
        return {"error": "Repairs are not set up in this Home Assistant."}
    try:
        one_step = await _is_one_confirmation(manager, issue)
    except _FLOW_ERRORS as exc:
        return {"error": f"The fix could not start: {sanitize_untrusted_text(str(exc), 200)}"}
    if not one_step:
        return {
            "error": (
                "This fix asks for choices, so it cannot be run from here. Open "
                "Settings → Repairs to go through it."
            )
        }
    texts = await _texts(hass, [issue])
    row = _row(issue, texts)
    confirm = texts.get(
        f"component.{issue.domain}.issues.{issue.translation_key}.fix_flow.step.confirm.description"
    )
    label = f"Fix: {row['title']}"
    if detail := (
        _fill(confirm, issue.translation_placeholders) if confirm else row.get("description")
    ):
        label = f"{label} — {sanitize_untrusted_text(detail, 200)}"
    return {
        "requires_approval": True,
        "destructive": {
            "kind": "repair",
            "verb": "fix",
            "target_id": f"{issue.domain}/{issue.issue_id}",
            "entity_id": "",
            "name": row["title"],
            "label": label,
            "fingerprint": _fingerprint(issue),
        },
    }


async def async_run_confirmed_fix(
    hass: HomeAssistant, domain: str, issue_id: str, expected_fingerprint: str
) -> dict[str, Any]:
    """Run a carded fix: still the issue shown, still a single confirmation."""
    from homeassistant.components.repairs import repairs_flow_manager  # noqa: PLC0415

    issue = _issue(hass, domain, issue_id)
    if isinstance(issue, str):
        return {"error": "That repair is no longer open."}
    if _fingerprint(issue) != expected_fingerprint:
        return {"error": "That repair was raised again since it was shown; ask again."}
    manager = repairs_flow_manager(hass)
    if manager is None:
        return {"error": "Repairs are not set up in this Home Assistant."}
    flow_id: str | None = None
    try:
        # Checked again: the integration decides the flow per call. Only the
        # stock flow is started, and its first step only shows the confirmation.
        if not await _is_one_confirmation(manager, issue):
            return {
                "error": "This fix now asks for more than a confirmation; use Settings → Repairs."
            }
        result = await manager.async_init(issue.domain, data={"issue_id": issue.issue_id})
        flow_id = result.get("flow_id")
        # Starting it awaited; the issue may have been replaced meanwhile, and
        # submitting would delete the new one by the same id.
        current = ir.async_get(hass).async_get_issue(issue.domain, issue.issue_id)
        if current is None or _fingerprint(current) != expected_fingerprint:
            return {"error": "That repair was raised again since it was shown; ask again."}
        if result.get("type") != "form":
            return {
                "error": "This fix now asks for more than a confirmation; use Settings → Repairs."
            }
        result = await manager.async_configure(flow_id, {})
        if result.get("type") == "create_entry":
            flow_id = None
            return {"status": "fixed", "domain": issue.domain, "issue_id": issue.issue_id}
        reason = result.get("reason") or result.get("step_id") or result.get("type")
        return {"error": f"The fix did not complete ({sanitize_untrusted_text(str(reason))})."}
    except _FLOW_ERRORS as exc:
        return {"error": f"The fix failed: {sanitize_untrusted_text(str(exc), 200)}"}
    finally:
        if flow_id:
            with contextlib.suppress(UnknownFlow):
                manager.async_abort(flow_id)
