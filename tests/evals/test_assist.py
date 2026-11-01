"""Does a real model keep tile markers out of Assist?

Assist speaks the reply and shows it as plain text, so its prompt forbids
`[[entity:…]]` markers; the tool policy is appended to the same prompt and
must not ask for them. Checked on the model's own reply, before
`_unwrap_entity_markers` rewrites any leak into names.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
import pytest

from custom_components.selora_ai import _collect_entity_states

from ..chat_harness import ChatHarness

pytestmark = pytest.mark.eval


@pytest.mark.parametrize(
    "message",
    ["Which lights do I have?", "Give me an overview of what's on and off in my home"],
)
async def test_assist_answers_name_devices_without_markers(
    hass: HomeAssistant, home: ChatHarness, message: str, run: int
) -> None:
    result = await home.llm.architect_chat(
        message, _collect_entity_states(hass), for_assist=True, language="en"
    )

    response = str(result.get("response") or "")
    assert "error" not in result, result
    assert "[[" not in response, response
    assert "Kitchen" in response, response
