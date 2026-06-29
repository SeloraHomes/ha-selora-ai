"""Unit tests for recipe auto-input resolvers.

Focuses on the ``tts_engine`` resolver, which replaces the hard-coded
``tts.cloud_say`` recipes used to ship (a paid HA Cloud-only service) with
a portable ``tts.speak`` engine resolved from the home's actual TTS setup.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.selora_ai.recipes.manifest import InputSpec
from custom_components.selora_ai.recipes.resolvers import (
    RESOLVERS,
    _resolve_tts_engine,
)
from custom_components.selora_ai.recipes.validator import _coerce_value


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
    async def test_prefers_cloud(self) -> None:
        hass = _hass(["tts.piper", "tts.home_assistant_cloud", "tts.google_en"])
        assert await _resolve_tts_engine(hass) == "tts.home_assistant_cloud"

    @pytest.mark.asyncio
    async def test_prefers_piper_over_google(self) -> None:
        hass = _hass(["tts.google_translate_en_com", "tts.piper"])
        assert await _resolve_tts_engine(hass) == "tts.piper"

    @pytest.mark.asyncio
    async def test_falls_back_to_first_available(self) -> None:
        # No preferred engine present → first entity (sorted) wins.
        hass = _hass(["tts.marytts", "tts.amazon_polly"])
        assert await _resolve_tts_engine(hass) == "tts.amazon_polly"

    @pytest.mark.asyncio
    async def test_empty_when_no_tts_engine(self) -> None:
        # Graceful: a home with no TTS gets "" (not a ResolverError), so the
        # rest of the recipe still installs and the template omits the
        # announcement block.
        assert await _resolve_tts_engine(_hass([])) == ""


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
        spec = InputSpec(
            id="shelter", type="string", label="Shelter", default="the basement"
        )
        assert _coerce_value(spec, "") == ("the basement", None)
