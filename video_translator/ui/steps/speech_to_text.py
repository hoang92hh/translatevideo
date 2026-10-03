from ...models import StepId
from ..components import Card
from ..specs import DEVICE_FIELD, FieldSpec, ProviderSpec, StepSpec
from .base import StepPage
from PySide6.QtWidgets import QComboBox


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
        for candidate in self.state.audio_candidates.values():
            for stem in candidate.stems:
                if stem not in {"voice", "original"}:
                    continue
                self.audio_input.addItem(
                    f"{candidate.label} · {stem.title()}",
                    (candidate.id, stem),
                )
        index = self.audio_input.findData(current)
        self.audio_input.setCurrentIndex(index if index >= 0 else 0)
        self.audio_input.blockSignals(False)

    def _input_changed(self, *_: object) -> None:
        value = self.audio_input.currentData()
        if value:
            self.state.select_audio_input(value[0], value[1])

    def settings(self) -> dict[str, object]:
        values = super().settings()
        values["input_audio"] = self.state.audio_input_path()
        values["input_candidate_id"] = self.state.selected_audio_candidate_id
        values["input_stem"] = self.state.selected_audio_stem
        return values
