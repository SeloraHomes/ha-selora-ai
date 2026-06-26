"""Recipes v2 — deterministic pipeline that renders an installation as a
Home Assistant Package (https://www.home-assistant.io/docs/configuration/packages/).

A recipe bundle is a directory containing:

- ``manifest.yaml``   — recipe metadata, role specs, input schema, prereqs
- ``package/*.yaml.j2`` — Jinja templates for the resources (automations,
  scripts, scenes, helpers) the package will provide

Install runs a six-stage pipeline:

    Recipe Definition  →  Role Resolution  →  Input Validation
                                                     ↓
                                              Render Package
                                                     ↓
                                              Install Package
                                                     ↓
                                              Reload HA Config

Each stage receives the previous stage's artifacts and either produces
its own artifact for the next stage or fails fast with a structured
punch list — no LLM, no chat, no negotiation. Authoring happens upstream
(Connect or a human editor); the integration just runs the pipeline.

Uninstall = delete the package file + reload. The package is the only
state the recipe owns, so there's nothing else to track.
"""

from __future__ import annotations
