# Selora AI — Home Assistant Integration

Custom Home Assistant integration that uses LLM-powered analysis to automatically discover devices, suggest automations, and manage your smart home.

## Features

- **Dual LLM Backend** — Anthropic Claude (cloud, recommended) or Ollama (local, on-prem)
- **Auto Device Discovery** — scans your network, accepts discoverable integrations, and auto-pairs Android TVs via ADB
- **Automation Suggestions** — periodically analyzes your home data and suggests useful automations
- **Cast Sync** — discovers Google Cast devices on your subnet and syncs `known_hosts`
- **Area Auto-Assignment** — matches device names to HA areas
- **Dashboard Generation** — builds a Lovelace dashboard with media player controls
- **Natural Language Commands** — webhook endpoint translates plain English into HA service calls

## Architecture

```
HA entity registry / state machine / recorder (SQLite)
    |
    v
DataCollector  ──snapshot──>  LLMClient (Anthropic API or local Ollama)
    |                              |
    |                         suggestions
    |                              v
    v                    automations.yaml (disabled)
logging + sensors              + reload
```

## Installation

1. Copy `custom_components/selora_ai/` into your Home Assistant `custom_components/` directory
2. Restart Home Assistant
3. Go to **Settings > Devices & Services > Add Integration > Selora AI**
4. Choose your LLM provider:
   - **Anthropic (Claude)** — enter your API key from [console.anthropic.com](https://console.anthropic.com)
   - **Ollama** — enter your local Ollama host URL and model name

## Hub Sensors

After setup, Selora AI registers a Hub device with these sensors:

| Sensor | Description |
|--------|-------------|
| **Status** | Aggregate device count + pending automations |
| **Devices** | Categorised inventory (TVs, speakers, lights, etc.) |
| **Discovery** | Pending discovery flows vs configured count |
| **Last Activity** | Recent action log (diagnostic) |

## Hub Buttons

| Button | Action |
|--------|--------|
| **Discover Devices** | Scan network, report discovered/configured devices |
| **Auto Setup** | Accept all auto-discoverable pending flows |
| **Cleanup** | Remove stale mirror devices and orphaned entities |
| **Reset Everything** | Wipe non-protected integrations, clean up, re-discover |

## Webhook API

### Command Endpoint

```
POST /api/webhook/selora_ai_command
Content-Type: application/json

{"command": "turn on the kitchen tv"}
```

Returns:
```json
{
  "command": "turn on the kitchen tv",
  "response": "Turning on Kitchen TV",
  "executed": ["media_player.turn_on"]
}
```

### Devices Endpoint

```
POST /api/webhook/selora_ai_devices
Content-Type: application/json

{"action": "discover"}
```

Supported actions: `discover`, `auto_setup`, `accept_flow`, `submit_pin`, `cleanup`, `reset`

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `llm_provider` | `anthropic` | LLM backend (`anthropic` or `ollama`) |
| `anthropic_api_key` | — | Anthropic API key (required for Claude) |
| `anthropic_model` | `claude-opus-4-6` | Anthropic model ID |
| `ollama_host` | `http://localhost:11434` | Ollama server URL |
| `ollama_model` | `llama3.1` | Ollama model name |
| `recorder_lookback_days` | `7` | Days of history to analyze |

## Known Integrations

Selora AI recognizes ~85 smart home integrations across categories: lighting, TVs, speakers, appliances, thermostats, cameras, locks, vacuums, cars, energy, IoT platforms, and protocol bridges. See `const.py` for the full list.

## Requirements

- Home Assistant 2025.1+
- Python 3.12+
- `aiohttp>=3.8.0`
- `adb-shell[async]>=0.4.4` (for Android TV auto-pairing)
- `pyyaml>=6.0`

## License

Selora Homes Software License. See [LICENSE](LICENSE) for details.
