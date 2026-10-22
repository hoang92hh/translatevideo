"""Compatibility access to the seven step pages.

The page classes are loaded lazily.  Importing ``ui.steps.base`` must not
eagerly import every step page, because Step 5's page imports that base class.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...steps.step1_separation.ui import ExtractStepPage
    from ...steps.step2_transcription.ui import SpeechToTextStepPage
    from ...steps.step3_translation.ui import TranslationStepPage
    from ...steps.step4_tts.ui import TextToSpeechStepPage
    from ...steps.step5_alignment.ui import AudioSyncStepPage
    from ...steps.step6_render.ui import BuildAudioStepPage
    from ...steps.step7_export.ui import RenderStepPage


def __getattr__(name: str):
    if name == "STEP_PAGE_TYPES":
        from ...steps.step1_separation.ui import ExtractStepPage
        from ...steps.step2_transcription.ui import SpeechToTextStepPage
        from ...steps.step3_translation.ui import TranslationStepPage
        from ...steps.step4_tts.ui import TextToSpeechStepPage
        from ...steps.step5_alignment.ui import AudioSyncStepPage
        from ...steps.step6_render.ui import BuildAudioStepPage
        from ...steps.step7_export.ui import RenderStepPage

        value = (
            ExtractStepPage,
            SpeechToTextStepPage,
            TranslationStepPage,
            TextToSpeechStepPage,
            AudioSyncStepPage,
            BuildAudioStepPage,
            RenderStepPage,
        )
        globals()[name] = value
        return value

    page_modules = {
        "ExtractStepPage": "...steps.step1_separation.ui",
        "SpeechToTextStepPage": "...steps.step2_transcription.ui",
        "TranslationStepPage": "...steps.step3_translation.ui",
        "TextToSpeechStepPage": "...steps.step4_tts.ui",
        "AudioSyncStepPage": "...steps.step5_alignment.ui",
        "BuildAudioStepPage": "...steps.step6_render.ui",
        "RenderStepPage": "...steps.step7_export.ui",
    }
    module_name = page_modules.get(name)
    if module_name is None:
        raise AttributeError(name)

    from importlib import import_module

    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


__all__ = ["STEP_PAGE_TYPES"]
