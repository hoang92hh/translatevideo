from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QSettings, QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import (
    QAudioFormat,
    QAudioOutput,
    QAudioSource,
    QMediaDevices,
    QMediaPlayer,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..services.ffmpeg_service import FFmpegError, FFmpegService


class VoiceLibraryDialog(QDialog):
    SETTINGS_FOLDER_KEY = "voiceLibrary/folder"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Thư viện giọng tham chiếu")
        self.resize(820, 620)
        self.setMinimumSize(680, 500)
        self.settings = QSettings("TransLanguage", "TransLanguage")
        self._audio_devices = []
        self._audio_source: QAudioSource | None = None
        self._record_device = None
        self._raw_file = None
        self._raw_path: Path | None = None
        self._record_target: Path | None = None
        self._record_format: QAudioFormat | None = None
        self._record_elapsed_ms = 0

        self.player_output = QAudioOutput(self)
        self.player_output.setVolume(0.8)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.player_output)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._playback_changed)
        self.player.errorOccurred.connect(self._player_error)

        self.record_timer = QTimer(self)
        self.record_timer.setInterval(100)
        self.record_timer.timeout.connect(self._record_tick)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel("Thư viện giọng tham chiếu")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        description = QLabel(
            "Thu WAV từ microphone, lưu vào thư mục trong repository và nghe thử các file đã có."
        )
        description.setObjectName("muted")
        description.setWordWrap(True)
        root.addWidget(description)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Thư mục"))
        self.folder_field = QLineEdit()
        self.folder_field.setReadOnly(True)
        folder_row.addWidget(self.folder_field, 1)
        self.choose_folder_button = QPushButton("Chọn thư mục…")
        self.choose_folder_button.clicked.connect(self._choose_folder)
        folder_row.addWidget(self.choose_folder_button)
        self.open_folder_button = QPushButton("Mở thư mục")
        self.open_folder_button.clicked.connect(self._open_folder)
        folder_row.addWidget(self.open_folder_button)
        self.refresh_button = QPushButton("Làm mới")
        self.refresh_button.clicked.connect(self._refresh_files)
        folder_row.addWidget(self.refresh_button)
        root.addLayout(folder_row)

        self.file_list = QListWidget()
        self.file_list.currentItemChanged.connect(self._file_selected)
        self.file_list.itemDoubleClicked.connect(lambda _item: self._toggle_play())
        root.addWidget(self.file_list, 1)

        player_row = QHBoxLayout()
        self.play_button = QPushButton("Phát")
        self.play_button.clicked.connect(self._toggle_play)
        self.stop_button = QPushButton("Dừng")
        self.stop_button.clicked.connect(self.player.stop)
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.sliderMoved.connect(self.player.setPosition)
        self.time_label = QLabel("00:00 / 00:00")
        player_row.addWidget(self.play_button)
        player_row.addWidget(self.stop_button)
        player_row.addWidget(self.position_slider, 1)
        player_row.addWidget(self.time_label)
        player_row.addWidget(QLabel("Âm lượng"))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setMaximumWidth(120)
        self.volume_slider.valueChanged.connect(
            lambda value: self.player_output.setVolume(value / 100)
        )
        player_row.addWidget(self.volume_slider)
        root.addLayout(player_row)

        record_panel = QWidget()
        record_layout = QGridLayout(record_panel)
        record_layout.setContentsMargins(0, 8, 0, 0)
        record_layout.addWidget(QLabel("Microphone"), 0, 0)
        self.microphone_combo = QComboBox()
        record_layout.addWidget(self.microphone_combo, 0, 1)
        self.refresh_devices_button = QPushButton("Tải lại thiết bị")
        self.refresh_devices_button.clicked.connect(self._refresh_microphones)
        record_layout.addWidget(self.refresh_devices_button, 0, 2)
        record_layout.addWidget(QLabel("Tên file WAV"), 1, 0)
        self.file_name = QLineEdit()
        self.file_name.setPlaceholderText("ten-giong.wav")
        self.file_name.setText(f"voice_{datetime.now():%Y%m%d_%H%M%S}.wav")
        record_layout.addWidget(self.file_name, 1, 1, 1, 2)
        self.record_button = QPushButton("Bắt đầu ghi")
        self.record_button.setObjectName("primaryButton")
        self.record_button.clicked.connect(self._start_recording)
        self.finish_button = QPushButton("Dừng và lưu WAV")
        self.finish_button.clicked.connect(self._finish_recording)
        self.finish_button.setEnabled(False)
        self.record_status = QLabel("Sẵn sàng · WAV mono PCM 16-bit 48 kHz")
        self.record_status.setObjectName("muted")
        record_layout.addWidget(self.record_button, 2, 0)
        record_layout.addWidget(self.finish_button, 2, 1)
        record_layout.addWidget(self.record_status, 2, 2)
        root.addWidget(record_panel)

        actions = QHBoxLayout()
        actions.addStretch()
        close_button = QPushButton("Đóng")
        close_button.clicked.connect(self.accept)
        actions.addWidget(close_button)
        root.addLayout(actions)

        default_folder = Path(__file__).resolve().parents[2] / "reference" / "sample"
        saved_folder = str(self.settings.value(self.SETTINGS_FOLDER_KEY, "") or "").strip()
        self._set_folder(Path(saved_folder) if saved_folder else default_folder)
        self._refresh_microphones()
        self._set_player_enabled(False)

    def _folder(self) -> Path:
        return Path(self.folder_field.text()).expanduser()

    def _set_folder(self, folder: Path) -> None:
        resolved = folder.expanduser().resolve()
        self.folder_field.setText(str(resolved))
        self.settings.setValue(self.SETTINGS_FOLDER_KEY, str(resolved))
        self._refresh_files()

    def _choose_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Chọn thư mục giọng tham chiếu",
            str(self._folder()),
        )
        if selected:
            self.player.stop()
            self.player.setSource(QUrl())
            self._set_folder(Path(selected))

    def _open_folder(self) -> None:
        folder = self._folder()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _refresh_files(self, select_path: Path | None = None) -> None:
        folder = self._folder()
        self.file_list.clear()
        if folder.is_dir():
            for path in sorted(folder.glob("*.wav"), key=lambda item: item.name.casefold()):
                item = QListWidgetItem(path.name)
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                item.setToolTip(str(path))
                self.file_list.addItem(item)
                if select_path and path.resolve() == select_path.resolve():
                    self.file_list.setCurrentItem(item)
        self._set_player_enabled(self.file_list.currentItem() is not None)

    def _selected_file(self) -> Path | None:
        item = self.file_list.currentItem()
        if not item:
            return None
        path = Path(str(item.data(Qt.ItemDataRole.UserRole)))
        return path if path.is_file() else None

    def _file_selected(self, _current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self.position_slider.setValue(0)
        self.time_label.setText("00:00 / 00:00")
        self._set_player_enabled(self._selected_file() is not None)

    def _toggle_play(self) -> None:
        path = self._selected_file()
        if not path or self._audio_source is not None:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            return
        if self.player.source().toLocalFile() != str(path):
            self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

    def _set_player_enabled(self, enabled: bool) -> None:
        self.play_button.setEnabled(enabled and self._audio_source is None)
        self.stop_button.setEnabled(enabled and self._audio_source is None)
        self.position_slider.setEnabled(enabled and self._audio_source is None)

    def _refresh_microphones(self) -> None:
        current = self.microphone_combo.currentText()
        self._audio_devices = list(QMediaDevices.audioInputs())
        self.microphone_combo.clear()
        for device in self._audio_devices:
            self.microphone_combo.addItem(device.description())
        index = self.microphone_combo.findText(current)
        self.microphone_combo.setCurrentIndex(index if index >= 0 else 0)
        self.record_button.setEnabled(bool(self._audio_devices))
        if not self._audio_devices:
            self.record_status.setText("Không tìm thấy microphone.")

    @staticmethod
    def _safe_file_name(value: str) -> str | None:
        name = value.strip()
        if not name:
            return None
        if not name.lower().endswith(".wav"):
            name += ".wav"
        if Path(name).name != name or re.search(r'[<>:"/\\|?*]', name):
            return None
        if name.rsplit(".", 1)[0].rstrip(" .").upper() in {
            "CON", "PRN", "AUX", "NUL",
            *(f"COM{value}" for value in range(1, 10)),
            *(f"LPT{value}" for value in range(1, 10)),
        }:
            return None
        return name

    @staticmethod
    def _recording_format(device) -> QAudioFormat:
        desired = QAudioFormat()
        desired.setSampleRate(48_000)
        desired.setChannelCount(1)
        desired.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        return desired if device.isFormatSupported(desired) else device.preferredFormat()

    @staticmethod
    def _ffmpeg_input_format(sample_format: QAudioFormat.SampleFormat) -> str | None:
        return {
            QAudioFormat.SampleFormat.UInt8: "u8",
            QAudioFormat.SampleFormat.Int16: "s16le",
            QAudioFormat.SampleFormat.Int32: "s32le",
            QAudioFormat.SampleFormat.Float: "f32le",
        }.get(sample_format)

    def _start_recording(self) -> None:
        if self._audio_source is not None:
            return
        name = self._safe_file_name(self.file_name.text())
        if not name:
            QMessageBox.information(
                self,
                "Tên file không hợp lệ",
                "Nhập tên file WAV không chứa các ký tự <>:\"/\\|?*.",
            )
            return
        if not self._audio_devices or self.microphone_combo.currentIndex() < 0:
            QMessageBox.information(self, "Thiếu microphone", "Không tìm thấy thiết bị thu âm.")
            return
        folder = self._folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Không thể tạo thư mục", str(exc))
            return
        target = folder / name
        if target.exists():
            answer = QMessageBox.question(
                self,
                "File đã tồn tại",
                f"Ghi đè file {target.name}?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self.player.stop()
        self.player.setSource(QUrl())
        device = self._audio_devices[self.microphone_combo.currentIndex()]
        record_format = self._recording_format(device)
        raw_format = self._ffmpeg_input_format(record_format.sampleFormat())
        if not raw_format:
            QMessageBox.warning(
                self,
                "Định dạng microphone không được hỗ trợ",
                f"Sample format: {record_format.sampleFormat()}",
            )
            return
        raw_path = folder / f".recording-{uuid4().hex}.raw"
        try:
            raw_file = raw_path.open("wb")
            audio_source = QAudioSource(device, record_format, self)
            record_device = audio_source.start()
            if record_device is None:
                raise RuntimeError("Qt không thể mở luồng microphone.")
            record_device.readyRead.connect(self._read_recording_data)
        except Exception as exc:
            try:
                raw_file.close()
            except (OSError, UnboundLocalError):
                pass
            raw_path.unlink(missing_ok=True)
            QMessageBox.warning(self, "Không thể bắt đầu ghi âm", str(exc))
            return

        self._audio_source = audio_source
        self._record_device = record_device
        self._raw_file = raw_file
        self._raw_path = raw_path
        self._record_target = target
        self._record_format = record_format
        self._record_elapsed_ms = 0
        self.record_timer.start()
        self.record_button.setEnabled(False)
        self.finish_button.setEnabled(True)
        self.microphone_combo.setEnabled(False)
        self.file_name.setEnabled(False)
        self.file_list.setEnabled(False)
        self.choose_folder_button.setEnabled(False)
        self.open_folder_button.setEnabled(False)
        self.refresh_devices_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self._set_player_enabled(False)
        self.record_status.setText("Đang ghi · 00:00")

    def _read_recording_data(self) -> None:
        if self._record_device is None or self._raw_file is None:
            return
        data = bytes(self._record_device.readAll())
        if data:
            self._raw_file.write(data)

    def _record_tick(self) -> None:
        self._record_elapsed_ms += self.record_timer.interval()
        self.record_status.setText(f"Đang ghi · {self._format_ms(self._record_elapsed_ms)}")

    def _finish_recording(self) -> None:
        self._stop_recording(save=True)

    def _stop_recording(self, save: bool) -> None:
        if self._audio_source is None:
            return
        self._read_recording_data()
        self._audio_source.stop()
        self.record_timer.stop()
        if self._raw_file is not None:
            self._raw_file.close()
        raw_path = self._raw_path
        target = self._record_target
        record_format = self._record_format
        self._audio_source.deleteLater()
        self._audio_source = None
        self._record_device = None
        self._raw_file = None
        self._raw_path = None
        self._record_target = None
        self._record_format = None
        self.record_button.setEnabled(bool(self._audio_devices))
        self.finish_button.setEnabled(False)
        self.microphone_combo.setEnabled(True)
        self.file_name.setEnabled(True)
        self.file_list.setEnabled(True)
        self.choose_folder_button.setEnabled(True)
        self.open_folder_button.setEnabled(True)
        self.refresh_devices_button.setEnabled(True)
        self.refresh_button.setEnabled(True)

        if not save or raw_path is None or target is None or record_format is None:
            if raw_path:
                raw_path.unlink(missing_ok=True)
            self.record_status.setText("Đã hủy bản ghi.")
            self._set_player_enabled(self._selected_file() is not None)
            return
        if not raw_path.is_file() or raw_path.stat().st_size == 0:
            raw_path.unlink(missing_ok=True)
            self.record_status.setText("Không nhận được dữ liệu từ microphone.")
            QMessageBox.warning(self, "Bản ghi trống", "Microphone không trả về dữ liệu âm thanh.")
            return

        raw_format = self._ffmpeg_input_format(record_format.sampleFormat())
        temporary = target.with_name(f".{target.stem}.part.wav")
        temporary.unlink(missing_ok=True)
        try:
            ffmpeg = FFmpegService().ffmpeg_path
            command = [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                str(raw_format),
                "-ar",
                str(record_format.sampleRate()),
                "-ac",
                str(record_format.channelCount()),
                "-i",
                str(raw_path),
                "-c:a",
                "pcm_s16le",
                "-ar",
                "48000",
                "-ac",
                "1",
                str(temporary),
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            if completed.returncode != 0 or not temporary.is_file():
                raise RuntimeError(
                    completed.stderr.strip() or f"FFmpeg kết thúc với mã {completed.returncode}."
                )
            temporary.replace(target)
        except (FFmpegError, OSError, RuntimeError) as exc:
            temporary.unlink(missing_ok=True)
            QMessageBox.critical(self, "Không thể lưu WAV", str(exc))
            self.record_status.setText("Lưu WAV thất bại.")
            return
        finally:
            raw_path.unlink(missing_ok=True)

        self.record_status.setText(f"Đã lưu: {target.name}")
        self.file_name.setText(f"voice_{datetime.now():%Y%m%d_%H%M%S}.wav")
        self._refresh_files(target)

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
            self.record_status.setText(f"Không thể phát file: {message}")

    def _update_time(self, position: int, duration: int) -> None:
        self.time_label.setText(
            f"{self._format_ms(position)} / {self._format_ms(duration)}"
        )

    @staticmethod
    def _format_ms(value: int) -> str:
        seconds = max(0, value // 1000)
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def accept(self) -> None:
        if self._audio_source is not None:
            answer = QMessageBox.question(
                self,
                "Đang ghi âm",
                "Dừng và hủy bản ghi hiện tại trước khi đóng?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._stop_recording(save=False)
        self.player.stop()
        self.player.setSource(QUrl())
        super().accept()

    def reject(self) -> None:
        if self._audio_source is not None:
            answer = QMessageBox.question(
                self,
                "Đang ghi âm",
                "Dừng và hủy bản ghi hiện tại trước khi đóng?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._stop_recording(save=False)
        self.player.stop()
        self.player.setSource(QUrl())
        super().reject()
