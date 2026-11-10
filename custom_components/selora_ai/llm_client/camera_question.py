"""The camera search a question about what the cameras saw opens with.

"Show when a white truck was in the street" always starts with the same
``find_camera_events`` call, and asking the model for it costs a whole round
of the full prompt before the search even runs. ``opening_search`` writes that
call from the message instead, and the chat loop runs it before the model's
first round (``LLMClient._stream_request_with_tools``), which then reads the
result as if it had asked. The model keeps every tool: a search that missed
what the user meant costs it one more call, as before.

English only, and it gives up rather than guess: a time it cannot read, a
command or an automation, a question about now rather than the past. A
question it passes on runs exactly as it did without it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import Any, Final

# Nouns for what a camera tracks, and the Frigate label that holds them. A
# truck or a van is a car to Frigate's coco labelmap (FOLDED_LABELS), so the
# noun stays in the query, which tells them apart. None: no label holds it.
_OBJECTS: Final[dict[str, tuple[str | None, bool]]] = {
    # noun: (label, whether the noun itself says more than the label)
    **dict.fromkeys(
        ("person", "people", "persons", "someone", "somebody", "anyone", "anybody"),
        ("person", False),
    ),
    **dict.fromkeys(("visitor", "visitors"), ("person", False)),
    **dict.fromkeys(
        (
            "man",
            "men",
            "woman",
            "women",
            "kid",
            "kids",
            "child",
            "children",
            "courier",
            "mailman",
            "postman",
            "stranger",
            "strangers",
        ),
        ("person", True),
    ),
    **dict.fromkeys(("car", "cars", "vehicle", "vehicles"), ("car", False)),
    **dict.fromkeys(
        ("truck", "trucks", "van", "vans", "pickup", "pickups", "suv", "suvs", "lorry"),
        ("car", True),
    ),
    **dict.fromkeys(("dog", "dogs"), ("dog", False)),
    **dict.fromkeys(("cat", "cats"), ("cat", False)),
    **dict.fromkeys(("bird", "birds"), ("bird", False)),
    **dict.fromkeys(("bicycle", "bicycles"), ("bicycle", False)),
    **dict.fromkeys(("motorcycle", "motorcycles", "motorbike"), ("motorcycle", False)),
    **dict.fromkeys(("bus", "buses", "package", "packages", "parcel", "parcels"), (None, True)),
}

# How the question asks about the past.
_OPENER: Final = re.compile(
    r"^(?:when|what time|show(?: me)?|find|list|was|were|did|has|have|had|any|"
    r"how many|who|which camera|were there|was there)\b"
)
_PAST: Final = re.compile(
    r"\b(?:was|were|did|saw|came|come by|passed|stopped|visited|went|drove|parked|"
    r"seen|spotted|showed up|arrived|left|delivered|rang|been|how many)\b"
)
# Not a question about the past: a command, an automation, a camera's setup.
_NOT_A_SEARCH: Final = re.compile(
    r"\b(?:notify|alert|tell me when|let me know|remind|every time|whenever|each time|"
    r"automation|automate|turn|switch|lock|unlock|locked|unlocked|open|close|arm|disarm|"
    r"track|tracks|"
    r"detect|detects|start|stop|enable|disable|record|recording|add|remove|delete|"
    r"set up|configure|zone|zones|live|stream|then)\b|\bif\b"
)
# What makes it a question for the cameras rather than about a thing: "was the
# dog fed" and "has the car been charged" name what a camera tracks too. A
# description, a face, a camera or one of these is needed besides the noun.
_WATCHED: Final = re.compile(
    r"\b(?:seen|saw|spotted|came by|come by|came to|come to|passed|walked|showed up|"
    r"stopped by|visited|drove|parked|arrived|delivered|dropped off|rang|camera|cameras|"
    r"clip|clips|footage|recorded)\b"
)
_PLACE: Final = re.compile(
    r"\b(?:at|in|on|outside|near|by|past|through|around|in front of) (?:the |my |our |a )?"
    r"(?:[a-z]+ )?(?:door|doorstep|doorbell|driveway|street|road|porch|yard|backyard|"
    r"garden|gate|garage|entrance|sidewalk|lawn|deck|patio)\b"
)
# Times this module can turn into a range; any other mention of a time gives up.
_RANGES: Final = re.compile(
    r"\b(?:(?:in )?the (?:last|past) (?P<n>\d{1,3}|an?|one|two|three|few) "
    r"(?P<unit>hours?|days?|weeks?)|(?:in )?the (?:last|past) (?P<one>hour|day|week|month)|"
    r"this (?P<part>morning|afternoon|evening|month)|(?P<word>today|tonight|yesterday|"
    r"last night))\b"
)
_OTHER_TIME: Final = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|weekend|january|"
    r"february|march|april|may|june|july|august|september|october|november|december|"
    r"ago|since|before|after|between|until|o'clock|noon|midnight|morning|afternoon|"
    r"evening|night|hours?|days?|weeks?|months?)\b|\d"
)
_COUNTS: Final = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "few": 3}
_UNIT_HOURS: Final = {"hour": 1, "day": 24, "week": 168}
_DETERMINERS: Final = frozenset({"a", "an", "the", "any", "some", "that", "this", "my", "our"})
# Words that end a description: where, when, or the rest of the question.
_ENDS_DESCRIPTION: Final = frozenset(
    {
        "in",
        "on",
        "at",
        "near",
        "by",
        "outside",
        "around",
        "through",
        "from",
        "to",
        "was",
        "were",
        "is",
        "are",
        "came",
        "come",
        "passed",
        "today",
        "yesterday",
        "tonight",
        "this",
        "last",
        "and",
        "or",
        "did",
        "how",
        "many",
    }
)
_DESCRIBES_MORE: Final = frozenset({"with", "carrying", "wearing", "holding", "pulling"})
_WORD: Final = re.compile(r"[a-z0-9']+|[^\sa-z0-9']")
# A description with no time asks about any time; a week keeps the search on
# the events a home still has (Frigate's default retention) and in reach.
_DESCRIBED_HOURS: Final = 168.0


def points_at_cameras(message: str) -> bool:
    """Whether the message itself is about what the cameras saw: a camera, a
    place one watches, or seeing. Only then does the card that changes what
    the cameras look for stand in for the reply: the model searches "was the
    dog fed?" too, and its answer comes first, the card under it."""
    text = _normalize(message)
    return bool(_WATCHED.search(text) or _PLACE.search(text))


def asks_about_the_past(message: str) -> bool:
    """Whether the message could be a camera question: the cheap test, before
    anything is read from Frigate for ``opening_search``."""
    text = _normalize(message)
    return bool(_OPENER.match(text)) and not _NOT_A_SEARCH.search(text)


def opening_search(
    message: str,
    now: datetime,
    *,
    cameras: dict[str, str] | None = None,
    names: list[str] | None = None,
) -> dict[str, Any] | None:
    """``find_camera_events`` arguments for a question about what the cameras
    saw, or None when the message is not plainly one.

    ``now`` is local time. ``cameras`` maps Frigate camera keys to the names
    people use; ``names`` are the faces and plates Frigate recognises.
    """
    if not asks_about_the_past(message):
        return None
    text = _normalize(message)
    words = _WORD.findall(text)
    named = [n for n in names or () if _has_phrase(text, n.lower())]
    labels: list[str] = []
    query = ""
    for index, word in enumerate(words):
        if word not in _OBJECTS:
            continue
        label, descriptive = _OBJECTS[word]
        if label and label not in labels:
            labels.append(label)
        if not query:
            described = _description(words, index)
            if described != word or descriptive:
                query = described
    # A name is a face or a plate, and the list does not say which: it filters
    # on its own, never adding a label.
    if not labels and not query and not named:
        return None
    chosen = [key for key, name in (cameras or {}).items() if _names_camera(text, key, name)]
    if not (query or named or chosen or _WATCHED.search(text) or _PLACE.search(text)):
        return None
    window = _range(text, now)
    if window is None or not (window or _PAST.search(text)):
        return None
    arguments: dict[str, Any] = {}
    if labels:
        arguments["objects"] = labels
    if query:
        arguments["query"] = query
    if named:
        arguments["names"] = named
    if chosen:
        arguments["cameras"] = chosen
    if window:
        arguments.update(window)
    elif query or named:
        arguments["hours"] = _DESCRIBED_HOURS
    return arguments


def _normalize(message: str) -> str:
    return " ".join(message.lower().replace("\u2019", "'").split()).rstrip("?.! ")


def _has_phrase(text: str, phrase: str) -> bool:
    return bool(phrase) and re.search(rf"\b{re.escape(phrase)}\b", text) is not None


def _names_camera(text: str, key: str, name: str) -> bool:
    return _has_phrase(text, name.lower()) or _has_phrase(text, key.lower().replace("_", " "))


def _description(words: list[str], index: int) -> str:
    """The noun at ``index`` with the words describing it: "white truck",
    "someone with a backpack"."""
    start = index
    while (
        start > 0
        and index - start < 3
        and words[start - 1] not in _DETERMINERS
        and words[start - 1] not in _ENDS_DESCRIPTION
        and words[start - 1].isalnum()
        and not _OPENER.match(words[start - 1])
        and not _PAST.match(words[start - 1])
    ):
        start -= 1
    end = index + 1
    # "with a backpack", and "in a red coat" but not "in the street".
    worn = words[end : end + 2] in (["in", "a"], ["in", "an"])
    if end < len(words) and (words[end] in _DESCRIBES_MORE or worn):
        end += 2 if worn else 1
        while (
            end < len(words)
            and end - index <= 5
            and words[end] not in _ENDS_DESCRIPTION
            and words[end].isalnum()
        ):
            end += 1
    return " ".join(words[start:end])


def _range(text: str, now: datetime) -> dict[str, Any] | None:
    """The range the question names: {} for none, None for a time it cannot read."""
    found = list(_RANGES.finditer(text))
    if len(found) > 1:
        return None
    rest = _RANGES.sub(" ", text)
    if _OTHER_TIME.search(rest):
        return None
    if not found:
        return {}
    match = found[0]
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if count := match["n"]:
        hours = (_COUNTS.get(count) or int(count)) * _UNIT_HOURS[match["unit"].rstrip("s")]
        return {"hours": float(hours)}
    if one := match["one"]:
        return {"hours": 720.0 if one == "month" else float(_UNIT_HOURS[one])}
    # A calendar week ("this week", "last week") starts on a day that depends on
    # who asks, so it is left to the model: never matched, it reads as a time
    # this cannot place.
    word = match["part"] or match["word"]
    if word == "month":
        return {"start": midnight.replace(day=1).isoformat()}
    if word == "today":
        return {"start": midnight.isoformat()}
    if word == "morning":
        noon = midnight + timedelta(hours=12)
        return {"start": midnight.isoformat(), **({"end": noon.isoformat()} if noon < now else {})}
    if word == "afternoon":
        return _since(midnight + timedelta(hours=12), now)
    if word in ("evening", "tonight"):
        return _since(midnight + timedelta(hours=17), now)
    if word == "yesterday":
        return {
            "start": (midnight - timedelta(days=1)).isoformat(),
            "end": midnight.isoformat(),
        }
    # last night
    return {
        "start": (midnight - timedelta(hours=6)).isoformat(),
        "end": min(midnight + timedelta(hours=6), now).isoformat(),
    }


def _since(start: datetime, now: datetime) -> dict[str, Any] | None:
    return {"start": start.isoformat()} if start < now else None
