import csv
import json
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from video_translator.common.models import StepId, StepStatus
from video_translator.errors import UserFacingError
from video_translator.steps.step2_transcription.csv_exchange import export_segments
from video_translator.ui.transcript_timeline_dialog import TranscriptTimelineDialog
from video_translator.ui.components import Card
from video_translator.ui.specs import DEVICE_FIELD, FieldSpec, ProviderSpec, StepSpec
from video_translator.ui.steps.base import StepPage


class SpeechToTextStepPage(StepPage):
    SPEC = StepSpec(
        StepId.STT,
        "02",
        "Speech to Text",
        "Nhận dạng lời nói, timestamp và segment ID.",
        (
            ProviderSpec("Faster Whisper", (
                FieldSpec("model", "Model", "choice", "medium", ("small", "medium", "large-v3")),
                DEVICE_FIELD,
                FieldSpec("vad", "Voice activity detection", "bool", True),
                FieldSpec("segment_recovery", "Segment recovery (tìm lời thoại bị bỏ sót)", "bool", False),
                FieldSpec("diarization", "Speaker diarization", "bool", False),
                FieldSpec(
                    "merge_adjacent_segments",
                    "Gộp segment liền kề cùng speaker",
                    "bool",
                    False,
                ),
            )),
            ProviderSpec("Provider khác (sắp có)", available=False),
        ),
    )

    def __init__(self, state) -> None:
        self._chain_running = False
        self._processing = False
        self._old_text_cache: dict[str, tuple[int, int, dict[int, str]]] = {}
        super().__init__(state)

    def _build_result_column(self) -> QVBoxLayout:
        column = super()._build_result_column()
        self.table.setHorizontalHeaderLabels(
            ["ID", "Speaker", "Start", "End", "Source", "Text Old", "Audio"]
        )
        return column

    def _show_result(self, result) -> None:
        super()._show_result(result)
        old_text_by_id: dict[int, str] = {}
        path = result.artifacts.get("transcript", "")
        if path:
            try:
                transcript_path = Path(path)
                stamp = transcript_path.stat()
                cached = self._old_text_cache.get(path)
                if cached and cached[:2] == (stamp.st_mtime_ns, stamp.st_size):
                    old_text_by_id = cached[2]
                else:
                    payload = json.loads(transcript_path.read_text(encoding="utf-8"))
                    old_text_by_id = {
                        int(row["id"]): str(row["text_old"])
                        for row in payload.get("segments", [])
                        if isinstance(row, dict) and "id" in row and "text_old" in row
                    }
                    self._old_text_cache[path] = (stamp.st_mtime_ns, stamp.st_size, old_text_by_id)
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                pass
        self._updating_table = True
        try:
            for index, segment in enumerate(result.segments):
                item = QTableWidgetItem(old_text_by_id.get(segment.id, ""))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(index, 5, item)
        finally:
            self._updating_table = False

    def _table_cell_changed(self, row: int, column: int) -> None:
        if column != 5:
            super()._table_cell_changed(row, column)

    def _build_header(self) -> QHBoxLayout:
        header = super()._build_header()
        self.rerun_chain_button = QPushButton("Chạy lại Step 2 → 7")
        self.rerun_chain_button.setObjectName("primaryButton")
        self.rerun_chain_button.clicked.connect(
            lambda: self.run_from_here_requested.emit(self.spec.step.value)
        )
        header.addWidget(self.rerun_chain_button)
        return header

    def _refresh_rerun_button(self) -> None:
        if not hasattr(self, "rerun_chain_button"):
            return
        busy = self._processing or self._chain_running
        self.rerun_chain_button.setEnabled(
            not busy and self.state.can_run(self.spec.step)
        )
        self.rerun_chain_button.setText(
            "Đang chạy Step 2 → 7…" if busy else "Chạy lại Step 2 → 7"
        )

    def set_chain_running(self, running: bool) -> None:
        self._chain_running = running
        self._refresh_rerun_button()

    def build_special_card(self) -> Card:
        card = Card("Audio input", "Chọn candidate Voice hoặc Original Mix được tạo ở Step 1.")
        self.audio_input = QComboBox()
        self.audio_input.currentIndexChanged.connect(self._input_changed)
        card.content_layout.addWidget(self.audio_input)
        return card

    def refresh_special(self) -> None:
        current = (self.state.selected_audio_candidate_id, self.state.selected_audio_stem)
        self.audio_input.blockSignals(True)
        self.audio_input.clear()
        selected_index = -1
        for candidate in self.state.audio_candidates.values():
            for stem in candidate.stems:
                if stem not in {"voice", "original"}:
                    continue
                self.audio_input.addItem(
                    f"{candidate.label} · {stem.title()}",
                    (candidate.id, stem),
                )
                value = self.audio_input.itemData(self.audio_input.count() - 1)
                if (
                    isinstance(value, (tuple, list))
                    and len(value) == 2
                    and (str(value[0]), str(value[1])) == current
                ):
                    selected_index = self.audio_input.count() - 1
        self.audio_input.setCurrentIndex(selected_index)
        self.audio_input.blockSignals(False)
        if hasattr(self, "transcript_combo") and not getattr(self, "_processing", False):
            self._refresh_transcripts()
        self._refresh_rerun_button()

    def set_busy(self, busy: bool) -> None:
        self._processing = busy
        super().set_busy(busy)
        self._refresh_rerun_button()
        if hasattr(self, "timeline_button"):
            self.timeline_button.setDisabled(busy)
        if hasattr(self, "import_button"):
            self.import_button.setDisabled(busy)
        if hasattr(self, "export_csv_button"):
            self.export_csv_button.setDisabled(busy)
            self.import_csv_button.setDisabled(busy)
        if not busy and hasattr(self, "transcript_combo"):
            self._refresh_transcripts(prefer_selected=True)

    def build_result_extra(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        title = QLabel("Chọn output transcript")
        title.setObjectName("cardTitle")
        layout.addWidget(title)

        self.transcript_combo = QComboBox()
        self.transcript_combo.currentIndexChanged.connect(self._transcript_changed)
        layout.addWidget(self.transcript_combo)

        self.transcript_info = QLabel("Chưa có transcript")
        self.transcript_info.setObjectName("muted")
        self.transcript_info.setWordWrap(True)
        layout.addWidget(self.transcript_info)

        self.import_button = QPushButton("Nhập TXT/SRT thành output mới")
        self.import_button.clicked.connect(self._import_text_file)
        self.export_csv_button = QPushButton("Xuất CSV")
        self.export_csv_button.clicked.connect(self._export_csv)
        self.import_csv_button = QPushButton("Nhập CSV thành output mới")
        self.import_csv_button.clicked.connect(self._import_csv)
        import_actions = QHBoxLayout()
        import_actions.addWidget(self.import_button)
        import_actions.addWidget(self.export_csv_button)
        import_actions.addWidget(self.import_csv_button)
        import_actions.addStretch()
        layout.addLayout(import_actions)

        actions = QHBoxLayout()
        self.timeline_button = QPushButton("Kiểm tra / sửa timeline")
        self.timeline_button.clicked.connect(self._open_timeline_editor)
        use_button = QPushButton("Dùng làm input Step 3")
        use_button.clicked.connect(self._select_for_step_three)
        open_file = QPushButton("Mở file")
        open_file.clicked.connect(self._open_transcript)
        open_folder = QPushButton("Mở thư mục")
        open_folder.clicked.connect(self._open_transcript_folder)
        delete_button = QPushButton("Xóa transcript")
        delete_button.clicked.connect(self._delete_transcript)
        actions.addWidget(self.timeline_button)
        actions.addWidget(use_button)
        actions.addWidget(open_file)
        actions.addWidget(open_folder)
        actions.addWidget(delete_button)
        actions.addStretch()
        layout.addLayout(actions)
        return container

    def _refresh_transcripts(self, prefer_selected: bool = False) -> None:
        self.transcript_combo.blockSignals(True)
        self.transcript_combo.clear()
        for candidate in self.newest_candidates(self.state.transcript_candidates.values()):
            suffix = (
                "  [Input Step 3]"
                if candidate.id == self.state.selected_transcript_candidate_id
                else ""
            )
            self.transcript_combo.addItem(f"{candidate.label}{suffix}", candidate.id)
        self.transcript_combo.setCurrentIndex(0 if self.transcript_combo.count() else -1)
        self.transcript_combo.blockSignals(False)
        self._transcript_changed()

    def _transcript_changed(self, *_: object) -> None:
        candidate = self._current_transcript()
        if not candidate:
            self.transcript_info.setText("Chưa có transcript")
            self.timeline_button.setEnabled(False)
            self.import_button.setEnabled(self.state.can_run(StepId.STT) and not self._processing)
            self.export_csv_button.setEnabled(False)
            self.import_csv_button.setEnabled(False)
            self.show_candidate_result(None)
            return
        status = "Sẵn sàng" if Path(candidate.path).is_file() else "File không tồn tại"
        model = str(candidate.metadata.get("model", ""))
        device = str(candidate.metadata.get("actual_device", ""))
        details = " · ".join(
            value
            for value in (model, device, f"{candidate.segment_count} segment", status)
            if value
        )
        self.transcript_info.setText(f"{details}\n{candidate.path}")
        result = self.state.candidate_result(StepId.STT, candidate.id)
        count = int(result.metadata.get("timeline_error_count", 0)) if result else 0
        self.timeline_button.setText(f"Kiểm tra / sửa timeline ({count})")
        self.timeline_button.setEnabled(result is not None and not self._processing)
        self.import_button.setEnabled(self.state.can_run(StepId.STT) and not self._processing)
        self.export_csv_button.setEnabled(Path(candidate.path).is_file() and not self._processing)
        self.import_csv_button.setEnabled(Path(candidate.path).is_file() and not self._processing)
        self.show_candidate_result(result)

    def _export_csv(self) -> None:
        candidate = self._current_transcript()
        if not candidate:
            return
        destination, _ = QFileDialog.getSaveFileName(
            self, "Xuất segment Step 2 ra CSV",
            str(Path(candidate.path).parent.parent / f"{candidate.id}.csv"), "CSV (*.csv)",
        )
        if not destination:
            return
        try:
            payload = json.loads(Path(candidate.path).read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
                raise ValueError("Output Step 2 không có danh sách segment hợp lệ.")
            export_segments(payload["segments"], Path(destination))
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, csv.Error) as exc:
            QMessageBox.warning(self, "Không thể xuất CSV", str(exc))
            return
        QMessageBox.information(self, "Đã xuất CSV", f"Đã xuất {destination}")

    def _import_csv(self) -> None:
        candidate = self._current_transcript()
        if not candidate:
            return
        if any(status == StepStatus.RUNNING for status in self.state.statuses.values()):
            QMessageBox.information(self, "Pipeline đang chạy", "Hãy chờ step hiện tại hoàn thành rồi nhập CSV.")
            return
        source, _ = QFileDialog.getOpenFileName(
            self, "Nhập CSV thành output Step 2 mới", "", "CSV (*.csv)",
        )
        if not source:
            return
        try:
            imported = self.state.import_transcript_csv(candidate.id, source)
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, csv.Error) as exc:
            QMessageBox.warning(self, "Không thể nhập CSV", str(exc))
            return
        self._refresh_transcripts(prefer_selected=True)
        QMessageBox.information(
            self, "Đã tạo output Step 2 mới",
            f"Đã nhập {imported.segment_count} segment và chọn làm input Step 3. "
            "Nếu còn lỗi timeline, hãy mở Kiểm tra / sửa timeline.\n\n"
            f"{imported.path}",
        )

    def _import_text_file(self) -> None:
        if any(status == StepStatus.RUNNING for status in self.state.statuses.values()):
            QMessageBox.information(self, "Pipeline đang chạy", "Hãy chờ step hiện tại hoàn thành rồi nhập file.")
            return
        source, _ = QFileDialog.getOpenFileName(
            self, "Nhập text vào Step 2", "", "Text và phụ đề (*.txt *.srt)",
        )
        if not source:
            return
        candidate = self._current_transcript()
        try:
            imported = self.state.import_transcript_text(source, candidate.id if candidate else "")
        except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            QMessageBox.warning(self, "Nhập transcript thất bại", f"File: {source}\n\n{exc}")
            return
        self._refresh_transcripts(prefer_selected=True)
        status = (
            "Transcript còn lỗi timeline; mở Kiểm tra / sửa timeline trước khi chạy Step 3."
            if self.state.statuses[StepId.STT] == StepStatus.ERROR
            else "Output mới đã được chọn làm input Step 3."
        )
        QMessageBox.information(
            self, "Đã nhập transcript",
            f"Đã tạo {imported.segment_count} segment. {status}\n\n{imported.path}",
        )

    def _open_timeline_editor(self) -> None:
        candidate = self._current_transcript()
        result = self.state.candidate_result(StepId.STT, candidate.id) if candidate else None
        if result is None:
            QMessageBox.information(self, "Không thể mở", "Không đọc được output transcript.")
            return
        dialog = TranscriptTimelineDialog(
            candidate, result.segments,
            candidate.id == self.state.selected_transcript_candidate_id, self,
        )
        dialog.save_requested.connect(
            lambda candidate_id, updates: self._save_timeline(candidate_id, updates, dialog)
        )
        dialog.exec()

    def _save_timeline(self, candidate_id, updates, dialog) -> None:
        try:
            self.state.save_transcript_candidate_edits(candidate_id, updates)
        except UserFacingError as exc:
            message = exc.error_message
            QMessageBox.warning(self, message.title, message.message + "\n" + message.suggestion)
            return
        except OSError as exc:
            QMessageBox.warning(self, "Không thể lưu transcript", str(exc))
            return
        dialog.save_succeeded()
        self._refresh_transcripts(prefer_selected=True)

    def _current_transcript(self):
        return self.state.transcript_candidate(str(self.transcript_combo.currentData() or ""))

    def _select_for_step_three(self) -> None:
        candidate = self._current_transcript()
        if candidate and self.state.select_transcript_candidate(candidate.id):
            self._refresh_transcripts()
            return
        QMessageBox.information(self, "Không thể chọn", "File transcript không tồn tại hoặc không hợp lệ.")

    def _open_transcript(self) -> None:
        candidate = self._current_transcript()
        if candidate and Path(candidate.path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(candidate.path))

    def _open_transcript_folder(self) -> None:
        candidate = self._current_transcript()
        if candidate:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(candidate.path).parent)))

    def _delete_transcript(self) -> None:
        candidate = self._current_transcript()
        if not candidate:
            return
        answer = QMessageBox.question(
            self,
            "Xóa transcript",
            f"Xóa {candidate.label} và file transcript của lần chạy này?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        path = Path(candidate.path)
        if self.state.project:
            root = self.state.project.path("transcripts").resolve()
            target = path.resolve()
            if target.is_relative_to(root) and target.is_file():
                target.unlink()
                if target.parent != root and target.parent.is_dir():
                    shutil.rmtree(target.parent, ignore_errors=True)
        self.state.remove_transcript_candidate(candidate.id)
        self._refresh_transcripts()

    def _input_changed(self, *_: object) -> None:
        value = self.audio_input.currentData()
        if isinstance(value, (tuple, list)) and len(value) == 2:
            self.state.select_audio_input(str(value[0]), str(value[1]))

    def settings(self) -> dict[str, object]:
        values = super().settings()
        values["input_audio"] = self.state.audio_input_path()
        values["input_candidate_id"] = self.state.selected_audio_candidate_id
        values["input_stem"] = self.state.selected_audio_stem
        return values


