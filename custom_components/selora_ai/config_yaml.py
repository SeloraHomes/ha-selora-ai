"""Read and edit Home Assistant's YAML configuration over MCP.

For what no API reaches: YAML-only integrations (``rest``, ``command_line``,
``shell_command``, ``notify`` platforms …), packages, and theme files. Modelled
on ha-mcp's ``ha_config_set_yaml`` — the same allowlist, the same two-step
write — because a wrong write here can keep Home Assistant from booting.

Every write is guarded, in this order:

* **Which file.** ``configuration.yaml``, one file directly in the packages
  folder (read off ``homeassistant: packages:``), or ``themes/<name>.yaml``.
  Paths are checked as plain names before they are joined, and the result is
  checked again after symlinks resolve: nothing outside the config folder,
  nothing hidden, never ``secrets.yaml`` or ``.storage``.
* **Which key.** A top-level key from ``_ALLOWED_KEYS`` (``automation`` /
  ``script`` / ``scene`` in packages only), a theme's name in a theme file,
  or the one ``frontend.themes`` include that makes the themes folder load.
  A key whose value is an ``!include`` is refused: the content lives in that
  other file.
* **Preview first.** A call without ``confirm_token`` writes nothing and
  returns the unified diff plus a token bound to the file's current bytes and
  the exact request; only the same request with that token writes. MCP has no
  confirmation card, so this is the confirmation.
* **Backup, then check.** The file is backed up before it is written, and Home
  Assistant's own configuration check runs before and after: an edit that
  introduces a NEW error is rolled back from the backup.

ruamel's round-trip mode keeps comments, key order and HA's tags (``!secret``,
``!include``, ``!include_dir_named`` …) exactly as they were. Values under
keys that name a credential are masked in everything this module returns.
"""

from __future__ import annotations

import asyncio
from collections import Counter
import difflib
import hashlib
import io
import logging
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, Final

from . import fs_safety
from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

CONFIG_FILE: Final = "configuration.yaml"
THEMES_DIR: Final = "themes"

# ha-mcp's allowlist: keys for which YAML is a legitimate way to manage
# configuration. Helpers with a storage API (input_*, counter, timer) are not
# here — create_helper owns them.
_ALLOWED_KEYS: Final = frozenset(
    {
        "template",
        "sensor",
        "binary_sensor",
        "command_line",
        "rest",
        "knx",
        "mqtt",
        "shell_command",
        "switch",
        "light",
        "fan",
        "cover",
        "climate",
        "notify",
        "group",
        "utility_meter",
        "recorder",
    }
)
# Storage-mode equivalents exist (and Selora's own tools manage them), but a
# packages workflow keeps them in YAML.
_PACKAGES_ONLY_KEYS: Final = frozenset({"automation", "script", "scene"})

# The one nested path allowed, and only to the include that loads the themes
# folder: the rest of `frontend` (and `homeassistant`, `http`) stays out.
_FRONTEND_THEMES: Final = "frontend.themes"
# Only the merge form: each themes/<name>.yaml this tool writes is keyed by the
# theme's name, which `!include_dir_merge_named` merges as is; `!include_dir_named`
# would wrap it in the filename too, and the frontend drops the nested theme.
_THEMES_INCLUDE_TAG: Final = "!include_dir_merge_named"

# Reload services that make an edit live without a restart.
_RELOAD_SERVICES: Final[dict[str, str]] = {
    "template": "template.reload",
    "mqtt": "mqtt.reload",
    "group": "group.reload",
    "rest": "rest.reload",
    "command_line": "command_line.reload",
    "automation": "automation.reload",
    "script": "script.reload",
    "scene": "scene.reload",
}

_NAME_RE: Final = re.compile(r"[a-z0-9][a-z0-9_-]*")
_THEME_NAME_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]*")

# A key that names a credential.
_SENSITIVE_KEY_RE: Final = re.compile(
    r"password|passwd|passphrase|token|secret|api_?key|private_key|access_key|"
    r"auth_key|authorization|cookie|credential",
    re.IGNORECASE,
)
_MAX_RETURNED_CHARS: Final = 12000
_BACKUP_DIR: Final = ".selora_ai/config_backups"
_BACKUPS_KEPT: Final = 10

# Serialises read-modify-write of the config files we own the write to.
_WRITE_LOCK = asyncio.Lock()


class ConfigYamlError(Exception):
    """A request this module refuses, with the reason to report."""


# ── YAML ────────────────────────────────────────────────────────────────────


# (mapping, sequence, offset) indentation styles HA configs are written in.
# A file is re-dumped in the first style that reproduces it byte for byte —
# ruamel keeps comments and tags but not indentation, and a write that
# re-indented every list in configuration.yaml would bury the real change in
# its own diff.
_STYLES: Final = ((2, 4, 2), (2, 2, 0), (4, 6, 4), (4, 4, 2), (4, 4, 0))
_DEFAULT_STYLE: Final = _STYLES[0]


def _yaml(style: tuple[int, int, int] = _DEFAULT_STYLE) -> Any:
    from ruamel.yaml import YAML  # noqa: PLC0415

    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096
    mapping, sequence, offset = style
    yaml.indent(mapping=mapping, sequence=sequence, offset=offset)
    return yaml


def _style_of(text: str) -> tuple[int, int, int]:
    """The indentation style that reproduces ``text`` unchanged, if any."""
    from ruamel.yaml.error import YAMLError  # noqa: PLC0415

    if not text.strip():
        return _DEFAULT_STYLE
    for style in _STYLES:
        try:
            if _dump(_yaml(style).load(text), style) == text:
                return style
        except YAMLError:
            return _DEFAULT_STYLE
    return _DEFAULT_STYLE


def _load(text: str) -> Any:
    from ruamel.yaml.error import YAMLError  # noqa: PLC0415

    try:
        return _yaml().load(text) if text.strip() else None
    except YAMLError as exc:
        # Not str(exc): ruamel quotes the offending source line, and that line
        # can be a credential ("password: [hunter2"). The position and the
        # parser's own description of the problem are enough to fix it.
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        # Quoted parts go too: ruamel's own description can carry values
        # ('found duplicate key "password" with value "abc" (original value:
        # "hunter2")'). The position says where to look.
        problem = re.sub(
            r"\"[^\"]*\"|'[^']*'", "…", str(getattr(exc, "problem", None) or "invalid YAML")
        )
        raise ConfigYamlError(f"The YAML does not parse{where}: {problem}.") from exc


def _dump(data: Any, style: tuple[int, int, int] = _DEFAULT_STYLE) -> str:
    out = io.StringIO()
    _yaml(style).dump(data, out)
    return out.getvalue()


def _tag_of(value: Any) -> str | None:
    tag = getattr(value, "tag", None)
    tag_value = getattr(tag, "value", tag)
    return str(tag_value) if isinstance(tag_value, str) and tag_value.startswith("!") else None


_MASK: Final = "***"


_NO_COMPARE: Final = object()
_ABSENT: Final = object()


def _mask_tree(node: Any, before: Any = _NO_COMPARE) -> None:
    """Replace, in place, every value under a key that names a credential.

    Structural, not textual: block scalars, nested mappings, unindented lists
    and flow style (``headers: {Authorization: Bearer x}``) are all just values
    to the parser, where each line-matching attempt missed one of them. Only a
    reference is kept (``_is_reference``).

    ``before`` is the same node in the previous version, for a diff: a value
    that differs from it is masked as ``*** (changed)``, one it lacked as
    ``*** (new)``, so an edit that changes only a credential still shows in
    the preview instead of masking to an identical line on both sides.
    Neither value is revealed. List items are paired by position, which errs
    towards marking more as changed, never less.
    """
    if isinstance(node, dict):
        prior = before if isinstance(before, dict) else None
        for key in list(node):
            value = node[key]
            old = prior.get(key, _ABSENT) if prior is not None else _ABSENT
            if before is _NO_COMPARE:
                old = _NO_COMPARE
            # Hyphens read as underscores: header names are spelled
            # `X-API-Key`, `Private-Key`, `X-Auth-Token`.
            sensitive = _SENSITIVE_KEY_RE.search(str(key).replace("-", "_"))
            if sensitive and not _is_reference(value):
                node[key] = _mask_label(value, old)
            else:
                _mask_tree(value, old)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            if before is _NO_COMPARE:
                old: Any = _NO_COMPARE
            elif isinstance(before, list) and index < len(before):
                old = before[index]
            else:
                old = _ABSENT
            _mask_tree(item, old)


def _is_reference(value: Any) -> bool:
    """Whether a value only names where the credential lives.

    ``!secret name`` and the ``!include`` family point elsewhere and are kept.
    Every other tag is masked like a plain value — ``!env_var NAME fallback``
    carries a literal fallback, and an unknown tag could carry anything.
    """
    tag = _tag_of(value)
    return tag is not None and (tag == "!secret" or tag.startswith("!include"))


def _mask_label(value: Any, old: Any) -> str:
    if old is _NO_COMPARE:
        return _MASK
    if old is _ABSENT:
        return f"{_MASK} (new)"
    return _MASK if old == value else f"{_MASK} (changed)"


def masked_text(text: str, style: tuple[int, int, int] = _DEFAULT_STYLE) -> str:
    """``text`` with its credentials masked, or a placeholder if it won't parse."""
    try:
        data = _load(text)
    except ConfigYamlError:
        return "(not shown: this YAML does not parse)\n"
    _mask_tree(data)
    return _dump(data, style) if data is not None else ""


# ── Files ───────────────────────────────────────────────────────────────────


def _packages_dir(config_text: str) -> str | None:
    """The folder `homeassistant: packages:` includes, or None if it names none.

    No default: Home Assistant loads no packages folder unless configured, so
    a guessed one would take writes that report success and never apply. Only
    ``!include_dir_named <folder>`` with a relative folder: there each file is
    one package whose top-level keys are integrations, the shape this tool
    writes. ``!include_dir_merge_named`` makes each file a mapping of package
    names instead, and an absolute folder is not under the config folder.
    """
    try:
        data = _load(config_text)
    except ConfigYamlError:
        return None
    packages = (data or {}).get("homeassistant", {}) if isinstance(data, dict) else {}
    value = packages.get("packages") if isinstance(packages, dict) else None
    if _tag_of(value) == "!include_dir_named":
        folder = str(getattr(value, "value", "")).strip().rstrip("/")
        if _NAME_RE.fullmatch(folder):
            return folder
    return None


async def _read_text(hass: HomeAssistant, path: Path) -> str | None:
    """The file's text, or None when it does not exist — never "" for missing:
    an empty file is a file, and a rollback must restore it, not delete it."""
    return await hass.async_add_executor_job(_current, path)


async def resolve_file(hass: HomeAssistant, file: str) -> tuple[Path, str, str]:
    """``(absolute path, normalized relative path, kind)`` for an allowed file.

    ``kind`` is ``config``, ``package`` or ``theme``. The relative path is
    checked as plain names before anything is joined, then the joined path is
    checked again once symlinks resolve, so neither ``..`` nor a link can
    reach outside the config folder.
    """
    rel = str(file or CONFIG_FILE).strip()
    config_dir = Path(hass.config.config_dir)
    if rel == CONFIG_FILE:
        kind = "config"
    else:
        parts = rel.split("/")
        if len(parts) != 2 or not parts[1].endswith(".yaml"):
            raise ConfigYamlError(
                "file must be configuration.yaml, <packages folder>/<name>.yaml or "
                "themes/<name>.yaml."
            )
        folder, stem = parts[0], parts[1].removesuffix(".yaml")
        if not _NAME_RE.fullmatch(stem):
            raise ConfigYamlError(
                "A file name may use lowercase letters, digits, '-' and '_' only."
            )
        config_text = await _read_text(hass, config_dir / CONFIG_FILE) or ""
        if folder == THEMES_DIR:
            kind = "theme"
        elif (packages := _packages_dir(config_text)) is not None and folder == packages:
            kind = "package"
        elif packages is None:
            raise ConfigYamlError(
                "configuration.yaml loads no packages folder this tool can edit, so a "
                "file there would never apply. It edits packages loaded with "
                "'homeassistant: packages: !include_dir_named <folder>' (a folder in the "
                "configuration folder, one package per file)."
            )
        else:
            raise ConfigYamlError(
                f"'{sanitize_untrusted_text(folder, 40)}' is neither the themes folder nor "
                f"the packages folder configuration.yaml includes ({packages})."
            )
    path = config_dir / rel
    # No symlinks at all, file or folder: one inside the config folder still
    # passes the boundary check below while aliasing secrets.yaml or .storage
    # (`themes` linked to the config folder makes themes/secrets.yaml the real
    # one). A real file in a real folder is what this tool edits.
    if path.is_symlink() or (kind != "config" and (config_dir / rel.split("/")[0]).is_symlink()):
        raise ConfigYamlError("That file or its folder is a symlink; edit the real file.")
    resolved = Path(os.path.realpath(path))
    if not str(resolved).startswith(str(Path(os.path.realpath(config_dir))) + os.sep):
        raise ConfigYamlError("That file resolves outside the configuration folder.")
    if resolved.name == "secrets.yaml" or ".storage" in resolved.parts:
        raise ConfigYamlError("That file is not one this tool reads or writes.")
    return path, rel, kind


# ── Read ────────────────────────────────────────────────────────────────────


def _bounded(text: str) -> tuple[str, bool]:
    if len(text) <= _MAX_RETURNED_CHARS:
        return text, False
    return text[:_MAX_RETURNED_CHARS], True


async def async_read(hass: HomeAssistant, file: str, yaml_path: str | None) -> dict[str, Any]:
    """A config file's top-level keys, or one key's YAML, credentials masked."""
    try:
        path, rel, kind = await resolve_file(hass, file)
        raw = await _read_text(hass, path)
        text = raw or ""
        data = _load(text)
    except ConfigYamlError as exc:
        return {"error": str(exc)}
    if raw is None:
        return {"file": rel, "exists": False}
    if data is None:
        return {"file": rel, "kind": kind, "exists": True, "keys": []}
    if not isinstance(data, dict):
        return {"error": f"{rel} is not a mapping of keys."}

    result: dict[str, Any] = {"file": rel, "kind": kind, "keys": [str(k) for k in data]}
    if kind == "config":
        config_dir = Path(hass.config.config_dir)
        packages = _packages_dir(text)

        def _list(folder: str) -> list[str]:
            base = config_dir / folder
            return sorted(
                f"{folder}/{p.name}"
                for p in (base.glob("*.yaml") if base.is_dir() else [])
                if _NAME_RE.fullmatch(p.stem)
            )

        result["other_files"] = await hass.async_add_executor_job(
            lambda: (_list(packages) if packages else []) + _list(THEMES_DIR)
        )
    if yaml_path:
        key = str(yaml_path).strip()
        if key not in data:
            return {**result, "error": f"No top-level key '{sanitize_untrusted_text(key, 60)}'."}
        subtree = {key: data[key]}
        _mask_tree(subtree)
        body, truncated = _bounded(_dump(subtree, _style_of(text.replace("\r\n", "\n"))))
        result["yaml"] = body
        if truncated:
            result["truncated"] = True
    return result


# ── Edit ────────────────────────────────────────────────────────────────────


def _check_target(kind: str, yaml_path: str) -> None:
    if kind == "theme":
        if not _THEME_NAME_RE.fullmatch(yaml_path):
            raise ConfigYamlError("In a theme file, yaml_path is the theme's name.")
        return
    if yaml_path == _FRONTEND_THEMES:
        if kind != "config":
            raise ConfigYamlError("frontend.themes belongs in configuration.yaml.")
        return
    allowed = _ALLOWED_KEYS | (_PACKAGES_ONLY_KEYS if kind == "package" else frozenset())
    if yaml_path not in allowed:
        hint = (
            " automation, script and scene are allowed in a package file only."
            if yaml_path in _PACKAGES_ONLY_KEYS
            else ""
        )
        raise ConfigYamlError(
            f"'{sanitize_untrusted_text(yaml_path, 60)}' is not a key this tool edits. "
            f"Allowed: {', '.join(sorted(allowed))}, or frontend.themes.{hint}"
        )


def _apply(data: Any, kind: str, yaml_path: str, action: str, value: Any) -> Any:
    """``data`` with the edit applied. Raises ConfigYamlError when it cannot be."""
    from ruamel.yaml.comments import CommentedMap  # noqa: PLC0415

    if data is None:
        data = CommentedMap()
    if not isinstance(data, dict):
        raise ConfigYamlError("The file is not a mapping of keys.")

    if yaml_path == _FRONTEND_THEMES:
        if action != "remove" and (
            _tag_of(value) != _THEMES_INCLUDE_TAG
            or str(getattr(value, "value", "")).strip() != THEMES_DIR
        ):
            raise ConfigYamlError(
                "frontend.themes takes exactly '!include_dir_merge_named themes'."
            )
        frontend = data.get("frontend")
        if frontend is None:
            if action == "remove":
                raise ConfigYamlError("There is no frontend.themes to remove.")
            frontend = data["frontend"] = CommentedMap()
        if not isinstance(frontend, dict):
            raise ConfigYamlError("frontend is not a mapping here; edit it in Home Assistant.")
        if action == "remove":
            if "themes" not in frontend:
                raise ConfigYamlError("There is no frontend.themes to remove.")
            del frontend["themes"]
        elif action == "add" and "themes" in frontend:
            # 'add' never overwrites: themes may be defined inline here, and
            # replacing them with the include drops them.
            raise ConfigYamlError(
                "frontend.themes is already set. Read it first; 'replace' swaps it "
                "for the include, and any themes defined inline there are lost."
            )
        else:
            frontend["themes"] = value
        return data

    current = data.get(yaml_path)
    if _tag_of(current) and _tag_of(current).startswith("!include"):
        raise ConfigYamlError(
            f"'{yaml_path}' is included from another file ({_tag_of(current)} "
            f"{getattr(current, 'value', '')}); edit that file instead."
        )
    if action == "remove":
        if yaml_path not in data:
            raise ConfigYamlError(f"There is no '{yaml_path}' to remove.")
        del data[yaml_path]
    elif action == "replace" or yaml_path not in data:
        data[yaml_path] = value
    elif isinstance(current, list) and isinstance(value, list):
        current.extend(value)
    elif isinstance(current, dict) and isinstance(value, dict):
        overlap = sorted(str(k) for k in value if k in current)
        if overlap:
            raise ConfigYamlError(
                f"'add' would overwrite {', '.join(overlap)} under '{yaml_path}'; use "
                "'replace' to change existing entries."
            )
        current.update(value)
    else:
        raise ConfigYamlError(
            f"'add' cannot merge this into '{yaml_path}': both must be lists or both "
            "mappings. Use 'replace'."
        )
    if kind == "theme" and action != "remove" and not isinstance(data.get(yaml_path), dict):
        raise ConfigYamlError("A theme is a mapping of variables (and optionally modes).")
    return data


def _token(rel: str, old_text: str, action: str, yaml_path: str, content: str | None) -> str:
    material = "\0".join((rel, old_text, action, yaml_path, content or ""))
    return hashlib.sha256(material.encode()).hexdigest()[:20]


def _diff(rel: str, old: str, new: str, style: tuple[int, int, int]) -> str:
    """The diff between the two MASKED documents.

    Masking the diff's own lines cannot work: a hunk's context can start
    inside a credential whose key is above it. Both sides are masked whole —
    the new one against the old, so a changed credential reads
    ``*** (changed)`` — and what differs between them is still the edit.
    """
    try:
        old_data, new_data = _load(old), _load(new)
    except ConfigYamlError:
        return "(not shown: this YAML does not parse)\n"
    _mask_tree(new_data, old_data)  # first, while old_data is unmasked
    _mask_tree(old_data)
    lines = difflib.unified_diff(
        (_dump(old_data, style) if old_data is not None else "").splitlines(keepends=True),
        (_dump(new_data, style) if new_data is not None else "").splitlines(keepends=True),
        fromfile=f"a/{rel}",
        tofile=f"b/{rel}",
    )
    return "".join(lines)


# "at configuration.yaml, line 12" / "line 3, column 5": positions move when an
# edit adds or removes lines, and a problem that was already there must not
# read as one the edit introduced.
_POSITION_RE: Final = re.compile(r",? line \d+(?:, column \d+)?")


# Diagnostics quote the configuration they reject — "expected a dictionary,
# got {'password': 'hunter2', ...}" — and that is the user's credential.
_GOT_VALUE_RE: Final = re.compile(r"([,.:;]?\s*\bgot\b).*$", re.IGNORECASE | re.DOTALL)
# A brace literal, or a bracket literal NOT continuing a key path: `data['rest'][0]`
# names a field; `['abc123']` after a space is a value.
_LITERAL_RE: Final = re.compile(r"\{[^{}]*\}|(?<!data)(?<!\])\[[^\[\]]*\]")


def _redact_problem(message: str) -> str:
    """A configuration-check message without the values it quotes.

    What is wrong and where survive ("Invalid config for 'rest' … expected a
    dictionary"); the rejected value does not — it is often the very block
    holding the credential. Bracket and brace literals go too, except a key
    path like ``@ data['rest'][0]``, which names a field, not its value.
    """
    redacted = _GOT_VALUE_RE.sub(r"\1 (value not shown)", message)
    return _LITERAL_RE.sub("{…}", redacted)


async def _config_errors(hass: HomeAssistant) -> Counter[str] | None:
    """Home Assistant's configuration check, as problems; None if it failed to run.

    Warnings count as much as errors. Since Home Assistant stopped refusing
    to boot over one integration's bad configuration, an invalid integration
    block is reported as a WARNING — and that integration simply does not set
    up — while ERRORS are left to core configuration. Comparing errors alone
    let exactly the mistakes this check is here for through.
    """
    from homeassistant.helpers.check_config import (  # noqa: PLC0415
        async_check_ha_config_file,
    )

    try:
        result = await async_check_ha_config_file(hass)
    except Exception:  # noqa: BLE001 — the check itself failing is reported, not raised
        _LOGGER.warning("Configuration check failed to run", exc_info=True)
        return None
    # Counted, not a set: with positions stripped, a second `rest` entry
    # missing the same field reads exactly like the first, and a set would
    # absorb the new one into the old.
    return Counter(
        _POSITION_RE.sub("", str(problem.message)) for problem in (*result.errors, *result.warnings)
    )


def _backup(config_dir: Path, rel: str, text: str) -> str:
    """Back the file up, owner-only (config files carry inline credentials)."""
    try:
        return fs_safety.backup(config_dir, _BACKUP_DIR, rel, text, _BACKUPS_KEPT)
    except fs_safety.UnsafePathError as exc:
        raise ConfigYamlError(str(exc)) from exc


def _current(path: Path) -> str | None:
    return fs_safety.read_exact(path)


def _replace_if_unchanged(path: Path, expected: str | None, text: str | None) -> bool:
    """See ``fs_safety.replace_if_unchanged``; a new file is owner-only."""
    return fs_safety.replace_if_unchanged(path, expected, text)


def _themes_folder_loaded(config_text: str) -> bool:
    """Whether configuration.yaml loads themes/ the way theme files are written."""
    try:
        data = _load(config_text)
    except ConfigYamlError:
        return False
    frontend = data.get("frontend") if isinstance(data, dict) else None
    themes = frontend.get("themes") if isinstance(frontend, dict) else None
    return (
        _tag_of(themes) == _THEMES_INCLUDE_TAG
        and str(getattr(themes, "value", "")).strip().rstrip("/") == THEMES_DIR
    )


async def _activate(hass: HomeAssistant, kind: str, yaml_path: str) -> dict[str, Any]:
    """Make the edit live where a reload can; otherwise say a restart is needed."""
    if kind == "theme":
        # The reload succeeds whether or not the folder is loaded — it then
        # reloads no themes — so "reload performed" would claim a theme that
        # is not available.
        config_text = await _read_text(hass, Path(hass.config.config_dir) / CONFIG_FILE)
        if not _themes_folder_loaded(config_text or ""):
            return {
                "post_action": "themes_folder_not_loaded",
                "hint": (
                    "configuration.yaml does not load the themes folder, so this theme "
                    "is saved but unavailable. Add it with yaml_path 'frontend.themes' "
                    "and content '!include_dir_merge_named themes'."
                ),
            }
    if kind == "theme" or yaml_path == _FRONTEND_THEMES:
        service = "frontend.reload_themes"
    else:
        service = _RELOAD_SERVICES.get(yaml_path, "")
    if not service:
        return {"post_action": "restart_required"}
    domain, name = service.split(".", 1)
    if not hass.services.has_service(domain, name):
        return {"post_action": "restart_required"}
    try:
        await hass.services.async_call(domain, name, {}, blocking=True)
    except Exception as exc:  # noqa: BLE001 — the write landed; report the reload
        return {
            "post_action": "reload_failed",
            "reload_service": service,
            "reload_error": sanitize_untrusted_text(str(exc), 200),
        }
    return {"post_action": "reload_performed", "reload_service": service}


async def async_edit(
    hass: HomeAssistant,
    *,
    file: str,
    yaml_path: str,
    action: str,
    content: str | None,
    confirm_token: str | None,
) -> dict[str, Any]:
    """Preview an edit, or apply it when the preview's token comes back."""
    action = str(action or "").strip()
    yaml_path = str(yaml_path or "").strip()
    if action not in ("add", "replace", "remove"):
        return {"error": "action must be add, replace or remove."}
    if action != "remove" and not (content or "").strip():
        return {"error": f"'{action}' needs content."}
    try:
        path, rel, kind = await resolve_file(hass, file)
        _check_target(kind, yaml_path)
        value = _load(content or "") if action != "remove" else None
    except ConfigYamlError as exc:
        return {"error": str(exc)}

    config_dir = Path(hass.config.config_dir)
    async with _WRITE_LOCK:
        old_raw = await _read_text(hass, path)
        old_text = old_raw or ""
        eol = "\r\n" if "\r\n" in old_text else "\n"
        old_lf = old_text.replace("\r\n", "\n")
        try:
            new_data = _apply(_load(old_lf), kind, yaml_path, action, value)
        except ConfigYamlError as exc:
            return {"error": str(exc)}
        style = _style_of(old_lf)
        # Written back in the file's own line endings: an edit to one key must
        # not convert a CRLF file to LF throughout.
        new_text = _dump(new_data, style).replace("\n", eol)
        if new_text == old_text:
            return {"status": "unchanged", "file": rel}
        diff, diff_truncated = _bounded(_diff(rel, old_lf, new_text.replace("\r\n", "\n"), style))
        expected = _token(rel, old_text, action, yaml_path, content)
        if confirm_token != expected:
            preview: dict[str, Any] = {
                "preview": True,
                "written": False,
                "file": rel,
                "diff": diff,
                **({"diff_truncated": True} if diff_truncated else {}),
                "confirm_token": expected,
                "message": (
                    "Nothing was written. Show the user this diff; once they agree, "
                    "repeat the same call with confirm_token."
                ),
            }
            if diff_truncated:
                # The token still covers the WHOLE edit; only the display is cut.
                preview["message"] = (
                    "Nothing was written. The diff is too long to show whole and was "
                    "cut; show the user the part above and the key's full new content "
                    "you are sending, and once they agree repeat the same call with "
                    "confirm_token."
                )
            if confirm_token:
                preview["confirm_token_mismatch"] = True
                preview["message"] = (
                    "The file changed since the preview, or the token was wrong. "
                    "Nothing was written; this is the fresh diff and token."
                )
            return preview

        baseline = await _config_errors(hass)
        try:
            backup = await hass.async_add_executor_job(_backup, config_dir, rel, old_text)
        except ConfigYamlError as exc:
            return {"error": str(exc), "written": False}
        # The lock serialises THIS tool only; the file is replaced only if it
        # still holds the bytes this edit was derived from.
        if not await hass.async_add_executor_job(_replace_if_unchanged, path, old_raw, new_text):
            return {
                "error": (
                    "The file changed while this edit was being checked. Nothing was "
                    "written; preview again."
                ),
                "written": False,
            }
        after = await _config_errors(hass)
        introduced = sorted(after - baseline) if after is not None and baseline is not None else []
        if introduced:
            problems = "; ".join(
                sanitize_untrusted_text(_redact_problem(e), 300) for e in introduced[:5]
            )
            # Only restore what this edit wrote: someone may have saved the
            # file during the check, and putting old_text back would erase it.
            restored = await hass.async_add_executor_job(
                _replace_if_unchanged, path, new_text, old_raw
            )
            if not restored:
                return {
                    "error": (
                        "Home Assistant's configuration check failed after this edit, "
                        "and the file was changed by someone else meanwhile, so it was "
                        f"NOT rolled back. The original is in {backup}. Problems: "
                        f"{problems}"
                    ),
                    "written": True,
                    "backup": backup,
                }
            return {
                "error": (
                    "Home Assistant's configuration check failed after this edit, so it "
                    f"was rolled back: {problems}"
                ),
                "written": False,
            }

    result: dict[str, Any] = {
        "written": True,
        "file": rel,
        "diff": diff,
        **({"diff_truncated": True} if diff_truncated else {}),
        "backup": backup,
        **await _activate(hass, kind, yaml_path),
    }
    if after is None or baseline is None:
        result["warning"] = (
            "Home Assistant's configuration check could not run, so this edit was not "
            "verified. Run Settings > Developer tools > Check configuration before "
            "restarting."
        )
    return result
