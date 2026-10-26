from __future__ import annotations

import json
import math
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
    QCheckBox,
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
from ..common.models import Segment
from ..steps.step3_translation.quality import assess_manual_segments, MANUAL_ISSUE_LABELS


class TranslationTableModel(QAbstractTableModel):
    HEADERS = ("ID", "Speaker", "Start", "End", "Source", "Translation")

    def __init__(
        self,
        segments: list[dict[str, object]],
        editable: bool,
        parent=None,
        manual_check: bool = False,
    ) -> None:
        super().__init__(parent)
        self.segments = segments
        self.editable = editable
        self.dirty = False
        self.manual_check = manual_check
        if manual_check:
            self.HEADERS = (*self.HEADERS, "Cần kiểm tra")

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
                str(segment.get("issue_label", "")),
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
            or index.column() not in ((1, 2, 3, 5) if self.manual_check else (4, 5))
        ):
            return False
        key = {1: "speaker_id", 2: "start", 3: "end", 4: "source_text", 5: "translated_text"}[index.column()]
        text = str(value)
        segment = self.segments[index.row()]
        if index.column() in (2, 3):
            try:
                number = float(text)
            except ValueError:
                return False
            if not math.isfinite(number) or number < 0:
                return False
            if segment[key] == number:
                return False
            segment[key] = number
            self.dirty = True
            self.dataChanged.emit(index, index, [role, Qt.ItemDataRole.DisplayRole])
            return True
        if str(segment[key]) == text:
            return False
        segment[key] = text
        self.dirty = True
        self.dataChanged.emit(index, index, [role, Qt.ItemDataRole.DisplayRole])
        return True

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        flags = super().flags(index)
        columns = (1, 2, 3, 5) if self.manual_check else (4, 5)
        if self.editable and index.isValid() and index.column() in columns:
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

    def updates(self) -> dict[int, dict[str, object]]:
        return {
            int(segment["id"]): {
                "source_text": str(segment["source_text"]),
                "translated_text": str(segment["translated_text"]),
                **({"speaker_id": str(segment["speaker_id"]),
                    "start": segment["start"], "end": segment["end"]}
                   if self.manual_check else {}),
            }
            for segment in self.segments
        }

    def mark_saved(self) -> None:
        self.dirty = False


class TranslationIssueFilter(QSortFilterProxyModel):
    allowed_ids: set[int] | None = None

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if self.allowed_ids is not None and model.segments[source_row]["id"] not in self.allowed_ids:
            return False
        return super().filterAcceptsRow(source_row, source_parent)


class TranslationEditorDialog(QDialog):
    save_requested = Signal(str, object)

    def __init__(self, candidate, editable: bool, parent=None, *, manual_check=False,
                 target_language="Vietnamese") -> None:
        super().__init__(parent)
        self.candidate_id = candidate.id
        segments, load_error = self._read_segments(candidate.path)
        self.manual_check = manual_check
        self.target_language = target_language

        self.setWindowTitle(f"Xem/chỉnh sửa bản dịch · {candidate.label}")
        if manual_check:
            self.setWindowTitle(f"Kiểm tra / sửa bản dịch · {candidate.label}")
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

        self.model = TranslationTableModel(segments, editable and not load_error, self,
                                           manual_check=manual_check)
        self.proxy_model = TranslationIssueFilter(self)
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
        if manual_check:
            options = QHBoxLayout()
            self.show_all = QCheckBox("Hiển thị toàn bộ segment")
            self.show_all.toggled.connect(self._refresh_manual_issues)
            refresh_button = QPushButton("Đánh giá lại")
            refresh_button.clicked.connect(self._refresh_manual_issues)
            options.addWidget(self.show_all)
            options.addWidget(refresh_button)
            options.addStretch()
            root.addLayout(options)
            hint = QLabel(
                "Cảnh báo còn chữ Trung Quốc, câu liền kề giống ≥70% hoặc chứa nhau; "
                "kèm một câu trước và một câu sau để đối chiếu. "
                "Cảnh báo chỉ để tham khảo, không bắt buộc sửa và không khóa Step 4."
            )
            hint.setWordWrap(True)
            root.addWidget(hint)

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
        if manual_check:
            header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.table, 1)
        self.search_input.textChanged.connect(self._filter_changed)
        self._update_match_info()
        if manual_check:
            self._refresh_manual_issues()

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
        if self.manual_check:
            self._refresh_manual_issues()
        self.status_label.setText("Đã lưu thay đổi.")
        self.status_label.setVisible(True)

    def _refresh_manual_issues(self, *_args) -> None:
        segments = self.model.segments
        assessed = assess_manual_segments([
            Segment(**{key: value for key, value in item.items() if key != "issue_label"})
            for item in segments
        ], self.target_language)
        reasons = {int(item["id"]): item["issues"] for item in assessed}
        details = {int(item["id"]): item for item in assessed}
        visible = set(reasons)
        context_ids = set(reasons)
        # Add one preceding/following spoken segment around every flagged pair.
        previous = None
        for item in segments:
            if item["id"] in context_ids and previous is not None:
                visible.add(previous)
            if str(item["translated_text"]).strip():
                previous = item["id"]
        following = None
        for item in reversed(segments):
            if item["id"] in context_ids and following is not None:
                visible.add(following)
            if str(item["translated_text"]).strip():
                following = item["id"]
        for item in segments:
            item["issue_label"] = "; ".join(
                MANUAL_ISSUE_LABELS[reason] for reason in reasons.get(item["id"], [])
            ) or ("Câu lân cận" if item["id"] in visible else "")
            detail = details.get(item["id"], {})
            if detail.get("related_ids"):
                ids = ", ".join(str(value) for value in detail["related_ids"])
                item["issue_label"] += f" · ID {ids} · giống {detail['similarity_percent']:g}%"
        if segments:
            self.model.dataChanged.emit(self.model.index(0, 6), self.model.index(len(segments) - 1, 6))
        self.proxy_model.allowed_ids = None if self.show_all.isChecked() else visible
        self.proxy_model.invalidateFilter()
        self._update_match_info()
        self.match_info.setText(f"{len(reasons)} segment cần kiểm tra · " + self.match_info.text())

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
