"""Expand the slim automation envelope into Home Assistant's schema.

The automation specialist emits a short envelope whose keys are
abbreviated so the envelope stops dominating the automation latency
budget::

    {"r": "Turns on the living room light every day at 8am.",
     "a": {"al": "Living Room Light On 8am",
           "t": [{"p": "time", "at": "08:00:00"}],
           "c": [],
           "x": [{"s": "light.turn_on", "e": "light.living_room"}]}}

Nothing in that shape is a defect: it is the wire contract the model was
trained against, so turning it back into ``alias`` / ``triggers`` /
``conditions`` / ``actions`` is shape translation, not repair. That is
why this lives beside the parser rather than inside the model-drift
repair module — it has to run no matter what repair mode is in force.

Pure functions with no provider state, so the expansion can be exercised
directly against the training corpus without a hub.
"""

from __future__ import annotations

import logging
from typing import Any

from ._qwen_repair import unwrap_listed_step_type

_LOGGER = logging.getLogger(__name__)


class SlimAutomationError(ValueError):
    """The block did not become an automation we are willing to run.

    Raised instead of returning a half-expanded payload. Every automation
    gate downstream — the service-registry check, the unknown-entity check,
    the elevated-risk classifier — recognises Home Assistant's own key
    names and nothing else, so a step that kept its short keys is a step
    those gates cannot see. Refusing is the only outcome that does not
    quietly widen what an automation is allowed to do.
    """


# Slim keys consumed by the expansion. Anything else on a step is passed
# through untouched (``at``, ``to``, ``from``, ``above``, ``below``,
# ``for``, ``offset``, ``event``, ``zone``, ``weekday``, …), which is what
# lets the model use a trigger option the expansion has never heard of.
_STEP_TYPE_KEY = "p"
_STEP_ENTITY_KEY = "e"
_ACTION_SERVICE_KEY = "s"
_ACTION_DATA_KEY = "d"
_NESTED_CONDITIONS_KEY = "c"
_NESTED_ACTIONS_KEY = "x"

# Control-flow containers a branch-shaped step nests its own steps in. The
# generator slims what is inside them with the same function it uses for a
# top-level step, so an expansion that does not invert them hands HA a
# service call still wearing its short keys — and the automation gates,
# which only recognise the long ones, never see that call at all.
#
# ``if`` / ``while`` / ``until`` hold CONDITIONS; ``then`` / ``else`` /
# ``sequence`` / ``default`` / ``parallel`` hold ACTIONS. The two need
# different type keys: an ``if`` entry expanded as an action would write
# ``service:`` onto a condition.
_BRANCH_CONDITION_KEYS = ("if",)
_BRANCH_ACTION_KEYS = ("then", "else", "sequence", "default", "parallel")
_REPEAT_CONDITION_KEYS = ("while", "until")
_SHORTHAND_CONDITION_KEYS = ("and", "or", "not")

# An action that waits on triggers rather than running one.
_WAIT_FOR_TRIGGER_KEY = "wait_for_trigger"

# Every short key the expansion owns. After expansion none of them may
# still be anywhere in the payload: one that survived marks a container
# the expansion failed to invert, and the step inside it is invisible to
# the automation gates.
_SHORT_KEYS = frozenset(
    {
        "al",
        _STEP_TYPE_KEY,
        _STEP_ENTITY_KEY,
        _ACTION_SERVICE_KEY,
        _ACTION_DATA_KEY,
        "t",
        _NESTED_CONDITIONS_KEY,
        _NESTED_ACTIONS_KEY,
    }
)

# Keys whose value is an opaque payload rather than a nested step: a
# service's own fields, an event's match data, a loop's items. A key named
# ``e`` or ``t`` in there is application data — a notification field, an
# MQTT payload — and the walk must not read it as a missed expansion.
_OPAQUE_VALUE_KEYS = frozenset(
    {
        "data",
        "data_template",
        "service_data",
        "event_data",
        "event_data_template",
        "variables",
        "trigger_variables",
        "metadata",
        "for_each",
        "payload",
        "response_variable",
    }
)

# Automation-block keys the expansion owns, in both the slim and the
# HA-canonical spelling. A block that carries both loses the unexpanded one.
_CONSUMED_BLOCK_KEYS = ("al", "t", "c", "x", "alias", "triggers", "conditions", "actions")


def _as_list(value: Any) -> list[Any]:
    """A slim section as a list. A lone object counts as a one-item section."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def _passthrough(step: dict[str, Any], consumed: tuple[str, ...]) -> dict[str, Any]:
    """Every key the expansion did not claim, in the model's own order."""
    return {k: v for k, v in step.items() if k not in consumed}


def _expand_step(step: Any, type_key: str) -> dict[str, Any]:
    """Expand one trigger or condition.

    ``p`` becomes the section's type key (``trigger`` for triggers,
    ``condition`` for conditions) and ``e`` becomes ``entity_id``. A step
    that omits ``p`` keeps its remaining keys so the automation validator
    reports the real problem ("each trigger must include a platform")
    instead of this function inventing a type.
    """
    if not isinstance(step, dict):
        raise SlimAutomationError(f"{type_key} is a {type(step).__name__}, not an object")
    expanded: dict[str, Any] = {}
    step_type = step.get(_STEP_TYPE_KEY)
    # ``{"p": ["time"]}`` is a known Qwen drift shape. Copied through, the
    # list is truthy so ``validate_automation_payload`` accepts it, but
    # ``_pad_time_trigger`` no longer recognises the trigger and Home
    # Assistant rejects the entry at reload -- after the file is on disk
    # and the user has been told the automation exists. The rule is
    # _qwen_repair's, imported rather than restated: this branch does not
    # run that module, but the two reach the same malformed trigger by
    # different routes and a second copy is how they drift apart.
    step_type = unwrap_listed_step_type(step_type)
    if step_type is not None:
        expanded[type_key] = step_type
    entity = step.get(_STEP_ENTITY_KEY)
    if entity is not None:
        expanded["entity_id"] = entity
    consumed = [_STEP_TYPE_KEY, _STEP_ENTITY_KEY]
    if type_key == "condition":
        # ``and`` / ``or`` / ``not`` conditions nest a condition list under
        # the same short key the top level uses.
        nested = step.get(_NESTED_CONDITIONS_KEY)
        if isinstance(nested, list | dict):
            expanded["conditions"] = _expand_conditions(nested)
            consumed.append(_NESTED_CONDITIONS_KEY)
        # The shorthand spelling of the same thing — ``{"or": [...]}`` with
        # no ``condition`` key at all — nests its list under the operator.
        for key in _SHORTHAND_CONDITION_KEYS:
            branch = step.get(key)
            if isinstance(branch, list | dict):
                expanded[key] = _expand_conditions(branch)
                consumed.append(key)
    expanded.update(_passthrough(step, tuple(consumed)))
    if type_key == "trigger":
        _pad_time_trigger(expanded)
    return expanded


def _pad_time_trigger(trigger: dict[str, Any]) -> None:
    """Give a time trigger's ``at`` the seconds HA's schema requires.

    The model writes ``"08:00"``; HA insists on ``HH:MM:SS`` and says so at
    reload, after the file is on disk and the user has been told the
    automation exists. Deliberately the same rule ``_qwen_repair`` applies
    on the full-envelope path: this branch does not run that module, and
    the two must not disagree about what a time trigger looks like.
    """
    if trigger.get("trigger") != "time":
        return
    at_value = trigger.get("at")
    if isinstance(at_value, str) and len(at_value) == 5 and at_value[2] == ":":
        trigger["at"] = at_value + ":00"


def _expand_conditions(value: Any) -> list[Any]:
    """A nested condition list, every entry expanded as a condition."""
    return [_expand_step(c, "condition") for c in _as_list(value)]


def _expand_actions(value: Any) -> list[Any]:
    """A nested action list, every entry expanded as an action.

    HA accepts a list of lists under ``parallel`` (``parallel: [[…], […]]``),
    so a list entry is recursed into rather than handed to the step
    expansion, which would have nothing to do with it.
    """
    expanded: list[Any] = []
    for step in _as_list(value):
        if isinstance(step, list):
            expanded.append(_expand_actions(step))
        else:
            expanded.append(_expand_action(step))
    return expanded


def _expand_choose_branch(branch: Any) -> Any:
    """One ``choose`` branch: the conditions, and the sequence they guard."""
    if not isinstance(branch, dict):
        raise SlimAutomationError(f"choose branch is a {type(branch).__name__}, not an object")
    expanded: dict[str, Any] = {}
    consumed: list[str] = []
    for key in ("conditions", _NESTED_CONDITIONS_KEY):
        if isinstance(branch.get(key), list | dict):
            expanded["conditions"] = _expand_conditions(branch[key])
            consumed.append(key)
            break
    for key in ("sequence", _NESTED_ACTIONS_KEY):
        if isinstance(branch.get(key), list | dict):
            expanded["sequence"] = _expand_actions(branch[key])
            consumed.append(key)
            break
    expanded.update(_passthrough(branch, tuple(consumed)))
    return expanded


def _expand_repeat(repeat: Any) -> Any:
    """``repeat``: the sequence, plus the ``while`` / ``until`` conditions.

    ``count`` and ``for_each`` are left alone — they are the loop's own
    data, not steps.
    """
    if not isinstance(repeat, dict):
        return repeat
    expanded: dict[str, Any] = {}
    consumed: list[str] = []
    for key in ("sequence", _NESTED_ACTIONS_KEY):
        if isinstance(repeat.get(key), list | dict):
            expanded["sequence"] = _expand_actions(repeat[key])
            consumed.append(key)
            break
    for key in _REPEAT_CONDITION_KEYS:
        if isinstance(repeat.get(key), list | dict):
            expanded[key] = _expand_conditions(repeat[key])
            consumed.append(key)
    expanded.update(_passthrough(repeat, tuple(consumed)))
    return expanded


def _expand_action(action: Any) -> dict[str, Any]:
    """Expand one action: ``s`` → ``service``, ``e`` → ``target.entity_id``,
    ``d`` → ``data``, plus the branch bodies a control-flow action nests.

    An action with no ``e`` keeps no target — that is the shape of a
    service call that takes none (``notify.notify`` carries its payload in
    ``data``), and inventing an empty target would make the validator
    reject it. The reverse — an ``e`` with no ``s`` — is a service call
    that lost its service, and is refused: written out as a bare
    ``target:`` it passes validation and fails at reload, by which point
    the user has been told the automation was created.

    An empty action is refused for the same reason. A step with neither a
    service nor a target but some other shape (``delay``, ``event``,
    ``scene``, ``stop``, a device action) is left alone — those are real
    Home Assistant actions the slim contract simply does not abbreviate.
    """
    if not isinstance(action, dict):
        raise SlimAutomationError(f"action is a {type(action).__name__}, not an object")
    if not action:
        raise SlimAutomationError("action is empty")
    # A condition used as an action step — the inline guard HA allows in
    # the middle of a sequence — is slimmed with the same ``p`` the
    # condition section uses, so it is expanded as a condition rather than
    # read as a service call that forgot its service.
    # ``and`` / ``or`` / ``not`` with no ``p`` at all is the shorthand
    # spelling of the same inline guard, already understood by
    # ``_expand_step`` and by the downstream action validator. Without it
    # here the branch's own slim keys are never inverted and the closing
    # guard refuses the whole automation over a step that was valid.
    _is_condition_action = _STEP_TYPE_KEY in action or any(
        isinstance(action.get(key), list | dict) for key in _SHORTHAND_CONDITION_KEYS
    )
    if _is_condition_action and _ACTION_SERVICE_KEY not in action:
        return _expand_step(action, "condition")
    expanded: dict[str, Any] = {}
    consumed = [_ACTION_SERVICE_KEY, _STEP_ENTITY_KEY]
    service = action.get(_ACTION_SERVICE_KEY)
    if service is not None:
        expanded["service"] = service
    entity = action.get(_STEP_ENTITY_KEY)
    if entity is not None:
        if service is None:
            raise SlimAutomationError(f"action targets {entity!r} with no service")
        expanded["target"] = {"entity_id": entity}
    data = action.get(_ACTION_DATA_KEY)
    if isinstance(data, dict):
        # An empty ``d`` is the contract's way of saying "no data", so it
        # is consumed and dropped rather than written back as ``data: {}``.
        if data:
            expanded["data"] = data
        consumed.append(_ACTION_DATA_KEY)
    elif data is not None:
        # Not an object, so not a service payload. Left where it is: the
        # closing guard sees the short key and refuses the automation,
        # which beats silently calling the service without its arguments.
        _LOGGER.warning("Slim automation action carried a non-object 'd': %r", data)
    for key in _BRANCH_CONDITION_KEYS:
        if isinstance(action.get(key), list | dict):
            expanded[key] = _expand_conditions(action[key])
            consumed.append(key)
    for key in _BRANCH_ACTION_KEYS:
        if isinstance(action.get(key), list | dict):
            expanded[key] = _expand_actions(action[key])
            consumed.append(key)
    # ``wait_for_trigger`` holds triggers, so neither the condition nor the
    # action branch above claims it and its ``p``/``e`` survive to the
    # closing guard, which refuses an automation the validator and the
    # entity walker both support.
    if isinstance(action.get(_WAIT_FOR_TRIGGER_KEY), list | dict):
        expanded[_WAIT_FOR_TRIGGER_KEY] = [
            _expand_step(t, "trigger") for t in _as_list(action[_WAIT_FOR_TRIGGER_KEY])
        ]
        consumed.append(_WAIT_FOR_TRIGGER_KEY)
    if isinstance(action.get("choose"), list | dict):
        expanded["choose"] = [_expand_choose_branch(b) for b in _as_list(action["choose"])]
        consumed.append("choose")
    if isinstance(action.get("repeat"), dict):
        expanded["repeat"] = _expand_repeat(action["repeat"])
        consumed.append("repeat")
    expanded.update(_passthrough(action, tuple(consumed)))
    return expanded


def _refuse_unexpanded(value: Any, path: str) -> None:
    """Refuse a payload that still carries a key the expansion owns.

    This closes the class rather than the instance. Every container the
    expansion knows about is inverted above; this walk is what happens when
    the model nests a step somewhere it does not. Without it, an action
    hidden in an unrecognised container reaches ``automations.yaml``
    unexamined: the entity check never reads its entity, the service check
    never reads its service, the risk classifier calls the automation
    normal, and the validator returns "valid".
    """
    if isinstance(value, dict):
        leftover = sorted(_SHORT_KEYS & value.keys())
        if leftover:
            raise SlimAutomationError(f"{path} still carries {', '.join(leftover)} after expansion")
        for key, nested in value.items():
            if key not in _OPAQUE_VALUE_KEYS:
                _refuse_unexpanded(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _refuse_unexpanded(item, f"{path}[{index}]")


def _synthesised_alias(response: str) -> str:
    """An alias built from the sentence the model wrote about the automation.

    The slim envelope always carries ``al``; when a turn comes back
    without one the alternative is a hard "automation alias is required"
    for an automation that is otherwise complete. The same four-word rule
    ``_qwen_repair`` uses on the full-envelope path, over the response
    sentence, which is the only description the slim contract sends.
    """
    words = str(response).split()[:4]
    return " ".join(word.strip(".,!?;:'\"") for word in words) or "Automation"


def expand_slim_automation(block: dict[str, Any], response: str = "") -> dict[str, Any]:
    """Expand the slim ``a`` block into an HA automation object.

    Emits the plural, HA-canonical section names. ``conditions`` is
    included only when the model sent some — the slim envelope omits the
    key entirely when there are none, and writing an empty list back would
    put a pointless ``conditions: []`` into the user's automations.yaml.

    No ``description`` and no ``mode`` are synthesised here: the slim
    contract drops both on purpose and the validator supplies the mode
    default, so guessing either would put words in the model's mouth. An
    ``alias`` is the exception — HA requires one — and is built from
    *response*, the sentence the model wrote about the automation, when
    the block came without.

    Raises :class:`SlimAutomationError` when the result still carries a
    short key anywhere — a step the expansion did not reach is a step the
    automation gates cannot read — and when a step is too malformed to
    become one.
    """
    if not isinstance(block, dict):
        raise SlimAutomationError(f"automation block is a {type(block).__name__}, not an object")
    expanded: dict[str, Any] = {}
    alias = block.get("al", block.get("alias"))
    if not (isinstance(alias, str) and alias.strip()):
        alias = _synthesised_alias(response)
    expanded["alias"] = alias
    triggers = block.get("t", block.get("triggers"))
    expanded["triggers"] = [_expand_step(t, "trigger") for t in _as_list(triggers)]
    conditions = block.get("c", block.get("conditions"))
    if conditions:
        expanded["conditions"] = [_expand_step(c, "condition") for c in _as_list(conditions)]
    expanded["actions"] = [
        _expand_action(x) for x in _as_list(block.get("x", block.get("actions")))
    ]
    # Forward compatibility: a key the model adds to the automation block
    # itself (``mode``, a future ``variables`` block, …) is carried over
    # rather than dropped. The section keys are excluded in both spellings:
    # carrying a full-word ``actions`` here would overwrite the section this
    # function just built, so a block that sent both would silently keep the
    # unexpanded copy.
    expanded.update(_passthrough(block, _CONSUMED_BLOCK_KEYS))
    _refuse_unexpanded(expanded, "automation")
    return expanded
