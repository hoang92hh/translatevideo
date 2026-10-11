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
from ...config.speaker_profiles import (
    GROUP_LABELS,
    LEGACY_SHARED_PROFILE,
    MINOR_FEMALE_PROFILE,
    MINOR_MALE_PROFILE,
    MINOR_UNKNOWN_PROFILE,
    speaker_profile_assignments,
)


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
        self._dialogue_profile: dict[str, Any] = {}
        self._speaker_assignments: dict[str, str] = {}
        self._speaker_profiles: dict[str, dict[str, Any]] = {}
        self._classified_speakers = False
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.root.setSpacing(12)
        self.settings_card = Card(title, subtitle)
        self.root.addWidget(self.settings_card)
        self.speaker_card = Card(
            "Giọng theo người nói",
            "Nhân vật chính/quan trọng dùng giọng riêng; nhân vật phụ dùng chung giọng theo giới tính.",
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

    def set_speakers(
        self,
        speakers: tuple[str, ...],
        dialogue_profile: dict[str, Any] | None = None,
    ) -> None:
        resolved_profile = dict(dialogue_profile or {})
        if speakers == self._speakers and resolved_profile == self._dialogue_profile:
            return
        self._speakers = speakers
        self._dialogue_profile = resolved_profile
        (
            self._speaker_assignments,
            self._speaker_profiles,
            self._classified_speakers,
        ) = speaker_profile_assignments(speakers, resolved_profile)
        while self.speaker_layout.count():
            item = self.speaker_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._build_speaker_controls()

    def speaker_rows(self) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        for speaker_id in self._speakers:
            if self._speaker_assignments.get(speaker_id) != speaker_id:
                continue
            profile = self._speaker_profiles.get(speaker_id, {})
            details = [speaker_id]
            name = str(profile.get("character_name", "")).strip()
            importance = str(profile.get("importance", "")).strip().lower()
            gender = str(profile.get("gender", "")).strip().lower()
            if name:
                details.append(name)
            if importance:
                details.append({"main": "chính", "supporting": "quan trọng"}.get(importance, importance))
            if gender:
                details.append({"male": "nam", "female": "nữ", "unknown": "chưa rõ"}.get(gender, gender))
            rows.append((speaker_id, " · ".join(details)))

        group_order = (
            MINOR_MALE_PROFILE,
            MINOR_FEMALE_PROFILE,
            MINOR_UNKNOWN_PROFILE,
            LEGACY_SHARED_PROFILE,
        )
        for group_key in group_order:
            members = [
                speaker_id
                for speaker_id in self._speakers
                if self._speaker_assignments.get(speaker_id) == group_key
            ]
            if members:
                rows.append((group_key, f"{GROUP_LABELS[group_key]} ({', '.join(members)})"))
        return rows

    def profile_settings(
        self,
        profiles_by_key: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        dedicated: dict[str, dict[str, Any]] = {}
        groups: dict[str, dict[str, Any]] = {}
        shared: dict[str, Any] = {}
        for key, profile in profiles_by_key.items():
            if key == LEGACY_SHARED_PROFILE:
                shared = profile
            elif key.startswith("__"):
                groups[key] = profile
            else:
                dedicated[key] = profile
        return {
            "speaker_profiles": dedicated,
            "speaker_group_profiles": groups,
            "speaker_profile_assignments": dict(self._speaker_assignments),
            "shared_speaker_profile": shared,
        }

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
        for key, (voice, reference) in self._profile_controls.items():
            profiles[key] = {"voice": voice.currentText(), "reference_voice": reference.value()}
        return {
            "provider": self.provider_name,
            "voice": self.voice.currentText(),
            "speed": self.speed.value(),
            "device": self.device.currentText(),
            "voice_consent": self.consent.isChecked(),
            **self.profile_settings(profiles),
        }
