from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from ..batch_project import BatchProject, BatchProjectService
from ..common.models import StepId
from ..state import ProjectState


class _TextImportWorker(QThread):
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, project: BatchProject, video_id: str, parent: QObject) -> None:
        super().__init__(parent)
        self.project = project
        self.video_id = video_id

    def run(self) -> None:
        try:
            record = self.project.video(self.video_id)
            if record is None:
                raise ValueError("Video không còn trong project.")
            input_folder = Path(self.project.input_folder)
            stem = Path(record.source_path).stem
            source = next(
                (path for suffix in (".srt", ".txt")
                 if (path := input_folder / f"{stem}{suffix}").is_file()),
                None,
            )
            if source is None:
                raise FileNotFoundError(f"Không tìm thấy {stem}.srt hoặc {stem}.txt trong thư mục input.")
            state = ProjectState()
            state.bind_project(BatchProjectService.open_video(self.project, self.video_id))
            candidate_id = state.selected_transcript_candidate_id if source.suffix.lower() == ".txt" else ""
            imported = state.import_transcript_text(str(source), candidate_id)
            self.succeeded.emit(f"{source.name}: đã nhập {imported.segment_count} segment")
        except Exception as exc:
            self.failed.emit(str(exc))


class TextImportBatchRunner(QObject):
    video_changed = Signal(str)
    progress_changed = Signal(int, str)
    worker_started = Signal(object)
    worker_finished = Signal(object)
    finished = Signal(int, int)

    def __init__(self, project: BatchProject, video_ids: list[str], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self.video_ids = list(dict.fromkeys(video_ids))
        self.position = 0
        self.success_count = 0
        self.failure_count = 0
        self.details: list[str] = []
        self.worker: _TextImportWorker | None = None
        self.summary = ""
        self.error = ""

    def start(self) -> None:
        QTimer.singleShot(0, self._next_video)

    def _next_video(self) -> None:
        if self.position >= len(self.video_ids):
            self.finished.emit(self.success_count, self.failure_count)
            return
        video_id = self.video_ids[self.position]
        self.position += 1
        self.summary = ""
        self.error = ""
        record = self.project.video(video_id)
        name = record.name if record else video_id
        self._progress(f"{name}: đang chuẩn bị import…", completed=False)
        try:
            BatchProjectService.update_video_status(
                self.project, video_id, "processing", step=StepId.STT.value,
            )
            worker = _TextImportWorker(self.project, video_id, self)
            self.worker = worker
            worker.succeeded.connect(self._succeeded)
            worker.failed.connect(self._failed)
            worker.finished.connect(self._worker_done)
            self.worker_started.emit(worker)
            worker.start()
        except Exception as exc:
            self.error = str(exc)
            self._complete_video()

    def _progress(self, message: str, *, completed: bool = True) -> None:
        percent = round((self.position if completed else self.position - 1) * 100 / len(self.video_ids))
        self.progress_changed.emit(
            percent, f"Step 1.5 · {self.position}/{len(self.video_ids)} · {message}",
        )

    @Slot(str)
    def _succeeded(self, summary: str) -> None:
        self.summary = summary

    @Slot(str)
    def _failed(self, error: str) -> None:
        self.error = error

    @Slot()
    def _worker_done(self) -> None:
        worker = self.worker
        self.worker = None
        if worker is not None:
            self.worker_finished.emit(worker)
            worker.deleteLater()
        if not self.error and not self.summary:
            self.error = "Import kết thúc nhưng chưa nhận được kết quả."
        self._complete_video()

    def _complete_video(self) -> None:
        video_id = self.video_ids[self.position - 1]
        record = self.project.video(video_id)
        name = record.name if record else video_id
        if record is not None:
            record.restart_step = StepId.STT.value if self.error else StepId.TRANSLATE.value
            try:
                BatchProjectService.update_video_status(
                    self.project, video_id, "failed" if self.error else "pending",
                    error=self.error, step=StepId.STT.value if self.error else "",
                )
            except Exception as exc:
                self.error = f"{self.error}\nKhông thể lưu trạng thái video: {exc}".strip()
        if self.error:
            self.failure_count += 1
            self.details.append(f"{name}: {self.error}")
            self._progress(f"{name}: lỗi — chuyển sang video tiếp theo")
        else:
            self.success_count += 1
            self.details.append(f"{name}: {self.summary}")
            self._progress(f"{name}: {self.summary}")
        self.video_changed.emit(video_id)
        QTimer.singleShot(0, self._next_video)
