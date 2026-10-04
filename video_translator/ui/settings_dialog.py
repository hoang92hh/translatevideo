from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config.gemini import GEMINI_CONNECTION_TEST_MODEL
from ..errors import UserFacingError
from ..services.credential_service import GOOGLE_GEMINI, CredentialService
from ..services.google_translation_service import GoogleTranslationService
from .components import Card


class ProviderTestWorker(QThread):
    completed = Signal(bool, str)

    def run(self) -> None:
        try:
            source = GoogleTranslationService.validate_connection(GEMINI_CONNECTION_TEST_MODEL)
        except UserFacingError as exc:
            self.completed.emit(False, exc.error_message.message)
        except Exception as exc:
            self.completed.emit(False, str(exc) or "Lỗi không xác định")
        else:
            self.completed.emit(True, f"Kết nối thành công qua {source}.")


class ProviderSettingsDialog(QDialog):
    credentials_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cài đặt — API & Providers")
        self.setMinimumWidth(680)
        self._test_worker: ProviderTestWorker | None = None

        root = QVBoxLayout(self)
        title = QLabel("API & Providers")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        description = QLabel(
            "Credential dùng chung cho toàn bộ TransLanguage và mọi project. "
            "API key được lưu trong kho bảo mật của hệ điều hành, không nằm trong project.json."
        )
        description.setObjectName("muted")
        description.setWordWrap(True)
        root.addWidget(description)

        card = Card("Google Gemini", "Provider dịch thuật của Step 3.")
        form = QFormLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        form.addRow("Trạng thái", self.status)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("Dán API key mới để lưu hoặc thay thế")
        form.addRow("Google API key", self.api_key)
        card.content_layout.addLayout(form)

        actions = QHBoxLayout()
        self.save_button = QPushButton("Lưu / Thay thế")
        self.save_button.clicked.connect(self._save)
        self.delete_button = QPushButton("Xóa key đã lưu")
        self.delete_button.clicked.connect(self._delete)
        self.test_button = QPushButton("Kiểm tra kết nối")
        self.test_button.clicked.connect(self._test)
        actions.addWidget(self.save_button)
        actions.addWidget(self.delete_button)
        actions.addWidget(self.test_button)
        actions.addStretch()
        card.content_layout.addLayout(actions)
        root.addWidget(card)

        note = QLabel(
            "Nếu không có key trong Windows Credential Locker, ứng dụng sẽ kiểm tra "
            "GEMINI_API_KEY rồi GOOGLE_API_KEY. Xóa key đã lưu không thể xóa biến môi trường."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._refresh_status()

    def _refresh_status(self) -> None:
        try:
            info = CredentialService.get(GOOGLE_GEMINI, include_value=False)
        except UserFacingError as exc:
            self.status.setText(f"Không thể kiểm tra · {exc.error_message.message}")
            self.delete_button.setEnabled(False)
            self.test_button.setEnabled(False)
            return
        if info.configured:
            self.status.setText(f"Đã cấu hình · {info.source}")
        else:
            self.status.setText("Chưa cấu hình")
        self.delete_button.setEnabled(info.source == "Windows Credential Locker")
        self.test_button.setEnabled(info.configured and self._test_worker is None)

    def _save(self) -> None:
        try:
            CredentialService.save(GOOGLE_GEMINI, self.api_key.text())
        except UserFacingError as exc:
            self._show_error(exc)
            return
        self.api_key.clear()
        self._refresh_status()
        self.credentials_changed.emit()
        QMessageBox.information(self, "Đã lưu", "Google API key đã được lưu an toàn cho toàn bộ tool.")

    def _delete(self) -> None:
        answer = QMessageBox.question(
            self,
            "Xóa Google API key",
            "Xóa Google API key đang lưu trong Windows Credential Locker?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            CredentialService.delete(GOOGLE_GEMINI)
        except UserFacingError as exc:
            self._show_error(exc)
            return
        self._refresh_status()
        self.credentials_changed.emit()

    def _test(self) -> None:
        if self._test_worker is not None:
            return
        self._set_actions_enabled(False)
        self.status.setText("Đang kiểm tra kết nối Google Gemini…")
        worker = ProviderTestWorker(self)
        self._test_worker = worker
        worker.completed.connect(self._test_completed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _test_completed(self, success: bool, message: str) -> None:
        self._test_worker = None
        self._set_actions_enabled(True)
        self._refresh_status()
        if success:
            QMessageBox.information(self, "Kết nối thành công", message)
        else:
            QMessageBox.warning(self, "Không thể kết nối", message)

    def _set_actions_enabled(self, enabled: bool) -> None:
        self.save_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)
        self.test_button.setEnabled(enabled)

    def _show_error(self, error: UserFacingError) -> None:
        message = error.error_message
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Critical)
        dialog.setWindowTitle(message.title)
        dialog.setText(message.message)
        if message.suggestion:
            dialog.setInformativeText(message.suggestion)
        if message.technical_detail:
            dialog.setDetailedText(message.technical_detail)
        dialog.exec()

    def reject(self) -> None:
        if self._test_worker is not None:
            QMessageBox.information(self, "Đang kiểm tra", "Hãy chờ kiểm tra kết nối hoàn thành.")
            return
        super().reject()
