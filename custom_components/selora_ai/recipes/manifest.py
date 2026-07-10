"""Recipe manifest schema + loader.

A manifest describes everything the pipeline needs to know about a
recipe BEFORE looking at the user's home:

- identity (slug, version, title, description, author)
- the roles the recipe needs filled (entity selectors)
- the inputs it expects the user to supply
- the integrations it depends on (informational; checked at validate time)
- which template files inside ``package/`` to render

Manifests are pure data — no behaviour, no Jinja, no logic. The recipe
author opens a YAML file, fills it in, and the pipeline takes it from
there. Strict validation runs once at load time so a malformed bundle
fails on disk parse, not three stages into the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Literal

import yaml

from .version_gate import _release_tuple

# ── Errors ──────────────────────────────────────────────────────────


class ManifestError(Exception):
    """Raised when a manifest fails to load, parse, or validate.

    The exception message is treated as user-visible — keep it short and
    actionable. The pipeline surfaces these as the "Recipe definition"
    stage's failure entries in the punch list.
    """


# ── Role spec ───────────────────────────────────────────────────────


_ROLE_COUNT_MIN = 0
_ROLE_COUNT_MAX = 256  # belt-and-braces; no real recipe needs more

_VALID_KINDS: frozenset[str] = frozenset(
    {
        "binary_sensor",
        "sensor",
        "light",
        "switch",
        "cover",
        "lock",
        "media_player",
        "climate",
        "fan",
        "vacuum",
        "camera",
        "siren",
        "input_boolean",
        "input_number",
        "input_text",
        "input_select",
        "person",
        "device_tracker",
        "zone",
    }
)


@dataclass(frozen=True, slots=True)
class RoleSpec:
    """Declarative description of one role the recipe needs to bind.

    Resolution is matchmaking, not negotiation: a role either resolves
    to a concrete list of entity_ids (between ``min_count`` and
    ``max_count``) or the pipeline halts and the user sees a "need more
    of X" punch-list item. There's no LLM in the loop deciding what's
    "close enough."

    Attributes:
        id: Stable identifier the templates reference (e.g.
            ``leak_sensors``). Must be a python-identifier shape so it
            plays nicely with Jinja.
        kind: HA domain the entity must belong to (``binary_sensor``,
            ``light``, ``cover``, ...).
        device_class: Optional HA device_class filter (e.g. ``moisture``,
            ``window``). Many domains overload entities — without this
            a ``binary_sensor`` role would match doorbells, leaks, and
            motion alike.
        integration: Optional integration (platform) filter, e.g.
            ``lg_thinq``. When set, only entities owned by that
            integration in the registry match. Use it for
            device-specific recipes where ``kind`` + ``device_class``
            is still too broad — an LG fridge door and a stick-on
            contact sensor both register as ``binary_sensor`` /
            ``door``, but only the fridge's is provided by
            ``lg_thinq``. Entities with no registry entry (so no known
            platform) never match a role that sets this.
        match: Optional case-insensitive regex, tested against both the
            entity_id and the friendly name. When set, only entities
            matching it qualify. Use it to pin a role to one entity
            among an integration's many of the same domain — e.g. an
            LG fridge exposes several ``sensor`` entities (water
            filter, fresh-air filter, ...), and ``water[ _]filter$``
            narrows to just the water-filter status. Matching name OR
            entity_id keeps it working if the homeowner renamed one.
        features: Optional list of capability strings the matched
            entity must support. Currently supports ``color`` (light
            must advertise an HS / RGB / RGBW / RGBWW colour mode).
        min_count / max_count: Inclusive bounds on how many entities
            must match. ``min_count=0`` makes the role optional; if
            nothing matches the pipeline doesn't fail, the templates
            just see an empty list. ``max_count`` of ``None`` means no
            upper bound — take everything that matches.
        description: Plain-prose explainer for the role. Shown under
            the role title in the wizard's right rail.
        title: Human-readable label for the role, e.g. ``"Leak
            sensor"`` instead of the python-identifier ``leak_sensors``.
            Falls back to humanised ``id`` (underscores → spaces,
            title-case) when omitted so authors aren't forced to
            duplicate the id.
        selection: ``"auto"`` (default) — every matched entity binds
            without asking. ``"required"`` — the wizard surfaces the
            candidates as toggles and the user must pick which to
            include. Use ``"required"`` when "all matches" is too
            broad an assumption (e.g. "bedroom lights" shouldn't grab
            every light in the home); use ``"auto"`` for the
            "all-of-them" cases (every moisture sensor, every cover).
    """

    id: str
    kind: str
    device_class: str | None = None
    integration: str | None = None
    match: str | None = None
    features: tuple[str, ...] = ()
    min_count: int = 1
    max_count: int | None = None
    description: str = ""
    title: str = ""
    selection: Literal["auto", "required"] = "auto"

    def validate(self) -> None:
        """Reject obviously-wrong specs at load time. Run once per role
        during manifest parse — we'd rather error on disk than render a
        garbage package.
        """
        if not self.id.isidentifier():
            raise ManifestError(
                f"role.id {self.id!r} must be a Python-identifier (letters, digits, underscore; not starting with a digit)"
            )
        if self.kind not in _VALID_KINDS:
            raise ManifestError(
                f"role {self.id!r}: unknown kind {self.kind!r} "
                f"(expected one of: {', '.join(sorted(_VALID_KINDS))})"
            )
        # HA integration/platform domains are lowercase [a-z0-9_] strings
        # that may start with a digit (e.g. 17track, 3_day_blinds) but
        # always carry at least one letter. Reject uppercase, punctuation,
        # and purely-numeric values the registry-platform match can't hit.
        if self.integration is not None and not re.fullmatch(
            r"[a-z0-9_]*[a-z][a-z0-9_]*", self.integration
        ):
            raise ManifestError(
                f"role {self.id!r}: integration {self.integration!r} must be a lowercase "
                "domain-shaped string (e.g. 'lg_thinq')"
            )
        # ``match`` must be a compilable regex — catch a bad pattern on disk
        # rather than throwing deep in the resolver's candidate scan.
        if self.match is not None:
            try:
                re.compile(self.match)
            except re.error as exc:
                raise ManifestError(
                    f"role {self.id!r}: match {self.match!r} is not a valid regex ({exc})"
                ) from exc
        if self.min_count < _ROLE_COUNT_MIN:
            raise ManifestError(f"role {self.id!r}: min_count={self.min_count} cannot be negative")
        if self.max_count is not None:
            if self.max_count < self.min_count:
                raise ManifestError(
                    f"role {self.id!r}: max_count={self.max_count} < min_count={self.min_count}"
                )
            if self.max_count > _ROLE_COUNT_MAX:
                raise ManifestError(
                    f"role {self.id!r}: max_count={self.max_count} exceeds cap {_ROLE_COUNT_MAX}"
                )
        if self.selection not in ("auto", "required"):
            raise ManifestError(
                f"role {self.id!r}: selection must be 'auto' or 'required', got {self.selection!r}"
            )


# ── Input spec ──────────────────────────────────────────────────────


InputType = Literal["string", "number", "boolean", "select"]

_VALID_INPUT_TYPES: frozenset[str] = frozenset({"string", "number", "boolean", "select"})


@dataclass(frozen=True, slots=True)
class InputSpec:
    """Schema for one user-supplied form field.

    Inputs are the "human dials" of a recipe — anything the BOM can't
    auto-resolve. The wizard renders these as a form before the role
    resolution preview; defaults fill in so the user can install with
    one click if the recipe doesn't need ceremony.

    Attributes:
        id: Identifier the templates reference (``inputs.bedtime``).
            Must be a python identifier.
        type: One of ``string``, ``number``, ``boolean``, ``select``.
        label: Field label shown to the user.
        description: Optional helper text shown beneath the field.
        default: Default value used when the user doesn't override.
        required: When True (the default) the wizard refuses to submit
            with this field blank. When False, blank is allowed and the
            template sees ``None``.
        min / max: For ``number`` only — inclusive bounds the wizard
            enforces.
        choices: For ``select`` only — list of allowed values.
    """

    id: str
    type: InputType
    label: str
    description: str = ""
    default: Any = None
    required: bool = True
    min: float | None = None
    max: float | None = None
    choices: tuple[Any, ...] = ()
    # When set, the value is computed by a registered async resolver
    # (see ``selora_ai.recipes.resolvers``) instead of asked from the
    # user. The wizard hides resolver-driven inputs from the Settings
    # form; the pipeline runs the resolver before render so templates
    # can reference ``inputs.<id>`` exactly as if a human had typed it.
    resolver: str | None = None

    def validate(self) -> None:
        if not self.id.isidentifier():
            raise ManifestError(f"input.id {self.id!r} must be a Python-identifier")
        if self.type not in _VALID_INPUT_TYPES:
            raise ManifestError(
                f"input {self.id!r}: unknown type {self.type!r} "
                f"(expected one of: {', '.join(sorted(_VALID_INPUT_TYPES))})"
            )
        if self.type == "select" and not self.choices:
            raise ManifestError(f"input {self.id!r}: type='select' requires non-empty choices")
        if self.type != "select" and self.choices:
            raise ManifestError(f"input {self.id!r}: choices only allowed with type='select'")
        if self.type != "number" and (self.min is not None or self.max is not None):
            raise ManifestError(f"input {self.id!r}: min/max only allowed with type='number'")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ManifestError(f"input {self.id!r}: min={self.min} > max={self.max}")


# ── Integration prereq ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class IntegrationSpec:
    """One required HA integration prereq.

    The pipeline checks each declared integration is loaded before
    rendering. If it isn't, the recipe halts at the prereq stage with a
    punch-list entry asking the user to install / configure it via the
    Integrations page. We deliberately do NOT trigger config flows
    automatically — that pulls the LLM-y "negotiate with the user"
    behaviour back in, and config flows often need credentials only the
    user has.
    """

    domain: str
    title: str = ""
    config_url: str = ""
    # Optional auto-setup spec. When present, the wizard's "Set up
    # <integration>" button drives the HA config flow directly via
    # backend orchestration instead of showing the form to the user.
    # Shape: ``{"values": {field: literal_value, ...},
    #          "resolved": {field: "<resolver_name>"}}`` — literals
    # are passed through as-is; resolved fields are computed via the
    # ``selora_ai.recipes.resolvers`` registry against HA state.
    auto_setup: dict[str, Any] | None = None

    def validate(self) -> None:
        if not self.domain or not self.domain.replace("_", "").isalnum():
            raise ManifestError(
                f"integration.domain {self.domain!r} must be a non-empty alphanumeric+underscore string"
            )
        if self.auto_setup is not None and not isinstance(self.auto_setup, dict):
            raise ManifestError(f"integration.auto_setup for {self.domain!r} must be a mapping")


# ── Binding spec (pre-pinned entities) ─────────────────────────────


@dataclass(frozen=True, slots=True)
class BindingSpec:
    """One pre-pinned entity for a role.

    Connect-authored installation manifests use this block to tell the
    integration "we already agreed on these devices with the customer
    — here's which entity_id each one will land at." The pipeline then
    runs as a *check*, not a *negotiation*: each pin is either present
    and matches the role filter (locked-bound), present but doesn't
    match (hard error), or missing (waiting-on punch list entry).

    Attributes:
        entity_id: The entity id the device should expose once paired.
            Authoritative — the resolver looks this up by name.
        device_class: Optional. Surfaced to the field tech in the
            "waiting on" card so they know what kind of device to
            pair.
        manufacturer / model: Optional. Same purpose — helps the tech
            identify the right hardware in the box.
        integration: Optional. Names the HA integration the device
            ships through (``zha``, ``zwave_js``, ``hue``); helps the
            tech know which workflow to use.
        identifier: Optional opaque vendor identifier (Z-Wave node id,
            ZHA IEEE, MAC, …). The pipeline doesn't try to match on
            this — it's purely informational so the tech can
            cross-reference the device card with the manifest.
        note: Optional free-text the recipe author can include for
            site-specific context (room, position, etc.).
    """

    entity_id: str
    device_class: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    integration: str | None = None
    identifier: str | None = None
    note: str = ""

    def validate(self, role_id: str) -> None:
        # entity_id must be of the form ``<domain>.<object>`` — HA's
        # universal entity-id grammar. Anything else is a malformed
        # binding and we'd rather reject it on disk than chase a
        # mysterious resolver miss at install time.
        if not self.entity_id or "." not in self.entity_id:
            raise ManifestError(
                f"binding entity_id {self.entity_id!r} in role "
                f"{role_id!r} must be of the form 'domain.object_id'"
            )


# ── Dashboard card spec ─────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DashboardCardSpec:
    """Optional ``dashboard:`` block — a single Lovelace card the recipe
    drops onto a dashboard as its final install step.

    This closes the "recipe creates a helper but leaves tapping it up to
    you" gap: instead of README prose telling the user to add a card by
    hand, the pipeline inserts it via HA's Lovelace storage API (a
    deterministic, reversible write — no LLM in the loop).

    Attributes:
        card: The raw Lovelace card config (``type`` required). Values
            may carry ``${role:<id>}`` / ``${input:<id>}`` placeholders
            that the apply stage substitutes against the resolved
            bindings + input values before insertion.
        target: Dashboard ``url_path`` to insert into. ``None`` (the
            default) targets the user's default dashboard.
        view: Which view to append the card to — an integer index or a
            view title string. Defaults to the first view.

    Only storage-mode dashboards are writable; YAML-mode dashboards are
    read-only, so the apply stage skips them and falls back to the
    recipe's manual instructions.
    """

    card: dict[str, Any]
    target: str | None = None
    view: int | str = 0

    def validate(self) -> None:
        if not isinstance(self.card, dict) or not self.card:
            raise ManifestError("dashboard.card must be a non-empty mapping")
        if not str(self.card.get("type", "")).strip():
            raise ManifestError("dashboard.card.type is required")
        if not isinstance(self.view, (int, str)):
            raise ManifestError("dashboard.view must be an integer index or a view title")


# ── Manifest ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Manifest:
    """A loaded, validated recipe manifest.

    Construction goes via :func:`load_manifest`, never the constructor
    directly — validation is intentionally up-front so downstream
    pipeline stages can assume the manifest is well-formed.
    """

    slug: str
    version: str
    title: str
    # Short one-line blurb shown directly under the recipe title in the
    # wizard. Optional — falls back to the first sentence of
    # ``description`` when missing.
    tagline: str
    description: str
    author: str
    # ISO-8601 date the version was released. Surfaced in the wizard
    # header for context. Optional.
    released: str
    # Lowest Selora AI integration version that ships the features this
    # recipe relies on (e.g. integration-scoped roles, the ``event``
    # role kind). A recipe requiring a newer integration than the one
    # installed is hidden from the catalog — see ``version_gate``. Blank
    # (the default) means "runs on any version". Semver-style string.
    min_integration_version: str
    # Short keyword tags rendered as pills under the title (``safety``,
    # ``water``, ``alerts``…). Pure display metadata; the pipeline
    # never reads them.
    tags: tuple[str, ...]
    roles: tuple[RoleSpec, ...]
    inputs: tuple[InputSpec, ...]
    integrations: tuple[IntegrationSpec, ...]
    # Pre-pinned entity bindings keyed by role id. Connect-authored
    # installation manifests populate this so the field tech doesn't
    # have to pick on D-Day — pinned entities lock to the role, missing
    # ones surface as waiting-on punch entries. Empty dict (the
    # default) means "no pins, treat every role as freely selectable."
    bindings: dict[str, tuple[BindingSpec, ...]] = field(default_factory=dict)
    # Relative paths (from the bundle root) of Jinja template files to
    # render. Order matters only for cosmetic concatenation of the
    # rendered YAML — HA's package loader merges by top-level key, not
    # by line order.
    package_files: tuple[str, ...] = ()
    # ── Recipe-engine v3 prototype flag ────────────────────────
    # ``literal`` (default): bindings are baked into the rendered
    # package YAML as concrete entity_ids. Atomic install/uninstall,
    # but updating a binding requires re-running the wizard so the
    # package re-renders.
    #
    # ``group``: bindings live in an HA ``group:`` section emitted as
    # part of the package. Templates reference ``groups.<role_id>``
    # (resolves to ``group.selora_<slug>_<role_id>``) instead of
    # literal entity lists. Updating a binding = updating the group's
    # member list — no re-render needed. Behaviour package and groups
    # share one file so uninstall stays atomic.
    binding_mode: Literal["literal", "group"] = "literal"
    # Optional final-stage Lovelace card. When set, the install pipeline
    # inserts this card onto a dashboard after the package reloads.
    # ``None`` means the recipe surfaces no dashboard affordance.
    dashboard: DashboardCardSpec | None = None
    # Full raw manifest dict — preserved for templates that want to read
    # an unschematized field. Should be considered opaque downstream.
    raw: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)


def _coerce_role(data: Any) -> RoleSpec:
    if not isinstance(data, dict):
        raise ManifestError(f"role entry must be a mapping, got {type(data).__name__}")
    try:
        role = RoleSpec(
            id=str(data.get("id", "")).strip(),
            kind=str(data.get("kind", "")).strip(),
            device_class=(str(data["device_class"]).strip() if data.get("device_class") else None),
            integration=(str(data["integration"]).strip() if data.get("integration") else None),
            match=(str(data["match"]) if data.get("match") else None),
            features=tuple(str(f).strip() for f in (data.get("features") or [])),
            min_count=int(data.get("min_count", 1)),
            max_count=(int(data["max_count"]) if data.get("max_count") is not None else None),
            description=str(data.get("description", "")),
            title=str(data.get("title", "")).strip(),
            selection=str(data.get("selection", "auto")).strip().lower(),  # type: ignore[arg-type]
        )
    except (ValueError, TypeError) as exc:
        # Non-numeric min_count/max_count etc. Raise ManifestError so the
        # loader skips this one bundle instead of failing the whole list.
        raise ManifestError(f"invalid role entry: {exc}") from exc
    role.validate()
    return role


def _coerce_input(data: Any) -> InputSpec:
    if not isinstance(data, dict):
        raise ManifestError(f"input entry must be a mapping, got {type(data).__name__}")
    try:
        spec = InputSpec(
            id=str(data.get("id", "")).strip(),
            type=str(data.get("type", "")).strip(),  # type: ignore[arg-type]
            label=str(data.get("label", "")),
            description=str(data.get("description", "")),
            default=data.get("default"),
            required=bool(data.get("required", True)),
            resolver=(str(data["resolver"]).strip() if data.get("resolver") else None),
            min=(float(data["min"]) if data.get("min") is not None else None),
            max=(float(data["max"]) if data.get("max") is not None else None),
            choices=tuple(data.get("choices") or ()),
        )
    except (ValueError, TypeError) as exc:
        # Non-numeric min/max etc. Raise ManifestError so the loader skips
        # this one bundle instead of failing the whole list.
        raise ManifestError(f"invalid input entry: {exc}") from exc
    spec.validate()
    return spec


def _coerce_integration(data: Any) -> IntegrationSpec:
    if isinstance(data, str):
        # Shorthand: just the domain name.
        spec = IntegrationSpec(domain=data.strip())
    elif isinstance(data, dict):
        auto_setup = data.get("auto_setup")
        if auto_setup is not None and not isinstance(auto_setup, dict):
            raise ManifestError(f"integration {data.get('domain')!r}: auto_setup must be a mapping")
        spec = IntegrationSpec(
            domain=str(data.get("domain", "")).strip(),
            title=str(data.get("title", "")),
            config_url=str(data.get("config_url", "")),
            auto_setup=auto_setup,
        )
    else:
        raise ManifestError(
            f"integration entry must be a string or mapping, got {type(data).__name__}"
        )
    spec.validate()
    return spec


def _coerce_dashboard(data: Any) -> DashboardCardSpec | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ManifestError(f"dashboard must be a mapping, got {type(data).__name__}")
    card = data.get("card")
    if not isinstance(card, dict):
        raise ManifestError("dashboard.card must be a mapping")
    target_raw = data.get("target")
    # ``default`` is sugar for "the default dashboard" (url_path None).
    target = (
        None
        if target_raw is None or str(target_raw).strip().lower() in ("", "default")
        else str(target_raw).strip()
    )
    spec = DashboardCardSpec(
        card=dict(card),
        target=target,
        view=data.get("view", 0),
    )
    spec.validate()
    return spec


def _validate_package_files(files: list[Any], root: Path) -> tuple[str, ...]:
    """Reject path traversal, missing files, non-Jinja-suffix entries."""
    if not files:
        raise ManifestError("package_files must list at least one template")
    out: list[str] = []
    for entry in files:
        if not isinstance(entry, str):
            raise ManifestError(f"package_files entry must be a string, got {type(entry).__name__}")
        rel = entry.strip()
        if not rel:
            raise ManifestError("package_files entry must not be empty")
        if rel.startswith("/") or ".." in Path(rel).parts:
            raise ManifestError(
                f"package_files entry {rel!r}: absolute paths and '..' traversal are refused"
            )
        target = (root / rel).resolve()
        if not str(target).startswith(str(root.resolve())):
            raise ManifestError(f"package_files entry {rel!r}: resolves outside the bundle")
        if not target.is_file():
            raise ManifestError(f"package_files entry {rel!r}: file not found at {target}")
        if not (rel.endswith(".yaml.j2") or rel.endswith(".yml.j2")):
            raise ManifestError(f"package_files entry {rel!r}: must end with .yaml.j2 or .yml.j2")
        out.append(rel)
    return tuple(out)


def load_manifest(bundle_root: Path) -> Manifest:
    """Read, parse, and validate ``<bundle_root>/manifest.yaml``.

    Raises :class:`ManifestError` with a single-sentence reason on any
    structural problem. Callers should treat ManifestError as the
    pipeline's "Recipe definition" stage failure.
    """
    if not bundle_root.is_dir():
        raise ManifestError(f"bundle root not a directory: {bundle_root}")
    manifest_path = bundle_root / "manifest.yaml"
    if not manifest_path.is_file():
        # Friendly fallback for ``.yml``.
        alt = bundle_root / "manifest.yml"
        if alt.is_file():
            manifest_path = alt
        else:
            raise ManifestError(f"manifest.yaml not found in bundle: {bundle_root}")
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"could not read {manifest_path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"manifest is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(
            f"manifest.yaml must parse to a mapping at the top level, got {type(data).__name__}"
        )

    slug = str(data.get("slug", "")).strip()
    if not slug or not all(c.isalnum() or c in "-_" for c in slug):
        raise ManifestError(
            f"slug {slug!r} must be a non-empty string of alphanumerics, '-', and '_'"
        )
    version = str(data.get("version", "")).strip()
    if not version:
        raise ManifestError("version is required (semver-style string)")
    title = str(data.get("title", slug)).strip()
    description = str(data.get("description", "")).strip()
    author = str(data.get("author", "")).strip()

    roles = tuple(_coerce_role(r) for r in (data.get("roles") or []))
    role_ids = {r.id for r in roles}
    if len(role_ids) != len(roles):
        raise ManifestError("role.id values must be unique within a manifest")

    inputs = tuple(_coerce_input(i) for i in (data.get("inputs") or []))
    input_ids = {i.id for i in inputs}
    if len(input_ids) != len(inputs):
        raise ManifestError("input.id values must be unique within a manifest")

    integrations = tuple(_coerce_integration(it) for it in (data.get("integrations") or []))

    package_files = _validate_package_files(data.get("package_files") or [], bundle_root)

    bindings = _coerce_bindings(data.get("bindings") or {}, role_ids)

    tagline = str(data.get("tagline", "")).strip()
    released = str(data.get("released", "")).strip()
    min_integration_version = str(data.get("min_integration_version", "")).strip()
    if min_integration_version and _release_tuple(min_integration_version) is None:
        raise ManifestError(
            f"min_integration_version {min_integration_version!r} must be a "
            "semver-style string (e.g. '0.12.0')"
        )
    tags_raw = data.get("tags") or []
    if not isinstance(tags_raw, list):
        raise ManifestError(f"tags must be a list of strings, got {type(tags_raw).__name__}")
    tags = tuple(str(t).strip() for t in tags_raw if str(t).strip())

    binding_mode = str(data.get("binding_mode", "literal")).strip().lower()
    if binding_mode not in ("literal", "group"):
        raise ManifestError(f"binding_mode must be 'literal' or 'group', got {binding_mode!r}")

    dashboard = _coerce_dashboard(data.get("dashboard"))

    return Manifest(
        slug=slug,
        version=version,
        title=title,
        tagline=tagline,
        description=description,
        author=author,
        released=released,
        min_integration_version=min_integration_version,
        tags=tags,
        roles=roles,
        inputs=inputs,
        integrations=integrations,
        bindings=bindings,
        package_files=package_files,
        binding_mode=binding_mode,  # type: ignore[arg-type]
        dashboard=dashboard,
        raw=data,
    )


def _coerce_binding(role_id: str, data: Any) -> BindingSpec:
    if isinstance(data, str):
        # Shorthand: bare entity_id with no identity hints. Connect
        # manifests usually carry the structured form, but a recipe
        # author working by hand may want to write just the id.
        spec = BindingSpec(entity_id=data.strip())
    elif isinstance(data, dict):
        ident = data.get("identity") or {}
        if not isinstance(ident, dict):
            raise ManifestError(
                f"binding {data.get('entity_id')!r} in role {role_id!r}: identity must be a mapping"
            )
        spec = BindingSpec(
            entity_id=str(data.get("entity_id", "")).strip(),
            device_class=(str(ident["device_class"]) if ident.get("device_class") else None),
            manufacturer=(str(ident["manufacturer"]) if ident.get("manufacturer") else None),
            model=(str(ident["model"]) if ident.get("model") else None),
            integration=(str(ident["integration"]) if ident.get("integration") else None),
            identifier=(str(ident["identifier"]) if ident.get("identifier") else None),
            note=str(data.get("note", "")),
        )
    else:
        raise ManifestError(
            f"binding entry in role {role_id!r} must be a string or mapping, "
            f"got {type(data).__name__}"
        )
    spec.validate(role_id)
    return spec


def _coerce_bindings(data: Any, role_ids: set[str]) -> dict[str, tuple[BindingSpec, ...]]:
    """Coerce + validate the manifest's optional ``bindings:`` block.

    Cross-checks every key against the declared role ids so a typo
    (``bedroom_light`` vs ``bedroom_lights``) fails on load instead of
    silently being ignored by the resolver.
    """
    if not data:
        return {}
    if not isinstance(data, dict):
        raise ManifestError(
            f"bindings must be a mapping of role_id -> [entries], got {type(data).__name__}"
        )
    out: dict[str, tuple[BindingSpec, ...]] = {}
    for role_id, entries in data.items():
        if role_id not in role_ids:
            raise ManifestError(
                f"bindings references unknown role {role_id!r} (declared roles: "
                f"{', '.join(sorted(role_ids))})"
            )
        if not isinstance(entries, list):
            raise ManifestError(f"bindings.{role_id} must be a list, got {type(entries).__name__}")
        coerced = tuple(_coerce_binding(role_id, e) for e in entries)
        # Reject duplicate entity_ids within a role — they'd double-
        # bind silently and inflate the count check.
        seen: set[str] = set()
        for b in coerced:
            if b.entity_id in seen:
                raise ManifestError(f"bindings.{role_id}: duplicate entity_id {b.entity_id!r}")
            seen.add(b.entity_id)
        out[role_id] = coerced
    return out
