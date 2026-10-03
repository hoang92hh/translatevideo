from ...models import StepId
from .audio_sync import execute as sync_audio
from .build_audio import execute as build_audio
from .extract import execute as extract
from .render import execute as render
from .speech_to_text import execute as speech_to_text
from .text_to_speech import execute as text_to_speech
from .translation import execute as translate

HANDLERS = {
    StepId.EXTRACT: extract,
    StepId.STT: speech_to_text,
    StepId.TRANSLATE: translate,
    StepId.TTS: text_to_speech,
    StepId.SYNC: sync_audio,
    StepId.BUILD_AUDIO: build_audio,
    StepId.RENDER: render,
}

__all__ = ["HANDLERS"]

