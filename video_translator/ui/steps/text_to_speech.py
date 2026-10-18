import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...models import StepId, StepResult
from ..specs import StepSpec
from ..tts_preview_dialog import TtsPreviewDialog
from .base import StepPage


class TextToSpeechStepPage(StepPage):
    SPEC = StepSpec(
        StepId.TTS,
        "04",
        "Text to Speech",
        "Tạo một file giọng nói riêng cho mỗi segment.",
    )

    def __init__(self, state) -> None:
        self._preview_dialog: TtsPreviewDialog | None = None
        self._chain_running = False
        super().__init__(state)

    def _build_header(self) -> QHBoxLayout:
        header = super()._build_header()
        self.rerun_chain_button = QPushButton("Chạy lại Step 4 → 7")
        self.rerun_chain_button.setObjectName("primaryButton")
        self.rerun_chain_button.clicked.connect(
            lambda: self.run_from_here_requested.emit(self.spec.step.value)
        )
        header.addWidget(self.rerun_chain_button)
        return header

    def _build_settings_column(self) -> QVBoxLayout:
        return super()._build_settings_column()

    def _input_speakers(self) -> tuple[str, ...]:
        previous = self.state.previous_result(StepId.TTS)
        if not previous:
            return ()
        return tuple(
            dict.fromkeys(
                segment.speaker_id.strip() or "SPEAKER_UNKNOWN"
                for segment in previous.segments
            )
        )

    def _input_dialogue_profile(self) -> dict[str, object]:
        previous = self.state.previous_result(StepId.TTS)
        if not previous:
            return {}
        profile = previous.metadata.get("dialogue_profile", {})
        return dict(profile) if isinstance(profile, dict) else {}

    def settings(self) -> dict[str, object]:
        return {}

    def build_result_extra(self) -> QWidget:
        # Danh sách segment được mở theo nhu cầu trong popup nghe thử.
        self.table.setVisible(False)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        title = QLabel("Chọn output giọng nói")
        title.setObjectName("cardTitle")
        layout.addWidget(title)
        self.tts_combo = QComboBox()
        self.tts_combo.currentIndexChanged.connect(self._tts_changed)
        layout.addWidget(self.tts_combo)
        self.tts_info = QLabel("Chưa có output TTS")
        self.tts_info.setObjectName("muted")
        self.tts_info.setWordWrap(True)
        layout.addWidget(self.tts_info)

        actions = QHBoxLayout()
        self.preview_button = QPushButton("Nghe thử Output")
        self.preview_button.clicked.connect(self._open_preview)
        use_button = QPushButton("Dùng làm input Step 5")
        use_button.clicked.connect(self._select_for_step_five)
        open_file = QPushButton("Mở manifest")
        open_file.clicked.connect(self._open_manifest)
        open_folder = QPushButton("Mở thư mục")
        open_folder.clicked.connect(self._open_folder)
        delete_button = QPushButton("Xóa output")
        delete_button.clicked.connect(self._delete_output)
        for button in (
            self.preview_button,
            use_button,
            open_file,
            open_folder,
            delete_button,
        ):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        return container

    def _show_result(self, result: StepResult) -> None:
        self._show_result_without_segments(result)

    def prepare_run(self) -> None:
        self._close_preview_dialog()

    def refresh_special(self) -> None:
        if hasattr(self, "rerun_chain_button"):
            self.rerun_chain_button.setEnabled(
                self.state.can_run(self.spec.step)
                and not self._chain_running
            )
        if hasattr(self, "tts_combo") and not getattr(self, "_processing", False):
            self._refresh_outputs()

    def set_busy(self, busy: bool) -> None:
        self._processing = busy
        super().set_busy(busy)
        if hasattr(self, "rerun_chain_button"):
            self.rerun_chain_button.setDisabled(busy or self._chain_running)
            self.rerun_chain_button.setText(
                "Đang chạy Step 4 → 7…"
                if busy or self._chain_running
                else "Chạy lại Step 4 → 7"
            )
        if not busy and hasattr(self, "tts_combo"):
            self._refresh_outputs(prefer_selected=True)

    def set_chain_running(self, running: bool) -> None:
        self._chain_running = running
        if hasattr(self, "rerun_chain_button"):
            self.rerun_chain_button.setDisabled(
                running or not self.state.can_run(self.spec.step)
            )
            self.rerun_chain_button.setText(
                "Đang chạy Step 4 → 7…" if running else "Chạy lại Step 4 → 7"
            )

    def _refresh_outputs(self, prefer_selected: bool = False) -> None:
        current = self.tts_combo.currentData()
        self.tts_combo.blockSignals(True)
        self.tts_combo.clear()
        for candidate in self.state.tts_candidates.values():
            suffix = "  [Input Step 5]" if candidate.id == self.state.selected_tts_candidate_id else ""
            self.tts_combo.addItem(f"{candidate.label}{suffix}", candidate.id)
        target_id = self.state.selected_tts_candidate_id if prefer_selected else current
        target = self.tts_combo.findData(target_id or self.state.selected_tts_candidate_id)
        self.tts_combo.setCurrentIndex(target if target >= 0 else 0)
        self.tts_combo.blockSignals(False)
        self._tts_changed()

    def _current_output(self):
        return self.state.tts_candidate(str(self.tts_combo.currentData() or ""))

    def _tts_changed(self, *_: object) -> None:
        candidate = self._current_output()
        if not candidate:
            self._close_preview_dialog()
            self.tts_info.setText("Chưa có output TTS")
            self.preview_button.setEnabled(False)
            return
        if self._preview_dialog and self._preview_dialog.candidate_id != candidate.id:
            self._close_preview_dialog()
        manifest_exists = Path(candidate.path).is_file()
        status = "Sẵn sàng" if manifest_exists else "Manifest không tồn tại"
        device = str(candidate.metadata.get("actual_device", ""))
        self.tts_info.setText(f"{candidate.provider} · {candidate.voice} · {device} · {candidate.segment_count} segment · {status}\n{candidate.folder}")
        self.preview_button.setEnabled(manifest_exists)

    def _open_preview(self) -> None:
        candidate = self._current_output()
        if not candidate or not Path(candidate.path).is_file():
            QMessageBox.information(
                self,
                "Không thể nghe thử",
                "Manifest của output đang chọn không còn tồn tại.",
            )
            return
        if self._preview_dialog and self._preview_dialog.isVisible():
            if self._preview_dialog.candidate_id == candidate.id:
                self._preview_dialog.raise_()
                self._preview_dialog.activateWindow()
                return
            self._preview_dialog.close()
        dialog = TtsPreviewDialog(candidate, self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.destroyed.connect(
            lambda *_args, target=dialog: self._clear_preview_dialog(target)
        )
        self._preview_dialog = dialog
        dialog.show()

    def _clear_preview_dialog(self, dialog: TtsPreviewDialog) -> None:
        if self._preview_dialog is dialog:
            self._preview_dialog = None

    def _close_preview_dialog(self, candidate_id: str = "") -> None:
        dialog = self._preview_dialog
        if dialog and (not candidate_id or dialog.candidate_id == candidate_id):
            dialog.close()

    def _select_for_step_five(self) -> None:
        candidate = self._current_output()
        if candidate and self.state.select_tts_candidate(candidate.id):
            self._refresh_outputs()
            return
        QMessageBox.information(self, "Không thể chọn", "Manifest hoặc một file audio segment không còn hợp lệ.")

    def _open_manifest(self) -> None:
        candidate = self._current_output()
        if candidate and Path(candidate.path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(candidate.path))

    def _open_folder(self) -> None:
        candidate = self._current_output()
        if candidate and Path(candidate.folder).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(candidate.folder))

    def _delete_output(self) -> None:
        candidate = self._current_output()
        if not candidate:
            return
        answer = QMessageBox.question(self, "Xóa output TTS", f"Xóa {candidate.label} và toàn bộ file audio của lần chạy này?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._close_preview_dialog(candidate.id)
        target = Path(candidate.folder).resolve()
        if self.state.project:
            root = self.state.project.path("generated_audio").resolve()
            if target.is_relative_to(root) and target != root and target.is_dir():
                shutil.rmtree(target)
        self.state.remove_tts_candidate(candidate.id)
        self._refresh_outputs()
