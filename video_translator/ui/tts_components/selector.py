from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...config.tts import XTTS_V2_PROVIDER
from ...config.tts_preferences import default_tts_provider, set_default_tts_provider
from ...steps.step4_tts.providers import all_manifests, load_component
from .base import TtsComponentBase


class TtsComponentSelector(QWidget):
    provider_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.manifests = all_manifests()
        self.components: list[TtsComponentBase] = [
            load_component(manifest)() for manifest in self.manifests
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
        self.component_scroll = QScrollArea()
        self.component_scroll.setWidgetResizable(True)
        self.component_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.component_scroll.setFixedHeight(460)
        self.component_scroll.setWidget(self.stack)
        root.addWidget(self.component_scroll)
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
        names = {manifest.id: manifest.name for manifest in self.manifests}
        for provider, button in self.buttons.items():
            prefix = "â˜… " if provider == self._default_provider else ""
            button.setText(prefix + names.get(provider, provider))
        self.default_label.setText(f"TTS mặc định khi mở ứng dụng: {self._default_provider}")

    def current_component(self) -> TtsComponentBase:
        return self.components[self.stack.currentIndex()]

    def settings(self) -> dict[str, Any]:
        return self.current_component().settings()

    def set_speakers(
        self,
        speakers: tuple[str, ...],
        dialogue_profile: dict[str, Any] | None = None,
    ) -> None:
        for component in self.components:
            component.set_speakers(speakers, dialogue_profile)
