from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QThread, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config.gemini import GEMINI_DEFAULT_MODEL, GEMINI_PROVIDER_NAME
from ..config.batch_prompts import load_batch_prompt
from ..batch_project import BatchProject, BatchProjectService
from ..common.models import STEP_ORDER, StepId, StepStatus
from ..pipeline import MockPipeline
from ..pipeline.step_batch_runner import StepBatchRunner
from ..pipeline.story_worker import VideoAnalysisWorker, validate_story_settings
from ..steps.step3_translation.quality import assess_manual_segments
from ..pipeline.worker import (
    AudioSyncAiRewriteWorker,
    AudioSyncBatchRepairWorker,
    AudioSyncNeighborBorrowWorker,
    PipelineWorker,
)
from ..state import ProjectState
from .batch_dialogs import BatchVideosDialog, OutputManagerDialog
from .step_batch_dialog import StepBatchDialog
from .project_manager import ProjectManagerPage
from .prompt_manager_dialog import PromptManagerDialog
from ..app.project_settings import ProjectSettingsDialog
from .settings_dialog import ProviderSettingsDialog
from .steps import STEP_PAGE_TYPES
from .steps.base import StepPage
from .voice_library_dialog import VoiceLibraryDialog


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("TransLanguage — Video Translation & Dubbing")
        self.resize(1440, 900)
        self.setMinimumSize(1120, 720)
        self.state = ProjectState()
        self.batch_state = ProjectState()
        self.pipeline = MockPipeline()
        self.running_all = False
        self.batch_project: BatchProject | None = None
        self.batch_running = False
        self.batch_status_message = "Sẵn sàng chạy batch"
        self.batch_queue: list[str] = []
        self.batch_start_steps: dict[str, StepId] = {}
        self.batch_run_video_ids: list[str] = []
        self.batch_run_results: dict[str, dict[str, str]] = {}
        self.batch_current_video_id = ""
        self.batch_context_video_id = ""
        self.batch_context_path = ""
        self.batch_tracks_selected_results = False
        self.batch_review_video_ids: set[str] = set()
        self.batch_settings: dict[str, dict[str, object]] = {}
        self.active_workers: set[QThread] = set()
        self.pages: dict[StepId, StepPage] = {}
        self._loading_project = False
        self._dirty_steps: set[StepId] = set()
        self.stack = QStackedWidget()
        self.project_manager = ProjectManagerPage()
        self.project_manager.project_opened.connect(self._open_project)
        self.project_manager.settings_requested.connect(self._open_provider_settings)
        self.workspace = self._build_workspace()
        self.stack.addWidget(self.project_manager)
        self.stack.addWidget(self.workspace)
        self.setCentralWidget(self.stack)
        self.state.step_changed.connect(self._refresh_step)
        self.state.project_changed.connect(self._refresh_header)

    def _build_workspace(self) -> QWidget:
        workspace = QWidget()
        root = QVBoxLayout(workspace)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top = QFrame()
        top.setObjectName("topBar")
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(24, 14, 24, 14)
        brand = QVBoxLayout()
        product = QLabel("TRANSLANGUAGE")
        product.setObjectName("brand")
        self.project_label = QLabel("Chưa mở project")
        self.project_label.setObjectName("muted")
        brand.addWidget(product)
        brand.addWidget(self.project_label)
        top_layout.addLayout(brand)
        top_layout.addStretch()
        video_box = QVBoxLayout()
        video_label = QLabel("Video đang xem")
        video_label.setObjectName("muted")
        self.video_selector = QComboBox()
        self.video_selector.setMinimumWidth(220)
        self.video_selector.currentIndexChanged.connect(self._view_video_selected)
        video_box.addWidget(video_label)
        video_box.addWidget(self.video_selector)
        project_button = QPushButton("Projects")
        project_button.clicked.connect(lambda: self.stack.setCurrentWidget(self.project_manager))
        new_button = QPushButton("Project mới")
        new_button.clicked.connect(self.project_manager.create_new)
        settings_button = QPushButton("Cài đặt")
        settings_button.clicked.connect(self._open_provider_settings)
        voice_library_button = QPushButton("Giọng tham chiếu")
        voice_library_button.clicked.connect(self._open_voice_library)
        prompt_manager_button = QPushButton("Quản lý Prompt")
        prompt_manager_button.clicked.connect(self._open_prompt_manager)
        videos_button = QPushButton("Videos")
        videos_button.clicked.connect(self._show_batch_videos)
        outputs_button = QPushButton("Outputs")
        outputs_button.clicked.connect(self._show_outputs)
        self.batch_button = QPushButton("Chạy batch")
        self.batch_button.setObjectName("primaryButton")
        self.batch_button.setEnabled(False)
        self.batch_button.clicked.connect(self._start_batch)
        self.step_batch_button = QPushButton("Chạy batch theo step")
        self.step_batch_button.setEnabled(False)
        self.step_batch_button.clicked.connect(self._show_step_batch)
        project_settings_button = QPushButton("Thiết lập project")
        project_settings_button.clicked.connect(self._open_project_settings)
        top_layout.addWidget(project_button)
        top_layout.addWidget(new_button)
        top_layout.addWidget(project_settings_button)
        top_layout.addWidget(settings_button)
        top_layout.addWidget(self.batch_button)
        top_layout.addWidget(self.step_batch_button)
        self.continue_button = QPushButton("Continue")
        self.continue_button.setObjectName("primaryButton")
        self.continue_button.clicked.connect(self._continue_pipeline)
        self.continue_button.setVisible(False)
        root.addWidget(top)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        for page_type in STEP_PAGE_TYPES:
            page = page_type(self.state)
            if page.spec.step in {StepId.STT, StepId.TRANSLATE, StepId.TTS, StepId.BUILD_AUDIO}:
                page.run_from_here_requested.connect(self._run_from_here_requested)
            elif page.spec.step == StepId.RENDER:
                page.run_requested.connect(self._run_requested)
            if hasattr(page, "ai_rewrite_requested"):
                page.ai_rewrite_requested.connect(self._rewrite_sync_segments)
            if hasattr(page, "batch_repair_requested"):
                page.batch_repair_requested.connect(self._repair_sync_segments)
            if hasattr(page, "neighbor_borrow_requested"):
                page.neighbor_borrow_requested.connect(self._borrow_sync_neighbor_time)
            self.pages[page.spec.step] = page
            self.tabs.addTab(page, f"{page.spec.number}  {page.spec.title}")
        self.tabs.currentChanged.connect(self._tab_changed)
        root.addWidget(self.tabs, 1)

        bottom = QFrame()
        bottom.setObjectName("bottomBar")
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(24, 10, 24, 10)
        bottom_layout.addWidget(voice_library_button)
        bottom_layout.addWidget(prompt_manager_button)
        bottom_layout.addWidget(videos_button)
        bottom_layout.addWidget(outputs_button)
        bottom_layout.addStretch()
        bottom_layout.addLayout(video_box)
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.continue_button)
        self.progress_label = QLabel(self.batch_status_message)
        self.progress_label.setObjectName("progressLabel")
        self.progress_label.setMinimumWidth(390)
        bottom_layout.addWidget(self.progress_label)
        root.addWidget(bottom)
        return workspace

    def _open_project(self, project: BatchProject) -> None:
        if not project.videos:
            QMessageBox.warning(self, "Project trống", "Project không có video nào được chọn.")
            return
        self.batch_project = project
        self.batch_status_message = f"Sẵn sàng chạy batch · {len(project.videos)} video"
        self.batch_settings = dict(project.batch_settings)
        self.project_manager.add_recent(project.manifest_path)
        self._refresh_video_selector(project.ordered_videos()[0].id)

    def _refresh_video_selector(self, selected_id: str = "") -> None:
        self.video_selector.blockSignals(True)
        self.video_selector.clear()
        if self.batch_project:
            for video in self.batch_project.ordered_videos():
                self.video_selector.addItem(video.name, video.id)
        index = self.video_selector.findData(selected_id)
        self.video_selector.setCurrentIndex(index if index >= 0 else 0)
        video_id = str(self.video_selector.currentData() or "")
        self.video_selector.blockSignals(False)
        if video_id:
            self._open_batch_video(video_id)

    def _view_video_selected(self, _index: int) -> None:
        video_id = str(self.video_selector.currentData() or "")
        if video_id:
            self._open_batch_video(video_id)

    def _select_view_video(self, video_id: str) -> None:
        index = self.video_selector.findData(video_id)
        if index >= 0:
            self.video_selector.setCurrentIndex(index)

    def _open_batch_video(self, video_id: str) -> None:
        if not self.batch_project:
            return
        project = BatchProjectService.open_video(self.batch_project, video_id)
        for page in self.pages.values():
            page.prepare_run()
        self._finish_pipeline_sequence()
        self._loading_project = True
        try:
            self.state.bind_project(project, restore_candidates=False)
            self.state.batch_resume_step()
        finally:
            self._loading_project = False
        self.stack.setCurrentWidget(self.workspace)
        current = self.tabs.currentWidget()
        if isinstance(current, StepPage):
            step = current.spec.step
            project_id = project.id

            def refresh_current_page() -> None:
                if not self.state.project or self.state.project.id != project_id:
                    return
                page = self.pages[step]
                if self.tabs.currentWidget() is page:
                    page.refresh()
                    self._dirty_steps.discard(step)

            QTimer.singleShot(25, refresh_current_page)
        self._refresh_header()

    def _tab_changed(self, index: int) -> None:
        page = self.tabs.widget(index)
        if not isinstance(page, StepPage):
            return
        if page.spec.step in self._dirty_steps:
            page.refresh()
            self._dirty_steps.discard(page.spec.step)
        self._refresh_header()

    def _open_provider_settings(self) -> None:
        dialog = ProviderSettingsDialog(self)
        dialog.credentials_changed.connect(self._refresh_provider_pages)
        dialog.exec()
        self._refresh_provider_pages()

    def _open_voice_library(self) -> None:
        VoiceLibraryDialog(self).exec()

    def _open_prompt_manager(self) -> None:
        PromptManagerDialog(self).exec()

    def _show_batch_videos(self) -> None:
        if not self.batch_project:
            QMessageBox.information(self, "Chưa có project", "Vui lòng tạo hoặc mở project trước.")
            return
        dialog = BatchVideosDialog(self.batch_project, self)
        dialog.video_requested.connect(self._select_view_video)

        def run_selected(start_steps: object) -> None:
            selected = {str(video_id): StepId(step) for video_id, step in dict(start_steps).items()}
            dialog.accept()
            QTimer.singleShot(
                0,
                lambda target=selected: self._start_selected_batch(target),
            )

        dialog.run_requested.connect(run_selected)
        dialog.exec()

    def _show_step_batch(self) -> None:
        if self.batch_project:
            dialog = StepBatchDialog(self.batch_project, self)
            dialog.step1_to_3_requested.connect(
                lambda video_ids: self._start_step_1_to_3(dialog, video_ids)
            )
            dialog.rebuild_1_2_requested.connect(
                lambda: self._rebuild_step_1_2_stories(dialog)
            )
            dialog.step3_requested.connect(lambda video_ids: self._start_step_batch(dialog, video_ids, StepId.TRANSLATE))
            dialog.step7_requested.connect(lambda video_ids: self._start_step_batch(dialog, video_ids, StepId.RENDER))
            dialog.exec()

    def _start_step_batch(
        self, dialog: StepBatchDialog, video_ids: list[str], step: StepId, *,
        story_video_ids: list[str] | None = None,
        force_rebuild_stories: bool = False,
        completion: object | None = None,
    ) -> bool:
        if not self.batch_project or (not video_ids and not story_video_ids):
            return False
        if self.batch_running or self.active_workers:
            QMessageBox.information(dialog, "Pipeline đang chạy", "Hãy chờ tác vụ hiện tại hoàn tất.")
            return False
        number = STEP_ORDER.index(step) + 1
        settings = dict(self.batch_project.batch_settings.get(step.value, {}))
        if not settings:
            QMessageBox.information(dialog, f"Thiếu thiết lập Step {number}", f"Hãy hoàn tất Step {number} trong Thiết lập project trước khi chạy.")
            return False
        story_settings = dict(self.batch_project.batch_settings.get(StepId.TRANSLATE.value, {}))
        try:
            if step == StepId.STT:
                if not story_settings:
                    raise ValueError("Hãy chọn Gemini hoặc ChatGPT và cấu hình AI tại Thiết lập project → Step 3 để tạo story.")
                validate_story_settings(story_settings)
            elif step == StepId.TRANSLATE:
                load_batch_prompt(str(settings.get("provider", GEMINI_PROVIDER_NAME)))
                group_stories: dict[str, dict[str, object]] = {}
                for video_id in video_ids:
                    group_number = self.batch_project.video_group(video_id)
                    if str(group_number) in group_stories:
                        continue
                    path = self.batch_project.story_path_for_group(group_number)
                    if path is None or not path.is_file():
                        continue
                    content = path.read_text(encoding="utf-8-sig")
                    if not content.strip():
                        continue
                    group_stories[str(group_number)] = {
                        "story_group": group_number,
                        "story_name": path.name,
                        "story_path": str(path),
                        "story_content": content,
                    }
                settings["batch_group_stories"] = group_stories
            for page in self.pages.values():
                page.prepare_run()
        except Exception as exc:
            QMessageBox.warning(dialog, "Không thể chuẩn bị batch", str(exc))
            return False
        runner = StepBatchRunner(
            self.batch_project, self.pipeline, video_ids, settings, self,
            step=step, story_settings=story_settings,
            video_positions={video_id: index + 1 for index, video_id in enumerate(dialog.video_ids)},
            story_video_ids=story_video_ids,
            force_rebuild_stories=force_rebuild_stories,
        )
        self.batch_running = True
        self.batch_status_message = f"Đang chuẩn bị batch Step {number}"
        dialog.set_batch_busy(True, number)
        self._refresh_header()
        runner.worker_started.connect(self.active_workers.add)
        runner.worker_finished.connect(self.active_workers.discard)
        runner.video_changed.connect(dialog.refresh_video_status)
        runner.video_changed.connect(lambda _video_id: self._refresh_header())
        runner.progress_changed.connect(dialog.update_batch_progress)
        runner.story_ready.connect(dialog.load_story)

        def finished(succeeded: int, failed: int) -> None:
            self.batch_running = False
            self.batch_status_message = f"Batch Step {number} hoàn tất · Thành công: {succeeded} · Lỗi: {failed}"
            if runner.story_error:
                self.batch_status_message += " · Chưa tạo được story"
            if runner.review_video_count:
                self.batch_status_message += f" · Có cảnh báo bản dịch: {runner.review_video_count} video"
            try:
                if self.state.project and self.state.project.video_id in video_ids:
                    self._reload_viewed_video(self.state.project.video_id)
            except Exception as exc:
                QMessageBox.warning(dialog, "Không thể nạp lại video đang xem", str(exc))
            self._refresh_header()
            story_error = runner.story_error
            review_video_count = runner.review_video_count
            story_count = len(runner.story_paths)
            runner.deleteLater()
            if callable(completion):
                completion(succeeded, failed, story_error, review_video_count, story_count)
                return
            dialog.batch_finished(
                succeeded, failed, story_error, review_video_count, story_count,
            )

        runner.finished.connect(finished)
        runner.start()
        return True

    def _start_step_1_to_3(self, dialog: StepBatchDialog, video_ids: list[str]) -> None:
        if not self.batch_project or not video_ids:
            return
        selected_groups = {self.batch_project.video_group(video_id) for video_id in video_ids}
        story_video_ids = [
            video.id for video in self.batch_project.ordered_videos()
            if self.batch_project.video_group(video.id) in selected_groups
        ]
        sequence_story = {"error": "", "count": 0}

        def finish_step3(
            succeeded: int, failed: int, story_error: str,
            review_count: int, story_count: int,
        ) -> None:
            combined_story_error = "\n".join(
                value for value in (sequence_story["error"], story_error) if value
            )
            dialog.batch_finished(
                succeeded, failed, combined_story_error, review_count,
                int(sequence_story["count"]) + story_count,
            )

        def start_step3() -> None:
            self.batch_running = False
            started = self._start_step_batch(
                dialog, video_ids, StepId.TRANSLATE, completion=finish_step3,
            )
            if not started:
                dialog.set_batch_busy(False)

        def finish_step2(
            _succeeded: int, _failed: int, story_error: str,
            _review_count: int, _story_count: int,
        ) -> None:
            sequence_story["error"] = story_error
            sequence_story["count"] = _story_count
            self.batch_running = True
            self.batch_status_message = "Đang chờ 10 giây để ổn định file story trước Step 3"
            if story_error:
                dialog.update_batch_progress(
                    90, "Đã xong Step 2 nhưng có Group chưa tạo được story; chờ 10 giây trước Step 3…",
                )
            else:
                dialog.update_batch_progress(
                    90, "Đã lưu story; chờ 10 giây trước khi bắt đầu Step 3…",
                )
            self._refresh_header()
            QTimer.singleShot(10_000, start_step3)

        def finish_step1(
            _succeeded: int, _failed: int, _story_error: str,
            _review_count: int, _story_count: int,
        ) -> None:
            started = self._start_step_batch(
                dialog, video_ids, StepId.STT,
                story_video_ids=story_video_ids,
                completion=finish_step2,
            )
            if not started:
                dialog.set_batch_busy(False)

        self._start_step_batch(
            dialog, video_ids, StepId.EXTRACT, completion=finish_step1,
        )

    def _rebuild_step_1_2_stories(self, dialog: StepBatchDialog) -> None:
        if not self.batch_project:
            return
        all_video_ids = [video.id for video in self.batch_project.ordered_videos()]

        def missing_outputs(step: StepId) -> list[str]:
            missing: list[str] = []
            for video_id in all_video_ids:
                try:
                    project = BatchProjectService.read_video(self.batch_project, video_id)
                    if project is None:
                        missing.append(video_id)
                        continue
                    state = ProjectState()
                    state.bind_project(project, restore_candidates=False)
                    if step == StepId.EXTRACT:
                        valid = state.valid_default_audio_input() is not None
                    else:
                        valid = state.candidate_result(
                            StepId.STT, state.selected_transcript_candidate_id,
                        ) is not None
                    if not valid:
                        missing.append(video_id)
                except (OSError, ValueError, TypeError, KeyError, AttributeError):
                    missing.append(video_id)
            return missing

        def finish_rebuild(
            succeeded: int, failed: int, story_error: str,
            review_count: int, story_count: int,
        ) -> None:
            dialog.batch_finished(succeeded, failed, story_error, review_count, story_count)

        def run_missing_step2() -> None:
            missing_step2 = missing_outputs(StepId.STT)
            started = self._start_step_batch(
                dialog, missing_step2, StepId.STT,
                story_video_ids=all_video_ids,
                force_rebuild_stories=True,
                completion=finish_rebuild,
            )
            if not started:
                dialog.set_batch_busy(False)

        missing_step1 = missing_outputs(StepId.EXTRACT)
        if not missing_step1:
            run_missing_step2()
            return

        def finish_missing_step1(
            _succeeded: int, _failed: int, _story_error: str,
            _review_count: int, _story_count: int,
        ) -> None:
            run_missing_step2()

        started = self._start_step_batch(
            dialog, missing_step1, StepId.EXTRACT, completion=finish_missing_step1,
        )
        if not started:
            dialog.set_batch_busy(False)

    def _show_outputs(self) -> None:
        if not self.batch_project:
            QMessageBox.information(self, "Chưa có project", "Vui lòng tạo hoặc mở project trước.")
            return
        OutputManagerDialog(self.batch_project, self).exec()

    def _open_project_settings(self) -> None:
        if not self.batch_project:
            QMessageBox.information(self, "Chưa có project", "Vui lòng tạo hoặc mở project trước.")
            return
        if self.active_workers or self.batch_running:
            QMessageBox.information(self, "Pipeline đang chạy", "Hãy chờ step hiện tại hoàn thành.")
            return
        dialog = ProjectSettingsDialog(self.batch_project, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        previous_order = [video.id for video in self.batch_project.ordered_videos()]
        previous_group_size = self.batch_project.story_group_size
        previous_group_members = {
            str(index // previous_group_size + 1): previous_order[
                (index // previous_group_size) * previous_group_size:
                (index // previous_group_size + 1) * previous_group_size
            ]
            for index in range(0, len(previous_order), previous_group_size)
        }
        existing_paths = {
            str(Path(video.source_path).resolve())
            for video in self.batch_project.videos
        }
        selected_new_paths = [
            path for path in dialog.values()["selected_videos"]
            if str(Path(path).resolve()) not in existing_paths
        ]
        added_videos = []
        if selected_new_paths:
            try:
                added_videos = BatchProjectService.add_videos(
                    self.batch_project,
                    selected_new_paths,
                )
            except Exception as exc:
                QMessageBox.critical(self, "Không thể thêm video", str(exc))
                return
        new_settings = dialog.settings_values()
        video_by_path = {
            str(Path(video.source_path).resolve()): video.id
            for video in self.batch_project.videos
        }
        self.batch_project.set_video_order([
            video_by_path[path]
            for value in dialog.video_order_paths()
            if (path := str(Path(value).resolve())) in video_by_path
        ])
        self.batch_project.story_group_size = dialog.story_group_size.value()
        current_order = [video.id for video in self.batch_project.ordered_videos()]
        current_size = self.batch_project.story_group_size
        current_group_members = {
            str(index // current_size + 1): current_order[
                (index // current_size) * current_size:
                (index // current_size + 1) * current_size
            ]
            for index in range(0, len(current_order), current_size)
        }
        self.batch_project.story_group_files = {
            group: path
            for group, path in self.batch_project.story_group_files.items()
            if previous_group_members.get(group) == current_group_members.get(group)
        }
        self.batch_project.source_language = dialog.source_language.currentText()
        self.batch_project.target_language = dialog.target_language.currentText()
        self.batch_project.batch_settings = new_settings
        self.batch_project.save()
        self.batch_settings = dict(new_settings)
        if self.state.project:
            self.state.source_language = self.batch_project.source_language
            self.state.target_language = self.batch_project.target_language
            self.state.project_changed.emit()
            self.state.save_project()
        selected_id = added_videos[0].id if added_videos else str(self.video_selector.currentData() or "")
        self._refresh_video_selector(selected_id)
        self._refresh_header()

    def _start_batch(self) -> None:
        if not self.batch_project:
            return
        video_ids = [
            video.id
            for video in self.batch_project.ordered_videos()
            if video.selected and video.output_count < 1
        ]
        self._start_batch_queue(video_ids)

    def _start_selected_batch(self, start_steps: dict[str, StepId]) -> None:
        self._start_batch_queue(list(start_steps), start_steps=start_steps)

    def _start_batch_queue(
        self, video_ids: list[str], *, start_steps: dict[str, StepId] | None = None,
    ) -> None:
        if not self.batch_project:
            return
        if self.batch_running:
            QMessageBox.information(self, "Batch đang chạy", "Hàng đợi hiện tại đang được xử lý.")
            return
        if self.active_workers:
            QMessageBox.information(self, "Pipeline đang chạy", "Hãy chờ step hiện tại hoàn thành.")
            return
        try:
            chosen_steps = (
                {video_id: StepId(start_steps[video_id]) for video_id in video_ids}
                if start_steps is not None else {}
            )
        except (ValueError, TypeError, KeyError):
            QMessageBox.warning(self, "Step bắt đầu không hợp lệ", "Mỗi video được chọn cần có Step bắt đầu từ 1 đến 7.")
            return
        self.batch_settings = dict(self.batch_project.batch_settings)
        if any(step.value not in self.batch_settings for step in STEP_ORDER):
            QMessageBox.information(
                self,
                "Thiếu thiết lập project",
                "Hãy hoàn tất Thiết lập project trước khi chạy batch.",
            )
            self._open_project_settings()
            return
        requested = set(video_ids)
        self.batch_queue = [
            video.id
            for video in self.batch_project.ordered_videos()
            if video.id in requested
        ]
        if not self.batch_queue:
            self.batch_status_message = "Không có video cần xử lý"
            self._refresh_header()
            QMessageBox.information(
                self,
                "Không có video cần xử lý",
                "Không có video phù hợp với chế độ batch đã chọn.",
            )
            return
        self.batch_review_video_ids.clear()
        self.batch_start_steps = chosen_steps
        self.batch_tracks_selected_results = start_steps is not None
        self.batch_run_video_ids = list(self.batch_queue)
        self.batch_run_results.clear()
        self.batch_current_video_id = ""
        self.batch_running = True
        self.batch_status_message = "Đang chuẩn bị batch"
        self._refresh_header()
        self._start_next_batch_video()

    def _start_next_batch_video(self) -> None:
        if not self.batch_project:
            return
        if not self.batch_queue:
            self.batch_running = False
            self.batch_current_video_id = ""
            total = len(self.batch_run_video_ids)
            succeeded = sum(
                result.get("status") == "completed"
                for result in self.batch_run_results.values()
            )
            failed_results = [
                (video_id, self.batch_run_results.get(video_id, {}))
                for video_id in self.batch_run_video_ids
                if self.batch_run_results.get(video_id, {}).get("status") == "failed"
            ]
            review_results = [
                (video_id, self.batch_run_results.get(video_id, {}))
                for video_id in self.batch_run_video_ids
                if self.batch_run_results.get(video_id, {}).get("status") == "review"
            ]
            self.batch_start_steps.clear()
            self.batch_status_message = (
                f"Batch hoàn thành · Thành công: {succeeded}/{total} video"
                f" · Lỗi: {len(failed_results)}"
            )
            if review_results:
                self.batch_status_message += f" · Chờ kiểm tra Step 03: {len(review_results)}"
            self.batch_tracks_selected_results = False
            self._refresh_header()
            message_lines = [f"Đã chạy thành công {succeeded}/{total} video."]
            if failed_results:
                message_lines.extend(["", "Video lỗi:"])
                for video_id, result in failed_results:
                    record = self.batch_project.video(video_id)
                    name = record.name if record else video_id
                    step = result.get("step", "")
                    step_text = (
                        f"Step {STEP_ORDER.index(StepId(step)) + 1:02d}"
                        if step in {item.value for item in STEP_ORDER}
                        else "Không xác định step"
                    )
                    message_lines.append(f"- {name} — {step_text}: {result.get('error', 'Không rõ lỗi')}")
            if review_results:
                message_lines.extend(["", "Video chờ kiểm tra bản dịch:"])
                for video_id, _result in review_results:
                    record = self.batch_project.video(video_id)
                    message_lines.append(f"- {record.name if record else video_id} — Step 03")
            QMessageBox.information(
                self,
                "Batch hoàn thành",
                "\n".join(message_lines),
            )
            return
        video_id = self.batch_queue.pop(0)
        self.batch_current_video_id = video_id
        record = self.batch_project.video(video_id)
        if not record:
            self.batch_run_results[video_id] = {
                "status": "failed", "step": "", "error": "Video không còn thuộc project.",
            }
            QTimer.singleShot(0, self._start_next_batch_video)
            return
        if not Path(record.source_path).is_file():
            failure_step = self.batch_start_steps.get(video_id, StepId.EXTRACT)
            error = "Không tìm thấy video nguồn."
            BatchProjectService.update_video_status(
                self.batch_project, video_id, "failed", error, step=failure_step.value,
            )
            self._update_selected_batch_status(
                video_id, "failed", step=failure_step, error=error,
            )
            self.batch_run_results[video_id] = {
                "status": "failed", "step": failure_step.value, "error": error,
            }
            self._refresh_header()
            QTimer.singleShot(0, self._start_next_batch_video)
            return
        explicit_start = video_id in self.batch_start_steps
        next_step = self.batch_start_steps.get(video_id, StepId.EXTRACT)
        try:
            project = BatchProjectService.open_video(self.batch_project, video_id)
            self.batch_state.bind_project(project, restore_candidates=False)
            if explicit_start:
                validated_step = self.batch_state.restore_selected_inputs(next_step)
                if validated_step != next_step:
                    requested_number = STEP_ORDER.index(next_step) + 1
                    input_number = STEP_ORDER.index(validated_step) + 1 if validated_step else requested_number - 1
                    raise ValueError(
                        f"Không thể chạy Step {requested_number:02d}: output Step {input_number:02d} "
                        "đang được chọn làm đầu vào không hợp lệ hoặc còn lỗi."
                    )
            else:
                next_step = self.batch_state.batch_resume_step()
        except Exception as exc:
            error = str(exc)
            BatchProjectService.update_video_status(
                self.batch_project, video_id, "failed", error, step=next_step.value,
            )
            self._update_selected_batch_status(
                video_id, "failed", step=next_step, error=error,
            )
            self.batch_run_results[video_id] = {
                "status": "failed", "step": next_step.value, "error": error,
            }
            self._refresh_header()
            QTimer.singleShot(0, self._start_next_batch_video)
            return
        record.restart_step = ""
        if next_step is None:
            self._complete_batch_video()
            return
        BatchProjectService.update_video_status(
            self.batch_project, video_id, "processing", step=next_step.value
        )
        self._update_selected_batch_status(video_id, "processing", step=next_step)
        self._refresh_header()
        self._execute_batch_step(next_step, explicit_start=explicit_start)

    def _execute_batch_step(self, step: StepId, *, explicit_start: bool = False) -> None:
        if not self.batch_project or not self.batch_state.project:
            return
        pending_transcript = self.batch_state.results.get(StepId.STT)
        if not explicit_start and step == StepId.STT and pending_transcript and pending_transcript.metadata.get("timeline_error_count", 0):
            self._fail_batch_video(
                "Output Step 2 đã lưu. Mở Kiểm tra / sửa timeline để sửa, không cần chạy lại ASR.", step,
            )
            return
        if step.value not in self.batch_settings:
            self._fail_batch_video("Step chưa có thiết lập project.", step)
            return
        if not self.batch_state.can_run(step):
            self._fail_batch_video("Step không có đủ đầu vào.", step)
            return
        video_id = self.batch_state.project.video_id
        BatchProjectService.update_video_status(
            self.batch_project, video_id, "processing", step=step.value
        )
        self._update_selected_batch_status(video_id, "processing", step=step)
        self._refresh_header()
        settings = dict(self.batch_settings[step.value])
        if step == StepId.STT:
            settings["input_audio"] = self.batch_state.audio_input_path()
            settings["input_candidate_id"] = self.batch_state.selected_audio_candidate_id
            settings["input_stem"] = self.batch_state.selected_audio_stem
        if step == StepId.TRANSLATE and self.batch_context_video_id != self.batch_state.project.video_id:
            self._start_video_context_analysis()
            return
        if step == StepId.TRANSLATE:
            context_path = Path(self.batch_context_path)
            if not context_path.is_file():
                self._fail_batch_video("Không tìm thấy hồ sơ phân tích video.", step)
                return
            settings["batch_prompt_context"] = {
                "story_name": context_path.name,
                "story_path": str(context_path),
                "story_content": context_path.read_text(encoding="utf-8-sig"),
                "batch_prompt_mode": "legacy",
                "video_id": self.batch_state.project.video_id,
                "video_order": self.batch_run_video_ids.index(self.batch_state.project.video_id) + 1,
                "video_name": Path(self.batch_state.project.input_video).name,
                "video_filename": Path(self.batch_state.project.input_video).name,
            }
        self.batch_state.mark_running(step)
        worker = PipelineWorker(self.pipeline, step, self.batch_state, settings)
        outcome: dict[str, object] = {
            "success": False,
            "failure_payload": None,
        }
        self.active_workers.add(worker)

        def succeeded(result: object) -> None:
            self.batch_state.set_result(result)
            outcome["success"] = self.batch_state.statuses[step] == StepStatus.DONE

        def failed(payload: object) -> None:
            outcome["failure_payload"] = payload
            self.batch_state.mark_error(step, restore_previous=False)
            self.batch_state.save_project()

        def finished() -> None:
            self.active_workers.discard(worker)
            worker.deleteLater()
            if outcome["failure_payload"] is not None:
                self._fail_batch_video(outcome["failure_payload"], step)
                return
            if not outcome["success"]:
                saved = self.batch_state.results.get(step)
                message = (
                    "Output Step 2 đã lưu. Mở Kiểm tra / sửa timeline để sửa trước khi tiếp tục."
                    if step == StepId.STT and saved and saved.metadata.get("timeline_error_count", 0)
                    else "Step còn lỗi cần xử lý."
                )
                self._fail_batch_video(message, step)
                return
            next_index = STEP_ORDER.index(step) + 1
            if step == StepId.TRANSLATE:
                result = self.batch_state.results.get(step)
                issues = assess_manual_segments(result.segments, self.batch_state.target_language) if result else []
                if issues:
                    self._hold_batch_video_for_translation_review(len(issues))
                    return
            if next_index < len(STEP_ORDER):
                self._execute_batch_step(STEP_ORDER[next_index])
            else:
                self._complete_batch_video()

        worker.succeeded.connect(succeeded)
        worker.failed.connect(failed)
        worker.finished.connect(finished)
        QTimer.singleShot(150, worker.start)

    def _start_video_context_analysis(self) -> None:
        if not self.batch_project or not self.batch_state.project:
            return
        video_id = self.batch_state.project.video_id
        project = self.batch_state.project
        result = self.batch_state.results.get(StepId.STT)
        if result is None:
            self._fail_batch_video("Không có transcript Step 2 để phân tích video.", StepId.TRANSLATE)
            return
        record = self.batch_project.video(video_id)
        if record is None:
            self._fail_batch_video("Video không còn thuộc project.", StepId.TRANSLATE)
            return
        path = Path(project.output_root) / "video-analysis.md"
        self.batch_status_message = f"Đang phân tích toàn bộ nội dung video: {record.name}"
        self._refresh_header()
        worker = VideoAnalysisWorker(
            path, project.source_language,
            dict(self.batch_settings.get(StepId.TRANSLATE.value, {})),
            {
                "video_order": self.batch_run_video_ids.index(video_id) + 1,
                "video_id": video_id,
                "video_filename": Path(project.input_video).name,
                "source_text": [s.source_text.strip() for s in result.segments if s.source_text.strip()],
            },
        )
        self.active_workers.add(worker)
        outcome: dict[str, object] = {"error": None, "path": ""}
        worker.succeeded.connect(lambda value: outcome.update(path=value))
        worker.failed.connect(lambda value: outcome.update(error=value))

        def finished() -> None:
            self.active_workers.discard(worker)
            worker.deleteLater()
            if outcome["error"] is not None:
                self._fail_batch_video(outcome["error"], StepId.TRANSLATE)
                return
            self.batch_context_video_id = video_id
            self.batch_context_path = str(outcome["path"])
            self._execute_batch_step(StepId.TRANSLATE)

        worker.finished.connect(finished)
        QTimer.singleShot(150, worker.start)

    def _hold_batch_video_for_translation_review(self, issue_count: int) -> None:
        if not self.batch_project or not self.batch_state.project:
            return
        video_id = self.batch_state.project.video_id
        record = self.batch_project.video(video_id)
        if record is None:
            return
        # Step 3 remains DONE and its selected output is usable by Step 4.
        record.restart_step = StepId.TTS.value
        self.batch_review_video_ids.add(video_id)
        self.batch_run_results[video_id] = {
            "status": "review",
            "step": StepId.TRANSLATE.value,
            "error": f"{issue_count} segment có cảnh báo cần xem lại.",
        }
        BatchProjectService.update_video_status(
            self.batch_project, video_id, "pending",
            error=f"Step 3 đã hoàn thành; {issue_count} segment có cảnh báo cần xem lại. Có thể chạy Step 4 mà không sửa.",
        )
        self._update_selected_batch_status(
            video_id,
            "failed",
            step=StepId.TRANSLATE,
            error=f"{issue_count} segment có cảnh báo cần xem lại.",
        )
        self._reload_viewed_video(video_id)
        self._refresh_header()
        QTimer.singleShot(0, self._start_next_batch_video)

    def _complete_batch_video(self) -> None:
        if not self.batch_project or not self.batch_state.project:
            return
        video_id = self.batch_state.project.video_id
        result = self.batch_state.results.get(StepId.RENDER)
        output_path = result.artifacts.get("output_video", "") if result else ""
        BatchProjectService.update_video_status(
            self.batch_project,
            video_id,
            "completed",
            output_path=output_path,
        )
        self._update_selected_batch_status(
            video_id, "completed", step=StepId.RENDER,
        )
        self.batch_run_results[video_id] = {
            "status": "completed", "step": StepId.RENDER.value, "error": "",
        }
        self._refresh_header()
        self._reload_viewed_video(video_id)
        QTimer.singleShot(0, self._start_next_batch_video)

    def _fail_batch_video(self, payload: object, step: StepId | None = None) -> None:
        if not self.batch_project or not self.batch_state.project:
            return
        video_id = self.batch_state.project.video_id
        if isinstance(payload, dict):
            error = str(payload.get("message") or payload.get("detail") or payload)
            technical_detail = str(payload.get("technical_detail") or "").strip()
            if step == StepId.RENDER and technical_detail:
                error = f"{error}\n\nChi tiết FFmpeg:\n{technical_detail[-4000:]}"
        else:
            error = str(payload)
        BatchProjectService.update_video_status(
            self.batch_project,
            video_id,
            "failed",
            error=error,
            step=step.value if step else "",
        )
        self._update_selected_batch_status(
            video_id, "failed", step=step, error=error,
        )
        self.batch_run_results[video_id] = {
            "status": "failed",
            "step": step.value if step else "",
            "error": error,
        }
        self._refresh_header()
        self._reload_viewed_video(video_id)
        QTimer.singleShot(0, self._start_next_batch_video)

    def _update_selected_batch_status(
        self,
        video_id: str,
        status: str,
        *,
        step: StepId | None = None,
        error: str = "",
    ) -> None:
        if not self.batch_project or not self.batch_tracks_selected_results:
            return
        BatchProjectService.update_selected_batch_status(
            self.batch_project,
            video_id,
            status,
            step=step.value if step else "",
            error=error,
        )

    def _reload_viewed_video(self, video_id: str) -> None:
        if self.state.project and self.state.project.video_id == video_id:
            self._open_batch_video(video_id)

    def _refresh_provider_pages(self) -> None:
        page = self.pages.get(StepId.TRANSLATE)
        if page:
            page.refresh()

    def _run_requested(self, step_value: str) -> None:
        self._execute_step(StepId(step_value))

    def _run_from_here_requested(self, step_value: str) -> None:
        if not self.state.project:
            QMessageBox.information(self, "Chưa có project", "Vui lòng tạo hoặc mở project trước.")
            self.stack.setCurrentWidget(self.project_manager)
            return
        if self.active_workers:
            QMessageBox.information(self, "Pipeline đang chạy", "Vui lòng chờ step hiện tại hoàn thành.")
            return
        step = StepId(step_value)
        if not self.state.can_run(step):
            if self.batch_running:
                self._fail_batch_video(
                    f"Step {STEP_ORDER.index(step) + 1:02d} không có đủ đầu vào."
                )
                return
            QMessageBox.information(
                self,
                "Chưa đủ đầu vào",
                "Hãy hoàn thành step trước trước khi chạy pipeline từ step này.",
            )
            return
        self.running_all = True
        chain_page = self.pages.get(step)
        if hasattr(chain_page, "set_chain_running"):
            chain_page.set_chain_running(True)
        self.continue_button.setVisible(True)
        self.continue_button.setEnabled(False)
        self.continue_button.setText(f"Đang chạy từ Step {STEP_ORDER.index(step) + 1}…")
        self._execute_step(step, continue_all=True)

    def _sync_repair_chain(self, candidate_id: str) -> dict[str, str] | None:
        candidate = self.state.sync_candidate(candidate_id)
        if not candidate or not Path(candidate.path).is_file():
            QMessageBox.information(self, "Không tìm thấy output", "Manifest Step 5 không còn tồn tại.")
            return None
        try:
            sync_payload = json.loads(Path(candidate.path).read_text(encoding="utf-8"))
            tts_id = str(sync_payload.get("source_tts_candidate_id", ""))
            tts_candidate = self.state.tts_candidate(tts_id)
            if not tts_candidate or not Path(tts_candidate.path).is_file():
                raise ValueError("Không tìm thấy candidate Step 4 nguồn.")
            tts_payload = json.loads(Path(tts_candidate.path).read_text(encoding="utf-8"))
            translation_id = str(tts_payload.get("source_translation_candidate_id", ""))
            translation_candidate = self.state.translation_candidate(translation_id)
            if not translation_candidate or not Path(translation_candidate.path).is_file():
                raise ValueError("Không tìm thấy candidate Step 3 nguồn.")
            translation_payload = json.loads(Path(translation_candidate.path).read_text(encoding="utf-8"))
            transcript_id = str(translation_payload.get("source_transcript_candidate_id", ""))
            transcript_candidate = self.state.transcript_candidate(transcript_id)
            transcript_manifest = (
                transcript_candidate.path
                if transcript_candidate and Path(transcript_candidate.path).is_file()
                else ""
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            QMessageBox.warning(
                self,
                "Chuỗi candidate không hợp lệ",
                f"Không thể nối candidate Step 2 → Step 3 → Step 4 → Step 5 để cập nhật tại chỗ.\n{exc}",
            )
            return None
        return {
            "sync_id": candidate_id,
            "sync_manifest": candidate.path,
            "tts_id": tts_id,
            "tts_manifest": tts_candidate.path,
            "translation_id": translation_id,
            "translation_manifest": translation_candidate.path,
            "transcript_id": transcript_id if transcript_manifest else "",
            "transcript_manifest": transcript_manifest,
            "provider": str(
                translation_payload.get("provider")
                or translation_candidate.metadata.get("provider")
                or GEMINI_PROVIDER_NAME
            ),
            "model": str(translation_payload.get("model") or translation_candidate.metadata.get("model") or GEMINI_DEFAULT_MODEL),
        }

    @staticmethod
    def _show_worker_error(parent: QMainWindow, payload: object, fallback_title: str) -> None:
        data = payload if isinstance(payload, dict) else {
            "title": fallback_title,
            "message": str(payload),
            "suggestion": "",
            "technical_detail": "",
        }
        dialog = QMessageBox(parent)
        dialog.setIcon(QMessageBox.Icon.Critical)
        dialog.setWindowTitle(str(data.get("title", fallback_title)))
        dialog.setText(str(data.get("message", "Đã xảy ra lỗi.")))
        if data.get("suggestion"):
            dialog.setInformativeText(str(data["suggestion"]))
        if data.get("technical_detail"):
            dialog.setDetailedText(str(data["technical_detail"]))
        dialog.exec()

    def _rewrite_sync_segments(self, candidate_id: str, selected_texts: dict[int, str]) -> None:
        if self.active_workers:
            QMessageBox.information(self, "Pipeline đang chạy", "Vui lòng chờ tác vụ hiện tại hoàn thành.")
            return
        if not selected_texts:
            QMessageBox.information(self, "Chưa chọn segment", "Hãy đánh dấu ít nhất một checkbox trước khi dùng AI.")
            return
        chain = self._sync_repair_chain(candidate_id)
        if not chain:
            return
        page = self.pages[StepId.SYNC]
        provider_name = chain["provider"]
        page.set_repair_busy(True, f"Đang gửi {len(selected_texts)} segment tới {provider_name}…")
        page.set_progress(0, f"Đang chuẩn bị nội dung cho {provider_name}…")
        worker = AudioSyncAiRewriteWorker(
            chain["sync_manifest"],
            selected_texts,
            chain["model"],
            provider_name,
            self.state.source_language,
            self.state.target_language,
        )
        self.active_workers.add(worker)
        worker.progress_changed.connect(page.set_progress)
        outcome: dict[str, object] = {"handled": False, "error": None}

        def succeeded(result: object) -> None:
            outcome["handled"] = True
            data = result if isinstance(result, dict) else {}
            translations = {
                int(key): str(value)
                for key, value in dict(data.get("translations", {})).items()
            }
            message = str(data.get("message") or "AI đã cập nhật nội dung trong danh sách.")
            page.set_progress(100, message)
            page.ai_rewrite_finished(translations, message)

        def failed(payload: object) -> None:
            outcome["handled"] = True
            outcome["error"] = payload
            page.set_repair_busy(False)

        def finished() -> None:
            self.active_workers.discard(worker)
            worker.deleteLater()
            if not outcome["handled"]:
                page.set_repair_busy(False)
                outcome["error"] = {
                    "message": "Tác vụ AI đã kết thúc mà không trả về kết quả."
                }
            if outcome["error"] is not None:
                self._show_worker_error(
                    self,
                    outcome["error"],
                    "Không thể dùng AI chỉnh sửa",
                )

        worker.succeeded.connect(succeeded)
        worker.failed.connect(failed)
        worker.finished.connect(finished)
        QTimer.singleShot(100, worker.start)

    def _repair_sync_segments(
        self, candidate_id: str, segment_updates: dict[int, dict[str, object]]
    ) -> None:
        if self.active_workers:
            QMessageBox.information(self, "Pipeline đang chạy", "Vui lòng chờ tác vụ hiện tại hoàn thành.")
            return
        chain = self._sync_repair_chain(candidate_id)
        if not chain:
            return
        page = self.pages[StepId.SYNC]
        page.prepare_run()
        page.set_repair_busy(True, "Đang tạo lại voice cho các segment đã chọn…")
        page.set_progress(0, "Đang chuẩn bị cập nhật timeline/nội dung riêng của candidate Step 5…")
        worker = AudioSyncBatchRepairWorker(
            chain["sync_manifest"],
            chain["tts_manifest"],
            chain["translation_manifest"],
            chain["transcript_manifest"],
            segment_updates,
            self.state.target_language,
        )
        self.active_workers.add(worker)
        worker.progress_changed.connect(page.set_progress)
        outcome: dict[str, object] = {
            "handled": False,
            "error": None,
            "refresh_warning": "",
        }

        def succeeded(result: object) -> None:
            outcome["handled"] = True
            data = result if isinstance(result, dict) else {}
            processed_ids = {
                int(item)
                for item in data.get("processed_ids", [])
                if isinstance(item, (int, str)) and str(item).isdigit()
            }
            refreshed = self.state.refresh_repaired_chain(
                chain["transcript_id"],
                chain["translation_id"],
                chain["tts_id"],
                candidate_id,
            )
            message = str(data.get("message") or "Đã cập nhật các segment.")
            page.set_progress(100, message)
            page.repair_finished(candidate_id, processed_ids)
            if not refreshed:
                outcome["refresh_warning"] = (
                    "Các manifest đã cập nhật nhưng không thể nạp lại chuỗi candidate."
                )

        def failed(payload: object) -> None:
            outcome["handled"] = True
            outcome["error"] = payload
            page.set_repair_busy(False)

        def finished() -> None:
            self.active_workers.discard(worker)
            worker.deleteLater()
            if not outcome["handled"]:
                page.set_repair_busy(False)
                outcome["error"] = {
                    "message": "Tác vụ tạo lại voice đã kết thúc mà không trả về kết quả."
                }
            if outcome["error"] is not None:
                self._show_worker_error(
                    self,
                    outcome["error"],
                    "Không thể tạo lại voice",
                )
            elif outcome["refresh_warning"]:
                QMessageBox.warning(
                    self,
                    "Không thể nạp lại output",
                    str(outcome["refresh_warning"]),
                )

        worker.succeeded.connect(succeeded)
        worker.failed.connect(failed)
        worker.finished.connect(finished)
        QTimer.singleShot(100, worker.start)

    def _borrow_sync_neighbor_time(self, candidate_id: str) -> None:
        if self.active_workers:
            QMessageBox.information(self, "Pipeline đang chạy", "Vui lòng chờ tác vụ hiện tại hoàn thành.")
            return
        candidate = self.state.sync_candidate(candidate_id)
        if not candidate or not Path(candidate.path).is_file():
            QMessageBox.information(self, "Không tìm thấy output", "Manifest Step 5 không còn tồn tại.")
            return
        page = self.pages[StepId.SYNC]
        page.prepare_run()
        page.set_repair_busy(True, "Đang thử vay thời gian lân cận cho các segment chưa đạt…")
        page.set_progress(0, "Đang chuẩn bị cân lại lịch phát A/B/C…")
        worker = AudioSyncNeighborBorrowWorker(candidate.path)
        self.active_workers.add(worker)
        worker.progress_changed.connect(page.set_progress)
        outcome: dict[str, object] = {
            "handled": False,
            "error": None,
            "refresh_warning": "",
        }

        def succeeded(result: object) -> None:
            outcome["handled"] = True
            data = result if isinstance(result, dict) else {}
            refreshed = self.state.refresh_sync_candidate(candidate_id, activate_if_complete=True)
            message = str(data.get("message") or "Đã hoàn tất vay thời gian lân cận.")
            page.set_progress(100, message)
            page.repair_finished(candidate_id)
            if not refreshed:
                outcome["refresh_warning"] = (
                    "Manifest Step 5 đã cập nhật nhưng không thể nạp lại candidate."
                )

        def failed(payload: object) -> None:
            outcome["handled"] = True
            outcome["error"] = payload
            page.set_repair_busy(False)

        def finished() -> None:
            self.active_workers.discard(worker)
            worker.deleteLater()
            if not outcome["handled"]:
                page.set_repair_busy(False)
                outcome["error"] = {
                    "message": "Tác vụ vay thời gian đã kết thúc mà không trả về kết quả."
                }
            if outcome["error"] is not None:
                self._show_worker_error(
                    self,
                    outcome["error"],
                    "Không thể vay thời gian lân cận",
                )
            elif outcome["refresh_warning"]:
                QMessageBox.warning(
                    self,
                    "Không thể nạp lại output",
                    str(outcome["refresh_warning"]),
                )

        worker.succeeded.connect(succeeded)
        worker.failed.connect(failed)
        worker.finished.connect(finished)
        QTimer.singleShot(100, worker.start)

    def _execute_step(self, step: StepId, continue_all: bool = False) -> None:
        if self.active_workers:
            QMessageBox.information(self, "Pipeline đang chạy", "Vui lòng chờ step hiện tại hoàn thành.")
            return
        if step.value not in self.batch_settings:
            if self.batch_running:
                self._fail_batch_video(f"Step {STEP_ORDER.index(step) + 1:02d} chưa có thiết lập project.")
                return
            QMessageBox.information(
                self,
                "Chưa có thiết lập",
                "Hãy hoàn tất Thiết lập project trước khi chạy pipeline.",
            )
            self._open_project_settings()
            if continue_all:
                self._finish_pipeline_sequence(needs_continue=True)
            return
        if not self.state.can_run(step):
            if self.batch_running:
                self._fail_batch_video(
                    f"Step {STEP_ORDER.index(step) + 1:02d} không có đủ đầu vào."
                )
                return
            QMessageBox.information(self, "Chưa đủ đầu vào", "Hãy hoàn thành step trước trước khi chạy step này.")
            if continue_all:
                self._finish_pipeline_sequence(needs_continue=True)
            return
        page = self.pages[step]
        settings = dict(self.batch_settings.get(step.value, {}))
        if step == StepId.STT:
            settings["input_audio"] = self.state.audio_input_path()
            settings["input_candidate_id"] = self.state.selected_audio_candidate_id
            settings["input_stem"] = self.state.selected_audio_stem
        page.set_busy(True)
        page.prepare_run()
        self.state.mark_running(step)
        self.tabs.setCurrentWidget(page)
        if continue_all:
            self.continue_button.setVisible(True)
            self.continue_button.setEnabled(False)
            self.continue_button.setText(
                f"Đang chạy Step {STEP_ORDER.index(step) + 1}/{len(STEP_ORDER)}…"
            )
        worker = PipelineWorker(self.pipeline, step, self.state, settings)
        outcome: dict[str, object] = {
            "success": False,
            "failed": False,
            "failure_payload": None,
        }
        self.active_workers.add(worker)
        worker.progress_changed.connect(page.set_progress)

        def succeeded(result: object) -> None:
            self.state.set_result(result)
            outcome["success"] = self.state.statuses[step] == StepStatus.DONE
            if (
                outcome["success"]
                and step == StepId.RENDER
                and not self.batch_running
                and self.batch_project
                and self.state.project
            ):
                output_path = result.artifacts.get("output_video", "")
                BatchProjectService.update_video_status(
                    self.batch_project,
                    self.state.project.video_id,
                    "completed",
                    output_path=output_path,
                )
            if outcome["success"]:
                page.set_progress(100, "Hoàn thành.")
            else:
                page.set_progress(0, "Step còn lỗi cần xử lý trước khi tiếp tục.")
            page.set_busy(False)
            page.refresh()

        def failed(payload: object) -> None:
            outcome["failed"] = True
            outcome["failure_payload"] = payload
            self.state.mark_error(step, restore_previous=not continue_all)
            page.set_progress(0, "Xử lý thất bại.")
            page.set_busy(False)
            page.refresh()

        def finished() -> None:
            self.active_workers.discard(worker)
            worker.deleteLater()
            if outcome["failed"]:
                if self.batch_running:
                    self._fail_batch_video(outcome["failure_payload"])
                    return
                self._finish_pipeline_sequence(needs_continue=continue_all)
                self._show_worker_error(
                    self,
                    outcome["failure_payload"],
                    "Không thể chạy step",
                )
            elif outcome["success"] and continue_all and self.running_all:
                next_index = STEP_ORDER.index(step) + 1
                if next_index < len(STEP_ORDER):
                    self._execute_step(STEP_ORDER[next_index], continue_all=True)
                else:
                    if self.batch_running:
                        self._complete_batch_video()
                    else:
                        self._finish_pipeline_sequence()
            elif continue_all and self.running_all:
                if self.batch_running:
                    self._fail_batch_video(
                        f"Step {STEP_ORDER.index(step) + 1:02d} còn lỗi cần xử lý."
                    )
                    return
                self._finish_pipeline_sequence(needs_continue=True)
                QMessageBox.warning(
                    self,
                    f"Step {STEP_ORDER.index(step) + 1:02d} cần xử lý",
                    "Pipeline đã dừng tại step này. Hãy xử lý các lỗi được hiển thị rồi nhấn Continue.",
                )

        worker.succeeded.connect(succeeded)
        worker.failed.connect(failed)
        worker.finished.connect(finished)
        QTimer.singleShot(150, worker.start)

    def _continue_pipeline(self) -> None:
        if not self.state.project:
            QMessageBox.information(self, "Chưa có project", "Vui lòng tạo hoặc mở project trước.")
            self.stack.setCurrentWidget(self.project_manager)
            return
        if self.active_workers:
            QMessageBox.information(self, "Pipeline đang chạy", "Vui lòng chờ step hiện tại hoàn thành.")
            return
        next_step = next(
            (step for step in STEP_ORDER if self.state.statuses[step] != StepStatus.DONE),
            None,
        )
        if next_step is None:
            self._finish_pipeline_sequence()
            return
        pending_transcript = self.state.results.get(StepId.STT)
        if next_step == StepId.STT and pending_transcript and pending_transcript.metadata.get("timeline_error_count", 0):
            self.tabs.setCurrentWidget(self.pages[StepId.STT])
            QMessageBox.information(
                self, "Transcript đã lưu, cần sửa timeline",
                "Mở Kiểm tra / sửa timeline ở Step 2 rồi lưu. Sau khi hết lỗi, Continue sẽ chạy từ Step 3, không nhận dạng lại.",
            )
            return
        self.running_all = True
        self.continue_button.setVisible(True)
        self.continue_button.setEnabled(False)
        self.continue_button.setText("Đang tiếp tục…")
        self._execute_step(next_step, continue_all=True)

    def _finish_pipeline_sequence(self, needs_continue: bool = False) -> None:
        self.running_all = False
        for chain_step in (StepId.STT, StepId.TRANSLATE, StepId.TTS, StepId.BUILD_AUDIO):
            chain_page = self.pages.get(chain_step)
            if hasattr(chain_page, "set_chain_running"):
                chain_page.set_chain_running(False)
        if needs_continue:
            self.continue_button.setVisible(True)
            self.continue_button.setEnabled(True)
            self.continue_button.setText("Continue")
            return
        self.continue_button.setVisible(False)
        self.continue_button.setEnabled(True)
        self.continue_button.setText("Continue")

    def _refresh_step(self, step_value: str) -> None:
        step = StepId(step_value)
        page = self.pages[step]
        if self._loading_project or self.tabs.currentWidget() is not page:
            self._dirty_steps.add(step)
        else:
            page.refresh()
            self._dirty_steps.discard(step)
        self._refresh_header()

    def _refresh_header(self) -> None:
        self._refresh_batch_button()
        active_batch_video = (
            self.batch_project.video(self.batch_current_video_id)
            if self.batch_project and self.batch_running and self.batch_current_video_id
            else None
        )
        if active_batch_video is not None:
            step = (
                StepId(active_batch_video.current_step)
                if active_batch_video.current_step in {item.value for item in STEP_ORDER}
                else None
            )
            step_text = self._step_status_text(step) if step else "Đang chuẩn bị"
            position = (
                self.batch_run_video_ids.index(active_batch_video.id) + 1
                if active_batch_video.id in self.batch_run_video_ids
                else 1
            )
            self.progress_label.setText(
                f"Đang chạy batch {position}/{len(self.batch_run_video_ids)}"
                f" · {active_batch_video.name} · {step_text}"
            )
        elif self.batch_running and self.batch_queue and self.batch_project:
            queued = self.batch_project.video(self.batch_queue[0])
            self.progress_label.setText(
                f"Chờ xử lý · {queued.name}" if queued else "Đang chuẩn bị batch"
            )
        else:
            self.progress_label.setText(self.batch_status_message)
        if (
            all(status == StepStatus.DONE for status in self.state.statuses.values())
            and not self.running_all
            and self.continue_button.isVisible()
        ):
            self._finish_pipeline_sequence()
        project = self.state.project
        if project:
            batch_name = self.batch_project.name if self.batch_project else project.name
            self.project_label.setText(
                f"{batch_name}  ·  {Path(self.state.input_video).name}  ·  "
                f"{self.state.source_language} â†’ {self.state.target_language}"
            )
        else:
            self.project_label.setText("Chưa mở project")

    def _step_status_text(self, step: StepId) -> str:
        page = self.pages.get(step)
        title = page.spec.title if page else step.value
        return f"Step {STEP_ORDER.index(step) + 1:02d}/{len(STEP_ORDER):02d} — {title}"

    def _refresh_batch_button(self) -> None:
        self.step_batch_button.setEnabled(
            bool(self.batch_project) and not self.batch_running and not self.active_workers
        )
        available = bool(
            self.batch_project
            and any(
                video.selected and video.output_count < 1
                for video in self.batch_project.videos
            )
        )
        self.batch_button.setEnabled(
            available and not self.batch_running and not self.active_workers
        )
        self.batch_button.setToolTip(
            "Tạo output lần đầu cho các video chưa có output."
            if available
            else "Tất cả video đã có output; dùng Videos để chạy lại video được chọn."
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.active_workers or self.batch_running:
            QMessageBox.information(self, "Pipeline đang chạy", "Hãy chờ step hiện tại hoàn thành trước khi đóng ứng dụng.")
            event.ignore()
            return
        self.state.save_project()
        if self.batch_project:
            self.batch_project.save()
        super().closeEvent(event)
