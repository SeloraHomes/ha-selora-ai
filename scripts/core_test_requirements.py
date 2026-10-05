"""Print the requirements of the core components the tests set up or import.

At the exact versions the installed Home Assistant pins in their manifests:
unpinned, ``hassil`` / ``home-assistant-intents`` pull a build that dropped
``FuzzyConfig``, and ``assist_pipeline`` cannot even be imported without what
``tts`` and ``ffmpeg`` need. Used by CI, the pre-push hook and CONTRIBUTING.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

COMPONENTS = ("conversation", "assist_pipeline", "tts", "ffmpeg")


def main() -> None:
    requirements: list[str] = []
    for component in COMPONENTS:
        spec = importlib.util.find_spec(f"homeassistant.components.{component}")
        if spec is None or spec.origin is None:
            continue
        manifest = json.loads((Path(spec.origin).parent / "manifest.json").read_text())
        requirements.extend(manifest.get("requirements", []))
    print(" ".join(dict.fromkeys(requirements)))


if __name__ == "__main__":
    main()
