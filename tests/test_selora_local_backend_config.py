"""The configured Selora Local runtime has to reach the provider.

Three places construct it: the setup wizard, from the form the user is
filling in; startup, from the saved config entry; and Settings → Test
connection, from a form that has no runtime field of its own. All three
were building the llama-server default regardless, so an Ollama install
was probed on /health — which Ollama does not serve — and every correct
configuration reported "provider unreachable".
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.config_flow import (
    SeloraAiConfigFlow,
    _validate_selora_local,
)
from custom_components.selora_ai.const import (
    CONF_LLM_PROVIDER,
    CONF_SELORA_LOCAL_BACKEND,
    CONF_SELORA_LOCAL_HOST,
    CONF_SELORA_LOCAL_OLLAMA_MODEL,
    DEFAULT_SELORA_LOCAL_BACKEND,
    DOMAIN,
    LLM_PROVIDER_OLLAMA,
    LLM_PROVIDER_SELORA_LOCAL,
    SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED,
    SELORA_LOCAL_BACKENDS,
)
from custom_components.selora_ai.providers import create_provider
from custom_components.selora_ai.websocket.linking import (
    _handle_websocket_update_config,
    _handle_websocket_validate_llm_key,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_validate = _handle_websocket_validate_llm_key.__wrapped__
_update_config = _handle_websocket_update_config.__wrapped__


async def _run_validate(hass: HomeAssistant) -> dict[str, object]:
    conn = MagicMock()
    conn.user.is_admin = True
    with patch(
        "custom_components.selora_ai.providers.create_provider",
        return_value=MagicMock(health_check=AsyncMock(return_value=True)),
    ) as factory:
        await _validate(
            hass,
            conn,
            {"id": 1, "type": "selora_ai/validate_llm_key", "provider": LLM_PROVIDER_SELORA_LOCAL},
        )
    return factory.call_args.kwargs


@pytest.mark.asyncio
async def test_the_connection_test_uses_the_saved_backend(hass: HomeAssistant) -> None:
    """The form has no backend field, so the saved one is the only way it
    can know which route to probe."""
    MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_LLM_PROVIDER: LLM_PROVIDER_SELORA_LOCAL,
            CONF_SELORA_LOCAL_BACKEND: SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED,
        },
    ).add_to_hass(hass)

    kwargs = await _run_validate(hass)
    assert kwargs["selora_local_backend"] == SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED


@pytest.mark.asyncio
async def test_no_saved_backend_leaves_the_default_alone(hass: HomeAssistant) -> None:
    """An install that predates the setting must keep the hub behaviour."""
    MockConfigEntry(domain=DOMAIN, data={CONF_LLM_PROVIDER: LLM_PROVIDER_SELORA_LOCAL}).add_to_hass(
        hass
    )

    assert "selora_local_backend" not in await _run_validate(hass)


@pytest.mark.asyncio
async def test_the_factory_carries_both_settings_through(hass: HomeAssistant) -> None:
    """The entry's backend and model override are passed as keyword
    arguments, so a factory that dropped unknown kwargs would leave the
    provider on the hub defaults with nothing to show for it."""
    provider = create_provider(
        LLM_PROVIDER_SELORA_LOCAL,
        hass,
        host="http://hub.invalid:11434",
        selora_local_backend=SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED,
        selora_local_ollama_model="candidate:tag",
    )
    assert provider._backend == SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED
    assert provider._ollama_model_override == "candidate:tag"


# ── the write path ───────────────────────────────────────────────────


async def _run_update_config(hass: HomeAssistant, config: dict[str, object]) -> None:
    conn = MagicMock()
    conn.user.is_admin = True
    with patch.object(hass.config_entries, "async_reload", AsyncMock(return_value=True)):
        await _update_config(hass, conn, {"id": 1, "config": config})
    conn.send_result.assert_called_once()


@pytest.mark.asyncio
async def test_settings_can_save_the_runtime_choice(hass: HomeAssistant) -> None:
    """Both runtime settings have to survive the save.

    ``update_config`` splits the payload into data and options by an
    allowlist, then drops every option key it does not recognise. A key in
    neither list is silently discarded with a log line — so leaving these
    out means nothing anywhere can write them and the three readers see
    None forever.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_LLM_PROVIDER: LLM_PROVIDER_SELORA_LOCAL},
    )
    entry.add_to_hass(hass)

    await _run_update_config(
        hass,
        {
            CONF_SELORA_LOCAL_BACKEND: SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED,
            CONF_SELORA_LOCAL_OLLAMA_MODEL: "candidate:tag",
        },
    )

    assert entry.data[CONF_SELORA_LOCAL_BACKEND] == SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED
    assert entry.data[CONF_SELORA_LOCAL_OLLAMA_MODEL] == "candidate:tag"
    # Data, not options: options are re-filtered by a second allowlist.
    assert CONF_SELORA_LOCAL_BACKEND not in entry.options


@pytest.mark.asyncio
async def test_a_saved_runtime_reaches_the_provider(hass: HomeAssistant) -> None:
    """End to end: what the settings save writes is what the connection
    test hands the factory. Either half alone leaves the setting inert."""
    MockConfigEntry(
        domain=DOMAIN,
        data={CONF_LLM_PROVIDER: LLM_PROVIDER_SELORA_LOCAL},
    ).add_to_hass(hass)

    await _run_update_config(hass, {CONF_SELORA_LOCAL_BACKEND: SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED})

    kwargs = await _run_validate(hass)
    assert kwargs["selora_local_backend"] == SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED


# ── the setup form ───────────────────────────────────────────────────


def test_the_setup_form_offers_every_runtime() -> None:
    """The form is the only supported way to pick a runtime at install
    time, and it must offer the whole constant — a hand-listed subset
    would leave a shipped runtime unreachable."""
    flow = SeloraAiConfigFlow()
    schema = asyncio.run(flow.async_step_selora_local())["data_schema"].schema
    field = next(k for k in schema if k == CONF_SELORA_LOCAL_BACKEND)

    assert field.default() == DEFAULT_SELORA_LOCAL_BACKEND
    offered = [o["value"] for o in schema[field].config["options"]]
    assert offered == list(SELORA_LOCAL_BACKENDS)


@pytest.mark.asyncio
async def test_the_setup_wizard_probes_the_runtime_it_was_told_about(
    hass: HomeAssistant,
) -> None:
    """The wizard's own form now carries the runtime, so the validation
    probe has no excuse to reach for the llama-server default — that is
    the round trip that rejects a working Ollama host at the last step."""
    with patch(
        "custom_components.selora_ai.providers.create_provider",
        return_value=MagicMock(health_check=AsyncMock(return_value=True)),
    ) as factory:
        await _validate_selora_local(
            hass,
            {
                CONF_SELORA_LOCAL_HOST: "http://hub.invalid:11434",
                CONF_SELORA_LOCAL_BACKEND: SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED,
            },
        )

    assert factory.call_args.kwargs["selora_local_backend"] == (SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED)


@pytest.mark.asyncio
async def test_another_provider_entry_does_not_supply_the_runtime(
    hass: HomeAssistant,
) -> None:
    """A runtime saved on an entry for a different provider says nothing
    about the host being tested. Reading it anyway probes the wrong route
    on an install that carries more than one entry."""
    MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_LLM_PROVIDER: LLM_PROVIDER_OLLAMA,
            CONF_SELORA_LOCAL_BACKEND: SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED,
        },
    ).add_to_hass(hass)
    MockConfigEntry(
        domain=DOMAIN,
        data={CONF_LLM_PROVIDER: LLM_PROVIDER_SELORA_LOCAL},
    ).add_to_hass(hass)

    assert "selora_local_backend" not in await _run_validate(hass)


@pytest.mark.asyncio
async def test_the_form_answer_is_stored_on_the_entry(hass: HomeAssistant) -> None:
    """Last link of the write path: what the user picks has to end up in
    entry.data, which is where setup reads it back from."""
    flow = SeloraAiConfigFlow()
    flow.hass = hass
    with (
        patch(
            "custom_components.selora_ai.config_flow._validate_selora_local",
            AsyncMock(return_value={"title": "Selora AI (Local)"}),
        ),
        patch.object(SeloraAiConfigFlow, "async_step_selora_connect", AsyncMock(return_value={})),
    ):
        await flow.async_step_selora_local(
            {
                CONF_SELORA_LOCAL_HOST: "http://hub.invalid:11434",
                CONF_SELORA_LOCAL_BACKEND: SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED,
            }
        )

    assert flow._llm_data[CONF_SELORA_LOCAL_BACKEND] == SELORA_LOCAL_BACKEND_OLLAMA_UNIFIED
