# Local Home Assistant Development Setup

How to set up a local Home Assistant Core instance for Selora AI development.

## Prerequisites

- Python 3.14+
- A running LLM backend (see [LLM Backend Options](#llm-backend-options) below)

## 1. Create the HA Core environment

```bash
# Clone this repo
git clone git@gitlab.com:selorahomes/products/selora-ai/ha-integration.git
cd ha-integration

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Home Assistant Core
pip install homeassistant

# First run — generates default config files
hass -c . --script ensure_config
```

## 2. Install the Selora AI integration

The `custom_components/selora_ai/` directory is already in the repo. If you cloned elsewhere:

```bash
# Copy into your HA config directory
cp -r custom_components/selora_ai ~/homeassistant/custom_components/

# Or symlink for active development:
ln -s $(pwd)/custom_components/selora_ai ~/homeassistant/custom_components/selora_ai
```

## 3. Start Home Assistant

```bash
hass -c .
```

Open http://localhost:8123 in your browser. Complete the onboarding wizard.

## 4. Add the Selora AI integration

1. Go to **Settings > Devices & Services**
2. Click **+ Add Integration**
3. Search for **Selora AI**
4. Choose your LLM provider:
   - **Anthropic (Claude)** — enter your API key
   - **Ollama (Local)** — enter host URL and model name
5. Click **Submit**

After 30 seconds, Selora AI will auto-discover devices on your network, accept discoverable integrations, sync Cast known_hosts, auto-assign areas, and generate a dashboard.

## LLM Backend Options

### Option A: Anthropic API (Recommended)

Uses Claude via Anthropic's cloud API. Best quality suggestions.

1. Get an API key at [console.anthropic.com](https://console.anthropic.com)
2. Select "Anthropic (Claude)" in the config flow
3. Paste your API key
4. Default model: `claude-sonnet-4-6`

### Option B: Ollama (Local / On-Prem)

Runs a local LLM. No data leaves your network.

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull the default model
ollama pull llama3.1

# Verify it's running
curl http://localhost:11434/api/tags
```

Then select "Ollama (Local LLM)" in the config flow.

- Default host: `http://localhost:11434`
- Default model: `llama3.1`

## Verifying Selora AI is working

After setup, Selora AI runs on a 1-hour analysis cycle and a 30-second startup auto-discovery.

1. Check HA logs: **Settings > System > Logs** — filter for `selora_ai`
2. Check the **Selora AI Hub** device page — should show Status, Devices, Discovery, and Last Activity sensors
3. Press **Discover Devices** to trigger a network scan
4. Press **Auto Setup** to accept any pending discovery flows
5. Check **Settings > Automations** — suggested automations appear as disabled (prefixed with `[Selora AI]`)

## Project structure

``` 
custom_components/selora_ai/
├── __init__.py          # Integration setup/teardown
├── button.py            # Hub action buttons (Discover, Auto Setup, Cleanup, Reset)
├── collector.py         # Data collection + automation writer
├── config_flow.py       # UI config flow (provider selection)
├── const.py             # Constants + known integrations database
├── device_manager.py    # Device discovery, pairing, dashboard generation
├── llm_client.py        # Unified LLM client (Anthropic + Ollama)
├── manifest.json        # HA integration manifest
├── sensor.py            # Hub sensors (Status, Devices, Discovery, Activity)
└── strings.json         # UI strings for config flow
```

## Contributing

1. Create a feature branch from `main`
2. Make your changes
3. Test locally with `hass -c .`
4. Open a merge request on GitLab
