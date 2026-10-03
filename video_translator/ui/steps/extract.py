from PySide6.QtWidgets import QFormLayout, QLabel

from ...models import StepId
from ..components import Card
from ..specs import FieldSpec, ProviderSpec, StepSpec
from .base import StepPage


class ExtractStepPage(StepPage):
    SPEC = StepSpec(
        StepId.EXTRACT,
        "01",
        "Input & Extract",
        "Kiểm tra video nguồn của project và tạo audio phục vụ nhận dạng.",
        (ProviderSpec("FFmpeg", (FieldSpec("sample_rate", "Sample rate", "choice", "16 kHz", ("16 kHz", "24 kHz", "48 kHz")),)),),
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

