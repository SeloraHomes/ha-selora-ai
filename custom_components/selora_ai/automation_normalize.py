"""Shape normalization for an automation payload, before anything validates it.

Everything here is PURE and `hass`-free: it rewrites the payload a model (or a
user's YAML editor) produced into the shape Home Assistant accepts, and the
validators in `automation_utils` then judge what comes out. Splitting the two
apart is what keeps each side readable — normalization walks the whole tree and
rebuilds it, validation walks the same tree and only answers yes or no.

Three kinds of work live here, in the order the tree reaches them:

- **Coercion** of individual fields whose type a model routinely gets wrong
  (`_coerce_time_value` and friends), applied by `normalize_item`.
- **Null-dropping**, because HA rejects a padded `event: null` on the KEY, not
  the value, so the automation writes and then refuses to set up.
- **Window merging**, which is the one repair here that changes MEANING rather
  than shape: a midnight-crossing window split across two sibling conditions
  can never be true, and HA reads the pair on a single condition as exactly
  that window.
"""

from __future__ import annotations

import re
from typing import Any

from homeassistant.helpers.config_validation import ACTIONS_SET

from .telemetry import record_repair

# Logical condition operators that wrap a nested ``conditions:`` list (or
# HA's shorthand ``{or: [...]}`` form). Used only to know when to recurse,
# not as a closed allowlist of valid condition types — integrations can
# register their own condition platforms (e.g. ``condition: mqtt``) which
# must not be blocked here.
LOGICAL_CONDITION_TYPES: frozenset[str] = frozenset({"and", "or", "not"})


# How far the two bounds of a split sun window may reach towards each other
# and still be certainly unsatisfiable — see `_merge_sun_night_window`.
_MAX_WINDOW_SLACK = 3600.0

# A sun offset is `cv.time_period`: a signed "HH:MM:SS" string, signed seconds,
# or a mapping. ``None`` means the value is not one of those and so cannot be
# reasoned about — an absent offset is zero, which is a different answer.
_OFFSET_RE = re.compile(r"^(-)?(\d+):([0-5]\d)(?::([0-5]\d(?:\.\d+)?))?$")
_OFFSET_UNITS = {
    "days": 86400.0,
    "hours": 3600.0,
    "minutes": 60.0,
    "seconds": 1.0,
    "milliseconds": 0.001,
}


def _offset_seconds(value: Any) -> float | None:
    """A sun condition's offset as signed seconds, or ``None`` if unreadable."""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        if not (m := _OFFSET_RE.match(value.strip())):
            return None
        total = int(m[2]) * 3600 + int(m[3]) * 60 + float(m[4] or 0)
        return -total if m[1] else total
    if isinstance(value, dict):
        if not value or any(k not in _OFFSET_UNITS for k in value):
            return None
        try:
            return sum(float(value[k]) * _OFFSET_UNITS[k] for k in value)
        except (TypeError, ValueError):
            return None
    return None


# A sun condition carrying BOTH `after: sunset` and `before: sunrise` is
# special-cased by HA (`components/sun/condition.py`): it returns
# `now < sunrise + before_offset OR now > sunset + after_offset` — the window
# that wraps midnight, which is what "at night" means. Split across two
# SIBLING conditions the same pair is ANDed instead, and that AND is never
# true at any instant: `after: sunset` is false until this evening's sunset,
# `before: sunrise` is false from this morning's sunrise onwards, and no
# moment satisfies both. The automation is written, validates, reloads
# cleanly, shows the window the user asked for on the card — and its actions
# never run. Nothing downstream can notice, which is why the pair is merged
# here rather than reported.
#
# Merged, not refused: the two conditions carry exactly the four fields the
# merged one takes, so there is nothing to guess and a round-trip would only
# ask the model to retype them. The reverse pair (`after: sunrise` +
# `before: sunset`, daytime) is left alone — ANDed it is already correct, and
# so is either pair inside an `or`, which is the other way to spell the night
# window.
def _merge_sun_night_window(conditions: list[Any]) -> list[Any]:
    """Fold a sunset/sunrise pair in an AND-list into one sun condition.

    Conservative by construction: exactly one opener and one closer, each
    carrying its own bound and nothing of the other's, or the list is returned
    untouched. PURE — the merged condition is a new dict.
    """

    def _sun(cond: Any) -> bool:
        return isinstance(cond, dict) and cond.get("condition") == "sun"

    openers = [
        i
        for i, c in enumerate(conditions)
        if _sun(c) and c.get("after") == "sunset" and "before" not in c and "before_offset" not in c
    ]
    closers = [
        i
        for i, c in enumerate(conditions)
        if _sun(c) and c.get("before") == "sunrise" and "after" not in c and "after_offset" not in c
    ]
    if len(openers) != 1 or len(closers) != 1:
        return conditions

    opener, closer = conditions[openers[0]], conditions[closers[0]]
    # Everything that is not the bound has to agree, for the reason the time
    # merge below requires it: a condition also carries `enabled` and `alias`,
    # and only the opener's copy survives the fold. `enabled: false` on the
    # closer leaves an AND-list HA evaluates as "after sunset" and nothing
    # else — a working automation — which the merge would silently turn into
    # a live night window.
    if {k: v for k, v in opener.items() if k not in ("after", "after_offset")} != {
        k: v for k, v in closer.items() if k not in ("before", "before_offset")
    }:
        return conditions
    # The AND is only certainly empty while the two bounds cannot reach each
    # other. `after: sunset` is true from `sunset + after_offset`, `before:
    # sunrise` until `sunrise + before_offset`, and those overlap once
    # `after_offset - before_offset` falls below the negated length of the
    # day: at -12h and +12h the pair is an ordinary DAYTIME window, roughly
    # 06:00 to 18:00, and folding it would hand HA the sunset/sunrise special
    # case and make it true around the clock. Held to one hour of slack, which
    # a shift stated in minutes stays well inside — the reading it leaves open
    # is a location seeing under an hour of daylight, where the pair overlaps
    # briefly around sunset and the merge produces the window that was asked
    # for anyway.
    opens, closes = (
        _offset_seconds(opener.get("after_offset")),
        _offset_seconds(closer.get("before_offset")),
    )
    if opens is None or closes is None or opens - closes < -_MAX_WINDOW_SLACK:
        return conditions

    merged = dict(opener)
    merged["before"] = closer["before"]
    if "before_offset" in closer:
        merged["before_offset"] = closer["before_offset"]

    keep, drop = sorted((openers[0], closers[0]))
    record_repair("night_window_merge")
    return [merged if i == keep else c for i, c in enumerate(conditions) if i != drop]


# HH:MM or HH:MM:SS. A time condition's bound may also be an entity_id
# (`input_datetime.bedtime`) or a template, and those say nothing about which
# way the window runs — only a literal pair can be compared.
_CLOCK_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?$")


# The same failure with a clock instead of the sun, and the same fix: HA's
# time condition reads `after: "22:00:00"` + `before: "06:00:00"` on ONE
# condition as the window that wraps midnight, while two siblings are ANDed
# and no instant is both after 22:00 and before 06:00 today.
#
# Only a WRAPPING pair is merged. `after: "07:00:00"` + `before: "11:00:00"`
# split in two is already correct as an AND, so rewriting it would change a
# payload that works — and the wrap is the whole evidence that the split was
# not what the user asked for.
def _merge_time_night_window(conditions: list[Any]) -> list[Any]:
    """Fold a midnight-crossing `after`/`before` time pair into one condition.

    Conservative on the same terms as the sun merge, plus: both bounds must be
    literal clock times (an entity_id or template bound cannot be compared, so
    nothing shows the window wraps), and every other key the two conditions
    carry must match — a `weekday` list on one and not the other is two
    different windows, not one split in half.
    """

    def _time(cond: Any) -> bool:
        return isinstance(cond, dict) and cond.get("condition") == "time"

    def _clock(value: Any) -> tuple[int, int, int] | None:
        if not isinstance(value, str) or not (m := _CLOCK_RE.match(value.strip())):
            return None
        return int(m[1]), int(m[2]), int(m[3] or 0)

    openers = [
        i
        for i, c in enumerate(conditions)
        if _time(c) and _clock(c.get("after")) is not None and "before" not in c
    ]
    closers = [
        i
        for i, c in enumerate(conditions)
        if _time(c) and _clock(c.get("before")) is not None and "after" not in c
    ]
    if len(openers) != 1 or len(closers) != 1:
        return conditions

    opener, closer = conditions[openers[0]], conditions[closers[0]]
    if {k: v for k, v in opener.items() if k != "after"} != {
        k: v for k, v in closer.items() if k != "before"
    }:
        return conditions
    opens_at, closes_at = _clock(opener["after"]), _clock(closer["before"])
    if opens_at is None or closes_at is None or opens_at <= closes_at:
        return conditions

    merged = {**opener, "before": closer["before"]}
    keep, drop = sorted((openers[0], closers[0]))
    record_repair("night_window_merge")
    return [merged if i == keep else c for i, c in enumerate(conditions) if i != drop]


def merge_night_windows(conditions: list[Any]) -> list[Any]:
    """Both midnight-crossing window repairs, over one AND-list.

    Applies to condition lists HA evaluates as an AND — the automation's own
    `conditions`, an `and` wrapper, an action's `if`, a `choose` branch, a
    `repeat` guard — never to `or` (already the correct spelling of the same
    window) or `not` (its members are negated individually, so the never-true
    argument does not apply).
    """
    return _merge_time_night_window(_merge_sun_night_window(conditions))


# A state trigger's match keys, where the KEY'S PRESENCE carries meaning of its
# own and an explicit null must therefore survive. HA's state trigger computes
#
#     match_all = all(k not in config for k in (from, not_from, to, not_to))
#
# from presence, not from value, and `match_all` is what decides whether an
# attribute-only update fires the trigger: with it set, the trigger "will fire
# even if just an attribute changes". So `to: null` is HA's documented idiom for
# "any state change, but not attribute churn", and dropping the key turns a
# trigger the user wrote to be quiet into one that fires on every attribute
# update — a light reporting brightness, a sensor reporting a new reading.
#
# Scoped to a state trigger. On anything else these are unknown keys HA rejects
# outright, which is the failure the null-drop exists to prevent.
_STATE_MATCH_KEYS = frozenset({"to", "from", "not_to", "not_from"})


# ---------------------------------------------------------------------------
# Pattern-based value coercion
#
# Instead of hardcoding which fields need fixing, we detect the *type* of
# mistake and fix it based on context.  This catches LLM errors on any
# field -- even ones we haven't seen yet -- as long as the mistake fits
# a known pattern (wrong type for the context).
# ---------------------------------------------------------------------------

# Keys where HA expects a time string ("HH:MM:SS").
_TIME_KEYS = frozenset({"at", "after", "before"})

# Keys where HA expects a duration (dict or "HH:MM:SS").
_DURATION_KEYS = frozenset({"for", "delay"})

# Keys where HA expects a state string ("on"/"off"/"home"/etc.).
_STATE_KEYS = frozenset({"to", "from", "state"})

# Keys where boolean values are intentional (not state strings).
_BOOL_KEYS = frozenset({"initial_state", "enabled", "hide_entity", "continue_on_error"})


def _coerce_time_value(value: Any) -> str | None:
    """Coerce a value to ``HH:MM:SS`` time string.

    - Integers/floats in 0..86399 are treated as seconds since midnight.
    - Out-of-range numbers are stringified as a fallback.
    - Strings pass through unchanged.
    - ``None`` is returned as ``None`` (caller should remove the key).
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return None  # bool is nonsensical for time; drop it
    if isinstance(value, (int, float)):
        total = int(value)
        if total < 0 or total >= 86400:
            return str(value)
        hours = total // 3600
        minutes = (total % 3600) // 60
        seconds = total % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return str(value)


def _coerce_duration_value(value: Any) -> Any:
    """Coerce a raw number to a duration dict ``{"seconds": N}``."""
    if isinstance(value, bool):
        return value  # don't misinterpret booleans
    if isinstance(value, (int, float)):
        return {"seconds": int(value)}
    return value


def _coerce_state_string(value: Any) -> str | None:
    """Coerce a value that HA expects to be a state string.

    - Booleans become ``"on"``/``"off"``.
    - Other non-strings are stringified.
    - ``None`` is returned as ``None`` (caller should remove the key).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "on" if value else "off"
    if not isinstance(value, str):
        return str(value)
    return value


def normalize_item(item: dict[str, Any], *, is_action: bool = False) -> dict[str, Any]:
    """Apply pattern-based coercion to a single trigger, condition or action dict.

    Detection is by *key name* and *value type*, not by which section
    the item lives in.  This lets us catch the same class of mistake
    in triggers, conditions, or any future HA automation section.

    ``is_action`` is needed by the null-drop alone, and only because HA
    identifies an action BY WHICH KEY IT CARRIES — see the comment there.
    """
    fixed = dict(item)
    is_state_trigger = fixed.get("trigger") == "state" or fixed.get("platform") == "state"
    # HA's own set of the keys that NAME an action. Asked rather than listed, so
    # a discriminator added in a later release is covered without anyone here
    # noticing it appeared.
    discriminators = ACTIONS_SET.intersection(fixed) if is_action else frozenset()

    for key in list(fixed.keys()):
        if key in _BOOL_KEYS:
            continue  # intentional boolean -- leave it alone

        val = fixed[key]

        # --- Explicit nulls: the key was never meant to be here ------------
        # A model filling in every field of a schema it half-remembers emits
        # the unused ones as null, and HA's voluptuous schemas reject the KEY,
        # not the value: `extra keys not allowed @ data['event']. Got None`.
        # The automation validates here, is written, and then fails to set up,
        # so the user is told it was saved and HA tells them it is broken.
        #
        # Dropped by value rather than by name. The named pairs this replaces
        # (`to`/`from` on a trigger) were the two spellings that had been seen
        # in the wild, and the next one — `event` and `offset` volunteered onto
        # a sun CONDITION from the sun TRIGGER's schema — was not among them.
        # There is no request an explicit null expresses that omitting the key
        # does not, so nothing is discarded by removing it.
        if val is None:
            if is_state_trigger and key in _STATE_MATCH_KEYS:
                continue  # see _STATE_MATCH_KEYS — presence is the setting
            # An action is identified by WHICH KEY it carries
            # (`cv.determine_script_action` intersects the dict with
            # `ACTIONS_SET`), and two of those keys take a null as a real
            # value: `stop: null` and `set_conversation_response: null` both
            # validate, and both are the whole action. Dropping the null there
            # does not lose a value, it erases the action's identity — what is
            # left is `{}`, which HA cannot classify at all ("Unable to
            # determine action"), so the automation saves and fails at reload.
            #
            # Only when it is the ONLY discriminator present. A null one beside
            # a real one is padding — `{action: light.turn_on, event: null}` —
            # and keeping it would make the action AMBIGUOUS, letting HA pick
            # `event` over the service call the user asked for. That is also
            # why this cannot simply exempt `ACTIONS_SET`: `event` is in it,
            # and `event: null` volunteered onto a sun CONDITION is exactly the
            # padding this whole branch exists to remove.
            if key in discriminators and len(discriminators) == 1:
                continue
            fixed.pop(key)
            continue

        # --- Time keys: integers are seconds-since-midnight ----------------
        if key in _TIME_KEYS:
            result = _coerce_time_value(val)
            if result is None:
                fixed.pop(key, None)
            else:
                fixed[key] = result
            continue

        # --- Duration keys: raw numbers -> {"seconds": N} -----------------
        if key in _DURATION_KEYS:
            fixed[key] = _coerce_duration_value(val)
            continue

        # --- State keys: must always be strings ----------------------------
        if key in _STATE_KEYS:
            result = _coerce_state_string(val)
            if result is None:
                fixed.pop(key, None)
            else:
                fixed[key] = result
            continue

    return fixed


def normalize_condition(cond: Any, *, is_action: bool = False) -> Any:
    """``normalize_item`` over a condition and everything nested inside it.

    `automation_utils._validate_condition` already walks ``and`` / ``or`` / ``not`` — both the
    explicit ``condition:`` form and HA's shorthand — because a bad field two
    levels down fails the reload exactly like a top-level one. Normalization
    has to walk the same tree for the same reason: a sun condition wrapped in
    an ``or`` was left completely untouched, so every coercion and repair here
    applied only to conditions the model happened to write flat.

    Non-dicts pass through: HA accepts a bare template string as a condition.

    ``is_action`` applies to THIS condition only, never to the nested ones — a
    condition inside an ``or`` is a condition, but an inline
    condition-as-action step is an action, and `condition` is one of the keys
    HA identifies an action by. Without the flag `{condition: null}` was
    stripped to `{}`, which `automation_utils._validate_action_conditions` skips (no `condition`
    key left to notice) and HA then rejects outright as an action it cannot
    determine. Keeping the key hands it to `automation_utils._validate_condition`, which already
    refuses a condition with no type — so the model is told what is wrong
    instead of the automation failing at reload.
    """
    if not isinstance(cond, dict):
        return cond

    fixed = normalize_item(cond, is_action=is_action)
    # Both spellings of the nested list, matching `automation_utils._validate_condition`: the
    # explicit `conditions:` of an and/or/not, and the shorthand `{or: [...]}`
    # with no `condition:` key at all.
    for key in ("conditions", "and", "or", "not"):
        if key not in fixed:
            continue
        if key != "conditions" and "condition" in fixed:
            continue
        nested = fixed[key]
        if isinstance(nested, dict):
            # HA's singular-dict sugar. Normalized in place, left singular —
            # The validator handles either shape and rewriting it here
            # would change what gets written for no reason.
            fixed[key] = normalize_condition(nested)
        elif isinstance(nested, list):
            walked = [normalize_condition(sub) for sub in nested]
            # Only the AND spellings. `or` is how the same pair is correctly
            # written, and `not` negates its members one by one.
            if key == "and" or (key == "conditions" and fixed.get("condition") == "and"):
                walked = merge_night_windows(walked)
            fixed[key] = walked
    return fixed


def normalize_action_conditions(action: Any) -> Any:
    """``normalize_condition`` over every condition block inside an action step.

    Mirrors ``automation_utils._validate_action_conditions``' traversal, for the reason that one
    exists: HA takes condition blocks in several places inside an action
    sequence, and a bad field two levels down in ``choose[0].conditions`` fails
    the reload exactly like a top-level one. Validation walked all of them;
    normalization walked none, so a null padded onto a condition inside an
    ``if`` was written verbatim and HA refused to set the automation up.

    PURE. It rebuilds every container it descends into rather than editing in
    place, because ``normalize_item`` copies one level deep — the nested lists
    and dicts it hands back are the caller's own objects, so mutating them
    would rewrite the payload the caller still holds.

    Shapes are preserved. HA accepts a singular dict where a list is expected
    and both spellings validate, so normalizing one into the other would change
    what gets written for no reason.
    """
    if isinstance(action, list):
        return [normalize_action_conditions(step) for step in action]
    if not isinstance(action, dict):
        return action

    # A service call carries `action:` / `service:`, and its payload is not a
    # condition — walking it as one is what the validator skips these for.
    is_service_call = "action" in action or "service" in action
    if not is_service_call and (
        "condition" in action or any(op in action for op in LOGICAL_CONDITION_TYPES)
    ):
        # An inline condition-as-action step. `normalize_condition` already
        # covers it and everything nested under it.
        fixed = normalize_condition(action, is_action=True)
        if not isinstance(fixed, dict):
            return fixed
    else:
        # `normalize_item`, not a bare copy. A nested step is as much an action
        # as a top-level one, and a padded null discriminator inside a `choose`
        # or `if` is the whole original failure again: HA reads
        # `{action: light.turn_on, event: null}` as an EVENT action — the
        # intersection with `ACTIONS_SET` has two members and `event` wins on
        # `ACTIONS_MAP` order — and then rejects `action` as an extra key.
        # `is_action=True` so a SOLE null discriminator still survives here.
        fixed = normalize_item(action, is_action=True)

    def _conds(value: Any) -> Any:
        # Every condition block reached from here — `if`, a `choose` branch's
        # `conditions`, a `repeat` guard — is evaluated as an AND, so each is a
        # place a split sunset/sunrise window is never true.
        if isinstance(value, list):
            return merge_night_windows([normalize_condition(c) for c in value])
        if isinstance(value, dict):
            return normalize_condition(value)
        return value

    if "if" in fixed:
        fixed["if"] = _conds(fixed["if"])

    choose = fixed.get("choose")
    if isinstance(choose, list):
        branches = []
        for branch in choose:
            if not isinstance(branch, dict):
                branches.append(branch)
                continue
            new_branch = dict(branch)
            if "conditions" in new_branch:
                new_branch["conditions"] = _conds(new_branch["conditions"])
            if "sequence" in new_branch:
                new_branch["sequence"] = normalize_action_conditions(new_branch["sequence"])
            branches.append(new_branch)
        fixed["choose"] = branches

    repeat = fixed.get("repeat")
    if isinstance(repeat, dict):
        new_repeat = dict(repeat)
        for guard_key in ("while", "until"):
            if guard_key in new_repeat:
                new_repeat[guard_key] = _conds(new_repeat[guard_key])
        if "sequence" in new_repeat:
            new_repeat["sequence"] = normalize_action_conditions(new_repeat["sequence"])
        fixed["repeat"] = new_repeat

    # `wait_for_trigger` embeds full TRIGGER dicts — `automation_utils._validate_action_conditions`
    # gates them with `automation_utils._validate_trigger`, so they get the item-level
    # normalization a top-level trigger gets.
    wait_for = fixed.get("wait_for_trigger")
    if isinstance(wait_for, list):
        fixed["wait_for_trigger"] = [
            normalize_item(t) if isinstance(t, dict) else t for t in wait_for
        ]
    elif isinstance(wait_for, dict):
        fixed["wait_for_trigger"] = normalize_item(wait_for)

    for nested_key in ("default", "then", "else", "parallel", "sequence"):
        if nested_key in fixed:
            fixed[nested_key] = normalize_action_conditions(fixed[nested_key])

    return fixed
