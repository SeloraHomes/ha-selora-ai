"""Tests for the deterministic dashboard-card install stage.

Covers manifest parsing of the ``dashboard:`` block, placeholder
substitution, and the Lovelace storage insert/remove against a light
fake of HA's LovelaceData (no full ``hass`` needed — the module only
touches ``hass.data[LOVELACE_DATA]`` and each dashboard's
``async_load`` / ``async_save`` / ``mode``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.selora_ai.recipes.dashboard import (
    CARD_TAG_KEY,
    async_insert_card,
    async_place_card,
    async_remove_cards,
    list_writable_dashboards,
    resolve_card,
)
from custom_components.selora_ai.recipes.manifest import (
    DashboardCardSpec,
    ManifestError,
    _coerce_dashboard,
)

# Import the real symbols so the fakes behave like the code expects.
from homeassistant.components.lovelace.const import (  # noqa: E402
    LOVELACE_DATA,
    ConfigNotFound,
)


# ── Fakes ───────────────────────────────────────────────────────────


class FakeDashboard:
    """Stand-in for a LovelaceStorage / LovelaceYAML config."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        mode: str = "storage",
        not_found: bool = False,
    ) -> None:
        self.mode = mode
        self._config = config
        self._not_found = not_found
        self.saved: dict[str, Any] | None = None

    async def async_load(self, force: bool) -> dict[str, Any]:
        if self._not_found:
            raise ConfigNotFound
        return self._config if self._config is not None else {"views": []}

    async def async_save(self, config: dict[str, Any]) -> None:
        self.saved = config
        self._config = config


def _hass_with(dashboards: dict[str | None, FakeDashboard]) -> Any:
    data = {LOVELACE_DATA: SimpleNamespace(dashboards=dashboards)}
    return SimpleNamespace(data=data)


def _spec(**kw: Any) -> DashboardCardSpec:
    base: dict[str, Any] = {
        "card": {"type": "button", "entity": "${role:toggle}", "name": "Baby sleeping"},
    }
    base.update(kw)
    return DashboardCardSpec(**base)


# ── Manifest parsing ────────────────────────────────────────────────


def test_coerce_dashboard_defaults() -> None:
    spec = _coerce_dashboard({"card": {"type": "button", "entity": "x.y"}})
    assert spec is not None
    assert spec.target is None  # default dashboard
    assert spec.view == 0
    assert spec.card["type"] == "button"


def test_coerce_dashboard_default_target_keyword() -> None:
    spec = _coerce_dashboard(
        {"target": "default", "view": "Bedroom", "card": {"type": "entity", "entity": "x.y"}}
    )
    assert spec is not None
    assert spec.target is None
    assert spec.view == "Bedroom"


def test_coerce_dashboard_explicit_url_path() -> None:
    spec = _coerce_dashboard({"target": "lovelace-home", "card": {"type": "button"}})
    assert spec is not None
    assert spec.target == "lovelace-home"


def test_coerce_dashboard_none() -> None:
    assert _coerce_dashboard(None) is None


def test_coerce_dashboard_rejects_missing_type() -> None:
    with pytest.raises(ManifestError):
        _coerce_dashboard({"card": {"entity": "x.y"}})


def test_coerce_dashboard_rejects_non_mapping_card() -> None:
    with pytest.raises(ManifestError):
        _coerce_dashboard({"card": "nope"})


# ── Placeholder substitution ────────────────────────────────────────


def test_resolve_card_substitutes_role_and_stamps_tag() -> None:
    card = resolve_card(
        _spec(),
        "baby-sleep",
        {"toggle": ["input_boolean.baby_sleeping"]},
        {},
    )
    assert card["entity"] == "input_boolean.baby_sleeping"
    assert card["name"] == "Baby sleeping"
    assert card[CARD_TAG_KEY] == "baby-sleep"


def test_resolve_card_role_without_binding_is_empty() -> None:
    card = resolve_card(_spec(), "s", {}, {})
    assert card["entity"] == ""


def test_resolve_card_input_preserves_type_for_whole_string() -> None:
    spec = DashboardCardSpec(card={"type": "x", "hours": "${input:n}"})
    card = resolve_card(spec, "s", {}, {"n": 8})
    assert card["hours"] == 8  # int preserved, not "8"


def test_resolve_card_embedded_placeholder_interpolates() -> None:
    spec = DashboardCardSpec(card={"type": "x", "name": "Tap ${role:toggle}"})
    card = resolve_card(spec, "s", {"toggle": ["input_boolean.b"]}, {})
    assert card["name"] == "Tap input_boolean.b"


# ── Insert ──────────────────────────────────────────────────────────


async def test_insert_appends_card_to_storage_dashboard() -> None:
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with({None: dash})

    result = await async_insert_card(
        hass,
        slug="baby-sleep",
        spec=_spec(),
        bindings={"toggle": ["input_boolean.baby_sleeping"]},
        inputs={},
    )

    assert result.ok and result.reason == "inserted"
    cards = dash.saved["views"][0]["cards"]
    assert len(cards) == 1
    assert cards[0]["entity"] == "input_boolean.baby_sleeping"
    assert cards[0][CARD_TAG_KEY] == "baby-sleep"


async def test_insert_is_idempotent() -> None:
    dash = FakeDashboard(
        {"views": [{"cards": [{"type": "button", "entity": "old", CARD_TAG_KEY: "baby-sleep"}]}]}
    )
    hass = _hass_with({None: dash})

    await async_insert_card(
        hass,
        slug="baby-sleep",
        spec=_spec(),
        bindings={"toggle": ["input_boolean.baby_sleeping"]},
        inputs={},
    )

    cards = dash.saved["views"][0]["cards"]
    # Replaced, not duplicated.
    assert len(cards) == 1
    assert cards[0]["entity"] == "input_boolean.baby_sleeping"


async def test_insert_seeds_view_when_config_not_found() -> None:
    dash = FakeDashboard(not_found=True)
    hass = _hass_with({None: dash})

    result = await async_insert_card(
        hass,
        slug="s",
        spec=_spec(),
        bindings={"toggle": ["input_boolean.b"]},
        inputs={},
    )

    assert result.ok
    assert dash.saved["views"][0]["cards"][0][CARD_TAG_KEY] == "s"


async def test_insert_skips_yaml_mode() -> None:
    dash = FakeDashboard({"views": []}, mode="yaml")
    hass = _hass_with({None: dash})

    result = await async_insert_card(
        hass,
        slug="s",
        spec=_spec(),
        bindings={},
        inputs={},
    )

    assert not result.ok
    assert result.reason == "yaml_mode"
    assert dash.saved is None  # never written


async def test_insert_missing_dashboard_returns_not_writable() -> None:
    hass = _hass_with({})  # no default dashboard
    result = await async_insert_card(
        hass,
        slug="s",
        spec=_spec(),
        bindings={},
        inputs={},
    )
    assert not result.ok


async def test_insert_view_not_found() -> None:
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with({None: dash})
    result = await async_insert_card(
        hass,
        slug="s",
        spec=_spec(view="Nonexistent"),
        bindings={"toggle": ["x.y"]},
        inputs={},
    )
    assert not result.ok and result.reason == "view_not_found"


async def test_insert_targets_named_dashboard() -> None:
    home = FakeDashboard({"views": [{"cards": []}]})
    other = FakeDashboard({"views": [{"cards": []}]})
    hass = _hass_with({None: home, "lovelace-home": other})

    await async_insert_card(
        hass,
        slug="s",
        spec=_spec(target="lovelace-home"),
        bindings={"toggle": ["x.y"]},
        inputs={},
    )

    assert home.saved is None
    assert len(other.saved["views"][0]["cards"]) == 1


async def test_insert_into_sections_view_uses_first_section() -> None:
    # A ``type: sections`` view ignores top-level ``cards`` — the card
    # must land in a section's ``cards`` to render.
    dash = FakeDashboard(
        {"views": [{"type": "sections", "sections": [{"type": "grid", "cards": []}]}]}
    )
    hass = _hass_with({None: dash})

    result = await async_insert_card(
        hass,
        slug="baby-sleep",
        spec=_spec(),
        bindings={"toggle": ["input_boolean.baby_sleeping"]},
        inputs={},
    )

    assert result.ok and result.reason == "inserted"
    view = dash.saved["views"][0]
    assert view.get("cards") in (None, [])  # not placed at the top level
    section_cards = view["sections"][0]["cards"]
    assert len(section_cards) == 1
    assert section_cards[0]["entity"] == "input_boolean.baby_sleeping"
    assert section_cards[0][CARD_TAG_KEY] == "baby-sleep"


async def test_insert_into_sections_view_seeds_section_when_none() -> None:
    dash = FakeDashboard({"views": [{"type": "sections", "sections": []}]})
    hass = _hass_with({None: dash})

    result = await async_insert_card(
        hass,
        slug="s",
        spec=_spec(),
        bindings={"toggle": ["input_boolean.b"]},
        inputs={},
    )

    assert result.ok
    sections = dash.saved["views"][0]["sections"]
    assert len(sections) == 1
    assert sections[0]["cards"][0][CARD_TAG_KEY] == "s"


async def test_insert_sections_view_is_idempotent() -> None:
    dash = FakeDashboard(
        {
            "views": [
                {
                    "type": "sections",
                    "sections": [
                        {
                            "type": "grid",
                            "cards": [{"type": "button", "entity": "old", CARD_TAG_KEY: "s"}],
                        }
                    ],
                }
            ]
        }
    )
    hass = _hass_with({None: dash})

    await async_insert_card(
        hass,
        slug="s",
        spec=_spec(),
        bindings={"toggle": ["input_boolean.new"]},
        inputs={},
    )

    cards = dash.saved["views"][0]["sections"][0]["cards"]
    assert len(cards) == 1  # replaced, not duplicated
    assert cards[0]["entity"] == "input_boolean.new"


async def test_remove_strips_tagged_card_from_sections_view() -> None:
    dash = FakeDashboard(
        {
            "views": [
                {
                    "type": "sections",
                    "sections": [
                        {
                            "type": "grid",
                            "cards": [
                                {"type": "button", CARD_TAG_KEY: "s"},
                                {"type": "markdown", "content": "mine"},
                            ],
                        }
                    ],
                }
            ]
        }
    )
    hass = _hass_with({None: dash})

    removed = await async_remove_cards(hass, "s")

    assert removed == 1
    kept = dash.saved["views"][0]["sections"][0]["cards"]
    assert {c.get("type") for c in kept} == {"markdown"}


# ── Remove ──────────────────────────────────────────────────────────


async def test_remove_strips_only_tagged_cards() -> None:
    dash = FakeDashboard(
        {
            "views": [
                {
                    "cards": [
                        {"type": "button", CARD_TAG_KEY: "s"},
                        {"type": "markdown", "content": "user's own"},
                        {"type": "entity", CARD_TAG_KEY: "other-recipe"},
                    ]
                }
            ]
        }
    )
    hass = _hass_with({None: dash})

    removed = await async_remove_cards(hass, "s")

    assert removed == 1
    kept = dash.saved["views"][0]["cards"]
    assert {c.get("type") for c in kept} == {"markdown", "entity"}


async def test_remove_noop_when_nothing_tagged() -> None:
    dash = FakeDashboard({"views": [{"cards": [{"type": "markdown"}]}]})
    hass = _hass_with({None: dash})

    removed = await async_remove_cards(hass, "s")

    assert removed == 0
    assert dash.saved is None  # nothing to write → no save


async def test_place_card_uses_given_tag() -> None:
    dash = FakeDashboard({"views": [{"cards": []}]})
    hass = _hass_with({None: dash})

    result = await async_place_card(
        hass,
        card={"type": "button", "entity": "x.y"},
        tag="my-tag",
    )

    assert result.ok
    assert dash.saved["views"][0]["cards"][0][CARD_TAG_KEY] == "my-tag"


# ── Dashboard listing ───────────────────────────────────────────────


def test_list_writable_dashboards_filters_yaml_and_orders_default_first() -> None:
    dashboards = {
        "bedroom": FakeDashboard({}, mode="storage"),
        None: FakeDashboard({}, mode="storage"),
        "readonly": FakeDashboard({}, mode="yaml"),
    }
    # Give the named storage dashboard a title via its .config attr.
    dashboards["bedroom"].config = {"title": "Bedroom"}
    hass = _hass_with(dashboards)

    listed = list_writable_dashboards(hass)

    # YAML dashboard excluded; default (None) first; title resolved.
    assert [d["url_path"] for d in listed] == [None, "bedroom"]
    assert listed[0]["title"] == "Overview"
    assert listed[1]["title"] == "Bedroom"


def test_list_writable_dashboards_empty_when_no_lovelace() -> None:
    hass = SimpleNamespace(data={})
    assert list_writable_dashboards(hass) == []


async def test_remove_skips_yaml_dashboards() -> None:
    dash = FakeDashboard(
        {"views": [{"cards": [{"type": "button", CARD_TAG_KEY: "s"}]}]}, mode="yaml"
    )
    hass = _hass_with({None: dash})

    removed = await async_remove_cards(hass, "s")

    assert removed == 0
    assert dash.saved is None


# ── Renderer: indentation preserved for embedded HA templates ───────
# Regression: lstrip_blocks=True stripped the indentation before a
# ``{% raw %}`` inside a YAML block scalar, collapsing the embedded HA
# runtime template to column 1 and producing invalid YAML
# ("found character '%' that cannot start any token").


def test_renderer_preserves_block_scalar_indentation() -> None:
    import yaml as pyyaml

    from custom_components.selora_ai.recipes.renderer import _build_environment

    tpl = (
        "automation:\n"
        "  - id: x\n"
        "    action:\n"
        "      - variables:\n"
        "          targets: >-\n"
        "            {% raw %}{% set controllers = [\n"
        "                 'a', 'b'\n"
        "               ] %}\n"
        "            {{ controllers }}{% endraw %}\n"
    )
    env = _build_environment({"t": tpl})
    out = env.get_template("t").render(slug="s", inputs={})
    # The embedded ``{% set %}`` must stay indented under ``targets:`` so
    # YAML treats it as a folded-scalar value, not a stray token.
    parsed = pyyaml.safe_load(out)
    assert "{% set controllers" in parsed["automation"][0]["action"][0]["variables"]["targets"]
