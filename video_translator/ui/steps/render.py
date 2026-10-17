import shutil
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
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
        super().__init__(state)

    def build_special_card(self) -> Card:
        card = Card("Thiết lập project", "Thông số render được quản lý tập trung trong Thiết lập project.")
        self.output_path = QLabel("—")
        self.output_path.setObjectName("inputValue")
        self.output_path.setWordWrap(True)
        card.content_layout.addWidget(QLabel("Thư mục output của video"))
        card.content_layout.addWidget(self.output_path)
        return card

    def build_result_extra(self) -> QWidget:
        # Step 7 chỉ cần thông tin video đầu ra, không cần bảng từng segment.
        self.table.setVisible(False)
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
        return {}

    def refresh_special(self) -> None:
        self.output_path.setText(self.state.output_folder)
        if hasattr(self, "render_combo") and not self._processing:
            self._refresh_outputs()

    def set_busy(self, busy: bool) -> None:
        self._processing = busy
        super().set_busy(busy)
        if not busy and hasattr(self, "render_combo"):
            self._refresh_outputs(prefer_selected=True)

    def _show_result(self, result: StepResult) -> None:
        self._show_result_without_segments(result)

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
            self.show_candidate_result(None)
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
