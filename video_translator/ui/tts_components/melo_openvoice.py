from __future__ import annotations

from ...config.tts import MELO_OPENVOICE_PROVIDER, MELO_SPEAKERS, PROVIDER_LICENSE_NOTES
from .base import ReferenceTtsComponentBase


class MeloOpenVoiceTtsComponent(ReferenceTtsComponentBase):
    def __init__(self) -> None:
        super().__init__(
            MELO_OPENVOICE_PROVIDER,
            "MeloTTS + OpenVoice V2",
            "MeloTTS tạo giọng nền, OpenVoice chuyển màu giọng từ audio tham chiếu.",
            PROVIDER_LICENSE_NOTES[MELO_OPENVOICE_PROVIDER],
            MELO_SPEAKERS,
            "EN-Default",
            "Giọng nền",
        )
