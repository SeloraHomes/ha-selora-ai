"""Cloud-session error messages must be localized like other runtime strings.

The provider raises ``CloudSessionExpiredError`` / ``CloudUnreachableError``
(both ``ConnectionError`` subclasses) carrying English default text; the
streaming WS chat handler maps the typed cause to a per-language message so
the advice (relink vs retry) reaches the user in their own language.
"""

from __future__ import annotations

import pytest

from custom_components.selora_ai import (
    _CLOUD_SESSION_EXPIRED_BY_LANG,
    _CLOUD_UNREACHABLE_BY_LANG,
    _approval_phrase,
)
from custom_components.selora_ai.providers.selora_cloud import (
    _REFRESH_TERMINAL_MESSAGE,
    _REFRESH_TRANSIENT_MESSAGE,
    CloudSessionExpiredError,
    CloudUnreachableError,
)

# The runtime dicts are keyed by base language code (region stripped by
# _normalize_lang), mirroring the sibling _*_BY_LANG tables.
_EXPECTED_LOCALES = {"en", "fr", "de", "es", "it", "nl", "hu", "zh", "pt", "ja", "ko", "ru"}


@pytest.mark.parametrize("table", [_CLOUD_SESSION_EXPIRED_BY_LANG, _CLOUD_UNREACHABLE_BY_LANG])
def test_all_locales_present(table: dict[str, str]) -> None:
    assert set(table) == _EXPECTED_LOCALES
    assert all(v.strip() for v in table.values())


def test_typed_errors_are_connection_errors() -> None:
    """Existing ``except ConnectionError`` arms must still catch these."""
    assert issubclass(CloudSessionExpiredError, ConnectionError)
    assert issubclass(CloudUnreachableError, ConnectionError)


def test_english_default_matches_dict() -> None:
    """The exception's fallback text must equal the ``en`` localized entry."""
    assert _REFRESH_TERMINAL_MESSAGE == _CLOUD_SESSION_EXPIRED_BY_LANG["en"]
    assert _REFRESH_TRANSIENT_MESSAGE == _CLOUD_UNREACHABLE_BY_LANG["en"]


def test_localizes_by_language() -> None:
    assert (
        _approval_phrase(_CLOUD_SESSION_EXPIRED_BY_LANG, "fr")
        == (_CLOUD_SESSION_EXPIRED_BY_LANG["fr"])
    )
    assert _approval_phrase(_CLOUD_UNREACHABLE_BY_LANG, "de") == (_CLOUD_UNREACHABLE_BY_LANG["de"])


def test_region_subtag_normalized() -> None:
    """zh-Hant / zh-Hans collapse to the single ``zh`` runtime entry."""
    assert (
        _approval_phrase(_CLOUD_SESSION_EXPIRED_BY_LANG, "zh-Hant")
        == (_CLOUD_SESSION_EXPIRED_BY_LANG["zh"])
    )


def test_unknown_locale_falls_back_to_english() -> None:
    assert _approval_phrase(_CLOUD_UNREACHABLE_BY_LANG, "xx") == (_CLOUD_UNREACHABLE_BY_LANG["en"])
    assert (
        _approval_phrase(_CLOUD_SESSION_EXPIRED_BY_LANG, None)
        == (_CLOUD_SESSION_EXPIRED_BY_LANG["en"])
    )
