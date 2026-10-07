from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...models import STEP_ORDER, StepResult, StepStatus
from ...state import ProjectState
from ..components import Card, ProviderPanel
from ..specs import StepSpec


class StepPage(QWidget):
    run_requested = Signal(str)
    SPEC: StepSpec

    def __init__(self, state: ProjectState) -> None:
        super().__init__()
        self.state = state
        self.spec = self.SPEC
        self._updating_table = False

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 24)
        root.setSpacing(16)
        root.addLayout(self._build_header())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)
        content_layout.addLayout(self._build_settings_column(), 2)
        content_layout.addLayout(self._build_result_column(), 3)
        scroll.setWidget(content)
        root.addWidget(scroll)
        self.refresh()

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        number = QLabel(self.spec.number)
        number.setObjectName("stepNumber")
        title_box = QVBoxLayout()
        title = QLabel(self.spec.title)
        title.setObjectName("pageTitle")
        description = QLabel(self.spec.description)
        description.setObjectName("muted")
        title_box.addWidget(title)
        title_box.addWidget(description)
        header.addWidget(number)
        header.addLayout(title_box)
        header.addStretch()
        self.status = QLabel()
        self.status.setObjectName("statusBadge")
        header.addWidget(self.status)
        return header

    def _build_settings_column(self) -> QVBoxLayout:
        column = QVBoxLayout()
        input_card = Card("Đầu vào", "Kết quả từ step trước được kết nối tự động.")
        self.input_summary = QLabel()
        self.input_summary.setObjectName("inputValue")
        self.input_summary.setWordWrap(True)
        input_card.content_layout.addWidget(self.input_summary)
        column.addWidget(input_card)
        special_card = self.build_special_card()
        if special_card:
            column.addWidget(special_card)
        self.provider_panel = ProviderPanel(self.spec.providers)
        column.addWidget(self.provider_panel)
        column.addStretch()
        return column

    def _build_result_column(self) -> QVBoxLayout:
        column = QVBoxLayout()
        output_card = Card("Kết quả", "Dữ liệu tại đây sẽ trở thành đầu vào của step tiếp theo.")
        self.result_summary = QLabel("Chưa có kết quả")
        self.result_summary.setObjectName("resultSummary")
        self.result_summary.setWordWrap(True)
        output_card.content_layout.addWidget(self.result_summary)
        self.progress_message = QLabel("Đang chờ xử lý")
        self.progress_message.setObjectName("muted")
        self.progress_message.setVisible(False)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setVisible(False)
        output_card.content_layout.addWidget(self.progress_message)
        output_card.content_layout.addWidget(self.progress_bar)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Speaker", "Start", "End", "Source", "Translation", "Audio"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(260)
        self.table.cellChanged.connect(self._table_cell_changed)
        output_card.content_layout.addWidget(self.table)
        self.artifacts = QTextEdit()
        self.artifacts.setReadOnly(True)
        self.artifacts.setMaximumHeight(92)
        self.artifacts.setPlaceholderText("Các artifact đầu ra sẽ xuất hiện tại đây")
        output_card.content_layout.addWidget(self.artifacts)
        result_extra = self.build_result_extra()
        if result_extra:
            output_card.content_layout.addWidget(result_extra)
        column.addWidget(output_card)
        actions = QHBoxLayout()
        actions.addStretch()
        self.run_button = QPushButton(f"Chạy step {self.spec.number}")
        self.run_button.setObjectName("primaryButton")
        self.run_button.clicked.connect(lambda: self.run_requested.emit(self.spec.step.value))
        actions.addWidget(self.run_button)
        column.addLayout(actions)
        return column

    def build_special_card(self) -> Card | None:
        return None

    def build_result_extra(self) -> QWidget | None:
        return None

    def refresh_special(self) -> None:
        pass

    def settings(self) -> dict[str, Any]:
        return self.provider_panel.values()

    def prepare_run(self) -> None:
        """Cho phép step giải phóng resource trước khi worker bắt đầu."""
        pass

    def set_busy(self, busy: bool) -> None:
        self.run_button.setDisabled(busy)
        self.run_button.setText("Đang xử lý…" if busy else f"Chạy step {self.spec.number}")
        if busy:
            self.progress_bar.setValue(0)
            self.progress_bar.setVisible(True)
            self.progress_message.setText("Đang bắt đầu xử lý…")
            self.progress_message.setVisible(True)

    def set_progress(self, value: int, message: str) -> None:
        self.progress_bar.setVisible(True)
        self.progress_message.setVisible(True)
        self.progress_bar.setValue(max(0, min(100, value)))
        self.progress_message.setText(message)

    def refresh(self) -> None:
        status = self.state.statuses[self.spec.step]
        labels = {
            StepStatus.PENDING: "ĐANG CHỜ",
            StepStatus.READY: "SẴN SÀNG",
            StepStatus.RUNNING: "ĐANG CHẠY",
            StepStatus.DONE: "HOÀN THÀNH",
            StepStatus.STALE: "CẦN CẬP NHẬT",
            StepStatus.ERROR: "CÓ LỖI",
        }
        self.status.setText(labels[status])
        self.status.setProperty("state", status.value)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.input_summary.setText(self.state.input_summary(self.spec.step))
        self.refresh_special()
        result = self.state.results.get(self.spec.step)
        if result:
            self._show_result(result)
        else:
            self.result_summary.setText("Chưa có kết quả")
            self.artifacts.clear()
            self.table.setRowCount(0)
        self.run_button.setEnabled(self.state.can_run(self.spec.step) and status != StepStatus.RUNNING)

    def _show_result(self, result: StepResult) -> None:
        self.result_summary.setText(result.summary)
        self.artifacts.setPlainText("\n".join(f"{name}:  {path}" for name, path in result.artifacts.items()))
        self._updating_table = True
        self.table.setRowCount(len(result.segments))
        for row, segment in enumerate(result.segments):
            values = (
                str(segment.id),
                segment.speaker_id,
                f"{segment.start:.2f}",
                f"{segment.end:.2f}",
                segment.source_text,
                segment.translated_text,
                segment.synced_audio_file or segment.audio_file,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column not in (4, 5):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
        self._updating_table = False

    def _table_cell_changed(self, row: int, column: int) -> None:
        if self._updating_table or column not in (4, 5):
            return
        result = self.state.results.get(self.spec.step)
        if not result or row >= len(result.segments):
            return
        text = self.table.item(row, column).text()
        if column == 4:
            result.segments[row].source_text = text
        else:
            result.segments[row].translated_text = text
        index = STEP_ORDER.index(self.spec.step)
        if index + 1 < len(STEP_ORDER):
            self.state.invalidate_from(STEP_ORDER[index + 1])
        self.state.save_project()
