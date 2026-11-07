from __future__ import annotations

from .demucs.manifest import MANIFEST as DEMUCS_MANIFEST
from .mdx.manifest import MANIFEST as MDX_MANIFEST

_MANIFESTS = (MDX_MANIFEST, DEMUCS_MANIFEST)


def all_manifests():
    return _MANIFESTS


def get_manifest(provider_id: str):
    for manifest in _MANIFESTS:
        if manifest.id == provider_id:
            return manifest
    raise KeyError(f"Unknown separation provider: {provider_id}")
