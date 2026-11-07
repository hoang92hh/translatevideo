from __future__ import annotations

from .installation import installation_settings
from .tts import VIENEU_PROVIDER


DEFAULT_PROVIDER_SETTING = "tts/defaultProvider"


def default_tts_provider() -> str:
    return str(
        installation_settings().value(
            DEFAULT_PROVIDER_SETTING,
            VIENEU_PROVIDER,
        )
    ).strip() or VIENEU_PROVIDER


def set_default_tts_provider(provider: str) -> None:
    settings = installation_settings()
    settings.setValue(DEFAULT_PROVIDER_SETTING, provider)
    settings.sync()
