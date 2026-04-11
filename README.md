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

## Configuration

1. In Home Assistant go to **Settings > Devices & Services > + Add integration**.
2. Search for **Selora AI** and select it.
3. Choose your LLM provider:

   **Anthropic Claude** (recommended)
   - Enter your Anthropic API key.
   - Default model: `claude-sonnet-4-6`.

   **OpenAI**
   - Enter your OpenAI API key.
   - Default model: `gpt-5.4`.

   **Ollama (local)**
   - Enter the host URL of your Ollama server (default: `http://localhost:11434`).
   - Default model: `llama4`.

After setup, click **Configure** on the Selora AI card to adjust the LLM model and analysis frequency.

---

## Using the AI

### Side panel

After setup, a **Selora AI** panel appears in the HA sidebar. Open it to:
- Chat with the AI about your home
- Request, review, and manage automations
- Browse pattern-based suggestions with confidence scores
- View automation version history and diffs
- Detect and clean up stale automations

### Home Assistant Assist

You can set Selora AI as the default Conversation Agent under **Settings > Voice assistants**. Once set, all Assist commands (voice or text) are handled by Selora AI.

---

## MCP Server

Selora AI exposes an MCP endpoint so external AI agents (Claude Code, Codex, etc.) can interact with your home programmatically.

The MCP endpoint is available at:
```
http://<your-ha-host>:8123/api/selora_ai/mcp
```

See the [MCP setup guide](https://selorahomes.com/docs/selora-ai/mcp-onboarding/) for OAuth 2.0 authentication and client configuration.

For full protocol details, see [docs/selora-mcp-server.md](./docs/selora-mcp-server.md).

---

## AI-Generated Automations

Selora AI writes draft automations to your `automations.yaml`. Every generated automation:
- Is **disabled by default** — it will not run until you enable it.
- Has a name prefixed with `[Selora AI]` so they're easy to find and review.
- Can be enabled, edited, or deleted from **Settings > Automations** like any other automation.

---

## Privacy

- **Anthropic Claude**: Device names, entity states, and automation text are sent to the Anthropic API. No audio or video is transmitted. Review [Anthropic's privacy policy](https://www.anthropic.com/privacy).
- **OpenAI**: Same data scope as Anthropic. Review [OpenAI's privacy policy](https://openai.com/policies/privacy-policy).
- **Ollama**: All data stays on your local network. Nothing leaves your home.

---

## Issues & Support

Report bugs or request features on the [GitHub issue tracker](https://github.com/SeloraHomes/ha-selora-ai/issues).

---

## License

Selora Homes Software License. See [LICENSE](LICENSE) for details.
