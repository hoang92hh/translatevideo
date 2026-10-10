from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from ...config.tts import (
    EDGE_TTS_PROVIDER,
    MELO_OPENVOICE_PROVIDER,
    PIPER_PROVIDER,
    VIENEU_PROVIDER,
    XTTS_V2_PROVIDER,
)
from ...config.tts_preferences import default_tts_provider, set_default_tts_provider
from .base import TtsComponentBase
from .edge import EdgeTtsComponent
from .melo_openvoice import MeloOpenVoiceTtsComponent
from .piper import PiperTtsComponent
from .vieneu import VieNeuTtsComponent


class TtsComponentSelector(QWidget):
    provider_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.components: list[TtsComponentBase] = [
            VieNeuTtsComponent(),
            PiperTtsComponent(),
            MeloOpenVoiceTtsComponent(),
            EdgeTtsComponent(),
        ]
        self.buttons: dict[str, QPushButton] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        header = QHBoxLayout()
        self.button_group = QButtonGroup(self)
        self.button_group.setExclusive(True)
        self.stack = QStackedWidget()
        for index, component in enumerate(self.components):
            button = QPushButton()
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, target=index: self.select_index(target))
            self.button_group.addButton(button, index)
            self.buttons[component.provider_name] = button
            header.addWidget(button)
            self.stack.addWidget(component)
        unavailable = QPushButton("XTTS-v2")
        unavailable.setEnabled(False)
        unavailable.setToolTip(XTTS_V2_PROVIDER)
        header.addWidget(unavailable)
        header.addStretch()
        self.default_button = QPushButton("Đặt làm mặc định")
        self.default_button.clicked.connect(self._set_current_default)
        header.addWidget(self.default_button)
        root.addLayout(header)
        self.default_label = QLabel()
        self.default_label.setObjectName("muted")
        root.addWidget(self.default_label)
        root.addWidget(self.stack)
        saved = default_tts_provider()
        index = next(
            (position for position, item in enumerate(self.components) if item.provider_name == saved),
            0,
        )
        self._default_provider = self.components[index].provider_name
        self.select_index(index)
        self._refresh_button_labels()

    def select_index(self, index: int) -> None:
        if index < 0 or index >= len(self.components):
            index = 0
        self.stack.setCurrentIndex(index)
        self.button_group.button(index).setChecked(True)
        self.provider_changed.emit(self.components[index].provider_name)

    def _set_current_default(self) -> None:
        self._default_provider = self.current_component().provider_name
        set_default_tts_provider(self._default_provider)
        self._refresh_button_labels()

    def _refresh_button_labels(self) -> None:
        short_names = {
            VIENEU_PROVIDER: "VieNeu-TTS",
            PIPER_PROVIDER: "Piper TTS",
            MELO_OPENVOICE_PROVIDER: "Melo + OpenVoice",
            EDGE_TTS_PROVIDER: "Edge TTS",
        }
        for provider, button in self.buttons.items():
            prefix = "★ " if provider == self._default_provider else ""
            button.setText(prefix + short_names.get(provider, provider))
        self.default_label.setText(f"TTS mặc định khi mở ứng dụng: {self._default_provider}")

    def current_component(self) -> TtsComponentBase:
        return self.components[self.stack.currentIndex()]

    def settings(self) -> dict[str, Any]:
        return self.current_component().settings()

    def set_speakers(self, speakers: tuple[str, ...]) -> None:
        for component in self.components:
            component.set_speakers(speakers)
