from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ..steps.step2_transcription.service import assess_transcript_timeline, TIMELINE_ISSUE_LABELS


class TranscriptTimelineDialog(QDialog):
    save_requested = Signal(str, object)

    def __init__(self, candidate, segments, editable, parent=None):
        super().__init__(parent)
        self.candidate_id = candidate.id
        self.segments = segments
        self.editable = editable
        self.dirty = False
        self.setWindowTitle(f"Kiểm tra / sửa timeline · {candidate.label}")
        self.resize(1200, 760)
        root = QVBoxLayout(self)
        info = QLabel(f"{candidate.path}\nChỉ chỉnh Start/End; giữ nguyên ID, speaker và câu nguồn.")
        info.setWordWrap(True)
        root.addWidget(info)
        if not editable:
            notice = QLabel("Đang xem chỉ đọc. Chọn output làm input Step 3 để lưu chỉnh sửa.")
            notice.setWordWrap(True)
            root.addWidget(notice)
        options = QHBoxLayout()
        self.show_all = QCheckBox("Hiển thị toàn bộ segment")
        self.show_all.toggled.connect(self._refresh_issues)
        check = QPushButton("Kiểm tra lại")
        check.clicked.connect(self._refresh_issues)
        options.addWidget(self.show_all)
        options.addWidget(check)
        options.addStretch()
        root.addLayout(options)
        self.table = QTableWidget(len(segments), 6)
        self.table.setHorizontalHeaderLabels(("ID", "Speaker", "Start", "End", "Câu nguồn", "Cần sửa"))
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column in (0, 1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        for row, segment in enumerate(segments):
            missing_timeline = segment.start == 0.0 and segment.end == 0.0
            values = (
                segment.id, segment.speaker_id,
                "" if missing_timeline else segment.start,
                "" if missing_timeline else segment.end,
                segment.source_text, "",
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column == 4:
                    cell.setToolTip(str(value))
                if column not in (2, 3) or not editable:
                    cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, cell)
        self.table.itemChanged.connect(self._mark_dirty)
        root.addWidget(self.table, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        actions = QHBoxLayout()
        actions.addStretch()
        save = QPushButton("Lưu timeline")
        save.setEnabled(editable)
        save.clicked.connect(self._request_save)
        close = QPushButton("Đóng")
        close.clicked.connect(self.close)
        actions.addWidget(save)
        actions.addWidget(close)
        root.addLayout(actions)
        self._refresh_issues()

    def _mark_dirty(self, _item):
        self.dirty = True

    def _updates(self):
        updates = {}
        for row, segment in enumerate(self.segments):
            try:
                start_text = self.table.item(row, 2).text().strip()
                end_text = self.table.item(row, 3).text().strip()
                updates[segment.id] = {
                    "start": float(start_text) if start_text else 0.0,
                    "end": float(end_text) if end_text else 0.0,
                }
            except ValueError:
                QMessageBox.warning(self, "Thời gian không hợp lệ", f"ID {segment.id}: nhập Start/End bằng số giây.")
                return None
        return updates

    def _refresh_issues(self, *_args):
        updates = self._updates()
        if updates is None:
            return
        edited = [replace(segment, **updates[segment.id]) for segment in self.segments]
        issues = assess_transcript_timeline(edited)
        by_id = {item["id"]: item["issues"] for item in issues}
        visible = set()
        for row, segment in enumerate(edited):
            if segment.id in by_id:
                visible.update(range(max(0, row - 1), min(len(edited), row + 2)))
        self.table.blockSignals(True)
        try:
            for row, segment in enumerate(edited):
                label = "; ".join(TIMELINE_ISSUE_LABELS[reason] for reason in by_id.get(segment.id, []))
                self.table.item(row, 5).setText(label or ("Câu lân cận" if row in visible else ""))
                self.table.setRowHidden(row, not self.show_all.isChecked() and row not in visible)
        finally:
            self.table.blockSignals(False)
        self.status.setText(
            f"{len(issues)} segment cần sửa timeline. "
            + ("Có thể lưu tiến độ; Step 3 chỉ chạy khi hết lỗi." if issues else "Timeline hợp lệ, có thể lưu và tiếp tục Step 3.")
        )

    def _request_save(self):
        self.table.clearFocus()
        updates = self._updates()
        if self.editable and updates is not None:
            self.save_requested.emit(self.candidate_id, updates)

    def save_succeeded(self):
        self.dirty = False
        self._refresh_issues()
        self.status.setText("Đã lưu. " + self.status.text())

    def closeEvent(self, event):
        if self.dirty:
            answer = QMessageBox.question(
                self, "Bỏ thay đổi chưa lưu", "Đóng popup và bỏ các thay đổi chưa lưu?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        super().closeEvent(event)
