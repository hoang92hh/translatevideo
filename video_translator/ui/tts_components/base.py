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


DEDICATED_SPEAKERS = {"SPEAKER_00", "SPEAKER_01", "SPEAKER_02"}


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
        self._speakers: tuple[str, ...] = ()
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.root.setSpacing(12)
        self.settings_card = Card(title, subtitle)
        self.root.addWidget(self.settings_card)
        self.speaker_card = Card(
            "Giọng theo người nói",
            "Ba speaker đầu dùng cấu hình riêng; mọi speaker còn lại dùng chung một cấu hình.",
        )
        self.speaker_container = QWidget()
        self.speaker_layout = QGridLayout(self.speaker_container)
        self.speaker_layout.setContentsMargins(0, 0, 0, 0)
        self.speaker_layout.setSpacing(8)
        self.speaker_card.content_layout.addWidget(self.speaker_container)
        note = QLabel(license_note)
        note.setObjectName("muted")
        note.setWordWrap(True)
        self.speaker_card.content_layout.addWidget(note)
        self.root.addWidget(self.speaker_card)

    def set_speakers(self, speakers: tuple[str, ...]) -> None:
        if speakers == self._speakers:
            return
        self._speakers = speakers
        while self.speaker_layout.count():
            item = self.speaker_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._build_speaker_controls()

    def speaker_rows(self) -> list[tuple[str, str]]:
        dedicated = [item for item in ("SPEAKER_00", "SPEAKER_01", "SPEAKER_02") if item in self._speakers]
        rows = [(speaker, speaker) for speaker in dedicated]
        if any(item not in DEDICATED_SPEAKERS for item in self._speakers):
            rows.append(("__shared__", "SPEAKER_03 trở đi / khác"))
        return rows

    def show_no_speakers(self, columns: int = 3) -> bool:
        if self._speakers:
            return False
        label = QLabel("Chưa có speaker từ output Step 3 đang chọn.")
        label.setObjectName("muted")
        self.speaker_layout.addWidget(label, 0, 0, 1, columns)
        return True

    @abstractmethod
    def _build_speaker_controls(self) -> None:
        raise NotImplementedError

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
        self.settings_card.content_layout.addLayout(form)

    def _build_speaker_controls(self) -> None:
        self._profile_controls = {}
        if self.show_no_speakers():
            return
        self.speaker_layout.addWidget(QLabel("Speaker"), 0, 0)
        self.speaker_layout.addWidget(QLabel("Giọng nền/preset"), 0, 1)
        self.speaker_layout.addWidget(QLabel("File giọng tham chiếu"), 0, 2)
        for row, (key, label_text) in enumerate(self.speaker_rows(), start=1):
            voice = QComboBox()
            voice.addItems(self.voices)
            voice.setCurrentText(self.default_voice)
            reference = FilePicker()
            self.speaker_layout.addWidget(QLabel(label_text), row, 0)
            self.speaker_layout.addWidget(voice, row, 1)
            self.speaker_layout.addWidget(reference, row, 2)
            self._profile_controls[key] = (voice, reference)

    def settings(self) -> dict[str, Any]:
        profiles: dict[str, dict[str, str]] = {}
        shared: dict[str, str] = {}
        for key, (voice, reference) in self._profile_controls.items():
            profile = {"voice": voice.currentText(), "reference_voice": reference.value()}
            if key == "__shared__":
                shared = profile
            else:
                profiles[key] = profile
        return {
            "provider": self.provider_name,
            "voice": self.voice.currentText(),
            "speed": self.speed.value(),
            "device": self.device.currentText(),
            "voice_consent": self.consent.isChecked(),
            "speaker_profiles": profiles,
            "shared_speaker_profile": shared,
        }
