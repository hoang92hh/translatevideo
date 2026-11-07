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
from ..config.deepseek import DEEPSEEK_MODEL
from ..config.opencode_go import OPENCODE_GO_MODEL
from ..config.installation import installation_root
from ..config.diarization import (
    DIARIZATION_MODEL_ENV,
    bundled_model_path,
    configured_model_path,
    missing_model_files,
    set_configured_model_path,
)
from ..errors import UserFacingError
from ..common.services.credential_service import DEEPSEEK, GOOGLE_GEMINI, OPENCODE_GO, CredentialService
from ..steps.step3_translation.providers.gemini.provider import GoogleTranslationService
from ..steps.step3_translation.providers.deepseek.provider import DeepSeekTranslationService
from ..steps.step3_translation.providers.opencode_go.provider import OpenCodeGoTranslationService
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


class DeepSeekTestWorker(QThread):
    completed = Signal(bool, str)

    def run(self) -> None:
        try:
            source = DeepSeekTranslationService.validate_connection(DEEPSEEK_MODEL)
        except UserFacingError as exc:
            self.completed.emit(False, exc.error_message.message)
        except Exception as exc:
            self.completed.emit(False, str(exc) or "Lỗi không xác định")
        else:
            self.completed.emit(True, f"Kết nối DeepSeek thành công qua {source}.")


class OpenCodeGoTestWorker(QThread):
    completed = Signal(bool, str)

    def run(self) -> None:
        try:
            source = OpenCodeGoTranslationService.validate_connection(OPENCODE_GO_MODEL)
        except UserFacingError as exc:
            self.completed.emit(False, exc.error_message.message)
        except Exception as exc:
            self.completed.emit(False, str(exc) or "Lỗi không xác định")
        else:
            self.completed.emit(True, f"Kết nối OpenCode Go thành công qua {source}.")


class ProviderSettingsDialog(QDialog):
    credentials_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cài đặt — API & Providers")
        self.setMinimumWidth(680)
        self._test_worker: ProviderTestWorker | None = None
        self._deepseek_test_worker: DeepSeekTestWorker | None = None
        self._opencode_test_worker: OpenCodeGoTestWorker | None = None

        root = QVBoxLayout(self)
        title = QLabel("API & Providers")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        description = QLabel(
            "Mỗi folder cài có file api_keys.env riêng. Sửa file rồi mở lại ứng dụng để nạp API key. "
            "API key không nằm trong project.json."
        )
        description.setObjectName("muted")
        description.setWordWrap(True)
        root.addWidget(description)
        key_file = QLabel(f"File API key: {installation_root() / 'api_keys.env'}")
        key_file.setWordWrap(True)
        root.addWidget(key_file)

        card = Card("Google Gemini", "Provider dịch thuật của Step 3.")
        form = QFormLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        form.addRow("Trạng thái", self.status)
        card.content_layout.addLayout(form)

        actions = QHBoxLayout()
        self.test_button = QPushButton("Kiểm tra kết nối")
        self.test_button.clicked.connect(self._test)
        actions.addWidget(self.test_button)
        actions.addStretch()
        card.content_layout.addLayout(actions)
        root.addWidget(card)

        deepseek_card = Card("DeepSeek", "Provider dịch thuật của Step 3 dùng model deepseek-flash.")
        deepseek_form = QFormLayout()
        self.deepseek_status = QLabel()
        self.deepseek_status.setWordWrap(True)
        deepseek_form.addRow("Trạng thái", self.deepseek_status)
        deepseek_card.content_layout.addLayout(deepseek_form)
        deepseek_actions = QHBoxLayout()
        self.deepseek_test_button = QPushButton("Kiểm tra kết nối")
        self.deepseek_test_button.clicked.connect(self._test_deepseek)
        deepseek_actions.addWidget(self.deepseek_test_button)
        deepseek_actions.addStretch()
        deepseek_card.content_layout.addLayout(deepseek_actions)
        root.addWidget(deepseek_card)

        opencode_card = Card("OpenCode Go", f"Provider Step 3 dùng model {OPENCODE_GO_MODEL}.")
        opencode_form = QFormLayout()
        self.opencode_status = QLabel()
        self.opencode_status.setWordWrap(True)
        opencode_form.addRow("Trạng thái", self.opencode_status)
        opencode_card.content_layout.addLayout(opencode_form)
        opencode_actions = QHBoxLayout()
        self.opencode_test_button = QPushButton("Kiểm tra kết nối")
        self.opencode_test_button.clicked.connect(self._test_opencode)
        opencode_actions.addWidget(self.opencode_test_button)
        opencode_actions.addStretch()
        opencode_card.content_layout.addLayout(opencode_actions)
        root.addWidget(opencode_card)

        diarization_card = Card(
            "Speaker diarization — Local",
            "Step 1.5 chỉ nạp model từ ổ đĩa và không gửi audio hoặc gọi Hugging Face.",
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
            "Trong api_keys.env, nhập GEMINI_API_KEY, DEEPSEEK_API_KEY và OPENCODE_API_KEY theo mẫu "
            "api_keys.env.example. Biến môi trường Windows và key đã lưu trước đây "
            "không được dùng khi mở ứng dụng."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        root.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._refresh_status()

    def _refresh_status(self) -> None:
        self._refresh_deepseek_status()
        self._refresh_opencode_status()
        try:
            info = CredentialService.get(GOOGLE_GEMINI, include_value=False)
        except UserFacingError as exc:
            self.status.setText(f"Không thể kiểm tra · {exc.error_message.message}")
            self.test_button.setEnabled(False)
            self._refresh_diarization_status()
            return
        if info.configured:
            self.status.setText(f"Đã cấu hình · {info.source}")
        else:
            self.status.setText("Chưa cấu hình")
        self.test_button.setEnabled(info.configured and self._test_worker is None)
        self._refresh_diarization_status()

    def _refresh_deepseek_status(self) -> None:
        try:
            info = CredentialService.get(DEEPSEEK, include_value=False)
        except UserFacingError as exc:
            self.deepseek_status.setText(f"Không thể kiểm tra · {exc.error_message.message}")
            self.deepseek_test_button.setEnabled(False)
            return
        self.deepseek_status.setText(
            f"Đã cấu hình · {info.source}" if info.configured else "Chưa cấu hình"
        )
        self.deepseek_test_button.setEnabled(info.configured and self._deepseek_test_worker is None)

    def _refresh_opencode_status(self) -> None:
        info = CredentialService.get(OPENCODE_GO, include_value=False)
        self.opencode_status.setText(
            f"Đã cấu hình · {info.source}" if info.configured else "Chưa cấu hình"
        )
        self.opencode_test_button.setEnabled(info.configured and self._opencode_test_worker is None)

    def _test_opencode(self) -> None:
        if self._opencode_test_worker is not None:
            return
        self.opencode_test_button.setEnabled(False)
        self.opencode_status.setText("Đang kiểm tra kết nối OpenCode Go…")
        worker = OpenCodeGoTestWorker(self)
        self._opencode_test_worker = worker
        worker.completed.connect(self._opencode_test_completed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _opencode_test_completed(self, success: bool, message: str) -> None:
        self._opencode_test_worker = None
        self._refresh_opencode_status()
        if success:
            QMessageBox.information(self, "Kết nối thành công", message)
        else:
            QMessageBox.warning(self, "Không thể kết nối", message)

    def _test_deepseek(self) -> None:
        if self._deepseek_test_worker is not None:
            return
        self.deepseek_test_button.setEnabled(False)
        self.deepseek_status.setText("Đang kiểm tra kết nối DeepSeek…")
        worker = DeepSeekTestWorker(self)
        self._deepseek_test_worker = worker
        worker.completed.connect(self._deepseek_test_completed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _deepseek_test_completed(self, success: bool, message: str) -> None:
        self._deepseek_test_worker = None
        self._refresh_deepseek_status()
        if success:
            QMessageBox.information(self, "Kết nối thành công", message)
        else:
            QMessageBox.warning(self, "Không thể kết nối", message)

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
        if self._test_worker is not None or self._deepseek_test_worker is not None or self._opencode_test_worker is not None:
            QMessageBox.information(self, "Đang kiểm tra", "Hãy chờ kiểm tra kết nối hoàn thành.")
            return
        super().reject()
