from pathlib import Path
import shutil

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QMessageBox,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ...models import StepId
from ..components import Card
from ..specs import FieldSpec, ProviderSpec, StepSpec
from .base import StepPage


class ExtractStepPage(StepPage):
    SPEC = StepSpec(
        StepId.EXTRACT,
        "01",
        "Audio Preparation",
        "Tạo Original Mix hoặc tách Voice + Background thành nhiều candidate để so sánh.",
        (
            ProviderSpec("MDX-Net — Audio Separator", (
                FieldSpec("ffmpeg_path", "FFmpeg executable", "file", "Auto"),
                FieldSpec("model", "MDX model", "choice", "UVR-MDX-NET-Inst_HQ_4.onnx", (
                    "UVR-MDX-NET-Inst_HQ_4.onnx",
                    "UVR-MDX-NET-Voc_FT.onnx",
                    "Kim_Vocal_2.onnx",
                )),
                FieldSpec("sample_rate", "Working sample rate", "choice", "44.1 kHz", ("44.1 kHz", "48 kHz")),
                FieldSpec("channels", "Audio channels", "choice", "Stereo", ("Stereo", "Mono")),
                FieldSpec(
                    "device",
                    "Thiết bị",
                    "choice",
                    "Auto",
                    ("Auto", "CPU", "NVIDIA GPU (CUDA)"),
                ),
            )),
            ProviderSpec("Original Audio — FFmpeg", (
                FieldSpec("ffmpeg_path", "FFmpeg executable", "file", "Auto"),
                FieldSpec("sample_rate", "Sample rate", "choice", "44.1 kHz", ("16 kHz", "44.1 kHz", "48 kHz")),
                FieldSpec("channels", "Audio channels", "choice", "Stereo", ("Stereo", "Mono")),
            )),
            ProviderSpec("Demucs — Chưa triển khai", available=False),
            ProviderSpec("RoFormer — Chưa triển khai", available=False),
        ),
    )

    def build_special_card(self) -> Card:
        card = Card("Thông tin project", "Video và cặp ngôn ngữ được thiết lập khi tạo project.")
        form = QFormLayout()
        self.project_name = QLabel("—")
        self.language_pair = QLabel("—")
        self.video_path = QLabel("—")
        self.video_path.setWordWrap(True)
        form.addRow("Project", self.project_name)
        form.addRow("Ngôn ngữ", self.language_pair)
        form.addRow("Video gốc", self.video_path)
        card.content_layout.addLayout(form)
        return card

    def refresh_special(self) -> None:
        project = self.state.project
        self.project_name.setText(project.name if project else "—")
        self.language_pair.setText(f"{self.state.source_language} → {self.state.target_language}")
        self.video_path.setText(self.state.input_video or "—")
        if hasattr(self, "candidate_combo") and not getattr(self, "_processing", False):
            self._refresh_candidates()

    def prepare_run(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self.audio_info.setText("Đã giải phóng audio player để bắt đầu xử lý…")

    def set_busy(self, busy: bool) -> None:
        self._processing = busy
        super().set_busy(busy)
        if not busy and hasattr(self, "candidate_combo"):
            self._refresh_candidates(prefer_selected=True)

    def build_result_extra(self) -> QWidget:
        self.table.setVisible(False)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        title = QLabel("Nghe và chọn output")
        title.setObjectName("cardTitle")
        layout.addWidget(title)

        selectors = QHBoxLayout()
        self.candidate_combo = QComboBox()
        self.candidate_combo.currentIndexChanged.connect(self._candidate_changed)
        self.stem_combo = QComboBox()
        self.stem_combo.currentIndexChanged.connect(self._stem_changed)
        selectors.addWidget(self.candidate_combo, 2)
        selectors.addWidget(self.stem_combo, 1)
        layout.addLayout(selectors)

        self.audio_info = QLabel("Chưa có candidate audio")
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

        transport = QHBoxLayout()
        self.play_button = QPushButton("Phát")
        self.play_button.clicked.connect(self._toggle_play)
        stop_button = QPushButton("Dừng")
        stop_button.clicked.connect(self.player.stop)
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.sliderMoved.connect(self.player.setPosition)
        self.time_label = QLabel("00:00 / 00:00")
        transport.addWidget(self.play_button)
        transport.addWidget(stop_button)
        transport.addWidget(self.position_slider, 1)
        transport.addWidget(self.time_label)
        volume_label = QLabel("Âm lượng")
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setMaximumWidth(110)
        self.volume_slider.valueChanged.connect(lambda value: self.audio_output.setVolume(value / 100))
        transport.addWidget(volume_label)
        transport.addWidget(self.volume_slider)
        layout.addLayout(transport)

        actions = QHBoxLayout()
        select_button = QPushButton("Dùng làm input Step 2")
        select_button.clicked.connect(self._select_for_step_two)
        open_file = QPushButton("Mở file")
        open_file.clicked.connect(self._open_file)
        open_folder = QPushButton("Mở thư mục")
        open_folder.clicked.connect(self._open_folder)
        delete_button = QPushButton("Xóa candidate")
        delete_button.clicked.connect(self._delete_candidate)
        actions.addWidget(select_button)
        actions.addWidget(open_file)
        actions.addWidget(open_folder)
        actions.addWidget(delete_button)
        actions.addStretch()
        layout.addLayout(actions)
        return container

    def _refresh_candidates(self, prefer_selected: bool = False) -> None:
        current = self.candidate_combo.currentData()
        self.candidate_combo.blockSignals(True)
        self.candidate_combo.clear()
        for candidate in self.state.audio_candidates.values():
            suffix = "  [Input Step 2]" if candidate.id == self.state.selected_audio_candidate_id else ""
            self.candidate_combo.addItem(f"{candidate.label}{suffix}", candidate.id)
        target_id = self.state.selected_audio_candidate_id if prefer_selected else current
        target = self.candidate_combo.findData(target_id or self.state.selected_audio_candidate_id)
        self.candidate_combo.setCurrentIndex(target if target >= 0 else 0)
        self.candidate_combo.blockSignals(False)
        self._candidate_changed()

    def _candidate_changed(self, *_: object) -> None:
        candidate = self.state.candidate(str(self.candidate_combo.currentData() or ""))
        self.stem_combo.blockSignals(True)
        self.stem_combo.clear()
        if candidate:
            for stem in candidate.stems:
                self.stem_combo.addItem(stem.replace("_", " ").title(), stem)
            preferred = self.state.selected_audio_stem if candidate.id == self.state.selected_audio_candidate_id else "voice"
            index = self.stem_combo.findData(preferred)
            self.stem_combo.setCurrentIndex(index if index >= 0 else 0)
        self.stem_combo.blockSignals(False)
        self._load_audio()

    def _stem_changed(self, *_: object) -> None:
        self._load_audio()

    def _current_selection(self) -> tuple[str, str, str]:
        candidate_id = str(self.candidate_combo.currentData() or "")
        stem = str(self.stem_combo.currentData() or "")
        candidate = self.state.candidate(candidate_id)
        return candidate_id, stem, candidate.stem_path(stem) if candidate else ""

    def _load_audio(self) -> None:
        self.player.stop()
        candidate_id, stem, path = self._current_selection()
        exists = bool(path and Path(path).is_file())
        self.player.setSource(QUrl.fromLocalFile(path) if exists else QUrl())
        candidate = self.state.candidate(candidate_id)
        if candidate:
            status = "Sẵn sàng" if exists else "File không tồn tại"
            actual_device = str(candidate.metadata.get("actual_device", "")).strip()
            device_text = f" · {actual_device}" if actual_device else ""
            self.audio_info.setText(
                f"{candidate.provider} · {candidate.model_name}{device_text} · {stem} · {status}\n{path}"
            )
        else:
            self.audio_info.setText("Chưa có candidate audio")
        self.play_button.setEnabled(exists)

    def _toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _playback_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self.play_button.setText("Tạm dừng" if state == QMediaPlayer.PlaybackState.PlayingState else "Phát")

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

    def _select_for_step_two(self) -> None:
        candidate_id, stem, path = self._current_selection()
        if stem not in {"voice", "original"}:
            QMessageBox.information(
                self,
                "Stem không phù hợp",
                "Step 2 nhận Voice hoặc Original Mix. Background được giữ lại để ghép audio ở các step sau.",
            )
            return
        if path and Path(path).is_file():
            self.state.select_audio_input(candidate_id, stem)
            self._refresh_candidates()

    def _open_file(self) -> None:
        path = self._current_selection()[2]
        if path and Path(path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_folder(self) -> None:
        path = self._current_selection()[2]
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

    def _delete_candidate(self) -> None:
        candidate_id, _, _ = self._current_selection()
        candidate = self.state.candidate(candidate_id)
        if not candidate or candidate_id == "original-mix":
            QMessageBox.information(self, "Không thể xóa", "Original Mix là artifact cơ sở của Step 1.")
            return
        answer = QMessageBox.question(
            self,
            "Xóa candidate",
            f"Xóa {candidate.label} và các file Voice/Background của candidate này?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.state.project:
            root = self.state.project.path("audio_separation").resolve()
            candidate_dir = self.state.project.path("audio_separation", candidate_id).resolve()
            if candidate_dir.is_relative_to(root) and candidate_dir != root:
                shutil.rmtree(candidate_dir, ignore_errors=True)
        self.state.remove_audio_candidate(candidate_id)
        self._refresh_candidates()
