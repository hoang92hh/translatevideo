from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
)


class TranslationTableModel(QAbstractTableModel):
    HEADERS = ("ID", "Speaker", "Start", "End", "Source", "Translation")

    def __init__(
        self,
        segments: list[dict[str, object]],
        editable: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.segments = segments
        self.editable = editable
        self.dirty = False

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.segments)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self.segments):
            return None
        segment = self.segments[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            values = (
                str(segment["id"]),
                str(segment["speaker_id"]),
                f"{float(segment['start']):.2f}",
                f"{float(segment['end']):.2f}",
                str(segment["source_text"]),
                str(segment["translated_text"]),
            )
            return values[index.column()]
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() < 4:
            return int(Qt.AlignmentFlag.AlignCenter)
        return None

    def setData(
        self,
        index: QModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if (
            role != Qt.ItemDataRole.EditRole
            or not self.editable
            or not index.isValid()
            or index.column() not in (4, 5)
        ):
            return False
        key = "source_text" if index.column() == 4 else "translated_text"
        text = str(value)
        segment = self.segments[index.row()]
        if str(segment[key]) == text:
            return False
        segment[key] = text
        self.dirty = True
        self.dataChanged.emit(index, index, [role, Qt.ItemDataRole.DisplayRole])
        return True

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        flags = super().flags(index)
        if self.editable and index.isValid() and index.column() in (4, 5):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(self.HEADERS)
        ):
            return self.HEADERS[section]
        return super().headerData(section, orientation, role)

    def updates(self) -> dict[int, dict[str, str]]:
        return {
            int(segment["id"]): {
                "source_text": str(segment["source_text"]),
                "translated_text": str(segment["translated_text"]),
            }
            for segment in self.segments
        }

    def mark_saved(self) -> None:
        self.dirty = False


class TranslationEditorDialog(QDialog):
    save_requested = Signal(str, object)

    def __init__(self, candidate, editable: bool, parent=None) -> None:
        super().__init__(parent)
        self.candidate_id = candidate.id
        segments, load_error = self._read_segments(candidate.path)

        self.setWindowTitle(f"Xem/chỉnh sửa bản dịch · {candidate.label}")
        self.resize(1360, 840)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(10)

        output_info = QLabel(
            f"{candidate.segment_count} segment · {candidate.path}"
        )
        output_info.setObjectName("muted")
        output_info.setWordWrap(True)
        root.addWidget(output_info)

        message = load_error
        if not message and not editable:
            message = (
                "Đang xem ở chế độ chỉ đọc. Để chỉnh sửa, đóng cửa sổ này rồi "
                "chọn output làm input Step 4."
            )
        self.status_label = QLabel(message)
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        self.status_label.setVisible(bool(message))
        root.addWidget(self.status_label)

        self.model = TranslationTableModel(segments, editable and not load_error, self)
        self.proxy_model = QSortFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.model)
        self.proxy_model.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy_model.setFilterKeyColumn(-1)

        search_row = QHBoxLayout()
        search_label = QLabel("Tìm kiếm")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "Nhập ID, speaker, nội dung nguồn hoặc bản dịch…"
        )
        self.search_input.setClearButtonEnabled(True)
        self.match_info = QLabel()
        self.match_info.setObjectName("muted")
        search_row.addWidget(search_label)
        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(self.match_info)
        root.addLayout(search_row)

        self.table = QTableView()
        self.table.setModel(self.proxy_model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setResizeContentsPrecision(100)
        for column in range(4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, 1)
        self.search_input.textChanged.connect(self._filter_changed)
        self._update_match_info()

        actions = QHBoxLayout()
        actions.addStretch()
        self.save_button = QPushButton("Lưu thay đổi")
        self.save_button.setEnabled(self.model.editable)
        self.save_button.clicked.connect(self._request_save)
        close_button = QPushButton("Đóng")
        close_button.clicked.connect(self.close)
        actions.addWidget(self.save_button)
        actions.addWidget(close_button)
        root.addLayout(actions)

    @staticmethod
    def _read_segments(manifest_path: str) -> tuple[list[dict[str, object]], str]:
        try:
            payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            raw_segments = payload.get("segments", [])
            if not isinstance(raw_segments, list):
                raise ValueError("Danh sách segment không hợp lệ")
            segments = []
            for index, item in enumerate(raw_segments, start=1):
                if not isinstance(item, dict):
                    continue
                segments.append(
                    {
                        "id": int(item.get("id", index)),
                        "speaker_id": str(item.get("speaker_id", "")),
                        "start": float(item.get("start", 0.0)),
                        "end": float(item.get("end", 0.0)),
                        "source_text": str(
                            item.get("source_text", item.get("text", ""))
                        ),
                        "translated_text": str(item.get("translated_text", "")),
                    }
                )
            if not segments:
                raise ValueError("Output không có segment để hiển thị")
            return segments, ""
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return [], f"Không thể đọc file bản dịch: {exc}"

    def _request_save(self) -> None:
        if not self.model.editable:
            return
        self.table.clearFocus()
        self.save_requested.emit(self.candidate_id, self.model.updates())

    def _filter_changed(self, text: str) -> None:
        self.proxy_model.setFilterFixedString(text.strip())
        self._update_match_info()

    def _update_match_info(self) -> None:
        self.match_info.setText(
            f"Đang hiển thị {self.proxy_model.rowCount()} / "
            f"{self.model.rowCount()} segment"
        )

    def save_succeeded(self) -> None:
        self.model.mark_saved()
        self.status_label.setText("Đã lưu thay đổi.")
        self.status_label.setVisible(True)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.model.dirty:
            answer = QMessageBox.question(
                self,
                "Bỏ thay đổi chưa lưu",
                "Bản dịch có thay đổi chưa được lưu. Bạn có muốn đóng cửa sổ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        super().closeEvent(event)
