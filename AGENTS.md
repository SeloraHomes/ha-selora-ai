# Repository Guidelines

## Project Structure & Module Organization

This is a custom Home Assistant integration located in `custom_components/selora_ai/`. The architecture is centered around a "smart butler" that uses LLMs (Anthropic Claude or local Ollama) to analyze device states and generate automations.

- **`__init__.py`**: Component setup, API registration, and panel initialization.
- **`collector.py`**: Background data gathering and LLM analysis logic.
- **`llm_client.py`**: Unified interface for LLM backends.
- **`device_manager.py`**: Discovery, ADB pairing (for Android TV), and dashboard generation.
- **`config_flow.py`**: UI-based setup and device onboarding.
- **`conversation.py`**: HA Assist Conversation Agent implementation.

## Build, Test, and Development Commands

The integration is developed for Home Assistant 2025.1+.

### Running Home Assistant

- **Docker**: `docker compose up -d`
- **Bare Metal**:
  ```bash
  python3 -m venv venv && source venv/bin/activate
  pip install homeassistant
  hass -c .
  ```

### Validation

- **Local (Lefthook)**: Pre-push hooks run `scripts/validate_hacs.py` and `scripts/validate_manifest.py` automatically. Install once with `lefthook install`.
- **CI**: The `validate` job in `.gitlab-ci.yml` runs both scripts on every MR and push to `main`.
- **What "valid" means**: `hacs.json` and `manifest.json` must be well-formed JSON containing all required fields. `manifest.json` version must be semver (`X.Y.Z`). `translations/en.json` must have the same top-level keys as `strings.json`. Both validation scripts are stdlib-only — no pip install required.

## Coding Style & Naming Conventions

- **Python**: 3.14+ with `async`/`await` throughout.
- **Imports**: Every file must start with `from __future__ import annotations`.
- **Typing**: Use modern type hints (`str | None` instead of `Optional[str]`).
- **Logging**: Use `_LOGGER = logging.getLogger(__name__)`.
- **Security**:
  - Never hardcode secrets or API keys.
  - Use `uuid.uuid4()` for unique IDs instead of `hashlib.md5`.
  - Avoid bare `except Exception`.

## Testing Guidelines

There is currently no local test suite. Validation is limited to HACS compliance in CI. Ensure new features are manually tested within a running Home Assistant instance.

## Commit & Pull Request Guidelines

- **Conventional Commits**: Use `feat:`, `fix:`, `refactor:`, `docs:`, etc.
- **Branching**:
  - Main branch is `main`.
  - Feature branches should be prefixed with `selora-ai-` or `feat/`.
- **PRs**: Ensure all GitLab CI SAST and secret detection findings are resolved.
