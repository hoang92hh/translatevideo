from ...models import StepId
from ..specs import FieldSpec, ProviderSpec, StepSpec
from .base import StepPage


class BuildAudioStepPage(StepPage):
    SPEC = StepSpec(
        StepId.BUILD_AUDIO,
        "06",
        "Build Audio",
        "Ghép các segment đã đồng bộ thành một audio track.",
        (ProviderSpec("FFmpeg Timeline", (
            FieldSpec("format", "Định dạng", "choice", "WAV", ("WAV", "AAC")),
            FieldSpec("sample_rate", "Sample rate", "choice", "48 kHz", ("24 kHz", "48 kHz")),
        )),),
    )

