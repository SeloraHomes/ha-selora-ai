# Contributing to Selora AI

Thanks for working on the integration. This document covers the developer-facing details that don't belong in the end-user README.

---

## Implementation Status

| Feature | Status | Description |
|---|---|---|
| **Core Logic** | ✅ Complete | Data collection, LLM interfacing, and integration setup. |
| **Android TV Auto-Pairing** | ✅ Complete | WoL + ADB + Claude Vision orchestration. |
| **Architect Chat** | ✅ Complete | Side panel & Home Assistant Assist (Conversation Agent). |
| **Automation Suggestions** | ✅ Complete | Periodic analysis + context-aware chat (sees existing automations). |
| **Hub Sensors & Buttons** | ✅ Complete | Real-time status, device inventory, and management actions (Discover, Cleanup, Reset). |
| **MQTT Listener** | 🚧 Pending | Future feature for reaction-based behavior capture. |

---

## Project Structure

```
custom_components/selora_ai/
├── __init__.py          # Integration setup/teardown, entry routing
├── config_flow.py       # UI config flow (LLM setup → device discovery → area assignment → results)
├── collector.py         # Hourly data collection + LLM automation writer
├── llm_client.py        # Unified LLM client (Anthropic + Ollama)
├── device_manager.py    # Device discovery, pairing, area assignment, dashboard generation
├── conversation.py      # Assist Conversation Agent — routes natural language to HA service calls
├── automation_utils.py  # Helpers for writing/reading automations.yaml
├── automation_store.py  # Lifecycle management for [Selora AI] automations
├── button.py            # Hub action buttons (Discover, Scan, Cleanup, Reset)
├── sensor.py            # Hub sensors (Status, Devices, Discovery, Last Activity)
├── const.py             # Constants, config keys, known integrations database
├── manifest.json        # HA integration manifest
├── strings.json         # UI strings for config flow
├── translations/en.json # English translations (must match strings.json)
├── frontend/            # Custom side panel (React/JS, token-streaming chat)
└── brand/               # Logo and icon assets
```

---

## Development Setup

### Docker (recommended)

```bash
docker compose up -d
```

### Bare metal

```bash
python3 -m venv venv && source venv/bin/activate
pip install homeassistant
hass -c .
```

Open http://localhost:8123, then add Selora AI under **Settings → Devices & Services**.

> **Note:** If running Ollama alongside Docker, use `http://host.docker.internal:11434` as the Ollama host instead of `localhost:11434`.

---

## Key Conventions

### Code Style
- Python 3.12+, `async`/`await` throughout
- `from __future__ import annotations` in every file
- Modern type hints (`str | None`, not `Optional[str]`)
- Logging via `_LOGGER = logging.getLogger(__name__)`
- No hardcoded secrets — API keys come from the user's config entry only

### Home Assistant Patterns
- Config entries use `entry_type`: `"llm_config"` or `"device_onboarding"`
- Entity platforms: `sensor`, `button` (registered in `PLATFORMS` in `__init__.py`)
- All entities use `_attr_has_entity_name = True` and reference the hub device `(DOMAIN, "selora_ai_hub")`
- Dispatcher signals for real-time updates: `SIGNAL_DEVICES_UPDATED`, `SIGNAL_ACTIVITY_LOG`
- Dashboard generation uses the HA Lovelace API (`LovelaceStorage.async_save`) — not direct file writes

### Config Flow
- First entry: LLM provider selection → credentials → device discovery → area assignment → results
- Subsequent "Add Entry": skips LLM config, goes straight to device discovery
- `strings.json` and `translations/en.json` must always stay in sync
- Step IDs must match keys in `strings.json`: `user`, `anthropic`, `ollama`, `select_devices`, `results`

### What NOT to Do
- Do not hardcode API keys or tokens anywhere
- Do not use `hashlib.md5` — use `uuid.uuid4()` for unique IDs (SAST flags md5 as weak crypto)
- Do not use bare `except Exception` — catch specific exceptions
- Do not auto-accept discovered devices without user consent
- Do not write to Lovelace files directly — use the HA Lovelace API

---

## Git & Branching

- **Main branch:** `main`
- **Feature branches:** `selora-ai-<feature>`
- **Commit style:** conventional commits (`feat:`, `fix:`, `refactor:`, `docs:`)
- Never commit secrets — `.env` and `secrets.yaml` are in `.gitignore`
- GitLab CI runs SAST and secret detection — all findings must be resolved before merge

---

## Next Steps / Roadmap

1. **MQTT Reaction System** — Implement `mqtt_listener.py` to capture and classify point-in-time events for better automation triggers.
2. **Expanded Device Support** — Increase the number of `KNOWN_INTEGRATIONS` and add specialized discovery handlers.
3. **Stability & Timeouts** — Refine LLM request handling to better manage timeouts and network latency.
4. **Multi-language Support** — Finalize translations for UI strings and LLM prompts.
5. **Cloud Mode** — Managed LLM backend (Selora-hosted) so end users don't need to supply their own API key.
