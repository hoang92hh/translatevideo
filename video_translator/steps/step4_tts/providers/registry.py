from __future__ import annotations

import importlib
from typing import Any

from .manifest import TtsProviderManifest
from .edge.manifest import MANIFEST as EDGE_MANIFEST
from .melo.manifest import MANIFEST as MELO_MANIFEST
from .piper.manifest import MANIFEST as PIPER_MANIFEST
from .vieneu.manifest import MANIFEST as VIENEU_MANIFEST


_MANIFESTS: tuple[TtsProviderManifest, ...] = (
    VIENEU_MANIFEST,
    PIPER_MANIFEST,
    MELO_MANIFEST,
    EDGE_MANIFEST,
)

_KEYS = {
    "vieneu": VIENEU_MANIFEST,
    "piper": PIPER_MANIFEST,
    "melo": MELO_MANIFEST,
    "edge": EDGE_MANIFEST,
}


def all_manifests() -> tuple[TtsProviderManifest, ...]:
    return _MANIFESTS


def get_manifest(provider_id: str) -> TtsProviderManifest:
    for manifest in _MANIFESTS:
        if manifest.id == provider_id:
            return manifest
    raise KeyError(f"Unknown TTS provider: {provider_id}")


def provider_id(key: str) -> str:
    return _KEYS[key].id


def provider_value(key: str, name: str) -> Any:
    module = importlib.import_module(f"video_translator.steps.step4_tts.providers.{key}.config")
    return getattr(module, name)


def load_provider_symbol(key: str, module_name: str, attribute: str) -> Any:
    module = importlib.import_module(module_name)
    return getattr(module, attribute)


def load_component(manifest: TtsProviderManifest) -> Any:
    module_name, attribute = manifest.component_path.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, attribute)

