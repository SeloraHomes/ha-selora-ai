"""Where the panel's bundled fonts live and the URL they are served at.

The panel loads ``fonts.css`` from Home Assistant itself rather than from
Google, so opening it makes no third-party request and keeps its font offline,
with or without a Selora hub. The panel resolves the stylesheet relative to
its own module URL, so the static path is the only thing the integration has
to provide.

Dashboards do not use these: fonts a dashboard depends on are provisioned by
Selora OS, so they outlive this integration.
"""

from __future__ import annotations

from pathlib import Path

from .const import DOMAIN

FONTS_DIR = Path(__file__).parent / "frontend" / "fonts"
FONTS_URL_BASE = f"/api/{DOMAIN}/fonts"
