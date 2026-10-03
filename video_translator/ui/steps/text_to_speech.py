from ...models import StepId
from ..specs import DEVICE_FIELD, FieldSpec, ProviderSpec, StepSpec
from .base import StepPage


class TextToSpeechStepPage(StepPage):
    SPEC = StepSpec(
        StepId.TTS,
        "04",
        "Text to Speech",
        "Tạo một file giọng nói riêng cho mỗi segment.",
        (
            ProviderSpec("VieNeu-TTS", (
                FieldSpec("voice", "Voice", "choice", "Default", ("Default", "Female 01", "Male 01")),
                FieldSpec("speed", "Speed", "float", 1.0),
                DEVICE_FIELD,
                FieldSpec("reference_voice", "Reference voice", "text", ""),
            )),
            ProviderSpec("Edge TTS", (
                FieldSpec("voice", "Voice", "text", "vi-VN-HoaiMyNeural"),
                FieldSpec("speed", "Speed", "float", 1.0),
            )),
            ProviderSpec("Provider khác (sắp có)", available=False),
        ),
    )

