"""The two entry options that let a harness measure the model, not the policy.

Both default to enforcing, and the first class here is the regression
guard for that: an install that sets neither must behave exactly as it
did before the options existed. Everything after it asserts that setting
one actually reaches the decision it names — the acceptance criterion is
that a control run which never acts stops scoring the same as a model
that does, and it cannot unless these refusals become service calls.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.command_policy_options import (
    ENFORCED,
    resolve_command_policy_options,
)
from custom_components.selora_ai.const import (
    CONF_COMMAND_ALLOWLIST_ENABLED,
    CONF_COMMAND_APPROVAL_REQUIRED,
    CONF_ENTRY_TYPE,
    DOMAIN,
    ENTRY_TYPE_DEVICE,
    ENTRY_TYPE_LLM,
)
from custom_components.selora_ai.llm_client.command_policy import (
    APPROVAL_PENDING_HINT,
    apply_command_policy,
    call_required_approval,
    validate_command_action,
)


def _llm_entry(hass, **options: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="test_llm_entry",
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_LLM},
        options=options,
    )
    entry.add_to_hass(hass)
    return entry


def _unattended(hass) -> MockConfigEntry:
    """Both overrides set — what the benchmark's harness configures."""
    return _llm_entry(
        hass,
        **{
            CONF_COMMAND_APPROVAL_REQUIRED: False,
            CONF_COMMAND_ALLOWLIST_ENABLED: False,
        },
    )


def _ent(entity_id: str, state: str = "on", **attributes: Any) -> dict[str, Any]:
    return {"entity_id": entity_id, "state": state, "attributes": attributes}


def _command(service: str, entity_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "intent": "command",
        "response": "Done.",
        "calls": [
            {
                "service": service,
                "target": {"entity_id": [entity_id]},
                "data": data or {},
            }
        ],
    }


# ── The default: nothing changes for an install that sets neither ───────────


class TestDefaultsUnchanged:
    def test_no_entry_at_all_resolves_enforced(self, hass) -> None:
        assert resolve_command_policy_options(hass) == ENFORCED

    def test_no_hass_resolves_enforced(self) -> None:
        assert resolve_command_policy_options(None) == ENFORCED

    def test_entry_without_the_keys_resolves_enforced(self, hass) -> None:
        _llm_entry(hass)
        assert resolve_command_policy_options(hass) == ENFORCED

    def test_device_entry_is_not_consulted(self, hass) -> None:
        """Only the LLM entry carries the options, as telemetry's toggle does."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            entry_id="device_entry",
            data={CONF_ENTRY_TYPE: ENTRY_TYPE_DEVICE},
            options={CONF_COMMAND_APPROVAL_REQUIRED: False},
        )
        entry.add_to_hass(hass)
        assert resolve_command_policy_options(hass) == ENFORCED

    def test_review_service_still_asks(self, hass) -> None:
        result = apply_command_policy(
            _command("lock.unlock", "lock.front"),
            [_ent("lock.front", "locked")],
            hass=hass,
        )
        assert result["intent"] == "command_approval"
        assert result["response"] == APPROVAL_PENDING_HINT
        assert result["calls"] == []

    def test_unlisted_domain_still_refused(self, hass) -> None:
        result = apply_command_policy(
            _command("todo.add_item", "todo.shopping_list", {"item": "eggs"}),
            [_ent("todo.shopping_list")],
            hass=hass,
        )
        assert result["intent"] == "answer"
        assert result["calls"] == []
        assert "no-chat-execution" in result["validation_error"]

    def test_unlisted_verb_still_refused(self, hass) -> None:
        result = apply_command_policy(
            _command("media_player.select_source", "media_player.den", {"source": "HDMI 1"}),
            [_ent("media_player.den")],
            hass=hass,
        )
        assert result["intent"] == "answer"
        assert result["calls"] == []
        assert "not a valid media_player service" in result["validation_error"]

    def test_unlisted_data_key_still_refused(self, hass) -> None:
        result = apply_command_policy(
            _command("light.turn_on", "light.kitchen", {"flash": "short"}),
            [_ent("light.kitchen")],
            hass=hass,
        )
        assert result["intent"] == "answer"
        assert "unsupported parameters" in result["validation_error"]


# ── command_approval_required: false ────────────────────────────────────────


class TestApprovalBypass:
    def test_review_service_executes(self, hass) -> None:
        _llm_entry(hass, **{CONF_COMMAND_APPROVAL_REQUIRED: False})
        result = apply_command_policy(
            _command("lock.unlock", "lock.front"),
            [_ent("lock.front", "locked")],
            hass=hass,
        )
        assert result["intent"] == "command"
        assert [c["service"] for c in result["calls"]] == ["lock.unlock"]
        assert result["response"] != APPROVAL_PENDING_HINT

    def test_garage_cover_elevation_executes(self, hass) -> None:
        """The device_class elevation is approval gating, so it yields too."""
        _llm_entry(hass, **{CONF_COMMAND_APPROVAL_REQUIRED: False})
        hass.states.async_set(
            "cover.garage", "closed", {"device_class": "garage", "friendly_name": "Garage"}
        )
        result = apply_command_policy(
            _command("cover.open_cover", "cover.garage"),
            [_ent("cover.garage", "closed", device_class="garage")],
            hass=hass,
        )
        assert result["intent"] == "command"
        assert [c["service"] for c in result["calls"]] == ["cover.open_cover"]

    def test_garage_cover_elevation_still_gates_by_default(self, hass) -> None:
        hass.states.async_set("cover.garage", "closed", {"device_class": "garage"})
        result = apply_command_policy(
            _command("cover.open_cover", "cover.garage"),
            [_ent("cover.garage", "closed", device_class="garage")],
            hass=hass,
        )
        assert result["intent"] == "command_approval"

    def test_shape_validation_still_runs(self, hass) -> None:
        """Only the wait-for-a-human step is dropped, not the payload check."""
        _llm_entry(hass, **{CONF_COMMAND_APPROVAL_REQUIRED: False})
        result = apply_command_policy(
            _command("lock.unlock", "lock.nonexistent"),
            [_ent("lock.front", "locked")],
            hass=hass,
        )
        assert result["intent"] == "answer"
        assert "isn't a device you have set up" in result["validation_error"]

    def test_tool_path_reports_no_approval_needed(self, hass) -> None:
        _llm_entry(hass, **{CONF_COMMAND_APPROVAL_REQUIRED: False})
        validation = validate_command_action(
            "lock.unlock",
            ["lock.front"],
            {},
            known_entity_ids={"lock.front"},
            hass=hass,
        )
        assert validation["valid"] is True
        assert validation["requires_approval"] is False

    def test_call_required_approval_is_false(self, hass) -> None:
        _llm_entry(hass, **{CONF_COMMAND_APPROVAL_REQUIRED: False})
        call = {"service": "lock.unlock", "target": {"entity_id": ["lock.front"]}}
        assert call_required_approval(hass, call) is False

    def test_call_required_approval_is_true_by_default(self, hass) -> None:
        call = {"service": "lock.unlock", "target": {"entity_id": ["lock.front"]}}
        assert call_required_approval(hass, call) is True

    def test_allowlist_still_enforced_on_its_own(self, hass) -> None:
        """The two options are independent — this one does not imply the other."""
        _llm_entry(hass, **{CONF_COMMAND_APPROVAL_REQUIRED: False})
        result = apply_command_policy(
            _command("todo.add_item", "todo.shopping_list", {"item": "eggs"}),
            [_ent("todo.shopping_list")],
            hass=hass,
        )
        assert result["intent"] == "answer"
        assert result["calls"] == []


# ── command_allowlist_enabled: false ────────────────────────────────────────


class TestAllowlistBypass:
    def test_unlisted_domain_executes(self, hass) -> None:
        _llm_entry(hass, **{CONF_COMMAND_ALLOWLIST_ENABLED: False})
        hass.services.async_register("todo", "add_item", lambda call: None)
        result = apply_command_policy(
            _command("todo.add_item", "todo.shopping_list", {"item": "eggs"}),
            [_ent("todo.shopping_list")],
            hass=hass,
        )
        assert result["intent"] == "command"
        assert result["calls"][0]["service"] == "todo.add_item"
        assert result["calls"][0]["data"] == {"item": "eggs"}

    def test_unlisted_verb_executes_unrewritten(self, hass) -> None:
        """A real service must not be verb-repaired into a curated one.

        ``media_player.select_source`` is absent from the tables and the
        prose says "Playing", which the repair hints map to
        ``media_play`` — answering a question nobody asked. HA's own
        registry is the authority once the tables stop being one.
        """
        _llm_entry(hass, **{CONF_COMMAND_ALLOWLIST_ENABLED: False})
        hass.services.async_register("media_player", "select_source", lambda call: None)
        proposal = _command("media_player.select_source", "media_player.den", {"source": "HDMI 1"})
        proposal["response"] = "Playing on the Den speaker."
        result = apply_command_policy(proposal, [_ent("media_player.den")], hass=hass)
        assert result["intent"] == "command"
        assert result["calls"][0]["service"] == "media_player.select_source"
        assert result["calls"][0]["data"] == {"source": "HDMI 1"}

    def test_unlisted_data_key_accepted(self, hass) -> None:
        _llm_entry(hass, **{CONF_COMMAND_ALLOWLIST_ENABLED: False})
        result = apply_command_policy(
            _command("light.turn_on", "light.kitchen", {"flash": "short"}),
            [_ent("light.kitchen")],
            hass=hass,
        )
        assert result["intent"] == "command"
        assert result["calls"][0]["data"] == {"flash": "short"}

    def test_cross_domain_target_accepted(self, hass) -> None:
        """``homeassistant.turn_on`` on a light is the shape this unblocks."""
        _llm_entry(hass, **{CONF_COMMAND_ALLOWLIST_ENABLED: False})
        hass.services.async_register("homeassistant", "turn_on", lambda call: None)
        result = apply_command_policy(
            _command("homeassistant.turn_on", "light.kitchen"),
            [_ent("light.kitchen", "off")],
            hass=hass,
        )
        assert result["intent"] == "command"
        assert result["calls"][0]["service"] == "homeassistant.turn_on"

    def test_service_home_assistant_does_not_have_is_refused(self, hass) -> None:
        """Not the allowlist: dispatching would raise and still read as success."""
        _llm_entry(hass, **{CONF_COMMAND_ALLOWLIST_ENABLED: False})
        result = apply_command_policy(
            _command("todo.invent_item", "todo.shopping_list"),
            [_ent("todo.shopping_list")],
            hass=hass,
        )
        assert result["intent"] == "answer"
        assert "not a service Home Assistant has" in result["validation_error"]

    def test_bogus_verb_in_a_curated_domain_is_still_repaired(self, hass) -> None:
        """The repair is a correction, not a restriction, so it survives."""
        _llm_entry(hass, **{CONF_COMMAND_ALLOWLIST_ENABLED: False})
        proposal = _command("cover.cover", "cover.blind")
        proposal["response"] = "Opening the blind."
        result = apply_command_policy(proposal, [_ent("cover.blind", "closed")], hass=hass)
        assert result["intent"] == "command"
        assert result["calls"][0]["service"] == "cover.open_cover"

    def test_unknown_entity_still_refused(self, hass) -> None:
        _llm_entry(hass, **{CONF_COMMAND_ALLOWLIST_ENABLED: False})
        hass.services.async_register("todo", "add_item", lambda call: None)
        result = apply_command_policy(
            _command("todo.add_item", "todo.nonexistent", {"item": "eggs"}),
            [_ent("todo.shopping_list")],
            hass=hass,
        )
        assert result["intent"] == "answer"
        assert "unknown entity_id" in result["validation_error"]

    def test_approval_still_required_on_its_own(self, hass) -> None:
        _llm_entry(hass, **{CONF_COMMAND_ALLOWLIST_ENABLED: False})
        result = apply_command_policy(
            _command("lock.unlock", "lock.front"),
            [_ent("lock.front", "locked")],
            hass=hass,
        )
        assert result["intent"] == "command_approval"

    def test_tool_path_accepts_the_same_call(self, hass) -> None:
        """A validator still refusing what the JSON path accepts reports the
        capability as missing on the surface a tool-capable provider uses."""
        _llm_entry(hass, **{CONF_COMMAND_ALLOWLIST_ENABLED: False})
        hass.services.async_register("todo", "add_item", lambda call: None)
        validation = validate_command_action(
            "todo.add_item",
            ["todo.shopping_list"],
            {"item": "eggs"},
            known_entity_ids={"todo.shopping_list"},
            hass=hass,
        )
        assert validation["valid"] is True, validation["errors"]

    def test_tool_path_refuses_it_by_default(self, hass) -> None:
        hass.services.async_register("todo", "add_item", lambda call: None)
        validation = validate_command_action(
            "todo.add_item",
            ["todo.shopping_list"],
            {"item": "eggs"},
            known_entity_ids={"todo.shopping_list"},
            hass=hass,
        )
        assert validation["valid"] is False

    def test_tool_path_refuses_a_service_home_assistant_lacks(self, hass) -> None:
        """The validator exists so a small model can self-check. Reporting
        valid for a call ``execute_command`` then fails on makes it a liar."""
        _llm_entry(hass, **{CONF_COMMAND_ALLOWLIST_ENABLED: False})
        validation = validate_command_action(
            "todo.invent_item",
            ["todo.shopping_list"],
            {},
            known_entity_ids={"todo.shopping_list"},
            hass=hass,
        )
        assert validation["valid"] is False
        assert any("not a service Home Assistant has" in e for e in validation["errors"])


# ── What neither option switches off ────────────────────────────────────────


class TestStillRefusedWithBothOff:
    @pytest.mark.parametrize(
        "service",
        ["homeassistant.stop", "python_script.exec", "recorder.purge", "automation.reload"],
    )
    def test_denylisted_services(self, hass, service: str) -> None:
        """``_BLOCKED_SERVICES`` is a denylist, not the allowlist relaxed here."""
        _unattended(hass)
        domain = service.split(".", 1)[0]
        result = apply_command_policy(
            _command(service, f"{domain}.thing"),
            [_ent(f"{domain}.thing")],
            hass=hass,
        )
        assert result["intent"] == "answer"
        assert result["calls"] == []

    def test_denylisted_service_in_a_safe_domain_on_the_tool_path(self, hass) -> None:
        """``scene.reload`` is the only ``_BLOCKED_SERVICES`` entry whose
        DOMAIN is in the curated tables, so ``validate_command_action`` —
        which reached ``_classify_call`` only for a domain outside them —
        was refusing it by the per-domain VERB check. That reads as the same
        outcome and is not: the verb check is exactly what this opt-out
        removes, so relaxing the allowlist made a denylisted service
        executable through the tool path."""
        _unattended(hass)
        validation = validate_command_action(
            "scene.reload",
            ["scene.movie_night"],
            {},
            known_entity_ids={"scene.movie_night"},
            hass=hass,
        )
        assert validation["valid"] is False
        assert any("no-chat-execution list" in e for e in validation["errors"])

    def test_denylisted_services_on_the_tool_path(self, hass) -> None:
        _unattended(hass)
        for service in ("python_script.exec", "homeassistant.stop", "recorder.purge"):
            domain = service.split(".", 1)[0]
            validation = validate_command_action(
                service,
                [f"{domain}.thing"],
                {},
                known_entity_ids={f"{domain}.thing"},
                hass=hass,
            )
            assert validation["valid"] is False, service

    def test_review_target_existence_on_the_tool_path(self, hass) -> None:
        """With nobody left to read the card, the entity-existence guard the
        JSON path has is the only thing between a fabricated id and a turn
        that reports success — HA's entity services match nothing rather
        than raising, so the tool would answer ``executed: true``."""
        _unattended(hass)
        validation = validate_command_action(
            "lock.unlock",
            ["lock.nonexistent"],
            {},
            known_entity_ids={"lock.front"},
            hass=hass,
        )
        assert validation["valid"] is False
        assert any("isn't a device you have set up" in e for e in validation["errors"])

    def test_review_target_existence_accepts_a_real_entity(self, hass) -> None:
        _unattended(hass)
        validation = validate_command_action(
            "lock.unlock",
            ["lock.front"],
            {},
            known_entity_ids={"lock.front"},
            hass=hass,
        )
        assert validation["valid"] is True, validation["errors"]

    def test_remote_media_content_id(self, hass) -> None:
        """An SSRF guard, not the allowlist."""
        _unattended(hass)
        result = apply_command_policy(
            _command(
                "media_player.play_media",
                "media_player.den",
                {
                    "media_content_id": "http://attacker.example/x.mp3",
                    "media_content_type": "music",
                },
            ),
            [_ent("media_player.den")],
            hass=hass,
        )
        assert result["intent"] == "answer"
        assert result["calls"] == []

    def test_too_many_calls(self, hass) -> None:
        _unattended(hass)
        proposal: dict[str, Any] = {
            "intent": "command",
            "response": "Done.",
            "calls": [
                {
                    "service": "light.turn_on",
                    "target": {"entity_id": [f"light.l{i}"]},
                    "data": {},
                }
                for i in range(6)
            ],
        }
        result = apply_command_policy(proposal, [_ent(f"light.l{i}") for i in range(6)], hass=hass)
        assert result["intent"] == "answer"
        assert "too many actions" in result["validation_error"]


# ── Both together: the configuration the harness sets ───────────────────────


class TestUnattendedMode:
    def test_lock_and_todo_both_execute(self, hass) -> None:
        _unattended(hass)
        hass.services.async_register("todo", "add_item", lambda call: None)

        lock = apply_command_policy(
            _command("lock.unlock", "lock.front"),
            [_ent("lock.front", "locked")],
            hass=hass,
        )
        assert lock["intent"] == "command"

        todo = apply_command_policy(
            _command("todo.add_item", "todo.shopping_list", {"item": "eggs"}),
            [_ent("todo.shopping_list")],
            hass=hass,
        )
        assert todo["intent"] == "command"

    def test_data_is_read_from_entry_data_too(self, hass) -> None:
        """``data`` is merged under ``options`` so a harness may set either."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            entry_id="test_llm_entry",
            data={
                CONF_ENTRY_TYPE: ENTRY_TYPE_LLM,
                CONF_COMMAND_APPROVAL_REQUIRED: False,
                CONF_COMMAND_ALLOWLIST_ENABLED: False,
            },
        )
        entry.add_to_hass(hass)
        policy = resolve_command_policy_options(hass)
        assert policy.approval_required is False
        assert policy.allowlist_enabled is False

    def test_options_win_over_data(self, hass) -> None:
        entry = MockConfigEntry(
            domain=DOMAIN,
            entry_id="test_llm_entry",
            data={CONF_ENTRY_TYPE: ENTRY_TYPE_LLM, CONF_COMMAND_APPROVAL_REQUIRED: False},
            options={CONF_COMMAND_APPROVAL_REQUIRED: True},
        )
        entry.add_to_hass(hass)
        assert resolve_command_policy_options(hass).approval_required is True

    def test_read_live_without_a_reload(self, hass) -> None:
        """The toggle is read per call, as telemetry's is — no reload to forget."""
        entry = _llm_entry(hass)
        assert resolve_command_policy_options(hass).approval_required is True
        hass.config_entries.async_update_entry(
            entry, options={CONF_COMMAND_APPROVAL_REQUIRED: False}
        )
        assert resolve_command_policy_options(hass).approval_required is False


# ── Targetless is for UNCURATED services only ───────────────────────────────


class TestTargetlessScope:
    """A curated domain is an ENTITY domain: HA acts on every entity in it
    when no target is supplied. Letting the allowlist opt-out accept a
    targetless call there opens every cover in the house — and an empty
    target list walks past ``_entity_aware_review_entry`` too, so the garage
    door goes up with no card on an install that opted out of the ALLOWLIST
    and left approval gating switched ON. That is not the opt-out being
    honoured, it is an escalation past a gate nobody touched.
    """

    def test_targetless_curated_service_is_refused(self, hass) -> None:
        _unattended(hass)
        result = apply_command_policy(
            {
                "intent": "command",
                "response": "Turning off.",
                "calls": [{"service": "light.turn_off", "target": {}, "data": {}}],
            },
            [_ent("light.kitchen")],
            hass=hass,
        )
        assert result["intent"] == "answer"
        assert result["calls"] == []

    def test_targetless_cover_cannot_skip_the_garage_card(self, hass) -> None:
        """Approval gating is left ENABLED here — only the allowlist is off."""
        _llm_entry(hass, **{CONF_COMMAND_ALLOWLIST_ENABLED: False})
        hass.states.async_set("cover.garage", "closed", {"device_class": "garage"})
        result = apply_command_policy(
            {
                "intent": "command",
                "response": "Opening.",
                "calls": [{"service": "cover.open_cover", "target": {}, "data": {}}],
            },
            [_ent("cover.garage", "closed", device_class="garage")],
            hass=hass,
        )
        assert result["intent"] != "command"
        assert not result.get("calls")

    def test_targetless_uncurated_service_still_passes(self, hass) -> None:
        _llm_entry(hass, **{CONF_COMMAND_ALLOWLIST_ENABLED: False})
        hass.services.async_register("todo", "add_item", lambda call: None)
        result = apply_command_policy(
            {
                "intent": "command",
                "response": "Added.",
                "calls": [{"service": "todo.add_item", "target": {}, "data": {"item": "eggs"}}],
            },
            [_ent("todo.shopping_list")],
            hass=hass,
        )
        assert result["intent"] == "command"
        assert result["calls"][0]["service"] == "todo.add_item"

    def test_tool_path_refuses_a_targetless_curated_service(self, hass) -> None:
        _unattended(hass)
        validation = validate_command_action(
            "light.turn_off", [], {}, known_entity_ids={"light.kitchen"}, hass=hass
        )
        assert validation["valid"] is False
        assert any("at least one entity_id" in e for e in validation["errors"])

    def test_tool_path_allows_a_targetless_uncurated_service(self, hass) -> None:
        _unattended(hass)
        hass.services.async_register("todo", "add_item", lambda call: None)
        validation = validate_command_action(
            "todo.add_item", [], {"item": "eggs"}, known_entity_ids=set(), hass=hass
        )
        assert validation["valid"] is True, validation["errors"]


# ── The override is read off the LIVE provider entry ────────────────────────


class TestEntryResolution:
    """A second "Add entry" that never linked a provider is a supported state
    and can sit AHEAD of the configured one. Taking the first match reads a
    stray entry's options instead of the live provider's.
    """

    def test_stray_entry_ahead_of_the_configured_one(self, hass) -> None:
        from custom_components.selora_ai.const import CONF_LLM_PROVIDER

        stray = MockConfigEntry(
            domain=DOMAIN, entry_id="stray", data={CONF_ENTRY_TYPE: ENTRY_TYPE_LLM}, options={}
        )
        stray.add_to_hass(hass)
        live = MockConfigEntry(
            domain=DOMAIN,
            entry_id="live",
            data={CONF_ENTRY_TYPE: ENTRY_TYPE_LLM, CONF_LLM_PROVIDER: "anthropic"},
            options={CONF_COMMAND_APPROVAL_REQUIRED: False},
        )
        live.add_to_hass(hass)
        assert resolve_command_policy_options(hass).approval_required is False

    def test_stray_entry_cannot_impose_an_override(self, hass) -> None:
        """The mirror image: a stray entry must not turn the gate OFF for a
        configured provider that never asked."""
        from custom_components.selora_ai.const import CONF_LLM_PROVIDER

        stray = MockConfigEntry(
            domain=DOMAIN,
            entry_id="stray",
            data={CONF_ENTRY_TYPE: ENTRY_TYPE_LLM},
            options={CONF_COMMAND_APPROVAL_REQUIRED: False},
        )
        stray.add_to_hass(hass)
        live = MockConfigEntry(
            domain=DOMAIN,
            entry_id="live",
            data={CONF_ENTRY_TYPE: ENTRY_TYPE_LLM, CONF_LLM_PROVIDER: "anthropic"},
            options={},
        )
        live.add_to_hass(hass)
        assert resolve_command_policy_options(hass).approval_required is True


# ── Approval-time revalidation agrees with the two paths above ──────────────


class TestApprovalResolution:
    """``_validate_safe_call`` re-applies the policy when a card is tapped.
    A turn mixing an unlisted call with a REVIEW one holds BOTH back, so a
    disagreement here silently drops the unlisted half after the user has
    approved the pair.
    """

    @staticmethod
    def _validate(call: dict[str, Any], known: set[str], hass) -> tuple[Any, Any]:
        from custom_components.selora_ai.command_policy_options import (
            resolve_command_policy_options,
        )
        from custom_components.selora_ai.llm_client.command_policy import _validate_safe_call

        return _validate_safe_call(call, known, policy=resolve_command_policy_options(hass))

    def test_targetless_call_survives_resolution(self, hass) -> None:
        _llm_entry(hass, **{CONF_COMMAND_ALLOWLIST_ENABLED: False})
        validated, err = self._validate(
            {"service": "todo.add_item", "data": {"item": "eggs"}}, set(), hass
        )
        assert err is None, err
        assert validated is not None
        assert validated["service"] == "todo.add_item"

    def test_targetless_call_is_refused_by_default(self, hass) -> None:
        validated, err = self._validate(
            {"service": "todo.add_item", "data": {"item": "eggs"}}, set(), hass
        )
        assert validated is None
        assert err is not None

    def test_unlisted_service_survives_resolution(self, hass) -> None:
        _llm_entry(hass, **{CONF_COMMAND_ALLOWLIST_ENABLED: False})
        validated, err = self._validate(
            {
                "service": "todo.add_item",
                "target": {"entity_id": ["todo.shopping_list"]},
                "data": {"item": "eggs"},
            },
            {"todo.shopping_list"},
            hass,
        )
        assert err is None, err
        assert validated is not None

    def test_targetless_curated_service_is_refused_at_resolution(self, hass) -> None:
        _llm_entry(hass, **{CONF_COMMAND_ALLOWLIST_ENABLED: False})
        validated, err = self._validate({"service": "light.turn_off"}, {"light.kitchen"}, hass)
        assert validated is None
        assert err is not None

    def test_malformed_target_is_still_refused(self, hass) -> None:
        """A missing ``entity_id`` is targetless; a non-list, non-string one
        is malformed, and stays refused on either setting."""
        _llm_entry(hass, **{CONF_COMMAND_ALLOWLIST_ENABLED: False})
        validated, err = self._validate(
            {"service": "todo.add_item", "target": {"entity_id": 42}}, set(), hass
        )
        assert validated is None
        assert err is not None


# ── The entity snapshot is the other half of the same restriction ───────────


class TestSnapshotWidening:
    """``todo`` is absent from COLLECTOR_DOMAINS for the same reason
    ``todo.add_item`` is absent from the service tables. Relaxing only the
    tables leaves the opt-out inert on those calls: the service is
    permitted against an entity the model was never shown, and the policy
    refuses it one check later as an unknown entity_id.
    """

    @staticmethod
    def _ids(hass) -> set[str]:
        from custom_components.selora_ai import _collect_entity_states

        return {e["entity_id"] for e in _collect_entity_states(hass)}

    def test_unlisted_domain_hidden_by_default(self, hass) -> None:
        hass.states.async_set("todo.shopping_list", "0", {"friendly_name": "Shopping List"})
        hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen"})
        ids = self._ids(hass)
        assert "light.kitchen" in ids
        assert "todo.shopping_list" not in ids

    def test_unlisted_domain_visible_under_the_opt_out(self, hass) -> None:
        _llm_entry(hass, **{CONF_COMMAND_ALLOWLIST_ENABLED: False})
        hass.states.async_set("todo.shopping_list", "0", {"friendly_name": "Shopping List"})
        hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen"})
        ids = self._ids(hass)
        assert "light.kitchen" in ids
        assert "todo.shopping_list" in ids

    def test_unavailable_entities_are_still_dropped(self, hass) -> None:
        """Widening the domain filter is not widening the state filter."""
        _llm_entry(hass, **{CONF_COMMAND_ALLOWLIST_ENABLED: False})
        hass.states.async_set("todo.broken", "unavailable", {})
        assert "todo.broken" not in self._ids(hass)


# ── The prompt must not contradict the policy behind it ─────────────────────


class TestPromptRule:
    """A prompt still saying "only these low-risk domains" over a policy that
    has stopped refusing anything else makes a cloud model decline calls that
    would now execute — so the opt-out reads as not having taken effect.
    """

    def test_default_names_the_low_risk_domains(self) -> None:
        from custom_components.selora_ai.llm_client.prompts import (
            build_architect_stream_system_prompt,
            build_architect_system_prompt,
        )

        for prompt in (build_architect_system_prompt(), build_architect_stream_system_prompt()):
            assert "only use these low-risk domains" in prompt

    def test_opt_out_drops_the_restriction(self) -> None:
        from custom_components.selora_ai.llm_client.prompts import (
            build_architect_stream_system_prompt,
            build_architect_system_prompt,
        )

        for prompt in (
            build_architect_system_prompt(unrestricted_commands=True),
            build_architect_stream_system_prompt(unrestricted_commands=True),
        ):
            assert "only use these low-risk domains" not in prompt
            assert "any Home Assistant service this install actually has" in prompt

    def test_the_rules_after_it_survive(self) -> None:
        """The replaced line was implicitly concatenated with the literal that
        follows it, so a call in its place silently drops the rest of RULES
        unless the ``+`` chain is restored."""
        from custom_components.selora_ai.llm_client.prompts import (
            build_architect_stream_system_prompt,
            build_architect_system_prompt,
        )

        for unrestricted in (False, True):
            json_prompt = build_architect_system_prompt(unrestricted_commands=unrestricted)
            assert 'you MUST include a non-empty "calls" array' in json_prompt
            stream_prompt = build_architect_stream_system_prompt(unrestricted_commands=unrestricted)
            assert "you MUST return a JSON object" in stream_prompt


# ── Unreachable from the settings UI, by allowlist ──────────────────────────


class TestNotUserSettable:
    """A homeowner flipping these from a UI would be removing the reason an
    unattended unlock asks first. The panel's save handler already refuses
    any key outside ``allowed_option_keys`` — its own comment names "a
    future safety-gating option that isn't meant to be user-settable" as
    the case it guards. This pins that these two are that case, so adding
    them there later is a failing test rather than a quiet regression.
    """

    def test_save_settings_does_not_accept_them(self) -> None:
        import inspect

        from custom_components.selora_ai.websocket import linking

        src = inspect.getsource(linking)
        allowlist = src.split("allowed_option_keys = {", 1)[1].split("}", 1)[0]
        assert "CONF_COMMAND_APPROVAL_REQUIRED" not in allowlist
        assert "CONF_COMMAND_ALLOWLIST_ENABLED" not in allowlist
        assert "command_approval_required" not in allowlist
        assert "command_allowlist_enabled" not in allowlist

    def test_not_in_strings_or_translations(self) -> None:
        """No UI string means no options-flow field and no settings row."""
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "custom_components" / "selora_ai"
        targets = [root / "strings.json", *sorted((root / "translations").glob("*.json"))]
        for path in targets:
            blob = json.dumps(json.loads(path.read_text(encoding="utf-8")))
            assert "command_approval_required" not in blob, path.name
            assert "command_allowlist_enabled" not in blob, path.name
