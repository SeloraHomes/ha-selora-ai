# Selora AI — Home Assistant Integration
# Run `just` to see all available recipes.

set dotenv-load

frontend_dir := "custom_components/selora_ai/frontend"

# List available recipes
default:
    @just --list

# ── Build ─────────────────────────────────────────────────────────────────────

# Build the frontend (panel.js + card.js)
build:
    cd {{ frontend_dir }} && npm run build

# Install frontend dependencies
install:
    cd {{ frontend_dir }} && npm ci

# ── Test ──────────────────────────────────────────────────────────────────────

# Run all tests (Python + frontend)
test: test-python test-frontend

# Run Python tests
test-python *args='':
    pytest tests/ -v {{ args }}

# Run frontend tests (vitest)
test-frontend:
    cd {{ frontend_dir }} && npx vitest run

# Run frontend tests in watch mode
test-watch:
    cd {{ frontend_dir }} && npx vitest

# ── Lint & Format ────────────────────────────────────────────────────────────

# Run all linters
lint: lint-python lint-frontend

# Lint Python with ruff
lint-python:
    ruff check custom_components/

# Check frontend formatting
lint-frontend:
    cd {{ frontend_dir }} && npx prettier --check 'src/**/*.js'

# Format all code
fmt: fmt-python fmt-frontend

# Format Python with ruff
fmt-python:
    ruff format custom_components/
    ruff check --fix custom_components/

# Format frontend with prettier
fmt-frontend:
    cd {{ frontend_dir }} && npx prettier --write 'src/**/*.js'

# ── Validate ─────────────────────────────────────────────────────────────────

# Run HACS + hassfest validation
validate: validate-hacs validate-hassfest

# Validate HACS manifest
validate-hacs:
    python3 scripts/validate_hacs.py

# Validate with hassfest (requires Docker)
validate-hassfest:
    mkdir -p .hassfest_tmp \
      && rsync -a --delete --exclude node_modules custom_components/ .hassfest_tmp/custom_components/ \
      && docker run --rm -v "$(pwd)/.hassfest_tmp/custom_components:/github/workspace/custom_components" ghcr.io/home-assistant/hassfest; \
    rc=$?; rm -rf .hassfest_tmp; exit $rc

# ── Deploy ──────────────────────────────────────────────────────────────────

ha_host := env_var_or_default("HA_HOST", "root@homeassistant.local")
ha_port := env_var_or_default("HA_PORT", "22")
ha_path := "~/config/custom_components/"

# Deploy to dev HA instance and restart
deploy: build (_sync-to-ha)
    -ssh -p {{ ha_port }} {{ ha_host }} -t 'ha core restart'

# Deploy to dev HA instance without restart
deploy-no-restart: build (_sync-to-ha)

_sync-to-ha:
    rsync -az -e 'ssh -p {{ ha_port }}' --delete --exclude node_modules custom_components/selora_ai/ {{ ha_host }}:{{ ha_path }}selora_ai/

# ── CI (mirrors pre-push checks) ────────────────────────────────────────────

# Run the full pre-push check suite
ci: lint test build validate
