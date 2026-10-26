from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ..batch_project import BatchProject, BatchProjectService
from ..common.models import StepId, StepResult, StepStatus
from ..state import ProjectState
from .mock_pipeline import MockPipeline
from .worker import PipelineWorker
from .story_worker import StoryWorker


class StepBatchRunner(QObject):
    """Run Step 1 or 2 sequentially; Step 2 ends with a story-writing task."""

    video_changed = Signal(str)
    progress_changed = Signal(int, str)
    worker_started = Signal(object)
    worker_finished = Signal(object)
    finished = Signal(int, int)
    story_ready = Signal(str)

    def __init__(
        self, project: BatchProject, pipeline: MockPipeline,
        video_ids: list[str], settings: dict[str, Any], parent: QObject | None = None,
        *, step: StepId = StepId.EXTRACT,
        story_settings: dict[str, Any] | None = None,
        video_positions: dict[str, int] | None = None,
    ) -> None:
        super().__init__(parent)
        if step not in {StepId.EXTRACT, StepId.STT}:
            raise ValueError("Runner hiện chỉ hỗ trợ Step 1 và Step 2.")
        self.step = step
        self.step_number = 1 if step == StepId.EXTRACT else 2
        self.project = project
        self.pipeline = pipeline
        self.video_ids = list(dict.fromkeys(video_ids))
        self.settings = dict(settings)
        self.position = 0
        self.success_count = 0
        self.failure_count = 0
        self.state: ProjectState | None = None
        self.worker: PipelineWorker | StoryWorker | None = None
        self.result: StepResult | None = None
        self.failure = ""
        self.story_settings = dict(story_settings or {})
        self.video_positions = (
            dict(video_positions) if video_positions is not None
            else {video.id: index + 1 for index, video in enumerate(project.videos)}
        )
        self.story_videos: list[dict[str, Any]] = []
        self.transcript_video_ids: set[str] = set()
        self.timeline_warning_positions: list[int] = []
        self.story_path = ""
        self.story_error = ""

    def start(self) -> None:
        QTimer.singleShot(0, self._next_video)

    def _next_video(self) -> None:
        if self.position >= len(self.video_ids):
            self._finish_queue()
            return
        video_id = self.video_ids[self.position]
        self.position += 1
        self.state = None
        self.result = None
        self.failure = ""
        record = self.project.video(video_id)
        try:
            if record is None:
                raise ValueError("Video không còn thuộc project.")
            if not Path(record.source_path).is_file():
                raise FileNotFoundError(f"Không tìm thấy video nguồn: {record.source_path}")
            state = ProjectState()
            state.bind_project(BatchProjectService.open_video(self.project, video_id))
            self.state = state
            if not state.can_run(self.step):
                raise ValueError(f"Step {self.step_number} chưa có đủ đầu vào. Hãy hoàn tất Step 1 và chọn audio hợp lệ.")
            settings = dict(self.settings)
            if self.step == StepId.STT:
                settings["input_audio"] = state.audio_input_path()
                settings["input_candidate_id"] = state.selected_audio_candidate_id
                settings["input_stem"] = state.selected_audio_stem
            record.restart_step = self.step.value
            state.mark_running(self.step)
            BatchProjectService.update_video_status(
                self.project, video_id, "processing", step=self.step.value,
            )
            self.video_changed.emit(video_id)
            self._progress(0, "Đang chuẩn bị…")
            worker = PipelineWorker(self.pipeline, self.step, state, settings)
            self.worker = worker
            worker.succeeded.connect(self._succeeded)
            worker.failed.connect(self._failed)
            worker.progress_changed.connect(self._progress)
            worker.finished.connect(self._worker_done)
            self.worker_started.emit(worker)
            worker.start()
        except Exception as exc:
            if self.worker is not None and not self.worker.isRunning():
                self.worker_finished.emit(self.worker)
                self.worker.deleteLater()
                self.worker = None
            self._complete_video(str(exc))

    @Slot(int, str)
    def _progress(self, value: int, message: str) -> None:
        record = self.project.video(self.video_ids[self.position - 1])
        name = record.name if record else self.video_ids[self.position - 1]
        progress = round(((self.position - 1) * 100 + max(0, min(100, value))) / len(self.video_ids))
        if self.step == StepId.STT:
            progress = round(progress * 0.85)
        self.progress_changed.emit(
            progress, f"Step {self.step_number} · {self.position}/{len(self.video_ids)} · {name} · {message}",
        )

    @Slot(object)
    def _succeeded(self, result: StepResult) -> None:
        self.result = result

    @Slot(object)
    def _failed(self, payload: object) -> None:
        if isinstance(payload, dict):
            self.failure = "\n".join(
                str(payload[key]) for key in ("message", "suggestion", "technical_detail")
                if payload.get(key)
            ) or str(payload)
        else:
            self.failure = str(payload)

    @Slot()
    def _worker_done(self) -> None:
        worker = self.worker
        self.worker = None
        if worker is not None:
            self.worker_finished.emit(worker)
            worker.deleteLater()
        error = self.failure
        if not error:
            try:
                if self.state is None or self.result is None:
                    raise RuntimeError(f"Step {self.step_number} kết thúc nhưng chưa có kết quả.")
                self.state.set_result(self.result)
                if self.step == StepId.STT:
                    video_id = self.video_ids[self.position - 1]
                    texts = [segment.source_text.strip() for segment in self.result.segments if segment.source_text.strip()]
                    if not texts:
                        raise ValueError("Step 2 không nhận được lời thoại nào.")
                    self.story_videos.append({
                        "video_order": self.video_positions[video_id],
                        "source_text": texts,
                    })
                    self.transcript_video_ids.add(video_id)
                    if self.state.statuses[self.step] != StepStatus.DONE:
                        self.timeline_warning_positions.append(self.video_positions[video_id])
                        error = "Transcript đã lưu nhưng còn lỗi timeline cần kiểm tra trước khi dịch."
            except Exception as exc:
                error = str(exc)
        self._complete_video(error)

    def _complete_video(self, error: str) -> None:
        video_id = self.video_ids[self.position - 1]
        record = self.project.video(video_id)
        if error and self.state is not None:
            try:
                self.state.mark_error(self.step, restore_previous=False)
                self.state.save_project()
            except Exception as exc:
                error += f"\nKhông thể lưu trạng thái step: {exc}"
        if record is not None:
            next_step = StepId.STT if self.step == StepId.EXTRACT else StepId.TRANSLATE
            record.restart_step = self.step.value if error else next_step.value
            try:
                BatchProjectService.update_video_status(
                    self.project, video_id, "failed" if error else "pending",
                    error=error, step=self.step.value if error else "",
                )
            except Exception as exc:
                error = f"{error}\nKhông thể lưu trạng thái video: {exc}".strip()
                record.status = "failed"
                record.failed_step = self.step.value
                record.last_error = error
        if error:
            self.failure_count += 1
        else:
            self.success_count += 1
        self._progress(100, "Lỗi — chuyển sang video tiếp theo" if error else "Hoàn thành")
        self.video_changed.emit(video_id)
        self.state = None
        self.result = None
        QTimer.singleShot(0, self._next_video)

    def _finish_queue(self) -> None:
        if self.step == StepId.EXTRACT:
            self.finished.emit(self.success_count, self.failure_count)
            return
        if not self.story_videos:
            self.story_error = "Không có lời thoại từ lần chạy Step 2 này để tạo story."
            self.finished.emit(self.success_count, self.failure_count)
            return
        self.progress_changed.emit(85, "Step 2 đã hết hàng đợi; đang thu gom lời thoại và tạo story…")
        try:
            worker = StoryWorker(
                self.project.root_path, self.project.source_language, self.story_settings,
                self.story_videos, [self.video_positions[video_id] for video_id in self.video_ids],
                [self.video_positions[video_id] for video_id in self.video_ids if video_id not in self.transcript_video_ids],
                self.timeline_warning_positions,
            )
            self.worker = worker
            worker.succeeded.connect(self._story_succeeded)
            worker.failed.connect(self._story_failed)
            worker.progress_changed.connect(self._story_progress)
            worker.finished.connect(self._story_done)
            self.worker_started.emit(worker)
            worker.start()
        except Exception as exc:
            self.story_error = f"Không thể bắt đầu tác vụ tạo story: {exc}"
            self._story_done()

    @Slot(str)
    def _story_succeeded(self, path: str) -> None:
        self.story_path = path

    @Slot(object)
    def _story_failed(self, payload: object) -> None:
        if isinstance(payload, dict):
            self.story_error = "\n".join(
                str(payload[key]) for key in ("message", "suggestion", "technical_detail") if payload.get(key)
            ) or str(payload)
        else:
            self.story_error = str(payload)

    @Slot(int, str)
    def _story_progress(self, value: int, message: str) -> None:
        self.progress_changed.emit(85 + round(value * 0.15), message)

    @Slot()
    def _story_done(self) -> None:
        worker = self.worker
        self.worker = None
        if worker is not None:
            self.worker_finished.emit(worker)
            worker.deleteLater()
        if self.story_path:
            self.story_ready.emit(self.story_path)
        elif not self.story_error:
            self.story_error = "Tác vụ AI kết thúc nhưng chưa tạo được file story."
        self.finished.emit(self.success_count, self.failure_count)
