"""The panel's bundled font is served by Home Assistant, not fetched from Google."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.const import (
    CONF_COLLECTOR_ENABLED,
    CONF_DISCOVERY_ENABLED,
    CONF_ENTRY_TYPE,
    CONF_INSIGHTS_ENABLED,
    DOMAIN,
    ENTRY_TYPE_LLM,
)
from custom_components.selora_ai.fonts import FONTS_DIR, FONTS_URL_BASE

_QUIESCE_TIMEOUT = 10.0


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    await async_setup_component(hass, "homeassistant", {})
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="fonts",
        data={
            CONF_ENTRY_TYPE: ENTRY_TYPE_LLM,
            "llm_provider": "ollama",
            "ollama_host": "http://127.0.0.1:1",
            "ollama_model": "test-model",
        },
        options={
            CONF_DISCOVERY_ENABLED: False,
            CONF_COLLECTOR_ENABLED: False,
            CONF_INSIGHTS_ENABLED: False,
            "pattern_detection_enabled": False,
        },
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.selora_ai.llm_client.LLMClient.health_check",
        AsyncMock(return_value=True),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await asyncio.wait_for(hass.async_block_till_done(), timeout=_QUIESCE_TIMEOUT)
    return entry


@pytest.mark.asyncio
async def test_serves_the_css_and_font_file(hass: HomeAssistant, hass_client: Any) -> None:
    entry = await _setup(hass)
    client = await hass_client()

    css = await client.get(f"{FONTS_URL_BASE}/fonts.css?v=1")
    assert css.status == 200
    body = await css.text()
    assert 'font-family: "Inter"' in body
    assert "googleapis" not in body
    assert "max-age" in css.headers.get("Cache-Control", "")

    font = await client.get(f"{FONTS_URL_BASE}/inter-4.1/InterVariable.woff2")
    assert font.status == 200
    assert await font.read() == (FONTS_DIR / "inter-4.1" / "InterVariable.woff2").read_bytes()

    await hass.config_entries.async_unload(entry.entry_id)


def test_every_font_the_css_references_is_bundled() -> None:
    css = (FONTS_DIR / "fonts.css").read_text()
    for chunk in css.split('url("')[1:]:
        assert (FONTS_DIR / chunk.split('"')[0]).is_file()
