from __future__ import annotations

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

from ..models import STEP_ORDER, StepId, StepStatus
from ..pipeline import MockPipeline
from ..project import VideoProject
from ..state import ProjectState
from .project_manager import ProjectManagerPage
from .steps import STEP_PAGE_TYPES
from .steps.base import StepPage


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("TransLanguage — Video Translation & Dubbing")
        self.resize(1440, 900)
        self.setMinimumSize(1120, 720)
        self.state = ProjectState()
        self.pipeline = MockPipeline()
        self.running_all = False
        self.pages: dict[StepId, StepPage] = {}
        self.stack = QStackedWidget()
        self.project_manager = ProjectManagerPage()
        self.project_manager.project_opened.connect(self._open_project)
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
        top_layout.addWidget(project_button)
        top_layout.addWidget(new_button)
        top_layout.addWidget(open_button)
        top_layout.addWidget(save_button)
        self.progress_label = QLabel("0 / 7 steps hoàn thành")
        self.progress_label.setObjectName("progressLabel")
        top_layout.addWidget(self.progress_label)
        self.run_all_button = QPushButton("Chạy toàn bộ pipeline")
        self.run_all_button.setObjectName("primaryButton")
        self.run_all_button.clicked.connect(self._run_all)
        top_layout.addWidget(self.run_all_button)
        root.addWidget(top)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        for page_type in STEP_PAGE_TYPES:
            page = page_type(self.state)
            page.run_requested.connect(self._run_requested)
            self.pages[page.spec.step] = page
            self.tabs.addTab(page, f"{page.spec.number}  {page.spec.title}")
        root.addWidget(self.tabs)
        return workspace

    def _open_project(self, project: VideoProject) -> None:
        self.state.bind_project(project)
        self.project_manager.add_recent(project.manifest_path)
        self.stack.setCurrentWidget(self.workspace)
        for page in self.pages.values():
            page.refresh()

    def _run_requested(self, step_value: str) -> None:
        self._execute_step(StepId(step_value))

    def _execute_step(self, step: StepId, continue_all: bool = False) -> None:
        if not self.state.can_run(step):
            QMessageBox.information(self, "Chưa đủ đầu vào", "Hãy hoàn thành step trước trước khi chạy step này.")
            self._stop_run_all()
            return
        page = self.pages[step]
        settings = page.settings()
        self.state.mark_running(step)
        page.set_busy(True)
        self.tabs.setCurrentWidget(page)

        def finish() -> None:
            try:
                self.state.set_result(self.pipeline.execute(step, self.state, settings))
            except Exception as exc:
                self.state.mark_error(step)
                QMessageBox.critical(self, "Không thể chạy step", str(exc))
                self._stop_run_all()
            finally:
                page.set_busy(False)
                page.refresh()
            if continue_all and self.running_all:
                next_index = STEP_ORDER.index(step) + 1
                if next_index < len(STEP_ORDER):
                    self._execute_step(STEP_ORDER[next_index], continue_all=True)
                else:
                    self._stop_run_all()

        QTimer.singleShot(550, finish)

    def _run_all(self) -> None:
        if not self.state.project:
            QMessageBox.information(self, "Chưa có project", "Vui lòng tạo hoặc mở project trước.")
            self.stack.setCurrentWidget(self.project_manager)
            return
        self.running_all = True
        self.run_all_button.setEnabled(False)
        self.run_all_button.setText("Pipeline đang chạy…")
        self._execute_step(StepId.EXTRACT, continue_all=True)

    def _stop_run_all(self) -> None:
        self.running_all = False
        self.run_all_button.setEnabled(True)
        self.run_all_button.setText("Chạy toàn bộ pipeline")

    def _refresh_step(self, step_value: str) -> None:
        self.pages[StepId(step_value)].refresh()
        self._refresh_header()

    def _refresh_header(self) -> None:
        completed = sum(status == StepStatus.DONE for status in self.state.statuses.values())
        self.progress_label.setText(f"{completed} / {len(STEP_ORDER)} steps hoàn thành")
        project = self.state.project
        if project:
            self.project_label.setText(
                f"{project.name}  ·  {Path(self.state.input_video).name}  ·  "
                f"{self.state.source_language} → {self.state.target_language}"
            )
        else:
            self.project_label.setText("Chưa mở project")

    def closeEvent(self, event: QCloseEvent) -> None:
        self.state.save_project()
        super().closeEvent(event)

