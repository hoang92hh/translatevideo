from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SeparationProviderManifest:
    id: str
    name: str
    version: str
    available: bool
    requirements_file: str

