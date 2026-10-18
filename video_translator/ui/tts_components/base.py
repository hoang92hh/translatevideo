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
from ...config.speaker_profiles import LEGACY_SHARED_PROFILE, REFERENCE_SPEAKERS


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
        # Fixed speaker slots are configured by the component itself. The
        # incoming metadata remains available to providers that need it later.
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
        form.addWidget(QLabel("Tốc độ"), 0, 0)
        self.speed = speed_control()
        form.addWidget(self.speed, 0, 1)
        form.addWidget(QLabel("Thiết bị"), 1, 0)
        self.device = device_control()
        form.addWidget(self.device, 1, 1)
        self.consent = QCheckBox("Tôi có quyền sử dụng giọng tham chiếu")
        form.addWidget(self.consent, 2, 0, 1, 2)
        self.same_reference = QCheckBox("Dùng cùng một giọng cho SPEAKER_00–03")
        self.same_reference.setChecked(False)
        self.same_reference.toggled.connect(self._same_reference_toggled)
        form.addWidget(self.same_reference, 3, 0, 1, 2)
        row = 4
        for speaker_id in REFERENCE_SPEAKERS:
            speaker_voice = QComboBox()
            speaker_voice.addItems(voices)
            speaker_voice.setCurrentText(default_voice)
            reference = FilePicker()
            self._profile_controls[speaker_id] = (speaker_voice, reference)
            form.addWidget(QLabel(f"{speaker_id} — {voice_label}"), row, 0)
            form.addWidget(speaker_voice, row, 1)
            row += 1
            form.addWidget(QLabel(f"{speaker_id} — file tham chiếu"), row, 0)
            form.addWidget(reference, row, 1)
            row += 1
        source_voice, source_reference = self._profile_controls["SPEAKER_00"]
        source_voice.currentTextChanged.connect(self._sync_shared_profile)
        source_reference.field.textChanged.connect(self._sync_shared_profile)
        self.settings_card.content_layout.addLayout(form)

    def _sync_shared_profile(self, *_args: object) -> None:
        if not self.same_reference.isChecked():
            return
        source_voice, source_reference = self._profile_controls["SPEAKER_00"]
        for speaker_id in REFERENCE_SPEAKERS[1:]:
            voice, reference = self._profile_controls[speaker_id]
            voice.setCurrentText(source_voice.currentText())
            reference.field.setText(source_reference.value())

    def _same_reference_toggled(self, checked: bool) -> None:
        if checked:
            self._sync_shared_profile()
        for speaker_id in REFERENCE_SPEAKERS[1:]:
            voice, reference = self._profile_controls[speaker_id]
            voice.setDisabled(checked)
            reference.setDisabled(checked)

    def settings(self) -> dict[str, Any]:
        source_voice, source_reference = self._profile_controls["SPEAKER_00"]
        profiles: dict[str, dict[str, Any]] = {}
        for speaker_id in REFERENCE_SPEAKERS:
            voice_control, reference_control = self._profile_controls[speaker_id]
            if self.same_reference.isChecked():
                voice_control, reference_control = source_voice, source_reference
            profiles[speaker_id] = {
                "voice": voice_control.currentText(),
                "reference_voice": reference_control.value(),
            }
        voice = profiles["SPEAKER_00"]["voice"]
        reference = profiles["SPEAKER_00"]["reference_voice"]
        return {
            "provider": self.provider_name,
            "voice": voice,
            "reference_voice": reference,
            "speed": self.speed.value(),
            "device": self.device.currentText(),
            "voice_consent": self.consent.isChecked(),
            "single_voice": self.same_reference.isChecked(),
            "speaker_profiles": profiles,
            "speaker_group_profiles": {},
            "speaker_profile_assignments": {
                speaker_id: speaker_id for speaker_id in REFERENCE_SPEAKERS
            },
            "shared_speaker_profile": profiles["SPEAKER_03"],
        }
