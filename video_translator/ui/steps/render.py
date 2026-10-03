from PySide6.QtWidgets import QLabel

from ...models import StepId
from ..components import Card
from ..specs import FieldSpec, ProviderSpec, StepSpec
from .base import StepPage


class RenderStepPage(StepPage):
    SPEC = StepSpec(
        StepId.RENDER,
        "07",
        "Render & Export",
        "Ghép video, dubbed audio và subtitle tùy chọn.",
        (ProviderSpec("FFmpeg Renderer", (
            FieldSpec("audio_mode", "Chế độ audio", "choice", "Replace original voice", (
                "Replace original voice",
                "Keep original at reduced volume",
                "Dubbed voice + background",
                "Subtitle only",
            )),
            FieldSpec("subtitle", "Tạo subtitle", "bool", True),
            FieldSpec("burn_subtitle", "Burn subtitle vào video", "bool", False),
        )),),
    )

    def build_special_card(self) -> Card:
        card = Card("Đích xuất file", "Output của project được lưu cố định trong thư mục này.")
        self.output_path = QLabel("—")
        self.output_path.setObjectName("inputValue")
        self.output_path.setWordWrap(True)
        card.content_layout.addWidget(self.output_path)
        return card

    def refresh_special(self) -> None:
        self.output_path.setText(self.state.output_folder)

