from __future__ import annotations

import subprocess
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
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..batch_project import BatchProject, BatchProjectService
from ..models import STEP_ORDER
from ..services.ffmpeg_service import FFmpegService


STATUS_LABELS = {
    "pending": "Chưa tạo",
    "processing": "Đang xử lý",
    "completed": "Hoàn thành",
    "failed": "Thất bại",
}
STEP_VALUES = [step.value for step in STEP_ORDER]


class BatchVideosDialog(QDialog):
    run_requested = Signal()
    video_requested = Signal(str)

    def __init__(self, project: BatchProject, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Danh sách video của project")
        self.resize(900, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Nhấp đúp một video để xem các output của video đó trên Step 1–7."))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Video", "Trạng thái", "Step", "Số output", "Lỗi gần nhất"]
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.cellDoubleClicked.connect(self._open_row)
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        actions.addStretch()
        run = QPushButton("Chạy batch")
        run.setObjectName("primaryButton")
        run.clicked.connect(self.run_requested.emit)
        actions.addWidget(run)
        self.close_button = QPushButton("Đóng")
        self.close_button.clicked.connect(self.accept)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self) -> None:
        self.table.setRowCount(0)
        for video in self.project.videos:
            row = self.table.rowCount()
            self.table.insertRow(row)
            name = QTableWidgetItem(video.name)
            name.setData(Qt.ItemDataRole.UserRole, video.id)
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(STATUS_LABELS.get(video.status, video.status)))
            step = video.failed_step or video.current_step or video.restart_step
            step_label = f"Step {STEP_VALUES.index(step) + 1:02d}" if step in STEP_VALUES else "—"
            self.table.setItem(row, 2, QTableWidgetItem(step_label))
            self.table.setItem(row, 3, QTableWidgetItem(str(video.output_count)))
            self.table.setItem(row, 4, QTableWidgetItem(video.last_error))

    def _open_row(self, row: int, _column: int) -> None:
        item = self.table.item(row, 0)
        if item:
            self.video_requested.emit(str(item.data(Qt.ItemDataRole.UserRole)))
            self.accept()


class MergeWorker(QThread):
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, inputs: list[str], output: str) -> None:
        super().__init__()
        self.inputs = inputs
        self.output = output

    def run(self) -> None:
        output = Path(self.output)
        list_file = output.with_suffix(".concat.txt")
        temporary = output.with_name(f"{output.stem}.part{output.suffix}")
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            lines: list[str] = []
            for path in self.inputs:
                safe_path = path.replace("\\", "/").replace("'", "'\\''")
                lines.append(f"file '{safe_path}'")
            list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            ffmpeg = FFmpegService().ffmpeg_path
            command = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", str(temporary),
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            if completed.returncode != 0 or not temporary.is_file():
                raise RuntimeError(completed.stderr.strip() or "FFmpeg không tạo được video ghép.")
            temporary.replace(output)
            self.succeeded.emit(str(output))
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            self.failed.emit(str(exc))
        finally:
            list_file.unlink(missing_ok=True)


class OutputManagerDialog(QDialog):
    def __init__(self, project: BatchProject, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self.worker: MergeWorker | None = None
        self.setWindowTitle("Output của project")
        self.resize(980, 580)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Chọn một output cho mỗi video, đánh dấu các row cần ghép theo thứ tự hiển thị."))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Ghép", "Video", "Phiên bản output", "Xem"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        actions.addStretch()
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
        self.table.setRowCount(0)
        for video in self.project.videos:
            outputs = BatchProjectService.render_outputs(self.project, video.id)
            row = self.table.rowCount()
            self.table.insertRow(row)
            checked = QTableWidgetItem()
            checked.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            checked.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, checked)
            self.table.setItem(row, 1, QTableWidgetItem(video.name))
            combo = QComboBox()
            for output in outputs:
                combo.addItem(output["id"], output["video_file"])
            combo.setEnabled(bool(outputs))
            self.table.setCellWidget(row, 2, combo)
            view = QPushButton("Xem")
            view.setEnabled(bool(outputs))
            view.clicked.connect(lambda _checked=False, box=combo: self._view(box))
            self.table.setCellWidget(row, 3, view)

    @staticmethod
    def _view(combo: QComboBox) -> None:
        path = str(combo.currentData() or "")
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _selected_paths(self) -> list[str]:
        selected: list[str] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            combo = self.table.cellWidget(row, 2)
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
        self.merge_button.setEnabled(False)
        self.merge_button.setText("Đang ghép…")
        self.close_button.setEnabled(False)
        self.worker = MergeWorker(inputs, output)
        self.worker.succeeded.connect(lambda path: self._merge_succeeded(path, inputs))
        self.worker.failed.connect(self._merge_failed)
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

    def _merge_finished(self) -> None:
        self.merge_button.setEnabled(True)
        self.merge_button.setText("Ghép các output đã chọn")
        self.close_button.setEnabled(True)
        if self.worker:
            self.worker.deleteLater()
            self.worker = None

    def reject(self) -> None:
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Đang ghép video", "Hãy chờ FFmpeg hoàn thành trước khi đóng.")
            return
        super().reject()
