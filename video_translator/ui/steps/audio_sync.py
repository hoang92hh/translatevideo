from ...models import StepId
from ..specs import FieldSpec, ProviderSpec, StepSpec
from .base import StepPage


class AudioSyncStepPage(StepPage):
    SPEC = StepSpec(
        StepId.SYNC,
        "05",
        "Audio Sync",
        "Căn thời lượng audio mới vào timestamp của video gốc.",
        (
            ProviderSpec("Adjust Speed", (FieldSpec("max_speed", "Tốc độ tối đa", "float", 1.35),)),
            ProviderSpec("Use Available Gap"),
            ProviderSpec("Trim Silence"),
        ),
    )

