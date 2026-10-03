from .audio_sync import AudioSyncStepPage
from .build_audio import BuildAudioStepPage
from .extract import ExtractStepPage
from .render import RenderStepPage
from .speech_to_text import SpeechToTextStepPage
from .text_to_speech import TextToSpeechStepPage
from .translation import TranslationStepPage

STEP_PAGE_TYPES = (
    ExtractStepPage,
    SpeechToTextStepPage,
    TranslationStepPage,
    TextToSpeechStepPage,
    AudioSyncStepPage,
    BuildAudioStepPage,
    RenderStepPage,
)

__all__ = ["STEP_PAGE_TYPES"]

