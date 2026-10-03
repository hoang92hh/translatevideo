from ...models import StepId
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

