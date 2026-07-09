"""WebSocket surface for the recipes pipeline.

Five commands, all admin-gated:

- ``selora_ai/recipes/list``      — list bundles on disk + installed records
- ``selora_ai/recipes/get``       — manifest detail for one slug
- ``selora_ai/recipes/preview``   — dry-run the pipeline through render
- ``selora_ai/recipes/install``   — full pipeline including disk write
- ``selora_ai/recipes/package``   — read an installed package's YAML + counts
- ``selora_ai/recipes/uninstall`` — remove package + record

The handlers are thin: they validate WS args, dispatch to the
pipeline, then serialise the result. No business logic lives here.
"""

from __future__ import annotations

from dataclasses import asdict, replace
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import decorators
import voluptuous as vol
import yaml

from ..const import KNOWN_INTEGRATIONS
from .archive import ArchiveError, async_install_from_url
from .catalog import CatalogError, _catalog_url, async_get_catalog
from .dashboard import async_insert_card, list_writable_dashboards
from .loader import async_list_bundles, async_load_bundle
from .manifest import ManifestError
from .packager import (
    PackagerError,
    async_reload_core_config,
    package_path,
    update_package_groups,
)
from .pipeline import (
    PipelineResult,
    async_install,
    async_preview,
    async_uninstall,
)
from .pipeline_items import derive_items
from .renderer import _group_object_id
from .resolver import resolve
from .resolvers import RESOLVERS, ResolverError
from .store import get_install_store

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .manifest import Manifest, RoleSpec

_LOGGER = logging.getLogger(__name__)


def _role_summary(role: RoleSpec) -> dict[str, Any]:
    """Serialise a role for the client, resolving a friendly title for
    its ``integration`` scope (``lg_thinq`` → ``LG ThinQ``) so the
    wizard can label the card without shipping the whole integrations
    database. Falls back to the raw domain for unknown integrations.
    """
    data = asdict(role)
    if role.integration:
        info = KNOWN_INTEGRATIONS.get(role.integration)
        data["integration_title"] = info.name if info else role.integration
    return data


def _integration_brands(items: list[tuple[str, str]]) -> list[dict[str, str]]:
    """Resolve ``(domain, reason)`` pairs to ``{domain, title, reason}``
    for the card brand strip — deduped by domain (first non-empty reason
    wins), order-preserving. Titles come from ``KNOWN_INTEGRATIONS``
    (``lg_thinq`` → ``LG ThinQ``); an unknown domain falls back to
    itself. ``reason`` explains why the recipe needs the integration and
    is surfaced on hover; empty when the recipe didn't say.
    """
    order: list[str] = []
    by_domain: dict[str, dict[str, str]] = {}
    for domain, reason in items:
        if not domain:
            continue
        if domain not in by_domain:
            info = KNOWN_INTEGRATIONS.get(domain)
            by_domain[domain] = {
                "domain": domain,
                "title": info.name if info else domain,
                "reason": reason or "",
            }
            order.append(domain)
        elif reason and not by_domain[domain]["reason"]:
            by_domain[domain]["reason"] = reason
    return [by_domain[d] for d in order]


def _require_admin(connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> bool:
    """Return False (and send a not_allowed error) when the user isn't an
    admin. All recipe writes touch the homeowner's filesystem and
    configuration; nothing here should be reachable from a guest.
    """
    user = connection.user
    if user is None or not user.is_admin:
        connection.send_error(
            msg["id"],
            "not_allowed",
            "Selora AI recipes require an administrator account",
        )
        return False
    return True


# ── Serialisation helpers ───────────────────────────────────────────


def _count_package_sections(contents: dict[str, Any]) -> dict[str, int]:
    """Count entries in each HA package section the recipe produces.

    Used by the wizard's Overview + Activate steps to summarise what
    the recipe creates ("Creates 4 automations · 2 scenes · 1 script").
    Top-level package keys map to HA domains; each value is either a
    list (counted via ``len``) or a dict of entries (also counted).
    Unknown / non-collection values are ignored — we only care about
    sections that produce user-visible artifacts.
    """
    counts: dict[str, int] = {}
    for key, value in (contents or {}).items():
        if isinstance(value, (list, dict)):
            counts[key] = len(value)
    return counts


def _manifest_summary(manifest: Manifest) -> dict[str, Any]:
    """Compact view of a manifest for the list/get responses. Strips
    template bodies (clients don't need them) and serialises dataclasses
    to plain dicts.
    """
    return {
        "slug": manifest.slug,
        "version": manifest.version,
        "title": manifest.title,
        "tagline": manifest.tagline,
        "description": manifest.description,
        "author": manifest.author,
        "released": manifest.released,
        "tags": list(manifest.tags),
        "binding_mode": manifest.binding_mode,
        "roles": [_role_summary(r) for r in manifest.roles],
        "inputs": [asdict(i) for i in manifest.inputs],
        "integrations": [asdict(i) for i in manifest.integrations],
        # Deduped {domain, title, reason} brands for the card logo strip
        # — declared integrations plus any integration-scoped role. The
        # role's description explains why the integration is needed
        # (shown on hover); declared integrations carry no reason.
        "integration_brands": _integration_brands(
            [(i.domain, "") for i in manifest.integrations]
            + [(r.integration, r.description) for r in manifest.roles if r.integration]
        ),
        # Present only when the recipe ships a final-stage dashboard card.
        # The wizard reads this to show the "which dashboard?" picker.
        "dashboard": asdict(manifest.dashboard) if manifest.dashboard else None,
    }


def _result_payload(
    hass: HomeAssistant,
    result: PipelineResult,
    manifest: Manifest | None,
) -> dict[str, Any]:
    """Serialise a PipelineResult for the wire. Trim the rendered
    package's parsed contents — the YAML text is the canonical preview;
    the structured dict is duplicate data.

    When ``manifest`` is provided we also derive the wizard's pipeline
    item list — one row per Prepare/Configure/Apply step the frontend
    renders. Items can't be derived when the manifest failed to load
    (definition-stage failure) so we just omit the field then.
    """
    payload: dict[str, Any] = {
        "ok": result.ok,
        "stage_reached": result.stage_reached,
        "punch_list": [asdict(item) for item in result.punch_list],
        # ``bindings`` = what the renderer will actually use (selected
        # subset). ``candidates`` = the full match list the wizard
        # renders as togglable chips. ``selection_modes`` = per-role
        # "auto" vs "required" so the UI knows whether to show the
        # toggle row or just the static bound list.
        "bindings": result.bindings,
        "candidates": result.candidates,
        "pinned": result.pinned,
        "selection_modes": result.selection_modes,
    }
    if manifest is not None:
        # Only count integrations that have a real config entry — i.e.
        # the user actually went through the config flow for them. We
        # used to union with ``hass.config.components`` too, but that
        # set includes anything HA imported during startup (transient
        # dependencies, default_config side-effects), which lit up
        # integrations as "Configured" in the wizard even when the
        # user never set them up.
        entries = list(hass.config_entries.async_entries())
        integrations_loaded = {entry.domain for entry in entries}
        # First entry per domain is good enough for the wizard's
        # "what got set up" hint — most recipes only require one
        # config entry per integration domain.
        integration_titles: dict[str, str] = {}
        for entry in entries:
            integration_titles.setdefault(entry.domain, entry.title or "")
        payload["items"] = [
            item.to_dict()
            for item in derive_items(
                manifest,
                result,
                integrations_loaded=integrations_loaded,
                integration_titles=integration_titles,
            )
        ]
    if result.preview is not None:
        payload["preview"] = {
            "yaml": result.preview.yaml_text,
            "created_counts": _count_package_sections(result.preview.contents),
        }
    if result.record is not None:
        payload["record"] = asdict(result.record)
    return payload


async def _load_manifest_quietly(hass: HomeAssistant, slug: str) -> Manifest | None:
    """Try to load the bundle for the slug just to grab its manifest
    — used by preview/install handlers so the payload can carry the
    derived pipeline items. Failures aren't fatal: the WS still
    returns a useful result with ``items`` absent when the bundle
    can't be parsed.
    """
    try:
        bundle = await async_load_bundle(hass, slug)
    except ManifestError:
        return None
    return bundle.manifest


# ── Handlers ────────────────────────────────────────────────────────


@websocket_api.async_response
@decorators.websocket_command({vol.Required("type"): "selora_ai/recipes/list"})
async def _ws_recipes_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    if not _require_admin(connection, msg):
        return
    bundles = await async_list_bundles(hass)
    available = [_manifest_summary(b.manifest) for b in bundles]
    installed = [asdict(r) for r in await get_install_store(hass).async_list()]
    connection.send_result(msg["id"], {"available": available, "installed": installed})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/recipes/get",
        vol.Required("slug"): str,
    }
)
async def _ws_recipes_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    if not _require_admin(connection, msg):
        return
    try:
        bundle = await async_load_bundle(hass, msg["slug"])
    except ManifestError as exc:
        connection.send_error(msg["id"], "not_found", str(exc))
        return
    record = await get_install_store(hass).async_get(msg["slug"])
    connection.send_result(
        msg["id"],
        {
            "manifest": _manifest_summary(bundle.manifest),
            "installed": asdict(record) if record else None,
        },
    )


_INPUTS_SCHEMA = vol.Schema(
    {str: vol.Any(str, int, float, bool, list, dict, None)},
    extra=vol.ALLOW_EXTRA,
)
# {role_id: [entity_id, ...]} — the wizard's per-role pick for
# ``selection: required`` roles. Anything missing from this dict means
# "use the auto-resolution default" (which is fine for auto roles and
# treated as "nothing selected yet" for required ones).
_SELECTIONS_SCHEMA = vol.Schema({str: [str]}, extra=vol.ALLOW_EXTRA)


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/recipes/preview",
        vol.Required("slug"): str,
        vol.Optional("inputs", default=dict): _INPUTS_SCHEMA,
        vol.Optional("selections", default=dict): _SELECTIONS_SCHEMA,
    }
)
async def _ws_recipes_preview(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    if not _require_admin(connection, msg):
        return
    result = await async_preview(
        hass,
        slug=msg["slug"],
        inputs=msg.get("inputs") or {},
        selections=msg.get("selections") or {},
    )
    manifest = await _load_manifest_quietly(hass, msg["slug"])
    connection.send_result(msg["id"], _result_payload(hass, result, manifest))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/recipes/install",
        vol.Required("slug"): str,
        vol.Optional("inputs", default=dict): _INPUTS_SCHEMA,
        vol.Optional("selections", default=dict): _SELECTIONS_SCHEMA,
        vol.Optional("dashboard_target"): vol.Any(str, None),
    }
)
async def _ws_recipes_install(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    if not _require_admin(connection, msg):
        return
    result = await async_install(
        hass,
        slug=msg["slug"],
        inputs=msg.get("inputs") or {},
        selections=msg.get("selections") or {},
        dashboard_target=msg.get("dashboard_target"),
    )
    manifest = await _load_manifest_quietly(hass, msg["slug"])
    connection.send_result(msg["id"], _result_payload(hass, result, manifest))


@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/recipes/install_stream",
        vol.Required("slug"): str,
        vol.Optional("inputs", default=dict): _INPUTS_SCHEMA,
        vol.Optional("selections", default=dict): _SELECTIONS_SCHEMA,
        vol.Optional("dashboard_target"): vol.Any(str, None),
    }
)
@websocket_api.async_response
async def _ws_recipes_install_stream(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Streaming install: emits ``apply/<step>`` events as the pipeline
    walks render → write → reload → verify, then a final ``result``
    event carrying the full PipelineResult payload. The client can
    flip its Apply column rows live instead of waiting for the whole
    install to finish before any feedback shows up.

    Subscribers receive events as standard subscribe-style messages
    (``connection.send_message(websocket_api.event_message(...))``).
    A final ``result`` event marks the install done; afterwards the
    handler returns without leaving the subscription open — there's
    nothing more to send.
    """
    if not _require_admin(connection, msg):
        return

    def _emit(payload: dict[str, Any]) -> None:
        # Called from inside the pipeline on the event loop thread,
        # so a direct ``send_message`` is safe (no thread hop needed).
        connection.send_message(websocket_api.event_message(msg["id"], {"event": payload}))

    # Send initial "subscribed" ack so the frontend knows the stream
    # is live before any work happens.
    connection.send_result(msg["id"])
    result = await async_install(
        hass,
        slug=msg["slug"],
        inputs=msg.get("inputs") or {},
        selections=msg.get("selections") or {},
        dashboard_target=msg.get("dashboard_target"),
        on_event=_emit,
    )
    manifest = await _load_manifest_quietly(hass, msg["slug"])
    # Final event: the complete result payload. ``event`` shape lets
    # the client tell stream events from the final aggregate.
    connection.send_message(
        websocket_api.event_message(
            msg["id"],
            {"event": {"type": "result", "result": _result_payload(hass, result, manifest)}},
        )
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/recipes/rebind",
        vol.Required("slug"): str,
        vol.Required("selections"): _SELECTIONS_SCHEMA,
    }
)
async def _ws_recipes_rebind(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """v3 prototype: update an installed recipe's group bindings without
    re-rendering the package. Takes new ``selections`` (same shape as
    install/preview), runs them through the resolver, and rewrites only
    the ``group:`` section of the on-disk package YAML. Automations
    untouched — the homeowner can swap devices without re-running the
    wizard.

    Only valid for recipes whose manifest declares ``binding_mode:
    group``. Literal-mode recipes return ``not_supported``.
    """
    if not _require_admin(connection, msg):
        return
    slug = msg["slug"]
    try:
        bundle = await async_load_bundle(hass, slug)
    except ManifestError as exc:
        connection.send_error(msg["id"], "not_found", str(exc))
        return
    if bundle.manifest.binding_mode != "group":
        connection.send_error(
            msg["id"],
            "not_supported",
            "Rebind requires binding_mode=group; this recipe is literal-mode.",
        )
        return

    resolution = resolve(bundle.manifest, hass, selections=msg.get("selections") or {})
    if not resolution.ok:
        connection.send_error(
            msg["id"],
            "rebind_unmet",
            "; ".join(f.reason for f in resolution.failures())
            or "Role resolution failed for the new selections.",
        )
        return

    group_updates = {
        _group_object_id(bundle.manifest.slug, role.id): list(resolution.bindings.get(role.id, []))
        for role in bundle.manifest.roles
    }
    try:
        await hass.async_add_executor_job(update_package_groups, hass, slug, group_updates)
    except PackagerError as exc:
        connection.send_error(msg["id"], "rebind_failed", str(exc))
        return

    # Reload so the new memberships take effect immediately.
    await async_reload_core_config(hass)

    # Persist the new bindings to the install record so the wizard's
    # "Manage devices" panel reflects current state on next open.
    store = get_install_store(hass)
    record = await store.async_get(slug)
    if record is not None:
        await store.async_record(
            slug=record.slug,
            version=record.version,
            title=record.title,
            package_path=record.package_path,
            bindings=resolution.bindings,
            inputs=record.inputs,
            # Carry forward the install metadata — async_record() does a
            # full overwrite, so omitting these would wipe the record's
            # owned config entries (breaking uninstall's "also remove this
            # integration") and its dashboard placement state on rebind.
            integrations_installed=record.integrations_installed,
            dashboard_card=record.dashboard_card,
        )

    connection.send_result(
        msg["id"],
        {
            "ok": True,
            "bindings": resolution.bindings,
        },
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/recipes/auto_setup_integration",
        vol.Required("slug"): str,
        vol.Required("domain"): str,
    }
)
async def _ws_recipes_auto_setup_integration(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Drive an integration's config flow hands-off using values the
    recipe specifies in ``integration.auto_setup``.

    The flow walks server-side via ``hass.config_entries.flow.async_init``
    + ``async_configure`` so the homeowner never sees the form. Each
    form step gets the same merged user_input — recipe values win over
    HA's schema defaults, but anything not provided falls back to the
    voluptuous Required defaults (e.g. lat/lon from hass.config).

    Returns ``{"ok": true}`` on create_entry; surfaces a homeowner-readable
    error otherwise.
    """
    if not _require_admin(connection, msg):
        return
    try:
        bundle = await async_load_bundle(hass, msg["slug"])
    except ManifestError as exc:
        connection.send_error(msg["id"], "not_found", str(exc))
        return

    spec = next(
        (i for i in bundle.manifest.integrations if i.domain == msg["domain"]),
        None,
    )
    if spec is None or not spec.auto_setup:
        connection.send_error(
            msg["id"],
            "not_supported",
            f"No auto_setup defined for {msg['domain']!r} on this recipe.",
        )
        return

    # Compute the values dict: literals + resolver outputs.
    user_input: dict[str, Any] = dict(spec.auto_setup.get("values") or {})
    for field, resolver_name in (spec.auto_setup.get("resolved") or {}).items():
        resolver = RESOLVERS.get(resolver_name)
        if resolver is None:
            connection.send_error(
                msg["id"],
                "resolver_missing",
                f"Recipe references unknown resolver {resolver_name!r}.",
            )
            return
        try:
            user_input[field] = await resolver(hass)
        except ResolverError as exc:
            connection.send_error(msg["id"], "resolver_failed", str(exc))
            return

    # Walk the flow until create_entry or abort. ``async_init`` returns
    # the first step; each ``async_configure`` returns the next.
    try:
        step = await hass.config_entries.flow.async_init(spec.domain, context={"source": "user"})
        for _ in range(8):  # bounded — flows that don't terminate in 8 steps are pathological
            if step.get("type") != "form":
                break
            step = await hass.config_entries.flow.async_configure(step["flow_id"], dict(user_input))
    except Exception as exc:  # noqa: BLE001 — surface any HA flow failure verbatim
        connection.send_error(msg["id"], "flow_failed", f"Integration setup failed: {exc}")
        return

    flow_type = step.get("type")
    if flow_type == "abort":
        connection.send_error(
            msg["id"],
            "flow_aborted",
            step.get("reason") or "Integration setup was aborted.",
        )
        return
    if flow_type != "create_entry":
        connection.send_error(
            msg["id"],
            "flow_incomplete",
            f"Integration setup needs manual input (step type: {flow_type}).",
        )
        return

    # Track ownership: stash the new entry id under
    # ``hass.data[selora_ai][_auto_setup_owned][slug][domain]`` so the
    # install pipeline can copy it into the InstallRecord when the
    # recipe finishes installing. This is what powers "this recipe
    # installed these integrations — remove them on uninstall?".
    entry_id = step.get("result")
    if hasattr(entry_id, "entry_id"):
        # async_init returns a ConfigEntry object in ``result`` on
        # create_entry. Older HA versions sometimes return just the id.
        entry_id = entry_id.entry_id
    owned = (
        hass.data.setdefault("selora_ai", {})
        .setdefault("_auto_setup_owned", {})
        .setdefault(msg["slug"], {})
    )
    if entry_id:
        owned[spec.domain] = entry_id

    connection.send_result(msg["id"], {"ok": True, "type": flow_type, "entry_id": entry_id})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/recipes/package",
        vol.Required("slug"): str,
    }
)
async def _ws_recipes_package(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Read the installed package file for ``slug`` and return its raw YAML
    plus a per-section count summary ("2 automations · 1 toggle"). Lets the
    panel show what a recipe actually created and the file's contents without
    making the user dig through ``/config`` by hand.
    """
    if not _require_admin(connection, msg):
        return
    slug = msg["slug"]
    record = await get_install_store(hass).async_get(slug)
    # Prefer the path the record actually wrote; fall back to the canonical one.
    path = Path(record.package_path) if record and record.package_path else package_path(hass, slug)

    def _read() -> str | None:
        return path.read_text(encoding="utf-8") if path.is_file() else None

    text = await hass.async_add_executor_job(_read)
    if text is None:
        connection.send_error(msg["id"], "not_found", f"No package file on disk for {slug!r}")
        return
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        parsed = None
    counts = _count_package_sections(parsed if isinstance(parsed, dict) else {})
    connection.send_result(msg["id"], {"yaml": text, "counts": counts, "package_path": str(path)})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/recipes/uninstall",
        vol.Required("slug"): str,
        vol.Optional("remove_entries", default=list): [str],
    }
)
async def _ws_recipes_uninstall(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    if not _require_admin(connection, msg):
        return
    # Safety check: only honour entry removal for entries the install
    # record actually claims as recipe-owned. Stops a malicious or
    # buggy client from passing arbitrary config_entry_ids and nuking
    # integrations the homeowner set up by hand.
    requested = set(msg.get("remove_entries") or [])
    if requested:
        record = await get_install_store(hass).async_get(msg["slug"])
        owned = set((record.integrations_installed or {}).values()) if record else set()
        validated = list(requested & owned)
    else:
        validated = []
    result = await async_uninstall(hass, msg["slug"], remove_entries=validated)
    # Uninstall doesn't need the items list — there's no wizard to
    # paint, just a confirm modal. Pass manifest=None to skip the
    # derivation.
    connection.send_result(msg["id"], _result_payload(hass, result, None))


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/recipes/catalog",
        vol.Optional("force_refresh", default=False): bool,
        vol.Optional("url"): str,
    }
)
async def _ws_recipes_catalog(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Fetch the public recipes catalog from selorahomes.com (or the
    dev override) and return it verbatim. Cached server-side for a
    short TTL; ``force_refresh`` bypasses the cache. ``url`` lets
    the client point at a different catalog endpoint per request —
    useful for dev (localhost Hugo server) or pointing at a staging
    environment without restarting HA.
    """
    if not _require_admin(connection, msg):
        return
    try:
        catalog = await async_get_catalog(
            hass,
            force_refresh=bool(msg.get("force_refresh")),
            url_override=msg.get("url") or None,
        )
    except CatalogError as exc:
        connection.send_error(msg["id"], "catalog_fetch_failed", str(exc))
        return
    installed = await get_install_store(hass).async_list()
    installed_slugs = {r.slug for r in installed}
    # The catalog may list ``package_url`` relative to itself (the Hugo
    # dev server emits ``/recipes/…tar.gz``); the installer needs an
    # absolute URL, so resolve each against the catalog's own URL. An
    # already-absolute URL is returned unchanged by urljoin.
    base_url = _catalog_url(msg.get("url") or None)
    # Enrich each entry with {domain, title} brands for the card logo
    # strip, resolved from the integration hints in its required/optional
    # blocks. Copy per entry so the shared cached payload isn't mutated.
    recipes: list[dict[str, Any]] = []
    for entry in catalog.get("recipes", []):
        reqs = (entry.get("required") or []) + (entry.get("optional") or [])
        items = [
            (item["integration"], str(item.get("reason") or ""))
            for item in reqs
            if isinstance(item, dict) and item.get("integration")
        ]
        enriched = {**entry, "integration_brands": _integration_brands(items)}
        pkg = entry.get("package_url")
        if pkg:
            enriched["package_url"] = urljoin(base_url, str(pkg))
        recipes.append(enriched)
    connection.send_result(
        msg["id"],
        {
            "generated_at": catalog.get("generated_at", ""),
            "count": catalog.get("count", 0),
            "recipes": recipes,
            "installed_slugs": sorted(installed_slugs),
        },
    )


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/recipes/install_from_url",
        vol.Required("url"): str,
    }
)
async def _ws_recipes_install_from_url(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Fetch a recipe archive from ``url``, extract + validate, and
    stage the resulting bundle under ``selora_ai_recipes/<slug>/``.

    Returns the staged bundle metadata so the frontend can immediately
    open the wizard for the just-fetched recipe. This step does NOT run
    the install pipeline — the user reviews bindings + inputs first.
    """
    if not _require_admin(connection, msg):
        return
    try:
        staged = await async_install_from_url(hass, msg["url"])
    except ArchiveError as exc:
        connection.send_error(msg["id"], "archive_error", str(exc))
        return
    connection.send_result(
        msg["id"],
        {
            "slug": staged.slug,
            "title": staged.title,
            "version": staged.version,
            "path": str(staged.path),
        },
    )


@websocket_api.async_response
@decorators.websocket_command({vol.Required("type"): "selora_ai/recipes/list_dashboards"})
async def _ws_recipes_list_dashboards(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List storage-mode (writable) Lovelace dashboards for the wizard's
    "which dashboard?" picker. Read-only; admin-gated for parity with the
    rest of the recipe surface.
    """
    if not _require_admin(connection, msg):
        return
    connection.send_result(msg["id"], {"dashboards": list_writable_dashboards(hass)})


@websocket_api.async_response
@decorators.websocket_command(
    {
        vol.Required("type"): "selora_ai/recipes/insert_dashboard_card",
        vol.Required("slug"): str,
        vol.Optional("target"): vol.Any(str, None),
        vol.Optional("view"): vol.Any(int, str),
    }
)
async def _ws_recipes_insert_dashboard_card(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Place the recipe's manifest dashboard card after install.

    Backs the wizard's Step 5 "Add card" action. The install pipeline
    no longer auto-inserts (the offer moved post-install), so this is the
    on-demand path: load the installed record for its bindings + inputs,
    apply the user's chosen ``target``/``view`` over the manifest spec,
    insert via the deterministic Lovelace writer, then patch the record
    so the wizard reflects the placement. Never mutates the package.
    """
    if not _require_admin(connection, msg):
        return
    slug = msg["slug"]
    try:
        bundle = await async_load_bundle(hass, slug)
    except (ManifestError, OSError) as exc:
        connection.send_error(msg["id"], "load_failed", str(exc))
        return
    spec = bundle.manifest.dashboard
    if spec is None:
        connection.send_error(msg["id"], "no_dashboard", "Recipe declares no dashboard card.")
        return
    record = await get_install_store(hass).async_get(slug)
    if record is None:
        connection.send_error(msg["id"], "not_installed", "Recipe is not installed.")
        return
    # The picker's choice overrides the manifest's declared target/view.
    if "target" in msg:
        spec = replace(spec, target=msg["target"])
    if "view" in msg:
        spec = replace(spec, view=msg["view"])
    result = await async_insert_card(
        hass,
        slug=slug,
        spec=spec,
        bindings=record.bindings,
        inputs=record.inputs,
    )
    card = {
        "ok": result.ok,
        "reason": result.reason,
        "target": result.target,
        "view": result.view,
    }
    await get_install_store(hass).async_update_dashboard_card(slug, card)
    connection.send_result(msg["id"], {**card, "message": result.message})


def async_register_recipe_websocket_commands(hass: HomeAssistant) -> None:
    """Register every WS command. Call once from ``async_setup``."""
    websocket_api.async_register_command(hass, _ws_recipes_list)
    websocket_api.async_register_command(hass, _ws_recipes_list_dashboards)
    websocket_api.async_register_command(hass, _ws_recipes_insert_dashboard_card)
    websocket_api.async_register_command(hass, _ws_recipes_get)
    websocket_api.async_register_command(hass, _ws_recipes_preview)
    websocket_api.async_register_command(hass, _ws_recipes_install)
    websocket_api.async_register_command(hass, _ws_recipes_install_stream)
    websocket_api.async_register_command(hass, _ws_recipes_rebind)
    websocket_api.async_register_command(hass, _ws_recipes_auto_setup_integration)
    websocket_api.async_register_command(hass, _ws_recipes_package)
    websocket_api.async_register_command(hass, _ws_recipes_uninstall)
    websocket_api.async_register_command(hass, _ws_recipes_install_from_url)
    websocket_api.async_register_command(hass, _ws_recipes_catalog)
