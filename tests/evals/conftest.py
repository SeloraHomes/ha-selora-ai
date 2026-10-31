"""Live wiring for the chat evals: a real provider, the network, a recorder.

The evals are deselected by default (the ``eval`` marker, see pyproject) and
skip without a key, so neither CI nor a plain ``pytest tests/`` ever calls a
model. Run them with::

    SELORA_EVAL_PROVIDER=anthropic SELORA_EVAL_API_KEY=... \\
        pytest tests/evals -m eval [SELORA_EVAL_RUNS=3]

For Selora Cloud, ``SELORA_EVAL_PROVIDER=selora_cloud`` with an access token
as the key (``SELORA_EVAL_CONNECT_URL`` for another Connect).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
import os
import socket
from typing import Any
from unittest.mock import patch

import aiohttp
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component import plugins
import pytest_socket

from ..chat_harness import ChatHarness
from .home import build_home, fake_frigate


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """``SELORA_EVAL_RUNS`` repeats each case: one pass proves little."""
    if "run" in metafunc.fixturenames:
        runs = int(os.environ.get("SELORA_EVAL_RUNS") or 1)
        metafunc.parametrize("run", range(runs), ids=[f"run{i}" for i in range(runs)])


@pytest.fixture(autouse=True)
def _enable_custom_component(recorder_db_url: str, enable_custom_integrations: None) -> None:
    """Overrides the repo-wide one: the recorder's database must be set up
    before ``hass``."""


# Never a refresh token: refreshing can rotate it, and a hub's own would stop
# working. Without one the provider would call itself unlinked, so it gets a
# stand-in that no refresh accepts — past the access token's expiry the run
# fails, it does not mint a new one.
_NO_REFRESH = "eval-does-not-refresh"


@pytest.fixture
def provider() -> dict[str, str]:
    """``SELORA_EVAL_API_KEY`` is the key, or for Selora Cloud an access token."""
    name = os.environ.get("SELORA_EVAL_PROVIDER", "")
    key = os.environ.get("SELORA_EVAL_API_KEY", "")
    if not name or not key:
        pytest.skip("set SELORA_EVAL_PROVIDER and SELORA_EVAL_API_KEY to run the chat evals")
    if name == "selora_cloud":
        return {
            "provider": name,
            "access_token": key,
            "refresh_token": _NO_REFRESH,
            "connect_url": os.environ.get("SELORA_EVAL_CONNECT_URL", ""),
        }
    return {"provider": name, "api_key": key, "model": os.environ.get("SELORA_EVAL_MODEL", "")}


@pytest.fixture
async def network(
    socket_enabled: None, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[aiohttp.ClientSession]:
    """Undo PHCC's network lockdown for this test, and give the providers a
    session of their own.

    PHCC allows only 127.0.0.1 and refuses DNS; HA's own client session
    resolves on a loop the test does not run, so a threaded resolver it is.
    """
    monkeypatch.setattr(socket, "getaddrinfo", plugins._real_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", pytest_socket._true_connect)
    session = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    )
    with (
        patch(
            "custom_components.selora_ai.providers.base.async_get_clientsession",
            return_value=session,
        ),
        patch(
            "custom_components.selora_ai.providers.selora_cloud.async_get_clientsession",
            return_value=session,
        ),
    ):
        yield session
    await session.close()


@pytest.fixture
async def home(
    recorder_mock: Any,
    hass: HomeAssistant,
    provider: dict[str, str],
    network: aiohttp.ClientSession,
) -> AsyncIterator[ChatHarness]:
    """A harness over the eval home, talking to the configured model."""
    await build_home(hass)
    harness = await ChatHarness.create(hass, **provider)
    with fake_frigate():
        yield harness
