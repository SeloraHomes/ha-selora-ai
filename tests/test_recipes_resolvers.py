"""Unit tests for recipe auto-input resolvers.

Focuses on the ``tts_engine`` resolver, which replaces the hard-coded
``tts.cloud_say`` recipes used to ship (a paid HA Cloud-only service) with
a portable ``tts.speak`` engine resolved from the home's actual TTS setup.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from custom_components.selora_ai.recipes.manifest import InputSpec, ManifestError
from custom_components.selora_ai.recipes.resolvers import (
    RESOLVERS,
    ResolverContext,
    ResolverError,
    _resolve_samsung_tv_mac,
    _resolve_samsung_tv_macs,
    _resolve_tts_engine,
    async_apply_auto_inputs,
)
from custom_components.selora_ai.recipes.validator import _coerce_value, validate_inputs

# What a resolver sees when nothing has been picked. The wizard offers
# integration auto-setup from the Match step, so every resolver has to
# cope with an empty context.
_CTX = ResolverContext()


def _hass(tts_entities: list[str]) -> MagicMock:
    hass = MagicMock()
    hass.states.async_entity_ids.side_effect = lambda domain: (
        list(tts_entities) if domain == "tts" else []
    )
    return hass


class TestTtsEngineResolver:
    @pytest.mark.asyncio
    async def test_registered_under_tts_engine(self) -> None:
        # The recipe manifests reference this resolver by name; an unknown
        # name halts the install pipeline.
        assert RESOLVERS.get("tts_engine") is _resolve_tts_engine

    @pytest.mark.asyncio
    async def test_prefers_piper_over_cloud_and_google(self) -> None:
        # HA Cloud (Nabu Casa) is a competitor — never preferred. With a
        # local engine present, Piper wins even when cloud is usable.
        hass = _hass(["tts.piper", "tts.home_assistant_cloud", "tts.google_en"])
        with patch(
            "custom_components.selora_ai.automation_utils._tts_engine_usable",
            return_value=True,
        ):
            assert await _resolve_tts_engine(hass, _CTX) == "tts.piper"

    @pytest.mark.asyncio
    async def test_skips_unusable_cloud(self) -> None:
        # Cloud reports available without a subscription but fails at call
        # time, so an unusable cloud engine is filtered out entirely.
        hass = _hass(["tts.home_assistant_cloud"])
        with patch(
            "custom_components.selora_ai.automation_utils._tts_engine_usable",
            side_effect=lambda h, eid: "home_assistant_cloud" not in eid,
        ):
            assert await _resolve_tts_engine(hass, _CTX) == ""

    @pytest.mark.asyncio
    async def test_cloud_used_only_as_last_resort(self) -> None:
        # Subscribed cloud-only home: nothing local to prefer, so cloud is
        # the fallback rather than leaving the announcement engineless.
        hass = _hass(["tts.home_assistant_cloud"])
        with patch(
            "custom_components.selora_ai.automation_utils._tts_engine_usable",
            return_value=True,
        ):
            assert await _resolve_tts_engine(hass, _CTX) == "tts.home_assistant_cloud"

    @pytest.mark.asyncio
    async def test_cloud_loses_to_other_usable_engine(self) -> None:
        # A usable non-Piper/Google engine (e.g. Microsoft) beats cloud
        # even though sorted order puts "home_assistant_cloud" first.
        hass = _hass(["tts.home_assistant_cloud", "tts.microsoft"])
        with patch(
            "custom_components.selora_ai.automation_utils._tts_engine_usable",
            return_value=True,
        ):
            assert await _resolve_tts_engine(hass, _CTX) == "tts.microsoft"

    @pytest.mark.asyncio
    async def test_prefers_piper_over_google(self) -> None:
        hass = _hass(["tts.google_translate_en_com", "tts.piper"])
        assert await _resolve_tts_engine(hass, _CTX) == "tts.piper"

    @pytest.mark.asyncio
    async def test_falls_back_to_first_available(self) -> None:
        # No preferred engine present → first entity (sorted) wins.
        hass = _hass(["tts.marytts", "tts.amazon_polly"])
        assert await _resolve_tts_engine(hass, _CTX) == "tts.amazon_polly"

    @pytest.mark.asyncio
    async def test_empty_when_no_tts_engine(self) -> None:
        # Graceful: a home with no TTS gets "" (not a ResolverError), so the
        # rest of the recipe still installs and the template omits the
        # announcement block.
        assert await _resolve_tts_engine(_hass([]), _CTX) == ""


class TestSamsungTvMacResolver:
    """The MAC lives in the samsungtv config entry, so the wake-on-LAN
    recipe can drive HA's wake_on_lan flow without ever showing the
    homeowner its form.
    """

    @staticmethod
    def _hass(entries: list[dict[str, Any]]) -> MagicMock:
        """``entries`` are config-entry dicts: ``data`` plus the optional
        ``disabled_by`` / ``source`` HA filters on. ``async_entries`` is
        modelled with its real filtering so a test covering a disabled TV
        fails unless the resolver actually asks to have it excluded.
        """
        built = [
            MagicMock(
                entry_id=e.get("entry_id", f"entry{i}"),
                title=e.get("title", "Samsung TV"),
                data=e.get("data", {}),
                disabled_by=e.get("disabled_by"),
                source=e.get("source", "user"),
            )
            for i, e in enumerate(entries)
        ]

        def _async_entries(
            domain: str | None = None,
            include_ignore: bool = True,
            include_disabled: bool = True,
        ) -> list[MagicMock]:
            if domain not in (None, "samsungtv"):
                return []
            return [
                entry
                for entry in built
                if (include_ignore or entry.source != "ignore")
                and (include_disabled or not entry.disabled_by)
            ]

        hass = MagicMock()
        hass.config_entries.async_entries.side_effect = _async_entries
        return hass

    @staticmethod
    def _registry(owners: dict[str, str | None]) -> Any:
        """Patch the entity registry so each entity id in ``owners``
        reports the config entry it belongs to. An entity id absent from
        the map is unknown to the registry, the way a stale selection or
        a template entity would be.
        """
        registry = MagicMock()
        registry.async_get.side_effect = lambda entity_id: (
            MagicMock(config_entry_id=owners[entity_id]) if entity_id in owners else None
        )
        return patch(
            "custom_components.selora_ai.recipes.resolvers.er.async_get",
            return_value=registry,
        )

    @pytest.mark.asyncio
    async def test_registered_under_samsung_tv_mac(self) -> None:
        # The recipe manifest references this resolver by name from
        # ``integration.auto_setup.resolved``; an unknown name halts the
        # install pipeline.
        assert RESOLVERS.get("samsung_tv_mac") is _resolve_samsung_tv_mac

    @pytest.mark.asyncio
    async def test_single_entry_returns_its_mac(self) -> None:
        # Returned as stored: HA's own wake_on_lan flow normalizes it
        # through dr.format_mac().
        hass = self._hass([{"data": {"mac": "AA:BB:CC:DD:EE:FF", "host": "10.0.0.5"}}])
        assert await _resolve_samsung_tv_mac(hass, _CTX) == "AA:BB:CC:DD:EE:FF"

    @pytest.mark.asyncio
    async def test_no_entries_raises(self) -> None:
        with pytest.raises(ResolverError) as exc:
            await _resolve_samsung_tv_mac(self._hass([]), _CTX)
        # Points the homeowner at the manual path rather than failing mute.
        assert "Wake on LAN" in str(exc.value)

    @pytest.mark.asyncio
    async def test_entry_without_mac_raises(self) -> None:
        # Legacy Samsung models don't publish a MAC, so the key is simply
        # absent from entry.data — not an empty string to be returned.
        with pytest.raises(ResolverError) as exc:
            await _resolve_samsung_tv_mac(self._hass([{"data": {"host": "10.0.0.5"}}]), _CTX)
        assert "MAC address" in str(exc.value)

    @pytest.mark.asyncio
    async def test_two_tvs_and_no_pick_yet_asks_for_the_pick(self) -> None:
        # Auto-setup is reachable before the television is chosen. With two
        # TVs there is no answer yet, and the message has to send the
        # homeowner to the pick rather than to a manual config flow.
        hass = self._hass(
            [
                {"data": {"mac": "AA:BB:CC:DD:EE:FF"}},
                {"data": {"mac": "11:22:33:44:55:66"}},
            ],
        )
        with pytest.raises(ResolverError) as exc:
            await _resolve_samsung_tv_mac(hass, _CTX)
        assert "2 Samsung TVs" in str(exc.value)
        assert "Pick the one" in str(exc.value)

    @pytest.mark.asyncio
    async def test_picked_tv_resolves_in_a_two_tv_home(self) -> None:
        # The whole point of the context: two televisions is not an
        # ambiguity once the homeowner has said which one the recipe is
        # for. The pick leads entity → config entry → MAC.
        hass = self._hass(
            [
                {"entry_id": "living", "data": {"mac": "AA:BB:CC:DD:EE:FF"}},
                {"entry_id": "bedroom", "data": {"mac": "11:22:33:44:55:66"}},
            ],
        )
        ctx = ResolverContext(bindings={"tv": ("media_player.bedroom_tv",)})
        with self._registry({"media_player.bedroom_tv": "bedroom"}):
            assert await _resolve_samsung_tv_mac(hass, ctx) == "11:22:33:44:55:66"

    @pytest.mark.asyncio
    async def test_other_roles_entities_do_not_count_as_the_tv(self) -> None:
        # ``entity_ids`` spans every role, so a recipe that also binds
        # speakers or lights hands those to the resolver too. They belong
        # to other integrations and must fall out, leaving the real pick.
        hass = self._hass(
            [
                {"entry_id": "living", "data": {"mac": "AA:BB:CC:DD:EE:FF"}},
                {"entry_id": "bedroom", "data": {"mac": "11:22:33:44:55:66"}},
            ],
        )
        ctx = ResolverContext(
            bindings={
                "speakers": ("media_player.kitchen_sonos",),
                "tv": ("media_player.living_room_tv",),
            }
        )
        owners = {
            "media_player.kitchen_sonos": "sonos_entry",
            "media_player.living_room_tv": "living",
        }
        with self._registry(owners):
            assert await _resolve_samsung_tv_mac(hass, ctx) == "AA:BB:CC:DD:EE:FF"

    @pytest.mark.asyncio
    async def test_picked_tv_without_a_mac_names_it(self) -> None:
        # A legacy set that publishes no MAC can't be woken. The homeowner
        # picked it by name, so the refusal says which television it is.
        hass = self._hass(
            [{"entry_id": "den", "title": "The Frame", "data": {"host": "10.0.0.5"}}],
        )
        ctx = ResolverContext(bindings={"tv": ("media_player.den_tv",)})
        with self._registry({"media_player.den_tv": "den"}):
            with pytest.raises(ResolverError) as exc:
                await _resolve_samsung_tv_mac(hass, ctx)
        assert "The Frame" in str(exc.value)

    @pytest.mark.asyncio
    async def test_unknown_entity_falls_back_to_the_single_tv(self) -> None:
        # A selection carried over from a home that has since changed
        # points at an entity the registry no longer knows. One TV left
        # means there's still only one answer.
        hass = self._hass([{"entry_id": "living", "data": {"mac": "AA:BB:CC:DD:EE:FF"}}])
        ctx = ResolverContext(bindings={"tv": ("media_player.gone",)})
        with self._registry({}):
            assert await _resolve_samsung_tv_mac(hass, ctx) == "AA:BB:CC:DD:EE:FF"

    @pytest.mark.asyncio
    async def test_disabled_entry_is_not_a_second_tv(self) -> None:
        # Replacing a TV and disabling the old entry rather than deleting it
        # leaves its MAC on record. A disabled entry exposes no entities, so
        # it can't be the TV picked in the role step - counting it would
        # refuse a home that has exactly one usable television.
        hass = self._hass(
            [
                {"data": {"mac": "11:22:33:44:55:66"}, "disabled_by": "user"},
                {"data": {"mac": "AA:BB:CC:DD:EE:FF"}},
            ],
        )
        assert await _resolve_samsung_tv_mac(hass, _CTX) == "AA:BB:CC:DD:EE:FF"

    @pytest.mark.asyncio
    async def test_only_a_disabled_tv_raises(self) -> None:
        # Resolving the disabled entry's MAC would wire Wake on LAN to a
        # television the home no longer uses, reporting success.
        hass = self._hass(
            [{"data": {"mac": "11:22:33:44:55:66"}, "disabled_by": "user"}],
        )
        with pytest.raises(ResolverError):
            await _resolve_samsung_tv_mac(hass, _CTX)

    @pytest.mark.asyncio
    async def test_ignored_discovery_entry_is_skipped(self) -> None:
        # An ignored entry stores data={}, so it carries no MAC either way.
        hass = self._hass(
            [
                {"data": {}, "source": "ignore"},
                {"data": {"mac": "AA:BB:CC:DD:EE:FF"}},
            ],
        )
        assert await _resolve_samsung_tv_mac(hass, _CTX) == "AA:BB:CC:DD:EE:FF"

    @pytest.mark.asyncio
    async def test_mac_less_entry_ignored_beside_a_good_one(self) -> None:
        # One legacy TV alongside one that reports a MAC is not ambiguous:
        # only one of them can be woken.
        hass = self._hass([{"data": {"host": "10.0.0.5"}}, {"data": {"mac": "AA:BB:CC:DD:EE:FF"}}])
        assert await _resolve_samsung_tv_mac(hass, _CTX) == "AA:BB:CC:DD:EE:FF"

    @pytest.mark.asyncio
    async def test_several_picked_seeds_the_flow_with_the_first(self) -> None:
        # wake_on_lan registers send_magic_packet in the component's
        # async_setup, so one entry makes the service exist for every
        # television. The scalar resolver only has to give that flow its
        # required field; refusing a three-TV house would be refusing to
        # set up a service the house can use.
        hass = self._hass(
            [
                {"entry_id": "living", "data": {"mac": "AA:BB:CC:DD:EE:FF"}},
                {"entry_id": "bedroom", "data": {"mac": "11:22:33:44:55:66"}},
            ],
        )
        ctx = ResolverContext(
            bindings={"tv": ("media_player.living_room_tv", "media_player.bedroom_tv")}
        )
        owners = {
            "media_player.living_room_tv": "living",
            "media_player.bedroom_tv": "bedroom",
        }
        with self._registry(owners):
            assert await _resolve_samsung_tv_mac(hass, ctx) == "AA:BB:CC:DD:EE:FF"


class TestSamsungTvMacsResolver:
    """The per-television mapping: what lets ONE install cover a house
    with several Samsung TVs. The package is written per recipe slug, so
    a second install of the same recipe would overwrite the first.
    """

    # Same home and same registry as the scalar resolver's tests — the
    # two answer different questions about one set of config entries.
    _hass = staticmethod(TestSamsungTvMacResolver._hass)
    _registry = staticmethod(TestSamsungTvMacResolver._registry)

    @pytest.mark.asyncio
    async def test_registered_under_samsung_tv_macs(self) -> None:
        assert RESOLVERS.get("samsung_tv_macs") is _resolve_samsung_tv_macs

    @pytest.mark.asyncio
    async def test_one_mac_per_picked_tv_keyed_by_entity(self) -> None:
        hass = self._hass(
            [
                {"entry_id": "living", "data": {"mac": "AA:BB:CC:DD:EE:FF"}},
                {"entry_id": "bedroom", "data": {"mac": "11:22:33:44:55:66"}},
                {"entry_id": "den", "data": {"mac": "99:88:77:66:55:44"}},
            ],
        )
        ctx = ResolverContext(
            bindings={"tv": ("media_player.living_room_tv", "media_player.den_tv")}
        )
        owners = {
            "media_player.living_room_tv": "living",
            "media_player.den_tv": "den",
            "media_player.bedroom_tv": "bedroom",
        }
        with self._registry(owners):
            macs = await _resolve_samsung_tv_macs(hass, ctx)
        # The TV they didn't pick stays out of it.
        assert macs == {
            "media_player.living_room_tv": "AA:BB:CC:DD:EE:FF",
            "media_player.den_tv": "99:88:77:66:55:44",
        }

    @pytest.mark.asyncio
    async def test_a_picked_tv_without_a_mac_stops_the_install(self) -> None:
        # Skipping it would leave a television the homeowner chose with
        # no wake automation and nothing said about why.
        hass = self._hass(
            [
                {"entry_id": "living", "data": {"mac": "AA:BB:CC:DD:EE:FF"}},
                {"entry_id": "old", "title": "Den Plasma", "data": {"host": "10.0.0.9"}},
            ],
        )
        ctx = ResolverContext(
            bindings={"tv": ("media_player.living_room_tv", "media_player.den_tv")}
        )
        owners = {
            "media_player.living_room_tv": "living",
            "media_player.den_tv": "old",
        }
        with self._registry(owners), pytest.raises(ResolverError) as exc:
            await _resolve_samsung_tv_macs(hass, ctx)
        assert "Den Plasma doesn't report" in str(exc.value)
        assert "Leave it out" in str(exc.value)

    @pytest.mark.asyncio
    async def test_several_mac_less_tvs_are_all_named_in_one_sentence(self) -> None:
        # The message reaches the homeowner verbatim, so it has to read as
        # English with more than one set in it.
        hass = self._hass(
            [
                {"entry_id": "den", "title": "Den Plasma", "data": {}},
                {"entry_id": "attic", "title": "Attic LCD", "data": {}},
            ],
        )
        ctx = ResolverContext(bindings={"tv": ("media_player.den_tv", "media_player.attic_tv")})
        owners = {"media_player.den_tv": "den", "media_player.attic_tv": "attic"}
        with self._registry(owners), pytest.raises(ResolverError) as exc:
            await _resolve_samsung_tv_macs(hass, ctx)
        assert "Den Plasma, Attic LCD don't report" in str(exc.value)
        assert "Leave them out" in str(exc.value)

    @pytest.mark.asyncio
    async def test_nothing_picked_is_an_empty_mapping(self) -> None:
        # Not an error: a required role with nothing bound fails at the
        # resolve stage, long before the renderer asks for this.
        hass = self._hass([{"data": {"mac": "AA:BB:CC:DD:EE:FF"}}])
        assert await _resolve_samsung_tv_macs(hass, _CTX) == {}


class TestAutoInputContext:
    """``async_apply_auto_inputs`` is the install-time path (the wizard's
    auto-setup is the other one). Both have to hand the resolver the role
    bindings, or a resolver that answers "which device did they pick?"
    is back to guessing at render time.
    """

    @pytest.mark.asyncio
    async def test_bindings_reach_the_resolver(self) -> None:
        seen: list[ResolverContext] = []

        async def _spy(hass: Any, ctx: ResolverContext) -> str:
            seen.append(ctx)
            return "resolved"

        manifest = MagicMock()
        manifest.inputs = [InputSpec(id="mac", type="string", label="MAC", resolver="spy")]
        inputs: dict[str, Any] = {"mac": "stale"}
        with patch.dict(RESOLVERS, {"spy": _spy}):
            await async_apply_auto_inputs(
                MagicMock(),
                manifest,
                inputs,
                bindings={"tv": ("media_player.living_room_tv",)},
            )

        assert inputs["mac"] == "resolved"
        assert seen[0].bindings == {"tv": ("media_player.living_room_tv",)}
        assert seen[0].entity_ids == ("media_player.living_room_tv",)

    @pytest.mark.asyncio
    async def test_entity_ids_span_roles_and_dedupe(self) -> None:
        # One entity can fill two roles (the TV that is also the
        # announcement speaker); a resolver should see it once.
        ctx = ResolverContext(
            bindings={
                "tv": ("media_player.tv",),
                "speakers": ("media_player.tv", "media_player.kitchen"),
                "unused": (),
            }
        )
        assert ctx.entity_ids == ("media_player.tv", "media_player.kitchen")


class TestResolverEmptyValueValidation:
    """A resolver-driven input that resolves to "" must be trusted by
    validate_inputs, not turned into a required-field error or replaced by
    the default — otherwise the documented no-TTS fallback halts the install.
    """

    def test_resolver_empty_is_trusted_over_required(self) -> None:
        # required defaults to True; an empty resolver result must NOT become
        # a "required" error.
        spec = InputSpec(id="tts_engine", type="string", label="TTS", resolver="tts_engine")
        assert _coerce_value(spec, "") == ("", None)

    def test_resolver_empty_not_replaced_by_default(self) -> None:
        # The non-empty default exists only for the offline render gate; an
        # empty resolver result at install must stay "", not become the
        # default (which would target a non-existent engine).
        spec = InputSpec(
            id="tts_engine",
            type="string",
            label="TTS",
            resolver="tts_engine",
            default="tts.home_assistant_cloud",
        )
        assert _coerce_value(spec, "") == ("", None)

    def test_resolver_nonempty_value_passes_through(self) -> None:
        spec = InputSpec(id="tts_engine", type="string", label="TTS", resolver="tts_engine")
        assert _coerce_value(spec, "tts.piper") == ("tts.piper", None)

    def test_non_resolver_required_blank_still_errors(self) -> None:
        # Unchanged behavior for ordinary user inputs: required + blank + no
        # default is still a "required" error.
        spec = InputSpec(id="shelter", type="string", label="Shelter")
        assert _coerce_value(spec, "") == (None, "required")

    def test_non_resolver_blank_still_uses_default(self) -> None:
        spec = InputSpec(id="shelter", type="string", label="Shelter", default="the basement")
        assert _coerce_value(spec, "") == ("the basement", None)


class TestMappingInput:
    """``mapping`` is the input type that carries one value per selected
    device — the MAC of each television picked, keyed by entity id. It is
    what lets a recipe cover a house rather than a device.
    """

    def _spec(self, **kwargs: Any) -> InputSpec:
        base = {"id": "macs", "type": "mapping", "label": "MACs", "resolver": "samsung_tv_macs"}
        return InputSpec(**{**base, **kwargs})

    def test_resolver_output_passes_through(self) -> None:
        value, err = _coerce_value(self._spec(), {"media_player.tv": "AA:BB:CC:DD:EE:FF"})
        assert err is None
        assert value == {"media_player.tv": "AA:BB:CC:DD:EE:FF"}

    def test_values_are_stringified(self) -> None:
        # A mapping renders into a template the way a string input does,
        # so nothing downstream has to care what the resolver returned.
        value, err = _coerce_value(self._spec(), {"media_player.tv": 42})
        assert err is None
        assert value == {"media_player.tv": "42"}

    def test_empty_mapping_is_valid(self) -> None:
        # Empty is a legitimate resolver answer, not a missing required
        # field: the role that feeds it is what enforces "pick at least
        # one", and it fails long before this.
        assert _coerce_value(self._spec(), {}) == ({}, None)

    def test_non_mapping_is_an_error(self) -> None:
        value, err = _coerce_value(self._spec(), "AA:BB:CC:DD:EE:FF")
        assert value is None
        assert err is not None and "not a mapping" in err

    def test_requires_a_resolver(self) -> None:
        # There is no form control for a dict keyed by entity id, so a
        # mapping the wizard would have to ask for is an authoring error.
        with pytest.raises(ManifestError) as exc:
            self._spec(resolver=None).validate()
        assert "requires a resolver" in str(exc.value)

    def test_default_must_be_a_mapping(self) -> None:
        with pytest.raises(ManifestError):
            self._spec(default="AA:BB:CC:DD:EE:FF").validate()

    def test_valid_spec_passes(self) -> None:
        self._spec(default={}).validate()

    def test_reaches_the_renderer_through_validate_inputs(self) -> None:
        # The template indexes it by entity id (``inputs.macs[tv]``), so
        # the mapping has to survive input validation intact rather than
        # being flattened or dropped on the way to the render context.
        manifest = MagicMock()
        manifest.inputs = [self._spec()]
        report = validate_inputs(
            manifest,
            {"macs": {"media_player.tv": "AA:BB:CC:DD:EE:FF"}},
        )
        assert report.ok
        assert report.values == {"macs": {"media_player.tv": "AA:BB:CC:DD:EE:FF"}}
