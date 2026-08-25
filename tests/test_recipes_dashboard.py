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

# Import the real symbols so the fakes behave like the code expects.
from homeassistant.components.lovelace.const import (  # noqa: E402
    LOVELACE_DATA,
    ConfigNotFound,
)
import pytest

from custom_components.selora_ai.recipes.dashboard import (
    CARD_TAG_KEY,
    async_insert_card,
    async_place_card,
    async_remove_cards,
    device_ids_for_bindings,
    list_writable_dashboards,
    resolve_card,
)
from custom_components.selora_ai.recipes.manifest import (
    DashboardCardSpec,
    ManifestError,
    _coerce_dashboard,
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
        auto_gen: bool = False,
    ) -> None:
        self.mode = mode
        self._config = config
        self._not_found = not_found
        self._auto_gen = auto_gen
        self.saved: dict[str, Any] | None = None

    async def async_load(self, force: bool) -> dict[str, Any]:
        if self._not_found:
            raise ConfigNotFound
        return self._config if self._config is not None else {"views": []}

    async def async_get_info(self) -> dict[str, Any]:
        """Every real LovelaceConfig has this; ConfigNotFound alone does not say
        whether HA is generating the dashboard or it is genuinely blank."""
        return {"mode": "auto-gen" if self._auto_gen else self.mode}

    async def async_save(self, config: dict[str, Any]) -> None:
        self.saved = config
        self._config = config


class FakeResources:
    """Stand-in for ResourceStorageCollection / ResourceYAMLCollection."""

    def __init__(self, urls: list[str], *, raises: bool = False) -> None:
        self._urls = list(urls)
        self._raises = raises
        self.info_calls = 0

    async def async_get_info(self) -> dict[str, int]:
        self.info_calls += 1
        if self._raises:
            raise RuntimeError("storage unavailable")
        return {"resources": len(self._urls)}

    def async_items(self) -> list[dict[str, Any]]:
        return [{"url": u, "type": "module"} for u in self._urls]

    def add(self, url: str) -> None:
        """Mirror what a real install does, so a caller that re-checks
        sees what it just registered."""
        self._urls.append(url)

    def remove(self, url: str) -> None:
        self._urls = [u for u in self._urls if u != url]


def _hass_with(
    dashboards: dict[str | None, FakeDashboard],
    resources: Any = None,
    config_dir: Any = None,
) -> Any:
    async def async_add_executor_job(func: Any, *args: Any) -> Any:
        return func(*args)

    async def register_static_paths(paths: Any) -> None:
        return None

    data = {
        LOVELACE_DATA: SimpleNamespace(dashboards=dashboards, resources=resources),
        "selora_ai": {},
    }
    base = str(config_dir) if config_dir else "/tmp/selora-test-config"
    return SimpleNamespace(
        data=data,
        config=SimpleNamespace(path=lambda *p: "/".join([base, *p])),
        async_add_executor_job=async_add_executor_job,
        http=SimpleNamespace(async_register_static_paths=register_static_paths),
    )


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


def test_resolve_card_substitutes_device() -> None:
    """Custom cards that target a device (the toothbrush card) get the
    device id of the role's first bound entity."""
    spec = DashboardCardSpec(
        card={"type": "custom:toothbrush-card", "device_id": "${device:brush}"}
    )
    card = resolve_card(spec, "s", {"brush": ["sensor.brush_duration"]}, {}, {"brush": "dev123"})
    assert card["device_id"] == "dev123"


def test_resolve_card_device_without_mapping_is_empty() -> None:
    """An entity with no device (a helper) leaves the role out of the map;
    the card gets an empty string, never a literal ${device:...}."""
    spec = DashboardCardSpec(card={"type": "x", "device_id": "${device:brush}"})
    card = resolve_card(spec, "s", {"brush": ["sensor.brush_duration"]}, {})
    assert card["device_id"] == ""


def test_resolve_card_substitutes_device_inside_nested_card() -> None:
    """Stacks nest cards in a list — substitution has to reach into them."""
    spec = DashboardCardSpec(
        card={
            "type": "vertical-stack",
            "cards": [
                {"type": "custom:toothbrush-card", "device_id": "${device:brush}"},
                {"type": "entity", "entity": "${role:brush}"},
            ],
        }
    )
    card = resolve_card(spec, "s", {"brush": ["sensor.brush_duration"]}, {}, {"brush": "dev123"})
    assert card["cards"][0]["device_id"] == "dev123"
    assert card["cards"][1]["entity"] == "sensor.brush_duration"


# ── Device id lookup ────────────────────────────────────────────────


class FakeEntityRegistry:
    def __init__(self, entries: dict[str, Any]) -> None:
        self._entries = entries

    def async_get(self, entity_id: str) -> Any:
        return self._entries.get(entity_id)


def _patch_registry(monkeypatch: pytest.MonkeyPatch, entries: dict[str, Any]) -> None:
    """Stub homeassistant.helpers.entity_registry.async_get, which the
    lookup imports inside the function body."""
    from homeassistant.helpers import entity_registry as er

    monkeypatch.setattr(er, "async_get", lambda hass: FakeEntityRegistry(entries))


def test_device_ids_for_bindings_maps_first_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_registry(
        monkeypatch,
        {
            "sensor.a_duration": SimpleNamespace(device_id="dev_a"),
            "sensor.b_duration": SimpleNamespace(device_id="dev_b"),
        },
    )
    devices = device_ids_for_bindings(
        _hass_with({}), {"brush": ["sensor.a_duration", "sensor.b_duration"]}
    )
    # First binding wins, matching ${role:} so a card mixing both
    # placeholders can't straddle two devices.
    assert devices == {"brush": "dev_a"}


def test_device_ids_for_bindings_omits_role_whose_first_entity_has_no_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No falling through to a later binding: ${role:} would still name the
    first entity, so a card using both would straddle two devices."""
    _patch_registry(
        monkeypatch,
        {
            "input_boolean.helper": SimpleNamespace(device_id=None),
            "sensor.b_duration": SimpleNamespace(device_id="dev_b"),
        },
    )
    devices = device_ids_for_bindings(
        _hass_with({}), {"brush": ["input_boolean.helper", "sensor.b_duration"]}
    )
    assert devices == {}


def test_device_ids_for_bindings_ignores_empty_binding_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_registry(monkeypatch, {})
    assert device_ids_for_bindings(_hass_with({}), {"brush": []}) == {}


def test_device_ids_for_bindings_omits_unregistered_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_registry(monkeypatch, {})
    assert device_ids_for_bindings(_hass_with({}), {"brush": ["sensor.gone"]}) == {}


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


async def test_insert_refuses_an_auto_generated_dashboard() -> None:
    """Seeding here would replace the Overview the user can see with one card."""
    dash = FakeDashboard(not_found=True, auto_gen=True)
    hass = _hass_with({None: dash})

    result = await async_insert_card(
        hass, slug="s", spec=_spec(), bindings={"toggle": ["input_boolean.b"]}, inputs={}
    )

    assert result.ok is False
    assert result.reason == "auto_generated"
    assert dash.saved is None


async def test_insert_refuses_when_the_mode_probe_fails() -> None:
    """Fails closed: this probe is the only thing between a transient storage
    error and a document written over a live Overview."""
    dash = FakeDashboard(not_found=True)
    dash.async_get_info = None  # type: ignore[assignment]
    hass = _hass_with({None: dash})

    result = await async_insert_card(
        hass, slug="s", spec=_spec(), bindings={"toggle": ["input_boolean.b"]}, inputs={}
    )

    assert result.ok is False
    assert dash.saved is None


# ── Missing custom-card resources ───────────────────────────────────


def _custom_spec(**kw: Any) -> DashboardCardSpec:
    base: dict[str, Any] = {
        "card": {"type": "custom:toothbrush-card", "device_id": "${device:brush}"}
    }
    base.update(kw)
    return DashboardCardSpec(**base)


async def test_insert_skips_custom_card_with_no_resource() -> None:
    """A custom card whose JS nobody installed renders as a red error box;
    withhold it and say what to install instead."""
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with(
        {None: dash}, FakeResources(["/hacsfiles/mini-graph-card/mini-graph-card.js"])
    )

    result = await async_insert_card(
        hass, slug="brush", spec=_custom_spec(), bindings={"brush": ["sensor.b"]}, inputs={}
    )

    assert not result.ok
    assert result.reason == "resource_missing"
    assert "toothbrush-card" in result.message
    assert dash.saved is None  # nothing written


async def test_insert_places_custom_card_when_resource_present() -> None:
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with(
        {None: dash},
        FakeResources(["/hacsfiles/toothbrush-card/toothbrush-card.js?hacstag=123"]),
    )

    result = await async_insert_card(
        hass, slug="brush", spec=_custom_spec(), bindings={"brush": ["sensor.b"]}, inputs={}
    )

    assert result.ok and result.reason == "inserted"
    assert dash.saved["views"][0]["cards"][0]["type"] == "custom:toothbrush-card"


async def test_insert_uses_declared_resource_fragment() -> None:
    """Bundles are often named for the repo, not the card they define, so
    the author declares what to look for rather than us guessing."""
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with({None: dash}, FakeResources(["/hacsfiles/lovelace-mushroom/mushroom.js"]))
    spec = DashboardCardSpec(
        card={"type": "custom:mushroom-chips-card"}, requires_resource="lovelace-mushroom"
    )

    result = await async_insert_card(hass, slug="s", spec=spec, bindings={}, inputs={})

    assert result.ok


async def test_insert_skips_when_declared_resource_is_absent() -> None:
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with(
        {None: dash}, FakeResources(["/hacsfiles/mini-graph-card/mini-graph-card.js"])
    )
    spec = DashboardCardSpec(
        card={"type": "custom:mushroom-chips-card"}, requires_resource="lovelace-mushroom"
    )

    result = await async_insert_card(hass, slug="s", spec=spec, bindings={}, inputs={})

    assert not result.ok and result.reason == "resource_missing"


def test_coerce_dashboard_reads_requires_resource() -> None:
    spec = _coerce_dashboard(
        {
            "card": {"type": "custom:toothbrush-card"},
            "requires_resource": "  toothbrush-card  ",
        }
    )
    assert spec is not None
    assert spec.requires_resource == "toothbrush-card"


def test_coerce_dashboard_requires_resource_defaults_empty() -> None:
    spec = _coerce_dashboard({"card": {"type": "button", "entity": "x.y"}})
    assert spec is not None
    assert spec.requires_resource == ""


async def test_insert_checks_nested_custom_cards() -> None:
    """Stacks hide their cards in a list; the missing one still counts."""
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with({None: dash}, FakeResources([]))
    spec = DashboardCardSpec(
        card={
            "type": "vertical-stack",
            "cards": [
                {"type": "entities", "entities": ["sensor.b"]},
                {"type": "custom:toothbrush-card", "device_id": "x"},
            ],
        }
    )

    result = await async_insert_card(hass, slug="s", spec=spec, bindings={}, inputs={})

    assert not result.ok and result.reason == "resource_missing"


async def test_insert_allows_builtin_card_without_reading_resources() -> None:
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    resources = FakeResources([])
    hass = _hass_with({None: dash}, resources)

    result = await async_insert_card(
        hass, slug="baby-sleep", spec=_spec(), bindings={"toggle": ["input_boolean.b"]}, inputs={}
    )

    assert result.ok
    assert resources.info_calls == 0  # built-in cards need no lookup


async def test_insert_places_custom_card_when_resources_unreadable() -> None:
    """An unreadable resource list is "unknown", not "missing": never
    withhold a card over a lookup we could not perform."""
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with({None: dash}, FakeResources([], raises=True))

    result = await async_insert_card(hass, slug="s", spec=_custom_spec(), bindings={}, inputs={})

    assert result.ok


async def test_insert_places_custom_card_when_lovelace_has_no_resources_attr() -> None:
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with({None: dash})  # resources None, as in some setups

    result = await async_insert_card(hass, slug="s", spec=_custom_spec(), bindings={}, inputs={})

    assert result.ok


async def test_insert_checks_nested_elements_from_other_bundles() -> None:
    """One fragment can't speak for two bundles, so with several elements
    each falls back to its own name and the declaration is checked alone."""
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with({None: dash}, FakeResources(["/hacsfiles/lovelace-mushroom/mushroom.js"]))
    spec = DashboardCardSpec(
        card={
            "type": "custom:mushroom-chips-card",
            "cards": [{"type": "custom:mini-graph-card"}],
        },
        requires_resource="lovelace-mushroom",
    )

    result = await async_insert_card(hass, slug="s", spec=spec, bindings={}, inputs={})

    assert not result.ok
    assert "mini-graph-card" in result.message


async def test_insert_honours_declared_resource_for_a_nested_card() -> None:
    """The declaration follows its element into a built-in wrapper: one
    custom element on the card, so the fragment speaks for it."""
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with({None: dash}, FakeResources(["/hacsfiles/lovelace-mushroom/mushroom.js"]))
    spec = DashboardCardSpec(
        card={
            "type": "vertical-stack",
            "cards": [
                {"type": "entities", "entities": ["sensor.b"]},
                {"type": "custom:mushroom-chips-card"},
            ],
        },
        requires_resource="lovelace-mushroom",
    )

    result = await async_insert_card(hass, slug="s", spec=spec, bindings={}, inputs={})

    assert result.ok


async def test_insert_names_the_declared_resource_when_missing() -> None:
    """What to install is the HACS project, not the element name."""
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with({None: dash}, FakeResources([]))
    spec = DashboardCardSpec(
        card={"type": "custom:mushroom-chips-card"}, requires_resource="lovelace-mushroom"
    )

    result = await async_insert_card(hass, slug="s", spec=spec, bindings={}, inputs={})

    assert not result.ok
    assert "lovelace-mushroom" in result.message


async def test_insert_removes_stale_card_when_resource_disappears() -> None:
    """Re-installing after the resource went away must clear the broken
    box a previous install left behind."""
    stale = {"type": "custom:toothbrush-card", CARD_TAG_KEY: "brush"}
    dash = FakeDashboard({"views": [{"title": "Home", "cards": [stale]}]})
    hass = _hass_with({None: dash}, FakeResources([]))

    result = await async_insert_card(
        hass, slug="brush", spec=_custom_spec(), bindings={"brush": ["sensor.b"]}, inputs={}
    )

    assert not result.ok and result.reason == "resource_missing"
    assert dash.saved["views"][0]["cards"] == []


def test_coerce_dashboard_rejects_non_string_requires_resource() -> None:
    with pytest.raises(ManifestError):
        _coerce_dashboard({"card": {"type": "custom:x"}, "requires_resource": ["toothbrush-card"]})


# ── Card resource install ───────────────────────────────────────────


async def test_insert_installs_a_declared_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    """A recipe that says where its card comes from gets it installed,
    rather than the homeowner being told to go and fetch it."""
    from custom_components.selora_ai.recipes import dashboard as module
    from custom_components.selora_ai.recipes.manifest import CardResourceSpec
    from custom_components.selora_ai.recipes.resources import ResourceResult

    calls: list[Any] = []

    async def fake_ensure(hass: Any, spec: Any, owner_slug: str = "") -> ResourceResult:
        calls.append(spec)
        # Register it, as the real one does: the caller re-checks.
        url = "/selora_ai_resources/toothbrush-card-v1.js"
        hass.data[LOVELACE_DATA].resources.add(url)
        return ResourceResult(ok=True, reason="installed", url=url)

    monkeypatch.setattr(module, "async_ensure_resource", fake_ensure)
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    # No resources registered: without the install the card would be withheld.
    hass = _hass_with({None: dash}, FakeResources([]))
    spec = DashboardCardSpec(
        card={"type": "custom:toothbrush-card"},
        requires_resource="toothbrush-card",
        resource=CardResourceSpec(name="toothbrush-card", url="https://github.com/a/b.js"),
    )

    result = await async_insert_card(hass, slug="brush", spec=spec, bindings={}, inputs={})

    assert result.ok and result.reason == "inserted"
    assert result.resource_urls == ("/selora_ai_resources/toothbrush-card-v1.js",)
    assert len(calls) == 1


async def test_insert_reports_a_failed_resource_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.selora_ai.recipes import dashboard as module
    from custom_components.selora_ai.recipes.manifest import CardResourceSpec
    from custom_components.selora_ai.recipes.resources import ResourceResult

    async def fake_ensure(hass: Any, spec: Any, owner_slug: str = "") -> ResourceResult:
        return ResourceResult(
            ok=False, reason="checksum_mismatch", message="does not match the checksum"
        )

    monkeypatch.setattr(module, "async_ensure_resource", fake_ensure)
    stale = {"type": "custom:toothbrush-card", CARD_TAG_KEY: "brush"}
    dash = FakeDashboard({"views": [{"title": "Home", "cards": [stale]}]})
    hass = _hass_with({None: dash}, FakeResources([]))
    spec = DashboardCardSpec(
        card={"type": "custom:toothbrush-card"},
        resource=CardResourceSpec(name="toothbrush-card", url="https://github.com/a/b.js"),
    )

    result = await async_insert_card(hass, slug="brush", spec=spec, bindings={}, inputs={})

    assert not result.ok and result.reason == "resource_install_failed"
    assert "checksum" in result.message
    assert dash.saved["views"][0]["cards"] == []  # stale card cleared too


async def test_insert_rolls_back_a_fresh_resource_when_placement_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing uses what we just downloaded, and what it was replacing is
    still registered — pruning waits for a placed card. Taking the new one
    back out returns the home to where it was, and leaves the record's
    previous claim standing rather than overwriting it with a URL that no
    longer exists."""
    from custom_components.selora_ai.recipes import dashboard as module
    from custom_components.selora_ai.recipes.manifest import CardResourceSpec
    from custom_components.selora_ai.recipes.resources import ResourceResult

    removed: list[str] = []

    async def fake_ensure(hass: Any, spec: Any, owner_slug: str = "") -> ResourceResult:
        url = "/selora_ai_resources/toothbrush-card-v2.js"
        hass.data[LOVELACE_DATA].resources.add(url)
        return ResourceResult(ok=True, reason="installed", url=url)

    async def fake_remove(hass: Any, url: str) -> bool:
        removed.append(url)
        hass.data[LOVELACE_DATA].resources.remove(url)
        return True

    monkeypatch.setattr(module, "async_ensure_resource", fake_ensure)
    monkeypatch.setattr(module, "async_remove_resource", fake_remove)
    # YAML-mode dashboard: writable nowhere, so the card can't be placed.
    dash = FakeDashboard({"views": []}, mode="yaml")
    hass = _hass_with({None: dash}, FakeResources([]))
    spec = DashboardCardSpec(
        card={"type": "custom:toothbrush-card"},
        resource=CardResourceSpec(
            name="toothbrush-card", url="https://github.com/a/b.js", version="v2"
        ),
    )

    result = await async_insert_card(hass, slug="brush", spec=spec, bindings={}, inputs={})

    assert not result.ok
    assert removed == ["/selora_ai_resources/toothbrush-card-v2.js"]
    # The rollback deregistered it, so it is nobody's to clean up: the
    # record keeps whatever it had, which is still installed.
    assert result.resource_urls == ()


async def test_insert_keeps_the_card_when_an_upgrade_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient download failure must not take a working dashboard
    backwards: the previous version is still registered and still renders."""
    from custom_components.selora_ai.recipes import dashboard as module
    from custom_components.selora_ai.recipes.manifest import CardResourceSpec
    from custom_components.selora_ai.recipes.resources import ResourceResult

    async def fake_ensure(hass: Any, spec: Any, owner_slug: str = "") -> ResourceResult:
        return ResourceResult(ok=False, reason="download_failed", message="offline")

    monkeypatch.setattr(module, "async_ensure_resource", fake_ensure)
    card = {"type": "custom:toothbrush-card", CARD_TAG_KEY: "brush"}
    dash = FakeDashboard({"views": [{"title": "Home", "cards": [card]}]})
    # The old version is still registered, so the existing card still works.
    hass = _hass_with(
        {None: dash}, FakeResources(["/selora_ai_resources/toothbrush-card-v0.34.0.js"])
    )
    spec = DashboardCardSpec(
        card={"type": "custom:toothbrush-card"},
        requires_resource="toothbrush-card",
        resource=CardResourceSpec(
            name="toothbrush-card", url="https://github.com/a/b.js", version="v0.35.0"
        ),
    )

    result = await async_insert_card(hass, slug="brush", spec=spec, bindings={}, inputs={})

    assert not result.ok and result.reason == "resource_install_failed"
    assert dash.saved is None  # the working card was left alone


def test_coerce_dashboard_reads_the_resource_block() -> None:
    spec = _coerce_dashboard(
        {
            "card": {"type": "custom:toothbrush-card"},
            "resource": {
                "name": "toothbrush-card",
                "url": "https://github.com/mtheli/toothbrush-card/releases/download/v0.34.0/toothbrush-card.js",
                "version": "v0.34.0",
                "sha256": "a" * 64,
            },
        }
    )
    assert spec is not None and spec.resource is not None
    assert spec.resource.name == "toothbrush-card"
    assert spec.resource.version == "v0.34.0"


def test_coerce_dashboard_rejects_a_non_mapping_resource() -> None:
    with pytest.raises(ManifestError):
        _coerce_dashboard({"card": {"type": "custom:x"}, "resource": "toothbrush-card"})


def test_coerce_dashboard_rejects_an_insecure_resource_url() -> None:
    with pytest.raises(ManifestError):
        _coerce_dashboard(
            {
                "card": {"type": "custom:x"},
                "resource": {"name": "x", "url": "http://github.com/a/b.js"},
            }
        )


async def test_insert_keeps_ownership_of_a_resource_we_already_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reinstalling must not lose the cleanup claim: without the URL on the
    new record, uninstall would leave the file and its registration."""
    from custom_components.selora_ai.recipes import dashboard as module
    from custom_components.selora_ai.recipes.manifest import CardResourceSpec
    from custom_components.selora_ai.recipes.resources import ResourceResult

    ours = "/selora_ai_resources/toothbrush-card-v0.34.0.js"
    seen: list[str] = []

    async def fake_ensure(hass: Any, spec: Any, owner_slug: str = "") -> ResourceResult:
        # What the real one does when the URL is already registered: no
        # download, no second registration, just the claim back.
        seen.append(owner_slug)
        return ResourceResult(ok=True, reason="present", url=ours)

    monkeypatch.setattr(module, "async_ensure_resource", fake_ensure)
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with({None: dash}, FakeResources([ours]))
    spec = DashboardCardSpec(
        card={"type": "custom:toothbrush-card"},
        resource=CardResourceSpec(
            name="toothbrush-card", url="https://github.com/a/b.js", version="v0.34.0"
        ),
    )

    result = await async_insert_card(hass, slug="brush", spec=spec, bindings={}, inputs={})

    assert result.ok and result.resource_urls == (ours,)
    assert seen == ["brush"]  # the recipe identifies itself, for pruning


async def test_insert_leaves_a_card_the_home_already_has(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A HACS copy is not ours to manage: use it, download nothing, and
    claim no ownership over it."""
    from custom_components.selora_ai.recipes import dashboard as module
    from custom_components.selora_ai.recipes.manifest import CardResourceSpec

    async def fake_ensure(hass: Any, spec: Any, owner_slug: str = "") -> Any:
        raise AssertionError("must not download a card the home already has")

    monkeypatch.setattr(module, "async_ensure_resource", fake_ensure)
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with(
        {None: dash}, FakeResources(["/hacsfiles/toothbrush-card/toothbrush-card.js"])
    )
    spec = DashboardCardSpec(
        card={"type": "custom:toothbrush-card"},
        requires_resource="toothbrush-card",
        resource=CardResourceSpec(
            name="toothbrush-card", url="https://github.com/a/b.js", version="v0.34.0"
        ),
    )

    result = await async_insert_card(hass, slug="brush", spec=spec, bindings={}, inputs={})

    assert result.ok and result.reason == "inserted"
    assert result.resource_urls == ()  # nothing of ours to clean up later


async def test_insert_installs_a_newly_pinned_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upgrading the pinned card must not be masked by our own old copy:
    the home would sit on the previous bundle forever."""
    from custom_components.selora_ai.recipes import dashboard as module
    from custom_components.selora_ai.recipes.manifest import CardResourceSpec
    from custom_components.selora_ai.recipes.resources import ResourceResult

    installed: list[str] = []

    async def fake_ensure(hass: Any, spec: Any, owner_slug: str = "") -> ResourceResult:
        installed.append(spec.version)
        url = f"/selora_ai_resources/toothbrush-card-{spec.version}.js"
        hass.data[LOVELACE_DATA].resources.add(url)
        return ResourceResult(ok=True, reason="installed", url=url)

    monkeypatch.setattr(module, "async_ensure_resource", fake_ensure)
    old = "/selora_ai_resources/toothbrush-card-v0.34.0.js"
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with({None: dash}, FakeResources([old]))
    spec = DashboardCardSpec(
        card={"type": "custom:toothbrush-card"},
        requires_resource="toothbrush-card",
        resource=CardResourceSpec(
            name="toothbrush-card", url="https://github.com/a/b.js", version="v0.35.0"
        ),
    )

    result = await async_insert_card(hass, slug="brush", spec=spec, bindings={}, inputs={})

    assert installed == ["v0.35.0"]
    assert result.resource_urls == ("/selora_ai_resources/toothbrush-card-v0.35.0.js",)


async def test_insert_keeps_ownership_when_placement_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The record is the only account of what we installed; losing the URL
    here means uninstall can never take it back out."""
    from custom_components.selora_ai.recipes import dashboard as module
    from custom_components.selora_ai.recipes.manifest import CardResourceSpec
    from custom_components.selora_ai.recipes.resources import ResourceResult

    ours = "/selora_ai_resources/toothbrush-card-v0.34.0.js"

    async def fake_ensure(hass: Any, spec: Any, owner_slug: str = "") -> ResourceResult:
        return ResourceResult(ok=True, reason="present", url=ours)

    monkeypatch.setattr(module, "async_ensure_resource", fake_ensure)
    dash = FakeDashboard({"views": []}, mode="yaml")  # not writable
    hass = _hass_with({None: dash}, FakeResources([ours]))
    spec = DashboardCardSpec(
        card={"type": "custom:toothbrush-card"},
        resource=CardResourceSpec(
            name="toothbrush-card", url="https://github.com/a/b.js", version="v0.34.0"
        ),
    )

    result = await async_insert_card(hass, slug="brush", spec=spec, bindings={}, inputs={})

    assert not result.ok
    assert result.resource_urls == (ours,)


async def test_a_similarly_named_resource_does_not_answer_for_a_card() -> None:
    """slider-button-card is not button-card: a substring test says it is,
    and the homeowner gets the red box the check exists to prevent."""
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with(
        {None: dash}, FakeResources(["/hacsfiles/slider-button-card/slider-button-card.js"])
    )
    spec = DashboardCardSpec(card={"type": "custom:button-card"})

    result = await async_insert_card(hass, slug="s", spec=spec, bindings={}, inputs={})

    assert not result.ok and result.reason == "resource_missing"


async def test_a_versioned_managed_file_answers_for_its_card() -> None:
    """Our own files carry a version and digest suffix; the segment they
    start with is still the element."""
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with(
        {None: dash},
        FakeResources(["/selora_ai_resources/toothbrush-card-v0.34.0-84b5cce8205a.js"]),
    )
    spec = DashboardCardSpec(card={"type": "custom:toothbrush-card"})

    result = await async_insert_card(hass, slug="s", spec=spec, bindings={}, inputs={})

    assert result.ok


async def test_removal_reports_a_dashboard_it_could_not_save() -> None:
    """Uninstall asks this before deleting the module a surviving card
    still needs."""
    from custom_components.selora_ai.recipes.dashboard import cards_fully_removed

    class RefusingDashboard(FakeDashboard):
        async def async_save(self, config: dict[str, Any]) -> None:
            raise RuntimeError("read-only storage")

    card = {"type": "custom:toothbrush-card", CARD_TAG_KEY: "brush"}
    dash = RefusingDashboard({"views": [{"title": "Home", "cards": [card]}]})
    hass = _hass_with({None: dash})

    await async_remove_cards(hass, "brush")

    assert not cards_fully_removed(hass, "brush")


async def test_removal_reports_success_when_every_dashboard_swept() -> None:
    from custom_components.selora_ai.recipes.dashboard import cards_fully_removed

    card = {"type": "custom:toothbrush-card", CARD_TAG_KEY: "brush"}
    dash = FakeDashboard({"views": [{"title": "Home", "cards": [card]}]})
    hass = _hass_with({None: dash})

    await async_remove_cards(hass, "brush")

    assert cards_fully_removed(hass, "brush")
    assert dash.saved["views"][0]["cards"] == []


async def test_insert_installs_when_the_resource_list_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unreadable is not "nothing missing": with a declared bundle in hand,
    installing it beats placing a card whose module may not be there."""
    from custom_components.selora_ai.recipes import dashboard as module
    from custom_components.selora_ai.recipes.manifest import CardResourceSpec
    from custom_components.selora_ai.recipes.resources import ResourceResult

    installs: list[str] = []

    async def fake_ensure(hass: Any, spec: Any, owner_slug: str = "") -> ResourceResult:
        installs.append(spec.name)
        return ResourceResult(ok=True, reason="installed", url="/selora_ai_resources/tb.js")

    monkeypatch.setattr(module, "async_ensure_resource", fake_ensure)
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with({None: dash}, FakeResources([], raises=True))
    spec = DashboardCardSpec(
        card={"type": "custom:toothbrush-card"},
        resource=CardResourceSpec(
            name="toothbrush-card", url="https://github.com/a/b.js", version="v1"
        ),
    )

    result = await async_insert_card(hass, slug="brush", spec=spec, bindings={}, inputs={})

    assert result.ok
    assert installs == ["toothbrush-card"]


async def test_insert_places_a_card_when_the_list_is_unreadable_and_undeclared() -> None:
    """With nothing to install, withholding over a failed lookup would be
    worse than placing a card that probably works."""
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with({None: dash}, FakeResources([], raises=True))
    spec = DashboardCardSpec(card={"type": "custom:toothbrush-card"})

    result = await async_insert_card(hass, slug="brush", spec=spec, bindings={}, inputs={})

    assert result.ok


async def test_insert_drops_our_copy_when_hacs_provides_the_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two modules defining one element is a race. Theirs is the copy the
    homeowner manages, so ours goes."""
    from custom_components.selora_ai.recipes import dashboard as module
    from custom_components.selora_ai.recipes.manifest import CardResourceSpec

    pruned: list[str | None] = []

    async def fake_ensure(hass: Any, spec: Any, owner_slug: str = "") -> Any:
        raise AssertionError("nothing to install: the home already has the card")

    async def fake_prune(
        hass: Any, spec: Any, owner_slug: str = "", keep_url: str | None = None
    ) -> None:
        pruned.append(keep_url)

    monkeypatch.setattr(module, "async_ensure_resource", fake_ensure)
    monkeypatch.setattr(module, "async_prune_superseded", fake_prune)
    ours = "/selora_ai_resources/toothbrush-card-v0.34.0-84b5cce8205a.js"
    hacs = "/hacsfiles/toothbrush-card/toothbrush-card.js"
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with({None: dash}, FakeResources([ours, hacs]))
    spec = DashboardCardSpec(
        card={"type": "custom:toothbrush-card"},
        requires_resource="toothbrush-card",
        resource=CardResourceSpec(
            name="toothbrush-card",
            url="https://github.com/a/b.js",
            version="v0.34.0",
            sha256="84b5cce8205aa28f38048f27925fb54b0f2aab08816862885aa2449cc8e8b951",
        ),
    )

    result = await async_insert_card(hass, slug="brush", spec=spec, bindings={}, inputs={})

    assert result.ok
    assert result.resource_urls == ()  # nothing of ours is claimed any more
    assert pruned == [""]  # keep nothing: every managed copy goes


async def test_removal_reaches_a_card_inside_a_conditional() -> None:
    """A conditional card holds its child under "card", not in a list, and
    uninstall promises to leave nothing behind."""
    tagged = {"type": "custom:toothbrush-card", CARD_TAG_KEY: "brush"}
    wrapper = {"type": "conditional", "conditions": [], "card": tagged}
    other = {"type": "entities", "entities": ["sensor.b"]}
    dash = FakeDashboard({"views": [{"title": "Home", "cards": [wrapper, other]}]})
    hass = _hass_with({None: dash})

    removed = await async_remove_cards(hass, "brush")

    assert removed == 1
    # The wrapper goes with it: a conditional with no card renders as an error.
    assert dash.saved["views"][0]["cards"] == [other]


async def test_reinstall_replaces_a_card_inside_a_conditional() -> None:
    """Re-installing refreshes the card where the homeowner put it."""
    tagged = {"type": "custom:toothbrush-card", "device_id": "old", CARD_TAG_KEY: "brush"}
    wrapper = {"type": "conditional", "conditions": [], "card": tagged}
    dash = FakeDashboard({"views": [{"title": "Home", "cards": [wrapper]}]})
    hass = _hass_with({None: dash})

    result = await async_place_card(
        hass, card={"type": "custom:toothbrush-card", "device_id": "new"}, tag="brush"
    )

    assert result.ok
    placed = dash.saved["views"][0]["cards"][0]
    assert placed["type"] == "conditional"
    assert placed["card"]["device_id"] == "new"


async def test_insert_drops_a_claim_a_built_in_card_no_longer_needs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A revision that drops the custom card leaves its module loading on
    every dashboard visit with nothing ever coming back for it."""
    from custom_components.selora_ai.recipes import dashboard as module

    dropped: list[str] = []
    ours = "/selora_ai_resources/toothbrush-card-v1-abc.js"

    async def fake_drop(hass: Any, url: str, owner_slug: str = "") -> None:
        dropped.append(url)
        hass.data[LOVELACE_DATA].resources.remove(url)

    class Record:
        dashboard_card = {"resource_urls": [ours]}

    from custom_components.selora_ai.recipes import store as store_module

    class FakeStore:
        async def async_get(self, slug: str) -> Any:
            return Record()

    monkeypatch.setattr(module, "async_drop_if_unshared", fake_drop)
    monkeypatch.setattr(store_module, "get_install_store", lambda hass: FakeStore())
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with({None: dash}, FakeResources([ours]))
    # Built-in card: no resource block at all.
    spec = DashboardCardSpec(card={"type": "entities", "entities": ["sensor.b"]})

    result = await async_insert_card(hass, slug="brush", spec=spec, bindings={}, inputs={})

    assert result.ok
    assert dropped == [ours]
    assert result.resource_urls == ()  # gone, so no longer ours


async def test_insert_keeps_claiming_what_it_could_not_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moving onto a HACS copy while pruning fails: the old module is still
    registered, so it stays this recipe's to clean up. Dropping the claim
    would leave it with no owner at all."""
    from custom_components.selora_ai.recipes import dashboard as module
    from custom_components.selora_ai.recipes.manifest import CardResourceSpec

    async def fake_prune(
        hass: Any, spec: Any, owner_slug: str = "", keep_url: str | None = None
    ) -> None:
        return None  # best-effort, and here it achieves nothing

    monkeypatch.setattr(module, "async_prune_superseded", fake_prune)

    ours = "/selora_ai_resources/toothbrush-card-v1-aaa.js"

    class Record:
        # v1, while the recipe now pins v2.
        dashboard_card = {"resource_urls": [ours]}

    from custom_components.selora_ai.recipes import store as store_module

    class FakeStore:
        async def async_get(self, slug: str) -> Any:
            return Record()

    monkeypatch.setattr(store_module, "get_install_store", lambda hass: FakeStore())
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hacs = "/hacsfiles/toothbrush-card/toothbrush-card.js"
    hass = _hass_with({None: dash}, FakeResources([hacs, ours]))
    spec = DashboardCardSpec(
        card={"type": "custom:toothbrush-card"},
        requires_resource="toothbrush-card",
        resource=CardResourceSpec(
            name="toothbrush-card", url="https://github.com/a/b.js", version="v2"
        ),
    )

    result = await async_insert_card(hass, slug="brush", spec=spec, bindings={}, inputs={})

    assert result.ok
    assert result.resource_urls == (ours,)


async def test_insert_does_not_roll_back_a_repaired_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repair re-fetches the file behind a registration that was already
    there. Another recipe may claim it and a card may be running on it, so
    a placement failure here must leave it alone."""
    from custom_components.selora_ai.recipes import dashboard as module
    from custom_components.selora_ai.recipes.manifest import CardResourceSpec
    from custom_components.selora_ai.recipes.resources import ResourceResult

    ours = "/selora_ai_resources/toothbrush-card-v1-aaa.js"
    removed: list[str] = []

    async def fake_ensure(hass: Any, spec: Any, owner_slug: str = "") -> ResourceResult:
        return ResourceResult(ok=True, reason="restored", url=ours)

    async def fake_remove(hass: Any, url: str) -> bool:
        removed.append(url)
        return True

    monkeypatch.setattr(module, "async_ensure_resource", fake_ensure)
    monkeypatch.setattr(module, "async_remove_resource", fake_remove)
    dash = FakeDashboard({"views": []}, mode="yaml")  # not writable
    hass = _hass_with({None: dash}, FakeResources([ours]))
    spec = DashboardCardSpec(
        card={"type": "custom:toothbrush-card"},
        resource=CardResourceSpec(
            name="toothbrush-card", url="https://github.com/a/b.js", version="v1"
        ),
    )

    result = await async_insert_card(hass, slug="brush", spec=spec, bindings={}, inputs={})

    assert not result.ok
    assert removed == []
    assert result.resource_urls == (ours,)  # still ours to clean up later


async def test_insert_keeps_claims_when_an_upgrade_download_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The previous version is still registered and still in use. Reporting
    no claims would have the record replace them with nothing, leaving
    uninstall no handle on it."""
    from custom_components.selora_ai.recipes import dashboard as module
    from custom_components.selora_ai.recipes.manifest import CardResourceSpec
    from custom_components.selora_ai.recipes.resources import ResourceResult

    ours = "/selora_ai_resources/toothbrush-card-v1-aaa.js"

    async def fake_ensure(hass: Any, spec: Any, owner_slug: str = "") -> ResourceResult:
        return ResourceResult(ok=False, reason="download_failed", message="offline")

    class Record:
        dashboard_card = {"resource_urls": [ours]}

    from custom_components.selora_ai.recipes import store as store_module

    class FakeStore:
        async def async_get(self, slug: str) -> Any:
            return Record()

    monkeypatch.setattr(module, "async_ensure_resource", fake_ensure)
    monkeypatch.setattr(store_module, "get_install_store", lambda hass: FakeStore())
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with({None: dash}, FakeResources([ours]))
    spec = DashboardCardSpec(
        card={"type": "custom:toothbrush-card"},
        requires_resource="toothbrush-card",
        resource=CardResourceSpec(
            name="toothbrush-card", url="https://github.com/a/b.js", version="v2"
        ),
    )

    result = await async_insert_card(hass, slug="brush", spec=spec, bindings={}, inputs={})

    assert not result.ok and result.reason == "resource_install_failed"
    assert result.resource_urls == (ours,)


async def test_insert_keeps_a_claim_the_placed_card_still_needs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A revision that drops the resource block but keeps the custom card:
    the module under the card it just placed may be the one we installed,
    so dropping the claim would deregister it and break the card."""
    from custom_components.selora_ai.recipes import dashboard as module

    dropped: list[str] = []
    ours = "/selora_ai_resources/toothbrush-card-v1-abc.js"

    async def fake_drop(hass: Any, url: str, owner_slug: str = "") -> None:
        dropped.append(url)

    class Record:
        dashboard_card = {"resource_urls": [ours]}

    from custom_components.selora_ai.recipes import store as store_module

    class FakeStore:
        async def async_get(self, slug: str) -> Any:
            return Record()

    monkeypatch.setattr(module, "async_drop_if_unshared", fake_drop)
    monkeypatch.setattr(store_module, "get_install_store", lambda hass: FakeStore())
    dash = FakeDashboard({"views": [{"title": "Home", "cards": []}]})
    hass = _hass_with({None: dash}, FakeResources([ours]))
    # Custom card, but no resource block on this revision.
    spec = DashboardCardSpec(card={"type": "custom:toothbrush-card"})

    result = await async_insert_card(hass, slug="brush", spec=spec, bindings={}, inputs={})

    assert result.ok
    assert dropped == []
    assert result.resource_urls == (ours,)


async def test_insert_clears_the_card_when_a_repair_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The registration survives while the file behind it does not, so a
    URL check would call the element provided when the browser is about to
    get a 404 for it."""
    from custom_components.selora_ai.recipes import dashboard as module
    from custom_components.selora_ai.recipes.manifest import CardResourceSpec
    from custom_components.selora_ai.recipes.resources import ResourceResult

    ours = "/selora_ai_resources/toothbrush-card-v1-abc.js"

    async def fake_ensure(hass: Any, spec: Any, owner_slug: str = "") -> ResourceResult:
        return ResourceResult(ok=False, reason="repair_failed", message="cannot reach GitHub")

    monkeypatch.setattr(module, "async_ensure_resource", fake_ensure)
    stale = {"type": "custom:toothbrush-card", CARD_TAG_KEY: "brush"}
    dash = FakeDashboard({"views": [{"title": "Home", "cards": [stale]}]})
    hass = _hass_with({None: dash}, FakeResources([ours]))
    spec = DashboardCardSpec(
        card={"type": "custom:toothbrush-card"},
        requires_resource="toothbrush-card",
        resource=CardResourceSpec(
            name="toothbrush-card", url="https://github.com/a/b.js", version="v1"
        ),
    )

    result = await async_insert_card(hass, slug="brush", spec=spec, bindings={}, inputs={})

    assert not result.ok and result.reason == "resource_install_failed"
    assert dash.saved["views"][0]["cards"] == []  # the unrenderable card goes
