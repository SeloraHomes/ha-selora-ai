"""register_mcp_server must be idempotent per HomeAssistant instance.

aiohttp's UrlDispatcher has no unregister, and register_mcp_server runs from
async_setup_entry — which also fires on every config-entry reload. Without a
guard, each reload appended another resource plus routes to the router, every
one retaining a closure over the superseded view instance, while requests kept
being served by the first registration. Unbounded growth for the lifetime of
the HA process.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from aiohttp import web
import pytest

from custom_components.selora_ai import mcp_server


@pytest.fixture
def fake_hass() -> Any:
    """A hass stand-in carrying a real aiohttp app so routes really accumulate."""
    hass = MagicMock()
    hass.data = {}
    app = web.Application()
    hass.http = MagicMock()
    hass.http.app = app
    registered: list[Any] = []
    hass.http.register_view.side_effect = registered.append
    hass.registered_views = registered
    return hass


def _route_count(hass: Any) -> int:
    return len(list(hass.http.app.router.routes()))


def test_repeat_registration_adds_no_further_routes(fake_hass: Any) -> None:
    mcp_server.register_mcp_server(fake_hass)
    after_first = _route_count(fake_hass)
    views_after_first = len(fake_hass.registered_views)
    assert after_first > 0, "expected the protected-resource routes to be added"

    # Simulate ten config-entry reloads.
    for _ in range(10):
        mcp_server.register_mcp_server(fake_hass)

    assert _route_count(fake_hass) == after_first
    assert len(fake_hass.registered_views) == views_after_first


def test_distinct_instances_each_register(fake_hass: Any) -> None:
    """The guard is per-instance, not process-global — a second HA still gets views."""
    mcp_server.register_mcp_server(fake_hass)
    first_routes = _route_count(fake_hass)

    other = MagicMock()
    other.data = {}
    other.http = MagicMock()
    other.http.app = web.Application()
    other_views: list[Any] = []
    other.http.register_view.side_effect = other_views.append

    mcp_server.register_mcp_server(other)
    assert _route_count(other) == first_routes
    assert other_views, "a distinct HomeAssistant must still get its views"


def test_a_failed_view_is_retried_without_re_adding_the_successful_one(
    fake_hass: Any,
) -> None:
    """A partial failure must stay retryable.

    Marking the whole instance registered after a view raised left that endpoint
    dead until HA restarted; retrying everything would re-add router resources for
    the views that already succeeded. Only the failed step repeats.
    """
    attempts: list[str] = []
    fail_for = {"selora_ai:oauth_token_proxy"}

    def _register(view: Any) -> None:
        attempts.append(view.name)
        if view.name in fail_for:
            raise RuntimeError("http not up yet")

    fake_hass.http.register_view.side_effect = _register

    mcp_server.register_mcp_server(fake_hass)
    first = list(attempts)
    assert "selora_ai:oauth_token_proxy" in first
    assert len(first) == 2  # both attempted despite one raising

    # The transient condition clears; the next config-entry reload retries.
    fail_for.clear()
    attempts.clear()
    mcp_server.register_mcp_server(fake_hass)

    assert attempts == ["selora_ai:oauth_token_proxy"], (
        "only the failed view should be re-attempted"
    )

    # Now that everything succeeded, further reloads are complete no-ops.
    attempts.clear()
    mcp_server.register_mcp_server(fake_hass)
    assert attempts == []


def test_a_failed_raw_route_is_retried(fake_hass: Any) -> None:
    """The same holds for the raw /.well-known route pair."""
    added: list[str] = []
    fail = {"OPTIONS"}
    real_app = fake_hass.http.app

    class _Router:
        def add_route(self, method: str, path: str, handler: Any) -> Any:
            added.append(method)
            if method in fail:
                raise RuntimeError("router busy")
            return real_app.router.add_route(method, path, handler)

    fake_hass.http.app = type("App", (), {"router": _Router()})()

    mcp_server.register_mcp_server(fake_hass)
    assert added == ["GET", "OPTIONS"]

    fail.clear()
    added.clear()
    mcp_server.register_mcp_server(fake_hass)
    assert added == ["OPTIONS"], "only the failed method should be re-attempted"


# ── Against HA's real HTTP stack ────────────────────────────────────────────
#
# The fixture above mocks `register_view` with a list append, so it never runs
# HomeAssistantView.register — and that is where the real failure was. HA adds
# the routes and then hands them to aiohttp_cors, which insists on owning
# OPTIONS; these views answer preflight themselves, so the decoration raises
# AFTER the routes are in the router. Registration was reported as failed on
# every single start, of endpoints that were live and serving.


async def test_registration_succeeds_against_the_real_http_stack(hass: Any) -> None:
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "http", {})
    await hass.async_block_till_done()

    mcp_server.register_mcp_server(hass)

    routed = {getattr(resource, "canonical", None) for resource in hass.http.app.router.resources()}
    assert "/api/selora_ai/mcp" in routed
    assert "/api/selora_ai/oauth/token" in routed


async def test_every_step_is_marked_done_so_reloads_do_not_retry(hass: Any) -> None:
    """The step stayed unmarked while the endpoint was live, so every reload
    re-ran it for the life of the process — the exact growth this module's
    guard exists to prevent."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "http", {})
    await hass.async_block_till_done()

    mcp_server.register_mcp_server(hass)
    done = mcp_server._REGISTERED_STEPS[hass]
    assert "selora_ai:mcp" in done
    assert "selora_ai:oauth_token_proxy" in done

    before = len(list(hass.http.app.router.routes()))
    mcp_server.register_mcp_server(hass)
    assert len(list(hass.http.app.router.routes())) == before


async def test_a_view_that_never_routed_is_still_reported(hass: Any) -> None:
    """The quieting is conditional on the router actually holding the route —
    a genuine failure must still warn, and now says why."""
    assert mcp_server._route_exists(web.Application(), "/api/selora_ai/mcp") is False
