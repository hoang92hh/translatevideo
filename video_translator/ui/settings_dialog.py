from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
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
from ..config.diarization import (
    DIARIZATION_MODEL_ENV,
    bundled_model_path,
    configured_model_path,
    missing_model_files,
    set_configured_model_path,
)
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

        diarization_card = Card(
            "Speaker diarization — Local",
            "Step 2 chỉ nạp model từ ổ đĩa và không gửi audio hoặc gọi Hugging Face.",
        )
        diarization_form = QFormLayout()
        self.diarization_status = QLabel()
        self.diarization_status.setWordWrap(True)
        diarization_form.addRow("Trạng thái", self.diarization_status)
        self.diarization_path = QLineEdit()
        self.diarization_path.setReadOnly(True)
        diarization_form.addRow("Thư mục model", self.diarization_path)
        diarization_card.content_layout.addLayout(diarization_form)
        diarization_actions = QHBoxLayout()
        choose_model = QPushButton("Chọn thư mục…")
        choose_model.clicked.connect(self._choose_diarization_model)
        use_default = QPushButton("Dùng thư mục mặc định")
        use_default.clicked.connect(self._use_default_diarization_model)
        refresh_model = QPushButton("Kiểm tra lại")
        refresh_model.clicked.connect(self._refresh_diarization_status)
        diarization_actions.addWidget(choose_model)
        diarization_actions.addWidget(use_default)
        diarization_actions.addWidget(refresh_model)
        diarization_actions.addStretch()
        diarization_card.content_layout.addLayout(diarization_actions)
        diarization_note = QLabel(
            "Khi chuyển máy, sao chép toàn bộ thư mục model vào "
            "<thư mục ứng dụng>\\models\\pyannote-speaker-diarization-community-1, "
            f"chọn thư mục tại đây hoặc đặt biến môi trường {DIARIZATION_MODEL_ENV}. "
            "Không cần sao chép token."
        )
        diarization_note.setObjectName("muted")
        diarization_note.setWordWrap(True)
        diarization_card.content_layout.addWidget(diarization_note)
        root.addWidget(diarization_card)

        note = QLabel(
            "Nếu không có key trong Windows Credential Locker, ứng dụng sẽ kiểm tra "
            "GEMINI_API_KEY rồi GOOGLE_API_KEY. Xóa credential đã lưu không thể xóa biến môi trường."
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
            self._refresh_diarization_status()
            return
        if info.configured:
            self.status.setText(f"Đã cấu hình · {info.source}")
        else:
            self.status.setText("Chưa cấu hình")
        self.delete_button.setEnabled(info.source == "Windows Credential Locker")
        self.test_button.setEnabled(info.configured and self._test_worker is None)
        self._refresh_diarization_status()

    def _refresh_diarization_status(self) -> None:
        path = configured_model_path().resolve()
        missing = missing_model_files(path)
        self.diarization_path.setText(str(path))
        if missing:
            self.diarization_status.setText(
                "Chưa sẵn sàng · thiếu hoặc không hợp lệ: " + ", ".join(missing)
            )
        else:
            self.diarization_status.setText("Sẵn sàng · model local đầy đủ")

    def _choose_diarization_model(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Chọn thư mục pyannote Community-1",
            str(configured_model_path()),
        )
        if not selected:
            return
        set_configured_model_path(selected)
        self._refresh_diarization_status()
        self.credentials_changed.emit()

    def _use_default_diarization_model(self) -> None:
        set_configured_model_path(None)
        self._refresh_diarization_status()
        self.credentials_changed.emit()
        if configured_model_path().resolve() != bundled_model_path().resolve():
            QMessageBox.information(
                self,
                "Biến môi trường đang được áp dụng",
                f"{DIARIZATION_MODEL_ENV} đang ghi đè thư mục mặc định.",
            )

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
