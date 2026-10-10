from __future__ import annotations

from PySide6.QtCore import QSettings

from .tts import VIENEU_PROVIDER


DEFAULT_PROVIDER_SETTING = "tts/defaultProvider"


def default_tts_provider() -> str:
    return str(
        QSettings("TransLanguage", "TransLanguage").value(
            DEFAULT_PROVIDER_SETTING,
            VIENEU_PROVIDER,
        )
    ).strip() or VIENEU_PROVIDER


def set_default_tts_provider(provider: str) -> None:
    settings = QSettings("TransLanguage", "TransLanguage")
    settings.setValue(DEFAULT_PROVIDER_SETTING, provider)
    settings.sync()
