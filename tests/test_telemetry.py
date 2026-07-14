"""Tests for anonymous, opt-in LLM-output repair telemetry.

The privacy contract is the point of these tests: nothing leaves the
network unless the user opts in, and a payload can only ever carry the
allowlisted counter/enum keys — never entity ids, friendly names,
prompts, or responses.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.const import (
    CONF_ENTRY_TYPE,
    CONF_TELEMETRY_ENABLED,
    DOMAIN,
    ENTRY_TYPE_LLM,
    TELEMETRY_EVENT_ACTIVITY,
    TELEMETRY_EVENT_REPAIR,
    TELEMETRY_EVENT_SNAPSHOT,
    TELEMETRY_PROJECT_KEY,
    TELEMETRY_SNAPSHOT_INTERVAL_HOURS,
)
from custom_components.selora_ai.telemetry import (
    _ACTIVITY_COUNTER_KEYS,
    _ACTIVITY_PROPERTY_KEYS,
    _REPAIR_PROPERTY_KEYS,
    _SNAPSHOT_PROPERTY_KEYS,
    REPAIR_TYPES,
    TelemetryClient,
    get_telemetry,
    record_activity,
    record_repair,
    repair_capture,
)


class _FakeResp:
    def __init__(self, status: int = 200) -> None:
        self.status = status


class _FakeCtx:
    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp

    async def __aenter__(self) -> _FakeResp:
        return self._resp

    async def __aexit__(self, *_: Any) -> bool:
        return False


class _FakeSession:
    """Records POST calls; optionally raises to exercise the error path."""

    def __init__(self, *, status: int = 200, raise_exc: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._status = status
        self._raise_exc = raise_exc

    def post(
        self, url: str, *, json: dict[str, Any] | None = None, timeout: Any = None
    ) -> _FakeCtx:
        self.calls.append({"url": url, "json": json})
        if self._raise_exc is not None:
            raise self._raise_exc
        return _FakeCtx(_FakeResp(self._status))


def _add_llm_entry(hass, *, telemetry_enabled: bool | None = None) -> MockConfigEntry:
    options: dict[str, Any] = {}
    if telemetry_enabled is not None:
        options[CONF_TELEMETRY_ENABLED] = telemetry_enabled
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="test_llm_entry",
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_LLM},
        options=options,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def client(hass) -> TelemetryClient:
    """A TelemetryClient with in-memory storage (no disk I/O)."""
    c = TelemetryClient(hass)
    c._store.async_load = AsyncMock(return_value=None)
    c._store.async_save = AsyncMock()
    return c


async def _emit(
    hass,
    client: TelemetryClient,
    repairs: list[str],
    session: _FakeSession,
    *,
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-6",
) -> None:
    with patch(
        "custom_components.selora_ai.telemetry.async_get_clientsession",
        return_value=session,
    ):
        client.record_repairs(repairs, provider=provider, model=model)
        await hass.async_block_till_done()


# ── opt-in gating ────────────────────────────────────────────────────


async def test_no_post_when_disabled_by_default(hass, client) -> None:
    """Default (no toggle set) is opt-OUT — nothing is sent."""
    _add_llm_entry(hass)  # no telemetry_enabled key → default False
    session = _FakeSession()
    await _emit(hass, client, ["qwen_normalize"], session)
    assert session.calls == []


async def test_no_post_when_explicitly_disabled(hass, client) -> None:
    _add_llm_entry(hass, telemetry_enabled=False)
    session = _FakeSession()
    await _emit(hass, client, ["service_name_inference"], session)
    assert session.calls == []


async def test_posts_when_enabled(hass, client) -> None:
    _add_llm_entry(hass, telemetry_enabled=True)
    session = _FakeSession()
    await _emit(hass, client, ["qwen_normalize"], session)

    assert len(session.calls) == 1
    body = session.calls[0]["json"]
    assert body["event"] == TELEMETRY_EVENT_REPAIR
    assert body["api_key"] == TELEMETRY_PROJECT_KEY
    assert body["distinct_id"]
    props = body["properties"]
    assert props["repair_type"] == "qwen_normalize"
    assert props["provider"] == "anthropic"
    # Cloud model ids are public version strings — passed through verbatim.
    assert props["model"] == "claude-sonnet-4-6"
    assert props["app_version"]


async def test_local_model_name_is_not_transmitted(hass, client) -> None:
    """A user-named local model must never reach the wire."""
    _add_llm_entry(hass, telemetry_enabled=True)
    session = _FakeSession()
    await _emit(
        hass,
        client,
        ["qwen_normalize"],
        session,
        provider="ollama",
        model="my-private-house-model-v3",
    )
    props = session.calls[0]["json"]["properties"]
    assert props["model"] == "local"
    assert "my-private-house-model-v3" not in str(props)


async def test_finetuned_cloud_model_is_redacted(hass, client) -> None:
    """OpenAI fine-tune ids embed an org name — must not be transmitted."""
    _add_llm_entry(hass, telemetry_enabled=True)
    session = _FakeSession()
    await _emit(
        hass,
        client,
        ["qwen_normalize"],
        session,
        provider="openai",
        model="ft:gpt-4o:acme-corp::abc123",
    )
    props = session.calls[0]["json"]["properties"]
    assert props["model"] == "custom"
    assert "acme-corp" not in str(props)


async def test_catalog_cloud_models_pass_through(hass, client) -> None:
    """Public catalog ids (incl. vendor/model) are safe version strings."""
    _add_llm_entry(hass, telemetry_enabled=True)
    for provider, model in (
        ("anthropic", "claude-sonnet-4-6"),
        ("openai", "gpt-5.4"),
        ("openrouter", "anthropic/claude-sonnet-4-6"),
    ):
        session = _FakeSession()
        await _emit(hass, client, ["qwen_normalize"], session, provider=provider, model=model)
        assert session.calls[0]["json"]["properties"]["model"] == model


async def test_catalog_shaped_unknown_cloud_model_is_redacted(hass, client) -> None:
    """A catalog-shaped but non-catalog id (private deployment) must not leak."""
    _add_llm_entry(hass, telemetry_enabled=True)
    session = _FakeSession()
    await _emit(
        hass,
        client,
        ["qwen_normalize"],
        session,
        provider="anthropic",
        model="my-private-house-model-v3",
    )
    props = session.calls[0]["json"]["properties"]
    assert props["model"] == "custom"
    assert "my-private-house-model-v3" not in str(props)


async def test_consent_withdrawn_during_prep_blocks_post(hass, client) -> None:
    """Disabling telemetry while async prep awaits stops the POST."""
    entry = _add_llm_entry(hass, telemetry_enabled=True)
    session = _FakeSession()

    real_app_version = client._async_app_version

    async def _disable_then_version() -> str:
        # Simulate the user flipping the toggle off mid-preparation.
        hass.config_entries.async_update_entry(entry, options={CONF_TELEMETRY_ENABLED: False})
        return await real_app_version()

    with patch.object(client, "_async_app_version", side_effect=_disable_then_version):
        await _emit(hass, client, ["qwen_normalize"], session)

    assert session.calls == []


async def test_dedups_repeated_repairs_within_a_call(hass, client) -> None:
    """The same repair firing twice in one call = one data point."""
    _add_llm_entry(hass, telemetry_enabled=True)
    session = _FakeSession()
    await _emit(hass, client, ["qwen_normalize", "qwen_normalize", "state_info_strip"], session)

    emitted = sorted(c["json"]["properties"]["repair_type"] for c in session.calls)
    assert emitted == ["qwen_normalize", "state_info_strip"]


async def test_empty_repairs_no_post(hass, client) -> None:
    _add_llm_entry(hass, telemetry_enabled=True)
    session = _FakeSession()
    await _emit(hass, client, [], session)
    assert session.calls == []


# ── privacy boundary ─────────────────────────────────────────────────


async def test_payload_only_has_allowlisted_keys(hass, client) -> None:
    """Emitted properties never contain anything outside the allowlist."""
    _add_llm_entry(hass, telemetry_enabled=True)
    session = _FakeSession()
    await _emit(hass, client, sorted(REPAIR_TYPES), session)

    for call in session.calls:
        props = call["json"]["properties"]
        # Ignore PostHog `$` control keys ($ip/$geoip_disable) and the
        # anonymous install id (PostHog requires distinct_id in properties)
        # — anonymity guards / identity, not telemetry data.
        data_keys = {k for k in props if not k.startswith("$") and k != "distinct_id"}
        assert data_keys <= _REPAIR_PROPERTY_KEYS
        # repair_type is always a fixed enum, never free-form text.
        assert props["repair_type"] in REPAIR_TYPES
        # The remaining values carry no household/content identifiers.
        free_form = f"{props['provider']} {props['model']} {props['app_version']}".lower()
        for banned in ("entity_id", "friendly_name", "prompt", "response", "light.", "@"):
            assert banned not in free_form


async def test_payload_discards_ip_and_geoip(hass, client) -> None:
    """Every POST overrides the IP + disables GeoIP so PostHog can't link
    the anon install id to the household network."""
    _add_llm_entry(hass, telemetry_enabled=True)
    session = _FakeSession()
    await _emit(hass, client, ["qwen_normalize"], session)
    props = session.calls[0]["json"]["properties"]
    assert props["$ip"] == "0.0.0.0"
    assert props["$geoip_disable"] is True


async def test_distinct_id_is_inside_properties(hass, client) -> None:
    """PostHog's capture schema requires distinct_id in properties."""
    _add_llm_entry(hass, telemetry_enabled=True)
    session = _FakeSession()
    await _emit(hass, client, ["qwen_normalize"], session)
    body = session.calls[0]["json"]
    install_id = await client._async_install_id()
    assert body["properties"]["distinct_id"] == install_id
    # Still also sent top-level for legacy capture compatibility.
    assert body["distinct_id"] == install_id


async def test_consent_withdrawn_during_install_id_blocks_post(hass, client) -> None:
    """Disabling telemetry while the install-id load awaits stops the POST."""
    entry = _add_llm_entry(hass, telemetry_enabled=True)
    session = _FakeSession()

    async def _disable_then_id() -> str:
        hass.config_entries.async_update_entry(entry, options={CONF_TELEMETRY_ENABLED: False})
        return "deadbeef"

    with patch.object(client, "_async_install_id", side_effect=_disable_then_id):
        await _emit(hass, client, ["qwen_normalize"], session)

    assert session.calls == []


async def test_capture_rejects_disallowed_property(hass, client) -> None:
    """A coding error that adds a non-allowlisted key drops the event."""
    _add_llm_entry(hass, telemetry_enabled=True)
    session = _FakeSession()
    with patch(
        "custom_components.selora_ai.telemetry.async_get_clientsession",
        return_value=session,
    ):
        await client._capture(
            TELEMETRY_EVENT_REPAIR,
            {"repair_type": "qwen_normalize", "entity_id": "light.kitchen"},
            allowed=_REPAIR_PROPERTY_KEYS,
        )
    assert session.calls == []


async def test_allowlist_excludes_content_keys() -> None:
    for banned in ("entity_id", "friendly_name", "prompt", "response", "message"):
        assert banned not in _REPAIR_PROPERTY_KEYS
        assert banned not in _SNAPSHOT_PROPERTY_KEYS
        assert banned not in _ACTIVITY_PROPERTY_KEYS


# ── record_repair buffer ─────────────────────────────────────────────


def test_record_repair_outside_scope_is_noop() -> None:
    # No active buffer → must not raise.
    record_repair("qwen_normalize")


def test_repair_capture_collects_known_types() -> None:
    with repair_capture() as repairs:
        record_repair("qwen_normalize")
        record_repair("state_info_strip")
    assert repairs == ["qwen_normalize", "state_info_strip"]


def test_repair_capture_drops_unknown_types() -> None:
    with repair_capture() as repairs:
        record_repair("totally_made_up")
        record_repair("service_name_inference")
    assert repairs == ["service_name_inference"]


# ── qwen_normalize covers all repair paths ───────────────────────────


def test_qwen_uppercase_intent_records_repair() -> None:
    """A valid-but-uppercased intent is a drift correction."""
    from custom_components.selora_ai.providers._qwen_repair import (
        normalize_response_content,
    )

    with repair_capture() as repairs:
        normalize_response_content('{"intent":"Answer","response":"hi"}')
    assert "qwen_normalize" in repairs


def test_qwen_automation_normalization_records_repair() -> None:
    """normalize_automation_block fixing the envelope counts."""
    from custom_components.selora_ai.providers._qwen_repair import (
        normalize_response_content,
    )

    with repair_capture() as repairs:
        normalize_response_content(
            '{"intent":"automation","triggers":[{"platform":"state","entity_id":"sensor.x"}]}'
        )
    assert "qwen_normalize" in repairs


def test_qwen_prose_wrapped_json_records_repair() -> None:
    """Stripping prose around a valid JSON object counts."""
    from custom_components.selora_ai.providers._qwen_repair import (
        normalize_response_content,
    )

    with repair_capture() as repairs:
        normalize_response_content('Here you go: {"intent":"answer","response":"hi"}')
    assert "qwen_normalize" in repairs


def test_qwen_clean_envelope_records_no_repair() -> None:
    """An already-canonical answer envelope is idempotent — no repair."""
    from custom_components.selora_ai.providers._qwen_repair import (
        normalize_response_content,
    )

    with repair_capture() as repairs:
        normalize_response_content('{"intent":"answer","response":"hi"}')
    assert repairs == []


def test_tool_markup_leak_records_repair() -> None:
    """Stripping a leaked tool-call block counts as a repair."""
    from custom_components.selora_ai.llm_client.parsers import (
        strip_leaked_tool_markup,
    )

    with repair_capture() as repairs:
        strip_leaked_tool_markup('Looking.\n\n<invoke name="list_devices">x</invoke>')
    assert "tool_markup_leak" in repairs


def test_no_tool_markup_leak_records_no_repair() -> None:
    """Clean prose with a bare ``<`` must not record a leak repair."""
    from custom_components.selora_ai.llm_client.parsers import (
        strip_leaked_tool_markup,
    )

    with repair_capture() as repairs:
        strip_leaked_tool_markup("Set the heat when temp < 20 degrees.")
    assert repairs == []


# ── resilience + identity ────────────────────────────────────────────


async def test_post_exception_is_swallowed(hass, client) -> None:
    """A network failure must never propagate into the call path."""
    _add_llm_entry(hass, telemetry_enabled=True)
    session = _FakeSession(raise_exc=RuntimeError("network down"))
    # Should not raise.
    await _emit(hass, client, ["qwen_normalize"], session)


# ── home-inventory snapshot ──────────────────────────────────────────


async def test_snapshot_posts_when_enabled(hass, client) -> None:
    _add_llm_entry(hass, telemetry_enabled=True)
    session = _FakeSession()
    with patch(
        "custom_components.selora_ai.telemetry.async_get_clientsession",
        return_value=session,
    ):
        await client.async_send_snapshot(provider="anthropic")
        await hass.async_block_till_done()

    assert len(session.calls) == 1
    body = session.calls[0]["json"]
    assert body["event"] == TELEMETRY_EVENT_SNAPSHOT
    props = body["properties"]
    # Only allowlisted counter/enum keys (ignoring `$` anonymity guards and
    # the install-id identity field) — never anything identifying.
    data_keys = {k for k in props if not k.startswith("$") and k != "distinct_id"}
    assert data_keys <= _SNAPSHOT_PROPERTY_KEYS
    assert props["$ip"] == "0.0.0.0"
    assert props["llm_provider"] == "anthropic"
    assert isinstance(props["devices"], int)
    assert isinstance(props["devices_by_integration"], dict)
    assert props["ha_version"]
    assert props["app_version"]


async def test_snapshot_no_post_when_disabled(hass, client) -> None:
    _add_llm_entry(hass, telemetry_enabled=False)
    session = _FakeSession()
    with patch(
        "custom_components.selora_ai.telemetry.async_get_clientsession",
        return_value=session,
    ):
        await client.async_send_snapshot(provider="anthropic")
        await hass.async_block_till_done()
    assert session.calls == []


async def test_install_id_is_stable_and_anonymous(hass, client) -> None:
    first = await client._async_install_id()
    second = await client._async_install_id()
    assert first == second
    # 32 hex chars from uuid4().hex — no household/network identifier.
    assert len(first) == 32
    assert all(ch in "0123456789abcdef" for ch in first)


async def test_snapshot_buckets_custom_integration_domains(hass, client) -> None:
    """A custom integration's domain may embed a private name — bucket it."""
    from homeassistant.helpers import device_registry as dr

    _add_llm_entry(hass, telemetry_enabled=True)
    custom = MockConfigEntry(domain="my_private_company_thing", entry_id="custom_entry")
    custom.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id="custom_entry",
        identifiers={("my_private_company_thing", "dev1")},
    )
    session = _FakeSession()
    with patch(
        "custom_components.selora_ai.telemetry.async_get_clientsession",
        return_value=session,
    ):
        await client.async_send_snapshot(provider="anthropic")
        await hass.async_block_till_done()

    breakdown = session.calls[0]["json"]["properties"]["devices_by_integration"]
    assert "my_private_company_thing" not in breakdown
    assert breakdown.get("other", 0) >= 1


async def test_snapshot_includes_configured_country(hass, client) -> None:
    """The coarse, self-declared HA country is sent when set — no IP leak."""
    _add_llm_entry(hass, telemetry_enabled=True)
    hass.config.country = "CA"
    session = _FakeSession()
    with patch(
        "custom_components.selora_ai.telemetry.async_get_clientsession",
        return_value=session,
    ):
        await client.async_send_snapshot(provider="anthropic")
        await hass.async_block_till_done()

    props = session.calls[0]["json"]["properties"]
    assert props["country"] == "CA"
    # GeoIP stays disabled and the real IP is never transmitted.
    assert props["$ip"] == "0.0.0.0"
    assert props["$geoip_disable"] is True


async def test_snapshot_omits_country_when_unset(hass, client) -> None:
    """An install with no configured country sends no country property."""
    _add_llm_entry(hass, telemetry_enabled=True)
    hass.config.country = None
    session = _FakeSession()
    with patch(
        "custom_components.selora_ai.telemetry.async_get_clientsession",
        return_value=session,
    ):
        await client.async_send_snapshot(provider="anthropic")
        await hass.async_block_till_done()

    assert "country" not in session.calls[0]["json"]["properties"]


# ── reload behaviour (telemetry options are hot, no reload) ───────────


async def test_options_only_telemetry_change_skips_reload(hass) -> None:
    from unittest.mock import AsyncMock as _AsyncMock

    from custom_components.selora_ai import async_reload_entry

    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="reload_entry",
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_LLM},
        options={},
    )
    entry.add_to_hass(hass)
    bucket = hass.data.setdefault(DOMAIN, {})
    bucket.setdefault("_entry_data_snapshots", {})["reload_entry"] = dict(entry.data)
    bucket.setdefault("_entry_options_snapshots", {})["reload_entry"] = dict(entry.options)

    hass.config_entries.async_update_entry(entry, options={CONF_TELEMETRY_ENABLED: True})
    with patch.object(hass.config_entries, "async_reload", new=_AsyncMock()) as reload:
        await async_reload_entry(hass, entry)
    reload.assert_not_called()


async def test_data_change_still_triggers_reload(hass) -> None:
    from unittest.mock import AsyncMock as _AsyncMock

    from custom_components.selora_ai import async_reload_entry

    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="reload_entry2",
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_LLM, "llm_provider": "anthropic"},
        options={},
    )
    entry.add_to_hass(hass)
    bucket = hass.data.setdefault(DOMAIN, {})
    bucket.setdefault("_entry_data_snapshots", {})["reload_entry2"] = dict(entry.data)
    bucket.setdefault("_entry_options_snapshots", {})["reload_entry2"] = dict(entry.options)

    hass.config_entries.async_update_entry(
        entry, data={CONF_ENTRY_TYPE: ENTRY_TYPE_LLM, "llm_provider": "openai"}
    )
    with patch.object(hass.config_entries, "async_reload", new=_AsyncMock()) as reload:
        await async_reload_entry(hass, entry)
    reload.assert_called_once()


# ── usage-activity rollup ────────────────────────────────────────────


async def _send_activity(hass, client: TelemetryClient, session: _FakeSession, **kw: Any) -> None:
    with patch(
        "custom_components.selora_ai.telemetry.async_get_clientsession",
        return_value=session,
    ):
        await client.async_send_activity(provider=kw.get("provider", "anthropic"))
        await hass.async_block_till_done()


def test_record_activity_accumulates(client) -> None:
    client.record_activity("chat_messages")
    client.record_activity("chat_messages", 4)
    client.record_activity("automations_created")
    assert client._activity == {"chat_messages": 5, "automations_created": 1}


def test_record_activity_tracks_chat_feedback(client) -> None:
    client.record_activity("chat_feedback_positive")
    client.record_activity("chat_feedback_negative", 2)
    assert client._activity == {
        "chat_feedback_positive": 1,
        "chat_feedback_negative": 2,
    }
    assert "chat_feedback_positive" in _ACTIVITY_COUNTER_KEYS
    assert "chat_feedback_negative" in _ACTIVITY_COUNTER_KEYS


def test_record_activity_tracks_chat_feedback_by_subject(client) -> None:
    for subject in ("automation", "scene", "prose"):
        for rating in ("positive", "negative"):
            client.record_activity(f"chat_feedback_{subject}_{rating}")
            assert f"chat_feedback_{subject}_{rating}" in _ACTIVITY_COUNTER_KEYS
    assert client._activity == {
        "chat_feedback_automation_positive": 1,
        "chat_feedback_automation_negative": 1,
        "chat_feedback_scene_positive": 1,
        "chat_feedback_scene_negative": 1,
        "chat_feedback_prose_positive": 1,
        "chat_feedback_prose_negative": 1,
    }


def test_record_activity_drops_unknown_and_nonpositive(client) -> None:
    client.record_activity("totally_made_up")
    client.record_activity("chat_messages", 0)
    client.record_activity("chat_messages", -3)
    assert client._activity == {}


async def test_activity_no_post_when_disabled_and_counters_preserved(hass, client) -> None:
    """Opted out: nothing is sent and the in-memory counters survive."""
    _add_llm_entry(hass, telemetry_enabled=False)
    client.record_activity("chat_messages", 7)
    session = _FakeSession()
    await _send_activity(hass, client, session)
    assert session.calls == []
    # Preserved so the first window after opt-in isn't lost.
    assert client._activity == {"chat_messages": 7}


async def test_activity_no_post_when_empty(hass, client) -> None:
    _add_llm_entry(hass, telemetry_enabled=True)
    session = _FakeSession()
    await _send_activity(hass, client, session)
    assert session.calls == []


async def test_activity_posts_and_resets_when_enabled(hass, client) -> None:
    _add_llm_entry(hass, telemetry_enabled=True)
    client.record_activity("automations_created", 2)
    client.record_activity("chat_messages", 9)
    client.record_activity("llm_input_tokens", 1234)
    session = _FakeSession()
    await _send_activity(hass, client, session, provider="openai")

    assert len(session.calls) == 1
    body = session.calls[0]["json"]
    assert body["event"] == TELEMETRY_EVENT_ACTIVITY
    props = body["properties"]
    assert props["automations_created"] == 2
    assert props["chat_messages"] == 9
    assert props["llm_input_tokens"] == 1234
    assert props["llm_provider"] == "openai"
    assert props["period_hours"] == TELEMETRY_SNAPSHOT_INTERVAL_HOURS
    assert props["app_version"]
    # Counters reset after a successful flush — the next window starts at 0.
    assert client._activity == {}


async def test_activity_payload_only_has_allowlisted_keys(hass, client) -> None:
    """Every counter we can emit stays inside the activity allowlist."""
    _add_llm_entry(hass, telemetry_enabled=True)
    for name in _ACTIVITY_COUNTER_KEYS:
        client.record_activity(name)
    session = _FakeSession()
    await _send_activity(hass, client, session)

    props = session.calls[0]["json"]["properties"]
    data_keys = {k for k in props if not k.startswith("$") and k != "distinct_id"}
    assert data_keys <= _ACTIVITY_PROPERTY_KEYS
    # Counters are plain ints; provider is the only free-form-ish field and
    # is a fixed enum supplied by the caller, never household content.
    for banned in ("entity_id", "friendly_name", "prompt", "response", "light.", "@"):
        assert banned not in str(props).lower()


async def test_module_record_activity_targets_shared_client(hass) -> None:
    """The module helper increments the same client get_telemetry returns."""
    record_activity(hass, "scenes_created", 3)
    assert get_telemetry(hass)._activity == {"scenes_created": 3}


async def test_activity_post_exception_is_swallowed(hass, client) -> None:
    _add_llm_entry(hass, telemetry_enabled=True)
    client.record_activity("chat_messages")
    session = _FakeSession(raise_exc=RuntimeError("network down"))
    # Must not raise even though the POST blows up.
    await _send_activity(hass, client, session)
    # And the window must survive a failed POST — retried next tick.
    assert client._activity == {"chat_messages": 1}


async def test_activity_preserved_on_http_error(hass, client) -> None:
    """An HTTP 4xx/5xx means the event wasn't ingested — keep the window."""
    _add_llm_entry(hass, telemetry_enabled=True)
    client.record_activity("automations_created", 3)
    session = _FakeSession(status=500)
    await _send_activity(hass, client, session)
    assert len(session.calls) == 1  # attempted
    assert client._activity == {"automations_created": 3}  # not cleared


async def test_activity_preserved_when_consent_withdrawn_mid_prep(hass, client) -> None:
    """Disabling telemetry during the awaited app-version lookup must not
    drop the window — it's blocked at _capture, never sent."""
    entry = _add_llm_entry(hass, telemetry_enabled=True)
    client.record_activity("chat_messages", 5)
    session = _FakeSession()

    real_app_version = client._async_app_version

    async def _disable_then_version() -> str:
        hass.config_entries.async_update_entry(entry, options={CONF_TELEMETRY_ENABLED: False})
        return await real_app_version()

    with patch.object(client, "_async_app_version", side_effect=_disable_then_version):
        await _send_activity(hass, client, session)

    assert session.calls == []
    assert client._activity == {"chat_messages": 5}


async def test_activity_concurrent_increments_survive_flush(hass, client) -> None:
    """Increments that land during the POST seed the next window: a
    successful flush subtracts only what was sent, not the whole dict."""
    _add_llm_entry(hass, telemetry_enabled=True)
    client.record_activity("chat_messages", 4)

    real_capture = client._capture

    async def _capture_then_increment(*args: Any, **kwargs: Any) -> bool:
        # Simulate a chat message arriving while the POST is in flight.
        client.record_activity("chat_messages", 2)
        return await real_capture(*args, **kwargs)

    session = _FakeSession()
    with patch.object(client, "_capture", side_effect=_capture_then_increment):
        await _send_activity(hass, client, session)

    # 4 were sent; the 2 that arrived mid-flush remain for the next tick.
    assert client._activity == {"chat_messages": 2}
