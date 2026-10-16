from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
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
from ..models import STEP_ORDER, StepId, StepStatus
from ..pipeline import MockPipeline
from ..pipeline.worker import (
    AudioSyncAiRewriteWorker,
    AudioSyncBatchRepairWorker,
    AudioSyncNeighborBorrowWorker,
    PipelineWorker,
)
from ..project import VideoProject
from ..state import ProjectState
from .project_manager import ProjectManagerPage
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
        self.pipeline = MockPipeline()
        self.running_all = False
        self.active_workers: set[PipelineWorker] = set()
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
        project_button = QPushButton("Projects")
        project_button.clicked.connect(lambda: self.stack.setCurrentWidget(self.project_manager))
        new_button = QPushButton("Project mới")
        new_button.clicked.connect(self.project_manager.create_new)
        open_button = QPushButton("Mở project")
        open_button.clicked.connect(self.project_manager.open_existing)
        save_button = QPushButton("Lưu")
        save_button.clicked.connect(self.state.save_project)
        settings_button = QPushButton("Cài đặt")
        settings_button.clicked.connect(self._open_provider_settings)
        voice_library_button = QPushButton("Giọng tham chiếu")
        voice_library_button.clicked.connect(self._open_voice_library)
        top_layout.addWidget(project_button)
        top_layout.addWidget(new_button)
        top_layout.addWidget(open_button)
        top_layout.addWidget(save_button)
        top_layout.addWidget(settings_button)
        top_layout.addWidget(voice_library_button)
        self.progress_label = QLabel("Step 00 / 07")
        self.progress_label.setObjectName("progressLabel")
        top_layout.addWidget(self.progress_label)
        self.continue_button = QPushButton("Continue")
        self.continue_button.setObjectName("primaryButton")
        self.continue_button.clicked.connect(self._continue_pipeline)
        self.continue_button.setVisible(False)
        top_layout.addWidget(self.continue_button)
        root.addWidget(top)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        for page_type in STEP_PAGE_TYPES:
            page = page_type(self.state)
            page.run_requested.connect(self._run_requested)
            page.run_from_here_requested.connect(self._run_from_here_requested)
            if hasattr(page, "ai_rewrite_requested"):
                page.ai_rewrite_requested.connect(self._rewrite_sync_segments)
            if hasattr(page, "batch_repair_requested"):
                page.batch_repair_requested.connect(self._repair_sync_segments)
            if hasattr(page, "neighbor_borrow_requested"):
                page.neighbor_borrow_requested.connect(self._borrow_sync_neighbor_time)
            self.pages[page.spec.step] = page
            self.tabs.addTab(page, f"{page.spec.number}  {page.spec.title}")
        self.tabs.currentChanged.connect(self._tab_changed)
        root.addWidget(self.tabs)
        return workspace

    def _open_project(self, project: VideoProject) -> None:
        for page in self.pages.values():
            page.prepare_run()
        self._finish_pipeline_sequence()
        self._loading_project = True
        try:
            self.state.bind_project(project)
        finally:
            self._loading_project = False
        self.project_manager.add_recent(project.manifest_path)
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
            QMessageBox.information(
                self,
                "Chưa đủ đầu vào",
                "Hãy hoàn thành step trước trước khi chạy pipeline từ step này.",
            )
            return
        self.running_all = True
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
        page.set_progress(0, "Đang chuẩn bị cập nhật thời gian/speaker/nội dung từ Step 2 → Step 5…")
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
        if not self.state.can_run(step):
            QMessageBox.information(self, "Chưa đủ đầu vào", "Hãy hoàn thành step trước trước khi chạy step này.")
            if continue_all:
                self._finish_pipeline_sequence(needs_continue=True)
            return
        page = self.pages[step]
        settings = page.settings()
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
                    self._finish_pipeline_sequence()
            elif continue_all and self.running_all:
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
        self.running_all = True
        self.continue_button.setVisible(True)
        self.continue_button.setEnabled(False)
        self.continue_button.setText("Đang tiếp tục…")
        self._execute_step(next_step, continue_all=True)

    def _finish_pipeline_sequence(self, needs_continue: bool = False) -> None:
        self.running_all = False
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
        current = self.tabs.currentWidget()
        progress_step = 0
        if isinstance(current, StepPage):
            step = current.spec.step
            current_index = STEP_ORDER.index(step)
            has_current_output = step in self.state.results
            is_running = self.state.statuses[step] == StepStatus.RUNNING
            progress_step = current_index + 1 if has_current_output or is_running else current_index
        self.progress_label.setText(f"Step {progress_step:02d} / {len(STEP_ORDER):02d}")
        if (
            all(status == StepStatus.DONE for status in self.state.statuses.values())
            and not self.running_all
            and self.continue_button.isVisible()
        ):
            self._finish_pipeline_sequence()
        project = self.state.project
        if project:
            self.project_label.setText(
                f"{project.name}  ·  {Path(self.state.input_video).name}  ·  "
                f"{self.state.source_language} → {self.state.target_language}"
            )
        else:
            self.project_label.setText("Chưa mở project")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.active_workers:
            QMessageBox.information(self, "Pipeline đang chạy", "Hãy chờ step hiện tại hoàn thành trước khi đóng ứng dụng.")
            event.ignore()
            return
        self.state.save_project()
        super().closeEvent(event)
