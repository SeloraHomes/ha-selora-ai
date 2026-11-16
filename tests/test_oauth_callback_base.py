"""Tests for the OAuth callback URL resolver — prefer panel origin.

The integration's OAuth flow was always landing on HA's external URL
(``get_url(prefer_external=True)``), which forced a user on the same
Wi-Fi as HA to bounce through their external endpoint. That breaks
for users whose external URL blocks anything other than HA-mediated
traffic (typical Cloudflare / proxy hardening setups). A Selora trial
user reported having to manually edit the callback URL during the
OAuth step (FB thread, 2026-06-03).

The fix lets the panel pass its current ``window.location.origin`` as
a hint, validated against HA's known reachable URLs before being
trusted as the callback base.
"""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.helpers.network import NoURLAvailableError
import pytest

from custom_components.selora_ai.oauth_link import (
    _is_mdns_origin,
    _known_ha_origins,
    _resolve_callback_base,
)


def _fake_get_url(**urls):
    """Build a ``get_url`` stub that returns a specific URL for matching
    flag combinations (``allow_internal``, ``allow_external``,
    ``require_cloud``)."""

    def _impl(_hass, **kwargs):
        # Cloud-only query — _known_ha_origins uses require_cloud to pick
        # up the Nabu Casa URL even when an external URL is also set.
        if kwargs.get("require_cloud"):
            if "cloud" in urls:
                return urls["cloud"]
            raise NoURLAvailableError
        internal = kwargs.get("allow_internal", True)
        external = kwargs.get("allow_external", True)
        # Internal-only call from _known_ha_origins
        if internal and not external:
            if "internal" in urls:
                return urls["internal"]
            raise NoURLAvailableError
        # External-only call from _known_ha_origins
        if not internal and external:
            if "external" in urls:
                return urls["external"]
            raise NoURLAvailableError
        # Combined / preferred-external call from the legacy fallback
        if internal and external:
            for key in ("external", "internal"):
                if key in urls:
                    return urls[key]
            raise NoURLAvailableError
        raise NoURLAvailableError

    return _impl


def test_known_origins_collects_internal_and_external() -> None:
    """Internal + external are queried separately so we see BOTH, not just
    the preferred one (the previous behaviour only ever returned the
    external URL)."""
    with patch(
        "custom_components.selora_ai.oauth_link.get_url",
        side_effect=_fake_get_url(
            internal="http://homeassistant.local:8123",
            external="https://example.duckdns.org",
        ),
    ):
        origins = _known_ha_origins(object())
    assert origins == {
        "http://homeassistant.local:8123",
        "https://example.duckdns.org",
    }


def test_known_origins_includes_cloud_alongside_external() -> None:
    """User has Nabu Casa Cloud AND a configured external URL — both must
    appear in the known-origins set. Previously the external-only query
    with ``allow_cloud=True`` returned only the external URL, so a panel
    opened via the Cloud remote URL was rejected and the OAuth callback
    fell back to the external host the user wasn't browsing from."""
    with patch(
        "custom_components.selora_ai.oauth_link.get_url",
        side_effect=_fake_get_url(
            internal="http://homeassistant.local:8123",
            external="https://example.duckdns.org",
            cloud="https://abc123.ui.nabu.casa",
        ),
    ):
        origins = _known_ha_origins(object())
    assert origins == {
        "http://homeassistant.local:8123",
        "https://example.duckdns.org",
        "https://abc123.ui.nabu.casa",
    }


def test_callback_base_uses_panel_origin_when_matches_cloud() -> None:
    """Panel opened via the Nabu Casa Cloud URL — callback stays on Cloud,
    not on the configured external URL."""
    with patch(
        "custom_components.selora_ai.oauth_link.get_url",
        side_effect=_fake_get_url(
            internal="http://homeassistant.local:8123",
            external="https://example.duckdns.org",
            cloud="https://abc123.ui.nabu.casa",
        ),
    ):
        base = _resolve_callback_base(object(), "https://abc123.ui.nabu.casa")
    assert base == "https://abc123.ui.nabu.casa"


def test_known_origins_tolerates_no_internal() -> None:
    """User has no internal URL set — external alone is fine."""
    with patch(
        "custom_components.selora_ai.oauth_link.get_url",
        side_effect=_fake_get_url(external="https://example.duckdns.org"),
    ):
        origins = _known_ha_origins(object())
    assert origins == {"https://example.duckdns.org"}


def test_callback_base_uses_panel_origin_when_matches_internal() -> None:
    """User opens the panel via the local URL — callback lands locally,
    not on the external URL."""
    with patch(
        "custom_components.selora_ai.oauth_link.get_url",
        side_effect=_fake_get_url(
            internal="http://homeassistant.local:8123",
            external="https://example.duckdns.org",
        ),
    ):
        base = _resolve_callback_base(object(), "http://homeassistant.local:8123")
    assert base == "http://homeassistant.local:8123"


def test_callback_base_uses_panel_origin_when_matches_external() -> None:
    """User opens the panel remotely — callback stays on the external URL.
    Same outcome as the legacy behaviour, but now via the hint path."""
    with patch(
        "custom_components.selora_ai.oauth_link.get_url",
        side_effect=_fake_get_url(
            internal="http://homeassistant.local:8123",
            external="https://example.duckdns.org",
        ),
    ):
        base = _resolve_callback_base(object(), "https://example.duckdns.org")
    assert base == "https://example.duckdns.org"


def test_callback_base_rejects_unknown_panel_origin() -> None:
    """A panel_origin that isn't one of HA's known URLs MUST NOT be used —
    otherwise a stray Origin header could redirect callbacks to an
    attacker-controlled host."""
    with patch(
        "custom_components.selora_ai.oauth_link.get_url",
        side_effect=_fake_get_url(
            internal="http://homeassistant.local:8123",
            external="https://example.duckdns.org",
        ),
    ):
        base = _resolve_callback_base(object(), "https://evil.example.com")
    # Falls back to the legacy preferred-external behaviour.
    assert base == "https://example.duckdns.org"


def test_callback_base_empty_panel_origin_falls_back() -> None:
    """No panel_origin (older panel build, non-panel caller) → legacy path."""
    with patch(
        "custom_components.selora_ai.oauth_link.get_url",
        side_effect=_fake_get_url(
            internal="http://homeassistant.local:8123",
            external="https://example.duckdns.org",
        ),
    ):
        base = _resolve_callback_base(object(), "")
    assert base == "https://example.duckdns.org"


def test_callback_base_tolerates_trailing_slash_in_panel_origin() -> None:
    """``window.location.origin`` doesn't carry a trailing slash, but be
    defensive — strip one if it shows up and compare normalised."""
    with patch(
        "custom_components.selora_ai.oauth_link.get_url",
        side_effect=_fake_get_url(internal="http://homeassistant.local:8123"),
    ):
        base = _resolve_callback_base(object(), "http://homeassistant.local:8123/")
    assert base == "http://homeassistant.local:8123"


def test_callback_base_case_insensitive_match() -> None:
    """Browsers sometimes upper-case the scheme/host on copy — match on lower."""
    with patch(
        "custom_components.selora_ai.oauth_link.get_url",
        side_effect=_fake_get_url(internal="http://homeassistant.local:8123"),
    ):
        base = _resolve_callback_base(object(), "HTTP://HomeAssistant.local:8123")
    # Returns the panel's exact casing (the OAuth provider will canonicalise
    # at its end); we just confirm the match succeeded.
    assert base.lower() == "http://homeassistant.local:8123"


def test_callback_base_accepts_mdns_origin_with_only_an_ip_internal_url() -> None:
    """No internal URL configured, so ``get_url`` reports the LAN IP — a panel
    opened via ``homeassistant.local`` must still get its callback there, not
    on the external URL the user may not be able to reach."""
    with patch(
        "custom_components.selora_ai.oauth_link.get_url",
        side_effect=_fake_get_url(
            internal="http://192.168.1.20:8123",
            external="https://example.duckdns.org",
        ),
    ):
        base = _resolve_callback_base(object(), "http://homeassistant.local:8123")
    assert base == "http://homeassistant.local:8123"


def test_callback_base_still_accepts_ip_origin() -> None:
    with patch(
        "custom_components.selora_ai.oauth_link.get_url",
        side_effect=_fake_get_url(
            internal="http://192.168.1.20:8123",
            external="https://example.duckdns.org",
        ),
    ):
        base = _resolve_callback_base(object(), "http://192.168.1.20:8123")
    assert base == "http://192.168.1.20:8123"


@pytest.mark.parametrize(
    "origin",
    [
        "http://homeassistant.local:8123",
        "https://my-hub.local",
        "HTTP://HomeAssistant.LOCAL:8123",
        "http://homeassistant.local.:8123",
    ],
)
def test_mdns_origin_accepted(origin: str) -> None:
    assert _is_mdns_origin(origin)


@pytest.mark.parametrize(
    "origin",
    [
        "http://evil.example.com",
        "http://homeassistant.local@evil.example.com",
        "http://evil.example.com#.local",
        "http://evil.example.com/.local",
        "http://homeassistant.local.evil.example.com",
        "http://homeassistant.local:8123/path",
        "http://homeassistant.local:8123?x=1",
        "http://user:pw@homeassistant.local:8123",
        "http://homeassistant.local:notaport",
        "ftp://homeassistant.local",
        "javascript://homeassistant.local",
        "http://.local",
        "",
    ],
)
def test_mdns_origin_rejected(origin: str) -> None:
    assert not _is_mdns_origin(origin)
