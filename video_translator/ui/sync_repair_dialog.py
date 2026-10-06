from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class SyncRepairDialog(QDialog):
    ai_requested = Signal(object)
    process_requested = Signal(object)
    borrow_requested = Signal()
    drafts_changed = Signal(object)

    SELECT_COLUMN = 0
    STATUS_COLUMN = 1
    SEQ_COLUMN = 2
    ID_COLUMN = 3
    SOURCE_COLUMN = 4
    ORIGINAL_COLUMN = 5
    CURRENT_COLUMN = 6
    TIMING_COLUMN = 7
    NOTE_COLUMN = 8
    RESOLVED_ROLE = Qt.ItemDataRole.UserRole.value + 1
    CONTEXT_ROLE = Qt.ItemDataRole.UserRole.value + 2

    def __init__(self, candidate_id: str, payload: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.candidate_id = candidate_id
        self._busy = False
        self.setWindowTitle("Xử lý segment quá thời lượng")
        self.resize(1280, 720)

        layout = QVBoxLayout(self)
        description = QLabel(
            "Popup hiển thị segment từng lỗi cùng segment liền trước/sau. Checkbox dùng cho cả AI và tạo voice. "
            "Bạn có thể sửa trực tiếp cột “Nội dung hiện tại”; nút tạo voice chỉ xử lý các row đã chọn. "
            "Nút vay thời gian xử lý riêng các segment vẫn chưa đạt mà không gọi TTS."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        selection_actions = QHBoxLayout()
        self.select_all_button = QPushButton("Chọn tất cả")
        self.select_all_button.clicked.connect(lambda: self._set_all_checked(True))
        self.clear_all_button = QPushButton("Bỏ chọn tất cả")
        self.clear_all_button.clicked.connect(lambda: self._set_all_checked(False))
        selection_actions.addWidget(self.select_all_button)
        selection_actions.addWidget(self.clear_all_button)
        selection_actions.addStretch()
        layout.addLayout(selection_actions)

        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            ("AI", "Trạng thái", "Seq", "Segment", "Câu nguồn", "Bản dịch ban đầu", "Nội dung hiện tại", "Thời lượng", "Ghi chú")
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        header.resizeSection(self.SELECT_COLUMN, 44)
        header.resizeSection(self.STATUS_COLUMN, 120)
        header.resizeSection(self.SEQ_COLUMN, 48)
        header.resizeSection(self.ID_COLUMN, 135)
        header.resizeSection(self.SOURCE_COLUMN, 210)
        header.resizeSection(self.ORIGINAL_COLUMN, 220)
        header.resizeSection(self.CURRENT_COLUMN, 260)
        header.resizeSection(self.TIMING_COLUMN, 170)
        self.table.itemChanged.connect(self._item_changed)
        layout.addWidget(self.table, 1)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        actions = QHBoxLayout()
        self.ai_button = QPushButton("AI chỉnh sửa các segment đã chọn")
        self.ai_button.clicked.connect(self._request_ai)
        self.process_button = QPushButton("Tạo lại voice và đồng bộ")
        self.process_button.setObjectName("primaryButton")
        self.process_button.clicked.connect(self._request_process)
        self.borrow_button = QPushButton("Vay thời gian lân cận")
        self.borrow_button.clicked.connect(self._request_borrow)
        close_button = QPushButton("Đóng")
        close_button.clicked.connect(self.close)
        actions.addWidget(self.ai_button)
        actions.addWidget(self.process_button)
        actions.addWidget(self.borrow_button)
        actions.addStretch()
        actions.addWidget(close_button)
        layout.addLayout(actions)
        self.load_payload(payload)

    @staticmethod
    def _status_text(item: dict[str, Any], is_context: bool = False) -> str:
        status = str(item.get("repair_status", ""))
        if is_context and status not in {"awaiting_voice", "too_long", "error"}:
            return "Lân cận"
        return {
            "pending": "Chưa chỉnh sửa",
            "awaiting_voice": "Chờ tạo voice",
            "too_long": "Chưa đạt",
            "error": "Lỗi",
            "resolved": "Đã xử lý",
        }.get(status, "Đã xử lý" if item.get("status") == "ready" else "Chưa chỉnh sửa")

    def load_payload(self, payload: dict[str, Any]) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        raw = payload.get("segments", [])
        all_segments = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
        error_indexes = {
            index for index, item in enumerate(all_segments) if item.get("status") != "ready"
        }
        visible_indexes = set(error_indexes)
        for index in error_indexes:
            if index > 0:
                visible_indexes.add(index - 1)
            if index + 1 < len(all_segments):
                visible_indexes.add(index + 1)
        for index in sorted(visible_indexes):
            item = all_segments[index]
            row = self.table.rowCount()
            self.table.insertRow(row)
            segment_id = int(item.get("id", row + 1))
            is_resolved = item.get("status") == "ready"
            is_context = index not in error_indexes
            selector = QTableWidgetItem()
            selector.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            selector.setCheckState(Qt.CheckState.Unchecked)
            selector.setData(Qt.ItemDataRole.UserRole, segment_id)
            selector.setData(self.RESOLVED_ROLE, is_resolved)
            selector.setData(self.CONTEXT_ROLE, is_context)
            self.table.setItem(row, self.SELECT_COLUMN, selector)
            values = (
                self._status_text(item, is_context),
                str(item.get("seq", 1 if item.get("status") != "ready" or item.get("corrected_in_step_5") else 0)),
                f"#{segment_id:04d}\n{float(item.get('start', 0)):.2f}s–{float(item.get('end', 0)):.2f}s",
                str(item.get("source_text", "")),
                str(item.get("original_translated_text", item.get("translated_text", ""))),
                str(item.get("draft_text") or item.get("translated_text", "")),
                (
                    f"TTS {float(item.get('prepared_duration', 0)):.2f}s\n"
                    f"Cho phép {float(item.get('allowed_duration', 0)):.2f}s\n"
                    f"Cần {float(item.get('speed_factor', 1)):.2f}x\n"
                    f"Vay {float(item.get('borrowed_before', 0)):.2f}s trước / "
                    f"{float(item.get('borrowed_after', 0)):.2f}s sau"
                ),
                str(item.get("error", "")),
            )
            for column, value in enumerate(values, start=1):
                cell = QTableWidgetItem(value)
                if column != self.CURRENT_COLUMN:
                    cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == self.CURRENT_COLUMN:
                    cell.setData(Qt.ItemDataRole.UserRole, str(item.get("edit_source", "original")))
                self.table.setItem(row, column, cell)
            self.table.setRowHeight(row, 76)
        self.table.blockSignals(False)
        has_selectable_rows = self.table.rowCount() > 0
        has_errors = any(
            not bool(self.table.item(row, self.SELECT_COLUMN).data(self.RESOLVED_ROLE))
            for row in range(self.table.rowCount())
            if self.table.item(row, self.SELECT_COLUMN)
        )
        self.select_all_button.setEnabled(has_selectable_rows and not self._busy)
        self.clear_all_button.setEnabled(has_selectable_rows and not self._busy)
        self.borrow_button.setEnabled(has_errors and not self._busy)
        self._update_summary()

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != self.CURRENT_COLUMN:
            return
        item.setData(Qt.ItemDataRole.UserRole, "manual")
        status = self.table.item(item.row(), self.STATUS_COLUMN)
        if status:
            status.setText("Chờ tạo voice")
        self._update_summary()

    def selected_ids(self) -> list[int]:
        selected: list[int] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.SELECT_COLUMN)
            if (
                item
                and item.checkState() == Qt.CheckState.Checked
            ):
                selected.append(int(item.data(Qt.ItemDataRole.UserRole)))
        return selected

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.table.blockSignals(True)
        try:
            for row in range(self.table.rowCount()):
                item = self.table.item(row, self.SELECT_COLUMN)
                if item:
                    item.setCheckState(state)
        finally:
            self.table.blockSignals(False)

    def edited_texts(self) -> dict[int, str]:
        result: dict[int, str] = {}
        for row in range(self.table.rowCount()):
            selector = self.table.item(row, self.SELECT_COLUMN)
            content = self.table.item(row, self.CURRENT_COLUMN)
            if selector and content:
                result[int(selector.data(Qt.ItemDataRole.UserRole))] = content.text().strip()
        return result

    def _request_ai(self) -> None:
        selected = self.selected_ids()
        if not selected:
            QMessageBox.information(
                self,
                "Chưa chọn segment",
                "Hãy đánh dấu ít nhất một checkbox trước khi dùng AI chỉnh sửa.",
            )
            return
        texts = self.edited_texts()
        self.drafts_changed.emit(texts)
        self.ai_requested.emit({segment_id: texts[segment_id] for segment_id in selected})

    def _request_process(self) -> None:
        selected = self.selected_ids()
        if not selected:
            QMessageBox.information(
                self,
                "Chưa chọn segment",
                "Hãy đánh dấu ít nhất một checkbox trước khi tạo lại voice.",
            )
            return
        texts = self.edited_texts()
        selected_texts = {segment_id: texts[segment_id] for segment_id in selected}
        empty = [segment_id for segment_id, text in selected_texts.items() if not text]
        if empty:
            QMessageBox.information(
                self,
                "Nội dung đang trống",
                "Hãy nhập nội dung cho: " + ", ".join(f"#{item:04d}" for item in empty),
            )
            return
        self.drafts_changed.emit(texts)
        self.process_requested.emit(selected_texts)

    def _request_borrow(self) -> None:
        self.drafts_changed.emit(self.edited_texts())
        self.borrow_requested.emit()

    def apply_ai_results(self, translations: dict[int, str]) -> None:
        self.table.blockSignals(True)
        try:
            for row in range(self.table.rowCount()):
                selector = self.table.item(row, self.SELECT_COLUMN)
                if not selector:
                    continue
                segment_id = int(selector.data(Qt.ItemDataRole.UserRole))
                if segment_id not in translations:
                    continue
                content = self.table.item(row, self.CURRENT_COLUMN)
                status = self.table.item(row, self.STATUS_COLUMN)
                if content:
                    content.setText(translations[segment_id])
                    content.setData(Qt.ItemDataRole.UserRole, "ai")
                if status:
                    status.setText("Chờ tạo voice")
        finally:
            self.table.blockSignals(False)
        self._update_summary()

    def set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.table.setEnabled(not busy)
        has_selectable_rows = self.table.rowCount() > 0
        has_errors = any(
            not bool(self.table.item(row, self.SELECT_COLUMN).data(self.RESOLVED_ROLE))
            for row in range(self.table.rowCount())
            if self.table.item(row, self.SELECT_COLUMN)
        )
        self.select_all_button.setEnabled(not busy and has_selectable_rows)
        self.clear_all_button.setEnabled(not busy and has_selectable_rows)
        self.ai_button.setEnabled(not busy)
        self.process_button.setEnabled(not busy)
        self.borrow_button.setEnabled(not busy and has_errors)
        if message:
            self.summary.setText(message)
        else:
            self._update_summary()

    def _update_summary(self) -> None:
        total = self.table.rowCount()
        errors = sum(
            1
            for row in range(total)
            if self.table.item(row, self.SELECT_COLUMN)
            and not bool(self.table.item(row, self.SELECT_COLUMN).data(self.RESOLVED_ROLE))
        )
        awaiting = sum(
            1
            for row in range(total)
            if self.table.item(row, self.STATUS_COLUMN)
            and self.table.item(row, self.STATUS_COLUMN).text() == "Chờ tạo voice"
        )
        self.summary.setText(
            f"{total} segment trong danh sách · {errors} đang lỗi · {awaiting} chờ tạo voice"
        )

    def closeEvent(self, event) -> None:
        if self._busy:
            QMessageBox.information(self, "Đang xử lý", "Hãy chờ tác vụ hiện tại hoàn thành.")
            event.ignore()
            return
        self.drafts_changed.emit(self.edited_texts())
        super().closeEvent(event)
