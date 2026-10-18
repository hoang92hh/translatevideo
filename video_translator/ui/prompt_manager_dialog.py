from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..config.gemini import GEMINI_PROVIDER_NAME
from ..config.prompt_templates import (
    DEFAULT_PROMPT_TEMPLATE_ID,
    PromptTemplate,
    delete_prompt_template,
    list_prompt_templates,
    save_prompt_template,
)
from ..config.web_translation import CHATGPT_WEB_PROVIDER_NAME, CLAUDE_WEB_PROVIDER_NAME


class PromptManagerDialog(QDialog):
    templates_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Quản lý Prompt")
        self.resize(1100, 760)
        self.setMinimumSize(850, 600)
        self._current_template_id = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)
        title = QLabel("Quản lý Prompt Step 3")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        description = QLabel(
            "Mỗi provider có thư viện prompt riêng. Mỗi batch gửi đúng một prompt để AI "
            "tự phân tích, dịch, kiểm tra và trả file kết quả cuối cùng."
        )
        description.setObjectName("muted")
        description.setWordWrap(True)
        root.addWidget(description)

        provider_row = QHBoxLayout()
        provider_row.addWidget(QLabel("Provider"))
        self.provider = QComboBox()
        self.provider.addItems(
            (GEMINI_PROVIDER_NAME, CHATGPT_WEB_PROVIDER_NAME, CLAUDE_WEB_PROVIDER_NAME)
        )
        self.provider.currentTextChanged.connect(self._reload_templates)
        provider_row.addWidget(self.provider, 1)
        root.addLayout(provider_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.template_list = QListWidget()
        self.template_list.currentItemChanged.connect(self._template_selected)
        left_layout.addWidget(self.template_list, 1)
        list_actions = QHBoxLayout()
        new_button = QPushButton("Thêm mới")
        new_button.clicked.connect(self._new_template)
        self.delete_button = QPushButton("Xóa")
        self.delete_button.clicked.connect(self._delete_template)
        list_actions.addWidget(new_button)
        list_actions.addWidget(self.delete_button)
        left_layout.addLayout(list_actions)
        splitter.addWidget(left)

        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(12, 0, 0, 0)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Tên prompt"))
        self.name = QLineEdit()
        name_row.addWidget(self.name, 1)
        editor_layout.addLayout(name_row)
        self.prompt_editor = QTextEdit()
        self.prompt_editor.setAcceptRichText(False)
        self.prompt_editor.setPlaceholderText(
            "Yêu cầu phân tích, dịch, tự kiểm tra và OUTPUT CONTRACT…"
        )
        editor_layout.addWidget(self.prompt_editor, 1)
        self.save_button = QPushButton("Lưu prompt")
        self.save_button.setObjectName("primaryButton")
        self.save_button.clicked.connect(self._save_template)
        editor_layout.addWidget(self.save_button)
        splitter.addWidget(editor)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, 1)

        actions = QHBoxLayout()
        actions.addStretch()
        close_button = QPushButton("Đóng")
        close_button.clicked.connect(self.accept)
        actions.addWidget(close_button)
        root.addLayout(actions)
        self._reload_templates()

    def _reload_templates(self, _provider: str = "", select_id: str = "") -> None:
        provider = self.provider.currentText()
        self.template_list.clear()
        try:
            templates = list_prompt_templates(provider)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Không thể đọc prompt", str(exc))
            templates = []
        for template in templates:
            item = QListWidgetItem(template.name)
            item.setData(Qt.ItemDataRole.UserRole, template)
            item.setToolTip(str(template.path))
            self.template_list.addItem(item)
            if template.template_id == select_id:
                self.template_list.setCurrentItem(item)
        if self.template_list.currentRow() < 0 and self.template_list.count():
            self.template_list.setCurrentRow(0)
        elif not self.template_list.count():
            self._new_template()

    def _template_selected(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        template = current.data(Qt.ItemDataRole.UserRole) if current else None
        if not isinstance(template, PromptTemplate):
            return
        self._current_template_id = template.template_id
        self.name.setText(template.name)
        self.prompt_editor.setPlainText(template.prompt)
        self.delete_button.setEnabled(
            template.template_id != DEFAULT_PROMPT_TEMPLATE_ID
        )

    def _new_template(self) -> None:
        self.template_list.clearSelection()
        self._current_template_id = ""
        self.name.clear()
        self.prompt_editor.clear()
        self.delete_button.setEnabled(False)
        self.name.setFocus()

    def _save_template(self) -> None:
        try:
            template = save_prompt_template(
                self.provider.currentText(),
                self.name.text(),
                self.prompt_editor.toPlainText(),
                self._current_template_id,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Không thể lưu prompt", str(exc))
            return
        self._current_template_id = template.template_id
        self.templates_changed.emit(template.provider)
        self._reload_templates(select_id=template.template_id)

    def _delete_template(self) -> None:
        if not self._current_template_id:
            return
        answer = QMessageBox.question(
            self,
            "Xóa prompt",
            f"Xóa prompt “{self.name.text().strip()}” khỏi thư viện {self.provider.currentText()}?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_prompt_template(self.provider.currentText(), self._current_template_id)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Không thể xóa prompt", str(exc))
            return
        provider = self.provider.currentText()
        self.templates_changed.emit(provider)
        self._current_template_id = ""
        self._reload_templates()
