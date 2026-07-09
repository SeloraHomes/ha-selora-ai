"""End-to-end install / uninstall orchestrator.

Walks the six stages described in :mod:`selora_ai.recipes.__init__` —
load → resolve → validate inputs / prereqs → render → install → reload.
Every stage produces an artifact for the next, and any stage that fails
short-circuits the rest with a structured punch list of what went wrong.

The pipeline runs without an LLM in the loop. Everything is data-in /
artifact-out; the same call shape works for a wizard click, a CI dry
run, or a Connect remote preview against a snapshot of the customer's
home.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
import logging
from typing import TYPE_CHECKING, Any

from .dashboard import SKIP_TARGET, async_insert_card, async_remove_cards
from .loader import async_load_bundle, async_remove_bundle
from .manifest import ManifestError
from .packager import (
    PackagerError,
    async_reload_core_config,
    ensure_packages_include,
    remove_package,
    write_package,
)
from .renderer import RenderedPackage, RenderError, render_package
from .resolver import resolve
from .resolvers import ResolverError, async_apply_auto_inputs
from .store import get_install_store
from .validator import (
    InputReport,
    IntegrationReport,
    check_integrations,
    validate_inputs,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .store import InstallRecord

_LOGGER = logging.getLogger(__name__)


# ── Punch list ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PunchItem:
    """One actionable item the user needs to address before the install
    can proceed (or to understand what went wrong if it already failed).

    ``stage`` lets the wizard group items by the pipeline stage that
    produced them, so the UI can show a clear "stuck at: role resolution"
    section.

    ``identity`` carries device-pairing hints for waiting-on bindings
    (manufacturer, model, integration, vendor id). The wizard renders
    these as a device-identity card so the field tech knows exactly
    what to pair on D-Day.
    """

    stage: str
    code: str
    message: str
    # Optional field hint (input id, role id, integration domain) the UI
    # can use to attach the item to the specific control that failed.
    target: str | None = None
    # Populated only for ``code == "binding_pending"`` items. ``None``
    # otherwise so the serialiser drops it.
    identity: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Aggregated outcome of one pipeline run.

    ``ok`` reflects whether every stage finished without halting; when
    False, ``punch_list`` carries at least one entry. ``record`` is set
    only when the install completed and was persisted; ``preview``
    surfaces a render so the UI can show what will be (or just was)
    written.
    """

    ok: bool
    stage_reached: str
    punch_list: tuple[PunchItem, ...] = ()
    preview: RenderedPackage | None = None
    record: InstallRecord | None = None
    # Snapshot of the resolved bindings for the wizard's review screen.
    # ``bindings`` is the SELECTED subset that will reach the renderer.
    # ``candidates`` is every entity that matched the role filter — the
    # wizard renders these as the togglable list for ``selection:
    # required`` roles. ``selection_modes`` lets the wizard tell which
    # roles need user input vs. are bound automatically.
    bindings: dict[str, list[str]] = field(default_factory=dict)
    candidates: dict[str, list[str]] = field(default_factory=dict)
    selection_modes: dict[str, str] = field(default_factory=dict)
    # ``pinned`` is the entity_ids the manifest pre-bound that DID
    # resolve to a real, filter-matching entity. The wizard renders
    # these as locked chips so the user knows they can't be toggled
    # off without editing the manifest.
    pinned: dict[str, list[str]] = field(default_factory=dict)
    # CI-style pipeline view: one item per step in the install. Each
    # carries id/stage/title/status/kind/payload. The wizard renders
    # the three Prepare/Configure/Apply columns straight from this
    # list and never has to re-derive "is this role still pending?".
    # Populated by ``_run`` via ``derive_items`` from this same
    # result + the live manifest.
    items: list[dict[str, Any]] = field(default_factory=list)


# ── Public entry points ─────────────────────────────────────────────


STAGES = (
    "definition",
    "resolve",
    "validate",
    "render",
    "install",
    "reload",
    "complete",
)


async def async_preview(
    hass: HomeAssistant,
    *,
    slug: str,
    inputs: dict[str, Any] | None = None,
    selections: dict[str, list[str]] | None = None,
) -> PipelineResult:
    """Run every stage up to and including render, but DON'T touch
    disk. The result's ``preview`` carries the rendered package the
    install would write. Used by the wizard's "Review" screen.

    ``selections`` is the per-role pick for any role declared with
    ``selection: required`` in the manifest. Auto-selection roles
    ignore it and take every candidate.
    """
    return await _run(hass, slug=slug, inputs=inputs, selections=selections, write=False)


async def async_install(
    hass: HomeAssistant,
    *,
    slug: str,
    inputs: dict[str, Any] | None = None,
    selections: dict[str, list[str]] | None = None,
    dashboard_target: str | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> PipelineResult:
    """Run the full pipeline through to disk + reload. Writes the
    package, updates configuration.yaml if needed, reloads HA core
    config, and persists the install record.

    ``dashboard_target`` overrides where the recipe's optional card is
    inserted: a dashboard ``url_path``, ``None`` to use the manifest's
    declared target, or the ``"__skip__"`` sentinel to skip insertion
    entirely (the user chose "don't add a card"). Ignored when the
    recipe declares no ``dashboard:`` block.

    ``on_event`` is an optional sync callback the pipeline invokes as
    each Apply-stage step starts and finishes. Each call gets a dict
    of shape ``{"type": "apply", "step": "<id>", "status": "<state>",
    "detail": "..."}`` where ``step`` ∈ ``render|write|reload|verify``
    and ``status`` ∈ ``running|ok|failed|skipped``. Used by the
    streaming WS command so the wizard board can flip items live.
    Failures are caught and dropped so a broken subscriber can't
    take the install down with it.
    """
    return await _run(
        hass,
        slug=slug,
        inputs=inputs,
        selections=selections,
        dashboard_target=dashboard_target,
        write=True,
        on_event=on_event,
    )


async def async_uninstall(
    hass: HomeAssistant,
    slug: str,
    *,
    remove_entries: list[str] | None = None,
) -> PipelineResult:
    """Remove the package file + install record, then reload core
    config. Idempotent: uninstalling something that isn't installed
    succeeds with ``ok=True`` and no record.

    ``remove_entries`` is an optional list of HA config_entry ids to
    delete after the package is gone. Caller (usually the uninstall
    WS) populates this with entries the recipe owns (via the install
    record's ``integrations_installed`` map) when the homeowner ticks
    "also remove this integration" in the uninstall modal. Failures
    on individual entries don't abort — the package removal already
    succeeded, dangling entries are a soft failure surfaced via the
    punch list.
    """
    store = get_install_store(hass)
    record = await store.async_get(slug)
    try:
        # Either may be missing — that's fine, we want this idempotent.
        await hass.async_add_executor_job(remove_package, hass, slug)
    except PackagerError as exc:
        return PipelineResult(
            ok=False,
            stage_reached="install",
            punch_list=(
                PunchItem(
                    stage="install",
                    code="package_remove_failed",
                    message=str(exc),
                ),
            ),
        )
    await store.async_remove(slug)

    # Delete the staged bundle directory so the uninstalled recipe stops
    # showing in the panel's "Installed recipes" list (and doesn't bloat the
    # filesystem). Best-effort — it's re-stageable from the catalog, so a
    # failure here never blocks the uninstall.
    try:
        await async_remove_bundle(hass, slug)
    except Exception as exc:  # noqa: BLE001 — bundle cleanup must not abort uninstall
        _LOGGER.warning("Recipe %s: bundle directory cleanup failed: %s", slug, exc)

    # Pull any dashboard card this recipe dropped. Scans storage-mode
    # dashboards by the slug tag, so it's correct even if the install
    # record is missing (idempotent uninstall). Best-effort — never
    # blocks package removal.
    try:
        await async_remove_cards(hass, slug)
    except Exception as exc:  # noqa: BLE001 — card cleanup must not abort uninstall
        _LOGGER.warning("Recipe %s: dashboard card cleanup failed: %s", slug, exc)

    # Remove the user-selected integration entries. We do this BEFORE
    # the core reload so HA processes the removals as part of the
    # same reload cycle.
    entry_punch: list[PunchItem] = []
    for entry_id in remove_entries or []:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            # Already gone — skip without complaining.
            continue
        try:
            await hass.config_entries.async_remove(entry_id)
        except Exception as exc:  # noqa: BLE001 — surface verbatim
            entry_punch.append(
                PunchItem(
                    stage="install",
                    code="integration_remove_failed",
                    message=(f"Couldn't remove {entry.domain} ({entry.title}): {exc}"),
                    target=entry.domain,
                )
            )

    # The package/record/bundle are already gone, so a reload failure here
    # doesn't un-remove anything — but surface it so the UI doesn't claim a
    # clean uninstall while HA is still running the old config.
    try:
        await async_reload_core_config(hass)
    except PackagerError as exc:
        entry_punch.append(
            PunchItem(
                stage="reload",
                code="reload_failed",
                message=str(exc),
            )
        )
    return PipelineResult(
        ok=not entry_punch,
        stage_reached="complete",
        punch_list=tuple(entry_punch),
        record=record,
    )


# ── Internal: shared install/preview flow ──────────────────────────


def _safe_emit(
    on_event: Callable[[dict[str, Any]], None] | None,
    payload: dict[str, Any],
) -> None:
    """Fire ``on_event`` and swallow anything it throws. The pipeline
    must never fail because the subscriber died — the install carries
    on regardless of whether anyone's listening.
    """
    if on_event is None:
        return
    try:
        on_event(payload)
    except Exception:  # noqa: BLE001 — subscriber-side errors are not our problem
        _LOGGER.debug("on_event subscriber raised; ignoring", exc_info=True)


async def _run(
    hass: HomeAssistant,
    *,
    slug: str,
    inputs: dict[str, Any] | None,
    selections: dict[str, list[str]] | None,
    write: bool,
    dashboard_target: str | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> PipelineResult:
    """Common path for preview and install. ``write=True`` flips the
    "actually touch disk" switch.
    """
    # ── Stage 1: definition ────────────────────────────────────
    try:
        bundle = await async_load_bundle(hass, slug)
    except ManifestError as exc:
        return PipelineResult(
            ok=False,
            stage_reached="definition",
            punch_list=(
                PunchItem(
                    stage="definition",
                    code="manifest_error",
                    message=str(exc),
                ),
            ),
        )

    # ── Stage 2: role resolution ───────────────────────────────
    resolution = resolve(bundle.manifest, hass, selections=selections)
    # Snapshot the wizard-facing role state once; every PipelineResult
    # built from here on includes it so the UI can refresh its toggle
    # set + selection-mode hints on every preview / install round-trip.
    selection_modes = {r.id: r.selection for r in bundle.manifest.roles}
    if not resolution.ok:
        punch_items: list[PunchItem] = []
        for fail in resolution.failures():
            # Each pending binding becomes its own punch entry with
            # the full device identity attached. Lets the wizard
            # render an "install this device" card per pin without
            # cross-referencing the resolution report separately.
            for pb in fail.pending:
                punch_items.append(
                    PunchItem(
                        stage="resolve",
                        code="binding_pending",
                        message=(f"waiting on {pb.binding.entity_id} for role {pb.role_id!r}"),
                        target=pb.role_id,
                        identity={
                            "entity_id": pb.binding.entity_id,
                            "device_class": pb.binding.device_class,
                            "manufacturer": pb.binding.manufacturer,
                            "model": pb.binding.model,
                            "integration": pb.binding.integration,
                            "identifier": pb.binding.identifier,
                            "note": pb.binding.note,
                        },
                    )
                )
            # Only emit a generic role_unmet item when there are no
            # pending pins (otherwise the per-pin items already cover
            # the failure shape).
            if not fail.pending:
                punch_items.append(
                    PunchItem(
                        stage="resolve",
                        code="role_unmet",
                        message=fail.reason,
                        target=fail.role.id,
                    )
                )
        return PipelineResult(
            ok=False,
            stage_reached="resolve",
            punch_list=tuple(punch_items),
            bindings=resolution.bindings,
            candidates=resolution.candidates,
            pinned=resolution.pinned,
            selection_modes=selection_modes,
        )

    # ── Stage 3a: run auto-resolvers ───────────────────────────
    # Inputs flagged with ``resolver:`` get their value computed
    # before validation (typically from hass.config + an external API
    # call). Resolver outputs override anything the user sent for
    # the same id — auto-resolved fields are hidden in the wizard.
    auto_inputs: dict[str, Any] = dict(inputs or {})
    try:
        await async_apply_auto_inputs(hass, bundle.manifest, auto_inputs)
    except ResolverError as exc:
        return PipelineResult(
            ok=False,
            stage_reached="validate",
            punch_list=(
                PunchItem(
                    stage="validate",
                    code="auto_input_failed",
                    message=str(exc),
                ),
            ),
            bindings=resolution.bindings,
            candidates=resolution.candidates,
            pinned=resolution.pinned,
            selection_modes=selection_modes,
        )

    # ── Stage 3b: validate inputs + integration prereqs ────────
    input_report: InputReport = validate_inputs(bundle.manifest, auto_inputs)
    integration_report: IntegrationReport = check_integrations(bundle.manifest, hass)
    if not input_report.ok or not integration_report.ok:
        punch: list[PunchItem] = []
        for issue in input_report.issues:
            punch.append(
                PunchItem(
                    stage="validate",
                    code="input_invalid",
                    message=issue.reason,
                    target=issue.input_id,
                )
            )
        for issue in integration_report.issues:
            punch.append(
                PunchItem(
                    stage="validate",
                    code="integration_missing",
                    message=issue.reason,
                    target=issue.domain,
                )
            )
        return PipelineResult(
            ok=False,
            stage_reached="validate",
            punch_list=tuple(punch),
            bindings=resolution.bindings,
            candidates=resolution.candidates,
            pinned=resolution.pinned,
            selection_modes=selection_modes,
        )

    # ── Stage 4: render package ────────────────────────────────
    if write:
        _safe_emit(on_event, {"type": "apply", "step": "render", "status": "running"})
    try:
        rendered = render_package(
            bundle=bundle,
            resolution=resolution,
            inputs=input_report.values or {},
        )
    except RenderError as exc:
        if write:
            _safe_emit(
                on_event,
                {
                    "type": "apply",
                    "step": "render",
                    "status": "failed",
                    "detail": str(exc),
                },
            )
        return PipelineResult(
            ok=False,
            stage_reached="render",
            punch_list=(
                PunchItem(
                    stage="render",
                    code="render_failed",
                    message=str(exc),
                ),
            ),
            bindings=resolution.bindings,
            candidates=resolution.candidates,
            pinned=resolution.pinned,
            selection_modes=selection_modes,
        )

    if not write:
        return PipelineResult(
            ok=True,
            stage_reached="render",
            preview=rendered,
            bindings=resolution.bindings,
            candidates=resolution.candidates,
            pinned=resolution.pinned,
            selection_modes=selection_modes,
        )

    _safe_emit(on_event, {"type": "apply", "step": "render", "status": "ok"})

    # ── Stage 5: install (disk write) ──────────────────────────
    _safe_emit(on_event, {"type": "apply", "step": "write", "status": "running"})
    try:
        # configuration.yaml include first: if the include is missing
        # AND we manage to write the package, HA wouldn't load it on
        # reload. Doing the include first means a half-failure leaves
        # the home in a state HA understands.
        await hass.async_add_executor_job(ensure_packages_include, hass)
        target = await hass.async_add_executor_job(write_package, hass, slug, rendered.yaml_text)
    except PackagerError as exc:
        _safe_emit(
            on_event,
            {
                "type": "apply",
                "step": "write",
                "status": "failed",
                "detail": str(exc),
            },
        )
        return PipelineResult(
            ok=False,
            stage_reached="install",
            punch_list=(
                PunchItem(
                    stage="install",
                    code="package_write_failed",
                    message=str(exc),
                ),
            ),
            bindings=resolution.bindings,
            candidates=resolution.candidates,
            pinned=resolution.pinned,
            selection_modes=selection_modes,
            preview=rendered,
        )

    _safe_emit(
        on_event,
        {
            "type": "apply",
            "step": "write",
            "status": "ok",
            "detail": str(target),
        },
    )

    # ── Stage 6: reload ────────────────────────────────────────
    _safe_emit(on_event, {"type": "apply", "step": "reload", "status": "running"})
    try:
        await async_reload_core_config(hass)
    except Exception as exc:  # noqa: BLE001 — HA service call can fail in many shapes
        _safe_emit(
            on_event,
            {
                "type": "apply",
                "step": "reload",
                "status": "failed",
                "detail": str(exc),
            },
        )
        # The package IS on disk. The reload failure is a signal but not
        # a rollback trigger — the user can reload manually. Surface as
        # a non-fatal advisory in the punch list.
        _LOGGER.warning("Reload of core config failed: %s", exc)
        return PipelineResult(
            ok=False,
            stage_reached="reload",
            punch_list=(
                PunchItem(
                    stage="reload",
                    code="reload_failed",
                    message=(
                        f"package written to {target} but HA reload failed: "
                        f"{exc}. Reload Home Assistant from Settings to activate."
                    ),
                ),
            ),
            bindings=resolution.bindings,
            candidates=resolution.candidates,
            pinned=resolution.pinned,
            selection_modes=selection_modes,
            preview=rendered,
        )

    _safe_emit(on_event, {"type": "apply", "step": "reload", "status": "ok"})

    # ── Stage 6b: dashboard card (optional, non-fatal) ─────────
    # Deterministic Lovelace insertion — the recipe's "add the toggle to
    # your dashboard" step. The package is already live, so a failure
    # here (YAML-mode dashboard, etc.) is a soft advisory, never a
    # rollback. Runs after reload so any helper the card points at has
    # materialised in the state machine.
    dashboard_card: dict[str, Any] = {}
    if bundle.manifest.dashboard is not None and dashboard_target != SKIP_TARGET:
        # User's picker choice (a url_path) overrides the manifest's
        # declared target; ``None`` keeps the manifest default.
        spec = bundle.manifest.dashboard
        if dashboard_target is not None:
            spec = replace(spec, target=dashboard_target)
        _safe_emit(on_event, {"type": "apply", "step": "dashboard", "status": "running"})
        insert = await async_insert_card(
            hass,
            slug=slug,
            spec=spec,
            bindings=resolution.bindings,
            inputs=input_report.values or {},
        )
        dashboard_card = {
            "ok": insert.ok,
            "reason": insert.reason,
            "target": insert.target,
            "view": insert.view,
        }
        _safe_emit(
            on_event,
            {
                "type": "apply",
                "step": "dashboard",
                # Not ok → "skipped", not "failed": the install succeeded,
                # the card is just a fall-back-to-manual advisory.
                "status": "ok" if insert.ok else "skipped",
                "detail": insert.message,
            },
        )

    # ── Persist record + done ──────────────────────────────────
    _safe_emit(on_event, {"type": "apply", "step": "verify", "status": "running"})
    # Pull any integrations the auto_setup WS created during the
    # wizard's Match step. Stored under hass.data by slug; drained
    # here so re-installing doesn't leak stale ownership claims.
    owned_map: dict[str, dict[str, str]] = hass.data.get("selora_ai", {}).get(
        "_auto_setup_owned", {}
    )
    integrations_installed = dict(owned_map.pop(slug, {}))
    store = get_install_store(hass)
    record = await store.async_record(
        slug=slug,
        version=bundle.manifest.version,
        title=bundle.manifest.title,
        package_path=str(target),
        bindings=resolution.bindings,
        inputs=input_report.values or {},
        integrations_installed=integrations_installed,
        dashboard_card=dashboard_card,
    )
    _safe_emit(on_event, {"type": "apply", "step": "verify", "status": "ok"})
    return PipelineResult(
        ok=True,
        stage_reached="complete",
        preview=rendered,
        record=record,
        bindings=resolution.bindings,
        candidates=resolution.candidates,
        pinned=resolution.pinned,
        selection_modes=selection_modes,
    )
