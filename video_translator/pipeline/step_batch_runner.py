from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ..batch_project import BatchProject, BatchProjectService
from ..common.models import STEP_ORDER, StepId, StepResult, StepStatus
from ..state import ProjectState
from ..steps.step3_translation.quality import assess_manual_segments
from .mock_pipeline import MockPipeline
from .worker import PipelineWorker
from .story_worker import StoryWorker


class StepBatchRunner(QObject):
    """Run one selected step and optionally generate stories by Group."""

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
        story_video_ids: list[str] | None = None,
        create_stories: bool = True,
        story_groups: list[int] | None = None,
        regenerate_stories: bool = False,
    ) -> None:
        super().__init__(parent)
        if step not in {StepId.EXTRACT, StepId.STT, StepId.TRANSLATE, StepId.TTS, StepId.SYNC, StepId.BUILD_AUDIO, StepId.RENDER}:
            raise ValueError("Runner không hỗ trợ step đã chọn.")
        self.step = step
        self.step_number = STEP_ORDER.index(step) + 1
        self.project = project
        self.pipeline = pipeline
        self.video_ids = list(dict.fromkeys(video_ids))
        self.settings = dict(settings)
        self.position = 0
        self.success_count = 0
        self.successful_video_ids: list[str] = []
        self.failure_count = 0
        self.failed_videos: list[str] = []
        self.failure_details: list[str] = []
        self.review_video_count = 0
        self.state: ProjectState | None = None
        self.worker: PipelineWorker | StoryWorker | None = None
        self.result: StepResult | None = None
        self.failure = ""
        self.story_settings = dict(story_settings or {})
        self.story_video_ids = list(dict.fromkeys(story_video_ids or self.video_ids))
        self.create_stories = create_stories
        self.story_groups = list(story_groups) if story_groups is not None else None
        self.regenerate_stories = regenerate_stories
        self.video_positions = (
            dict(video_positions) if video_positions is not None
            else {video.id: index + 1 for index, video in enumerate(project.ordered_videos())}
        )
        self.story_videos: dict[int, list[dict[str, Any]]] = {}
        self.transcript_video_ids: set[str] = set()
        self.timeline_warning_positions: list[int] = []
        self.story_path = ""
        self.story_error = ""
        self.story_paths: list[str] = []
        self.story_errors: list[str] = []
        self.pending_story_groups: list[int] = []
        self.current_story_group = 0
        self.temporary_story_path: Path | None = None

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
                raise ValueError(
                    f"Step {self.step_number} chưa có đủ đầu vào. "
                    "Hãy hoàn tất step trước và chọn output hợp lệ."
                )
            settings = dict(self.settings)
            if self.step == StepId.STT:
                settings["input_audio"] = state.audio_input_path()
                settings["input_candidate_id"] = state.selected_audio_candidate_id
                settings["input_stem"] = state.selected_audio_stem
            elif self.step == StepId.TRANSLATE:
                group_number = self.project.video_group(video_id)
                stories = dict(settings.pop("batch_group_stories"))
                story = stories.get(str(group_number))
                if not isinstance(story, dict):
                    raise ValueError(f"Group {group_number} chưa có story để dịch.")
                context = dict(story)
                context.update({
                    "batch_prompt_mode": "step",
                    "story_group": group_number,
                    "video_id": video_id,
                    "video_order": self.video_positions[video_id],
                    "video_name": record.name,
                    "video_filename": Path(record.source_path).name,
                })
                settings["batch_prompt_context"] = context
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
            progress,
            f"Step {self.step_number} · hoàn thành {self.success_count}/{len(self.video_ids)} video"
            f" · {name} · {message}",
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
                if self.step == StepId.TRANSLATE and assess_manual_segments(
                    self.result.segments, self.state.target_language,
                ):
                    self.review_video_count += 1
                if self.step == StepId.SYNC and self.state.statuses[self.step] != StepStatus.DONE:
                    error = (
                        f"Step 5 đã lưu output nhưng còn {int(self.result.metadata.get('error_count', 0))} "
                        "segment đồng bộ lỗi cần sửa."
                    )
                if self.step == StepId.STT:
                    video_id = self.video_ids[self.position - 1]
                    texts = [segment.source_text.strip() for segment in self.result.segments if segment.source_text.strip()]
                    if not texts:
                        raise ValueError("Step 2 không nhận được lời thoại nào.")
                    group_number = self.project.video_group(video_id)
                    self.story_videos.setdefault(group_number, []).append({
                        "video_order": self.video_positions[video_id],
                        "video_id": video_id,
                        "video_filename": Path(self.state.input_video).name,
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
            if error:
                record.restart_step = self.step.value
            elif self.step == StepId.RENDER:
                record.restart_step = ""
            else:
                next_step = STEP_ORDER[STEP_ORDER.index(self.step) + 1]
                record.restart_step = next_step.value
            try:
                status = "failed" if error else "completed" if self.step == StepId.RENDER else "pending"
                output_path = (
                    str(self.result.artifacts.get("output_video", ""))
                    if not error and self.step == StepId.RENDER and self.result is not None
                    else ""
                )
                BatchProjectService.update_video_status(
                    self.project, video_id, status,
                    error=error, output_path=output_path,
                    step=self.step.value if error else "",
                )
            except Exception as exc:
                error = f"{error}\nKhông thể lưu trạng thái video: {exc}".strip()
                record.status = "failed"
                record.failed_step = self.step.value
                record.last_error = error
        if error:
            self.failure_count += 1
            name = record.name if record is not None else video_id
            self.failed_videos.append(name)
            self.failure_details.append(f"Step {self.step_number} · {name}: {error}")
        else:
            self.success_count += 1
            self.successful_video_ids.append(video_id)
        self._progress(100, "Lỗi — chuyển sang video tiếp theo" if error else "Hoàn thành")
        self.video_changed.emit(video_id)
        self.state = None
        self.result = None
        QTimer.singleShot(0, self._next_video)

    def _finish_queue(self) -> None:
        if self.step != StepId.STT or not self.create_stories:
            self.finished.emit(self.success_count, self.failure_count)
            return
        self._collect_saved_transcripts()
        self.progress_changed.emit(
            85,
            "Đang thu gom transcript và tạo story theo Group…"
            if not self.video_ids else "Step 2 đã hết hàng đợi; đang thu gom lời thoại và tạo story…",
        )
        groups = self.story_groups if self.story_groups is not None else sorted({
            self.project.video_group(video_id) for video_id in self.story_video_ids
        })
        self.pending_story_groups = (
            list(groups) if self.regenerate_stories else [
                group for group in groups
                if not (
                    (path := self.project.story_path_for_group(group)) is not None
                    and path.is_file()
                )
            ]
        )
        if not self.pending_story_groups:
            self.finished.emit(self.success_count, self.failure_count)
            return
        self._start_next_story()

    def _collect_saved_transcripts(self) -> None:
        self.story_videos.clear()
        self.transcript_video_ids.clear()
        self.timeline_warning_positions.clear()
        for video_id in self.story_video_ids:
            try:
                project = BatchProjectService.read_video(self.project, video_id)
                if project is None:
                    continue
                state = ProjectState()
                state.bind_project(project, restore_candidates=False)
                candidate_id = (
                    state.selected_transcript_candidate_id
                    or state.default_transcript_candidate_id
                )
                result = state.candidate_result(
                    StepId.STT, candidate_id,
                )
                if result is None:
                    continue
                texts = [
                    segment.source_text.strip()
                    for segment in result.segments
                    if segment.source_text.strip()
                ]
                if not texts:
                    continue
                group_number = self.project.video_group(video_id)
                self.story_videos.setdefault(group_number, []).append({
                    "video_order": self.video_positions[video_id],
                    "video_id": video_id,
                    "video_filename": Path(project.input_video).name,
                    "source_text": texts,
                })
                self.transcript_video_ids.add(video_id)
                if int(result.metadata.get("timeline_error_count", 0)) > 0:
                    self.timeline_warning_positions.append(self.video_positions[video_id])
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                continue

    def _start_next_story(self) -> None:
        if not self.pending_story_groups:
            self.story_error = "\n".join(self.story_errors)
            self.finished.emit(self.success_count, self.failure_count)
            return
        group_number = self.pending_story_groups.pop(0)
        self.current_story_group = group_number
        videos = self.story_videos.get(group_number, [])
        selected_ids = [
            video_id for video_id in self.story_video_ids
            if self.project.video_group(video_id) == group_number
        ]
        selected_positions = [self.video_positions[video_id] for video_id in selected_ids]
        if not videos:
            self.story_errors.append(
                f"Group {group_number}: không có lời thoại thành công để tạo story."
            )
            QTimer.singleShot(0, self._start_next_story)
            return
        try:
            canonical_path = self.project.path("stories", f"story_group_{group_number}.md")
            self.temporary_story_path = canonical_path.with_name(
                f".{canonical_path.stem}-{uuid4().hex}.part"
            )
            worker = StoryWorker(
                self.project.root_path, self.project.source_language, self.story_settings,
                videos, selected_positions,
                [self.video_positions[video_id] for video_id in selected_ids if video_id not in self.transcript_video_ids],
                [position for position in self.timeline_warning_positions if position in selected_positions],
                group_number, self.temporary_story_path,
            )
            self.worker = worker
            worker.succeeded.connect(self._story_succeeded)
            worker.failed.connect(self._story_failed)
            worker.progress_changed.connect(self._story_progress)
            worker.finished.connect(self._story_done)
            self.worker_started.emit(worker)
            worker.start()
        except Exception as exc:
            self.story_errors.append(
                f"Group {group_number}: không thể bắt đầu tác vụ tạo story: {exc}"
            )
            QTimer.singleShot(0, self._start_next_story)

    @Slot(str)
    def _story_succeeded(self, path: str) -> None:
        canonical = self.project.path("stories", f"story_group_{self.current_story_group}.md")
        previous_binding = self.project.story_group_files.get(str(self.current_story_group))
        backup: Path | None = None
        installed = False
        try:
            if canonical.is_file():
                archive = canonical.parent / "archive" / f"replaced-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
                archive.mkdir(parents=True, exist_ok=False)
                backup = archive / canonical.name
                canonical.replace(backup)
            Path(path).replace(canonical)
            installed = True
            self.project.set_story_for_group(self.current_story_group, canonical)
            self.project.save()
        except Exception as exc:
            try:
                if backup is not None and backup.is_file():
                    canonical.unlink(missing_ok=True)
                    backup.replace(canonical)
                elif installed:
                    canonical.unlink(missing_ok=True)
            except OSError as restore_error:
                exc = RuntimeError(f"{exc}; không thể khôi phục story cũ: {restore_error}")
            if previous_binding is None:
                self.project.story_group_files.pop(str(self.current_story_group), None)
            else:
                self.project.story_group_files[str(self.current_story_group)] = previous_binding
            self.story_errors.append(f"Group {self.current_story_group}: không thể lưu story mới: {exc}")
            return
        self.story_path = str(canonical)
        self.story_paths.append(str(canonical))

    @Slot(object)
    def _story_failed(self, payload: object) -> None:
        if self.temporary_story_path is not None:
            try:
                self.temporary_story_path.unlink(missing_ok=True)
            except OSError:
                pass
        if isinstance(payload, dict):
            detail = "\n".join(
                str(payload[key]) for key in ("message", "suggestion", "technical_detail") if payload.get(key)
            ) or str(payload)
        else:
            detail = str(payload)
        self.story_errors.append(f"Group {self.current_story_group}: {detail}")

    @Slot(int, str)
    def _story_progress(self, value: int, message: str) -> None:
        total = len(self.pending_story_groups) + len(self.story_paths) + len(self.story_errors) + 1
        completed = len(self.story_paths) + len(self.story_errors)
        progress = 85 + round(((completed * 100 + value) / max(1, total)) * 0.15)
        self.progress_changed.emit(
            progress, f"Group {self.current_story_group} · {message}",
        )

    @Slot()
    def _story_done(self) -> None:
        worker = self.worker
        self.worker = None
        if worker is not None:
            self.worker_finished.emit(worker)
            worker.deleteLater()
        if self.story_path and self.story_path not in self.story_paths:
            self.story_paths.append(self.story_path)
        if self.story_path:
            self.story_ready.emit(self.story_path)
        elif not self.story_errors:
            self.story_errors.append(
                f"Group {self.current_story_group}: tác vụ AI kết thúc nhưng chưa tạo được file story."
            )
        self.story_path = ""
        if self.temporary_story_path is not None:
            try:
                self.temporary_story_path.unlink(missing_ok=True)
            except OSError:
                pass
        self.temporary_story_path = None
        QTimer.singleShot(0, self._start_next_story)
