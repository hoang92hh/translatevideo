import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ...models import StepId, StepResult
from ..specs import ProviderSpec, StepSpec
from .base import StepPage


class BuildAudioStepPage(StepPage):
    SPEC = StepSpec(
        StepId.BUILD_AUDIO,
        "06",
        "Build Audio",
        "Ghép các segment voice đã đồng bộ thành một track WAV dài bằng video gốc.",
        (ProviderSpec("FFmpeg Voice Timeline", ()),),
    )

    def __init__(self, state) -> None:
        self._processing = False
        super().__init__(state)

    def build_result_extra(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        title = QLabel("Chọn output voice hoàn chỉnh")
        title.setObjectName("cardTitle")
        layout.addWidget(title)
        self.audio_combo = QComboBox()
        self.audio_combo.currentIndexChanged.connect(self._candidate_changed)
        layout.addWidget(self.audio_combo)
        self.audio_info = QLabel("Chưa có output Step 6")
        self.audio_info.setObjectName("muted")
        self.audio_info.setWordWrap(True)
        layout.addWidget(self.audio_info)

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.8)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._playback_changed)
        self.player.errorOccurred.connect(self._player_error)

        transport = QHBoxLayout()
        self.play_button = QPushButton("Phát")
        self.play_button.clicked.connect(self._toggle_play)
        self.stop_button = QPushButton("Dừng")
        self.stop_button.clicked.connect(self.player.stop)
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.sliderMoved.connect(self.player.setPosition)
        self.time_label = QLabel("00:00 / 00:00")
        transport.addWidget(self.play_button)
        transport.addWidget(self.stop_button)
        transport.addWidget(self.position_slider, 1)
        transport.addWidget(self.time_label)
        volume_label = QLabel("Âm lượng")
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setMaximumWidth(110)
        self.volume_slider.valueChanged.connect(
            lambda value: self.audio_output.setVolume(value / 100)
        )
        transport.addWidget(volume_label)
        transport.addWidget(self.volume_slider)
        layout.addLayout(transport)

        actions = QHBoxLayout()
        use_button = QPushButton("Dùng làm input Step 7")
        use_button.clicked.connect(self._select_for_step_seven)
        open_file = QPushButton("Mở file audio")
        open_file.clicked.connect(self._open_audio)
        open_folder = QPushButton("Mở thư mục")
        open_folder.clicked.connect(self._open_folder)
        delete_button = QPushButton("Xóa output")
        delete_button.clicked.connect(self._delete_output)
        for button in (use_button, open_file, open_folder, delete_button):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        return container

    def _show_result(self, result: StepResult) -> None:
        super()._show_result(result)
        for row in range(self.table.rowCount()):
            for column in range(self.table.columnCount()):
                item = self.table.item(row, column)
                if item:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

    def prepare_run(self) -> None:
        self._release_player("Đã giải phóng trình phát để chạy lại Step 6…")

    def refresh_special(self) -> None:
        if hasattr(self, "audio_combo") and not self._processing:
            self._refresh_outputs()

    def set_busy(self, busy: bool) -> None:
        self._processing = busy
        super().set_busy(busy)
        if not busy and hasattr(self, "audio_combo"):
            self._refresh_outputs(prefer_selected=True)

    def _refresh_outputs(self, prefer_selected: bool = False) -> None:
        current = self.audio_combo.currentData()
        self.audio_combo.blockSignals(True)
        self.audio_combo.clear()
        for candidate in self.state.build_audio_candidates.values():
            suffix = (
                "  [Input Step 7]"
                if candidate.id == self.state.selected_build_audio_candidate_id
                else ""
            )
            self.audio_combo.addItem(f"{candidate.label}{suffix}", candidate.id)
        target_id = self.state.selected_build_audio_candidate_id if prefer_selected else current
        target = self.audio_combo.findData(target_id or self.state.selected_build_audio_candidate_id)
        self.audio_combo.setCurrentIndex(target if target >= 0 else 0)
        self.audio_combo.blockSignals(False)
        self._candidate_changed()

    def _current_candidate(self):
        return self.state.build_audio_candidate(str(self.audio_combo.currentData() or ""))

    def _candidate_changed(self, *_: object) -> None:
        candidate = self._current_candidate()
        self._release_player()
        if not candidate:
            self.audio_info.setText("Chưa có output Step 6")
            self._set_player_enabled(False)
            self.show_candidate_result(None)
            return
        path = Path(candidate.audio_file)
        status = "Sẵn sàng để nghe" if path.is_file() else "File audio không tồn tại"
        self.audio_info.setText(
            f"WAV mono 48 kHz · {candidate.segment_count} segment · "
            f"{candidate.duration_seconds:.2f}s · {status}\n{path}"
        )
        self._set_player_enabled(path.is_file())
        self.show_candidate_result(
            self.state.candidate_result(StepId.BUILD_AUDIO, candidate.id)
        )

    def _toggle_play(self) -> None:
        candidate = self._current_candidate()
        if not candidate or not Path(candidate.audio_file).is_file():
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            return
        if self.player.source().toLocalFile() != candidate.audio_file:
            self.player.setSource(QUrl.fromLocalFile(candidate.audio_file))
        self.player.play()

    def _select_for_step_seven(self) -> None:
        candidate = self._current_candidate()
        if candidate and self.state.select_build_audio_candidate(candidate.id):
            self._refresh_outputs()
            return
        QMessageBox.information(
            self,
            "Không thể chọn",
            "File voice hoặc manifest Step 6 không còn hợp lệ.",
        )

    def _open_audio(self) -> None:
        candidate = self._current_candidate()
        if candidate and Path(candidate.audio_file).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(candidate.audio_file))

    def _open_folder(self) -> None:
        candidate = self._current_candidate()
        if candidate and Path(candidate.folder).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(candidate.folder))

    def _delete_output(self) -> None:
        candidate = self._current_candidate()
        if not candidate:
            return
        answer = QMessageBox.question(
            self,
            "Xóa output Step 6",
            f"Xóa {candidate.label} và file voice hoàn chỉnh của lần chạy này?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._release_player()
        target = Path(candidate.folder).resolve()
        if self.state.project:
            root = self.state.project.path("built_audio").resolve()
            if target.is_relative_to(root) and target != root and target.is_dir():
                shutil.rmtree(target)
        self.state.remove_build_audio_candidate(candidate.id)
        self._refresh_outputs()

    def _set_player_enabled(self, enabled: bool) -> None:
        self.play_button.setEnabled(enabled)
        self.stop_button.setEnabled(enabled)
        self.position_slider.setEnabled(enabled)
        if not enabled:
            self.position_slider.setValue(0)
            self.time_label.setText("00:00 / 00:00")

    def _release_player(self, message: str = "") -> None:
        if not hasattr(self, "player"):
            return
        self.player.stop()
        self.player.setSource(QUrl())
        if message and hasattr(self, "audio_info"):
            self.audio_info.setText(message)

    def _playback_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self.play_button.setText(
            "Tạm dừng" if state == QMediaPlayer.PlaybackState.PlayingState else "Phát"
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
            self.audio_info.setText(f"Không thể phát voice track: {message}")

    def _update_time(self, position: int, duration: int) -> None:
        self.time_label.setText(f"{self._format_ms(position)} / {self._format_ms(duration)}")

    @staticmethod
    def _format_ms(value: int) -> str:
        seconds = max(0, value // 1000)
        return f"{seconds // 60:02d}:{seconds % 60:02d}"
