"""Tests for SeloraCloudProvider's retry/log policy and the
hub-provisioned credential view that feeds it.

Two regressions fixed here, pinned by these tests:

- ``_aigateway_view`` now reads ``expires_at`` from the nested
  ``selora_ai_gateway`` block, not just the flat key. Otherwise a
  provisioned access token paired with an unknown-expiry value (0.0)
  would bypass ``_needs_refresh()`` and keep being used past expiry.
- ``SeloraCloudProvider.send_request`` no longer silently drops
  non-transient failures on the first attempt. Cold-start retries are
  still silent on intermediate attempts, but a 4xx / non-transient 5xx
  is logged at ERROR on the spot, and an exhausted retry budget logs
  at WARNING (so HA's error notification doesn't fire on a self-
  healing condition).
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from custom_components.selora_ai import _aigateway_view
from custom_components.selora_ai.providers.selora_cloud import SeloraCloudProvider


class TestAigatewayView:
    """Hub-provisioned credentials must be readable end-to-end."""

    def test_expires_at_picked_up_from_nested_blob(self) -> None:
        """Without this, refresh never fires for hub-provisioned creds."""
        view = _aigateway_view(
            {
                "selora_ai_gateway": {
                    "access_token": "ey.access",
                    "refresh_token": "aigw_refresh",
                    "expires_at": 1234567890.0,
                    "client_id": "selora-hub-config-sync",
                    "token_url": "https://example.test/oauth/aigw/token",
                },
                "selora_connect_url": "https://example.test",
            }
        )
        assert view["expires_at"] == 1234567890.0
        assert view["access_token"] == "ey.access"
        assert view["refresh_token"] == "aigw_refresh"

    def test_flat_expires_at_wins_over_nested(self) -> None:
        """Flat keys are the source of truth after a successful refresh."""
        view = _aigateway_view(
            {
                "aigateway_expires_at": 9_999_999_999.0,
                "selora_ai_gateway": {"expires_at": 1.0},
            }
        )
        assert view["expires_at"] == 9_999_999_999.0

    def test_missing_expiry_defaults_to_zero(self) -> None:
        view = _aigateway_view(
            {"selora_ai_gateway": {"refresh_token": "aigw_x"}, "selora_connect_url": "https://a"}
        )
        assert view["expires_at"] == 0.0

    def test_legacy_ai_gateway_alias_still_read(self) -> None:
        """Older provisioner output used ``ai_gateway``; keep reading it."""
        view = _aigateway_view(
            {"ai_gateway": {"refresh_token": "aigw_x", "expires_at": 42.0}}
        )
        assert view["refresh_token"] == "aigw_x"
        assert view["expires_at"] == 42.0


class TestSendRequestRetryAndLogging:
    """Retry loop must surface real failures and stay quiet on cold-starts."""

    @pytest.fixture
    def provider(self, hass) -> SeloraCloudProvider:
        p = SeloraCloudProvider(
            hass,
            access_token="ey.access",
            refresh_token="aigw_refresh",
            expires_at=9_999_999_999.0,
            connect_url="https://example.test",
            client_id="cid",
            entry_id="entry-id",
        )
        # Suppress the actual HTTP refresh path; tokens are already "fresh".
        p._needs_refresh = lambda: False  # type: ignore[method-assign]
        return p

    async def test_non_transient_failure_logs_error_immediately(
        self, provider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A 401 (or any non-transient error) must log loudly on the first attempt."""
        provider_send = AsyncMock(return_value=(None, "HTTP 401: Unauthorized"))
        provider_super_send = provider_send

        from custom_components.selora_ai.providers import openai_compat

        # Patch the parent class's send_request so super().send_request() inside
        # SeloraCloudProvider.send_request goes through our mock.
        original = openai_compat.OpenAICompatibleProvider.send_request
        openai_compat.OpenAICompatibleProvider.send_request = provider_super_send  # type: ignore[assignment]
        try:
            with caplog.at_level(
                logging.ERROR, logger="custom_components.selora_ai.providers.selora_cloud"
            ):
                result, err = await provider.send_request("sys", [{"role": "user", "content": "hi"}])
        finally:
            openai_compat.OpenAICompatibleProvider.send_request = original  # type: ignore[assignment]

        assert result is None
        assert "401" in (err or "")
        # Only one base call — no retries on non-transient errors.
        assert provider_super_send.await_count == 1
        # Must be logged as ERROR, surface to the user.
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("401" in r.getMessage() for r in error_records)

    async def test_non_transient_failure_respects_log_errors_false(
        self, provider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Callers that opt out (e.g. health_check) get no error log."""
        from custom_components.selora_ai.providers import openai_compat

        provider_super_send = AsyncMock(return_value=(None, "HTTP 401: Unauthorized"))
        original = openai_compat.OpenAICompatibleProvider.send_request
        openai_compat.OpenAICompatibleProvider.send_request = provider_super_send  # type: ignore[assignment]
        try:
            with caplog.at_level(
                logging.ERROR, logger="custom_components.selora_ai.providers.selora_cloud"
            ):
                result, _err = await provider.send_request(
                    "sys", [{"role": "user", "content": "hi"}], log_errors=False
                )
        finally:
            openai_compat.OpenAICompatibleProvider.send_request = original  # type: ignore[assignment]

        assert result is None
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    async def test_transient_retry_exhausted_logs_warning(
        self, provider, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cold-start outliving the retry budget logs WARNING, not ERROR.

        WARNING doesn't trigger HA's "error reported by integration"
        notification, which is the whole point — the integration is
        recovering on its own, so don't keep flagging the user.
        """
        from custom_components.selora_ai.providers import openai_compat, selora_cloud

        # Skip the actual delays so the test runs fast.
        async def _no_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(selora_cloud.asyncio, "sleep", _no_sleep)

        provider_super_send = AsyncMock(
            return_value=(None, "HTTP 500: proxy handler: unable to reach app")
        )
        original = openai_compat.OpenAICompatibleProvider.send_request
        openai_compat.OpenAICompatibleProvider.send_request = provider_super_send  # type: ignore[assignment]
        try:
            with caplog.at_level(
                logging.DEBUG, logger="custom_components.selora_ai.providers.selora_cloud"
            ):
                result, err = await provider.send_request("sys", [{"role": "user", "content": "hi"}])
        finally:
            openai_compat.OpenAICompatibleProvider.send_request = original  # type: ignore[assignment]

        assert result is None
        assert "500" in (err or "")
        # All retry attempts + final attempt
        attempts = len(selora_cloud._UPSTREAM_RETRY_DELAYS) + 1
        assert provider_super_send.await_count == attempts
        # Must be WARNING, never ERROR.
        warn = [r for r in caplog.records if r.levelno == logging.WARNING]
        err_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("still unreachable" in r.getMessage() for r in warn)
        assert not err_records

    async def test_transient_retry_recovers_silently(
        self, provider, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If a retry succeeds, no ERROR/WARNING — only the INFO retry note."""
        from custom_components.selora_ai.providers import openai_compat, selora_cloud

        async def _no_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(selora_cloud.asyncio, "sleep", _no_sleep)

        responses = [
            (None, "HTTP 500: proxy handler: unable to reach app"),
            ("recovered", None),
        ]
        provider_super_send = AsyncMock(side_effect=responses)
        original = openai_compat.OpenAICompatibleProvider.send_request
        openai_compat.OpenAICompatibleProvider.send_request = provider_super_send  # type: ignore[assignment]
        try:
            with caplog.at_level(
                logging.DEBUG, logger="custom_components.selora_ai.providers.selora_cloud"
            ):
                result, err = await provider.send_request("sys", [{"role": "user", "content": "hi"}])
        finally:
            openai_compat.OpenAICompatibleProvider.send_request = original  # type: ignore[assignment]

        assert result == "recovered"
        assert err is None
        assert provider_super_send.await_count == 2
        # No WARNING / ERROR — recovery is transparent.
        loud = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not loud
