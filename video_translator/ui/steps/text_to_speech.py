import json
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

from ...config.tts import (
    EDGE_TTS_PROVIDER,
    EDGE_VOICES,
    MELO_OPENVOICE_PROVIDER,
    MELO_SPEAKERS,
    PROVIDER_LICENSE_NOTES,
    VIENEU_PROVIDER,
    VIENEU_VOICES,
    XTTS_V2_PROVIDER,
)
from ...models import StepId
from ..components import Card
from ..specs import DEVICE_FIELD, FieldSpec, ProviderSpec, StepSpec
from .base import StepPage


class TextToSpeechStepPage(StepPage):
    SPEC = StepSpec(
        StepId.TTS,
        "04",
        "Text to Speech",
        "Tạo một file giọng nói riêng cho mỗi segment.",
        (
            ProviderSpec(VIENEU_PROVIDER, (
                FieldSpec("voice", "Giọng", "choice", "Default", VIENEU_VOICES),
                FieldSpec("speed", "Tốc độ", "float", 1.0),
                DEVICE_FIELD,
                FieldSpec("reference_voice", "Audio tham chiếu", "file", ""),
                FieldSpec("voice_consent", "Tôi có quyền sử dụng giọng", "bool", False),
            )),
            ProviderSpec(MELO_OPENVOICE_PROVIDER, (
                FieldSpec("voice", "Giọng nền", "choice", "EN-Default", MELO_SPEAKERS),
                FieldSpec("speed", "Tốc độ", "float", 1.0),
                DEVICE_FIELD,
                FieldSpec("reference_voice", "Audio để chuyển/clone giọng", "file", ""),
                FieldSpec("voice_consent", "Tôi có quyền sử dụng giọng", "bool", False),
            )),
            ProviderSpec(EDGE_TTS_PROVIDER, (
                FieldSpec("voice", "Giọng online", "choice", "vi-VN-HoaiMyNeural", EDGE_VOICES),
                FieldSpec("speed", "Tốc độ", "float", 1.0),
            )),
            ProviderSpec(XTTS_V2_PROVIDER, available=False),
        ),
    )

    def __init__(self, state) -> None:
        self._loaded_manifest_signature: tuple[str, int, int] | None = None
        super().__init__(state)
        self.table.cellClicked.connect(self._table_segment_clicked)
        self.provider_panel.provider_combo.currentTextChanged.connect(self._update_provider_note)
        self._update_provider_note(self.provider_panel.provider_combo.currentText())

    def build_special_card(self) -> Card:
        card = Card("Giấy phép & quyền sử dụng giọng")
        self.provider_note = QLabel()
        self.provider_note.setObjectName("muted")
        self.provider_note.setWordWrap(True)
        card.content_layout.addWidget(self.provider_note)
        return card

    def _update_provider_note(self, provider: str) -> None:
        self.provider_note.setText(PROVIDER_LICENSE_NOTES.get(provider, ""))

    def build_result_extra(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        title = QLabel("Chọn output giọng nói")
        title.setObjectName("cardTitle")
        layout.addWidget(title)
        self.tts_combo = QComboBox()
        self.tts_combo.currentIndexChanged.connect(self._tts_changed)
        layout.addWidget(self.tts_combo)
        self.tts_info = QLabel("Chưa có output TTS")
        self.tts_info.setObjectName("muted")
        self.tts_info.setWordWrap(True)
        layout.addWidget(self.tts_info)

        self.segment_combo = QComboBox()
        self.segment_combo.currentIndexChanged.connect(self._segment_changed)
        layout.addWidget(self.segment_combo)
        self.segment_info = QLabel("Chọn một segment để nghe kiểm tra")
        self.segment_info.setObjectName("muted")
        self.segment_info.setWordWrap(True)
        layout.addWidget(self.segment_info)

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.8)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._playback_changed)
        self.player.errorOccurred.connect(self._player_error)

        transport = QHBoxLayout()
        previous_button = QPushButton("Đoạn trước")
        previous_button.clicked.connect(lambda: self._move_segment(-1))
        self.play_button = QPushButton("Phát")
        self.play_button.clicked.connect(self._toggle_play)
        self.stop_button = QPushButton("Dừng")
        self.stop_button.clicked.connect(self.player.stop)
        next_button = QPushButton("Đoạn tiếp")
        next_button.clicked.connect(lambda: self._move_segment(1))
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.sliderMoved.connect(self.player.setPosition)
        self.time_label = QLabel("00:00 / 00:00")
        transport.addWidget(previous_button)
        transport.addWidget(self.play_button)
        transport.addWidget(self.stop_button)
        transport.addWidget(next_button)
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
        use_button = QPushButton("Dùng làm input Step 5")
        use_button.clicked.connect(self._select_for_step_five)
        open_file = QPushButton("Mở manifest")
        open_file.clicked.connect(self._open_manifest)
        open_folder = QPushButton("Mở thư mục")
        open_folder.clicked.connect(self._open_folder)
        delete_button = QPushButton("Xóa output")
        delete_button.clicked.connect(self._delete_output)
        for button in (use_button, open_file, open_folder, delete_button):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        return container

    def prepare_run(self) -> None:
        self._release_player("Đã giải phóng trình phát để chạy lại Step 4…")

    def refresh_special(self) -> None:
        if hasattr(self, "tts_combo") and not getattr(self, "_processing", False):
            self._refresh_outputs()

    def set_busy(self, busy: bool) -> None:
        self._processing = busy
        super().set_busy(busy)
        if not busy and hasattr(self, "tts_combo"):
            self._refresh_outputs(prefer_selected=True)

    def _refresh_outputs(self, prefer_selected: bool = False) -> None:
        current = self.tts_combo.currentData()
        self.tts_combo.blockSignals(True)
        self.tts_combo.clear()
        for candidate in self.state.tts_candidates.values():
            suffix = "  [Input Step 5]" if candidate.id == self.state.selected_tts_candidate_id else ""
            self.tts_combo.addItem(f"{candidate.label}{suffix}", candidate.id)
        target_id = self.state.selected_tts_candidate_id if prefer_selected else current
        target = self.tts_combo.findData(target_id or self.state.selected_tts_candidate_id)
        self.tts_combo.setCurrentIndex(target if target >= 0 else 0)
        self.tts_combo.blockSignals(False)
        self._tts_changed()

    def _current_output(self):
        return self.state.tts_candidate(str(self.tts_combo.currentData() or ""))

    def _tts_changed(self, *_: object) -> None:
        candidate = self._current_output()
        if not candidate:
            self.tts_info.setText("Chưa có output TTS")
            self._clear_segments()
            return
        status = "Sẵn sàng" if Path(candidate.path).is_file() else "Manifest không tồn tại"
        device = str(candidate.metadata.get("actual_device", ""))
        self.tts_info.setText(f"{candidate.provider} · {candidate.voice} · {device} · {candidate.segment_count} segment · {status}\n{candidate.folder}")
        self._load_candidate_segments(candidate.path)

    def _load_candidate_segments(self, manifest_path: str) -> None:
        manifest = Path(manifest_path)
        try:
            stat = manifest.stat()
            signature = (str(manifest.resolve()), stat.st_mtime_ns, stat.st_size)
        except OSError:
            signature = None
        if signature and signature == self._loaded_manifest_signature and self._preview_segments:
            return
        self.player.stop()
        if not self.player.source().isEmpty():
            self.player.setSource(QUrl())
        self.segment_combo.blockSignals(True)
        self.segment_combo.clear()
        self._preview_segments: list[dict[str, object]] = []
        error_message = ""
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            raw_segments = payload.get("segments", [])
            if not isinstance(raw_segments, list):
                raise ValueError("Danh sách segment không hợp lệ")
            for item in raw_segments:
                if not isinstance(item, dict):
                    continue
                segment_id = int(item.get("id", len(self._preview_segments) + 1))
                start = float(item.get("start", 0.0))
                end = float(item.get("end", 0.0))
                text = str(item.get("translated_text", "")).strip().replace("\n", " ")
                item_data = {**item, "id": segment_id, "start": start, "end": end}
                self._preview_segments.append(item_data)
                short_text = f"{text[:72]}…" if len(text) > 72 else text
                self.segment_combo.addItem(
                    f"#{segment_id:04d} · {start:.2f}s–{end:.2f}s · {short_text}",
                    len(self._preview_segments) - 1,
                )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            error_message = f"Không thể đọc manifest để nghe thử: {exc}"
        self.segment_combo.blockSignals(False)
        if self.segment_combo.count():
            self._loaded_manifest_signature = signature
            self.segment_combo.setCurrentIndex(0)
            self._segment_changed()
        else:
            self._loaded_manifest_signature = None
            self._clear_player_source(
                error_message or "Output này không có segment hợp lệ để nghe."
            )

    def _clear_segments(self) -> None:
        self._loaded_manifest_signature = None
        self._preview_segments = []
        self.segment_combo.blockSignals(True)
        self.segment_combo.clear()
        self.segment_combo.blockSignals(False)
        self._clear_player_source("Chưa có segment để nghe kiểm tra.")

    def _segment_changed(self, *_: object) -> None:
        index = self.segment_combo.currentData()
        if not isinstance(index, int) or index < 0 or index >= len(self._preview_segments):
            self._clear_player_source("Chưa có segment để nghe kiểm tra.")
            return
        segment = self._preview_segments[index]
        path = str(segment.get("audio_file", ""))
        exists = bool(path and Path(path).is_file())
        self.player.stop()
        if not self.player.source().isEmpty():
            self.player.setSource(QUrl())
        segment_id = int(segment.get("id", index + 1))
        text = str(segment.get("translated_text", "")).strip()
        status = "Sẵn sàng để nghe" if exists else "File audio không tồn tại"
        self.segment_info.setText(f"Segment #{segment_id:04d} · {status}\n{text}\n{path}")
        self.play_button.setEnabled(exists)
        self.stop_button.setEnabled(exists)
        self.position_slider.setEnabled(exists)
        self.position_slider.setValue(0)
        self.time_label.setText("00:00 / 00:00")

    def _table_segment_clicked(self, row: int, _column: int) -> None:
        item = self.table.item(row, 0)
        if not item:
            return
        try:
            segment_id = int(item.text())
        except ValueError:
            return
        for combo_index, segment in enumerate(self._preview_segments):
            if int(segment.get("id", -1)) == segment_id:
                self.segment_combo.setCurrentIndex(combo_index)
                return

    def _move_segment(self, offset: int) -> None:
        count = self.segment_combo.count()
        if not count:
            return
        target = max(0, min(count - 1, self.segment_combo.currentIndex() + offset))
        self.segment_combo.setCurrentIndex(target)

    def _toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            index = self.segment_combo.currentData()
            if not isinstance(index, int) or index < 0 or index >= len(self._preview_segments):
                return
            path = str(self._preview_segments[index].get("audio_file", ""))
            if not path or not Path(path).is_file():
                return
            if self.player.source().toLocalFile() != path:
                self.player.setSource(QUrl.fromLocalFile(path))
            self.player.play()

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

    def _update_time(self, position: int, duration: int) -> None:
        self.time_label.setText(f"{self._format_ms(position)} / {self._format_ms(duration)}")

    @staticmethod
    def _format_ms(value: int) -> str:
        seconds = max(0, value // 1000)
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def _player_error(self, _error: QMediaPlayer.Error, message: str) -> None:
        if message:
            self.segment_info.setText(f"Không thể phát segment: {message}")

    def _clear_player_source(self, message: str) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self.play_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.position_slider.setEnabled(False)
        self.position_slider.setValue(0)
        self.time_label.setText("00:00 / 00:00")
        self.segment_info.setText(message)

    def _release_player(self, message: str = "") -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        if message:
            self.segment_info.setText(message)

    def _select_for_step_five(self) -> None:
        candidate = self._current_output()
        if candidate and self.state.select_tts_candidate(candidate.id):
            self._refresh_outputs()
            return
        QMessageBox.information(self, "Không thể chọn", "Manifest hoặc một file audio segment không còn hợp lệ.")

    def _open_manifest(self) -> None:
        candidate = self._current_output()
        if candidate and Path(candidate.path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(candidate.path))

    def _open_folder(self) -> None:
        candidate = self._current_output()
        if candidate and Path(candidate.folder).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(candidate.folder))

    def _delete_output(self) -> None:
        candidate = self._current_output()
        if not candidate:
            return
        answer = QMessageBox.question(self, "Xóa output TTS", f"Xóa {candidate.label} và toàn bộ file audio của lần chạy này?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._release_player()
        target = Path(candidate.folder).resolve()
        if self.state.project:
            root = self.state.project.path("generated_audio").resolve()
            if target.is_relative_to(root) and target != root and target.is_dir():
                shutil.rmtree(target)
        self.state.remove_tts_candidate(candidate.id)
        self._refresh_outputs()
