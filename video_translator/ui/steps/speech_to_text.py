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

from ...models import StepId
from ..components import Card
from ..specs import DEVICE_FIELD, FieldSpec, ProviderSpec, StepSpec
from .base import StepPage


class SpeechToTextStepPage(StepPage):
    SPEC = StepSpec(
        StepId.STT,
        "02",
        "Speech to Text",
        "Nhận dạng lời nói, timestamp và segment ID.",
        (
            ProviderSpec("Faster Whisper", (
                FieldSpec("model", "Model", "choice", "medium", ("small", "medium", "large-v3")),
                DEVICE_FIELD,
                FieldSpec("vad", "Voice activity detection", "bool", True),
            )),
            ProviderSpec("Provider khác (sắp có)", available=False),
        ),
    )

    def build_special_card(self) -> Card:
        card = Card("Audio input", "Chọn candidate Voice hoặc Original Mix được tạo ở Step 1.")
        self.audio_input = QComboBox()
        self.audio_input.currentIndexChanged.connect(self._input_changed)
        card.content_layout.addWidget(self.audio_input)
        return card

    def refresh_special(self) -> None:
        current = (self.state.selected_audio_candidate_id, self.state.selected_audio_stem)
        self.audio_input.blockSignals(True)
        self.audio_input.clear()
        selected_index = -1
        for candidate in self.state.audio_candidates.values():
            for stem in candidate.stems:
                if stem not in {"voice", "original"}:
                    continue
                self.audio_input.addItem(
                    f"{candidate.label} · {stem.title()}",
                    (candidate.id, stem),
                )
                value = self.audio_input.itemData(self.audio_input.count() - 1)
                if (
                    isinstance(value, (tuple, list))
                    and len(value) == 2
                    and (str(value[0]), str(value[1])) == current
                ):
                    selected_index = self.audio_input.count() - 1
        self.audio_input.setCurrentIndex(selected_index)
        self.audio_input.blockSignals(False)
        if hasattr(self, "transcript_combo") and not getattr(self, "_processing", False):
            self._refresh_transcripts()

    def set_busy(self, busy: bool) -> None:
        self._processing = busy
        super().set_busy(busy)
        if not busy and hasattr(self, "transcript_combo"):
            self._refresh_transcripts(prefer_selected=True)

    def build_result_extra(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        title = QLabel("Chọn output transcript")
        title.setObjectName("cardTitle")
        layout.addWidget(title)

        self.transcript_combo = QComboBox()
        self.transcript_combo.currentIndexChanged.connect(self._transcript_changed)
        layout.addWidget(self.transcript_combo)

        self.transcript_info = QLabel("Chưa có transcript")
        self.transcript_info.setObjectName("muted")
        self.transcript_info.setWordWrap(True)
        layout.addWidget(self.transcript_info)

        actions = QHBoxLayout()
        use_button = QPushButton("Dùng làm input Step 3")
        use_button.clicked.connect(self._select_for_step_three)
        open_file = QPushButton("Mở file")
        open_file.clicked.connect(self._open_transcript)
        open_folder = QPushButton("Mở thư mục")
        open_folder.clicked.connect(self._open_transcript_folder)
        delete_button = QPushButton("Xóa transcript")
        delete_button.clicked.connect(self._delete_transcript)
        actions.addWidget(use_button)
        actions.addWidget(open_file)
        actions.addWidget(open_folder)
        actions.addWidget(delete_button)
        actions.addStretch()
        layout.addLayout(actions)
        return container

    def _refresh_transcripts(self, prefer_selected: bool = False) -> None:
        current = self.transcript_combo.currentData()
        self.transcript_combo.blockSignals(True)
        self.transcript_combo.clear()
        for candidate in self.state.transcript_candidates.values():
            suffix = (
                "  [Input Step 3]"
                if candidate.id == self.state.selected_transcript_candidate_id
                else ""
            )
            self.transcript_combo.addItem(f"{candidate.label}{suffix}", candidate.id)
        target_id = self.state.selected_transcript_candidate_id if prefer_selected else current
        target = self.transcript_combo.findData(target_id or self.state.selected_transcript_candidate_id)
        self.transcript_combo.setCurrentIndex(target if target >= 0 else 0)
        self.transcript_combo.blockSignals(False)
        self._transcript_changed()

    def _transcript_changed(self, *_: object) -> None:
        candidate = self._current_transcript()
        if not candidate:
            self.transcript_info.setText("Chưa có transcript")
            return
        status = "Sẵn sàng" if Path(candidate.path).is_file() else "File không tồn tại"
        model = str(candidate.metadata.get("model", ""))
        device = str(candidate.metadata.get("actual_device", ""))
        details = " · ".join(
            value
            for value in (model, device, f"{candidate.segment_count} segment", status)
            if value
        )
        self.transcript_info.setText(f"{details}\n{candidate.path}")

    def _current_transcript(self):
        return self.state.transcript_candidate(str(self.transcript_combo.currentData() or ""))

    def _select_for_step_three(self) -> None:
        candidate = self._current_transcript()
        if candidate and self.state.select_transcript_candidate(candidate.id):
            self._refresh_transcripts()
            return
        QMessageBox.information(self, "Không thể chọn", "File transcript không tồn tại hoặc không hợp lệ.")

    def _open_transcript(self) -> None:
        candidate = self._current_transcript()
        if candidate and Path(candidate.path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(candidate.path))

    def _open_transcript_folder(self) -> None:
        candidate = self._current_transcript()
        if candidate:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(candidate.path).parent)))

    def _delete_transcript(self) -> None:
        candidate = self._current_transcript()
        if not candidate:
            return
        answer = QMessageBox.question(
            self,
            "Xóa transcript",
            f"Xóa {candidate.label} và file transcript của lần chạy này?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        path = Path(candidate.path)
        if self.state.project:
            root = self.state.project.path("transcripts").resolve()
            target = path.resolve()
            if target.is_relative_to(root) and target.is_file():
                target.unlink()
                if target.parent != root and target.parent.is_dir():
                    shutil.rmtree(target.parent, ignore_errors=True)
        self.state.remove_transcript_candidate(candidate.id)
        self._refresh_transcripts()

    def _input_changed(self, *_: object) -> None:
        value = self.audio_input.currentData()
        if isinstance(value, (tuple, list)) and len(value) == 2:
            self.state.select_audio_input(str(value[0]), str(value[1]))

    def settings(self) -> dict[str, object]:
        values = super().settings()
        values["input_audio"] = self.state.audio_input_path()
        values["input_candidate_id"] = self.state.selected_audio_candidate_id
        values["input_stem"] = self.state.selected_audio_stem
        return values
