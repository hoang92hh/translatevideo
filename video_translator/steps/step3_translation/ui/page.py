import json
import csv
import shutil
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QComboBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from video_translator.errors import UserFacingError
from video_translator.common.models import StepId, StepResult, StepStatus, Segment
from video_translator.config.gemini import GEMINI_PROVIDER_NAME
from video_translator.config.web_translation import CHATGPT_WEB_PROVIDER_NAME
from video_translator.steps.step3_translation.quality import assess_manual_segments
from video_translator.steps.step3_translation.csv_exchange import export_segments
from video_translator.ui.specs import StepSpec
from video_translator.ui.translation_editor_dialog import TranslationEditorDialog
from video_translator.ui.steps.base import StepPage


class TranslationStepPage(StepPage):
    SPEC = StepSpec(
        StepId.TRANSLATE,
        "03",
        "Translation",
        "Dịch theo batch và giữ nguyên ID của từng segment.",
    )

    def __init__(self, state) -> None:
        self._chain_running = False
        super().__init__(state)

    def _build_header(self) -> QHBoxLayout:
        header = super()._build_header()
        self.rerun_chain_button = QPushButton("Chạy lại Step 3 → 7")
        self.rerun_chain_button.setObjectName("primaryButton")
        self.rerun_chain_button.clicked.connect(
            lambda: self.run_from_here_requested.emit(self.spec.step.value)
        )
        header.addWidget(self.rerun_chain_button)
        return header

    def _build_settings_column(self) -> QVBoxLayout:
        return super()._build_settings_column()

    def settings(self) -> dict[str, object]:
        return {}

    def refresh_special(self) -> None:
        if hasattr(self, "rerun_chain_button"):
            self.rerun_chain_button.setEnabled(
                self.state.can_run(self.spec.step)
                and not self._chain_running
            )
        if hasattr(self, "translation_combo") and not getattr(self, "_processing", False):
            self._refresh_translations()

    def set_busy(self, busy: bool) -> None:
        self._processing = busy
        super().set_busy(busy)
        if hasattr(self, "rerun_chain_button"):
            self.rerun_chain_button.setDisabled(busy or self._chain_running)
            self.rerun_chain_button.setText(
                "Đang chạy Step 3 → 7…"
                if busy or self._chain_running
                else "Chạy lại Step 3 → 7"
            )
        if hasattr(self, "editor_button"):
            self.editor_button.setDisabled(busy)
        if hasattr(self, "manual_check_button"):
            self.manual_check_button.setDisabled(busy)
        if hasattr(self, "export_csv_button"):
            self.export_csv_button.setDisabled(busy)
            self.import_csv_button.setDisabled(busy)
        if not busy and hasattr(self, "translation_combo"):
            self._refresh_translations(prefer_selected=True)

    def set_chain_running(self, running: bool) -> None:
        self._chain_running = running
        if hasattr(self, "rerun_chain_button"):
            self.rerun_chain_button.setDisabled(
                running or not self.state.can_run(self.spec.step)
            )
            self.rerun_chain_button.setText(
                "Đang chạy Step 3 → 7…" if running else "Chạy lại Step 3 → 7"
            )

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
        self.manual_check_button = QPushButton("Kiểm tra / sửa bản dịch")
        self.manual_check_button.clicked.connect(lambda: self._open_translation_editor(manual_check=True))
        self.export_csv_button = QPushButton("Xuất CSV")
        self.export_csv_button.clicked.connect(self._export_csv)
        self.import_csv_button = QPushButton("Nhập CSV thành output mới")
        self.import_csv_button.clicked.connect(self._import_csv)
        csv_actions = QHBoxLayout()
        csv_actions.addWidget(self.export_csv_button)
        csv_actions.addWidget(self.import_csv_button)
        csv_actions.addStretch()
        layout.addLayout(csv_actions)
        use_button = QPushButton("Dùng làm input Step 4")
        use_button.clicked.connect(self._select_for_step_four)
        open_file = QPushButton("Mở file")
        open_file.clicked.connect(self._open_translation)
        open_folder = QPushButton("Mở thư mục")
        open_folder.clicked.connect(self._open_translation_folder)
        delete_button = QPushButton("Xóa bản dịch")
        delete_button.clicked.connect(self._delete_translation)
        actions.addWidget(self.editor_button)
        actions.addWidget(self.manual_check_button)
        actions.addWidget(use_button)
        actions.addWidget(open_file)
        actions.addWidget(open_folder)
        actions.addWidget(delete_button)
        actions.addStretch()
        layout.addLayout(actions)
        return container

    def _show_result(self, result: StepResult) -> None:
        self._show_result_without_segments(result)

    def _refresh_translations(self, prefer_selected: bool = False) -> None:
        self.translation_combo.blockSignals(True)
        self.translation_combo.clear()
        for candidate in self.newest_candidates(self.state.translation_candidates.values()):
            suffix = (
                "  [Input Step 4]"
                if candidate.id == self.state.selected_translation_candidate_id
                else ""
            )
            self.translation_combo.addItem(f"{candidate.label}{suffix}", candidate.id)
        self.translation_combo.setCurrentIndex(0 if self.translation_combo.count() else -1)
        self.translation_combo.blockSignals(False)
        self._translation_changed()

    def _translation_changed(self, *_: object) -> None:
        candidate = self._current_translation()
        if not candidate:
            self.show_candidate_result(None)
            self.translation_info.setText("Chưa có bản dịch")
            self.editor_button.setEnabled(False)
            self.manual_check_button.setEnabled(False)
            self.export_csv_button.setEnabled(False)
            self.import_csv_button.setEnabled(False)
            return
        self.show_candidate_result(self.state.candidate_result(StepId.TRANSLATE, candidate.id))
        output_exists = Path(candidate.path).is_file()
        status = "Sẵn sàng" if output_exists else "File không tồn tại"
        model = str(candidate.metadata.get("model", ""))
        strategy = str(candidate.metadata.get("translation_strategy", ""))
        if strategy == "single_request_per_file_batch_v1":
            quality_mode = "Mỗi batch: phân tích, dịch và tự kiểm tra trong 1 request"
        elif strategy == "project_file_single_batch_v1":
            quality_mode = "Toàn project: phân tích, dịch và tự kiểm tra trong 1 request"
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
        supported = candidate.metadata.get("provider") in {GEMINI_PROVIDER_NAME, CHATGPT_WEB_PROVIDER_NAME}
        self.manual_check_button.setVisible(supported)
        self.manual_check_button.setEnabled(
            supported and output_exists and not getattr(self, "_processing", False)
        )
        self.export_csv_button.setEnabled(output_exists and not getattr(self, "_processing", False))
        self.import_csv_button.setEnabled(output_exists and not getattr(self, "_processing", False))
        count = 0
        if supported and output_exists:
            rows, _ = TranslationEditorDialog._read_segments(candidate.path)
            count = len(assess_manual_segments(
                [Segment(**row) for row in rows], self.state.target_language,
            ))
        self.manual_check_button.setText(f"Kiểm tra / sửa bản dịch ({count})")

    def _current_translation(self):
        return self.state.translation_candidate(str(self.translation_combo.currentData() or ""))

    def _export_csv(self) -> None:
        candidate = self._current_translation()
        if not candidate:
            return
        destination, _ = QFileDialog.getSaveFileName(
            self, "Xuất segment Step 3 ra CSV",
            str(Path(candidate.path).parent.parent / f"{candidate.id}.csv"), "CSV (*.csv)",
        )
        if not destination:
            return
        try:
            payload = json.loads(Path(candidate.path).read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Output Step 3 không phải JSON object hợp lệ.")
            rows = payload.get("segments")
            if not isinstance(rows, list):
                raise ValueError("Output Step 3 không có danh sách segment hợp lệ.")
            export_segments(rows, Path(destination))
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, csv.Error) as exc:
            QMessageBox.warning(self, "Không thể xuất CSV", str(exc))
            return
        QMessageBox.information(
            self, "Đã xuất CSV",
            "Có thể thêm hàng với ID và Source để trống. Giữ nguyên ID của các hàng cũ."
            f"\n\n{destination}",
        )

    def _import_csv(self) -> None:
        candidate = self._current_translation()
        if not candidate:
            return
        if any(status == StepStatus.RUNNING for status in self.state.statuses.values()):
            QMessageBox.information(self, "Pipeline đang chạy", "Hãy chờ step hiện tại hoàn thành rồi nhập CSV.")
            return
        source, _ = QFileDialog.getOpenFileName(
            self, "Nhập CSV thành output Step 3 mới", "", "CSV (*.csv)",
        )
        if not source:
            return
        try:
            imported = self.state.import_translation_csv(candidate.id, source)
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, csv.Error) as exc:
            QMessageBox.warning(self, "Không thể nhập CSV", str(exc))
            return
        self._refresh_translations(prefer_selected=True)
        QMessageBox.information(
            self, "Đã tạo output Step 3 mới",
            f"Đã tạo {imported.segment_count} segment và chọn làm input Step 4. "
            "Có thể chạy lại Step 4 → 7.\n\n"
            f"{imported.path}",
        )

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

    def _open_translation_editor(self, _checked=False, *, manual_check=False) -> None:
        candidate = self._current_translation()
        if not candidate or not Path(candidate.path).is_file():
            QMessageBox.information(
                self,
                "Không thể mở bản dịch",
                "File của output đang chọn không còn tồn tại.",
            )
            return
        editable = candidate.id == self.state.selected_translation_candidate_id
        dialog = TranslationEditorDialog(candidate, editable, self,
                                         manual_check=manual_check,
                                         target_language=self.state.target_language)
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
        updates: dict[int, dict[str, object]],
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


