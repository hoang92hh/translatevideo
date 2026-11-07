from ..interface import (
    DialogueAnalysisResponse,
    TranslationProvider,
    TranslationResponse,
    TranslationReviewResponse,
    create_translation_provider,
    register_translation_provider,
    registered_translation_providers,
)

__all__ = [
    "DialogueAnalysisResponse",
    "TranslationProvider",
    "TranslationResponse",
    "TranslationReviewResponse",
    "create_translation_provider",
    "register_translation_provider",
    "registered_translation_providers",
]

# Provider modules register their factories when this package is loaded.
from .gemini import provider as _gemini_provider
from .deepseek import provider as _deepseek_provider
from .opencode_go import provider as _opencode_go_provider
from .chatgpt_web import provider as _chatgpt_web_provider
