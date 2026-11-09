from __future__ import annotations

import os
from pathlib import Path


DEFAULT_VIENEU_REFERENCE_VOICE = r"reference\voice\vietNeu\example_ngoc_huyen.wav"


def resolve_reference_voice(value: str) -> Path:
    """Resolve a stored reference path from the application distribution root."""
    path = Path(value)
    if path.is_absolute():
        return path
    root = Path(os.environ.get("TRANSLANGUAGE_DISTRIBUTION_ROOT", Path(__file__).resolve().parents[2]))
    return root / path
