"""The card vocabulary, handed to the model at the moment it composes one.

Lovelace has no server-side validator: it stores whatever it is given and the
frontend finds out, rendering "Unknown type encountered: fan" on the user's
wall. `dashboard_manager` catches the mistakes it can name — a domain used as
a card type, an entity that does not exist — but a refusal only says what NOT
to write.

This is the other half, and the cheaper one: the model composes better cards
when it has the taxonomy than when it is corrected afterwards. The same
observation ha-mcp acts on by shipping a card reference with every dashboard
response.

Kept SHORT on purpose. It rides on tool results that already carry a
dashboard's views and cards, inside a 16K result budget the executor trims
against, so a long document would push out the thing the model actually asked
for. Card OPTIONS are deliberately absent — the model knows Lovelace's card
schemas, and restating them here would be a second, staler copy of that
knowledge.

It is a PROMPT, not the validator, and must not read as one. Calling it
exhaustive would make the model refuse or rewrite valid requests — `picture`,
`entity-filter` and `logbook` are all real and all omitted here, and next
release adds more. Only the check in `dashboard_manager` refuses anything, and
it refuses one thing: a domain used as a card type.
"""

from __future__ import annotations

from typing import Final

# Grouped by what the user is trying to see, because that is how the request
# arrives ("show me the office lights"), not by HA's internal taxonomy.
CARD_REFERENCE: Final = (
    "Common Home Assistant card types — not the full catalogue, and Home "
    "Assistant adds more. A type not listed here may still be valid; what is "
    "NOT valid is a domain used as a card type (there is no 'fan', 'switch' or "
    "'cover' card — use tile). A card from a custom integration must be written "
    "'custom:<name>'.\n"
    "- Any entity: tile (the default — supports features like "
    "light-brightness, fan-speed, cover-open-close, target-temperature), "
    "entities (a list), glance, button, sensor, gauge, history-graph, "
    "statistics-graph.\n"
    "- Domain-specific: light, lock, thermostat, humidifier, water-heater, "
    "media-control, alarm-panel, weather-forecast, calendar, todo-list, map.\n"
    "- Layout: grid (columns), vertical-stack, horizontal-stack, conditional, "
    "area, heading, markdown, iframe, picture-glance, picture-entity.\n"
    "- A view of type 'sections' holds cards inside its sections; 'section' "
    "is not a card type."
)


def with_card_reference(result: dict[str, object]) -> dict[str, object]:
    """Attach the card vocabulary to a tool result.

    Placed on the READ a model does before composing — `get_dashboard`'s own
    description tells it to always call that first — so the taxonomy arrives
    once per turn, before any card is written, rather than on every write where
    it would be paid for repeatedly and mostly wasted.

    Never on an error result: a refusal has its own specific guidance, and
    burying that under a general reference makes the actionable part harder to
    find.
    """
    if result.get("error"):
        return result
    return {**result, "card_reference": CARD_REFERENCE}
