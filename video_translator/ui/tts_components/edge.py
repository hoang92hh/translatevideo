from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QComboBox, QGridLayout, QLabel

from ...config.tts import EDGE_TTS_PROVIDER, EDGE_VOICES, PROVIDER_LICENSE_NOTES
from .base import TtsComponentBase, speed_control


class EdgeTtsComponent(TtsComponentBase):
    provider_name = EDGE_TTS_PROVIDER

    def __init__(self) -> None:
        self._profile_controls: dict[str, QComboBox] = {}
        super().__init__(
            "Edge TTS",
            "TTS online; mỗi speaker chọn một voice preset.",
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

    def _build_speaker_controls(self) -> None:
        self._profile_controls = {}
        if self.show_no_speakers(2):
            return
        self.speaker_layout.addWidget(QLabel("Speaker"), 0, 0)
        self.speaker_layout.addWidget(QLabel("Voice preset"), 0, 1)
        for row, (key, label_text) in enumerate(self.speaker_rows(), start=1):
            voice = QComboBox()
            voice.addItems(EDGE_VOICES)
            self.speaker_layout.addWidget(QLabel(label_text), row, 0)
            self.speaker_layout.addWidget(voice, row, 1)
            self._profile_controls[key] = voice

    def settings(self) -> dict[str, Any]:
        profiles: dict[str, dict[str, str]] = {}
        shared: dict[str, str] = {}
        for key, voice in self._profile_controls.items():
            profile = {"voice": voice.currentText()}
            if key == "__shared__":
                shared = profile
            else:
                profiles[key] = profile
        return {
            "provider": self.provider_name,
            "voice": self.voice.currentText(),
            "speed": self.speed.value(),
            "speaker_profiles": profiles,
            "shared_speaker_profile": shared,
        }
