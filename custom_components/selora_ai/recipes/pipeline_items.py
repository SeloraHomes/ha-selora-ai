"""Pipeline → wizard items.

The wizard renders the install as a three-column pipeline (Prepare /
Configure / Apply) where each cell is one ``PipelineItem``. The
backend derives the full item list from a ``PipelineResult`` so the
frontend never has to re-implement "is this role still pending?"
logic — every cell's status comes from one place.

Item identity is stable across preview calls so the frontend can
diff: if ``configure/role:bedroom_lights`` was ``needs_input`` on the
last preview and is ``ok`` on the next, the cell just flips colour.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .manifest import Manifest
    from .pipeline import PipelineResult


Stage = Literal["prepare", "configure", "apply"]
Status = Literal["pending", "running", "ok", "failed", "skipped", "needs_input"]
ItemKind = Literal[
    # Backend-only steps the user can't interact with.
    "system",
    # User-supplied form values.
    "inputs",
    # An integration the recipe needs; opens config flow inline.
    "integration",
    # A manifest pin waiting on a device pair.
    "pin",
    # A role with selection: required.
    "role_selection",
]


@dataclass(frozen=True, slots=True)
class PipelineItem:
    """One row in the wizard's pipeline view.

    Attributes:
        id: Stable identifier the frontend uses as a key. Shape:
            ``<stage>/<kind>:<scope>``.
        stage: ``prepare`` / ``configure`` / ``apply``.
        title: Short label shown in the column.
        status: Current state — drives the icon + colour.
        kind: Tells the action panel which UI to render when the user
            clicks this row.
        detail: Optional supporting text rendered under the title.
        payload: Item-specific data (role id, integration domain, pin
            identity, candidates list, …). Opaque from this module's
            POV — each ``kind`` defines its shape.
    """

    id: str
    stage: Stage
    title: str
    status: Status
    kind: ItemKind
    detail: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Derivation ─────────────────────────────────────────────────────


def derive_items(
    manifest: Manifest,
    result: PipelineResult,
    *,
    integrations_loaded: set[str],
    integration_titles: dict[str, str] | None = None,
) -> list[PipelineItem]:
    """Turn a preview ``PipelineResult`` into the full item list.

    ``integrations_loaded`` is the set of HA component domains that
    have at least one config entry — used to decide which integration
    items are already ``ok`` vs need a config flow.

    The order matches what the wizard shows: prepare items first
    (always present), then configure items (in manifest order), then
    apply items (always present, all ``pending`` until install runs).
    """
    items: list[PipelineItem] = []

    # ── Prepare stage ────────────────────────────────────────
    # These all reflect work the resolver already did. By the time
    # the frontend renders a preview, the manifest has been parsed
    # and roles have been resolved — so these are almost always ``ok``
    # unless the manifest itself was malformed.
    manifest_ok = result.stage_reached != "definition"
    items.append(
        PipelineItem(
            id="prepare/manifest",
            stage="prepare",
            kind="system",
            title="Recipe loaded",
            status="ok" if manifest_ok else "failed",
            detail=(
                f"{manifest.title} v{manifest.version}"
                if manifest_ok
                else _first_punch(result, "definition")
            ),
        )
    )

    # Role resolution: ok if the resolve stage cleared. When a role
    # has pending pins, those become Configure items — Prepare just
    # reflects "we ran the resolver against the home."
    resolve_ran = result.stage_reached not in ("definition",)
    items.append(
        PipelineItem(
            id="prepare/resolve",
            stage="prepare",
            kind="system",
            title="Home scanned",
            status="ok" if resolve_ran else "pending",
            detail=_resolve_detail(manifest, result),
        )
    )

    items.append(
        PipelineItem(
            id="prepare/inputs",
            stage="prepare",
            kind="system",
            title="Inputs validated",
            status=("ok" if result.stage_reached not in ("definition", "resolve") else "pending"),
        )
    )

    # ── Configure stage ──────────────────────────────────────
    # 1. Inputs as a single row (when the manifest declares any).
    # Auto-resolved inputs (``resolver:`` set) are hidden from the
    # wizard — they're computed by the pipeline before render, not
    # asked from the user.
    user_inputs = [i for i in manifest.inputs if not i.resolver]
    if user_inputs:
        bad_inputs = {p.target for p in result.punch_list if p.code == "input_invalid" and p.target}
        if bad_inputs:
            inputs_status: Status = "needs_input"
            inputs_detail = (
                f"{len(bad_inputs)} field{'s' if len(bad_inputs) != 1 else ''} need attention"
            )
        else:
            inputs_status = "ok"
            inputs_detail = f"{len(user_inputs)} setting{'s' if len(user_inputs) != 1 else ''}"
        items.append(
            PipelineItem(
                id="configure/inputs",
                stage="configure",
                kind="inputs",
                title="Settings",
                status=inputs_status,
                detail=inputs_detail,
                payload={
                    "inputs": [
                        {
                            "id": i.id,
                            "type": i.type,
                            "label": i.label,
                            "description": i.description,
                            "default": i.default,
                            "required": i.required,
                            "min": i.min,
                            "max": i.max,
                            "choices": list(i.choices),
                        }
                        for i in user_inputs
                    ],
                },
            )
        )

    # 2. One row per declared integration. ``ok`` when the domain has
    # at least one config entry loaded; ``needs_input`` otherwise.
    titles = integration_titles or {}
    for integration in manifest.integrations:
        loaded = integration.domain in integrations_loaded
        entry_title = titles.get(integration.domain) if loaded else None
        # Prefer the config entry's title (NWS sets it to its lat/lon
        # string, Hue to the bridge IP, etc.) so the user sees a concrete
        # confirmation of what got set up — not just a generic
        # "configured" badge.
        detail = (entry_title or "Configured") if loaded else "Needs setup"
        items.append(
            PipelineItem(
                id=f"configure/integration:{integration.domain}",
                stage="configure",
                kind="integration",
                title=integration.title or integration.domain,
                status="ok" if loaded else "needs_input",
                detail=detail,
                payload={
                    "domain": integration.domain,
                    "config_url": integration.config_url,
                    # ``True`` when the manifest declares auto_setup
                    # for this integration; the wizard's "Set up" UI
                    # uses this to switch from "open inline form" to
                    # "one-click backend orchestration."
                    "auto_setup": integration.auto_setup is not None,
                    "entry_title": entry_title,
                },
            )
        )

    # 3. One row per pending pinned binding (the device pair flow).
    # Plus the per-role selection row when the role has open
    # candidates to pick.
    for role in manifest.roles:
        pending = [
            p for p in result.punch_list if p.code == "binding_pending" and p.target == role.id
        ]
        for p in pending:
            ident = p.identity or {}
            items.append(
                PipelineItem(
                    id=f"configure/pin:{role.id}:{ident.get('entity_id', '')}",
                    stage="configure",
                    kind="pin",
                    title=(ident.get("note") or "").strip()
                    or _humanise(ident.get("entity_id", "")),
                    status="needs_input",
                    detail=_pin_detail(ident),
                    payload={
                        "role_id": role.id,
                        "identity": ident,
                    },
                )
            )

        # Selection row only when the manifest requires the user to
        # pick (selection: required). Auto roles bind silently —
        # they show up implicitly under prepare/resolve.
        if role.selection == "required":
            candidates = result.candidates.get(role.id, [])
            pinned = result.pinned.get(role.id, [])
            bound = result.bindings.get(role.id, [])
            # ``needs_input`` when this role has open candidates that
            # still need a pick; ``ok`` when the bound count is in
            # range and there are no open candidates left to choose
            # from; ``skipped`` when the role is optional and the
            # user picked nothing.
            unmet = any(p.code == "role_unmet" and p.target == role.id for p in result.punch_list)
            optional = role.min_count == 0
            if unmet:
                status: Status = "needs_input"
                detail = f"Pick at least {role.min_count} of {len(candidates) + len(pinned)}"
            elif optional and not bound:
                status = "skipped"
                detail = "Not used"
            elif bound:
                status = "ok"
                detail = f"{len(bound)} selected"
            else:
                status = "needs_input"
                detail = "Pick devices to include"
            items.append(
                PipelineItem(
                    id=f"configure/role:{role.id}",
                    stage="configure",
                    kind="role_selection",
                    title=role.title or _humanise(role.id),
                    status=status,
                    detail=detail,
                    payload={
                        "role": {
                            "id": role.id,
                            "kind": role.kind,
                            "description": role.description,
                            "min_count": role.min_count,
                            "max_count": role.max_count,
                        },
                        "candidates": candidates,
                        "pinned": pinned,
                        "bound": bound,
                    },
                )
            )

    # ── Apply stage ──────────────────────────────────────────
    # All four steps stay ``pending`` in the preview; the install WS
    # subscription flips them as it runs them.
    apply_baseline: Status = "ok" if result.stage_reached == "complete" else "pending"
    apply_steps = [
        ("render", "Render package"),
        ("write", "Write to disk"),
        ("reload", "Reload Home Assistant"),
    ]
    # Optional dashboard card step — only shown when the recipe declares
    # a ``dashboard:`` block. Sits after reload (the helper it points at
    # has to exist first) and before the final verify.
    if manifest.dashboard is not None:
        apply_steps.append(("dashboard", "Add card to dashboard"))
    apply_steps.append(("verify", "Verify"))
    for step, label in apply_steps:
        items.append(
            PipelineItem(
                id=f"apply/{step}",
                stage="apply",
                kind="system",
                title=label,
                status=apply_baseline,
            )
        )

    return items


# ── Helpers ────────────────────────────────────────────────────────


def _humanise(s: str) -> str:
    """``light.bedroom_lamp`` → ``Bedroom lamp``."""
    if not s:
        return ""
    obj = s.split(".", 1)[1] if "." in s else s
    spaced = obj.replace("_", " ").strip()
    return spaced[:1].upper() + spaced[1:] if spaced else s


def _first_punch(result: PipelineResult, stage: str) -> str:
    for p in result.punch_list:
        if p.stage == stage:
            return p.message
    return ""


def _resolve_detail(manifest: Manifest, result: PipelineResult) -> str:
    """One-line summary of role resolution for the Prepare column.

    Example: "3 roles satisfied · 1 needs a device · 2 optional skipped"
    """
    satisfied = 0
    needs_device = 0
    needs_pick = 0
    optional_skipped = 0
    for role in manifest.roles:
        bound_count = len(result.bindings.get(role.id, []))
        pending = any(
            p.code == "binding_pending" and p.target == role.id for p in result.punch_list
        )
        unmet = any(p.code == "role_unmet" and p.target == role.id for p in result.punch_list)
        if pending:
            needs_device += 1
        elif unmet:
            needs_pick += 1
        elif role.min_count == 0 and bound_count == 0:
            optional_skipped += 1
        else:
            satisfied += 1
    parts = [f"{satisfied} satisfied"]
    if needs_device:
        parts.append(f"{needs_device} waiting on device")
    if needs_pick:
        parts.append(f"{needs_pick} need a pick")
    if optional_skipped:
        parts.append(f"{optional_skipped} optional skipped")
    return " · ".join(parts)


def _pin_detail(identity: dict[str, Any]) -> str:
    """Short device-identity line shown under a pin's title.

    Example: "Signify Hue Color A19 · via Philips Hue".
    """
    bits: list[str] = []
    name = " ".join(x for x in (identity.get("manufacturer"), identity.get("model")) if x).strip()
    if name:
        bits.append(name)
    if identity.get("integration"):
        bits.append(f"via {identity['integration']}")
    return " · ".join(bits)
