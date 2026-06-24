"""Tests for conversational-language detection over the shipped locales."""

from __future__ import annotations

import pytest

from custom_components.selora_ai.llm_client.lang_detect import (
    detect_language,
    resolve_reply_language,
)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Allumes la lumière de la chambre", "fr"),
        ("Quelles lumières sont allumées ?", "fr"),
        ("schalte das licht im wohnzimmer ein", "de"),
        ("welche lichter sind an", "de"),
        ("enciende la luz de la cocina", "es"),
        ("¿qué luces están encendidas?", "es"),
        ("accendi la luce del salotto", "it"),
        ("quali luci sono accese", "it"),
    ],
)
def test_detects_shipped_locales(message: str, expected: str) -> None:
    assert detect_language(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        "Turn on the bedroom light",  # English — no marker set
        "What lights are on?",
        "燈を点けて",  # Japanese — non-Latin, unsupported
        "",  # empty
        "ok",  # too little signal
    ],
)
def test_returns_none_when_undetected(message: str) -> None:
    assert detect_language(message) is None


def test_resolve_prefers_detected_over_panel() -> None:
    # French message on an English-UI install → French reply.
    assert resolve_reply_language("Allumes la lumière", "en", "en") == "fr"


def test_resolve_falls_back_to_panel_then_config() -> None:
    assert resolve_reply_language("Turn on the light", "es", "en") == "es"
    assert resolve_reply_language("Turn on the light", None, "de") == "de"
    assert resolve_reply_language("", None, None) is None
