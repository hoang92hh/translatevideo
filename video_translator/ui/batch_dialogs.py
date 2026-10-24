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

from ..batch_project import BatchProject, BatchProjectService
from ..common.models import STEP_ORDER
from ..common.services.ffmpeg_service import FFmpegService


STATUS_LABELS = {
    "pending": "Chưa tạo",
    "processing": "Đang xử lý",
    "completed": "Hoàn thành",
    "failed": "Thất bại",
}
STEP_VALUES = [step.value for step in STEP_ORDER]


class BatchVideosDialog(QDialog):
    run_requested = Signal(object)
    video_requested = Signal(str)

    def __init__(self, project: BatchProject, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Danh sách video của project")
        self.resize(900, 560)
        layout = QVBoxLayout(self)
        helper = QLabel(
            "Đánh dấu video cần chạy batch. Video hoàn thành sẽ chạy lại từ Step 1; "
            "video thất bại tiếp tục từ step lỗi. Nhấp đúp để xem output Step 1–7."
        )
        helper.setWordWrap(True)
        layout.addWidget(helper)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Chọn", "Video", "Trạng thái", "Step", "Số output", "Lỗi gần nhất"]
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.cellDoubleClicked.connect(self._open_row)
        self.table.itemChanged.connect(self._selection_changed)
        layout.addWidget(self.table)
        actions = QHBoxLayout()
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
        self.table.setRowCount(0)
        for video in self.project.videos:
            row = self.table.rowCount()
            self.table.insertRow(row)
            checked = QTableWidgetItem()
            checked.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            checked.setCheckState(Qt.CheckState.Unchecked)
            checked.setData(Qt.ItemDataRole.UserRole, video.id)
            self.table.setItem(row, 0, checked)
            name = QTableWidgetItem(video.name)
            name.setData(Qt.ItemDataRole.UserRole, video.id)
            self.table.setItem(row, 1, name)
            self.table.setItem(row, 2, QTableWidgetItem(STATUS_LABELS.get(video.status, video.status)))
            step = (
                STEP_VALUES[-1]
                if video.status == "completed"
                else video.failed_step
                if video.status == "failed"
                else video.current_step or video.restart_step
            )
            step_label = f"Step {STEP_VALUES.index(step) + 1:02d}" if step in STEP_VALUES else "—"
            self.table.setItem(row, 3, QTableWidgetItem(step_label))
            self.table.setItem(row, 4, QTableWidgetItem(str(video.output_count)))
            self.table.setItem(row, 5, QTableWidgetItem(video.last_error))
        self.table.blockSignals(False)
        self._selection_changed()

    def _selected_video_ids(self) -> list[str]:
        return [
            str(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole))
            for row in range(self.table.rowCount())
            if self.table.item(row, 0)
            and self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        ]

    def _selection_changed(self, _item: QTableWidgetItem | None = None) -> None:
        self.run_button.setEnabled(bool(self._selected_video_ids()))

    def _run_selected(self) -> None:
        selected = self._selected_video_ids()
        if selected:
            self.run_requested.emit(selected)

    def _open_row(self, row: int, _column: int) -> None:
        item = self.table.item(row, 1)
        if item:
            self.video_requested.emit(str(item.data(Qt.ItemDataRole.UserRole)))
            self.accept()


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
        self._video_order = [video.id for video in project.videos]
        self.setWindowTitle("Output của project")
        self.resize(980, 580)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Chọn một dòng rồi dùng Xem/Lên/Xuống. Chỉ các video được tích sẽ được ghép từ trên xuống dưới."))
        row_actions = QHBoxLayout()
        self.view_button = QPushButton("Xem")
        self.view_button.clicked.connect(self._view_selected)
        row_actions.addWidget(self.view_button)
        self.up_button = QPushButton("Lên")
        self.up_button.clicked.connect(lambda: self._move_selected(-1))
        row_actions.addWidget(self.up_button)
        self.down_button = QPushButton("Xuống")
        self.down_button.clicked.connect(lambda: self._move_selected(1))
        row_actions.addWidget(self.down_button)
        row_actions.addStretch()
        layout.addLayout(row_actions)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Ghép", "Thứ tự", "Video", "Phiên bản output"])
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
        videos = {video.id: video for video in self.project.videos}
        for video_id in self._video_order:
            video = videos.get(video_id)
            if video is None:
                continue
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
        self.up_button.setEnabled(row > 0)
        self.down_button.setEnabled(0 <= row < self.table.rowCount() - 1)

    def _move_selected(self, direction: int) -> None:
        source = self._selected_row()
        target = source + direction
        if source < 0 or target < 0 or target >= len(self._video_order):
            return
        self._video_order[source], self._video_order[target] = (
            self._video_order[target], self._video_order[source]
        )
        self.refresh()
        self.table.selectRow(target)

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
        self.up_button.setEnabled(False)
        self.down_button.setEnabled(False)
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
