"""The bundled command prompt must describe what the integration can actually do.

Each of these pins one claim the prompt makes against the code that has to
honour it, so a prompt and the parser or the safety policy cannot drift
apart silently — which is the failure mode that costs a whole turn: the
model does what it was told and the integration discards the result.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import pytest

from custom_components.selora_ai.llm_client.command_policy import (
    _ALLOWED_COMMAND_SERVICES,
    _classify_call,
    _remote_media_content_error,
    apply_command_policy,
    approval_pending_hint,
)
from custom_components.selora_ai.providers.selora_local import (
    _SELORA_LOCAL_PROMPT_FILENAMES,
    _SELORA_LOCAL_PROMPTS_DIR,
)

# A byte copy of the prompt the command specialist was fine-tuned on. The
# model goes out of distribution on small wording changes, so the bundled
# prompt is not free text — it is a build artefact that has to match this.
_TRAINED_COMMAND_PROMPT = (
    Path(__file__).parent / "fixtures" / "trained_command_system_prompt.txt"
).read_text(encoding="utf-8")

# The prompt spells the service format as "domain.action". It is a
# placeholder for the shape, not a service anyone can call, so the
# executability sweep below skips it.
_SERVICE_FORMAT_PLACEHOLDER = "domain.action"


def _bundled_command_prompt() -> str:
    return (_SELORA_LOCAL_PROMPTS_DIR / _SELORA_LOCAL_PROMPT_FILENAMES["command"]).read_text(
        encoding="utf-8"
    )


class TestCommandPrompt:
    def test_bundled_prompt_matches_the_prompt_the_model_was_trained_on(self) -> None:
        """Byte parity with the training prompt, not merely similar wording.

        The model goes out of distribution on small format differences,
        so editing this prompt without retraining degrades every command
        turn — invisibly, because the damage does not show up in tests
        that only check for keywords. This assertion also subsumes every
        negative claim about the wording: text identical to the training
        prompt cannot have grown a new instruction.
        """
        assert _bundled_command_prompt() == _TRAINED_COMMAND_PROMPT

    def test_every_service_the_prompt_names_is_executable(self) -> None:
        """A service named in the prompt must survive the safety policy.

        The model treats the prompt's examples as the vocabulary it is
        allowed to use. Naming a service the policy then rejects costs
        the whole turn, not just the one call, because the rejection
        path returns rather than dropping the offending call.
        """
        services = re.findall(r'"([a-z_]+\.[a-z_]+)"', _bundled_command_prompt())
        assert services, "prompt names no services — the regex or the prompt changed"
        for service in services:
            if service == _SERVICE_FORMAT_PLACEHOLDER:
                continue
            bucket, _entry = _classify_call(service)
            assert bucket != "blocked", f"prompt names {service}, policy blocks it"
            domain, verb = service.split(".", 1)
            if bucket == "safe":
                # _classify_call buckets by domain, so it rates any verb
                # in a safe domain as "safe". The per-verb table is what
                # actually decides, and it is what drifts.
                assert verb in _ALLOWED_COMMAND_SERVICES[domain], (
                    f"prompt names {service}, policy has no such verb"
                )

    def test_an_approval_card_never_carries_the_models_confirmation(self) -> None:
        """The card replaces the model's `r`, which the prompt made past-tense.

        The prompt mandates a past-tense confirmation ("Front door
        locked."), so `r` is never empty and a "replace it only when
        blank" rule never fires. Letting it stand on a pending approval
        card tells the user the door is locked while nothing has run.
        """
        result = apply_command_policy(
            {
                "intent": "command",
                "calls": [{"service": "lock.lock", "target": {"entity_id": "lock.front_door"}}],
                "response": "Front door locked.",
            },
            [{"entity_id": "lock.front_door", "state": "unlocked"}],
        )
        assert result["intent"] == "command_approval"
        assert result["calls"] == []
        assert result["response"] == approval_pending_hint(None)

    def test_approval_gating_is_per_verb_not_per_domain(self) -> None:
        """Only some verbs of an approval-gated domain are gated.

        `lock.lock` and `lock.unlock` reach the user as a confirmation
        card and then run. Sibling verbs of the same domain are BLOCKED
        outright, and a blocked call takes every other call in the turn
        down with it — so "the lock domain is approval-gated" is not a
        safe shorthand for what the policy does.
        """
        for service in ("lock.lock", "lock.unlock", "notify.notify", "vacuum.start"):
            verdict, _entry = _classify_call(service)
            assert verdict == "review", f"{service} is {verdict}, not approval-gated"
        for service in (
            "lock.turn_on",
            "lock.open_door",
            "alarm_control_panel.alarm_trigger",
            "script.reload",
            "vacuum.send_command",
        ):
            verdict, _entry = _classify_call(service)
            assert verdict == "blocked", f"{service} is {verdict}, not blocked"


class TestPlayMediaRequiredData:
    """``media_player.play_media`` is only executable with both fields.

    Home Assistant requires ``media_content_id`` AND
    ``media_content_type``; the prompt that names the service leaves
    ``d`` optional. Accepting a call without them dispatches something
    the service layer rejects while the user is told the command ran.
    """

    @pytest.mark.parametrize(
        "data",
        [
            None,
            {},
            {"media_content_id": "media-source://media_source/local/a.mp3"},
            {"media_content_type": "music"},
            {"media_content_id": "   ", "media_content_type": "music"},
            {"media_content_id": 7, "media_content_type": "music"},
        ],
    )
    def test_incomplete_play_media_is_refused(self, data: Any) -> None:
        assert _remote_media_content_error("media_player.play_media", data) is not None

    def test_a_complete_local_play_media_is_allowed(self) -> None:
        assert (
            _remote_media_content_error(
                "media_player.play_media",
                {
                    "media_content_id": "media-source://media_source/local/a.mp3",
                    "media_content_type": "music",
                },
            )
            is None
        )

    def test_other_services_are_untouched(self) -> None:
        """Only play_media takes an address; the rest take settings."""
        assert _remote_media_content_error("light.turn_on", None) is None
