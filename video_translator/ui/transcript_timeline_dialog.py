from dataclasses import replace

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QTableView, QVBoxLayout,
)

from ..steps.step2_transcription.service import assess_transcript_timeline, TIMELINE_ISSUE_LABELS


class TimelineModel(QAbstractTableModel):
    HEADERS = ("ID", "Speaker", "Start", "End", "Câu nguồn", "Cần sửa")

    def __init__(self, segments, editable, parent=None):
        super().__init__(parent)
        self.segments = segments
        self.editable = editable
        self.times = [(segment.start, segment.end) for segment in segments]
        self.labels = [""] * len(segments)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.segments)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole, Qt.ItemDataRole.ToolTipRole):
            return None
        row, column = index.row(), index.column()
        segment = self.segments[row]
        start, end = self.times[row]
        values = (segment.id, segment.speaker_id,
                  "" if start == end == 0 else start,
                  "" if start == end == 0 else end,
                  segment.source_text, self.labels[row])
        return str(values[column])

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and self.editable and index.column() in (2, 3):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid() or index.column() not in (2, 3) or not self.editable:
            return False
        try:
            number = float(str(value).strip()) if str(value).strip() else 0.0
        except ValueError:
            return False
        values = list(self.times[index.row()])
        values[index.column() - 2] = number
        self.times[index.row()] = tuple(values)
        self.dataChanged.emit(index, index)
        return True

    def updates(self):
        return {segment.id: {"start": start, "end": end}
                for segment, (start, end) in zip(self.segments, self.times)}

    def set_labels(self, labels):
        self.labels = labels
        if self.segments:
            self.dataChanged.emit(self.index(0, 5), self.index(len(self.segments) - 1, 5))


class IssueFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.visible_rows = None

    def filterAcceptsRow(self, source_row, source_parent):
        return self.visible_rows is None or source_row in self.visible_rows


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
        self.show_all.setChecked(True)
        self.show_all.toggled.connect(self._refresh_issues)
        check = QPushButton("Kiểm tra lại")
        check.clicked.connect(self._refresh_issues)
        options.addWidget(self.show_all)
        options.addWidget(check)
        options.addStretch()
        root.addLayout(options)
        self.model = TimelineModel(segments, editable, self)
        self.proxy = IssueFilter(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column in (0, 1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 65)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 90)
        self.table.setColumnWidth(3, 90)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(5, 240)
        self.model.dataChanged.connect(self._mark_dirty)
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

    def _mark_dirty(self, first, last):
        if first.column() in (2, 3):
            self.dirty = True

    def _updates(self):
        return self.model.updates()

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
        labels = [
            "; ".join(TIMELINE_ISSUE_LABELS[reason] for reason in by_id.get(segment.id, []))
            or ("Câu lân cận" if row in visible else "")
            for row, segment in enumerate(edited)
        ]
        self.model.set_labels(labels)
        self.proxy.visible_rows = None if self.show_all.isChecked() else visible
        self.proxy.invalidateFilter()
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
