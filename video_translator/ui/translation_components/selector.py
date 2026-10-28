from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...config.gemini import GEMINI_DEFAULT_MODEL, GEMINI_MODEL_IDS, GEMINI_PROVIDER_NAME, gemini_model_note
from ...config.prompt_templates import (
    DEFAULT_PROMPT_TEMPLATE_ID,
    GEMINI_LEGACY_TEMPLATE_IDS,
    list_prompt_templates,
)
from ...config.translation import PROPER_NAME_MODE_AUTO, PROPER_NAME_MODES
from ...config.web_translation import (
    CHATGPT_WEB_PROVIDER_NAME,
    CLAUDE_WEB_PROVIDER_NAME,
    WEB_CURRENT_MODEL,
    project_url_error,
)
from ...errors import UserFacingError
from ...common.services.credential_service import GOOGLE_GEMINI, CredentialService
from ...infrastructure.web_bridge_service import web_bridge_service
from ..components import Card
from ..settings_dialog import ProviderSettingsDialog


class _ProviderComponent(QWidget):
    provider_name = ""

    def settings(self) -> dict[str, Any]:
        raise NotImplementedError

    def refresh_status(self) -> None:
        return

    def is_ready(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""


class _CommonTranslationComponent(_ProviderComponent):
    def _add_common_controls(self, form: QFormLayout) -> None:
        self.batch_size = QSpinBox()
        self.batch_size.setRange(1, 1000)
        self.batch_size.setValue(250)
        form.addRow("Segments / batch", self.batch_size)
        self.proper_name_mode = QComboBox()
        self.proper_name_mode.addItems(PROPER_NAME_MODES)
        self.proper_name_mode.setCurrentText(PROPER_NAME_MODE_AUTO)
        form.addRow("Quy tắc tên riêng", self.proper_name_mode)
        self.all_project_segments = QCheckBox(
            "Dịch toàn bộ segment của project trong một lượt"
        )
        self.all_project_segments.setChecked(True)
        self.all_project_segments.toggled.connect(self.batch_size.setDisabled)
        form.addRow("Phạm vi dịch", self.all_project_segments)
        self.batch_size.setDisabled(self.all_project_segments.isChecked())

    def _common_settings(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "batch_size": self.batch_size.value(),
            "proper_name_mode": self.proper_name_mode.currentText(),
            "all_project_segments": self.all_project_segments.isChecked(),
        }


class _GeminiComponent(_CommonTranslationComponent):
    provider_name = GEMINI_PROVIDER_NAME
    credentials_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        card = Card("Google Gemini", "Dùng Gemini API; credential được lưu dùng chung cho toàn ứng dụng.")
        status_row = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        button = QPushButton("Mở cài đặt API")
        button.clicked.connect(self._open_settings)
        status_row.addWidget(self.status, 1)
        status_row.addWidget(button)
        card.content_layout.addLayout(status_row)
        form = QFormLayout()
        self.model = QComboBox()
        self.model.addItems(GEMINI_MODEL_IDS)
        self.model.setCurrentText(GEMINI_DEFAULT_MODEL)
        self.model.currentTextChanged.connect(self._update_note)
        form.addRow("Model", self.model)
        self._add_common_controls(form)
        card.content_layout.addLayout(form)
        self.model_note = QLabel()
        self.model_note.setObjectName("muted")
        self.model_note.setWordWrap(True)
        card.content_layout.addWidget(self.model_note)
        root.addWidget(card)
        self._configured = False
        self._update_note(self.model.currentText())
        self.refresh_status()

    def _open_settings(self) -> None:
        dialog = ProviderSettingsDialog(self)
        dialog.credentials_changed.connect(self.credentials_changed)
        dialog.exec()
        self.refresh_status()
        self.credentials_changed.emit()

    def _update_note(self, model: str) -> None:
        self.model_note.setText(gemini_model_note(model))

    def refresh_status(self) -> None:
        try:
            info = CredentialService.get(GOOGLE_GEMINI, include_value=False)
        except UserFacingError as exc:
            self._configured = False
            self.status.setText(f"Không thể kiểm tra · {exc.error_message.message}")
        else:
            self._configured = info.configured
            self.status.setText(f"Đã cấu hình · {info.source}" if info.configured else "Chưa cấu hình Google API key")

    def is_ready(self) -> bool:
        return self._configured

    def unavailable_reason(self) -> str:
        return "Cấu hình Google API key trong Cài đặt → API & Providers."

    def settings(self) -> dict[str, Any]:
        return {**self._common_settings(), "model": self.model.currentText()}


class _WebComponent(_CommonTranslationComponent):
    def __init__(self, provider_name: str, website: str) -> None:
        super().__init__()
        self.provider_name = provider_name
        self.website = website
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        card = Card(provider_name, f"Dùng phiên đăng nhập hiện tại trên {website} qua Chrome extension; không đọc cookie.")
        self.status = QLabel()
        self.status.setWordWrap(True)
        card.content_layout.addWidget(self.status)
        form = QFormLayout()
        self.model = QComboBox()
        self.model.addItem(WEB_CURRENT_MODEL)
        form.addRow("Model", self.model)
        self.project_url = QLineEdit()
        self.project_url.setPlaceholderText(f"URL Project trên {website}")
        form.addRow("Project URL", self.project_url)
        self._add_common_controls(form)
        card.content_layout.addLayout(form)
        note = QLabel(
            "Trước khi chạy: nạp extension, đăng nhập, chỉ để một tab của provider và để ô nhập trống. "
            "Model được chọn trực tiếp trên trang web."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        card.content_layout.addWidget(note)
        root.addWidget(card)
        self._ready = False
        self.refresh_status()

    def refresh_status(self) -> None:
        status = web_bridge_service.status()
        self._ready = bool(status["running"] and status["connected"])
        if status["error"]:
            self.status.setText(f"Bridge không thể khởi động · {status['error']}")
        elif self._ready:
            self.status.setText("Chrome extension đã kết nối · sẵn sàng nhận job")
        else:
            self.status.setText("Chưa thấy Chrome extension kết nối tới 127.0.0.1:8765")

    def is_ready(self) -> bool:
        return self._ready and not project_url_error(self.provider_name, self.project_url.text())

    def unavailable_reason(self) -> str:
        url_error = project_url_error(self.provider_name, self.project_url.text())
        if url_error:
            return url_error
        return f"Nạp MyTool Bridge Extension và mở một tab {self.website} đã đăng nhập."

    def settings(self) -> dict[str, Any]:
        return {
            **self._common_settings(),
            "model": self.model.currentText(),
            "project_url": self.project_url.text().strip(),
        }


class TranslationProviderSelector(QWidget):
    provider_changed = Signal(str)
    credentials_changed = Signal()
    status_changed = Signal()
    project_url_changed = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        web_bridge_service.start()
        self.components: list[_ProviderComponent] = [
            _GeminiComponent(),
            _WebComponent(CHATGPT_WEB_PROVIDER_NAME, "chatgpt.com"),
            _WebComponent(CLAUDE_WEB_PROVIDER_NAME, "claude.ai"),
        ]
        self.components[0].credentials_changed.connect(self.credentials_changed.emit)
        self._default_provider = GEMINI_PROVIDER_NAME
        self._prompt_by_provider: dict[str, str] = {
            provider: DEFAULT_PROMPT_TEMPLATE_ID
            for provider in (
                GEMINI_PROVIDER_NAME,
                CHATGPT_WEB_PROVIDER_NAME,
                CLAUDE_WEB_PROVIDER_NAME,
            )
        }
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
            if isinstance(component, _WebComponent):
                component.project_url.textChanged.connect(
                    lambda _text: self.status_changed.emit()
                )
                component.project_url.editingFinished.connect(
                    lambda target=component: self.project_url_changed.emit(
                        target.provider_name, target.project_url.text().strip()
                    )
                )
        header.addStretch()
        self.default_button = QPushButton("Đặt làm mặc định")
        self.default_button.clicked.connect(self._set_current_default)
        header.addWidget(self.default_button)
        root.addLayout(header)
        self.default_label = QLabel()
        self.default_label.setObjectName("muted")
        root.addWidget(self.default_label)
        prompt_row = QHBoxLayout()
        prompt_row.addWidget(QLabel("Prompt template"))
        self.prompt_template = QComboBox()
        self.prompt_template.currentIndexChanged.connect(self._prompt_selected)
        prompt_row.addWidget(self.prompt_template, 1)
        root.addLayout(prompt_row)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setFixedHeight(350)
        scroll.setWidget(self.stack)
        root.addWidget(scroll)
        self.select_index(0)
        self._refresh_button_labels()
        self._last_ready = self.is_ready()
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(2000)
        self.status_timer.timeout.connect(self._poll_status)
        self.status_timer.start()

    def select_index(self, index: int) -> None:
        if index < 0 or index >= len(self.components):
            index = 0
        self.stack.setCurrentIndex(index)
        self.button_group.button(index).setChecked(True)
        component = self.current_component()
        if isinstance(component, _WebComponent):
            component.refresh_status()
        if hasattr(self, "_last_ready"):
            self._last_ready = component.is_ready()
        if hasattr(self, "prompt_template"):
            self._reload_prompt_templates()
        self.provider_changed.emit(component.provider_name)

    def _poll_status(self) -> None:
        component = self.current_component()
        if not isinstance(component, _WebComponent):
            return
        component.refresh_status()
        ready = component.is_ready()
        if ready != self._last_ready:
            self._last_ready = ready
            self.status_changed.emit()

    def _set_current_default(self) -> None:
        self._default_provider = self.current_component().provider_name
        self._refresh_button_labels()

    def _refresh_button_labels(self) -> None:
        for provider, button in self.buttons.items():
            button.setText(("â˜… " if provider == self._default_provider else "") + provider)
        self.default_label.setText(f"Provider dịch mặc định: {self._default_provider}")

    def current_component(self) -> _ProviderComponent:
        return self.components[self.stack.currentIndex()]

    def settings(self) -> dict[str, Any]:
        component = next(
            item
            for item in self.components
            if item.provider_name == self._default_provider
        )
        return {
            **component.settings(),
            "prompt_template": self._prompt_by_provider.get(
                self._default_provider,
                DEFAULT_PROMPT_TEMPLATE_ID,
            ),
        }

    def set_default_provider(self, provider: str) -> _ProviderComponent:
        index = next(
            (
                index
                for index, component in enumerate(self.components)
                if component.provider_name == provider
            ),
            0,
        )
        self.select_index(index)
        self._default_provider = self.components[index].provider_name
        self._refresh_button_labels()
        return self.components[index]

    def set_prompt_template(self, template_id: str) -> None:
        provider = self.current_component().provider_name
        self._prompt_by_provider[provider] = (
            template_id.strip() or DEFAULT_PROMPT_TEMPLATE_ID
        )
        self._reload_prompt_templates()

    def _prompt_selected(self, index: int) -> None:
        if index < 0:
            return
        template_id = str(self.prompt_template.itemData(index) or "").strip()
        if template_id:
            self._prompt_by_provider[
                self.current_component().provider_name
            ] = template_id

    def _reload_prompt_templates(self) -> None:
        provider = self.current_component().provider_name
        selected = self._prompt_by_provider.get(
            provider,
            DEFAULT_PROMPT_TEMPLATE_ID,
        )
        if provider == GEMINI_PROVIDER_NAME:
            selected = GEMINI_LEGACY_TEMPLATE_IDS.get(selected, selected)
        self.prompt_template.blockSignals(True)
        self.prompt_template.clear()
        try:
            templates = list_prompt_templates(provider)
        except (OSError, ValueError):
            templates = []
        for template in templates:
            self.prompt_template.addItem(template.name, template.template_id)
        index = self.prompt_template.findData(selected)
        if index < 0:
            self.prompt_template.insertItem(0, f"[Không tìm thấy] {selected}", selected)
            index = 0
        self.prompt_template.setCurrentIndex(index)
        self.prompt_template.blockSignals(False)

    def set_project_urls(self, urls: dict[str, str]) -> None:
        for component in self.components:
            if isinstance(component, _WebComponent):
                component.project_url.setText(str(urls.get(component.provider_name, "")))
        self._last_ready = self.is_ready()
        self.status_changed.emit()

    def refresh_status(self) -> None:
        component = self.current_component()
        if isinstance(component, _WebComponent):
            component.refresh_status()

    def is_ready(self) -> bool:
        return self.current_component().is_ready()

    def unavailable_reason(self) -> str:
        return self.current_component().unavailable_reason()
