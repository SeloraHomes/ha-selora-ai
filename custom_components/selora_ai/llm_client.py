"""LLM client — unified interface for Anthropic API and local Ollama.

Both backends use the Anthropic /v1/messages format, so one client handles
both. The only differences are host, auth headers, model name, and health check.

Backends:
  1. Anthropic API (Claude) — cloud, needs API key
  2. Ollama — local, Anthropic-compatible endpoint, no key needed
     https://docs.ollama.com/api/anthropic-compatibility
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    ANTHROPIC_API_VERSION,
    DEFAULT_ANTHROPIC_HOST,
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_LLM_TIMEOUT,
    DEFAULT_MAX_SUGGESTIONS,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_RECORDER_LOOKBACK_DAYS,
    LLM_PROVIDER_ANTHROPIC,
    LLM_PROVIDER_OLLAMA,
    MESSAGES_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)


class LLMClient:
    """Unified client for Anthropic API and local Ollama."""

    def __init__(
        self,
        hass: HomeAssistant,
        provider: str = LLM_PROVIDER_ANTHROPIC,
        *,
        api_key: str = "",
        model: str = "",
        host: str = "",
        max_suggestions: int = DEFAULT_MAX_SUGGESTIONS,
        lookback_days: int = DEFAULT_RECORDER_LOOKBACK_DAYS,
    ) -> None:
        self._hass = hass
        self._provider = provider
        self._max_suggestions = max_suggestions
        self._lookback_days = lookback_days

        if provider == LLM_PROVIDER_ANTHROPIC:
            self._host = DEFAULT_ANTHROPIC_HOST
            self._model = model or DEFAULT_ANTHROPIC_MODEL
            self._api_key = api_key
        else:
            self._host = (host or DEFAULT_OLLAMA_HOST).rstrip("/")
            self._model = model or DEFAULT_OLLAMA_MODEL
            self._api_key = ""

    def _get_headers(self) -> dict[str, str]:
        """Build per-request headers."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._provider == LLM_PROVIDER_ANTHROPIC and self._api_key:
            headers["x-api-key"] = self._api_key
            headers["anthropic-version"] = ANTHROPIC_API_VERSION
        return headers

    @property
    def _endpoint(self) -> str:
        return f"{self._host}{MESSAGES_ENDPOINT}"

    @property
    def provider_name(self) -> str:
        if self._provider == LLM_PROVIDER_ANTHROPIC:
            return f"Anthropic ({self._model})"
        return f"Ollama ({self._model})"

    async def analyze_home_data(
        self, home_snapshot: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Send collected HA data to LLM for automation analysis."""
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_analysis_prompt(home_snapshot)

        try:
            session = async_get_clientsession(self._hass)
            async with session.post(
                self._endpoint,
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_LLM_TIMEOUT),
                json={
                    "model": self._model,
                    "max_tokens": 4096,
                    "messages": [
                        {"role": "user", "content": user_prompt},
                    ],
                    "system": system_prompt,
                },
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    _LOGGER.warning(
                        "%s returned %s: %s", self.provider_name, resp.status, body[:200]
                    )
                    return []

                data = await resp.json()
                return self._parse_suggestions(data)

        except Exception:
            _LOGGER.exception("Failed to get analysis from %s", self.provider_name)
            return []

    def _build_system_prompt(self) -> str:
        """System prompt — defines Selora AI's persona and output format."""
        return (
            "You are Selora AI, a Home Assistant automation expert. "
            "Given a summary of a user's smart home, you suggest useful automations.\n\n"
            "RULES:\n"
            f"1. Suggest up to {self._max_suggestions} practical automations. Quality over quantity.\n"
            "2. Only use entity_ids from the provided data.\n"
            "3. Do NOT echo back the input data.\n"
            "4. Each suggestion MUST have these keys: alias, description, triggers, actions.\n"
            "   Use PLURAL key names: 'triggers' (not 'trigger'), 'actions' (not 'action'), "
            "'conditions' (not 'condition'). This matches HA 2024+ automation schema.\n"
            "5. Use valid Home Assistant automation YAML schema (as JSON).\n"
            "6. For actions, use 'action' key (not 'service') for the service call, e.g. "
            '"notify.notify". Include \'data\' for parameters.\n\n'
            "EXAMPLE OUTPUT:\n"
            '[\n'
            '  {\n'
            '    "alias": "Notify when sun sets",\n'
            '    "description": "Send a notification at sunset each day",\n'
            '    "triggers": [{"trigger": "sun", "event": "sunset"}],\n'
            '    "actions": [{"action": "notify.notify", "data": {"message": "Sun has set"}}]\n'
            '  }\n'
            ']\n\n'
            "Respond with ONLY the JSON array. No markdown fences. No explanation."
        )

    def _build_analysis_prompt(self, snapshot: dict[str, Any]) -> str:
        """Build a summarized prompt — avoid overwhelming the model with raw data."""
        devices = snapshot.get("devices", [])
        device_lines = []
        for d in devices:
            name = d.get("name", "Unknown")
            mfr = d.get("manufacturer") or "unknown"
            model = d.get("model") or ""
            device_lines.append(f"  - {name} ({mfr} {model})".strip())

        entities = snapshot.get("entity_states", [])
        entity_lines = []
        for e in entities:
            eid = e.get("entity_id", "")
            state = e.get("state", "unknown")
            entity_lines.append(f"  - {eid}: {state}")

        automations = snapshot.get("automations", [])
        if automations:
            auto_lines = [f"  - {a.get('alias', a.get('entity_id', 'unknown'))}" for a in automations]
            auto_section = "EXISTING AUTOMATIONS (do not duplicate):\n" + "\n".join(auto_lines)
        else:
            auto_section = "EXISTING AUTOMATIONS: None yet."

        history = snapshot.get("recorder_history", [])
        history_counts: dict[str, int] = {}
        for h in history:
            eid = h.get("entity_id", "")
            history_counts[eid] = history_counts.get(eid, 0) + 1
        history_lines = [
            f"  - {eid}: {count} state changes"
            for eid, count in sorted(history_counts.items(), key=lambda x: -x[1])
        ]

        prompt = (
            "Here is a summary of my Home Assistant setup. "
            "Suggest useful automations I should create.\n\n"
            f"DEVICES ({len(devices)}):\n" + "\n".join(device_lines or ["  None"]) + "\n\n"
            f"ENTITIES ({len(entities)}):\n" + "\n".join(entity_lines or ["  None"]) + "\n\n"
            f"{auto_section}\n\n"
            f"RECENT ACTIVITY (last {self._lookback_days} days):\n" + "\n".join(history_lines or ["  No history"]) + "\n\n"
            f"Based on this data, suggest up to {self._max_suggestions} practical Home Assistant automations as a JSON array."
        )
        return prompt

    def _parse_suggestions(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse the Anthropic-format response into automation configs."""
        try:
            content = response.get("content", [])
            text = ""
            for block in content:
                if block.get("type") == "text":
                    text += block.get("text", "")

            _LOGGER.debug("Raw %s response: %s", self._provider, text[:500])

            # Strip markdown fences if present
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

            # Find JSON array in response
            start = text.find("[")
            end = text.rfind("]")
            if start == -1 or end == -1:
                _LOGGER.warning("No JSON array found in %s response", self._provider)
                return []
            text = text[start : end + 1]

            suggestions = json.loads(text)
            if not isinstance(suggestions, list):
                return []

            valid = []
            for s in suggestions:
                if not isinstance(s, dict):
                    continue
                has_name = "alias" in s or "description" in s
                has_behavior = any(
                    k in s for k in ("actions", "action", "triggers", "trigger")
                )
                if has_name and has_behavior:
                    valid.append(s)
            if len(valid) < len(suggestions):
                _LOGGER.debug(
                    "Filtered %d/%d suggestions (missing required keys)",
                    len(suggestions) - len(valid),
                    len(suggestions),
                )
            return valid

        except (json.JSONDecodeError, KeyError, IndexError) as exc:
            _LOGGER.warning("Failed to parse %s response: %s", self._provider, exc)
            return []

    async def execute_command(
        self, command: str, entities: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Process a natural language command and return HA service calls to execute.

        Returns: {"calls": [...], "response": "human-readable response"}
        """
        system_prompt = (
            "You are Selora AI, a Home Assistant remote control. "
            "The user will give you a command and a list of available entities with their current states. "
            "Your job is to translate the command into Home Assistant service calls.\n\n"
            "RULES:\n"
            "1. Only use entity_ids from the provided entity list.\n"
            "2. Return a JSON object with 'calls' (list of service calls) and 'response' (short confirmation message).\n"
            "3. Each call must have: 'service' (e.g. 'media_player.turn_on'), 'target' (with 'entity_id'), "
            "and optionally 'data' for parameters.\n"
            "4. For media players: use media_player.turn_on, media_player.turn_off, media_player.volume_set, "
            "media_player.media_play, media_player.media_pause, media_player.media_stop.\n"
            "5. For lights: use light.turn_on, light.turn_off, light.toggle.\n"
            "6. For switches: use switch.turn_on, switch.turn_off, switch.toggle.\n"
            "7. Match entity names flexibly — 'kitchen tv' should match 'media_player.kitchen', etc.\n"
            "8. If the command is unclear or no matching entity exists, return an empty calls list "
            "with a helpful response explaining what's available.\n\n"
            "EXAMPLE:\n"
            "Command: 'turn on the kitchen tv'\n"
            '{"calls": [{"service": "media_player.turn_on", "target": {"entity_id": "media_player.kitchen"}}], '
            '"response": "Turning on Kitchen TV"}\n\n'
            "Respond with ONLY the JSON object. No markdown fences. No explanation."
        )

        entity_lines = []
        for e in entities:
            eid = e.get("entity_id", "")
            state = e.get("state", "unknown")
            name = e.get("attributes", {}).get("friendly_name", eid)
            entity_lines.append(f"  - {eid} ({name}): {state}")

        user_prompt = (
            f"COMMAND: {command}\n\n"
            f"AVAILABLE ENTITIES ({len(entities)}):\n"
            + "\n".join(entity_lines)
        )

        try:
            session = async_get_clientsession(self._hass)
            async with session.post(
                self._endpoint,
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_LLM_TIMEOUT),
                json={
                    "model": self._model,
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": user_prompt}],
                    "system": system_prompt,
                },
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    _LOGGER.warning(
                        "%s command failed (%s): %s",
                        self.provider_name, resp.status, body[:200],
                    )
                    return {"calls": [], "response": f"LLM error: {resp.status}"}

                data = await resp.json()
                return self._parse_command_response(data)

        except Exception:
            _LOGGER.exception("Failed to execute command via %s", self.provider_name)
            return {"calls": [], "response": "Failed to reach LLM"}

    def _parse_command_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Parse LLM response into service calls."""
        try:
            content = response.get("content", [])
            text = ""
            for block in content:
                if block.get("type") == "text":
                    text += block.get("text", "")

            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1:
                return {"calls": [], "response": "Could not parse LLM response"}

            result = json.loads(text[start : end + 1])
            if not isinstance(result, dict):
                return {"calls": [], "response": "Invalid response format"}

            return {
                "calls": result.get("calls", []),
                "response": result.get("response", "Command processed"),
            }

        except (json.JSONDecodeError, KeyError) as exc:
            _LOGGER.warning("Failed to parse command response: %s", exc)
            return {"calls": [], "response": "Failed to parse LLM response"}

    async def health_check(self) -> bool:
        """Verify the LLM backend is reachable."""
        if self._provider == LLM_PROVIDER_ANTHROPIC:
            return await self._health_check_anthropic()
        return await self._health_check_ollama()

    async def _health_check_anthropic(self) -> bool:
        """Check Anthropic API with a minimal request."""
        try:
            session = async_get_clientsession(self._hass)
            async with session.post(
                self._endpoint,
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_LLM_TIMEOUT),
                json={
                    "model": self._model,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "Hi"}],
                },
            ) as resp:
                if resp.status == 200:
                    return True
                body = await resp.text()
                _LOGGER.warning(
                    "Anthropic health check failed (%s): %s", resp.status, body[:200]
                )
                return False
        except Exception:
            _LOGGER.exception("Anthropic health check failed")
            return False

    async def _health_check_ollama(self) -> bool:
        """Check Ollama is reachable and the model is pulled."""
        try:
            session = async_get_clientsession(self._hass)
            async with session.get(
                f"{self._host}/api/tags",
                headers=self._get_headers(),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_LLM_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
                models = [m.get("name", "") for m in data.get("models", [])]
                if not any(self._model in m for m in models):
                    _LOGGER.warning(
                        "Model '%s' not found in Ollama. Available: %s",
                        self._model,
                        models,
                    )
                    return False
                return True
        except Exception:
            _LOGGER.exception("Ollama health check failed")
            return False
