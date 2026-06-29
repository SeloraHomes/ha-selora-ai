"""Input validation + integration prereq checks.

Two responsibilities, both gates on the pipeline:

1. **Inputs**: coerce + validate user-supplied form values against the
   manifest's :class:`InputSpec` list. Returns a flat ``{id: value}``
   dict ready for the Jinja context, or a structured list of validation
   failures the wizard can attach to specific form fields.

2. **Integration prereqs**: confirm each declared HA integration is
   loaded. We don't attempt to start integrations automatically — a
   config flow needs credentials only the user has. We just gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .manifest import InputSpec, Manifest

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@dataclass(frozen=True, slots=True)
class InputIssue:
    """One thing that's wrong with a user-supplied input. The wizard
    surfaces these next to the relevant form field.
    """

    input_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class InputReport:
    """Aggregate of input validation. ``values`` is None when ``ok`` is
    False — the renderer mustn't see partially-validated input.
    """

    ok: bool
    values: dict[str, Any] | None
    issues: tuple[InputIssue, ...]


@dataclass(frozen=True, slots=True)
class IntegrationIssue:
    domain: str
    reason: str


@dataclass(frozen=True, slots=True)
class IntegrationReport:
    ok: bool
    issues: tuple[IntegrationIssue, ...]


# ── Input validation ────────────────────────────────────────────────


def _coerce_value(spec: InputSpec, raw: Any) -> tuple[Any, str | None]:
    """Convert a raw form value to the right Python type. Returns
    ``(value, error)`` — ``error`` is None on success.
    """
    if raw is None or raw == "":
        # A resolver-driven input is computed, not user-supplied. An empty
        # string from the resolver is its intended result (a resolver raises
        # ResolverError when a value is genuinely required but unavailable —
        # e.g. the tts_engine resolver returns "" for a home with no TTS
        # engine so the template can omit the announcement). Trust it: don't
        # treat it as a missing required field or replace it with the default,
        # which would otherwise either halt the install or render a service
        # call against a non-existent engine.
        if spec.resolver and raw == "" and spec.type == "string":
            return "", None
        if spec.required and spec.default is None:
            return None, "required"
        return spec.default, None

    if spec.type == "string":
        return str(raw), None

    if spec.type == "boolean":
        if isinstance(raw, bool):
            return raw, None
        s = str(raw).strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True, None
        if s in ("false", "0", "no", "off"):
            return False, None
        return None, f"not a boolean: {raw!r}"

    if spec.type == "number":
        try:
            n = float(raw)
        # fmt: skip keeps the required parens: ruff format strips them from a
        # bare multi-type except (no `as` binding), producing invalid syntax.
        except (TypeError, ValueError):  # fmt: skip
            return None, f"not a number: {raw!r}"
        if spec.min is not None and n < spec.min:
            return None, f"below minimum {spec.min}"
        if spec.max is not None and n > spec.max:
            return None, f"above maximum {spec.max}"
        # Preserve int-ness for nicer YAML rendering.
        if n.is_integer():
            return int(n), None
        return n, None

    if spec.type == "select":
        if raw in spec.choices:
            return raw, None
        return None, (f"not one of the allowed choices: {', '.join(map(str, spec.choices))}")

    # Unknown type — manifest validation should have caught it.
    return None, f"unknown input type {spec.type!r}"


def validate_inputs(manifest: Manifest, supplied: dict[str, Any] | None) -> InputReport:
    """Coerce + validate every input the manifest declares.

    Unknown input ids in ``supplied`` are silently dropped — they may
    be leftovers from an earlier manifest version. Missing required
    inputs (and any failed coercion) become issues. Defaults fill in
    for optional inputs the user didn't touch.
    """
    supplied = supplied or {}
    issues: list[InputIssue] = []
    values: dict[str, Any] = {}
    for spec in manifest.inputs:
        raw = supplied.get(spec.id)
        value, err = _coerce_value(spec, raw)
        if err:
            issues.append(InputIssue(input_id=spec.id, reason=err))
            continue
        values[spec.id] = value
    if issues:
        return InputReport(ok=False, values=None, issues=tuple(issues))
    return InputReport(ok=True, values=values, issues=())


# ── Integration prereq ──────────────────────────────────────────────


def check_integrations(manifest: Manifest, hass: HomeAssistant) -> IntegrationReport:
    """Confirm every declared integration is loaded.

    "Loaded" means HA has the component imported AND at least one
    config entry exists in the registry. Empty integrations list ⇒
    OK with no issues.
    """
    if not manifest.integrations:
        return IntegrationReport(ok=True, issues=())
    issues: list[IntegrationIssue] = []
    loaded: set[str] = set()
    for entry in hass.config_entries.async_entries():
        loaded.add(entry.domain)
    for spec in manifest.integrations:
        if spec.domain in loaded:
            continue
        # Some integrations are user-config-only (e.g. ``sun``,
        # ``cloud``); they don't have config entries but DO appear in
        # ``hass.config.components``. Honour that as a second signal so
        # a recipe that depends on ``sun`` doesn't fail-spuriously.
        if spec.domain in hass.config.components:
            continue
        title = spec.title or spec.domain
        issues.append(
            IntegrationIssue(
                domain=spec.domain,
                reason=(
                    f"integration {title!r} is not configured — add it from "
                    "Settings → Devices & Services before installing this recipe"
                ),
            )
        )
    return IntegrationReport(ok=not issues, issues=tuple(issues))
