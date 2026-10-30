from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from ..batch_project import BatchProject, BatchProjectService
from ..common.models import StepId, StepResult
from ..config.web_translation import CHATGPT_WEB_PROVIDER_NAME
from ..state import ProjectState
from ..steps.step3_translation.providers import create_translation_provider
from ..steps.step2_transcription.ai_asr_repair import correct_asr_with_story
from ..steps.step2_transcription.txt_repair import reference_path, repair_transcript


class _TxtRepairWorker(QThread):
    succeeded = Signal(object, str)
    failed = Signal(str)
    progress_changed = Signal(int, str)

    def __init__(self, project: BatchProject, video_id: str, state: ProjectState,
                 parent: QObject, asr_provider: object | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self.video_id = video_id
        self.state = state
        self.asr_provider = asr_provider

    def run(self) -> None:
        try:
            record = self.project.video(self.video_id)
            if record is None:
                raise ValueError("Video không còn trong project.")
            txt_path = reference_path(Path(self.project.input_folder), record.name)
            if txt_path is not None:
                result, summary = repair_transcript(
                    self.state, txt_path, self.progress_changed.emit,
                )
            else:
                group = self.project.video_group(self.video_id)
                story_path = self.project.story_path_for_group(group)
                if story_path is None:
                    raise FileNotFoundError(f"Group {group} chưa có story để sửa ASR.")
                self.progress_changed.emit(10, "Không có TXT trong input; đang sửa ASR bằng AI…")
                settings = dict(self.project.batch_settings.get(StepId.TRANSLATE.value, {}))
                ai_content, duplicate_pairs = correct_asr_with_story(
                    self.state, story_path, settings, record.name, self.asr_provider,
                )
                result, summary = repair_transcript(
                    self.state, None, self.progress_changed.emit,
                    ai_content=ai_content, story_path=story_path,
                    duplicate_pairs=duplicate_pairs,
                )
            self.succeeded.emit(result, summary)
        except Exception as exc:
            self.failed.emit(str(exc))


class TxtRepairBatchRunner(QObject):
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
        self.errors: list[str] = []
        self.summaries: list[str] = []
        self.worker: _TxtRepairWorker | None = None
        self.state: ProjectState | None = None
        self.result: StepResult | None = None
        self.summary = ""
        self.error = ""
        self.asr_provider: object | None = None
        settings = dict(project.batch_settings.get(StepId.TRANSLATE.value, {}))
        if settings.get("provider") == CHATGPT_WEB_PROVIDER_NAME:
            self.asr_provider = create_translation_provider(
                CHATGPT_WEB_PROVIDER_NAME, str(settings.get("model", "")),
                "Chinese", "Chinese", settings,
            )

    def start(self) -> None:
        QTimer.singleShot(0, self._next_video)

    def _next_video(self) -> None:
        if self.position >= len(self.video_ids):
            self.finished.emit(self.success_count, self.failure_count)
            return
        video_id = self.video_ids[self.position]
        self.position += 1
        self.result = None
        self.summary = ""
        self.error = ""
        record = self.project.video(video_id)
        name = record.name if record else video_id
        self.progress_changed.emit(
            round((self.position - 1) * 100 / len(self.video_ids)),
            f"Step 2.5 · {self.position}/{len(self.video_ids)} · {name} · Đang chuẩn bị…",
        )
        try:
            state = ProjectState()
            state.bind_project(BatchProjectService.open_video(self.project, video_id))
            self.state = state
            worker = _TxtRepairWorker(self.project, video_id, state, self, self.asr_provider)
            self.worker = worker
            worker.succeeded.connect(self._succeeded)
            worker.failed.connect(self._failed)
            worker.progress_changed.connect(self._progress)
            worker.finished.connect(self._worker_done)
            self.worker_started.emit(worker)
            worker.start()
        except Exception as exc:
            self.error = str(exc)
            self._complete_video()

    @Slot(int, str)
    def _progress(self, value: int, message: str) -> None:
        video_id = self.video_ids[self.position - 1]
        record = self.project.video(video_id)
        name = record.name if record else video_id
        percent = round(((self.position - 1) * 100 + max(0, min(100, value))) / len(self.video_ids))
        self.progress_changed.emit(
            percent, f"Step 2.5 · {self.position}/{len(self.video_ids)} · {name} · {message}",
        )

    @Slot(object, str)
    def _succeeded(self, result: StepResult | None, summary: str) -> None:
        self.result = result
        self.summary = summary

    @Slot(str)
    def _failed(self, message: str) -> None:
        self.error = message

    @Slot()
    def _worker_done(self) -> None:
        worker = self.worker
        self.worker = None
        if worker is not None:
            self.worker_finished.emit(worker)
            worker.deleteLater()
        if not self.error and self.result is not None:
            try:
                assert self.state is not None
                self.state.set_result(self.result)
                video_id = self.video_ids[self.position - 1]
                record = self.project.video(video_id)
                if record is not None:
                    record.restart_step = StepId.TRANSLATE.value
                    BatchProjectService.update_video_status(self.project, video_id, "pending")
            except Exception as exc:
                self.error = f"Không thể lưu output Step 2.5: {exc}"
        self._complete_video()

    def _complete_video(self) -> None:
        video_id = self.video_ids[self.position - 1]
        record = self.project.video(video_id)
        name = record.name if record else video_id
        if self.error:
            self.failure_count += 1
            self.errors.append(f"{name}: {self.error}")
            message = "Lỗi — chuyển sang video tiếp theo"
        else:
            self.success_count += 1
            self.summaries.append(f"{name}: {self.summary}")
            message = self.summary
        self._progress(100, message)
        self.video_changed.emit(video_id)
        self.state = None
        self.result = None
        QTimer.singleShot(0, self._next_video)
