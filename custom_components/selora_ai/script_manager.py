"""Create, inspect, and delete Home Assistant scripts.

A script is a named, reusable action sequence with no trigger — the thing to
reach for when several automations need to do the same five steps, or when the
user wants a button they can press ("Movie Night", "Leaving the House"). Selora
could already write automations and scenes but not scripts, so the model's only
way to offer a reusable sequence was to inline it into every automation that
needed it.

``scripts.yaml`` is a **mapping** keyed by object_id, unlike ``automations.yaml``
which is a list of dicts carrying their own ``id``. That difference is why this
does not reuse ``automation_utils``' readers: the same code shape would silently
write a list HA then ignores.

Storage-collection helpers aside, the write path mirrors automations exactly —
atomic replace of the YAML file, then ``script.reload`` — because HA reads
``scripts.yaml`` on reload and holds no other copy.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final
import uuid

from homeassistant.util import slugify

from .const import MAX_TOOL_RESULT_CHARS
from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

SCRIPTS_FILE: Final = "scripts.yaml"

# Serialises the whole read-modify-write transaction, not just the write.
# Every mutation here rewrites the entire file, so two concurrent callers that
# each read the same snapshot will each write it back — and the second silently
# discards the first's script. Mirrors ``AUTOMATIONS_YAML_LOCK`` in
# ``automation_utils``, which exists for exactly this reason.
SCRIPTS_YAML_LOCK: Final = asyncio.Lock()

# Cap on scripts returned by a list call, and on sequence steps echoed back per
# script. A home with 80 scripts is not unusual and each sequence can run to
# dozens of steps; the whole file would crowd out the conversation.
_MAX_LISTED: Final = 50

# Size ceiling for the WHOLE result ``get_script`` returns, not just its
# sequence. ``ToolExecutor._truncate_result`` trims the longest list in a result
# that exceeds ``MAX_TOOL_RESULT_CHARS``, and the longest list is the sequence —
# so a sequence comfortably under any per-sequence cap still gets steps removed
# once ``fields``/``variables``/``description`` push the total over. That
# trimming is silent, and a caller told ``editable: True`` would then replace
# the script from a partial copy.
#
# The margin covers the keys the uneditable form adds and any wrapper the
# executor puts around the payload.
_MAX_RESULT_CHARS: Final = MAX_TOOL_RESULT_CHARS - 500


def _scripts_path(hass: HomeAssistant) -> Path:
    return Path(hass.config.path(SCRIPTS_FILE))


class ScriptsFileError(Exception):
    """``scripts.yaml`` exists but could not be parsed as a script mapping.

    Distinct from "the file is absent or empty", which is an ordinary state
    that reads as ``{}``. Conflating the two is a data-loss bug rather than a
    cosmetic one: every write here is a read-modify-write of the whole file, so
    a malformed file read as ``{}`` means the next ``set_script`` rewrites it
    with only the new script and the user's other scripts are gone. A parse
    failure is recoverable — a rewrite is not.
    """


def _read_scripts(path: Path) -> dict[str, Any]:
    """Read scripts.yaml (runs in executor). Returns {} for a missing file.

    Uses the same ruamel reader as ``automation_utils`` so a double-quoted
    ``"on"`` in a sequence survives the round-trip as a string rather than
    coming back as the boolean ``True``.

    Raises :class:`ScriptsFileError` when the file is present but unparseable
    or is not a mapping.
    """
    from ruamel.yaml import YAML

    from .automation_utils import _to_plain_types

    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8").strip()
        if not text or text in ("{}", "[]"):
            return {}
        data = YAML().load(text)
    except Exception as exc:
        _LOGGER.error("Error reading scripts.yaml: %s", exc)
        raise ScriptsFileError(f"scripts.yaml could not be parsed: {exc}") from exc
    if not isinstance(data, dict):
        _LOGGER.error("scripts.yaml is not a mapping (got %s)", type(data).__name__)
        raise ScriptsFileError(
            f"scripts.yaml is a {type(data).__name__}, not a mapping of scripts."
        )
    return _to_plain_types(data)


def _write_scripts(path: Path, scripts: dict[str, Any]) -> None:
    """Write scripts.yaml atomically (runs in executor).

    The temp file carries a uuid rather than a fixed ``.yaml.tmp`` suffix.
    :data:`SCRIPTS_YAML_LOCK` already serialises this process's writers, so the
    unique name is there for the writer we do not control — a second HA worker,
    or a user's own tooling — where a shared temp path means one partial dump
    lands on top of another and the ``replace`` publishes the mixture.
    """
    from ruamel.yaml import YAML

    _quote_script_yaml(scripts)

    ryaml = YAML()
    ryaml.default_flow_style = False
    ryaml.allow_unicode = True
    tmp_path = path.with_suffix(f".yaml.{uuid.uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            ryaml.dump(scripts, fh)
        tmp_path.replace(path)
    finally:
        tmp_path.unlink(missing_ok=True)


# Strings YAML 1.1 reinterprets as booleans if written bare.
_YAML_BOOL_STRINGS: Final = frozenset({"true", "false", "yes", "no", "on", "off", "y", "n"})


def _quote_script_yaml(scripts: dict[str, Any]) -> None:
    """Quote strings YAML 1.1 would reinterpret, in place.

    **No boolean is ever rewritten.** The reader is ruamel in round-trip mode,
    which follows the YAML 1.2 core schema: a bare ``to: on`` loads as the
    *string* ``"on"``, and only a literal ``true``/``false`` loads as a bool. So
    every bool reaching this function is a genuine bool that some action means
    as one — ``continue_on_error``, an MQTT action's ``retain``, or a service
    field literally named ``state`` (``evohome.set_dhw_override`` takes one).
    Rewriting any of them to ``"on"``/``"off"`` changes valid service data, and
    because each write rewrites the whole file it does so to unrelated scripts.

    This is why ``automation_utils._quote_yaml_booleans`` is not reused: it
    converts bools by key name, which the automations path may need for its own
    reasons but which is simply wrong here.

    Quoting *strings* stays, and is lossless — it protects a value on the way
    OUT, so a YAML 1.1 reader does not turn our ``on`` back into a bool.
    """
    import re  # noqa: PLC0415

    from ruamel.yaml.scalarstring import DoubleQuotedScalarString  # noqa: PLC0415

    sexagesimal = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")

    def walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            for k, v in obj.items():
                obj[k] = walk(v)
            return obj
        if isinstance(obj, list):
            for i, v in enumerate(obj):
                obj[i] = walk(v)
            return obj
        if isinstance(obj, str) and (obj.lower() in _YAML_BOOL_STRINGS or sexagesimal.match(obj)):
            return DoubleQuotedScalarString(obj)
        return obj

    for object_id, config in scripts.items():
        scripts[object_id] = walk(config)


def _entity_id(object_id: str) -> str:
    return f"script.{object_id}"


def resolve_script(scripts: dict[str, Any], ref: str) -> tuple[str | None, str | None]:
    """Resolve a script by object_id, entity_id, or alias.

    Returns ``(object_id, error)``. The model quotes back whatever the last tool
    returned, and users say the alias ("Movie Night"), so all three have to land
    on the same script.

    An alias is NOT unique — Home Assistant happily takes two scripts called
    "Movie Night" under different object_ids — so an alias matching more than
    one is an ambiguity, not a match. Returning the first was silent and
    destructive: ``delete_script`` and ``set_script`` would have removed or
    overwritten whichever happened to come first in the mapping. An object_id
    is unique, so it is tried first and never ambiguous.
    """
    ref = str(ref or "").strip()
    if not ref:
        return None, None
    if ref.startswith("script."):
        ref = ref.split(".", 1)[1]
    if ref in scripts:
        return ref, None

    wanted = ref.casefold()
    for object_id in scripts:
        if object_id.casefold() == wanted:
            return object_id, None

    matches = [
        object_id
        for object_id, config in scripts.items()
        if str((config or {}).get("alias", "")).casefold() == wanted
    ]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, (
            f"{len(matches)} scripts are named "
            f"'{sanitize_untrusted_text(ref, 60)}' ({', '.join(sorted(matches))}). "
            f"Use the object_id or entity_id to say which one."
        )
    return None, None


def resolve_write_target(
    scripts: dict[str, Any], *, object_id: str | None, alias: str
) -> tuple[str, bool, str | None]:
    """Return ``(object_id, replaces_existing)`` for a ``set_script`` call.

    The single source of truth for "is this a create or a replace", shared by
    the write and by the confirmation preview. They MUST agree: when the
    preview called something a creation and the write treated it as a
    replacement, the script was overwritten with no card shown at all.

    The trap is that an alias does not round-trip through a slug.
    ``resolve_script`` matches on alias, so "Movie Night" does not resolve
    against a script whose alias is "Something Else" — but ``slugify`` still
    turns it into ``movie_night``, which that script may already occupy. Taking
    the slug unchecked meant writing over an unrelated script.

    So a genuinely new script gets the first FREE slug. HA's own registries
    number collisions the same way, and it keeps "create a script called X"
    working rather than dead-ending on a name the user cannot see.
    """
    explicit = str(object_id or "").strip()
    if explicit.startswith("script."):
        explicit = explicit.split(".", 1)[1]
    if explicit:
        return explicit, explicit in scripts, None

    # Match by alias so "update the Movie Night script" edits the existing one
    # instead of creating movie_night_2 beside it. An alias shared by several
    # scripts is refused rather than resolved — overwriting an arbitrary one is
    # the worst available outcome.
    match, ambiguous = resolve_script(scripts, alias)
    if ambiguous:
        return "", False, ambiguous
    if match is not None:
        return match, True, None

    base = slugify(alias) or "script"
    candidate = base
    suffix = 2
    while candidate in scripts:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate, False, None


def script_fingerprint(config: Any) -> str:
    """A content hash of one script's stored configuration.

    The alias alone is not identity. A script can be edited in place — new
    sequence, same name — while a confirmation card sits open, and an alias
    check passes happily: the old delete card then destroys the new content and
    the old replace card overwrites it. Hashing the whole config catches any
    edit, not just a rename.

    sha256 rather than md5: SAST flags md5 as weak crypto, and this is cheap
    enough that the stronger hash costs nothing.
    """
    canonical = json.dumps(config or {}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _fingerprint_mismatch(
    scripts: dict[str, Any], object_id: str, expected: str | None
) -> str | None:
    """Why the script at *object_id* is not the one that was approved.

    Callers MUST invoke this while holding :data:`SCRIPTS_YAML_LOCK`, with the
    same ``scripts`` mapping they are about to write back. Checking against a
    separately-loaded copy reopens the window it exists to close.
    """
    if not expected:
        return None
    if object_id not in scripts:
        return "no longer exists"
    if script_fingerprint(scripts[object_id]) != expected:
        return "has changed since it was shown; not applied"
    return None


async def _load(hass: HomeAssistant) -> dict[str, Any]:
    """Read the script mapping. Raises :class:`ScriptsFileError` on a bad file."""
    return await hass.async_add_executor_job(_read_scripts, _scripts_path(hass))


def _unreadable(exc: ScriptsFileError) -> dict[str, Any]:
    """The single error shape every entry point returns for a bad file.

    Named so the wording stays identical across reads and writes: the user
    needs to know their scripts are intact and the file needs hand-editing, not
    that one particular tool failed.
    """
    return {
        "error": (
            f"{exc} Fix the file by hand before changing scripts — Selora will not "
            f"rewrite it, because doing so would discard every script it cannot read."
        )
    }


async def async_list_scripts(hass: HomeAssistant) -> dict[str, Any]:
    """Return every script in scripts.yaml with its alias and step count."""
    try:
        scripts = await _load(hass)
    except ScriptsFileError as exc:
        return _unreadable(exc)
    records = []
    for object_id, config in sorted(scripts.items()):
        config = config or {}
        sequence = config.get("sequence") or []
        state = hass.states.get(_entity_id(object_id))
        records.append(
            {
                "entity_id": _entity_id(object_id),
                "object_id": object_id,
                "alias": sanitize_untrusted_text(config.get("alias") or object_id, 80),
                "step_count": len(sequence) if isinstance(sequence, list) else 1,
                "mode": config.get("mode", "single"),
                "state": state.state if state else "unavailable",
            }
        )
    return {
        "scripts": records[:_MAX_LISTED],
        "count": len(records),
        "scripts_omitted": max(0, len(records) - _MAX_LISTED),
    }


async def async_get_script(hass: HomeAssistant, ref: str) -> dict[str, Any]:
    """Return one script's full configuration."""
    try:
        scripts = await _load(hass)
    except ScriptsFileError as exc:
        return _unreadable(exc)
    object_id, ambiguous = resolve_script(scripts, ref)
    if ambiguous:
        return {"error": ambiguous}
    if object_id is None:
        return {"error": f"No script matching '{sanitize_untrusted_text(ref, 60)}'."}

    config = dict(scripts[object_id] or {})
    sequence = config.get("sequence") or []
    step_count = len(sequence) if isinstance(sequence, list) else 1

    # The sequence is returned WHOLE or not at all — never trimmed.
    #
    # ``set_script`` replaces a script wholesale, and this tool is documented as
    # the thing to call first when editing one. A truncated sequence handed back
    # through that flow is silent data loss: the caller edits what it was given,
    # replaces with it, and every step past the cut is gone. A partial script is
    # worse than no script, because only one of them looks like an answer.
    #
    # The size test is on the ASSEMBLED result, not the sequence alone. The
    # executor trims whatever result it is handed, and it trims the longest
    # list — so a modest sequence still loses steps once the script's other
    # top-level config makes the whole payload too big.
    editable = {
        "entity_id": _entity_id(object_id),
        "object_id": object_id,
        "config": config,
        "step_count": step_count,
        "editable": True,
    }
    if len(json.dumps(editable, ensure_ascii=False, default=str)) <= _MAX_RESULT_CHARS:
        return editable

    # Too large to hand back intact: refuse as uneditable rather than preview a
    # partial copy. A dead end, but a visible one, pointing at the UI that can
    # still do the job.
    config.pop("sequence", None)
    return {
        "entity_id": _entity_id(object_id),
        "object_id": object_id,
        "config": config,
        "step_count": step_count,
        "sequence_omitted": True,
        "editable": False,
        "message": (
            f"This script is too large to return in full ({step_count} steps). The "
            f"sequence is omitted rather than shortened, because set_script replaces "
            f"a script wholesale and editing from a partial copy would delete the "
            f"rest. Edit it in Settings → Automations & scenes → Scripts instead."
        ),
    }


async def async_set_script(
    hass: HomeAssistant,
    *,
    alias: str,
    sequence: list[dict[str, Any]],
    object_id: str | None = None,
    description: str | None = None,
    mode: str | None = None,
    icon: str | None = None,
    expected_fingerprint: str | None = None,
    expect_create: bool = False,
) -> dict[str, Any]:
    """Create a script, or replace an existing one wholesale.

    Replacement is total, not a merge: a partial sequence merged into an
    existing script would produce a sequence neither the user nor the model
    asked for, and there is no way to express "keep step 3" in the tool schema.
    The caller is expected to read the script first when editing.

    The config is validated through HA's own ``async_validate_config_item``
    before anything is written — a bad ``sequence`` otherwise lands in
    ``scripts.yaml``, fails at reload, and leaves the file in a state the user
    has to fix by hand.
    """
    alias = str(alias or "").strip()
    if not alias:
        return {"error": "A script alias (name) is required."}
    if not isinstance(sequence, list) or not sequence:
        return {"error": "A non-empty sequence of actions is required."}

    path = _scripts_path(hass)
    # The lock spans read → validate → write. Holding it only around the write
    # would still let two callers read the same snapshot and have the second
    # write back a mapping missing the first's script.
    async with SCRIPTS_YAML_LOCK:
        try:
            scripts = await _load(hass)
        except ScriptsFileError as exc:
            return _unreadable(exc)

        target, existed, ambiguous = resolve_write_target(scripts, object_id=object_id, alias=alias)
        if ambiguous:
            return {"error": ambiguous}

        # The chat preview classifies create-vs-replace, then awaits before this
        # runs. Another caller can create the alias inside that window, and this
        # re-resolution would then see a replacement and overwrite a script that
        # never got a confirmation card. The expectation the preview formed is
        # carried in and re-checked HERE, inside the lock, where it cannot go
        # stale between the check and the write.
        if expect_create and existed:
            return {
                "error": (
                    f"A script named '{sanitize_untrusted_text(alias, 80)}' was created "
                    f"while this request was in flight. Replacing it needs confirmation — "
                    f"read it with get_script and ask again."
                )
            }

        # A replacement starts from what is already there, so top-level settings
        # this tool has no parameter for survive. A script can carry ``fields``,
        # ``variables``, ``max``, ``max_exceeded``, and ``trace``; rebuilding the
        # config from the five keys below would silently strip a parameterised
        # script's inputs and break every caller passing them — and ``get_script``
        # returns those keys, so the documented edit flow led straight into it.
        #
        # The trade-off is that a key cannot be REMOVED through this tool. That is
        # the right way round: an unwanted leftover is visible in the UI, whereas
        # a deleted ``fields`` block shows up as callers failing later.
        config: dict[str, Any] = dict(scripts[target] or {}) if existed else {}
        config.update({"alias": alias, "sequence": sequence})
        if description:
            config["description"] = str(description).strip()
        if mode:
            config["mode"] = str(mode).strip()
        if icon:
            config["icon"] = str(icon).strip()

        from homeassistant.components.script.config import (  # noqa: PLC0415
            async_validate_config_item,
        )

        try:
            validated = await async_validate_config_item(hass, target, config)
        except Exception as exc:  # noqa: BLE001 — vol.Invalid and friends
            return {"error": f"Script validation failed: {exc}"}
        if validated is None:
            return {"error": "Home Assistant rejected this script configuration."}

        # Validation awaited, so the snapshot above is stale. SCRIPTS_YAML_LOCK
        # only serialises THIS module's callers — HA's own script editor, a
        # second worker, or the user's tooling can have rewritten the file in
        # the meantime, and writing the old mapping back would silently discard
        # whatever they did.
        #
        # Re-read and redo the decisions against what is actually on disk. This
        # preserves unrelated concurrent edits (they are in ``scripts`` now) and
        # still refuses when the concurrent edit touched OUR target, because the
        # guards below run against the fresh copy.
        try:
            scripts = await _load(hass)
        except ScriptsFileError as exc:
            return _unreadable(exc)
        target, existed, ambiguous = resolve_write_target(scripts, object_id=object_id, alias=alias)
        if ambiguous:
            return {"error": ambiguous}
        if expect_create and existed:
            return {
                "error": (
                    f"A script named '{sanitize_untrusted_text(alias, 80)}' was created "
                    f"while this request was in flight. Replacing it needs confirmation — "
                    f"read it with get_script and ask again."
                )
            }
        if existed and (error := _fingerprint_mismatch(scripts, target, expected_fingerprint)):
            return {"error": f"'{sanitize_untrusted_text(alias, 80)}' {error}"}
        scripts[target] = config
        await hass.async_add_executor_job(_write_scripts, path, scripts)

    reload_error = await _async_reload(hass)

    result: dict[str, Any] = {
        "status": "updated" if existed else "created",
        "entity_id": _entity_id(target),
        "object_id": target,
        "alias": sanitize_untrusted_text(alias, 80),
        "step_count": len(sequence),
    }
    if reload_error:
        result["reload_error"] = reload_error
    return result


async def _async_reload(hass: HomeAssistant) -> str | None:
    """Reload scripts, returning an error string instead of raising.

    The file is already written by the time this runs, so a raising reload
    would surface as "tool execution failed" on a change that in fact landed —
    the user would be told nothing happened and the script would appear at the
    next restart. Reporting it alongside the write is the honest outcome.
    """
    try:
        await hass.services.async_call("script", "reload", blocking=True)
    except Exception as exc:  # noqa: BLE001 — the write already succeeded
        _LOGGER.warning("scripts.yaml was written but script.reload failed: %s", exc)
        return f"Saved, but Home Assistant could not reload scripts: {exc}"
    return None


def script_dependents(hass: HomeAssistant, entity_id: str) -> dict[str, list[str]]:
    """What breaks if this script goes away.

    ``group_dependents`` is entity-generic despite its name — a script is
    referenced the same way a group is, so the four lookups it already does
    (automations, scripts, scenes, groups) are exactly the ones needed here.
    """
    from .group_manager import group_dependents  # noqa: PLC0415

    return group_dependents(hass, entity_id)


async def async_delete_script(
    hass: HomeAssistant, ref: str, *, expected_fingerprint: str | None = None
) -> dict[str, Any]:
    """Remove a script from scripts.yaml and reload."""
    path = _scripts_path(hass)
    async with SCRIPTS_YAML_LOCK:
        try:
            scripts = await _load(hass)
        except ScriptsFileError as exc:
            return _unreadable(exc)
        object_id, ambiguous = resolve_script(scripts, ref)
        if ambiguous:
            return {"error": ambiguous}
        if object_id is None:
            return {"error": f"No script matching '{sanitize_untrusted_text(ref, 60)}'."}

        if error := _fingerprint_mismatch(scripts, object_id, expected_fingerprint):
            return {"error": f"'{sanitize_untrusted_text(ref, 60)}' {error}"}

        alias = str((scripts[object_id] or {}).get("alias") or object_id)
        del scripts[object_id]
        await hass.async_add_executor_job(_write_scripts, path, scripts)

    reload_error = await _async_reload(hass)

    result: dict[str, Any] = {
        "status": "deleted",
        "entity_id": _entity_id(object_id),
        "object_id": object_id,
        "alias": sanitize_untrusted_text(alias, 80),
    }
    if reload_error:
        result["reload_error"] = reload_error
    return result
