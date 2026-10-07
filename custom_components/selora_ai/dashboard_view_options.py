"""A dashboard page's options, its badges, and its sections' options.

Lovelace validates none of this server-side: a theme that does not exist, a
badge for an entity that does not, a column count on a page that has no
columns are all stored and then silently ignored or rendered as errors. So
every option is checked here, by name, before anything is written, and an
option the page cannot use is refused rather than stored.

Options are set through one ``options`` object and removed through ``clear``,
the same split the view tools use for title and icon: a blank value reads as
"not set", never as "remove".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from .helpers import sanitize_untrusted_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

VIEW_OPTIONS: Final = (
    "theme",
    "background",
    "subview",
    "back_path",
    "visible",
    "max_columns",
    "dense_section_placement",
    "badges",
)
SECTION_OPTIONS: Final = ("column_span", "visibility")

SECTIONS_ONLY: Final = frozenset({"max_columns", "dense_section_placement"})
_BACKGROUND_KEYS: Final = frozenset(
    {"image", "opacity", "size", "alignment", "repeat", "attachment"}
)
_MAX_BADGES: Final = 30


class OptionError(ValueError):
    """An option that cannot be stored; the message says why."""


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OptionError("options must be an object, e.g. {'theme': 'Midnight'}.")
    return value


def check_view(view: dict[str, Any]) -> None:
    """What must hold of the page once every change is applied."""
    if view.get("back_path") and not view.get("subview"):
        raise OptionError("back_path is for a subview; set subview=true with it, or clear it too.")


def _bool(name: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise OptionError(f"{name} must be true or false.")


def _int(name: str, value: Any, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise OptionError(f"{name} must be a whole number from {low} to {high}.") from None
    if not low <= number <= high or (isinstance(value, float) and not value.is_integer()):
        raise OptionError(f"{name} must be a whole number from {low} to {high}.")
    return number


def _theme(hass: HomeAssistant, value: Any) -> str:
    name = str(value or "").strip()
    themes = hass.data.get("frontend_themes")
    if isinstance(themes, dict) and name not in themes:
        known = ", ".join(sorted(themes)[:20]) or "none installed"
        raise OptionError(
            f"There is no theme named '{sanitize_untrusted_text(name, 40)}'. Installed: {known}."
        )
    return name


def _background(value: Any) -> str | dict[str, Any]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict) and value:
        unknown = set(value) - _BACKGROUND_KEYS
        if unknown:
            raise OptionError(
                f"background takes {', '.join(sorted(_BACKGROUND_KEYS))}, not {sorted(unknown)}."
            )
        return dict(value)
    raise OptionError(
        "background is a CSS background (e.g. 'center / cover no-repeat url(/local/x.jpg)') "
        "or an object with image, opacity, size, alignment, repeat, attachment."
    )


async def _visible(hass: HomeAssistant, value: Any) -> bool | list[dict[str, str]]:
    """``true``/``false``, or the users who see the page, by id or name."""
    if isinstance(value, bool):
        return value
    if not isinstance(value, list) or not value:
        raise OptionError("visible is true, false, or a list of user names or ids.")
    users = [u for u in await hass.auth.async_get_users() if not u.system_generated]
    chosen: list[dict[str, str]] = []
    for ref in value:
        key = str(ref.get("user") if isinstance(ref, dict) else ref).strip()
        matches = [
            u for u in users if key in (u.id, u.name) or key.lower() == (u.name or "").lower()
        ]
        if len(matches) != 1:
            # The home's users are not listed here: view options can be set by a
            # credential that is not a Home Assistant admin.
            raise OptionError(
                f"No single user matches '{sanitize_untrusted_text(key, 40)}'. Pass a "
                "user's exact name or id."
            )
        if {"user": matches[0].id} not in chosen:
            chosen.append({"user": matches[0].id})
    return chosen


def _badges(hass: HomeAssistant, value: Any) -> list[Any]:
    """Entity ids or badge objects; every entity must resolve."""
    if not isinstance(value, list):
        raise OptionError("badges is a list of entity ids or badge objects.")
    if len(value) > _MAX_BADGES:
        raise OptionError(f"At most {_MAX_BADGES} badges.")
    badges: list[Any] = []
    for position, badge in enumerate(value):
        if isinstance(badge, str):
            badge = {"type": "entity", "entity": badge.strip()}
        if not isinstance(badge, dict):
            raise OptionError(f"badges[{position}] must be an entity id or an object.")
        badge = {"type": "entity", **badge}
        entity_id = badge.get("entity")
        if badge["type"] == "entity" and not entity_id:
            raise OptionError(f"badges[{position}] is an entity badge with no entity.")
        if entity_id is not None and hass.states.get(str(entity_id)) is None:
            raise OptionError(
                f"badges[{position}]: no entity '{sanitize_untrusted_text(entity_id, 60)}'. "
                "Resolve it with search_entities first."
            )
        badges.append(badge)
    return badges


def _conditions(name: str, value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(
        isinstance(c, dict) and c.get("condition") for c in value
    ):
        raise OptionError(
            f"{name} is a list of conditions, e.g. "
            "[{'condition': 'state', 'entity': 'input_boolean.guest', 'state': 'on'}]."
        )
    return [dict(c) for c in value]


async def async_apply_view_options(
    hass: HomeAssistant, view: dict[str, Any], options: dict[str, Any]
) -> list[str]:
    """Check every option, then set them on *view*. Raises before any is set."""
    options = _mapping(options)
    unknown = set(options) - set(VIEW_OPTIONS)
    if unknown:
        raise OptionError(f"Unknown view options {sorted(unknown)}; use {', '.join(VIEW_OPTIONS)}.")
    sections = view.get("type") == "sections"
    for name in SECTIONS_ONLY & set(options):
        if not sections:
            raise OptionError(f"{name} applies to a sections page only; this page is not one.")
    checked: dict[str, Any] = {}
    for name, value in options.items():
        if name == "theme":
            checked[name] = _theme(hass, value)
        elif name == "background":
            checked[name] = _background(value)
        elif name in ("subview", "dense_section_placement"):
            checked[name] = _bool(name, value)
        elif name == "back_path":
            checked[name] = str(value or "").strip()
        elif name == "visible":
            checked[name] = await _visible(hass, value)
        elif name == "max_columns":
            checked[name] = _int(name, value, 1, 10)
        elif name == "badges":
            checked[name] = _badges(hass, value)
    check_view({**view, **checked})
    view.update(checked)
    return sorted(checked)


def apply_section_options(section: dict[str, Any], options: dict[str, Any]) -> list[str]:
    """Check, then set, a section's options."""
    options = _mapping(options)
    unknown = set(options) - set(SECTION_OPTIONS)
    if unknown:
        raise OptionError(
            f"Unknown section options {sorted(unknown)}; use {', '.join(SECTION_OPTIONS)}."
        )
    checked: dict[str, Any] = {}
    if "column_span" in options:
        checked["column_span"] = _int("column_span", options["column_span"], 1, 4)
    if "visibility" in options:
        checked["visibility"] = _conditions("visibility", options["visibility"])
    section.update(checked)
    return sorted(checked)


def view_options_summary(view: dict[str, Any]) -> dict[str, Any]:
    """The options a page has set, as a read reports them."""
    summary: dict[str, Any] = {}
    for name in VIEW_OPTIONS:
        if name not in view:
            continue
        value = view[name]
        if name == "badges" and isinstance(value, list):
            summary[name] = [
                b.get("entity") if isinstance(b, dict) and b.get("entity") else b
                for b in value[:_MAX_BADGES]
            ]
        elif name == "background" and isinstance(value, str):
            summary[name] = sanitize_untrusted_text(value, 120)
        else:
            summary[name] = value
    return summary


def sections_summary(view: dict[str, Any]) -> list[dict[str, Any]]:
    """Each section of a sections page: its index, card count and options."""
    out: list[dict[str, Any]] = []
    for index, section in enumerate(view.get("sections") or []):
        if not isinstance(section, dict):
            continue
        row: dict[str, Any] = {"index": index, "card_count": len(section.get("cards") or [])}
        for name in SECTION_OPTIONS:
            if name in section:
                row[name] = section[name]
        out.append(row)
    return out
