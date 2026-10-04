import json
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...models import StepId, StepResult
from ..specs import FieldSpec, ProviderSpec, StepSpec
from .base import StepPage


class AudioSyncStepPage(StepPage):
    repair_requested = Signal(str, int, str)

    SPEC = StepSpec(
        StepId.SYNC,
        "05",
        "Audio Sync",
        "Căn thời lượng audio mới vào timestamp của video gốc.",
        (
            ProviderSpec(
                "FFmpeg — Duration Sync",
                (
                    FieldSpec("max_speed", "Tốc độ tối đa", "float", 1.35),
                    FieldSpec("use_gap", "Tận dụng khoảng trống kế tiếp", "bool", True),
                    FieldSpec("trim_silence", "Cắt khoảng lặng đầu/cuối", "bool", True),
                ),
            ),
        ),
    )

    def __init__(self, state) -> None:
        self._candidate_payload: dict[str, object] = {}
        self._loaded_manifest_signature: tuple[str, int, int] | None = None
        self._repairing = False
        super().__init__(state)

    def build_result_extra(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        title = QLabel("Kiểm tra output Audio Sync")
        title.setObjectName("cardTitle")
        layout.addWidget(title)
        self.sync_combo = QComboBox()
        self.sync_combo.currentIndexChanged.connect(self._candidate_changed)
        layout.addWidget(self.sync_combo)
        self.sync_info = QLabel("Chưa có output Step 5")
        self.sync_info.setObjectName("muted")
        self.sync_info.setWordWrap(True)
        layout.addWidget(self.sync_info)

        self.preview_combo = QComboBox()
        self.preview_combo.currentIndexChanged.connect(self._preview_changed)
        layout.addWidget(self.preview_combo)
        self.preview_info = QLabel("Chọn một segment để nghe kiểm tra")
        self.preview_info.setObjectName("muted")
        self.preview_info.setWordWrap(True)
        layout.addWidget(self.preview_info)

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.8)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._playback_changed)
        transport = QHBoxLayout()
        self.play_source_button = QPushButton("Nghe TTS gốc")
        self.play_source_button.clicked.connect(lambda: self._play_selected("audio_file"))
        self.play_synced_button = QPushButton("Nghe đã đồng bộ")
        self.play_synced_button.clicked.connect(lambda: self._play_selected("synced_audio_file"))
        self.pause_button = QPushButton("Dừng")
        self.pause_button.clicked.connect(self.player.stop)
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.sliderMoved.connect(self.player.setPosition)
        self.time_label = QLabel("00:00 / 00:00")
        transport.addWidget(self.play_source_button)
        transport.addWidget(self.play_synced_button)
        transport.addWidget(self.pause_button)
        transport.addWidget(self.position_slider, 1)
        transport.addWidget(self.time_label)
        layout.addLayout(transport)

        error_title = QLabel("Segment cần sửa")
        error_title.setObjectName("cardTitle")
        layout.addWidget(error_title)
        self.error_combo = QComboBox()
        self.error_combo.currentIndexChanged.connect(self._error_changed)
        layout.addWidget(self.error_combo)
        self.error_info = QLabel("Không có segment lỗi")
        self.error_info.setObjectName("muted")
        self.error_info.setWordWrap(True)
        layout.addWidget(self.error_info)
        self.translation_editor = QTextEdit()
        self.translation_editor.setPlaceholderText("Sửa ngắn lại câu dịch của segment đang lỗi")
        self.translation_editor.setMaximumHeight(90)
        layout.addWidget(self.translation_editor)
        self.repair_button = QPushButton("Tạo lại giọng và đồng bộ segment này")
        self.repair_button.clicked.connect(self._request_repair)
        layout.addWidget(self.repair_button)

        actions = QHBoxLayout()
        use_button = QPushButton("Dùng làm input Step 6")
        use_button.clicked.connect(self._select_for_step_six)
        open_manifest = QPushButton("Mở manifest")
        open_manifest.clicked.connect(self._open_manifest)
        open_folder = QPushButton("Mở thư mục")
        open_folder.clicked.connect(self._open_folder)
        delete_button = QPushButton("Xóa output")
        delete_button.clicked.connect(self._delete_output)
        for button in (use_button, open_manifest, open_folder, delete_button):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        return container

    def _show_result(self, result: StepResult) -> None:
        super()._show_result(result)
        for row in range(self.table.rowCount()):
            for column in range(self.table.columnCount()):
                item = self.table.item(row, column)
                if item:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

    def refresh_special(self) -> None:
        if hasattr(self, "sync_combo") and not self._repairing:
            self._refresh_candidates()

    def set_busy(self, busy: bool) -> None:
        super().set_busy(busy)
        if not busy and hasattr(self, "sync_combo"):
            self._refresh_candidates(prefer_current=True)

    def prepare_run(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())

    def _refresh_candidates(self, prefer_current: bool = False) -> None:
        current = self.sync_combo.currentData()
        result = self.state.results.get(StepId.SYNC)
        result_id = str(result.metadata.get("sync_candidate_id", "")) if result else ""
        self.sync_combo.blockSignals(True)
        self.sync_combo.clear()
        for candidate in self.state.sync_candidates.values():
            suffix = "  [Input Step 6]" if candidate.id == self.state.selected_sync_candidate_id else ""
            if candidate.error_count:
                suffix += f"  [Cần sửa: {candidate.error_count}]"
            self.sync_combo.addItem(f"{candidate.label}{suffix}", candidate.id)
        target_id = result_id if prefer_current else (current or result_id)
        target = self.sync_combo.findData(target_id)
        self.sync_combo.setCurrentIndex(target if target >= 0 else 0)
        self.sync_combo.blockSignals(False)
        self._candidate_changed()

    def _current_candidate(self):
        return self.state.sync_candidate(str(self.sync_combo.currentData() or ""))

    def _candidate_changed(self, *_: object) -> None:
        self.player.stop()
        if not self.player.source().isEmpty():
            self.player.setSource(QUrl())
        candidate = self._current_candidate()
        if not candidate:
            self._candidate_payload = {}
            self._loaded_manifest_signature = None
            self.sync_info.setText("Chưa có output Step 5")
            self._fill_segments([])
            return
        manifest = Path(candidate.path)
        try:
            stat = manifest.stat()
            signature = (str(manifest.resolve()), stat.st_mtime_ns, stat.st_size)
        except OSError:
            signature = None
        if signature and signature == self._loaded_manifest_signature and self._candidate_payload:
            return
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._candidate_payload = {}
            self._loaded_manifest_signature = None
            self.sync_info.setText(f"Không thể đọc manifest: {exc}")
            self._fill_segments([])
            return
        self._candidate_payload = payload
        self._loaded_manifest_signature = signature
        errors = int(payload.get("error_count", 0))
        status = "Hoàn thành" if not errors else f"Cần sửa {errors} segment"
        self.sync_info.setText(f"{status} · {candidate.segment_count} segment\n{candidate.folder}")
        raw_segments = payload.get("segments", [])
        self._fill_segments(raw_segments if isinstance(raw_segments, list) else [])

    def _fill_segments(self, segments: list[object]) -> None:
        valid = [item for item in segments if isinstance(item, dict)]
        self.preview_combo.blockSignals(True)
        self.error_combo.blockSignals(True)
        self.preview_combo.clear()
        self.error_combo.clear()
        for index, item in enumerate(valid):
            segment_id = int(item.get("id", index + 1))
            label = f"#{segment_id:04d} · {float(item.get('start', 0)):.2f}s–{float(item.get('end', 0)):.2f}s"
            self.preview_combo.addItem(label, index)
            if item.get("status") != "ready":
                self.error_combo.addItem(f"{label} · {item.get('error', '')}", index)
        self.preview_combo.blockSignals(False)
        self.error_combo.blockSignals(False)
        if self.preview_combo.count():
            self.preview_combo.setCurrentIndex(0)
            self._preview_changed()
        else:
            self.preview_info.setText("Candidate không có segment hợp lệ")
        if self.error_combo.count():
            self.error_combo.setCurrentIndex(0)
            self._error_changed()
        else:
            self.error_info.setText("Không có segment lỗi. Output có thể dùng cho Step 6.")
            self.translation_editor.clear()
            self.translation_editor.setEnabled(False)
            self.repair_button.setEnabled(False)

    def _segments(self) -> list[dict[str, object]]:
        raw = self._candidate_payload.get("segments", [])
        return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    def _preview_changed(self, *_: object) -> None:
        index = self.preview_combo.currentData()
        segments = self._segments()
        if not isinstance(index, int) or index >= len(segments):
            return
        item = segments[index]
        source_exists = Path(str(item.get("audio_file", ""))).is_file()
        synced_exists = Path(str(item.get("synced_audio_file", ""))).is_file()
        self.play_source_button.setEnabled(source_exists)
        self.play_synced_button.setEnabled(synced_exists)
        self.preview_info.setText(
            f"Audio {float(item.get('prepared_duration', 0)):.2f}s · "
            f"khung {float(item.get('target_duration', 0)):.2f}s · "
            f"cho phép {float(item.get('allowed_duration', 0)):.2f}s · "
            f"tốc độ {float(item.get('speed_factor', 1)):.2f}x\n"
            f"{item.get('translated_text', '')}"
        )

    def _play_selected(self, key: str) -> None:
        index = self.preview_combo.currentData()
        segments = self._segments()
        if not isinstance(index, int) or index >= len(segments):
            return
        path = Path(str(segments[index].get(key, "")))
        if path.is_file():
            self.player.setSource(QUrl.fromLocalFile(str(path)))
            self.player.play()

    def _error_changed(self, *_: object) -> None:
        index = self.error_combo.currentData()
        segments = self._segments()
        if not isinstance(index, int) or index >= len(segments):
            return
        item = segments[index]
        self.translation_editor.setEnabled(True)
        self.translation_editor.setPlainText(str(item.get("translated_text", "")))
        self.error_info.setText(
            f"Segment #{int(item.get('id', 0)):04d} · "
            f"audio {float(item.get('prepared_duration', 0)):.2f}s / "
            f"cho phép {float(item.get('allowed_duration', 0)):.2f}s\n"
            f"{item.get('error', '')}"
        )
        self.repair_button.setEnabled(not self._repairing)
        preview_index = self.preview_combo.findData(index)
        if preview_index >= 0:
            self.preview_combo.setCurrentIndex(preview_index)

    def _request_repair(self) -> None:
        candidate = self._current_candidate()
        index = self.error_combo.currentData()
        segments = self._segments()
        text = self.translation_editor.toPlainText().strip()
        if not candidate or not isinstance(index, int) or index >= len(segments):
            return
        if not text:
            QMessageBox.information(self, "Nội dung đang trống", "Hãy nhập câu dịch đã rút gọn.")
            return
        self.repair_requested.emit(candidate.id, int(segments[index].get("id", 0)), text)

    def set_repair_busy(self, busy: bool) -> None:
        self._repairing = busy
        self.repair_button.setEnabled(not busy and self.error_combo.count() > 0)
        self.repair_button.setText("Đang tạo lại segment…" if busy else "Tạo lại giọng và đồng bộ segment này")
        self.run_button.setEnabled(not busy and self.state.can_run(StepId.SYNC))

    def repair_finished(self, candidate_id: str) -> None:
        self.set_repair_busy(False)
        self._refresh_candidates(prefer_current=True)
        target = self.sync_combo.findData(candidate_id)
        if target >= 0:
            self.sync_combo.setCurrentIndex(target)

    def _select_for_step_six(self) -> None:
        candidate = self._current_candidate()
        if candidate and self.state.select_sync_candidate(candidate.id):
            self._refresh_candidates()
            return
        QMessageBox.information(
            self,
            "Output chưa sẵn sàng",
            "Hãy sửa hết segment lỗi và bảo đảm các file đã đồng bộ còn tồn tại.",
        )

    def _open_manifest(self) -> None:
        candidate = self._current_candidate()
        if candidate and Path(candidate.path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(candidate.path))

    def _open_folder(self) -> None:
        candidate = self._current_candidate()
        if candidate and Path(candidate.folder).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(candidate.folder))

    def _delete_output(self) -> None:
        candidate = self._current_candidate()
        if not candidate:
            return
        answer = QMessageBox.question(
            self,
            "Xóa output Audio Sync",
            f"Xóa {candidate.label} và toàn bộ file của lần chạy này?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.player.stop()
        target = Path(candidate.folder).resolve()
        if self.state.project:
            root = self.state.project.path("synchronized_audio").resolve()
            if target.is_relative_to(root) and target != root and target.is_dir():
                shutil.rmtree(target)
        self.state.remove_sync_candidate(candidate.id)
        self._refresh_candidates()

    def _duration_changed(self, duration: int) -> None:
        self.position_slider.setRange(0, duration)
        self._update_time(self.player.position(), duration)

    def _position_changed(self, position: int) -> None:
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(position)
        self._update_time(position, self.player.duration())

    def _playback_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self.pause_button.setText(
            "Tạm dừng" if state == QMediaPlayer.PlaybackState.PlayingState else "Dừng"
        )
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.pause_button.clicked.disconnect()
            self.pause_button.clicked.connect(self.player.pause)
        else:
            self.pause_button.clicked.disconnect()
            self.pause_button.clicked.connect(self.player.stop)

    def _update_time(self, position: int, duration: int) -> None:
        self.time_label.setText(f"{self._format_ms(position)} / {self._format_ms(duration)}")

    @staticmethod
    def _format_ms(value: int) -> str:
        seconds = max(0, value // 1000)
        return f"{seconds // 60:02d}:{seconds % 60:02d}"
