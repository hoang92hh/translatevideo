from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from ...config.tts import PIPER_PROVIDER, PROVIDER_LICENSE_NOTES
from .base import TtsComponentBase


def native_parameter(minimum: float, maximum: float, value: float) -> QDoubleSpinBox:
    control = QDoubleSpinBox()
    control.setRange(minimum, maximum)
    control.setDecimals(3)
    control.setSingleStep(0.05)
    control.setValue(value)
    return control


class PiperModelPicker(QWidget):
    config_loaded = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.config: dict[str, Any] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.field = QLineEdit()
        self.field.setPlaceholderText("Chọn voice model Piper .onnx")
        self.field.editingFinished.connect(self._load_config)
        browse = QPushButton("Chọn model…")
        browse.clicked.connect(self._browse)
        layout.addWidget(self.field, 1)
        layout.addWidget(browse)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn model Piper",
            "",
            "Piper model (*.onnx);;Tất cả file (*)",
        )
        if path:
            self.field.setText(path)
            self._load_config()

    def _load_config(self) -> None:
        self.config = {}
        model_path = self.value()
        if model_path:
            try:
                payload = json.loads(Path(f"{model_path}.json").read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    self.config = payload
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        self.config_loaded.emit(self.config)

    def value(self) -> str:
        return self.field.text().strip()


class PiperTtsComponent(TtsComponentBase):
    provider_name = PIPER_PROVIDER

    def __init__(self) -> None:
        self._profile_controls: dict[str, tuple[PiperModelPicker, QComboBox]] = {}
        self._native_defaults_loaded = False
        super().__init__(
            "Piper TTS",
            "TTS local bằng voice model ONNX cố định; không sử dụng audio giọng tham chiếu.",
            PROVIDER_LICENSE_NOTES[PIPER_PROVIDER],
        )
        form = QGridLayout()
        self.length_scale = native_parameter(0.1, 4.0, 1.0)
        self.length_scale.setToolTip("Nhỏ hơn 1 nói nhanh hơn; lớn hơn 1 nói chậm hơn.")
        self.noise_scale = native_parameter(0.0, 2.0, 0.667)
        self.noise_scale.setToolTip("Độ biến thiên của bộ sinh âm thanh.")
        self.noise_w_scale = native_parameter(0.0, 2.0, 0.8)
        self.noise_w_scale.setToolTip("Độ biến thiên trường độ âm vị.")
        self.volume = native_parameter(0.0, 4.0, 1.0)
        self.normalize_audio = QCheckBox("Chuẩn hóa biên độ đầu ra")
        self.normalize_audio.setChecked(True)
        controls = (
            ("Độ dài âm vị (length scale)", self.length_scale),
            ("Độ biến thiên (noise scale)", self.noise_scale),
            ("Biến thiên trường độ (noise width)", self.noise_w_scale),
            ("Âm lượng", self.volume),
        )
        for row, (label, control) in enumerate(controls):
            form.addWidget(QLabel(label), row, 0)
            form.addWidget(control, row, 1)
        form.addWidget(self.normalize_audio, len(controls), 0, 1, 2)
        device = QLabel("CPU (ONNX)")
        device.setObjectName("inputValue")
        form.addWidget(QLabel("Thiết bị"), len(controls) + 1, 0)
        form.addWidget(device, len(controls) + 1, 1)
        self.settings_card.content_layout.addLayout(form)

    def _build_speaker_controls(self) -> None:
        self._profile_controls = {}
        if self.show_no_speakers(3):
            return
        self.speaker_layout.addWidget(QLabel("Speaker"), 0, 0)
        self.speaker_layout.addWidget(QLabel("Voice model Piper (.onnx)"), 0, 1)
        self.speaker_layout.addWidget(QLabel("Giọng trong model"), 0, 2)
        for row, (key, label_text) in enumerate(self.speaker_rows(), start=1):
            model = PiperModelPicker()
            model_speaker = QComboBox()
            model_speaker.addItem("Mặc định của model", None)
            model.config_loaded.connect(
                lambda config, target=model_speaker: self._apply_model_config(config, target)
            )
            self.speaker_layout.addWidget(QLabel(label_text), row, 0)
            self.speaker_layout.addWidget(model, row, 1)
            self.speaker_layout.addWidget(model_speaker, row, 2)
            self._profile_controls[key] = (model, model_speaker)

    def _apply_model_config(self, config: dict[str, Any], speaker: QComboBox) -> None:
        current_id = speaker.currentData()
        speaker.blockSignals(True)
        speaker.clear()
        speaker.addItem("Mặc định của model", None)
        speaker_map = config.get("speaker_id_map", {})
        if isinstance(speaker_map, dict):
            valid_speakers: list[tuple[str, int]] = []
            for name, speaker_id in speaker_map.items():
                try:
                    valid_speakers.append((str(name), int(speaker_id)))
                except (TypeError, ValueError):
                    continue
            for name, speaker_id in sorted(valid_speakers, key=lambda item: item[1]):
                speaker.addItem(f"{name} (ID {speaker_id})", speaker_id)
        index = speaker.findData(current_id)
        speaker.setCurrentIndex(index if index >= 0 else 0)
        speaker.blockSignals(False)

        if not self._native_defaults_loaded:
            inference = config.get("inference", {})
            if isinstance(inference, dict):
                try:
                    self.length_scale.setValue(float(inference.get("length_scale", 1.0)))
                    self.noise_scale.setValue(float(inference.get("noise_scale", 0.667)))
                    self.noise_w_scale.setValue(float(inference.get("noise_w", 0.8)))
                    self._native_defaults_loaded = True
                except (TypeError, ValueError):
                    pass

    def settings(self) -> dict[str, Any]:
        profiles: dict[str, dict[str, object]] = {}
        shared: dict[str, object] = {}
        for key, (picker, model_speaker) in self._profile_controls.items():
            path = picker.value()
            profile: dict[str, object] = {
                "voice": path,
                "model_path": path,
                "piper_speaker_id": model_speaker.currentData(),
            }
            if key == "__shared__":
                shared = profile
            else:
                profiles[key] = profile
        return {
            "provider": self.provider_name,
            "voice": "",
            "speed": 1.0,
            "device": "CPU",
            "length_scale": self.length_scale.value(),
            "noise_scale": self.noise_scale.value(),
            "noise_w_scale": self.noise_w_scale.value(),
            "volume": self.volume.value(),
            "normalize_audio": self.normalize_audio.isChecked(),
            "speaker_profiles": profiles,
            "shared_speaker_profile": shared,
        }
