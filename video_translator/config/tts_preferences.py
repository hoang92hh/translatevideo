from __future__ import annotations

from .installation import installation_settings
from ..common.models import StepId
from .step_defaults import default_value


DEFAULT_PROVIDER_SETTING = "tts/defaultProvider"


def default_tts_provider() -> str:
    return str(
        installation_settings().value(
            DEFAULT_PROVIDER_SETTING,
            default_value(StepId.TTS, "provider"),
        )
    ).strip() or str(default_value(StepId.TTS, "provider"))


def set_default_tts_provider(provider: str) -> None:
    settings = installation_settings()
    settings.setValue(DEFAULT_PROVIDER_SETTING, provider)
    settings.sync()
