from __future__ import annotations

import json
import math
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QAbstractItemView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..batch_project import BatchProject, BatchProjectService, ProjectVideo
from ..common.models import STEP_ORDER, StepId
from ..common.services.ffmpeg_service import FFmpegService


STATUS_LABELS = {
    "pending": "Chưa tạo",
    "processing": "Đang xử lý",
    "completed": "Hoàn thành",
    "failed": "Thất bại",
}
STEP_VALUES = [step.value for step in STEP_ORDER]
STEP_CANDIDATE_FIELDS = (
    "audio_candidates", "transcript_candidates", "translation_candidates",
    "tts_candidates", "sync_candidates", "build_audio_candidates", "render_candidates",
)


class VideoProgressDialog(QDialog):
    """Read-only summary of recorded errors and completed outputs."""

    def __init__(self, project: BatchProject, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Trạng thái output của video")
        self.resize(1050, 580)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "STT", "Group", "Video", "Step lỗi\ncao nhất", "SL output\nStep lỗi",
            "Step hoàn thành\ncao nhất (1–6)", "SL output\nStep hoàn thành",
            "SL video\nStep 7",
        ])
        header = self.table.horizontalHeader()
        header.setMinimumHeight(44)
        for column in (0, 1, 3, 4, 5, 6, 7):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)
        close_button = QPushButton("Đóng")
        close_button.clicked.connect(self.accept)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(close_button)
        layout.addLayout(actions)
        self.refresh()

    def _progress(self, video: ProjectVideo) -> tuple[str, str, str, str, str]:
        render_count = len(BatchProjectService.render_outputs(self.project, video.id))
        try:
            saved = BatchProjectService.read_video(self.project, video.id)
        except (OSError, ValueError, TypeError):
            return "Không rõ", "—", "Không rõ", "—", str(render_count)
        if saved is None:
            counts = [0] * len(STEP_ORDER)
            pipeline = {}
            candidates_by_step = [[] for _ in STEP_ORDER]
        else:
            candidates_by_step = [getattr(saved, field, []) for field in STEP_CANDIDATE_FIELDS]
            counts = [len(items) for items in candidates_by_step]
            pipeline = saved.pipeline if isinstance(saved.pipeline, dict) else {}

        errors = set()
        if video.status == "failed" and video.failed_step in STEP_VALUES:
            errors.add(STEP_VALUES.index(video.failed_step))
        for index, step in enumerate(STEP_ORDER[:6]):
            stored = pipeline.get(step.value)
            metadata = stored.get("metadata", {}) if isinstance(stored, dict) else {}
            if not isinstance(metadata, dict):
                metadata = {}
            if step == StepId.STT and metadata.get("timeline_error_count", 0):
                errors.add(index)
            if step == StepId.SYNC:
                latest = max(
                    candidates_by_step[index],
                    key=lambda item: str(item.get("created_at", "")),
                    default={},
                )
                if latest.get("error_count", 0) or metadata.get("error_count", 0):
                    errors.add(index)

        error_index = max(errors) if errors else None
        completed = []
        for index, items in enumerate(candidates_by_step[:6]):
            if not items:
                continue
            if index == 4 and not any(not item.get("error_count", 0) for item in items):
                continue
            if index == 1 and index in errors and len(items) == 1:
                continue
            completed.append(index)
        completed_index = max(completed) if completed else None
        error_step = f"Step {error_index + 1:02d}" if error_index is not None else "—"
        error_outputs = str(counts[error_index]) if error_index is not None else "—"
        completed_step = (
            f"Step {completed_index + 1:02d}"
            if completed_index is not None else "—"
        )
        completed_outputs = str(counts[completed_index]) if completed_index is not None else "—"
        return error_step, error_outputs, completed_step, completed_outputs, str(render_count)

    def refresh(self) -> None:
        self.table.setRowCount(0)
        for number, video in enumerate(self.project.ordered_videos(), start=1):
            row = self.table.rowCount()
            self.table.insertRow(row)
            name = QTableWidgetItem(video.name)
            name.setToolTip(video.source_path)
            values = (
                str(number), str(self.project.video_group(video.id)), name,
                *self._progress(video),
            )
            for column, value in enumerate(values):
                self.table.setItem(
                    row, column,
                    value if isinstance(value, QTableWidgetItem) else QTableWidgetItem(value),
                )


class BatchVideosDialog(QDialog):
    run_requested = Signal(object)
    video_requested = Signal(str)

    def __init__(self, project: BatchProject, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self.start_step_selectors: dict[str, QComboBox] = {}
        self.start_steps = {
            video.id: video.batch_start_step if video.batch_start_step in STEP_VALUES else ""
            for video in project.videos
        }
        self.setWindowTitle("Danh sách video của project")
        self.resize(1120, 600)
        layout = QVBoxLayout(self)
        helper = QLabel(
            "Trạng thái và Step bên trái là kết quả gần nhất của Chạy batch video đã chọn; "
            "video chưa có lịch sử dùng mặc định Chưa tạo / Step 04 nếu chưa có output render. "
            "Tích các video, chọn Step chung rồi bấm Set Step, hoặc chọn Step riêng từng dòng. "
            "Batch chạy đúng step đã chọn đến Step 7; thiếu đầu vào sẽ báo lỗi, không tự đổi step. "
            "Step và checkbox được lưu vào project khi bấm Lưu Step bắt đầu hoặc chạy batch."
        )
        helper.setWordWrap(True)
        layout.addWidget(helper)
        selection_actions = QHBoxLayout()
        self.select_all_button = QPushButton("Chọn tất cả")
        self.select_all_button.clicked.connect(lambda _checked=False: self._set_all_checked(True))
        selection_actions.addWidget(self.select_all_button)
        self.clear_selection_button = QPushButton("Bỏ chọn tất cả")
        self.clear_selection_button.clicked.connect(lambda _checked=False: self._set_all_checked(False))
        selection_actions.addWidget(self.clear_selection_button)
        selection_actions.addStretch()
        selection_actions.addWidget(QLabel("Step chung:"))
        self.common_step_selector = QComboBox()
        self.common_step_selector.addItem("Chọn Step…", "")
        for number, step_id in enumerate(STEP_ORDER, start=1):
            self.common_step_selector.addItem(f"Step {number:02d}", step_id.value)
        self.common_step_selector.currentIndexChanged.connect(
            lambda _index: self._selection_changed()
        )
        selection_actions.addWidget(self.common_step_selector)
        self.apply_step_button = QPushButton("Set Step")
        self.apply_step_button.clicked.connect(lambda _checked=False: self._apply_common_step())
        selection_actions.addWidget(self.apply_step_button)
        layout.addLayout(selection_actions)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Chọn", "Video", "Trạng thái", "Step gợi ý / đang chạy", "Step bắt đầu", "Số output", "Lỗi gần nhất"]
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        self.table.cellDoubleClicked.connect(self._open_row)
        self.table.itemChanged.connect(self._selection_changed)
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        self.save_steps_button = QPushButton("Lưu Step bắt đầu")
        self.save_steps_button.clicked.connect(self._save_start_steps)
        actions.addWidget(self.save_steps_button)
        self.selection_info = QLabel()
        actions.addWidget(self.selection_info)
        actions.addStretch()
        self.run_button = QPushButton("Chạy batch video đã chọn")
        self.run_button.setObjectName("primaryButton")
        self.run_button.setEnabled(False)
        self.run_button.clicked.connect(self._run_selected)
        actions.addWidget(self.run_button)
        self.close_button = QPushButton("Đóng")
        self.close_button.clicked.connect(self.accept)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self) -> None:
        self.table.blockSignals(True)
        self.start_step_selectors.clear()
        self.table.setRowCount(0)
        for video in self.project.ordered_videos():
            row = self.table.rowCount()
            self.table.insertRow(row)
            checked = QTableWidgetItem()
            checked.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            checked.setCheckState(
                Qt.CheckState.Checked if video.batch_selected else Qt.CheckState.Unchecked
            )
            checked.setData(Qt.ItemDataRole.UserRole, video.id)
            self.table.setItem(row, 0, checked)
            name = QTableWidgetItem(video.name)
            name.setData(Qt.ItemDataRole.UserRole, video.id)
            self.table.setItem(row, 1, name)
            batch_status = video.selected_batch_status
            batch_step = video.selected_batch_step
            batch_error = video.selected_batch_error
            if batch_status in {"processing", "failed", "completed"}:
                status_label = STATUS_LABELS[batch_status]
                if batch_status == "completed":
                    step_label = "Đã xong Step 07"
                    detail = "Kết quả gần nhất của Chạy batch video đã chọn: hoàn thành Step 07."
                elif batch_step in STEP_VALUES:
                    step = StepId(batch_step)
                    step_label = f"Step {STEP_ORDER.index(step) + 1:02d}"
                    detail = (
                        "Step thực tế đang chạy của Chạy batch video đã chọn."
                        if batch_status == "processing"
                        else "Step lỗi của lần Chạy batch video đã chọn gần nhất."
                    )
                else:
                    step_label = "Không rõ"
                    detail = "Lần chạy batch video đã chọn chưa lưu được step cụ thể."
            elif video.output_count < 1:
                status_label = STATUS_LABELS["pending"]
                step_label = "Step 04"
                batch_error = ""
                detail = "Chưa có output Step 07 và chưa có lịch sử Chạy batch video đã chọn."
            else:
                status_label = STATUS_LABELS["completed"]
                step_label = "Đã xong Step 07"
                batch_error = ""
                detail = "Đã có output Step 07; chưa có lịch sử Chạy batch video đã chọn."
            self.table.setItem(row, 2, QTableWidgetItem(status_label))
            step_item = QTableWidgetItem(step_label)
            step_item.setToolTip(detail)
            self.table.setItem(row, 3, step_item)
            selector = QComboBox()
            selector.addItem("Chọn step…", "")
            for number, step_id in enumerate(STEP_ORDER, start=1):
                selector.addItem(f"Step {number:02d}", step_id.value)
            selector.setCurrentIndex(max(0, selector.findData(self.start_steps[video.id])))
            selector.setEnabled(not self._pipeline_busy())
            selector.currentIndexChanged.connect(
                lambda _index, video_id=video.id: self._start_step_changed(video_id)
            )
            self.start_step_selectors[video.id] = selector
            self.table.setCellWidget(row, 4, selector)
            self.table.setItem(row, 5, QTableWidgetItem(str(video.output_count)))
            self.table.setItem(row, 6, QTableWidgetItem(batch_error))
        self.table.blockSignals(False)
        self._selection_changed()

    def _selected_video_ids(self) -> list[str]:
        return [
            str(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole))
            for row in range(self.table.rowCount())
            if self.table.item(row, 0)
            and self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        ]

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None:
                item.setCheckState(state)
        self.table.blockSignals(False)
        self._selection_changed()

    def _apply_common_step(self) -> None:
        step = str(self.common_step_selector.currentData() or "")
        if step in STEP_VALUES and not self._pipeline_busy():
            for video_id in self._selected_video_ids():
                selector = self.start_step_selectors[video_id]
                selector.setCurrentIndex(selector.findData(step))
        self._selection_changed()

    def _selection_changed(self, _item: QTableWidgetItem | None = None) -> None:
        selected = self._selected_video_ids()
        missing = sum(self.start_steps[video_id] not in STEP_VALUES for video_id in selected)
        busy = self._pipeline_busy()
        self.run_button.setEnabled(bool(selected) and not missing and not busy)
        self.save_steps_button.setEnabled(self._choices_dirty() and not busy)
        self.common_step_selector.setEnabled(bool(selected) and not busy)
        self.apply_step_button.setEnabled(
            bool(selected) and self.common_step_selector.currentData() in STEP_VALUES and not busy
        )
        self.select_all_button.setEnabled(bool(self.project.videos) and not busy)
        self.clear_selection_button.setEnabled(bool(selected) and not busy)
        self.selection_info.setText(
            f"{missing} video đã chọn chưa có Step bắt đầu" if missing else f"Đã chọn {len(selected)} video"
        )

    def _pipeline_busy(self) -> bool:
        parent = self.parent()
        return bool(getattr(parent, "batch_running", False) or getattr(parent, "active_workers", None))

    def _start_step_changed(self, video_id: str) -> None:
        self.start_steps[video_id] = str(self.start_step_selectors[video_id].currentData() or "")
        self._selection_changed()

    def _steps_dirty(self) -> bool:
        return any(video.batch_start_step != self.start_steps[video.id] for video in self.project.videos)

    def _choices_dirty(self) -> bool:
        selected = set(self._selected_video_ids())
        return self._steps_dirty() or any(
            video.batch_selected != (video.id in selected) for video in self.project.videos
        )

    def _save_start_steps(self) -> bool:
        if self._pipeline_busy():
            QMessageBox.information(self, "Pipeline đang chạy", "Hãy chờ tác vụ hiện tại hoàn tất trước khi lưu lựa chọn.")
            return False
        previous = {
            video.id: (video.batch_start_step, video.batch_selected)
            for video in self.project.videos
        }
        selected = set(self._selected_video_ids())
        for video in self.project.videos:
            video.batch_start_step = self.start_steps[video.id]
            video.batch_selected = video.id in selected
        try:
            self.project.save()
        except OSError as exc:
            for video in self.project.videos:
                video.batch_start_step, video.batch_selected = previous[video.id]
            QMessageBox.warning(self, "Không thể lưu Step bắt đầu", str(exc))
            return False
        self._selection_changed()
        return True

    def _run_selected(self) -> None:
        selected = self._selected_video_ids()
        if not selected or self._pipeline_busy():
            return
        if any(self.start_steps[video_id] not in STEP_VALUES for video_id in selected):
            QMessageBox.information(self, "Chưa chọn Step bắt đầu", "Hãy chọn Step bắt đầu cho tất cả video đã tích.")
            return
        if self._choices_dirty() and not self._save_start_steps():
            return
        self.run_requested.emit({video_id: self.start_steps[video_id] for video_id in selected})

    def _open_row(self, row: int, _column: int) -> None:
        if _column in {0, 4}:
            return
        item = self.table.item(row, 1)
        if item:
            self.accept()
            if self.result() == QDialog.DialogCode.Accepted:
                self.video_requested.emit(str(item.data(Qt.ItemDataRole.UserRole)))

    def done(self, result: int) -> None:
        if self._choices_dirty():
            choice = QMessageBox.question(
                self, "Lựa chọn chưa lưu", "Bạn có muốn lưu Step bắt đầu và các video đã chọn?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                return
            if choice == QMessageBox.StandardButton.Save and not self._save_start_steps():
                return
        super().done(result)


class MergeCancelled(Exception):
    pass


class MergeWorker(QThread):
    succeeded = Signal(str)
    failed = Signal(str)
    cancelled = Signal()
    progress_changed = Signal(int, str)

    def __init__(self, inputs: list[str], output: str) -> None:
        super().__init__()
        self.inputs = inputs
        self.output = output
        self._cancel = threading.Event()
        self._process: subprocess.Popen[str] | None = None

    def cancel(self) -> None:
        self._cancel.set()
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise MergeCancelled()

    def _command(self, command: list[str], start: int, end: int, label: str, duration: float) -> None:
        self._check_cancel()
        command[-1:-1] = ["-progress", "pipe:1", "-nostats"]
        self._process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        process = self._process
        errors: list[str] = []

        def read_errors() -> None:
            if process.stderr is not None:
                for line in process.stderr:
                    errors.append(line)
                    if len(errors) > 100:
                        del errors[:50]

        stderr_reader = threading.Thread(target=read_errors, daemon=True)
        stderr_reader.start()
        try:
            assert process.stdout is not None
            for raw in process.stdout:
                self._check_cancel()
                key, _, value = raw.strip().partition("=")
                if key in {"out_time_us", "out_time_ms"}:
                    try:
                        elapsed = int(value) / 1_000_000
                    except ValueError:
                        continue
                    percent = start + min(1.0, elapsed / max(duration, 0.001)) * (end - start)
                    self.progress_changed.emit(min(end, round(percent)), label)
            code = process.wait()
            stderr_reader.join()
            stderr = "".join(errors)
            self._check_cancel()
            if code != 0:
                raise RuntimeError(stderr.strip() or f"FFmpeg kết thúc với mã {code}.")
            if "non monotonically increasing dts" in stderr.lower():
                raise RuntimeError("FFmpeg phát hiện timestamp không đồng bộ khi ghép.")
            self.progress_changed.emit(end, label)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait()
            stderr_reader.join()
            self._process = None

    def _probe(self, ffprobe: str, path: Path) -> dict:
        self._check_cancel()
        completed = subprocess.run(
            [ffprobe, "-v", "error", "-show_streams", "-show_format",
             "-show_data_hash", "sha256", "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
        )
        self._check_cancel()
        if completed.returncode != 0:
            raise RuntimeError(f"Không đọc được video: {path}\n{completed.stderr.strip()}")
        data = json.loads(completed.stdout)
        streams = data.get("streams", [])
        if not any(s.get("codec_type") == "video" for s in streams) or not any(
            s.get("codec_type") == "audio" for s in streams
        ):
            raise RuntimeError(f"Video thiếu hình hoặc âm thanh: {path}")
        duration = float(data.get("format", {}).get("duration", 0))
        if not math.isfinite(duration) or duration <= 0:
            raise RuntimeError(f"Thời lượng video không hợp lệ: {path}")
        data["duration_seconds"] = duration
        return data

    @staticmethod
    def _compatible(probes: list[dict]) -> bool:
        video_keys = ("codec_name", "profile", "level", "width", "height", "pix_fmt",
                      "sample_aspect_ratio", "time_base", "avg_frame_rate", "extradata_hash")
        audio_keys = ("codec_name", "profile", "sample_rate", "channels", "channel_layout",
                      "time_base", "extradata_hash")
        signatures = []
        for data in probes:
            streams = data["streams"]
            videos = [s for s in streams if s.get("codec_type") == "video"]
            audios = [s for s in streams if s.get("codec_type") == "audio"]
            if len(videos) != 1 or len(audios) != 1:
                return False
            if videos[0].get("codec_name") != "h264" or audios[0].get("codec_name") != "aac":
                return False
            signatures.append((
                tuple(videos[0].get(key) for key in video_keys),
                tuple(audios[0].get(key) for key in audio_keys),
            ))
        return all(signature == signatures[0] for signature in signatures[1:])

    @staticmethod
    def _write_list(path: Path, inputs: list[Path]) -> None:
        lines = []
        for item in inputs:
            safe = item.resolve().as_posix().replace("'", "'\\''")
            lines.append(f"file '{safe}'")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _concat(self, ffmpeg: str, inputs: list[Path], list_file: Path,
                temporary: Path, duration: float, start: int, end: int) -> None:
        self._write_list(list_file, inputs)
        self._command(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "warning", "-fflags", "+genpts",
             "-f", "concat", "-safe", "0", "-i", str(list_file),
             "-map", "0:v:0", "-map", "0:a:0", "-c", "copy",
             "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(temporary)],
            start, end, "Đang nối video…", duration,
        )

    def _validate(self, ffmpeg: str, ffprobe: str, path: Path,
                  expected: float, start: int, end: int) -> None:
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("File MP4 đầu ra không tồn tại hoặc rỗng.")
        actual = self._probe(ffprobe, path)["duration_seconds"]
        if abs(actual - expected) > max(1.0, expected * 0.01):
            raise RuntimeError(f"Thời lượng video ghép không khớp ({actual:.2f}s / {expected:.2f}s).")
        self.progress_changed.emit(start, "Đang kiểm tra hình ảnh và âm thanh…")
        self._command(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-xerror", "-err_detect", "explode",
             "-i", str(path), "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "NUL"],
            start, end, "Đang kiểm tra hình ảnh và âm thanh…", actual,
        )

    def run(self) -> None:
        output = Path(self.output)
        token = uuid.uuid4().hex
        list_file = output.with_name(f".{output.stem}.{token}.concat.txt")
        temporary = output.with_name(f".{output.stem}.{token}.part.mp4")
        normalized: list[Path] = []
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            service = FFmpegService()
            if not service.ffprobe_path:
                raise RuntimeError("Không tìm thấy FFprobe để kiểm tra video đầu vào/đầu ra.")
            ffmpeg, ffprobe = service.ffmpeg_path, service.ffprobe_path
            inputs = [Path(item) for item in self.inputs]
            probes = [self._probe(ffprobe, item) for item in inputs]
            duration = sum(data["duration_seconds"] for data in probes)
            copied = False
            if self._compatible(probes):
                try:
                    self._concat(ffmpeg, inputs, list_file, temporary, duration, 0, 75)
                    self._validate(ffmpeg, ffprobe, temporary, duration, 75, 99)
                    copied = True
                except MergeCancelled:
                    raise
                except (OSError, RuntimeError, ValueError):
                    temporary.unlink(missing_ok=True)
                    self.progress_changed.emit(0, "Ghép nhanh không phù hợp; đang chuẩn hóa video…")
            if not copied:
                first_video = next(s for s in probes[0]["streams"] if s.get("codec_type") == "video")
                width = max(2, int(first_video["width"]) // 2 * 2)
                height = max(2, int(first_video["height"]) // 2 * 2)
                for index, (source, probe) in enumerate(zip(inputs, probes)):
                    self._check_cancel()
                    target = output.with_name(f".{output.stem}.{token}.{index}.mp4")
                    normalized.append(target)
                    start = round(index / len(inputs) * 65)
                    end = round((index + 1) / len(inputs) * 65)
                    self._command(
                        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
                         "-map", "0:v:0", "-map", "0:a:0",
                         "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                                "fps=30,format=yuv420p,setsar=1,setpts=PTS-STARTPTS",
                         "-af", "aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS",
                         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                         "-video_track_timescale", "30000", str(target)],
                        start, end, f"Đang chuẩn hóa video {index + 1}/{len(inputs)}…",
                        probe["duration_seconds"],
                    )
                normalized_probes = [self._probe(ffprobe, item) for item in normalized]
                normalized_duration = sum(item["duration_seconds"] for item in normalized_probes)
                self._concat(ffmpeg, normalized, list_file, temporary, normalized_duration, 65, 80)
                self._validate(ffmpeg, ffprobe, temporary, normalized_duration, 80, 99)
            self._check_cancel()
            temporary.replace(output)
            self.progress_changed.emit(100, "Đã ghép và kiểm tra video.")
            self.succeeded.emit(str(output))
        except MergeCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            temporary.unlink(missing_ok=True)
            list_file.unlink(missing_ok=True)
            for item in normalized:
                item.unlink(missing_ok=True)


class OutputManagerDialog(QDialog):
    def __init__(self, project: BatchProject, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self.worker: MergeWorker | None = None
        self.setWindowTitle("Output của project")
        self.resize(980, 580)
        layout = QVBoxLayout(self)
        helper = QLabel(
            "Thứ tự lấy từ Thiết lập project → Project & Input. "
            "Chỉ các video được tích sẽ được ghép từ trên xuống dưới; chọn một dòng rồi bấm Xem để kiểm tra."
        )
        helper.setWordWrap(True)
        layout.addWidget(helper)
        row_actions = QHBoxLayout()
        self.view_button = QPushButton("Xem")
        self.view_button.clicked.connect(self._view_selected)
        row_actions.addWidget(self.view_button)
        row_actions.addStretch()
        layout.addLayout(row_actions)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Ghép", "Thứ tự", "Video", "Phiên bản output"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._update_row_actions)
        layout.addWidget(self.table)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        self.progress_label = QLabel("")
        layout.addWidget(self.progress_label)
        actions = QHBoxLayout()
        actions.addStretch()
        self.cancel_button = QPushButton("Hủy ghép")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_merge)
        actions.addWidget(self.cancel_button)
        self.merge_button = QPushButton("Ghép các output đã chọn")
        self.merge_button.setObjectName("primaryButton")
        self.merge_button.clicked.connect(self._merge)
        actions.addWidget(self.merge_button)
        self.close_button = QPushButton("Đóng")
        self.close_button.clicked.connect(self.accept)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self) -> None:
        previous: dict[str, tuple[bool, str]] = {}
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 2)
            checked = self.table.item(row, 0)
            combo = self.table.cellWidget(row, 3)
            if name and checked and isinstance(combo, QComboBox):
                previous[str(name.data(Qt.ItemDataRole.UserRole))] = (
                    checked.checkState() == Qt.CheckState.Checked,
                    str(combo.currentData() or ""),
                )
        self.table.setRowCount(0)
        for video in self.project.ordered_videos():
            outputs = BatchProjectService.render_outputs(self.project, video.id)
            row = self.table.rowCount()
            self.table.insertRow(row)
            checked = QTableWidgetItem()
            checked.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            checked.setCheckState(Qt.CheckState.Checked if previous.get(video.id, (False, ""))[0]
                                  else Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, checked)
            number = QTableWidgetItem(str(row + 1))
            number.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row, 1, number)
            name = QTableWidgetItem(video.name)
            name.setData(Qt.ItemDataRole.UserRole, video.id)
            self.table.setItem(row, 2, name)
            combo = QComboBox()
            for output in outputs:
                combo.addItem(output["id"], output["video_file"])
            selected = combo.findData(previous.get(video.id, (False, ""))[1])
            if selected >= 0:
                combo.setCurrentIndex(selected)
            combo.setEnabled(bool(outputs))
            self.table.setCellWidget(row, 3, combo)
        self._update_row_actions()

    def _selected_row(self) -> int:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _update_row_actions(self) -> None:
        row = self._selected_row()
        combo = self.table.cellWidget(row, 3) if row >= 0 else None
        self.view_button.setEnabled(isinstance(combo, QComboBox) and bool(combo.currentData()))

    def _view_selected(self) -> None:
        row = self._selected_row()
        combo = self.table.cellWidget(row, 3) if row >= 0 else None
        if not isinstance(combo, QComboBox):
            return
        path = str(combo.currentData() or "")
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _selected_paths(self) -> list[str]:
        selected: list[str] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            combo = self.table.cellWidget(row, 3)
            if item and item.checkState() == Qt.CheckState.Checked and isinstance(combo, QComboBox):
                path = str(combo.currentData() or "")
                if path:
                    selected.append(path)
        return selected

    def _merge(self) -> None:
        inputs = self._selected_paths()
        if len(inputs) < 2:
            QMessageBox.information(self, "Chưa đủ output", "Hãy chọn ít nhất hai output để ghép.")
            return
        default_dir = self.project.path("output", "merged")
        default_name = f"merged-{datetime.now().strftime('%Y%m%d-%H%M%S')}.mp4"
        output, _ = QFileDialog.getSaveFileName(
            self,
            "Lưu video đã ghép",
            str(default_dir / default_name),
            "MP4 Video (*.mp4)",
        )
        if not output:
            return
        target = Path(output)
        if target.resolve() in {Path(item).resolve() for item in inputs}:
            QMessageBox.warning(self, "Đường dẫn không hợp lệ", "File đích trùng với một video đầu vào. Hãy chọn tên khác.")
            return
        if target.exists():
            answer = QMessageBox.question(self, "Thay thế file?", f"File đã tồn tại:\n{output}\nThay thế sau khi ghép thành công?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.merge_button.setEnabled(False)
        self.merge_button.setText("Đang ghép…")
        self.close_button.setEnabled(False)
        self.table.setEnabled(False)
        self.view_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.progress_label.setText("Đang kiểm tra video đầu vào…")
        self.worker = MergeWorker(inputs, output)
        self.worker.succeeded.connect(lambda path: self._merge_succeeded(path, inputs))
        self.worker.failed.connect(self._merge_failed)
        self.worker.cancelled.connect(self._merge_cancelled)
        self.worker.progress_changed.connect(self._merge_progress)
        self.worker.finished.connect(self._merge_finished)
        self.worker.start()

    def _merge_succeeded(self, path: str, inputs: list[str]) -> None:
        self.project.merge_outputs.append({
            "path": path,
            "inputs": inputs,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        })
        self.project.save()
        QMessageBox.information(self, "Đã ghép video", f"Đã tạo:\n{path}")

    def _merge_failed(self, error: str) -> None:
        QMessageBox.critical(self, "Không thể ghép video", error)

    def _merge_cancelled(self) -> None:
        self.progress_label.setText("Đã hủy ghép video.")

    def _merge_progress(self, percent: int, message: str) -> None:
        self.progress_bar.setValue(percent)
        self.progress_label.setText(f"{message} {percent}%")

    def _cancel_merge(self) -> None:
        if self.worker and self.worker.isRunning():
            self.cancel_button.setEnabled(False)
            self.progress_label.setText("Đang hủy và dọn file tạm…")
            self.worker.cancel()

    def _merge_finished(self) -> None:
        self.merge_button.setEnabled(True)
        self.merge_button.setText("Ghép các output đã chọn")
        self.close_button.setEnabled(True)
        self.table.setEnabled(True)
        self._update_row_actions()
        self.cancel_button.setEnabled(False)
        if self.worker:
            self.worker.deleteLater()
            self.worker = None

    def reject(self) -> None:
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Đang ghép video", "Hãy chờ FFmpeg hoàn thành trước khi đóng.")
            return
        super().reject()
