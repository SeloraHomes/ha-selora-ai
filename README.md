# Selora AI — Home Assistant Integration

Selora AI is a smart-home AI butler for Home Assistant. It connects to an LLM backend (Anthropic Claude, OpenAI, or a local Ollama model), learns your home's patterns, and proactively generates automations — all while keeping you in full control.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?category=Integration&repository=ha-selora-ai&owner=SeloraHomes)

**[Documentation](https://selorahomes.com/docs/selora-ai/)**

---

## Features

| Feature | Description |
|---|---|
| **AI Automation Suggestions** | Analyzes device states and history, then writes draft automations (disabled, prefixed `[Selora AI]`) for your review. |
| **Pattern Detection** | Detects time-based routines, device correlations, and usage sequences — then converts them into automation suggestions with confidence scoring. |
| **Natural Language Commands** | Send plain-English commands via the Selora AI panel or Home Assistant Assist. |
| **Automation Versioning** | Full version history for every Selora AI automation, with diff viewer in the panel. |
| **Stale Automation Detection** | Flags automations referencing unavailable entities or that haven't triggered in a while. |
| **MCP Server** | Exposes a [Model Context Protocol](https://modelcontextprotocol.io/) endpoint so external AI agents can interact with your home through Selora AI. |
| **Three LLM Backends** | Supports **Anthropic Claude** (recommended), **OpenAI**, and **Ollama** (fully local, zero data egress). |

---

## Requirements

- Home Assistant **2025.1** or later
- For **Anthropic Claude**: an [Anthropic API key](https://console.anthropic.com/)
- For **OpenAI**: an [OpenAI API key](https://platform.openai.com/)
- For **Ollama**: a running [Ollama](https://ollama.com/) server reachable from your HA host

---

## Installation

See the [installation guide](https://selorahomes.com/docs/selora-ai/installation/) for detailed instructions.

---

## Learn More

| Topic | Link |
|---|---|
| **Configuration** | [Setting up LLM providers and options](https://selorahomes.com/docs/selora-ai/configuration/) |
| **Chat Panel & Assist** | [Natural language commands and voice control](https://selorahomes.com/docs/selora-ai/chat-and-assist/) |
| **AI-Generated Automations** | [How Selora AI suggests and manages automations](https://selorahomes.com/docs/selora-ai/automations/) |
| **MCP Server** | [Connecting external AI agents to your home](https://selorahomes.com/docs/selora-ai/mcp-onboarding/) |
| **Privacy & Support** | [Data privacy per provider and issue reporting](https://selorahomes.com/docs/selora-ai/privacy/) |

---

## License

Selora Homes Software License. See [LICENSE](LICENSE) for details.
