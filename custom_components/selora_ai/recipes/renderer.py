"""Render manifest + resolved roles + validated inputs into one HA package.

Each bundle ships one or more ``*.yaml.j2`` templates under ``package/``.
The renderer:

1. Builds a sandboxed Jinja environment with the bundle's templates
   loaded via :class:`DictLoader` (no filesystem reach-out at render
   time).
2. Renders each template with a context dict the manifest author can
   rely on: ``slug``, ``title``, ``version``, ``inputs.<id>``,
   ``roles.<id>``, plus a ``roles`` mapping for iteration.
3. YAML-loads each rendered text into a dict, merges them into one
   package dict by top-level key (HA's package shape), and dumps the
   result as a deterministic YAML string ready for disk.

Errors at any step (template syntax, YAML parse, merge conflict, size
cap) raise :class:`RenderError` with a single-sentence reason. The
pipeline surfaces these in the punch list — there is no auto-fix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from jinja2 import (
    DictLoader,
    StrictUndefined,
    TemplateError,
    select_autoescape,
)
from jinja2.sandbox import SandboxedEnvironment
import yaml

from .const import (
    BUNDLE_MAX_TEMPLATE_FILES,
    PACKAGE_RESOURCE_KINDS,
    RENDERED_PACKAGE_MAX_BYTES,
)
from .loader import Bundle
from .resolver import ResolutionReport

if TYPE_CHECKING:
    from .manifest import Manifest


class RenderError(Exception):
    """One reason the package couldn't be rendered. Caller treats this
    as the pipeline's "Render package" stage failure.
    """


@dataclass(frozen=True, slots=True)
class RenderedPackage:
    """The output of the render stage.

    ``yaml_text`` is what the packager will write to disk verbatim. The
    structured ``contents`` dict is also surfaced for the wizard's
    "preview" step so the UI can show what's about to land in YAML form.
    """

    yaml_text: str
    contents: dict[str, Any]


# ── Jinja environment ──────────────────────────────────────────────


def _build_environment(templates: dict[str, str]) -> SandboxedEnvironment:
    """Construct a fresh sandboxed Jinja environment for this render.

    Recipe ``*.yaml.j2`` templates are attacker-controllable content
    (they arrive from the public catalog / a pasted URL / an upload), so
    this MUST be a :class:`SandboxedEnvironment` — a plain ``Environment``
    evaluates arbitrary Python via gadget chains like
    ``{{ cycler.__init__.__globals__ }}``, which is remote code execution
    at render time (and render runs during *preview*, before the admin
    confirms install).

    ``StrictUndefined`` so a missing variable (typo'd input/role) fails
    loud at render time instead of silently emitting a YAML hole. We
    deliberately don't enable autoescape for our YAML output —
    autoescape is for HTML; running it on YAML would mangle ``&``,
    ``<``, ``>`` inside values.
    """
    env = SandboxedEnvironment(
        loader=DictLoader(templates),
        autoescape=select_autoescape([], default=False),
        undefined=StrictUndefined,
        # trim_blocks drops the newline after a block tag so a
        # ``{% if %}`` on its own line doesn't leave a blank line.
        trim_blocks=True,
        # lstrip_blocks is intentionally OFF. It strips the whitespace
        # before a block tag — which silently collapses the indentation
        # of YAML block scalars (``foo: >-``) that embed Home Assistant
        # runtime templates via ``{% raw %}{% set ... %}{% endraw %}``.
        # That produced ``{%`` at column 1 and broke YAML parsing. With
        # it off, indentation is preserved verbatim; authors use the
        # explicit ``{%-``/``-%}`` trim markers when they want a line
        # removed cleanly.
        lstrip_blocks=False,
        keep_trailing_newline=True,
    )
    return env


def _group_object_id(slug: str, role_id: str) -> str:
    """Deterministic HA group object-id for one role in v3 mode.

    Used both by the renderer (to emit the group reference into
    automations) and by the packager / rebind WS (to find the right
    group when updating membership). Underscored slug because HA
    object-ids can't contain hyphens.
    """
    return f"selora_{slug.replace('-', '_')}_{role_id}"


def _group_entity_id(slug: str, role_id: str) -> str:
    """Full HA entity_id (``group.selora_<slug>_<role_id>``) for the
    role's binding group. Templates reference this via the ``groups``
    context dict so manifest authors don't need to know the format.
    """
    return f"group.{_group_object_id(slug, role_id)}"


def _build_context(
    bundle: Bundle,
    resolution: ResolutionReport,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """The variable namespace each template renders against.

    Keep this list authoritative; manifest authors will pattern-match
    against it. ``roles`` is a SimpleNamespace-friendly dict and a
    direct attr-style alias so templates can write either
    ``{{ roles.leak_sensors }}`` or ``{{ roles['leak_sensors'] }}``.

    In ``binding_mode: group`` (v3 prototype), every role also gets a
    ``groups.<role_id>`` entry that resolves to the group entity id
    (e.g. ``group.selora_bedtime_routine_bedroom_lights``). Templates
    in v3 recipes reference this group instead of iterating literal
    entity lists — so updating role membership doesn't require a
    re-render of the package.
    """
    groups = {
        role.id: _group_entity_id(bundle.manifest.slug, role.id) for role in bundle.manifest.roles
    }
    return {
        "slug": bundle.manifest.slug,
        "title": bundle.manifest.title,
        "version": bundle.manifest.version,
        "author": bundle.manifest.author,
        "binding_mode": bundle.manifest.binding_mode,
        "roles": resolution.bindings,
        "groups": groups,
        "inputs": dict(inputs),
    }


# ── Rendering + merging ─────────────────────────────────────────────


def _render_one(env: SandboxedEnvironment, name: str, context: dict[str, Any]) -> dict[str, Any]:
    """Render one Jinja template + parse the resulting YAML into a dict."""
    try:
        template = env.get_template(name)
        rendered = template.render(**context)
    except TemplateError as exc:
        raise RenderError(f"template {name!r}: {exc}") from exc

    try:
        parsed = yaml.safe_load(rendered)
    except yaml.YAMLError as exc:
        raise RenderError(f"template {name!r} produced invalid YAML: {exc}") from exc
    if parsed is None:
        # Empty render (all-commented template). Treat as a no-op.
        return {}
    if not isinstance(parsed, dict):
        raise RenderError(
            f"template {name!r} must render to a YAML mapping at the top "
            f"level (got {type(parsed).__name__}). HA packages are keyed by "
            "resource kind (automation, script, sensor, …)."
        )
    return parsed


def _merge(
    accumulator: dict[str, Any],
    name: str,
    rendered: dict[str, Any],
) -> None:
    """Merge one rendered template into the running package dict.

    HA packages contain two shapes of section per top-level kind:

    - **List-shaped**: ``automation:``, ``scene:``, ``sensor:``,
      ``shell_command:`` etc. Multiple entries are concatenated.
    - **Mapping-shaped**: ``group:``, ``script:``, ``input_boolean:``,
      ``input_number:`` and the rest of the helper domains. Object ids
      are merged into a single dict.

    We detect the shape from the rendered body — a list stays a list,
    a dict gets dict-merged. Mixing shapes for the same kind across
    two templates is an authoring bug and raises ``RenderError``
    rather than silently wrapping the dict into the list (which was
    the old behaviour and produced an invalid ``group: - {...}``
    YAML structure HA refuses to parse).
    """
    for kind, body in rendered.items():
        if kind not in PACKAGE_RESOURCE_KINDS:
            raise RenderError(
                f"template {name!r}: unknown package resource kind {kind!r}. "
                f"Supported kinds: {', '.join(sorted(PACKAGE_RESOURCE_KINDS))}"
            )
        if body is None:
            continue
        existing = accumulator.get(kind)
        if isinstance(body, list):
            if existing is None:
                accumulator[kind] = list(body)
            elif isinstance(existing, list):
                existing.extend(body)
            else:
                raise RenderError(
                    f"template {name!r}: kind {kind!r} emits a list but a "
                    f"previous template emitted a mapping — pick one shape"
                )
        elif isinstance(body, dict):
            if existing is None:
                accumulator[kind] = dict(body)
            elif isinstance(existing, dict):
                existing.update(body)
            else:
                raise RenderError(
                    f"template {name!r}: kind {kind!r} emits a mapping but a "
                    f"previous template emitted a list — pick one shape"
                )
        else:
            raise RenderError(
                f"template {name!r}: kind {kind!r} body must be a list or "
                f"mapping, got {type(body).__name__}"
            )


def render_package(
    bundle: Bundle,
    resolution: ResolutionReport,
    inputs: dict[str, Any],
) -> RenderedPackage:
    """Render every package file in the bundle, merge into one HA
    package, and serialise to YAML.

    Caller must have already confirmed the resolution report is OK and
    the inputs validated — this function trusts its arguments and will
    happily render with empty role lists if you hand it that.

    Raises :class:`RenderError` on the first failure. We don't keep
    going after one — partial packages are worse than no package.
    """
    if len(bundle.templates) > BUNDLE_MAX_TEMPLATE_FILES:
        raise RenderError(
            f"bundle has {len(bundle.templates)} template files; "
            f"cap is {BUNDLE_MAX_TEMPLATE_FILES} per recipe"
        )
    env = _build_environment(bundle.templates)
    context = _build_context(bundle, resolution, inputs)

    accumulator: dict[str, list[Any]] = {}
    for name in bundle.manifest.package_files:
        rendered = _render_one(env, name, context)
        _merge(accumulator, name, rendered)

    # Filter out empty buckets so the on-disk YAML is tidy. A kind
    # that ended up with an empty list contributes nothing and just
    # confuses HA's package loader.
    contents: dict[str, Any] = {k: v for k, v in accumulator.items() if v}

    # v3: emit one HA group per role inside the package itself. Keeping
    # groups in the same file means uninstall is still atomic (removing
    # the package file removes the groups too) while letting the rebind
    # WS rewrite just the ``group:`` section to update bindings without
    # re-rendering automations.
    if bundle.manifest.binding_mode == "group":
        # Start from any group entries the templates emitted so recipe
        # authors can hand-roll a special-case group on top of what we
        # auto-generate. ``_merge`` keeps mapping-shaped sections as
        # dicts now, so ``contents["group"]`` is already in the right
        # shape if present. Our auto-generated entries win on key
        # collision since they represent the live role bindings.
        existing_groups = contents.get("group")
        groups_block: dict[str, dict[str, Any]] = (
            dict(existing_groups) if isinstance(existing_groups, dict) else {}
        )
        for role in bundle.manifest.roles:
            object_id = _group_object_id(bundle.manifest.slug, role.id)
            role_label = role.title or role.id.replace("_", " ").title()
            # Prefix with the recipe title so HA's default dashboard
            # doesn't mistake the role group ("Bedroom lights") for the
            # homeowner's primary group of the same name and auto-add
            # it to Favorites. The qualified name makes provenance
            # obvious in the UI.
            groups_block[object_id] = {
                "name": f"{bundle.manifest.title} — {role_label}",
                "entities": list(resolution.bindings.get(role.id, [])),
            }
        contents["group"] = groups_block

    yaml_text = _dump(contents, bundle.manifest)
    if len(yaml_text.encode("utf-8")) > RENDERED_PACKAGE_MAX_BYTES:
        raise RenderError(
            f"rendered package exceeded {RENDERED_PACKAGE_MAX_BYTES} bytes — "
            "check templates for runaway loops or unbounded role expansion"
        )
    return RenderedPackage(yaml_text=yaml_text, contents=contents)


def _dump(contents: dict[str, Any], manifest: Manifest) -> str:
    """Produce the final on-disk YAML text.

    The leading comment block tags the file as generated output. Field
    techs reading the package file should see immediately that editing
    it by hand will be overwritten on next install.
    """
    header = (
        f"# Generated by Selora AI — recipe '{manifest.slug}' v{manifest.version}\n"
        "# DO NOT EDIT BY HAND. Re-install the recipe to regenerate.\n"
        "# Removing this file (and reloading HA) is equivalent to uninstall.\n"
        "\n"
    )
    body = yaml.safe_dump(
        contents,
        default_flow_style=False,
        sort_keys=True,
        allow_unicode=True,
        indent=2,
    )
    return header + body
