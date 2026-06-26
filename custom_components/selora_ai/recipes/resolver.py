"""Role resolution — match each manifest role against the live HA registry.

Deterministic matchmaking, no LLM. A role declares what it needs
(domain, device_class, capability features, count bounds); the resolver
walks the entity registry and produces a concrete entity_id list per
role. If the user's home doesn't carry enough matches for the role's
``min_count``, the role fails — the pipeline halts at the next stage
and the user gets a "you need N more X" punch-list entry.

This is the part of the install that Connect can dry-run against a
remote snapshot of the customer's home before D-Day: same resolver, same
result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .manifest import BindingSpec, Manifest, RoleSpec

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


# ── Resolution result types ─────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PendingBinding:
    """One manifest-pinned entity that isn't present in HA yet.

    Surfaced to the wizard so the field tech sees a "waiting on this
    device" card with whatever identity hints the manifest carries
    (manufacturer, model, integration, vendor id). Once the device
    pairs and lands at the expected entity_id, Re-check moves it from
    pending → bound.
    """

    role_id: str
    binding: BindingSpec


@dataclass(frozen=True, slots=True)
class PinnedBinding:
    """A manifest-pinned entity that IS present and matches the role's
    filter. Locked to the role — the wizard renders it as a non-
    toggleable chip with a lock icon.
    """

    role_id: str
    entity_id: str
    binding: BindingSpec


@dataclass(frozen=True, slots=True)
class RoleResolution:
    """Outcome of resolving one role against the home.

    Three sets:

    - ``candidates``: every entity in HA that matches the role's
      filter (kind/device_class/features). What the wizard offers as
      checkboxes for unpinned slots. Empty when the home doesn't
      carry anything that fits.
    - ``pinned``: manifest-declared bindings that resolved to a real
      entity matching the role filter. Locked in; not user-toggleable.
    - ``pending``: manifest-declared bindings whose entity isn't
      present in HA (yet) or doesn't match the role filter. Each one
      becomes a punch-list entry; install gated until they resolve.
    - ``selected``: the subset (pinned ∪ user-picked-from-candidates)
      that will reach the renderer.

    Attributes:
        role: The original spec, kept here so the UI/punch list can
            render the role's description without a second lookup.
        candidates: Entities that match the role filter and aren't
            pre-pinned by the manifest.
        pinned: Manifest pins that resolved cleanly.
        pending: Manifest pins still waiting on pair / missing.
        selected: The final bound subset.
        candidates_considered: How many entities the resolver looked at
            before filtering. Useful when debugging "why didn't this
            match" — the UI can show this when a role failed.
        ok: True when (a) every pin resolved AND (b) len(selected)
            satisfies the role's bounds.
        reason: Empty when ``ok`` is True; otherwise a single-sentence
            explanation suitable for the punch list. For pending pins
            the wizard prefers ``pending`` over this — it can render
            the device identity card from the BindingSpec.
    """

    role: RoleSpec
    candidates: tuple[str, ...]
    selected: tuple[str, ...]
    candidates_considered: int
    ok: bool
    reason: str = ""
    pinned: tuple[PinnedBinding, ...] = ()
    pending: tuple[PendingBinding, ...] = ()

    # Back-compat alias: a couple of existing call sites still read
    # ``entity_ids`` as "what will be bound." Point them at ``selected``
    # so nothing reads the old "matches everything" semantics by
    # accident. Templates only ever see ``selected`` via
    # ``ResolutionReport.bindings``.
    @property
    def entity_ids(self) -> tuple[str, ...]:
        return self.selected


@dataclass(frozen=True, slots=True)
class ResolutionReport:
    """Aggregate over every role in the manifest. ``ok`` is True iff
    every role's ``ok`` is True.
    """

    roles: tuple[RoleResolution, ...]
    ok: bool

    @property
    def bindings(self) -> dict[str, list[str]]:
        """Compact ``{role_id: [entity_id, ...]}`` view for the
        renderer. Always returns the SELECTED subset, never the full
        candidate list — the package only includes what the user
        actually chose.
        """
        return {r.role.id: list(r.selected) for r in self.roles}

    @property
    def candidates(self) -> dict[str, list[str]]:
        """``{role_id: [entity_id, ...]}`` of every match the resolver
        considered. The wizard renders these as the toggle options for
        ``selection: required`` roles. Excludes pinned entities —
        those render as locked chips, not toggle options.
        """
        return {r.role.id: list(r.candidates) for r in self.roles}

    @property
    def pinned(self) -> dict[str, list[str]]:
        """``{role_id: [entity_id, ...]}`` for resolved manifest pins.
        Wizard renders these as non-toggleable rows with a lock icon.
        """
        return {r.role.id: [p.entity_id for p in r.pinned] for r in self.roles}

    def failures(self) -> list[RoleResolution]:
        """Roles that didn't resolve, in manifest order."""
        return [r for r in self.roles if not r.ok]

    def pending_bindings(self) -> list[PendingBinding]:
        """Flat list of pinned-but-missing bindings across every role.
        The wizard pulls device identity cards from these.
        """
        out: list[PendingBinding] = []
        for r in self.roles:
            out.extend(r.pending)
        return out


# ── Internal helpers ────────────────────────────────────────────────


# Feature → predicate. Each predicate inspects the entity's attributes
# and returns True if the entity supports that capability. Keep the set
# small and well-defined; the manifest schema already validates the
# feature strings, so unknown features won't get this far.
_COLOR_MODES_WITH_COLOR: frozenset[str] = frozenset({"hs", "rgb", "rgbw", "rgbww", "xy"})


def _entity_supports_feature(feature: str, attributes: dict | None) -> bool:
    """Return True when ``attributes`` indicates the entity supports
    ``feature``. ``attributes`` may be None (entity registered but never
    seen a state) — in that case features can't be verified and we treat
    it as "doesn't qualify" so a strict role doesn't bind to a phantom.
    """
    if not attributes:
        return False
    if feature == "color":
        modes = attributes.get("supported_color_modes") or []
        return bool(set(modes) & _COLOR_MODES_WITH_COLOR)
    # Unknown features should have been rejected at manifest load,
    # but if one slipped through, treat it as a non-match — we'd
    # rather fail-close than silently bind anything.
    return False


def _resolve_one(
    role: RoleSpec,
    hass: HomeAssistant,
    user_selection: list[str] | None,
    role_bindings: tuple[BindingSpec, ...],
) -> RoleResolution:
    """Walk hass.states for candidate entities matching this role's
    filters, apply manifest pins, narrow to the user's selection.

    The state machine is the source of truth for live attributes
    (domain/device_class + capability flags). Entity registry has
    stable metadata but not live attribute values, so we read both
    for the strictest matching.

    Resolution order:

    1. Build the candidate set: every entity that fits the role's
       kind/device_class/features filter.
    2. Walk the manifest's pinned bindings for this role:
       - present + matches filter → ``pinned`` (locked-bound).
       - present + doesn't match  → ``pending`` (recipe author error).
       - missing                  → ``pending`` (device not paired yet).
    3. ``selection: auto``: every non-pinned candidate auto-binds.
       ``selection: required``: the user picks from candidates
       *minus* pinned (pinned slots are already filled).
    4. ``ok`` requires every pin to resolve AND the bound count to
       satisfy min/max bounds.
    """
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    ent_reg = er.async_get(hass)
    candidates: list[str] = []
    considered = 0
    for state in hass.states.async_all(role.kind):
        considered += 1
        if not _entity_satisfies_role(role, state, ent_reg):
            continue
        candidates.append(state.entity_id)

    candidates.sort()  # deterministic ordering — same home, same package
    candidates_t = tuple(candidates)
    candidate_set = set(candidates_t)

    # ── Apply manifest pins ────────────────────────────────────
    pinned: list[PinnedBinding] = []
    pending: list[PendingBinding] = []
    pinned_ids: set[str] = set()
    for b in role_bindings:
        if b.entity_id in candidate_set:
            pinned.append(PinnedBinding(role_id=role.id, entity_id=b.entity_id, binding=b))
            pinned_ids.add(b.entity_id)
        else:
            # Two reasons we land here:
            #   a) the entity doesn't exist yet — most common for
            #      D-Day installation manifests.
            #   b) entity exists but doesn't match the role filter
            #      (wrong domain/device_class/capability) — the
            #      recipe author paired the wrong device class.
            # Both surface as a pending punch entry; the wizard
            # renders the device-identity card.
            pending.append(PendingBinding(role_id=role.id, binding=b))

    # Candidates the wizard offers for selection = matches minus pinned.
    # Pinned chips render separately as locked rows; the toggle list
    # shouldn't double-count them.
    open_candidates = tuple(c for c in candidates_t if c not in pinned_ids)
    open_set = set(open_candidates)

    # ── Selection ──────────────────────────────────────────────
    if role.selection == "required":
        chosen = [e for e in (user_selection or []) if e in open_set]
    else:
        chosen = list(open_candidates)

    # Pinned + chosen, deduplicated, capped at max_count. Pins win
    # priority — the manifest author's intent ranks above user
    # additions when the cap forces a drop.
    bound: list[str] = []
    seen: set[str] = set()
    for pb in pinned:
        if pb.entity_id not in seen:
            bound.append(pb.entity_id)
            seen.add(pb.entity_id)
    for e in chosen:
        if e not in seen:
            bound.append(e)
            seen.add(e)
    if role.max_count is not None and len(bound) > role.max_count:
        bound = bound[: role.max_count]

    bound_t = tuple(bound)

    # ── OK gate ────────────────────────────────────────────────
    pin_failure = bool(pending)
    count = len(bound_t)
    too_few = count < role.min_count

    if pin_failure or too_few:
        if pin_failure:
            # When pins are pending, that's the headline failure for
            # the punch list — the wizard renders an identity card
            # from ``pending`` separately, but we still set a short
            # ``reason`` for log lines and tests.
            first = pending[0].binding
            label = first.entity_id
            extras: list[str] = []
            if first.manufacturer or first.model:
                extras.append(f"{first.manufacturer or ''} {first.model or ''}".strip())
            if first.integration:
                extras.append(f"via {first.integration}")
            extra_str = f" ({', '.join(extras)})" if extras else ""
            reason = f"waiting on {label}{extra_str}"
            if len(pending) > 1:
                reason += f" — and {len(pending) - 1} more"
        else:
            reason = _format_count_failure(
                role, len(candidates_t), considered, selected_count=count
            )
        return RoleResolution(
            role=role,
            candidates=open_candidates,
            selected=bound_t,
            candidates_considered=considered,
            ok=False,
            reason=reason,
            pinned=tuple(pinned),
            pending=tuple(pending),
        )
    return RoleResolution(
        role=role,
        candidates=open_candidates,
        selected=bound_t,
        candidates_considered=considered,
        ok=True,
        pinned=tuple(pinned),
        pending=tuple(pending),
    )


def _entity_satisfies_role(role: RoleSpec, state: Any, ent_reg: Any) -> bool:
    """Apply the role's kind/device_class/features filter to one HA
    state. Lifted out of ``_resolve_one`` so the pin-presence test
    uses the same predicate as the candidate scan. ``state`` and
    ``ent_reg`` are HA's State + EntityRegistry; typed as Any here
    to avoid the import dance at module load time.
    """
    attrs = state.attributes
    if role.device_class:
        dc = attrs.get("device_class")
        if dc != role.device_class:
            ent = ent_reg.async_get(state.entity_id)
            if not ent or ent.original_device_class != role.device_class:
                return False
    return not any(not _entity_supports_feature(f, attrs) for f in role.features)


def _format_count_failure(
    role: RoleSpec,
    found: int,
    considered: int,
    *,
    selected_count: int | None = None,
) -> str:
    """Human-sentence describing why a role didn't resolve. Goes into
    the punch list verbatim, so make it actionable.

    Two failure shapes:

    - ``selection == "auto"`` (or ``selection_count is None``): the home
      doesn't carry enough matches — actionable as "install more
      moisture sensors".
    - ``selection == "required"`` with too few picked: candidates exist
      but the user hasn't ticked enough — actionable as "select N
      lights in the wizard".
    """
    filter_summary = role.kind
    if role.device_class:
        filter_summary += f", device_class={role.device_class}"
    if role.features:
        filter_summary += f", features=[{', '.join(role.features)}]"
    need = role.min_count
    needed = f"{need} {'or more ' if role.max_count != need else ''}".rstrip()
    # User-selection shortfall (candidates exist, the user just needs to pick more).
    if role.selection == "required" and selected_count is not None and found > 0:
        return (
            f"select at least {need} of {found} candidate "
            f"{role.kind} entit{'y' if found == 1 else 'ies'} for "
            f"role {role.id!r} (currently selected: {selected_count})"
        )
    if found == 0:
        return (
            f"need {need} matching {role.kind}{' with ' + role.device_class if role.device_class else ''}, "
            f"found none ({considered} {role.kind} entities considered)"
        )
    return (
        f"need {needed}({filter_summary}); only {found} match "
        f"({considered} {role.kind} entities considered)"
    )


# ── Public API ──────────────────────────────────────────────────────


def resolve(
    manifest: Manifest,
    hass: HomeAssistant,
    *,
    selections: dict[str, list[str]] | None = None,
) -> ResolutionReport:
    """Resolve every role in the manifest against the current HA state.

    ``selections`` is the wizard's per-role pick for ``selection:
    required`` roles. Keys are role ids; values are lists of
    entity_ids the user wants bound. ``selection: auto`` roles ignore
    this argument and take all candidates.

    Pure function over (manifest, live HA state, selections) — call
    it as many times as you like; same inputs, same output. The
    pipeline calls this twice per install: once for the preview the
    user reviews in the wizard, then again right before rendering, in
    case the home changed between preview and install.
    """
    sel = selections or {}
    results = tuple(
        _resolve_one(
            role,
            hass,
            sel.get(role.id),
            manifest.bindings.get(role.id, ()),
        )
        for role in manifest.roles
    )
    overall_ok = all(r.ok for r in results)
    return ResolutionReport(roles=results, ok=overall_ok)
