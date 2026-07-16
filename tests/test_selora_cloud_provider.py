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
from custom_components.selora_ai.providers.selora_cloud import (
    CloudSessionExpiredError,
    CloudUnreachableError,
    SeloraCloudProvider,
    _RefreshResult,
)


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
        view = _aigateway_view({"ai_gateway": {"refresh_token": "aigw_x", "expires_at": 42.0}})
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
        """A non-transient, non-auth error must log loudly on the first attempt.

        (A 401 is handled separately — it triggers a refresh-and-retry, see
        ``TestAuthErrorRefreshRetry`` — so use a 400 here for the "fail fast,
        no retry" path.)
        """
        provider_send = AsyncMock(return_value=(None, "HTTP 400: Bad Request"))
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
                result, err = await provider.send_request(
                    "sys", [{"role": "user", "content": "hi"}]
                )
        finally:
            openai_compat.OpenAICompatibleProvider.send_request = original  # type: ignore[assignment]

        assert result is None
        assert "400" in (err or "")
        # Only one base call — no retries on non-transient errors.
        assert provider_super_send.await_count == 1
        # Must be logged as ERROR, surface to the user.
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("400" in r.getMessage() for r in error_records)

    async def test_non_transient_failure_respects_log_errors_false(
        self, provider, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Callers that opt out (e.g. health_check) get no error log."""
        from custom_components.selora_ai.providers import openai_compat

        provider_super_send = AsyncMock(return_value=(None, "HTTP 400: Bad Request"))
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
                result, err = await provider.send_request(
                    "sys", [{"role": "user", "content": "hi"}]
                )
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
                result, err = await provider.send_request(
                    "sys", [{"role": "user", "content": "hi"}]
                )
        finally:
            openai_compat.OpenAICompatibleProvider.send_request = original  # type: ignore[assignment]

        assert result == "recovered"
        assert err is None
        assert provider_super_send.await_count == 2
        # No WARNING / ERROR — recovery is transparent.
        loud = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not loud


class TestStreamingColdStartRetry:
    """Streaming chat (architect_chat_stream) must also retry cold-start 5xx,
    otherwise the first chat after HA restart fails while subsequent ones work.
    """

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
        p._needs_refresh = lambda: False  # type: ignore[method-assign]
        return p

    async def test_send_request_stream_retries_on_cold_start(
        self, provider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """First call raises proxy 5xx, second yields chunks — caller sees the chunks."""
        from custom_components.selora_ai.providers import openai_compat, selora_cloud

        async def _no_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(selora_cloud.asyncio, "sleep", _no_sleep)

        attempts = {"n": 0}

        async def _fake_stream(self, *_a, **_kw):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise ConnectionError(
                    "Selora Cloud: proxy handler: unable to reach app "
                    "(try increasing the proxy.app_start_timeout)"
                )
            for chunk in ("hel", "lo"):
                yield chunk

        original = openai_compat.OpenAICompatibleProvider.send_request_stream
        openai_compat.OpenAICompatibleProvider.send_request_stream = _fake_stream  # type: ignore[assignment]
        try:
            chunks: list[str] = []
            async for chunk in provider.send_request_stream(
                "sys", [{"role": "user", "content": "hi"}]
            ):
                chunks.append(chunk)
        finally:
            openai_compat.OpenAICompatibleProvider.send_request_stream = original  # type: ignore[assignment]

        assert "".join(chunks) == "hello"
        assert attempts["n"] == 2

    async def test_send_request_stream_does_not_retry_non_transient(
        self, provider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unrelated (non-auth, non-transient) ConnectionError propagates without retry.

        (A 401 is handled separately by the refresh-and-retry path — see
        ``TestAuthErrorRefreshRetry`` — so this uses a 400 to exercise the
        "propagate immediately" branch.)
        """
        from custom_components.selora_ai.providers import openai_compat, selora_cloud

        async def _no_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(selora_cloud.asyncio, "sleep", _no_sleep)

        attempts = {"n": 0}

        async def _fake_stream(self, *_a, **_kw):
            attempts["n"] += 1
            raise ConnectionError("Selora Cloud: HTTP 400: Bad Request")
            yield  # pragma: no cover — keep this an async generator

        original = openai_compat.OpenAICompatibleProvider.send_request_stream
        openai_compat.OpenAICompatibleProvider.send_request_stream = _fake_stream  # type: ignore[assignment]
        try:
            with pytest.raises(ConnectionError, match="400"):
                async for _ in provider.send_request_stream(
                    "sys", [{"role": "user", "content": "hi"}]
                ):
                    pass
        finally:
            openai_compat.OpenAICompatibleProvider.send_request_stream = original  # type: ignore[assignment]

        # No retry on non-transient.
        assert attempts["n"] == 1

    async def test_raw_request_stream_retries_on_cold_start(
        self, provider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The tool-calling streaming path must also recover from cold-start 5xx."""
        from custom_components.selora_ai.providers import openai_compat, selora_cloud

        async def _no_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(selora_cloud.asyncio, "sleep", _no_sleep)

        attempts = {"n": 0}
        sentinel = object()

        async def _fake_stream(self, *_a, **_kw):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise ConnectionError("LLM stream: HTTP 500: proxy handler: unable to reach app")
            yield sentinel

        original = openai_compat.OpenAICompatibleProvider.raw_request_stream
        openai_compat.OpenAICompatibleProvider.raw_request_stream = _fake_stream  # type: ignore[assignment]
        try:
            yielded: list[object] = []
            async for item in provider.raw_request_stream(
                "sys", [{"role": "user", "content": "hi"}]
            ):
                yielded.append(item)
        finally:
            openai_compat.OpenAICompatibleProvider.raw_request_stream = original  # type: ignore[assignment]

        assert yielded == [sentinel]
        assert attempts["n"] == 2


class TestTransientErrorDetector:
    """_is_transient_upstream_error must catch both request and stream formats."""

    def test_matches_proxy_500_with_marker(self) -> None:
        from custom_components.selora_ai.providers.selora_cloud import (
            _is_transient_upstream_error,
        )

        assert _is_transient_upstream_error("HTTP 500: proxy handler: unable to reach app")

    def test_matches_stream_format_without_status_prefix(self) -> None:
        """Streaming path raises 'Selora Cloud: <body>' without the HTTP code."""
        from custom_components.selora_ai.providers.selora_cloud import (
            _is_transient_upstream_error,
        )

        assert _is_transient_upstream_error(
            "Selora Cloud: proxy handler: unable to reach app "
            "(try increasing the proxy.app_start_timeout)"
        )

    def test_matches_app_start_timeout_hint(self) -> None:
        from custom_components.selora_ai.providers.selora_cloud import (
            _is_transient_upstream_error,
        )

        assert _is_transient_upstream_error("something app_start_timeout something")

    def test_does_not_match_unrelated_500(self) -> None:
        """A real 500 from the AI Gateway must NOT be silently retried."""
        from custom_components.selora_ai.providers.selora_cloud import (
            _is_transient_upstream_error,
        )

        assert not _is_transient_upstream_error("HTTP 500: internal server error")

    def test_does_not_match_401(self) -> None:
        from custom_components.selora_ai.providers.selora_cloud import (
            _is_transient_upstream_error,
        )

        assert not _is_transient_upstream_error("HTTP 401: Unauthorized")


class TestHealthCheckDoesNotCreateChatSession:
    """``health_check`` used to POST a "Hi" prompt to /chat/completions on every
    config-entry setup and reload, which the AI Gateway materializes as a chat
    session. That phantom-session leak is the regression these tests pin.
    """

    def _make_provider(self, hass, **overrides) -> SeloraCloudProvider:
        defaults = {
            "access_token": "ey.access",
            "refresh_token": "aigw_refresh",
            "expires_at": 9_999_999_999.0,
            "connect_url": "https://example.test",
            "client_id": "cid",
            "entry_id": "entry-id",
        }
        defaults.update(overrides)
        return SeloraCloudProvider(hass, **defaults)

    async def test_fresh_access_token_returns_true_without_any_request(
        self, hass, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The previous chat round-trip is the bug — a non-expired access
        token already proves the link works.
        """
        from custom_components.selora_ai.providers import openai_compat

        provider = self._make_provider(hass)

        send_super = AsyncMock(return_value=("should-not-be-called", None))
        send_self = AsyncMock(return_value=("should-not-be-called", None))
        refresh = AsyncMock(return_value=_RefreshResult.OK)

        monkeypatch.setattr(openai_compat.OpenAICompatibleProvider, "send_request", send_super)
        monkeypatch.setattr(provider, "send_request", send_self)
        monkeypatch.setattr(provider, "_refresh_access_token", refresh)

        assert await provider.health_check() is True
        assert send_self.await_count == 0
        assert send_super.await_count == 0
        assert refresh.await_count == 0

    async def test_no_credentials_returns_false_without_any_request(
        self, hass, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from custom_components.selora_ai.providers import openai_compat

        provider = self._make_provider(hass, access_token="", refresh_token="")

        send_super = AsyncMock(return_value=("nope", None))
        send_self = AsyncMock(return_value=("nope", None))
        monkeypatch.setattr(openai_compat.OpenAICompatibleProvider, "send_request", send_super)
        monkeypatch.setattr(provider, "send_request", send_self)

        assert await provider.health_check() is False
        assert send_self.await_count == 0
        assert send_super.await_count == 0

    async def test_expired_access_token_refreshes_via_token_endpoint(
        self, hass, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Refresh path probes the /oauth/aigw/token endpoint, NOT chat
        completions — so the AI Gateway never sees this as a session.
        """
        from custom_components.selora_ai.providers import openai_compat

        provider = self._make_provider(hass, expires_at=1.0)
        assert provider._needs_refresh() is True

        send_super = AsyncMock(return_value=("nope", None))
        send_self = AsyncMock(return_value=("nope", None))
        refresh = AsyncMock(return_value=_RefreshResult.OK)

        monkeypatch.setattr(openai_compat.OpenAICompatibleProvider, "send_request", send_super)
        monkeypatch.setattr(provider, "send_request", send_self)
        monkeypatch.setattr(provider, "_refresh_access_token", refresh)

        assert await provider.health_check() is True
        assert refresh.await_count == 1
        # Critical: no chat-completions roundtrip was issued.
        assert send_self.await_count == 0
        assert send_super.await_count == 0

    async def test_failed_refresh_returns_false(
        self, hass, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = self._make_provider(hass, expires_at=1.0)
        monkeypatch.setattr(
            provider, "_refresh_access_token", AsyncMock(return_value=_RefreshResult.TERMINAL)
        )

        assert await provider.health_check() is False


class TestMaskTokens:
    """_mask_tokens must redact every credential shape we may log."""

    def test_redacts_access_token_json_field(self) -> None:
        from custom_components.selora_ai.providers.selora_cloud import _mask_tokens

        out = _mask_tokens('{"access_token":"sk-abcdef-secret-value-123","x":1}')
        assert "sk-abcdef" not in out
        assert "secret-value" not in out
        assert '"access_token":"***"' in out

    def test_redacts_refresh_token_json_field(self) -> None:
        from custom_components.selora_ai.providers.selora_cloud import _mask_tokens

        out = _mask_tokens('{"refresh_token":"rt_abc.def_ghi-jkl"}')
        assert "rt_abc" not in out
        assert '"refresh_token":"***"' in out

    def test_redacts_bearer_header(self) -> None:
        from custom_components.selora_ai.providers.selora_cloud import _mask_tokens

        out = _mask_tokens("Authorization: Bearer eyJhbGc.eyJzdWI.signature_part")
        assert "eyJhbGc" not in out
        assert "Bearer ***" in out

    def test_redacts_bare_jwt(self) -> None:
        from custom_components.selora_ai.providers.selora_cloud import _mask_tokens

        out = _mask_tokens("oops eyJabc123-_.eyJdef456-_.signaturepartXYZ trailing")
        assert "eyJabc123" not in out
        assert "***" in out
        assert "trailing" in out

    def test_truncated_token_still_masked_when_called_before_slice(self) -> None:
        """Regression for the mask-then-truncate ordering bug.

        The refresh-failure log path must mask the full body BEFORE truncating
        to 200 chars; otherwise a token whose closing quote falls past the
        cutoff slips past the regex and leaks the prefix.
        """
        from custom_components.selora_ai.providers.selora_cloud import _mask_tokens

        long_token = "secret_prefix_" + ("A" * 300)
        body = f'{{"access_token":"{long_token}","trailing":"x"}}'
        # Production order: mask first, then slice. The masked output must
        # not contain any portion of the secret even when sliced to 200.
        masked_then_sliced = _mask_tokens(body)[:200]
        assert "secret_prefix" not in masked_then_sliced
        assert "AAAA" not in masked_then_sliced
        assert '"access_token":"***"' in masked_then_sliced


class _FakeResponse:
    """Minimal async-context-manager stand-in for an aiohttp response."""

    def __init__(self, status: int, payload: dict | None = None) -> None:
        self.status = status
        self._payload = payload or {}

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def text(self) -> str:
        return "body"

    async def json(self) -> dict:
        return self._payload


class _FakeSession:
    def __init__(self, response: _FakeResponse | Exception) -> None:
        self._response = response

    def post(self, *_a: object, **_kw: object) -> _FakeResponse:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _make_refresh_provider(hass) -> SeloraCloudProvider:
    return SeloraCloudProvider(
        hass,
        access_token="ey.stale",
        refresh_token="aigw_refresh",
        expires_at=1.0,  # already within refresh leeway → _needs_refresh() is True
        connect_url="https://example.test",
        client_id="cid",
        entry_id="entry-id",
    )


class TestRefreshCategorization:
    """_refresh_access_token classifies failures so the caller can react."""

    async def test_4xx_is_terminal(self, hass, monkeypatch: pytest.MonkeyPatch) -> None:
        from custom_components.selora_ai.providers import selora_cloud

        provider = _make_refresh_provider(hass)
        monkeypatch.setattr(
            selora_cloud,
            "async_get_clientsession",
            lambda _hass: _FakeSession(_FakeResponse(401)),
        )
        assert await provider._refresh_access_token() is _RefreshResult.TERMINAL

    async def test_5xx_is_transient(self, hass, monkeypatch: pytest.MonkeyPatch) -> None:
        from custom_components.selora_ai.providers import selora_cloud

        provider = _make_refresh_provider(hass)
        monkeypatch.setattr(
            selora_cloud,
            "async_get_clientsession",
            lambda _hass: _FakeSession(_FakeResponse(503)),
        )
        assert await provider._refresh_access_token() is _RefreshResult.TRANSIENT

    async def test_429_is_transient(self, hass, monkeypatch: pytest.MonkeyPatch) -> None:
        from custom_components.selora_ai.providers import selora_cloud

        provider = _make_refresh_provider(hass)
        monkeypatch.setattr(
            selora_cloud,
            "async_get_clientsession",
            lambda _hass: _FakeSession(_FakeResponse(429)),
        )
        assert await provider._refresh_access_token() is _RefreshResult.TRANSIENT

    async def test_network_error_is_transient(self, hass, monkeypatch: pytest.MonkeyPatch) -> None:
        import aiohttp

        from custom_components.selora_ai.providers import selora_cloud

        provider = _make_refresh_provider(hass)
        monkeypatch.setattr(
            selora_cloud,
            "async_get_clientsession",
            lambda _hass: _FakeSession(aiohttp.ClientError("boom")),
        )
        assert await provider._refresh_access_token() is _RefreshResult.TRANSIENT

    async def test_missing_access_token_is_transient(
        self, hass, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from custom_components.selora_ai.providers import selora_cloud

        provider = _make_refresh_provider(hass)
        monkeypatch.setattr(
            selora_cloud,
            "async_get_clientsession",
            lambda _hass: _FakeSession(_FakeResponse(200, {"expires_in": 3600})),
        )
        assert await provider._refresh_access_token() is _RefreshResult.TRANSIENT

    async def test_success_is_ok_and_persists(self, hass, monkeypatch: pytest.MonkeyPatch) -> None:
        from custom_components.selora_ai.providers import selora_cloud

        provider = _make_refresh_provider(hass)
        monkeypatch.setattr(provider, "_persist_tokens", lambda: None)
        monkeypatch.setattr(
            selora_cloud,
            "async_get_clientsession",
            lambda _hass: _FakeSession(
                _FakeResponse(200, {"access_token": "ey.new", "expires_in": 3600})
            ),
        )
        assert await provider._refresh_access_token() is _RefreshResult.OK
        assert provider._access_token == "ey.new"


class TestEnsureTokenBehaviour:
    """_ensure_token picks the message + retry behaviour by failure class."""

    async def test_terminal_fails_fast_with_relink_message(
        self, hass, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from custom_components.selora_ai.providers import selora_cloud

        provider = _make_refresh_provider(hass)
        refresh = AsyncMock(return_value=_RefreshResult.TERMINAL)
        monkeypatch.setattr(provider, "_refresh_access_token", refresh)
        slept: list[float] = []
        monkeypatch.setattr(
            selora_cloud.asyncio, "sleep", lambda s: slept.append(s) or _async_none()
        )

        with pytest.raises(CloudSessionExpiredError, match="relink"):
            await provider._ensure_token()
        # No retry, no sleep — a rejected credential can't be retried away.
        assert refresh.await_count == 1
        assert slept == []

    async def test_transient_retries_then_recovers(
        self, hass, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from custom_components.selora_ai.providers import selora_cloud

        provider = _make_refresh_provider(hass)
        refresh = AsyncMock(side_effect=[_RefreshResult.TRANSIENT, _RefreshResult.OK])
        monkeypatch.setattr(provider, "_refresh_access_token", refresh)
        monkeypatch.setattr(selora_cloud.asyncio, "sleep", lambda _s: _async_none())

        # Should not raise — the second attempt recovers.
        await provider._ensure_token()
        assert refresh.await_count == 2

    async def test_transient_exhausted_raises_try_again_message(
        self, hass, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from custom_components.selora_ai.providers import selora_cloud

        provider = _make_refresh_provider(hass)
        refresh = AsyncMock(return_value=_RefreshResult.TRANSIENT)
        monkeypatch.setattr(provider, "_refresh_access_token", refresh)
        monkeypatch.setattr(selora_cloud.asyncio, "sleep", lambda _s: _async_none())

        with pytest.raises(CloudUnreachableError, match="try again"):
            await provider._ensure_token()
        # Initial attempt + one per retry delay.
        assert refresh.await_count == len(selora_cloud._UPSTREAM_RETRY_DELAYS) + 1

    async def test_transient_then_terminal_switches_to_relink(
        self, hass, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A refresh token revoked mid-retry must surface relink, not try-again."""
        from custom_components.selora_ai.providers import selora_cloud

        provider = _make_refresh_provider(hass)
        refresh = AsyncMock(side_effect=[_RefreshResult.TRANSIENT, _RefreshResult.TERMINAL])
        monkeypatch.setattr(provider, "_refresh_access_token", refresh)
        monkeypatch.setattr(selora_cloud.asyncio, "sleep", lambda _s: _async_none())

        with pytest.raises(CloudSessionExpiredError, match="relink"):
            await provider._ensure_token()
        assert refresh.await_count == 2


async def _async_none() -> None:
    return None


def _make_jwt(exp: float | int | None) -> str:
    """Build a signature-less JWT whose payload carries (or omits) ``exp``.

    Only the payload segment matters to ``_jwt_expiry`` — the header and
    signature are cosmetic. Uses unpadded base64url, exactly as a real JWT.
    """
    import base64
    import json

    def _seg(obj: dict) -> str:
        raw = json.dumps(obj).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    header = _seg({"alg": "RS256", "typ": "JWT"})
    payload = _seg({} if exp is None else {"exp": exp})
    return f"{header}.{payload}.sig"


class TestJwtExpiry:
    """_jwt_expiry reads the token's own ``exp`` so we don't depend on expires_in."""

    def test_reads_numeric_exp(self) -> None:
        from custom_components.selora_ai.providers.selora_cloud import _jwt_expiry

        assert _jwt_expiry(_make_jwt(1_800_000_000)) == 1_800_000_000.0

    def test_missing_exp_is_zero(self) -> None:
        from custom_components.selora_ai.providers.selora_cloud import _jwt_expiry

        assert _jwt_expiry(_make_jwt(None)) == 0.0

    def test_zero_exp_is_zero(self) -> None:
        from custom_components.selora_ai.providers.selora_cloud import _jwt_expiry

        assert _jwt_expiry(_make_jwt(0)) == 0.0

    def test_boolean_exp_rejected(self) -> None:
        """``True`` is an int subclass — must not be read as exp=1.0."""
        from custom_components.selora_ai.providers.selora_cloud import _jwt_expiry

        assert _jwt_expiry(_make_jwt(True)) == 0.0

    def test_not_a_jwt_is_zero(self) -> None:
        from custom_components.selora_ai.providers.selora_cloud import _jwt_expiry

        assert _jwt_expiry("not-a-jwt") == 0.0
        assert _jwt_expiry("") == 0.0

    def test_undecodable_payload_is_zero(self) -> None:
        from custom_components.selora_ai.providers.selora_cloud import _jwt_expiry

        # Middle segment isn't valid base64url JSON.
        assert _jwt_expiry("aaa.!!!not-base64!!!.sig") == 0.0


class TestAuthErrorDetector:
    """_is_auth_error must catch the 401 across the two error shapes."""

    def test_matches_http_401_prefix(self) -> None:
        from custom_components.selora_ai.providers.selora_cloud import _is_auth_error

        # send_request / raw_request / raw_request_stream keep the status.
        assert _is_auth_error("HTTP 401: Unauthorized")
        assert _is_auth_error("LLM stream: HTTP 401: nope")

    def test_matches_gateway_message_without_status(self) -> None:
        """send_request_stream unwraps to just the body message (no code)."""
        from custom_components.selora_ai.providers.selora_cloud import _is_auth_error

        assert _is_auth_error("Selora Cloud: Invalid or expired token")
        assert _is_auth_error("Failed to connect to Selora Cloud: authentication_error")

    def test_ignores_non_auth_errors(self) -> None:
        from custom_components.selora_ai.providers.selora_cloud import _is_auth_error

        assert not _is_auth_error("HTTP 500: unable to reach app")
        assert not _is_auth_error("HTTP 400: Bad Request")
        assert not _is_auth_error(None)
        assert not _is_auth_error("")


class TestInitRecoversExpiryFromJwt:
    """A provisioned token with unknown expiry (0.0) recovers exp from the JWT."""

    def test_unknown_expiry_recovered_from_jwt(self, hass) -> None:
        provider = SeloraCloudProvider(
            hass,
            access_token=_make_jwt(1_800_000_000),
            refresh_token="aigw_refresh",
            expires_at=0.0,  # nested blob / provisioner carries no expiry
            connect_url="https://example.test",
        )
        assert provider._expires_at == 1_800_000_000.0

    def test_explicit_expiry_is_not_overridden(self, hass) -> None:
        provider = SeloraCloudProvider(
            hass,
            access_token=_make_jwt(1_800_000_000),
            refresh_token="aigw_refresh",
            expires_at=42.0,  # a known (flat) expiry wins — no JWT peek
            connect_url="https://example.test",
        )
        assert provider._expires_at == 42.0

    def test_no_token_stays_zero(self, hass) -> None:
        provider = SeloraCloudProvider(
            hass,
            access_token="",
            refresh_token="aigw_refresh",
            expires_at=0.0,
            connect_url="https://example.test",
        )
        assert provider._expires_at == 0.0


class TestRefreshPrefersJwtExp:
    """A refresh must derive expiry from the new token's ``exp``.

    Previously a response missing ``expires_in`` left _expires_at=0.0
    ("unknown"), which both defeated the proactive refresh AND meant
    .storage never got a durable expiry — the two upstream bugs this
    fixes.
    """

    async def test_expiry_taken_from_jwt_when_expires_in_absent(
        self, hass, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from custom_components.selora_ai.providers import selora_cloud

        provider = _make_refresh_provider(hass)
        persisted: list[float] = []
        monkeypatch.setattr(
            provider, "_persist_tokens", lambda: persisted.append(provider._expires_at)
        )
        monkeypatch.setattr(
            selora_cloud,
            "async_get_clientsession",
            lambda _hass: _FakeSession(
                # No expires_in — expiry must come from the token itself.
                _FakeResponse(200, {"access_token": _make_jwt(1_800_000_000)})
            ),
        )
        assert await provider._refresh_access_token() is _RefreshResult.OK
        assert provider._expires_at == 1_800_000_000.0
        # And the durable copy carries the real expiry, not 0.0.
        assert persisted == [1_800_000_000.0]

    async def test_jwt_exp_wins_over_expires_in(
        self, hass, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from custom_components.selora_ai.providers import selora_cloud

        provider = _make_refresh_provider(hass)
        monkeypatch.setattr(provider, "_persist_tokens", lambda: None)
        monkeypatch.setattr(
            selora_cloud,
            "async_get_clientsession",
            lambda _hass: _FakeSession(
                _FakeResponse(
                    200,
                    {"access_token": _make_jwt(1_800_000_000), "expires_in": 3600},
                )
            ),
        )
        assert await provider._refresh_access_token() is _RefreshResult.OK
        assert provider._expires_at == 1_800_000_000.0


class TestAuthErrorRefreshRetry:
    """A 401 forces a fresh token and one retry — the durable fix for a dead
    token that slipped past the (unknown/stale) expiry check."""

    @pytest.fixture
    def provider(self, hass) -> SeloraCloudProvider:
        p = SeloraCloudProvider(
            hass,
            access_token="ey.stale",
            refresh_token="aigw_refresh",
            expires_at=9_999_999_999.0,
            connect_url="https://example.test",
            client_id="cid",
            entry_id="entry-id",
        )
        # Proactive check is happy — the token only fails reactively (401).
        p._needs_refresh = lambda: False  # type: ignore[method-assign]
        return p

    async def test_send_request_refreshes_and_retries(
        self, provider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from custom_components.selora_ai.providers import openai_compat, selora_cloud

        monkeypatch.setattr(selora_cloud.asyncio, "sleep", lambda _s: _async_none())
        refresh = AsyncMock(return_value=_RefreshResult.OK)
        monkeypatch.setattr(provider, "_refresh_access_token", refresh)

        super_send = AsyncMock(side_effect=[(None, "HTTP 401: Invalid token"), ("ok", None)])
        original = openai_compat.OpenAICompatibleProvider.send_request
        openai_compat.OpenAICompatibleProvider.send_request = super_send  # type: ignore[assignment]
        try:
            result, err = await provider.send_request("sys", [{"role": "user", "content": "hi"}])
        finally:
            openai_compat.OpenAICompatibleProvider.send_request = original  # type: ignore[assignment]

        assert (result, err) == ("ok", None)
        assert refresh.await_count == 1
        assert super_send.await_count == 2
        # The refresh was scoped to the exact token that was rejected, so a
        # concurrent request that already refreshed it isn't clobbered.
        assert refresh.await_args.kwargs == {"stale_token": "ey.stale"}

    async def test_send_request_terminal_refresh_raises_relink(
        self, provider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from custom_components.selora_ai.providers import openai_compat, selora_cloud

        monkeypatch.setattr(selora_cloud.asyncio, "sleep", lambda _s: _async_none())
        monkeypatch.setattr(
            provider, "_refresh_access_token", AsyncMock(return_value=_RefreshResult.TERMINAL)
        )
        super_send = AsyncMock(return_value=(None, "HTTP 401: Invalid token"))
        original = openai_compat.OpenAICompatibleProvider.send_request
        openai_compat.OpenAICompatibleProvider.send_request = super_send  # type: ignore[assignment]
        try:
            with pytest.raises(CloudSessionExpiredError, match="relink"):
                await provider.send_request("sys", [{"role": "user", "content": "hi"}])
        finally:
            openai_compat.OpenAICompatibleProvider.send_request = original  # type: ignore[assignment]

    async def test_send_request_persistent_401_does_not_loop(
        self, provider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A token the gateway keeps rejecting surfaces after one retry."""
        from custom_components.selora_ai.providers import openai_compat, selora_cloud

        monkeypatch.setattr(selora_cloud.asyncio, "sleep", lambda _s: _async_none())
        refresh = AsyncMock(return_value=_RefreshResult.OK)
        monkeypatch.setattr(provider, "_refresh_access_token", refresh)
        super_send = AsyncMock(return_value=(None, "HTTP 401: Invalid token"))
        original = openai_compat.OpenAICompatibleProvider.send_request
        openai_compat.OpenAICompatibleProvider.send_request = super_send  # type: ignore[assignment]
        try:
            result, err = await provider.send_request("sys", [{"role": "user", "content": "hi"}])
        finally:
            openai_compat.OpenAICompatibleProvider.send_request = original  # type: ignore[assignment]

        assert result is None
        assert "401" in (err or "")
        # Exactly one forced refresh + one retry — no hammering.
        assert refresh.await_count == 1
        assert super_send.await_count == 2

    async def test_reactive_refresh_retries_transient_then_recovers(
        self, provider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A one-off refresh-endpoint blip during 401 recovery self-heals.

        The reactive path must retry TRANSIENT refresh outcomes over the
        same budget as the proactive ``_ensure_token`` path — otherwise a
        momentary 5xx on the token endpoint turns a recoverable 401 into a
        hard failure.
        """
        from custom_components.selora_ai.providers import selora_cloud

        monkeypatch.setattr(selora_cloud.asyncio, "sleep", lambda _s: _async_none())
        refresh = AsyncMock(side_effect=[_RefreshResult.TRANSIENT, _RefreshResult.OK])
        monkeypatch.setattr(provider, "_refresh_access_token", refresh)

        # Must not raise — the second refresh attempt recovers.
        await provider._force_refresh_after_auth_error("ey.stale")

        assert refresh.await_count == 2
        # stale_token is forwarded on every attempt (forces + dedups).
        assert all(c.kwargs == {"stale_token": "ey.stale"} for c in refresh.await_args_list)

    async def test_reactive_refresh_transient_exhausted_raises_unreachable(
        self, provider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from custom_components.selora_ai.providers import selora_cloud

        monkeypatch.setattr(selora_cloud.asyncio, "sleep", lambda _s: _async_none())
        refresh = AsyncMock(return_value=_RefreshResult.TRANSIENT)
        monkeypatch.setattr(provider, "_refresh_access_token", refresh)

        with pytest.raises(CloudUnreachableError, match="try again"):
            await provider._force_refresh_after_auth_error("ey.stale")

        assert refresh.await_count == len(selora_cloud._UPSTREAM_RETRY_DELAYS) + 1

    async def test_send_request_final_auth_retry_preserves_timeout(
        self, provider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A custom timeout must survive the final attempt and its 401 retry.

        The final out-of-loop attempt (and the post-refresh retry) previously
        fell back to DEFAULT_LLM_TIMEOUT, so a long analysis call could time
        out only on this recovery path.
        """
        from custom_components.selora_ai.providers import openai_compat, selora_cloud

        monkeypatch.setattr(selora_cloud.asyncio, "sleep", lambda _s: _async_none())
        monkeypatch.setattr(
            provider, "_refresh_access_token", AsyncMock(return_value=_RefreshResult.OK)
        )

        # 3 transient attempts (drain the loop), then a 401 on the final
        # attempt, then a successful post-refresh retry.
        n = len(selora_cloud._UPSTREAM_RETRY_DELAYS)
        responses = [(None, "HTTP 500: proxy handler: unable to reach app")] * n
        responses += [(None, "HTTP 401: Invalid token"), ("ok", None)]
        super_send = AsyncMock(side_effect=responses)
        original = openai_compat.OpenAICompatibleProvider.send_request
        openai_compat.OpenAICompatibleProvider.send_request = super_send  # type: ignore[assignment]
        try:
            result, err = await provider.send_request(
                "sys", [{"role": "user", "content": "hi"}], timeout=99.0
            )
        finally:
            openai_compat.OpenAICompatibleProvider.send_request = original  # type: ignore[assignment]

        assert (result, err) == ("ok", None)
        # Every attempt — loop, final, and post-refresh retry — kept timeout=99.
        assert super_send.await_count == n + 2
        assert all(c.kwargs.get("timeout") == 99.0 for c in super_send.await_args_list)

    async def test_raw_request_refreshes_and_retries(
        self, provider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from custom_components.selora_ai.providers import openai_compat

        monkeypatch.setattr(
            provider, "_refresh_access_token", AsyncMock(return_value=_RefreshResult.OK)
        )
        super_raw = AsyncMock(
            side_effect=[ConnectionError("HTTP 401: Invalid token"), {"ok": True}]
        )
        original = openai_compat.OpenAICompatibleProvider.raw_request
        openai_compat.OpenAICompatibleProvider.raw_request = super_raw  # type: ignore[assignment]
        try:
            result = await provider.raw_request("sys", [{"role": "user", "content": "hi"}])
        finally:
            openai_compat.OpenAICompatibleProvider.raw_request = original  # type: ignore[assignment]

        assert result == {"ok": True}
        assert super_raw.await_count == 2

    async def test_raw_request_non_auth_error_propagates(
        self, provider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from custom_components.selora_ai.providers import openai_compat

        refresh = AsyncMock(return_value=_RefreshResult.OK)
        monkeypatch.setattr(provider, "_refresh_access_token", refresh)
        super_raw = AsyncMock(side_effect=ConnectionError("HTTP 400: Bad Request"))
        original = openai_compat.OpenAICompatibleProvider.raw_request
        openai_compat.OpenAICompatibleProvider.raw_request = super_raw  # type: ignore[assignment]
        try:
            with pytest.raises(ConnectionError, match="400"):
                await provider.raw_request("sys", [{"role": "user", "content": "hi"}])
        finally:
            openai_compat.OpenAICompatibleProvider.raw_request = original  # type: ignore[assignment]

        assert refresh.await_count == 0
        assert super_raw.await_count == 1

    async def test_send_request_stream_refreshes_and_retries(
        self, provider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from custom_components.selora_ai.providers import openai_compat, selora_cloud

        monkeypatch.setattr(selora_cloud.asyncio, "sleep", lambda _s: _async_none())
        monkeypatch.setattr(
            provider, "_refresh_access_token", AsyncMock(return_value=_RefreshResult.OK)
        )
        attempts = {"n": 0}

        async def _fake_stream(self, *_a, **_kw):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise ConnectionError("Selora Cloud: Invalid or expired token")
                yield  # pragma: no cover
            for chunk in ("hel", "lo"):
                yield chunk

        original = openai_compat.OpenAICompatibleProvider.send_request_stream
        openai_compat.OpenAICompatibleProvider.send_request_stream = _fake_stream  # type: ignore[assignment]
        try:
            chunks = [
                c
                async for c in provider.send_request_stream(
                    "sys", [{"role": "user", "content": "hi"}]
                )
            ]
        finally:
            openai_compat.OpenAICompatibleProvider.send_request_stream = original  # type: ignore[assignment]

        assert "".join(chunks) == "hello"
        assert attempts["n"] == 2

    async def test_raw_request_stream_refreshes_and_retries(
        self, provider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from custom_components.selora_ai.providers import openai_compat, selora_cloud

        monkeypatch.setattr(selora_cloud.asyncio, "sleep", lambda _s: _async_none())
        monkeypatch.setattr(
            provider, "_refresh_access_token", AsyncMock(return_value=_RefreshResult.OK)
        )
        attempts = {"n": 0}
        sentinel = object()

        async def _fake_stream(self, *_a, **_kw):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise ConnectionError("LLM stream: HTTP 401: Invalid token")
                yield  # pragma: no cover
            yield sentinel

        original = openai_compat.OpenAICompatibleProvider.raw_request_stream
        openai_compat.OpenAICompatibleProvider.raw_request_stream = _fake_stream  # type: ignore[assignment]
        try:
            yielded = [
                item
                async for item in provider.raw_request_stream(
                    "sys", [{"role": "user", "content": "hi"}]
                )
            ]
        finally:
            openai_compat.OpenAICompatibleProvider.raw_request_stream = original  # type: ignore[assignment]

        assert yielded == [sentinel]
        assert attempts["n"] == 2

    async def test_send_request_stream_final_attempt_recovers_from_401(
        self, provider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """3 transient failures, then the final attempt is the first to 401.

        The final streaming attempt lives outside the retry loop, so it must
        carry its own one-time auth recovery — otherwise a token that expires
        mid-cold-start propagates the 401 unhandled.
        """
        from custom_components.selora_ai.providers import openai_compat, selora_cloud

        monkeypatch.setattr(selora_cloud.asyncio, "sleep", lambda _s: _async_none())
        monkeypatch.setattr(
            provider, "_refresh_access_token", AsyncMock(return_value=_RefreshResult.OK)
        )
        attempts = {"n": 0}

        async def _fake_stream(self, *_a, **_kw):
            attempts["n"] += 1
            n = attempts["n"]
            if n <= len(selora_cloud._UPSTREAM_RETRY_DELAYS):
                raise ConnectionError("Selora Cloud: proxy handler: unable to reach app")
                yield  # pragma: no cover
            if n == len(selora_cloud._UPSTREAM_RETRY_DELAYS) + 1:
                raise ConnectionError("Selora Cloud: Invalid or expired token")
                yield  # pragma: no cover
            for chunk in ("he", "llo"):
                yield chunk

        original = openai_compat.OpenAICompatibleProvider.send_request_stream
        openai_compat.OpenAICompatibleProvider.send_request_stream = _fake_stream  # type: ignore[assignment]
        try:
            chunks = [
                c
                async for c in provider.send_request_stream(
                    "sys", [{"role": "user", "content": "hi"}]
                )
            ]
        finally:
            openai_compat.OpenAICompatibleProvider.send_request_stream = original  # type: ignore[assignment]

        assert "".join(chunks) == "hello"
        # transient loop (N) + final 401 attempt (1) + recovered retry (1)
        assert attempts["n"] == len(selora_cloud._UPSTREAM_RETRY_DELAYS) + 2

    async def test_raw_request_stream_final_attempt_recovers_from_401(
        self, provider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from custom_components.selora_ai.providers import openai_compat, selora_cloud

        monkeypatch.setattr(selora_cloud.asyncio, "sleep", lambda _s: _async_none())
        monkeypatch.setattr(
            provider, "_refresh_access_token", AsyncMock(return_value=_RefreshResult.OK)
        )
        attempts = {"n": 0}
        sentinel = object()

        async def _fake_stream(self, *_a, **_kw):
            attempts["n"] += 1
            n = attempts["n"]
            if n <= len(selora_cloud._UPSTREAM_RETRY_DELAYS):
                raise ConnectionError("LLM stream: HTTP 503: unable to reach app")
                yield  # pragma: no cover
            if n == len(selora_cloud._UPSTREAM_RETRY_DELAYS) + 1:
                raise ConnectionError("LLM stream: HTTP 401: Invalid token")
                yield  # pragma: no cover
            yield sentinel

        original = openai_compat.OpenAICompatibleProvider.raw_request_stream
        openai_compat.OpenAICompatibleProvider.raw_request_stream = _fake_stream  # type: ignore[assignment]
        try:
            yielded = [
                item
                async for item in provider.raw_request_stream(
                    "sys", [{"role": "user", "content": "hi"}]
                )
            ]
        finally:
            openai_compat.OpenAICompatibleProvider.raw_request_stream = original  # type: ignore[assignment]

        assert yielded == [sentinel]
        assert attempts["n"] == len(selora_cloud._UPSTREAM_RETRY_DELAYS) + 2


class _CountingSession:
    """Fake aiohttp session that counts POSTs to the token endpoint."""

    def __init__(self, payload: dict) -> None:
        self.calls = 0
        self._payload = payload

    def post(self, *_a: object, **_kw: object) -> _FakeResponse:
        self.calls += 1
        return _FakeResponse(200, self._payload)


class TestConcurrentAuthRefresh:
    """A late 401 for an already-refreshed token must not clobber it."""

    async def test_concurrent_401s_refresh_once(
        self, hass, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        from custom_components.selora_ai.providers import selora_cloud

        old_token = _make_jwt(1_800_000_000)
        new_token = _make_jwt(1_900_000_000)
        provider = SeloraCloudProvider(
            hass,
            access_token=old_token,
            refresh_token="aigw_refresh",
            expires_at=1_800_000_000.0,
            connect_url="https://example.test",
            client_id="cid",
            entry_id="entry-id",
        )
        monkeypatch.setattr(provider, "_persist_tokens", lambda: None)
        session = _CountingSession({"access_token": new_token})
        monkeypatch.setattr(selora_cloud, "async_get_clientsession", lambda _hass: session)

        # Two requests that both used the same stale bearer report a 401.
        await asyncio.gather(
            provider._force_refresh_after_auth_error(old_token),
            provider._force_refresh_after_auth_error(old_token),
        )

        # The second recovery sees the token already rotated → no extra POST,
        # and the freshly minted token is preserved.
        assert session.calls == 1
        assert provider._access_token == new_token

    async def test_stale_token_no_longer_current_is_noop(
        self, hass, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If another request already replaced the token, recovery is a no-op OK."""
        from custom_components.selora_ai.providers import selora_cloud

        provider = SeloraCloudProvider(
            hass,
            access_token=_make_jwt(1_900_000_000),  # already the fresh token
            refresh_token="aigw_refresh",
            expires_at=1_900_000_000.0,
            connect_url="https://example.test",
            client_id="cid",
            entry_id="entry-id",
        )
        session = _CountingSession({"access_token": "unused"})
        monkeypatch.setattr(selora_cloud, "async_get_clientsession", lambda _hass: session)

        # A 401 arrives for a token that is no longer live.
        await provider._force_refresh_after_auth_error("ey.some-older-token")

        assert session.calls == 0
        assert provider._access_token == _make_jwt(1_900_000_000)
