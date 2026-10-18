from __future__ import annotations

from abc import abstractmethod
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..components import Card, FilePicker
from ...config.speaker_profiles import LEGACY_SHARED_PROFILE


def speed_control() -> QDoubleSpinBox:
    control = QDoubleSpinBox()
    control.setRange(0.25, 4.0)
    control.setSingleStep(0.05)
    control.setValue(1.0)
    return control


def device_control() -> QComboBox:
    control = QComboBox()
    control.addItems(("Auto", "CPU", "GPU"))
    return control


class TtsComponentBase(QWidget):
    provider_name = ""

    def __init__(self, title: str, subtitle: str, license_note: str) -> None:
        super().__init__()
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.root.setSpacing(12)
        self.settings_card = Card(title, subtitle)
        self.root.addWidget(self.settings_card)
        note = QLabel(license_note)
        note.setObjectName("muted")
        note.setWordWrap(True)
        self.root.addWidget(note)

    def set_speakers(
        self,
        speakers: tuple[str, ...],
        dialogue_profile: dict[str, Any] | None = None,
    ) -> None:
        # Speaker metadata is retained for translation context. This edition
        # intentionally applies one shared voice to every speaker.
        del speakers, dialogue_profile

    def profile_settings(
        self,
        profiles_by_key: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        shared = profiles_by_key.get(LEGACY_SHARED_PROFILE, {})
        return {
            "speaker_profiles": {},
            "speaker_group_profiles": {},
            "speaker_profile_assignments": {},
            "shared_speaker_profile": shared,
        }

    @abstractmethod
    def settings(self) -> dict[str, Any]:
        raise NotImplementedError


class ReferenceTtsComponentBase(TtsComponentBase):
    def __init__(
        self,
        provider_name: str,
        title: str,
        subtitle: str,
        license_note: str,
        voices: tuple[str, ...],
        default_voice: str,
        voice_label: str,
    ) -> None:
        self.provider_name = provider_name
        self.voices = voices
        self.default_voice = default_voice
        self._profile_controls: dict[str, tuple[QComboBox, FilePicker]] = {}
        super().__init__(title, subtitle, license_note)
        form = QGridLayout()
        form.addWidget(QLabel(voice_label), 0, 0)
        self.voice = QComboBox()
        self.voice.addItems(voices)
        self.voice.setCurrentText(default_voice)
        form.addWidget(self.voice, 0, 1)
        form.addWidget(QLabel("Tốc độ"), 1, 0)
        self.speed = speed_control()
        form.addWidget(self.speed, 1, 1)
        form.addWidget(QLabel("Thiết bị"), 2, 0)
        self.device = device_control()
        form.addWidget(self.device, 2, 1)
        self.consent = QCheckBox("Tôi có quyền sử dụng giọng tham chiếu")
        form.addWidget(self.consent, 3, 0, 1, 2)
        form.addWidget(QLabel("File giọng tham chiếu"), 4, 0)
        self.reference = FilePicker()
        form.addWidget(self.reference, 4, 1)
        self.settings_card.content_layout.addLayout(form)

    def settings(self) -> dict[str, Any]:
        voice = self.voice.currentText()
        reference = self.reference.value()
        profiles = {
            LEGACY_SHARED_PROFILE: {
                "voice": voice,
                "reference_voice": reference,
            }
        }
        return {
            "provider": self.provider_name,
            "voice": voice,
            "reference_voice": reference,
            "speed": self.speed.value(),
            "device": self.device.currentText(),
            "voice_consent": self.consent.isChecked(),
            "single_voice": True,
            **self.profile_settings(profiles),
        }
