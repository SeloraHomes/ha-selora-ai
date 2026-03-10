# Selora AI — Home Assistant Integration

Selora AI is a next-generation Home Assistant integration that acts as an "AI Architect" for your smart home. It uses Large Language Models (LLMs) to analyze your home's data, discover devices, and help you build a more automated, intuitive living space.

## 🚀 Key Features

- **Dual LLM Backend** — Support for **Anthropic Claude** (cloud-based, high performance) and **Ollama** (local, privacy-focused).
- **Selora AI Architect** — A dedicated side panel chat interface where you can ask questions about your home and generate automations using natural language.
- **Home Assistant Assist Integration** — Use Selora AI as your primary conversation agent in the standard HA chat interface (Assist).
- **Context-Aware Conversations** — The AI now sees your existing automations, allowing it to suggest modifications or avoid duplicates during chat.
- **Intelligent Background Analysis** — Periodically analyzes your devices, entity states, and historical data to suggest useful, context-aware automations.
- **Zero-Touch Android TV Pairing** — Fully automatic onboarding for Android TVs. Selora AI wakes the TV via WoL, captures the pairing PIN via ADB screenshots, and uses Claude Vision to read and submit the PIN.
- **Network Discovery & Onboarding** — Scans your network for supported integrations and helps you onboard them with area auto-assignment.
- **Selora AI Hub** — A centralized device in Home Assistant with sensors and buttons to monitor and manage the integration's status and actions.
- **Automated Dashboard Generation** — Automatically builds a Lovelace dashboard with controls for your media players and other discovered devices.

## 🛠 Implementation Status

| Feature | Status | Description |
|---------|--------|-------------|
| **Core Logic** | ✅ Complete | Data collection, LLM interfacing, and integration setup. |
| **Android TV Auto-Pairing** | ✅ Complete | WoL + ADB + Claude Vision orchestration. |
| **Architect Chat** | ✅ Complete | Side panel & Home Assistant Assist (Conversation Agent). |
| **Automation Suggestions** | ✅ Complete | Periodic analysis + context-aware chat (sees existing automations). |
| **Hub Sensors & Buttons** | ✅ Complete | Real-time status, device inventory, and management actions (Discover, Cleanup, Reset). |
| **Webhook API** | ✅ Complete | Endpoints for external commands and discovery orchestration. |
| **MQTT Listener** | 🚧 Pending | Future feature for reaction-based behavior capture (Matthew, Mar 4). |

## 📂 Project Structure

- `__init__.py`: Component setup, API registration, and panel initialization.
- `collector.py`: Background data gathering and LLM analysis logic.
- `llm_client.py`: Unified interface for Anthropic and Ollama backends.
- `device_manager.py`: The "brain" for discovery, ADB pairing, and dashboard generation.
- `config_flow.py`: User-friendly setup and device onboarding flow.
- `conversation.py`: Assist Conversation Agent implementation for natural language control.
- `automation_utils.py`: Helpers for writing automations to `automations.yaml`.
- `sensor.py` & `button.py`: Hub device entity implementations.
- `frontend/`: Custom side panel frontend (React/JS).

## ⏭ Next Steps

1.  **MQTT Reaction System**: Implement `mqtt_listener.py` to capture and classify point-in-time events for better automation triggers.
2.  **Expanded Device Support**: Increase the number of `KNOWN_INTEGRATIONS` and specialized discovery handlers.
3.  **Stability & Timeouts**: Refine LLM request handling to better manage timeouts and network latency.
4.  **Multi-language Support**: Finalize translations for the UI and LLM prompts.

## 📋 Requirements

- Home Assistant 2025.1+
- Python 3.12+
- `aiohttp>=3.8.0`
- `adb-shell[async]>=0.4.4` (for Android TV auto-pairing)
- `ruamel.yaml>=0.17.0`

## 📄 License

Selora Homes Software License. See [LICENSE](LICENSE) for details.
