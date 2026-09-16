"""A refusal must name the cause it actually had.

Every rejection in ``apply_command_policy`` funnels through
``_blocked_command_result``, and most of them are not the allowlist: a
malformed target, a denylisted service, an entity the home does not have,
a service Home Assistant itself does not define. Appending the
allowed-domain list to all of them names the wrong cause more often than
the right one.

The case that cost a debugging session: the released 1.7B invented
``valve.close`` (Home Assistant has ``valve.close_valve``) and was
refused with a true first sentence followed by a domain list that omits
``valve`` — on a benchmark run with ``command_allowlist_enabled: false``,
where no such policy was running at all. It reads as "valve is blocked by
policy". It was not.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.selora_ai.const import (
    CONF_COMMAND_ALLOWLIST_ENABLED,
    CONF_ENTRY_TYPE,
    CONF_LLM_PROVIDER,
    DOMAIN,
    ENTRY_TYPE_LLM,
)
from custom_components.selora_ai.llm_client.command_policy import (
    _SAFE_COMMAND_DOMAINS,
    apply_command_policy,
)

_HINT = "Immediate commands are currently limited to"


def _llm_entry(hass, **options: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="test_llm_entry",
        data={CONF_ENTRY_TYPE: ENTRY_TYPE_LLM, CONF_LLM_PROVIDER: "selora_local"},
        options=options,
    )
    entry.add_to_hass(hass)
    return entry


def _ent(entity_id: str, state: str = "on") -> dict[str, Any]:
    return {"entity_id": entity_id, "state": state, "attributes": {}}


def _command(service: str, entity_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "intent": "command",
        "response": "Done.",
        "calls": [{"service": service, "target": {"entity_id": [entity_id]}, "data": data or {}}],
    }


# ── The reported case ───────────────────────────────────────────────────────


class TestInventedServiceName:
    """``valve.close`` is a service the model made up. Refusing is right; the
    allowlist is not why."""

    @pytest.mark.parametrize("service", ["valve.close", "valve.open", "valve.set_percentage"])
    def test_no_allowlist_hint_with_the_allowlist_off(self, hass, service: str) -> None:
        _llm_entry(hass, **{CONF_COMMAND_ALLOWLIST_ENABLED: False})
        result = apply_command_policy(
            _command(service, "valve.garden"), [_ent("valve.garden", "open")], hass=hass
        )
        assert result["intent"] == "answer"
        assert "is not a service Home Assistant has" in result["response"]
        assert _HINT not in result["response"], (
            "the domain list describes a bound that is not in force on this run, "
            "and omits valve — it reads as 'valve is blocked by policy'"
        )
        assert "valve" not in result["response"].split("because")[0]

    def test_the_reason_still_reaches_the_panel(self, hass) -> None:
        """``validation_error`` is what the panel reads; it is unchanged."""
        _llm_entry(hass, **{CONF_COMMAND_ALLOWLIST_ENABLED: False})
        result = apply_command_policy(
            _command("valve.close", "valve.garden"), [_ent("valve.garden", "open")], hass=hass
        )
        assert result["validation_error"] == "`valve.close` is not a service Home Assistant has"


# ── Reasons that are not the allowlist keep quiet about it ──────────────────


class TestNonAllowlistReasons:
    @pytest.mark.parametrize(
        ("call", "entities", "fragment"),
        [
            (
                {"service": "python_script.exec", "target": {"entity_id": ["python_script.x"]}},
                [_ent("python_script.x")],
                "no-chat-execution list",
            ),
            (
                {"service": "light.turn_on", "target": {"entity_id": ["light.nope"]}},
                [_ent("light.kitchen")],
                "unknown entity_id",
            ),
            (
                {"service": "light.turn_on", "target": "not-a-dict"},
                [_ent("light.kitchen")],
                "invalid target payload",
            ),
            (
                {"service": "light.turn_on", "target": {"entity_id": 42}},
                [_ent("light.kitchen")],
                "did not target explicit entity_ids",
            ),
            (
                {"service": "light.turn_on", "target": {"entity_id": ["light.kitchen"]}, "data": 7},
                [_ent("light.kitchen")],
                "invalid data payload",
            ),
        ],
    )
    def test_hint_is_absent(
        self, hass, call: dict[str, Any], entities: list[dict[str, Any]], fragment: str
    ) -> None:
        result = apply_command_policy(
            {"intent": "command", "response": "Done.", "calls": [call]}, entities, hass=hass
        )
        assert result["intent"] == "answer"
        assert fragment in result["response"]
        assert _HINT not in result["response"], f"{fragment!r} is not an allowlist rejection"

    def test_too_many_actions_is_a_cap_not_the_allowlist(self, hass) -> None:
        result = apply_command_policy(
            {
                "intent": "command",
                "response": "Done.",
                "calls": [
                    {"service": "light.turn_on", "target": {"entity_id": [f"light.l{i}"]}}
                    for i in range(6)
                ],
            },
            [_ent(f"light.l{i}") for i in range(6)],
            hass=hass,
        )
        assert "too many actions" in result["response"]
        assert _HINT not in result["response"]


# ── Where the allowlist IS the reason, the hint stays ───────────────────────


class TestAllowlistReasons:
    def test_unlisted_domain_says_allowlist_and_carries_the_hint(self, hass) -> None:
        result = apply_command_policy(
            _command("todo.add_item", "todo.list", {"item": "eggs"}),
            [_ent("todo.list")],
            hass=hass,
        )
        assert result["intent"] == "answer"
        assert (
            "the todo domain is outside the current safe command allowlist" in (result["response"])
        )
        assert _HINT in result["response"]

    def test_a_denylisted_service_is_not_called_unlisted(self, hass) -> None:
        """``_classify_call`` returns BLOCKED for two unrelated findings — on
        the denylist, and in no curated table. Reporting both as the denylist
        told a caller whose ``todo.add_item`` was merely unlisted that it sits
        on a no-chat-execution list, which is false and sends them looking at
        a list it is not on. Reporting both as unlisted would be the same
        error mirrored, so the split is asserted from both sides."""
        result = apply_command_policy(
            _command("python_script.exec", "python_script.x"),
            [_ent("python_script.x")],
            hass=hass,
        )
        assert "no-chat-execution list" in result["response"]
        assert "outside the current safe command allowlist" not in result["response"]
        # A denylist is not the allowlist, so the domain list explains nothing.
        assert _HINT not in result["response"]

    def test_unknown_verb_in_a_curated_domain(self, hass) -> None:
        result = apply_command_policy(
            _command("media_player.select_source", "media_player.den", {"source": "HDMI"}),
            [_ent("media_player.den")],
            hass=hass,
        )
        assert "is not a valid media_player service" in result["response"]
        assert _HINT in result["response"]
        assert _SAFE_COMMAND_DOMAINS in result["response"]

    def test_unsupported_parameter(self, hass) -> None:
        result = apply_command_policy(
            _command("light.turn_on", "light.kitchen", {"flash": "short"}),
            [_ent("light.kitchen")],
            hass=hass,
        )
        assert "unsupported parameters" in result["response"]
        assert _HINT in result["response"]

    def test_cross_domain_target(self, hass) -> None:
        result = apply_command_policy(
            _command("light.turn_on", "switch.plug"),
            [_ent("switch.plug"), _ent("light.kitchen")],
            hass=hass,
        )
        assert "outside the light domain" in result["response"]
        assert _HINT in result["response"]

    def test_hint_is_dropped_when_the_allowlist_is_off(self, hass) -> None:
        """The list is built from ``_ALLOWED_COMMAND_SERVICES`` at import, so
        it describes a bound nothing applies once the allowlist is opted out
        of.

        The case has to be a rejection that is still LIVE with the flag off.
        An unsupported-parameter call is not: that check is itself gated on
        the flag, so the command succeeds and an "and no hint" assertion
        passes without any refusal having happened. A curated service with an
        empty target list still refuses — ``unlisted_targetless`` covers only
        domains outside the tables — so the hint argument is genuinely
        evaluated here.
        """
        _llm_entry(hass, **{CONF_COMMAND_ALLOWLIST_ENABLED: False})
        result = apply_command_policy(
            {
                "intent": "command",
                "response": "Done.",
                "calls": [{"service": "light.turn_on", "target": {"entity_id": []}, "data": {}}],
            },
            [_ent("light.kitchen")],
            hass=hass,
        )
        assert result["intent"] == "answer"
        assert result["calls"] == []
        assert result["validation_error"] == "light.turn_on did not include any target entities"
        assert _HINT not in result["response"]

    def test_the_same_rejection_carries_the_hint_with_the_allowlist_on(self, hass) -> None:
        """The other half of the pair — without it the test above cannot tell
        "the flag dropped the hint" from "this site never had one"."""
        result = apply_command_policy(
            {
                "intent": "command",
                "response": "Done.",
                "calls": [{"service": "light.turn_on", "target": {"entity_id": []}, "data": {}}],
            },
            [_ent("light.kitchen")],
            hass=hass,
        )
        assert result["validation_error"] == "light.turn_on did not include any target entities"
        assert _HINT in result["response"]


# ── The rule itself ─────────────────────────────────────────────────────────


class TestHintIsOptIn:
    def test_default_is_off(self) -> None:
        """A site added later that forgets the flag gives a SHORTER
        explanation. One that inherited it by default would give a WRONG
        one, and the wrong one is what sends someone chasing a policy
        problem that does not exist."""
        from custom_components.selora_ai.llm_client.command_policy import (
            _blocked_command_result,
        )

        out = _blocked_command_result("the sky is the wrong colour")
        assert out["response"] == (
            "I couldn't safely execute that request because the sky is the wrong colour."
        )
        assert _HINT not in out["response"]

    def test_opt_in_appends_the_list(self) -> None:
        from custom_components.selora_ai.llm_client.command_policy import (
            _blocked_command_result,
        )

        out = _blocked_command_result("reasons", allowlist_hint=True)
        assert _HINT in out["response"]
        assert _SAFE_COMMAND_DOMAINS in out["response"]
