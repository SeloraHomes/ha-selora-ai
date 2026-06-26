"""Constants for the v2 recipes pipeline.

Kept in this sub-package (not the integration-wide ``const.py``) so the
pipeline modules form a self-contained unit. The top-level integration
imports recipe constants from here when it needs them.
"""

from __future__ import annotations

# Subdirectory of <config>/ where bundle sources live after download or
# manual placement. Each recipe is a directory named after its slug.
RECIPE_BUNDLE_DIR = "selora_ai_recipes"

# Subdirectory of <config>/ where rendered HA packages get written.
# Matches HA's standard ``packages/`` convention so users who already
# use packages can recognise the layout. We namespace under ``selora_ai/``
# so installed recipes can't collide with user-authored packages.
PACKAGE_DIR_NAME = "packages"
PACKAGE_NAMESPACE = "selora_ai"

# Filename for the per-recipe rendered package inside the namespace.
# Slug + .yaml.
PACKAGE_FILE_SUFFIX = ".yaml"

# HA's ``configuration.yaml`` is where we add the packages include the
# first time the integration writes a package. Existing user content is
# preserved; a timestamped backup is created before any edit.
CONFIGURATION_FILENAME = "configuration.yaml"
CONFIGURATION_BACKUP_SUFFIX = ".selora-ai.backup"

# Store key for install records (slug → record metadata). Bumped from
# the v1 store key so the v2 records sit in their own slot and the
# v1 chat-driven records (if any are still on disk from the prior
# branch) can't be misread by the v2 reader.
INSTALL_STORE_KEY = "selora_ai_recipe_installs_v2"
INSTALL_STORE_VERSION = 1

# Cap on rendered package size — bounds the blast radius if a template
# explodes (Jinja loops over a huge entity list, unbounded recursion).
RENDERED_PACKAGE_MAX_BYTES = 1 * 1024 * 1024  # 1 MB

# Cap on number of package files inside a bundle. Recipes are not
# meant to be sprawling — keep them focused.
BUNDLE_MAX_TEMPLATE_FILES = 32

# Limits on bundle archives downloaded or uploaded by the user. Caps
# are intentionally tight — a recipe is a small descriptor + a handful
# of Jinja templates; anything bigger is almost certainly a mistake.
RECIPE_ARCHIVE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB on the wire
RECIPE_ARCHIVE_MAX_EXTRACTED_BYTES = 50 * 1024 * 1024  # 50 MB on disk
RECIPE_ARCHIVE_MAX_FILES = 200  # zip-bomb guard
RECIPE_FETCH_TIMEOUT_SECONDS = 30

# Catalog endpoint. The HA integration fetches the recipes index
# from here so updates can ship without re-releasing. Env var
# SELORA_AI_RECIPE_CATALOG_URL overrides for dev — set to
# http://localhost:1313/api/recipes.json against a local Hugo server.
RECIPE_CATALOG_URL_DEFAULT = "https://selorahomes.com/api/recipes.json"
RECIPE_CATALOG_TTL_SECONDS = 300  # 5 min cache

# Resource kinds an HA package can carry. We accept any top-level key
# the recipe's templates emit, but validate the union against this set
# so a typo in the template doesn't silently write a no-op file. List
# pulled from HA's documented package schema.
PACKAGE_RESOURCE_KINDS: frozenset[str] = frozenset(
    {
        "automation",
        "script",
        "scene",
        "input_boolean",
        "input_number",
        "input_text",
        "input_select",
        "input_datetime",
        "input_button",
        "counter",
        "timer",
        "template",
        "sensor",
        "binary_sensor",
        "switch",
        "shell_command",
        "rest_command",
        "notify",
        "group",
        "alert",
    }
)
