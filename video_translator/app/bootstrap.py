"""Composition hooks for discovering isolated providers."""

from ..steps.step4_tts.providers import all_manifests


def load_provider_manifests():
    return all_manifests()

