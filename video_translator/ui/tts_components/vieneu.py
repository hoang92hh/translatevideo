from __future__ import annotations

from ...config.tts import PROVIDER_LICENSE_NOTES, VIENEU_PROVIDER, VIENEU_VOICES
from .base import ReferenceTtsComponentBase


class VieNeuTtsComponent(ReferenceTtsComponentBase):
    def __init__(self) -> None:
        super().__init__(
            VIENEU_PROVIDER,
            "VieNeu-TTS",
            "TTS tiếng Việt local; hỗ trợ preset hoặc audio tham chiếu theo speaker.",
            PROVIDER_LICENSE_NOTES[VIENEU_PROVIDER],
            VIENEU_VOICES,
            "Default",
            "Giọng",
        )
