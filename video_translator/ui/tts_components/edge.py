from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QComboBox, QGridLayout, QLabel

from ...config.tts import EDGE_TTS_PROVIDER, EDGE_VOICES, PROVIDER_LICENSE_NOTES
from .base import TtsComponentBase, speed_control


class EdgeTtsComponent(TtsComponentBase):
    provider_name = EDGE_TTS_PROVIDER

    def __init__(self) -> None:
        super().__init__(
            "Edge TTS",
            "TTS online; một voice preset dùng chung cho toàn bộ video.",
            PROVIDER_LICENSE_NOTES[EDGE_TTS_PROVIDER],
        )
        form = QGridLayout()
        form.addWidget(QLabel("Giọng online"), 0, 0)
        self.voice = QComboBox()
        self.voice.addItems(EDGE_VOICES)
        form.addWidget(self.voice, 0, 1)
        form.addWidget(QLabel("Tốc độ"), 1, 0)
        self.speed = speed_control()
        form.addWidget(self.speed, 1, 1)
        self.settings_card.content_layout.addLayout(form)

    def settings(self) -> dict[str, Any]:
        voice = self.voice.currentText()
        return {
            "provider": self.provider_name,
            "voice": voice,
            "speed": self.speed.value(),
            "single_voice": True,
            **self.profile_settings({"__shared__": {"voice": voice}}),
        }
