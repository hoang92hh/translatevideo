from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TtsProviderManifest:
    """Stable metadata contract consumed by services and shared UI."""

    id: str
    name: str
    version: str
    component_path: str
    description: str
    license_note: str
    runtime_name: str | None = None
    runtime_env: str | None = None
    runtime_setup_script: str | None = None
    runtime_description: str | None = None
    available: bool = True

