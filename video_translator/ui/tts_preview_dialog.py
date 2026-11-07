from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QUrl
from PySide6.QtGui import QCloseEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QTableView,
    QVBoxLayout,
)


class TtsPreviewTableModel(QAbstractTableModel):
    HEADERS = ("ID", "Speaker", "Start", "End", "Nội dung")

    def __init__(self, segments: list[dict[str, object]], parent=None) -> None:
        super().__init__(parent)
        self.segments = segments

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.segments)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self.segments):
            return None
        segment = self.segments[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            values = (
                f"{int(segment.get('id', index.row() + 1)):04d}",
                str(segment.get("speaker_id", "")),
                f"{float(segment.get('start', 0.0)):.2f}",
                f"{float(segment.get('end', 0.0)):.2f}",
                str(segment.get("translated_text", "")).strip(),
            )
            return values[index.column()]
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() < 4:
            return int(Qt.AlignmentFlag.AlignCenter)
        return None

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


class TtsPreviewDialog(QDialog):
    def __init__(self, candidate, parent=None) -> None:
        super().__init__(parent)
        self.candidate_id = candidate.id
        self._segments, load_error = self._read_segments(candidate.path)
        self._loaded_row = -1

        self.setWindowTitle(f"Nghe thử output · {candidate.label}")
        self.resize(1280, 800)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(10)

        output_info = QLabel(
            f"{candidate.provider} · {candidate.voice} · {len(self._segments)} segment\n"
            f"{candidate.folder}"
        )
        output_info.setObjectName("muted")
        output_info.setWordWrap(True)
        root.addWidget(output_info)

        self.table = QTableView()
        self.model = TtsPreviewTableModel(self._segments, self)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setResizeContentsPrecision(100)
        for column in range(4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, 1)

        self.segment_info = QLabel(
            load_error or "Chọn một segment rồi bấm “Nghe segment đã chọn”."
        )
        self.segment_info.setObjectName("muted")
        self.segment_info.setWordWrap(True)
        root.addWidget(self.segment_info)

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.8)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._playback_changed)
        self.player.errorOccurred.connect(self._player_error)

        controls = QHBoxLayout()
        previous_button = QPushButton("Đoạn trước")
        previous_button.clicked.connect(lambda: self._move_selection(-1))
        self.play_button = QPushButton("Nghe segment đã chọn")
        self.play_button.clicked.connect(self._toggle_play)
        self.stop_button = QPushButton("Dừng")
        self.stop_button.clicked.connect(self.player.stop)
        next_button = QPushButton("Đoạn tiếp")
        next_button.clicked.connect(lambda: self._move_selection(1))
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.sliderMoved.connect(self.player.setPosition)
        self.time_label = QLabel("00:00 / 00:00")
        volume_label = QLabel("Âm lượng")
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setMaximumWidth(110)
        self.volume_slider.valueChanged.connect(
            lambda value: self.audio_output.setVolume(value / 100)
        )
        for widget in (previous_button, self.play_button, self.stop_button, next_button):
            controls.addWidget(widget)
        controls.addWidget(self.position_slider, 1)
        controls.addWidget(self.time_label)
        controls.addWidget(volume_label)
        controls.addWidget(self.volume_slider)
        root.addLayout(controls)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_button = QPushButton("Đóng")
        close_button.clicked.connect(self.close)
        close_row.addWidget(close_button)
        root.addLayout(close_row)

        self.table.selectionModel().currentRowChanged.connect(self._selection_changed)
        has_segments = bool(self._segments)
        for widget in (
            previous_button,
            self.play_button,
            self.stop_button,
            next_button,
            self.position_slider,
        ):
            widget.setEnabled(has_segments)
        if has_segments:
            self.table.selectRow(0)

    @staticmethod
    def _read_segments(manifest_path: str) -> tuple[list[dict[str, object]], str]:
        try:
            payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            raw_segments = payload.get("segments", [])
            if not isinstance(raw_segments, list):
                raise ValueError("Danh sách segment không hợp lệ")
            segments = [dict(item) for item in raw_segments if isinstance(item, dict)]
            if not segments:
                raise ValueError("Output không có segment để nghe thử")
            return segments, ""
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return [], f"Không thể đọc manifest để nghe thử: {exc}"

    def _selected_row(self) -> int:
        index = self.table.currentIndex()
        return index.row() if index.isValid() else -1

    def _selection_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        self._release_source()
        row = current.row()
        if not 0 <= row < len(self._segments):
            self.segment_info.setText("Chưa chọn segment để nghe thử.")
            return
        segment = self._segments[row]
        segment_id = int(segment.get("id", row + 1))
        text = str(segment.get("translated_text", "")).strip()
        self.segment_info.setText(
            f"Segment #{segment_id:04d} · Chưa nạp audio\n{text}"
        )

    def _toggle_play(self) -> None:
        row = self._selected_row()
        if not 0 <= row < len(self._segments):
            return
        if (
            self._loaded_row == row
            and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        ):
            self.player.pause()
            return
        segment = self._segments[row]
        segment_id = int(segment.get("id", row + 1))
        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", start))
        if bool(segment.get("tts_skipped", False)) or end <= start:
            self.segment_info.setText(f"Segment #{segment_id:04d} · Bỏ qua — segment 0 giây")
            return
        audio_path = str(segment.get("audio_file", "")).strip()
        if not audio_path or not Path(audio_path).is_file():
            QMessageBox.warning(
                self,
                "Không thể nghe segment",
                f"File audio của segment #{segment_id:04d} không tồn tại.",
            )
            return
        if self._loaded_row != row or self.player.source().toLocalFile() != audio_path:
            self.player.stop()
            self.player.setSource(QUrl.fromLocalFile(audio_path))
            self._loaded_row = row
        self.segment_info.setText(f"Segment #{segment_id:04d} · {audio_path}")
        self.player.play()

    def _move_selection(self, offset: int) -> None:
        if not self._segments:
            return
        row = self._selected_row()
        target = max(0, min(len(self._segments) - 1, (row if row >= 0 else 0) + offset))
        self.table.selectRow(target)
        self.table.scrollTo(self.model.index(target, 0))

    def _playback_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self.play_button.setText(
            "Tạm dừng"
            if state == QMediaPlayer.PlaybackState.PlayingState
            else "Nghe segment đã chọn"
        )

    def _duration_changed(self, duration: int) -> None:
        self.position_slider.setRange(0, duration)
        self._update_time(self.player.position(), duration)

    def _position_changed(self, position: int) -> None:
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(position)
        self._update_time(position, self.player.duration())

    def _player_error(self, _error: QMediaPlayer.Error, message: str) -> None:
        if message:
            self.segment_info.setText(f"Không thể phát segment: {message}")

    def _release_source(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self._loaded_row = -1
        self.position_slider.setValue(0)
        self.time_label.setText("00:00 / 00:00")

    @staticmethod
    def _format_ms(value: int) -> str:
        seconds = max(0, value // 1000)
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def _update_time(self, position: int, duration: int) -> None:
        self.time_label.setText(f"{self._format_ms(position)} / {self._format_ms(duration)}")

    def closeEvent(self, event: QCloseEvent) -> None:
        self._release_source()
        super().closeEvent(event)
