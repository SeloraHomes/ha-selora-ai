"""Frigate NVR: adding the home's cameras to it.

Frigate runs on the Selora OS host, outside Home Assistant, and is reached at
the URL the Frigate integration was set up with. Selora AI writes only the
per-camera part of Frigate's configuration; detectors, hardware acceleration
and everything global belong to the host. See ``docs/dev/frigate.md``.
"""

from __future__ import annotations
