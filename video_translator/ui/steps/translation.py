import shutil
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from ...errors import UserFacingError
from ...models import StepId, StepResult, StepStatus
from ..components import Card
from ..specs import StepSpec
from ..translation_components import TranslationProviderSelector
from ..translation_editor_dialog import TranslationEditorDialog
from .base import StepPage


class TranslationStepPage(StepPage):
    SPEC = StepSpec(
        StepId.TRANSLATE,
        "03",
        "Translation",
        "Dịch theo batch và giữ nguyên ID của từng segment.",
    )

    def __init__(self, state) -> None:
        self._loaded_project_id: str | None = None
        super().__init__(state)
        self.translation_selector.provider_changed.connect(self._provider_changed)
        self.translation_selector.credentials_changed.connect(self._update_run_availability)
        self.translation_selector.status_changed.connect(self._update_run_availability)
        self.translation_selector.project_url_changed.connect(self._save_project_url)

    def _build_settings_column(self) -> QVBoxLayout:
        column = QVBoxLayout()
        input_card = Card("Đầu vào", "Kết quả từ step trước được kết nối tự động.")
        self.input_summary = QLabel()
        self.input_summary.setObjectName("inputValue")
        self.input_summary.setWordWrap(True)
        input_card.content_layout.addWidget(self.input_summary)
        column.addWidget(input_card)
        self.translation_selector = TranslationProviderSelector()
        column.addWidget(self.translation_selector)
        column.addStretch()
        return column

    def settings(self) -> dict[str, object]:
        settings = self.translation_selector.settings()
        provider = str(settings.get("provider", ""))
        if "project_url" in settings:
            self._save_project_url(provider, str(settings["project_url"]))
        return settings

    def refresh(self) -> None:
        super().refresh()
        project_id = self.state.project.id if self.state.project else None
        if project_id != self._loaded_project_id:
            urls = self.state.project.translation_project_urls if self.state.project else {}
            self.translation_selector.set_project_urls(urls)
            self._loaded_project_id = project_id
        self.translation_selector.refresh_status()
        self._update_run_availability()

    def _save_project_url(self, provider: str, url: str) -> None:
        if not self.state.project:
            return
        if self.state.project.translation_project_urls.get(provider, "") == url:
            return
        self.state.project.translation_project_urls[provider] = url
        self.state.save_project()

    def _provider_changed(self, _provider: str) -> None:
        self._update_run_availability()

    def _update_run_availability(self) -> None:
        status = self.state.statuses[self.spec.step]
        ready = self.translation_selector.is_ready()
        self.run_button.setEnabled(
            ready
            and self.state.can_run(self.spec.step)
            and status != StepStatus.RUNNING
        )
        self.run_from_here_button.setEnabled(
            ready
            and self.state.can_run(self.spec.step)
            and status != StepStatus.RUNNING
        )
        unavailable_reason = "" if ready else self.translation_selector.unavailable_reason()
        self.run_button.setToolTip(unavailable_reason)
        self.run_from_here_button.setToolTip(unavailable_reason)

    def refresh_special(self) -> None:
        if hasattr(self, "translation_combo") and not getattr(self, "_processing", False):
            self._refresh_translations()

    def set_busy(self, busy: bool) -> None:
        self._processing = busy
        super().set_busy(busy)
        if hasattr(self, "editor_button"):
            self.editor_button.setDisabled(busy)
        if not busy and hasattr(self, "translation_combo"):
            self._refresh_translations(prefer_selected=True)

    def build_result_extra(self) -> QWidget:
        # Chỉ dựng danh sách segment khi người dùng mở popup xem/chỉnh sửa.
        self.table.setVisible(False)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        title = QLabel("Chọn output bản dịch")
        title.setObjectName("cardTitle")
        layout.addWidget(title)

        self.translation_combo = QComboBox()
        self.translation_combo.currentIndexChanged.connect(self._translation_changed)
        layout.addWidget(self.translation_combo)
        self.translation_info = QLabel("Chưa có bản dịch")
        self.translation_info.setObjectName("muted")
        self.translation_info.setWordWrap(True)
        layout.addWidget(self.translation_info)

        actions = QHBoxLayout()
        self.editor_button = QPushButton("Xem/chỉnh sửa bản dịch")
        self.editor_button.clicked.connect(self._open_translation_editor)
        use_button = QPushButton("Dùng làm input Step 4")
        use_button.clicked.connect(self._select_for_step_four)
        open_file = QPushButton("Mở file")
        open_file.clicked.connect(self._open_translation)
        open_folder = QPushButton("Mở thư mục")
        open_folder.clicked.connect(self._open_translation_folder)
        delete_button = QPushButton("Xóa bản dịch")
        delete_button.clicked.connect(self._delete_translation)
        actions.addWidget(self.editor_button)
        actions.addWidget(use_button)
        actions.addWidget(open_file)
        actions.addWidget(open_folder)
        actions.addWidget(delete_button)
        actions.addStretch()
        layout.addLayout(actions)
        return container

    def _show_result(self, result: StepResult) -> None:
        self.result_summary.setText(result.summary)
        self.artifacts.setPlainText(
            "\n".join(f"{name}:  {path}" for name, path in result.artifacts.items())
        )
        self.table.setRowCount(0)

    def _refresh_translations(self, prefer_selected: bool = False) -> None:
        current = self.translation_combo.currentData()
        self.translation_combo.blockSignals(True)
        self.translation_combo.clear()
        for candidate in self.state.translation_candidates.values():
            suffix = (
                "  [Input Step 4]"
                if candidate.id == self.state.selected_translation_candidate_id
                else ""
            )
            self.translation_combo.addItem(f"{candidate.label}{suffix}", candidate.id)
        target_id = self.state.selected_translation_candidate_id if prefer_selected else current
        target = self.translation_combo.findData(target_id or self.state.selected_translation_candidate_id)
        self.translation_combo.setCurrentIndex(target if target >= 0 else 0)
        self.translation_combo.blockSignals(False)
        self._translation_changed()

    def _translation_changed(self, *_: object) -> None:
        candidate = self._current_translation()
        if not candidate:
            self.translation_info.setText("Chưa có bản dịch")
            self.editor_button.setEnabled(False)
            return
        output_exists = Path(candidate.path).is_file()
        status = "Sẵn sàng" if output_exists else "File không tồn tại"
        model = str(candidate.metadata.get("model", ""))
        strategy = str(candidate.metadata.get("translation_strategy", ""))
        if strategy == "project_file_single_batch_v1":
            quality_mode = "Toàn project: phân tích ngữ cảnh và nhận 1 file"
        elif strategy == "contextual_file_batches_v1":
            quality_mode = "Theo batch: phân tích ngữ cảnh và nhận từng file"
        elif candidate.metadata.get("context_consistency", False):
            quality_mode = "Chất lượng: phân tích toàn truyện"
        else:
            quality_mode = "Nhanh: dịch một lượt"
        details = " · ".join(
            value
            for value in (model, f"{candidate.segment_count} segment", quality_mode, status)
            if value
        )
        self.translation_info.setText(f"{details}\n{candidate.path}")
        self.editor_button.setEnabled(
            output_exists and not getattr(self, "_processing", False)
        )

    def _current_translation(self):
        return self.state.translation_candidate(str(self.translation_combo.currentData() or ""))

    def _select_for_step_four(self) -> None:
        candidate = self._current_translation()
        if candidate and self.state.select_translation_candidate(candidate.id):
            self._refresh_translations()
            return
        QMessageBox.information(self, "Không thể chọn", "File bản dịch không tồn tại hoặc không hợp lệ.")

    def _open_translation(self) -> None:
        candidate = self._current_translation()
        if candidate and Path(candidate.path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(candidate.path))

    def _open_translation_folder(self) -> None:
        candidate = self._current_translation()
        if candidate:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(candidate.path).parent)))

    def _open_translation_editor(self) -> None:
        candidate = self._current_translation()
        if not candidate or not Path(candidate.path).is_file():
            QMessageBox.information(
                self,
                "Không thể mở bản dịch",
                "File của output đang chọn không còn tồn tại.",
            )
            return
        editable = candidate.id == self.state.selected_translation_candidate_id
        dialog = TranslationEditorDialog(candidate, editable, self)
        dialog.save_requested.connect(
            lambda candidate_id, updates, target=dialog: self._save_translation_changes(
                candidate_id,
                updates,
                target,
            )
        )
        dialog.exec()

    def _save_translation_changes(
        self,
        candidate_id: str,
        updates: dict[int, dict[str, str]],
        dialog: TranslationEditorDialog,
    ) -> None:
        try:
            self.state.save_translation_candidate_edits(candidate_id, updates)
        except UserFacingError as exc:
            message = exc.error_message
            details = f"\n\n{message.suggestion}" if message.suggestion else ""
            QMessageBox.warning(self, message.title, f"{message.message}{details}")
            return
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Không thể lưu bản dịch",
                f"Không thể ghi translated_segments.json.\n\n{exc}",
            )
            return
        dialog.save_succeeded()
        self._refresh_translations(prefer_selected=True)
        QMessageBox.information(
            self,
            "Đã lưu thay đổi",
            "Bản dịch đã được cập nhật. Step 4 đã sẵn sàng để chạy lại.",
        )

    def _delete_translation(self) -> None:
        candidate = self._current_translation()
        if not candidate:
            return
        answer = QMessageBox.question(
            self,
            "Xóa bản dịch",
            f"Xóa {candidate.label} và file output của lần dịch này?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        path = Path(candidate.path)
        if self.state.project:
            root = self.state.project.path("translations").resolve()
            target = path.resolve()
            if target.is_relative_to(root) and target.is_file():
                target.unlink()
                if target.parent != root and target.parent.is_dir():
                    shutil.rmtree(target.parent, ignore_errors=True)
        self.state.remove_translation_candidate(candidate.id)
        self._refresh_translations()
