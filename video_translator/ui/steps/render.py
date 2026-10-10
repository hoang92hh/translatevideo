import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
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
from ..components import Card
from ..specs import ProviderSpec, StepSpec
from .base import StepPage


class RenderStepPage(StepPage):
    SPEC = StepSpec(
        StepId.RENDER,
        "07",
        "Render & Export",
        "Ghép hình ảnh gốc, voice mới, voice gốc, background và subtitle tùy chọn.",
        (ProviderSpec("FFmpeg Renderer", ()),),
    )

    def __init__(self, state) -> None:
        self._processing = False
        self._original_voice_available = False
        self._background_available = False
        super().__init__(state)

    def build_special_card(self) -> Card:
        card = Card(
            "Thành phần đưa vào video",
            "Voice mới, voice gốc đã tách và background có thể chỉnh âm lượng riêng.",
        )
        self.image_check = QCheckBox("Hình ảnh video gốc — bắt buộc")
        self.image_check.setChecked(True)
        self.image_check.setEnabled(False)
        self.voice_check = QCheckBox("Voice mới từ Step 6 — bắt buộc")
        self.voice_check.setChecked(True)
        self.voice_check.setEnabled(False)
        voice_volume_row = QHBoxLayout()
        voice_volume_row.addWidget(QLabel("Âm lượng Voice mới"))
        self.voice_volume = QSlider(Qt.Orientation.Horizontal)
        self.voice_volume.setRange(0, 100)
        self.voice_volume.setValue(100)
        self.voice_volume.valueChanged.connect(self._voice_volume_changed)
        self.voice_volume_label = QLabel("100%")
        voice_volume_row.addWidget(self.voice_volume, 1)
        voice_volume_row.addWidget(self.voice_volume_label)
        self.original_voice_check = QCheckBox("Voice gốc đã tách ở Step 1")
        self.original_voice_check.setChecked(False)
        self.original_voice_check.toggled.connect(self._original_voice_toggled)
        self.original_voice_info = QLabel()
        self.original_voice_info.setObjectName("muted")
        self.original_voice_info.setWordWrap(True)
        original_voice_volume_row = QHBoxLayout()
        original_voice_volume_row.addWidget(QLabel("Âm lượng Voice gốc"))
        self.original_voice_volume = QSlider(Qt.Orientation.Horizontal)
        self.original_voice_volume.setRange(0, 100)
        self.original_voice_volume.setValue(20)
        self.original_voice_volume.valueChanged.connect(self._original_voice_volume_changed)
        self.original_voice_volume_label = QLabel("20%")
        original_voice_volume_row.addWidget(self.original_voice_volume, 1)
        original_voice_volume_row.addWidget(self.original_voice_volume_label)
        self.background_check = QCheckBox("Âm thanh nền gốc (Background)")
        self.background_check.setChecked(True)
        self.background_check.toggled.connect(self._background_toggled)
        self.background_info = QLabel()
        self.background_info.setObjectName("muted")
        self.background_info.setWordWrap(True)
        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel("Âm lượng Background"))
        self.background_volume = QSlider(Qt.Orientation.Horizontal)
        self.background_volume.setRange(0, 100)
        self.background_volume.setValue(80)
        self.background_volume.valueChanged.connect(self._background_volume_changed)
        self.background_volume_label = QLabel("80%")
        volume_row.addWidget(self.background_volume, 1)
        volume_row.addWidget(self.background_volume_label)
        self.subtitle_check = QCheckBox("Tạo subtitle SRT")
        self.subtitle_check.setChecked(False)
        self.subtitle_check.toggled.connect(self._subtitle_toggled)
        self.burn_subtitle_check = QCheckBox("Burn subtitle vào video")
        self.burn_subtitle_check.setChecked(False)
        self.burn_subtitle_check.setEnabled(False)
        card.content_layout.addWidget(self.image_check)
        card.content_layout.addWidget(self.voice_check)
        card.content_layout.addLayout(voice_volume_row)
        card.content_layout.addWidget(self.original_voice_check)
        card.content_layout.addWidget(self.original_voice_info)
        card.content_layout.addLayout(original_voice_volume_row)
        card.content_layout.addWidget(self.background_check)
        card.content_layout.addWidget(self.background_info)
        card.content_layout.addLayout(volume_row)
        card.content_layout.addWidget(self.subtitle_check)
        card.content_layout.addWidget(self.burn_subtitle_check)
        self.output_path = QLabel("—")
        self.output_path.setObjectName("inputValue")
        self.output_path.setWordWrap(True)
        card.content_layout.addWidget(QLabel("Thư mục xuất video"))
        card.content_layout.addWidget(self.output_path)
        return card

    def build_result_extra(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        title = QLabel("Các video đã render")
        title.setObjectName("cardTitle")
        layout.addWidget(title)
        self.render_combo = QComboBox()
        self.render_combo.currentIndexChanged.connect(self._render_changed)
        layout.addWidget(self.render_combo)
        self.render_info = QLabel("Chưa có output Step 7")
        self.render_info.setObjectName("muted")
        self.render_info.setWordWrap(True)
        layout.addWidget(self.render_info)
        actions = QHBoxLayout()
        open_video = QPushButton("Mở video")
        open_video.clicked.connect(self._open_video)
        open_subtitle = QPushButton("Mở subtitle")
        open_subtitle.clicked.connect(self._open_subtitle)
        open_folder = QPushButton("Mở thư mục")
        open_folder.clicked.connect(self._open_folder)
        delete_button = QPushButton("Xóa output")
        delete_button.clicked.connect(self._delete_output)
        for button in (open_video, open_subtitle, open_folder, delete_button):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        return container

    def settings(self) -> dict[str, object]:
        return {
            **super().settings(),
            "include_video": True,
            "include_voice": True,
            "voice_volume": self.voice_volume.value() / 100,
            "include_original_voice": (
                self._original_voice_available and self.original_voice_check.isChecked()
            ),
            "original_voice_volume": self.original_voice_volume.value() / 100,
            "include_background": self._background_available and self.background_check.isChecked(),
            "background_volume": self.background_volume.value() / 100,
            "subtitle": self.subtitle_check.isChecked(),
            "burn_subtitle": self.subtitle_check.isChecked() and self.burn_subtitle_check.isChecked(),
        }

    def refresh_special(self) -> None:
        self.output_path.setText(self.state.output_folder)
        candidate = self.state.candidate(self.state.selected_audio_candidate_id)
        original_voice_value = candidate.stem_path("voice") if candidate else ""
        original_voice = Path(original_voice_value) if original_voice_value else None
        original_voice_available = bool(original_voice and original_voice.is_file())
        if original_voice_available != self._original_voice_available:
            self._original_voice_available = original_voice_available
            self.original_voice_check.setEnabled(original_voice_available)
            self.original_voice_check.setChecked(original_voice_available)
        if original_voice_available and original_voice:
            self.original_voice_info.setText(f"Voice gốc được chọn:\n{original_voice}")
        else:
            self.original_voice_info.setText(
                "Candidate Step 1 không có stem Voice. Hãy chạy tách MDX để dùng voice gốc."
            )
            self.original_voice_check.setChecked(False)
            self.original_voice_check.setEnabled(False)
        self._original_voice_toggled(self.original_voice_check.isChecked())
        background_value = candidate.stem_path("background") if candidate else ""
        background = Path(background_value) if background_value else None
        available = bool(background and background.is_file())
        if available != self._background_available:
            self._background_available = available
            self.background_check.setEnabled(available)
            self.background_check.setChecked(available)
        if available and background:
            self.background_info.setText(f"Background được chọn:\n{background}")
        else:
            self.background_info.setText(
                "Candidate Step 1 không có Background. Step 7 sẽ render hình ảnh + voice mới."
            )
            self.background_check.setChecked(False)
            self.background_check.setEnabled(False)
        self._background_toggled(self.background_check.isChecked())
        if hasattr(self, "render_combo") and not self._processing:
            self._refresh_outputs()

    def set_busy(self, busy: bool) -> None:
        self._processing = busy
        super().set_busy(busy)
        if not busy and hasattr(self, "render_combo"):
            self._refresh_outputs(prefer_selected=True)

    def _show_result(self, result: StepResult) -> None:
        super()._show_result(result)
        for row in range(self.table.rowCount()):
            for column in range(self.table.columnCount()):
                item = self.table.item(row, column)
                if item:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

    def _background_toggled(self, checked: bool) -> None:
        self.background_volume.setEnabled(self._background_available and checked)

    def _original_voice_toggled(self, checked: bool) -> None:
        self.original_voice_volume.setEnabled(self._original_voice_available and checked)

    def _voice_volume_changed(self, value: int) -> None:
        self.voice_volume_label.setText(f"{value}%")

    def _original_voice_volume_changed(self, value: int) -> None:
        self.original_voice_volume_label.setText(f"{value}%")

    def _background_volume_changed(self, value: int) -> None:
        self.background_volume_label.setText(f"{value}%")

    def _subtitle_toggled(self, checked: bool) -> None:
        self.burn_subtitle_check.setEnabled(checked)
        if not checked:
            self.burn_subtitle_check.setChecked(False)

    def _refresh_outputs(self, prefer_selected: bool = False) -> None:
        current = self.render_combo.currentData()
        self.render_combo.blockSignals(True)
        self.render_combo.clear()
        for candidate in self.state.render_candidates.values():
            suffix = "  [Mới nhất]" if candidate.id == self.state.selected_render_candidate_id else ""
            self.render_combo.addItem(f"{candidate.label}{suffix}", candidate.id)
        target_id = self.state.selected_render_candidate_id if prefer_selected else current
        target = self.render_combo.findData(target_id or self.state.selected_render_candidate_id)
        self.render_combo.setCurrentIndex(target if target >= 0 else 0)
        self.render_combo.blockSignals(False)
        self._render_changed()

    def _current_candidate(self):
        return self.state.render_candidate(str(self.render_combo.currentData() or ""))

    def _render_changed(self, *_: object) -> None:
        candidate = self._current_candidate()
        if not candidate:
            self.render_info.setText("Chưa có output Step 7")
            return
        voice_volume = round(float(candidate.metadata.get("voice_volume", 1.0)) * 100)
        components = ["Hình ảnh", f"Voice mới {voice_volume}%"]
        if candidate.original_voice_used:
            original_voice_volume = round(
                float(candidate.metadata.get("original_voice_volume", 0.2)) * 100
            )
            components.append(f"Voice gốc {original_voice_volume}%")
        if candidate.background_used:
            background_volume = round(
                float(candidate.metadata.get("background_volume", 1.0)) * 100
            )
            components.append(f"Background {background_volume}%")
        if candidate.subtitle_file:
            components.append("Subtitle burn" if candidate.burned_subtitle else "Subtitle SRT")
        status = "Sẵn sàng" if Path(candidate.video_file).is_file() else "File không tồn tại"
        self.render_info.setText(
            f"{' + '.join(components)} · {candidate.duration_seconds:.2f}s · {status}\n"
            f"{candidate.video_file}"
        )

    def _open_video(self) -> None:
        candidate = self._current_candidate()
        if candidate and Path(candidate.video_file).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(candidate.video_file))

    def _open_subtitle(self) -> None:
        candidate = self._current_candidate()
        if candidate and candidate.subtitle_file and Path(candidate.subtitle_file).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(candidate.subtitle_file))

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
            "Xóa output Step 7",
            f"Xóa {candidate.label} và toàn bộ video/subtitle của lần render này?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        target = Path(candidate.folder).resolve()
        if self.state.project:
            root = self.state.project.path("output").resolve()
            if target.is_relative_to(root) and target != root and target.is_dir():
                shutil.rmtree(target)
        self.state.remove_render_candidate(candidate.id)
        self._refresh_outputs()
