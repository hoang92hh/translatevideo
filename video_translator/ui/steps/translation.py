from ...models import StepId
from ..specs import DEVICE_FIELD, FieldSpec, ProviderSpec, StepSpec
from .base import StepPage


class TranslationStepPage(StepPage):
    SPEC = StepSpec(
        StepId.TRANSLATE,
        "03",
        "Translation",
        "Dịch theo batch và giữ nguyên ID của từng segment.",
        (
            ProviderSpec("Google Gemini", (
                FieldSpec("model", "Model", "choice", "gemini-2.5-flash", ("gemini-2.5-flash", "gemini-2.5-pro")),
                FieldSpec("batch_size", "Segments / batch", "int", 30),
                FieldSpec("credential", "API key environment", "text", "GOOGLE_API_KEY"),
            )),
            ProviderSpec("Local Model", (
                FieldSpec("model_path", "Model path", "text", "models/translator"),
                DEVICE_FIELD,
            )),
            ProviderSpec("Provider khác (sắp có)", available=False),
        ),
    )

